"""Refunds (docs/briefs/plans-billing.md 4.10 and 6.7): made from Admin → Billing through Razorpay's refund API with an
idempotency key (bank transfers are recorded only), and seen again by the API's answer, a webhook or the reconciler.
Each refund's state changes once; the payment's refunded amount is the sum of its processed refunds; a full refund (or
a partial one with "End access") ends what the payment bought, once."""

import logging
import secrets
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Final, Literal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import BillingOrder, BillingPayment, BillingRefund, PlanPeriod, User, utcnow
from app.schemas_billing import RefundRequest
from app.services.billing import audit, business, payments, periods, razorpay
from app.services.billing.locks import lock_user
from app.services.billing.money import format_inr

logger = logging.getLogger(__name__)

PENDING_FETCH_AFTER: Final = timedelta(minutes=30)  # a pending refund is fetched again after this
RETRY_CREATING_AFTER: Final = timedelta(minutes=1)  # a refund left creating by a timeout is sent again after this
_STATUSES = ("pending", "processed", "failed")

Seen = Literal["changed", "same", "unknown"]


async def _locked_payment(db: AsyncSession, payment_id: int) -> BillingPayment:
    payment = await db.scalar(
        select(BillingPayment)
        .where(BillingPayment.id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert payment is not None, payment_id
    return payment


async def end_access(db: AsyncSession, payment: BillingPayment, *, reason: str, source: str, actor: str | None) -> None:
    """What the payment bought ends (4.10): its period is revoked when it hasn't ended, or when an upgrade ended it (so
    it is never given back); a `now` payment's replaced period gets its end back when nothing changed it since; later
    periods re-chain. Credit that a running upgrade took from the period and that the payer got back as well is
    recorded (`credit.reversed`; "Needs attention" lists it). The caller holds the user's lock."""
    period = await payments.bought_period(db, payment)
    if period is None:
        return
    now = utcnow()
    if period.revoked_at is None and (period.ends_at > now or periods.ended_by_order(period)):
        await periods.revoke(db, period, reason=reason, actor=actor, source=source, now=now)
    if unbacked := await payments.unbacked_credit(db, payment):
        await audit.record_event(
            db,
            source=source,
            type="credit.reversed",
            actor=actor,
            user_id=payment.user_id,
            order_id=payment.order_id,
            payment_id=payment.id,
            detail={"period_id": period.id, "credit": unbacked, "reason": reason},
        )
    order = await db.get(BillingOrder, payment.order_id) if payment.order_id else None
    if order is None or order.kind != "now" or order.replaces_period_id is None:
        return
    replaced = await db.get(PlanPeriod, order.replaces_period_id)
    if replaced is None or not await periods.restore(
        db, replaced, order_id=order.id, actor=actor, source=source, now=now
    ):
        await audit.record_event(
            db,
            source=source,
            type="period.not_restored",
            actor=actor,
            user_id=payment.user_id,
            order_id=order.id,
            payment_id=payment.id,
            detail={"period_id": order.replaces_period_id, "reason": "The replaced period changed or has ended"},
        )


async def _processed(db: AsyncSession, payment: BillingPayment, refund: BillingRefund, *, source: str) -> None:
    """A refund reached `processed` (once): the payment's refunded sum and status, then the access rule."""
    refund.processed_at = refund.processed_at or utcnow()
    await db.flush()
    total = await db.scalar(
        select(func.coalesce(func.sum(BillingRefund.amount_paise), 0)).where(
            BillingRefund.payment_id == payment.id, BillingRefund.status == "processed"
        )
    )
    payment.refunded_paise = int(total or 0)
    full = payment.refunded_paise >= payment.amount_paise
    if payment.status != "disputed":
        payment.status = "refunded" if full else "partially_refunded"
    await audit.record_event(
        db,
        source=source,
        type="refund.processed",
        actor=refund.admin_username,
        user_id=payment.user_id,
        order_id=payment.order_id,
        payment_id=payment.id,
        detail={"refund_id": refund.id, "amount": refund.amount_paise, "refunded": payment.refunded_paise},
    )
    if (full or refund.end_access) and refund.access_ended_at is None:
        refund.access_ended_at = utcnow()
        await end_access(db, payment, reason="Refunded", source=source, actor=refund.admin_username)


async def start(db: AsyncSession, admin: User, payment_id: int, body: RefundRequest) -> None:
    """POST /admin/billing/payments/{id}/refund: the refund row first (committed), then Razorpay's refund API outside
    the transaction; a bank transfer's refund is recorded as processed at once."""
    payment = await db.get(BillingPayment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    if payment.user_id is not None:
        await lock_user(db, payment.user_id)
    payment = await _locked_payment(db, payment_id)
    offline = payment.mode == "offline"
    if not offline and payment.mode != settings.razorpay_mode:
        keys = settings.razorpay_mode or "no"
        detail = f"This {payment.mode} payment can't be refunded while the server runs {keys} keys."
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    creating = await db.scalar(
        select(func.count())
        .select_from(BillingRefund)
        .where(BillingRefund.payment_id == payment.id, BillingRefund.status == "creating")
    )
    if creating:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Another refund of this payment is being made.")
    available = await payments.refundable(db, payment)
    if available < 100:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing is left to refund on this payment.")
    amount = body.amount_paise or available
    if amount > available:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"At most {format_inr(available)} of this payment can be refunded."
        )
    refund = BillingRefund(
        payment_id=payment.id,
        idempotency_key=f"rfd_{secrets.token_urlsafe(18)}",
        amount_paise=amount,
        status="creating",
        initiated_by="admin",
        admin_username=admin.username,
        reason=body.reason,
        end_access=body.end_access,
    )
    db.add(refund)
    await db.flush()
    await audit.record_event(
        db,
        source="admin",
        type="refund.requested",
        actor=admin.username,
        user_id=payment.user_id,
        order_id=payment.order_id,
        payment_id=payment.id,
        detail={"refund_id": refund.id, "amount": amount, "reason": body.reason, "end_access": body.end_access},
    )
    if offline:
        refund.status = "processed"
        await _processed(db, payment, refund, source="admin")
    await db.commit()
    if not offline:
        await send(db, refund.id)


async def send(db: AsyncSession, refund_id: int) -> None:
    """Sends a `creating` refund to Razorpay (again, with the same key and body, after a timeout). No transaction is
    held during the call."""
    refund = await db.get(BillingRefund, refund_id, populate_existing=True)
    payment = await db.get(BillingPayment, refund.payment_id) if refund else None
    await db.commit()
    if refund is None or payment is None or refund.status != "creating" or not payment.razorpay_payment_id:
        return
    body = {"amount": refund.amount_paise, "speed": "normal", "notes": {"refund": str(refund.id)}}
    try:
        entity = await razorpay.create_refund(payment.razorpay_payment_id, body, refund.idempotency_key)
    except razorpay.RazorpayError as exc:
        if exc.keys_rejected:
            await business.set_keys_rejected(exc.description)
        await _send_failed(db, refund.id, exc)
        return
    await apply(db, entity, source="admin")


async def _send_failed(db: AsyncSession, refund_id: int, exc: razorpay.RazorpayError) -> None:
    """A timeout or a server error leaves the refund `creating` for the reconciler; a refusal fails it."""
    refund = await db.get(BillingRefund, refund_id)
    assert refund is not None
    payment = await db.get(BillingPayment, refund.payment_id)
    if payment is not None and payment.user_id is not None:
        await lock_user(db, payment.user_id)
    refund = await db.scalar(
        select(BillingRefund)
        .where(BillingRefund.id == refund_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert refund is not None
    if refund.status == "creating":
        if exc.transient:
            refund.last_error = "Razorpay unreachable; the refund is sent again shortly"
        else:
            refund.status, refund.last_error = "failed", exc.description
            await audit.record_event(
                db,
                source="admin",
                type="refund.failed",
                actor=refund.admin_username,
                user_id=payment.user_id if payment else None,
                payment_id=refund.payment_id,
                detail={"refund_id": refund.id, "code": exc.code, "description": exc.description},
            )
    await db.commit()
    logger.warning("A refund request failed (%s %s)", exc.status, exc.code)


async def _find(
    db: AsyncSession, payment: BillingPayment, entity: Mapping[str, Any], razorpay_id: str
) -> BillingRefund:
    """Our refund by the id in its notes, else by Razorpay's id, else a new row for a refund made in Razorpay's
    Dashboard (inserted in a savepoint; a racing insert is re-read)."""
    notes = entity.get("notes")
    ours = notes.get("refund") if isinstance(notes, dict) else None
    refund = None
    if isinstance(ours, str) and ours.isdigit():
        refund = await db.scalar(
            select(BillingRefund).where(BillingRefund.id == int(ours), BillingRefund.payment_id == payment.id)
        )
    by_razorpay = select(BillingRefund).where(BillingRefund.razorpay_refund_id == razorpay_id)
    refund = refund or await db.scalar(by_razorpay)
    if refund is None:
        amount = entity.get("amount")
        try:
            async with db.begin_nested():
                refund = BillingRefund(
                    payment_id=payment.id,
                    razorpay_refund_id=razorpay_id,
                    idempotency_key=f"rzp_{razorpay_id}",
                    amount_paise=amount if isinstance(amount, int) and not isinstance(amount, bool) else 0,
                    status="pending",
                    initiated_by="razorpay",
                    end_access=False,
                )
                db.add(refund)
        except IntegrityError:
            refund = await db.scalar(by_razorpay)
            assert refund is not None
        await audit.record_event(
            db,
            source="webhook",
            type="refund.recorded",
            user_id=payment.user_id,
            order_id=payment.order_id,
            payment_id=payment.id,
            detail={"refund_id": refund.id, "razorpay_refund_id": razorpay_id, "amount": refund.amount_paise},
        )
    locked = await db.scalar(
        select(BillingRefund)
        .where(BillingRefund.id == refund.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert locked is not None
    return locked


async def apply(db: AsyncSession, entity: Mapping[str, Any], *, source: str) -> Seen:
    """A refund entity fetched from Razorpay (or its create answer): its state changes once, whichever source sees it
    first; a payment we don't know leaves it unknown. Commits."""
    razorpay_id = razorpay.entity_id(entity.get("id"))
    payment_ref = razorpay.entity_id(entity.get("payment_id"))
    known = await db.scalar(select(BillingPayment).where(BillingPayment.razorpay_payment_id == payment_ref))
    if razorpay_id is None or payment_ref is None or known is None:
        await db.commit()
        return "unknown"
    if known.user_id is not None:
        await lock_user(db, known.user_id)
    payment = await _locked_payment(db, known.id)
    refund = await _find(db, payment, entity, razorpay_id)
    refund.razorpay_refund_id = refund.razorpay_refund_id or razorpay_id
    fetched = entity.get("status")
    new = fetched if fetched in _STATUSES else "pending"
    if refund.status == new or refund.status in ("processed", "failed"):
        await db.commit()
        return "same"
    refund.status = new
    if new == "processed":
        refund.last_error = None
        await _processed(db, payment, refund, source=source)
    elif new == "failed":
        refund.last_error = "Razorpay couldn't make the refund"
        await audit.record_event(
            db,
            source=source,
            type="refund.failed",
            user_id=payment.user_id,
            payment_id=payment.id,
            detail={"refund_id": refund.id},
        )
    else:
        refund.last_error = None
    await db.commit()
    return "changed"


# ---------------- The reconciler's part ----------------


async def retry_creating() -> int:
    """Refunds a timeout left `creating`, sent again with the same key and body."""
    cutoff = utcnow() - RETRY_CREATING_AFTER
    async with SessionLocal() as db:
        ids = list(
            await db.scalars(
                select(BillingRefund.id)
                .join(BillingPayment, BillingPayment.id == BillingRefund.payment_id)
                .where(
                    BillingRefund.status == "creating",
                    BillingRefund.created_at <= cutoff,
                    BillingPayment.mode == settings.razorpay_mode,
                )
            )
        )
        for refund_id in ids:
            await send(db, refund_id)
    return len(ids)


async def refresh_pending() -> int:
    """Refunds pending for over 30 minutes, fetched from Razorpay."""
    cutoff = utcnow() - PENDING_FETCH_AFTER
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(BillingRefund.razorpay_refund_id)
                .join(BillingPayment, BillingPayment.id == BillingRefund.payment_id)
                .where(
                    BillingRefund.status == "pending",
                    BillingRefund.razorpay_refund_id.is_not(None),
                    BillingRefund.updated_at <= cutoff,
                    BillingPayment.mode == settings.razorpay_mode,
                )
            )
        ).scalars()
        refund_ids = [refund_id for refund_id in rows if refund_id]
        await db.commit()
        for refund_id in refund_ids:
            try:
                entity = await razorpay.fetch_refund(refund_id)
            except razorpay.RazorpayError as exc:
                if exc.keys_rejected:
                    await business.set_keys_rejected(exc.description)
                    break
                continue
            if await apply(db, entity, source="reconcile") == "same":
                await _touch(db, refund_id)
    return len(refund_ids)


async def _touch(db: AsyncSession, razorpay_id: str) -> None:
    """Still pending: fetched again in 30 minutes."""
    refund = await db.scalar(select(BillingRefund).where(BillingRefund.razorpay_refund_id == razorpay_id))
    if refund is not None:
        refund.updated_at = utcnow()
        await db.commit()
