"""Applying a payment exactly once (docs/briefs/plans-billing.md 6.5, 6.6 and 4.9): a payment entity already fetched
from Razorpay (or a bank transfer the admin records) buys its order's period under the user's row lock, only when its
status, amount, currency, order and mode match; anything else goes to the admin's review. Also the payment views."""

import logging
import secrets
from collections.abc import Mapping
from datetime import datetime, time
from fractions import Fraction
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    BillingDispute,
    BillingOrder,
    BillingPayment,
    BillingReceipt,
    BillingRefund,
    Plan,
    PlanPeriod,
    User,
    utcnow,
)
from app.schemas_billing import (
    AdminPayment,
    DisputeView,
    OfflinePaymentRequest,
    PaymentView,
    PlanSnapshot,
    ReceiptRef,
    RefundView,
    UserRef,
)
from app.services.billing import audit, business, entitlements, periods, plans, receipts
from app.services.billing.locks import lock_user
from app.services.billing.money import IST, PERIOD_DAYS, credit_paise, format_inr, now_charge

logger = logging.getLogger(__name__)

Outcome = Literal["applied", "done", "review", "failed", "waiting"]
LISTED_STATUSES = ("captured", "refunded", "partially_refunded", "disputed")  # what a user's history shows
_OWN_STATUSES = ("refunded", "partially_refunded", "disputed")  # set by our refunds and disputes, never by a fetch
_HOLDING_STATUSES = ("creating", "pending", "processed")  # refunds that take from the refundable amount


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: object, limit: int) -> str | None:
    return str(value)[:limit] if isinstance(value, str) and value else None


async def _locked_order(db: AsyncSession, order_id: int) -> BillingOrder:
    order = await db.scalar(
        select(BillingOrder)
        .where(BillingOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert order is not None, order_id
    return order


async def _upsert(db: AsyncSession, order: BillingOrder, entity: Mapping[str, Any]) -> tuple[BillingPayment, bool]:
    """The payment row by its Razorpay id, locked, with the fetched fields (only ids, status, amount, method, fees
    and the error; never the payer's details); True when its status changed."""
    payment_id = str(entity["id"])
    query = (
        select(BillingPayment)
        .where(BillingPayment.razorpay_payment_id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    payment = await db.scalar(query)
    if payment is None:
        try:
            async with db.begin_nested():
                payment = BillingPayment(
                    razorpay_payment_id=payment_id,
                    order_id=order.id,
                    user_id=order.user_id,
                    username=order.username,
                    status="new",
                    amount_paise=_int(entity.get("amount")) or 0,
                    currency=str(entity.get("currency") or "")[:3],
                    mode=order.mode,
                )
                db.add(payment)
        except IntegrityError:
            payment = await db.scalar(query)
            assert payment is not None
    fetched = str(entity.get("status") or "")[:24]
    changed = payment.status != fetched and payment.status not in _OWN_STATUSES
    if payment.status not in _OWN_STATUSES:
        payment.status = fetched
    if payment.applied_at is None:
        payment.amount_paise = _int(entity.get("amount")) or payment.amount_paise
        payment.currency = str(entity.get("currency") or payment.currency)[:3]
    payment.method = _text(entity.get("method"), 24) or payment.method
    fee, tax = _int(entity.get("fee")), _int(entity.get("tax"))
    payment.fee_paise = fee if fee is not None else payment.fee_paise
    payment.fee_tax_paise = tax if tax is not None else payment.fee_tax_paise
    payment.error_code = _text(entity.get("error_code"), 64) or payment.error_code
    payment.error_reason = (
        _text(entity.get("error_description"), 200) or _text(entity.get("error_reason"), 200) or payment.error_reason
    )
    if fetched == "captured" and payment.captured_at is None:
        payment.captured_at = utcnow()
    return payment, changed


def _mismatch(order: BillingOrder, payment: BillingPayment, entity: Mapping[str, Any]) -> str | None:
    if payment.currency != "INR":
        return f"Currency differs: paid in {payment.currency or 'an unknown currency'}, expected INR"
    if payment.amount_paise != order.amount_paise:
        return f"Amount differs: paid {format_inr(payment.amount_paise)}, expected {format_inr(order.amount_paise)}"
    razorpay_order = entity.get("order_id")
    if order.razorpay_order_id and razorpay_order != order.razorpay_order_id:
        return "The payment belongs to another Razorpay order"
    if order.mode != settings.razorpay_mode:
        return f"Paid in {order.mode} mode while the server runs {settings.razorpay_mode or 'no'} keys"
    return None


async def _review(
    db: AsyncSession, order: BillingOrder, payment: BillingPayment, reason: str, *, source: str, type: str
) -> None:
    order.status = "review"
    order.review_reason = reason[:200]
    order.next_check_at = None
    await audit.record_event(
        db,
        source=source,
        type=type,
        user_id=order.user_id,
        order_id=order.id,
        payment_id=payment.id,
        detail={"reason": order.review_reason, "razorpay_payment_id": payment.razorpay_payment_id},
    )
    logger.warning("A payment went to review: %s", order.review_reason)


async def apply_payment(db: AsyncSession, order_id: int, entity: Mapping[str, Any], *, source: str) -> Outcome:
    """Records a payment entity fetched from Razorpay and, when it is captured and matches its order, applies it:
    once per Razorpay payment id, in one short transaction (committed here). The caller holds no transaction."""
    user_id = await db.scalar(select(BillingOrder.user_id).where(BillingOrder.id == order_id))
    if user_id is not None:
        await lock_user(db, user_id)
    order = await _locked_order(db, order_id)
    payment, changed = await _upsert(db, order, entity)
    if payment.applied_at is not None:
        await db.commit()
        return "done"
    fetched = entity.get("status")
    if fetched == "failed":
        if changed:
            order.last_payment_error = payment.error_reason or "The payment failed."
            await audit.record_event(
                db,
                source=source,
                type="payment.failed",
                user_id=order.user_id,
                order_id=order.id,
                payment_id=payment.id,
                detail={"razorpay_payment_id": payment.razorpay_payment_id, "error_code": payment.error_code},
            )
        await db.commit()
        return "failed"
    if fetched != "captured":
        await db.commit()
        return "waiting"
    reason, kind = _mismatch(order, payment, entity), "payment.mismatch"
    if reason is None:
        kind = "order.review"
        if order.status == "paid" and order.payment_id not in (None, payment.id):
            reason = "A second payment for one checkout"
        elif order.user_id is None:
            reason = "The user was deleted"
        elif order.status == "review":
            reason = order.review_reason or "The checkout is under review"
    if reason is not None:
        await _review(db, order, payment, reason, source=source, type=kind)
        await db.commit()
        return "review"
    assert order.user_id is not None
    now = utcnow()
    snapshot = PlanSnapshot.model_validate(order.plan_snapshot)
    days = periods.TEST_PERIOD_DAYS if order.mode == "test" else PERIOD_DAYS
    if order.kind == "now":
        if (fell := await _credit_fell(db, order, now)) is not None:
            await _review(db, order, payment, fell, source=source, type="order.review")
            await db.commit()
            return "review"
        try:
            period = await periods.start_now(
                db,
                order.user_id,
                snapshot=snapshot,
                days=days,
                basis_paise=int(order.plan_snapshot.get("price_paise", order.amount_paise + order.credit_paise)),
                mode="test" if order.mode == "test" else "live",
                order_id=order.id,
                replaces_period_id=order.replaces_period_id,
                replaces_plan_id=order.replaces_plan_id,
                replaces_ends_at=order.replaces_ends_at,
                now=now,
            )
        except periods.PeriodChanged:
            reason = (
                "The plan this payment was priced against has changed. Suggested: refund "
                f"{format_inr(payment.amount_paise)} or grant instead."
            )
            await _review(db, order, payment, reason, source=source, type="order.review")
            await db.commit()
            return "review"
    else:
        period = await periods.add_period(
            db,
            order.user_id,
            snapshot=snapshot,
            days=days,
            source="payment",
            mode="test" if order.mode == "test" else "live",
            basis_paise=payment.amount_paise,
            order_id=order.id,
            now=now,
        )
    await _paid(db, order, payment, period, now, source=source)
    await db.commit()
    return "applied"


async def _credit_fell(db: AsyncSession, order: BillingOrder, now: datetime) -> str | None:
    """A `now` order's credit again (4.4), with the replaced period's usage and refunds now and the time left as
    quoted: the reason for review when it fell below the quoted credit (the allowance was used up, or the payment
    partly refunded, while the link was open). None when it holds, or when the period is no longer the user's
    current one (start_now then refuses the order)."""
    user = await db.get(User, order.user_id) if order.credit_paise and order.user_id is not None else None
    replaced = await db.get(PlanPeriod, order.replaces_period_id) if order.replaces_period_id else None
    if user is None or replaced is None:
        return None
    ent = await entitlements.load(db, user, now=now)
    if ent.period_id != replaced.id:
        return None
    credit = await upgrade_credit(db, replaced, used=await entitlements.allowance_used(db, ent), at=order.created_at)
    price = int(order.plan_snapshot.get("price_paise", order.amount_paise + order.credit_paise))
    given = price - now_charge(price, credit)
    if given >= order.credit_paise:
        return None
    return (
        f"Usage or a refund since the quote lowered the credit to {format_inr(given)} (quoted "
        f"{format_inr(order.credit_paise)}). Suggested: refund {format_inr(order.amount_paise)} or grant instead."
    )


async def _paid(
    db: AsyncSession,
    order: BillingOrder,
    payment: BillingPayment,
    period: PlanPeriod | None,
    now: datetime,
    *,
    source: str,
) -> None:
    payment.applied_at = now
    order.status, order.paid_at, order.payment_id, order.next_check_at = "paid", now, payment.id, None
    order.review_reason = None
    await receipts.issue(db, payment, order, period, now=now)
    detail = {
        "razorpay_payment_id": payment.razorpay_payment_id,
        "amount": payment.amount_paise,
        "method": payment.method,
    }
    await audit.record_event(
        db,
        source=source,
        type="payment.captured",
        user_id=order.user_id,
        order_id=order.id,
        payment_id=payment.id,
        detail=detail,
    )
    if period is not None:
        await audit.record_event(
            db,
            source=source,
            type="period.started_now" if order.kind == "now" else "period.granted",
            user_id=order.user_id,
            order_id=order.id,
            payment_id=payment.id,
            detail={
                "period_id": period.id,
                "plan": PlanSnapshot.model_validate(order.plan_snapshot).code,
                "starts_at": period.starts_at,
                "ends_at": period.ends_at,
            },
        )


# ---------------- Bank transfers (4.9) ----------------


async def record_offline(db: AsyncSession, admin: User, body: OfflinePaymentRequest) -> BillingPayment:
    """A bank transfer the admin received: an order and a payment, then the same apply as a Razorpay payment (a period
    from the paid time on, unless `apply` is false) and a receipt in the live series."""
    user = await db.get(User, body.user_id)
    plan = await db.get(Plan, body.plan_id)
    if user is None or user.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    if body.amount_paise < plan.price_paise and not body.note:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Add a note: {format_inr(body.amount_paise)} is below the plan's price of {format_inr(plan.price_paise)}.",
        )
    await lock_user(db, user.id)
    now = utcnow()
    snapshot = periods.snapshot_of(plan)
    order = BillingOrder(
        public_id=secrets.token_urlsafe(15),
        user_id=user.id,
        username=user.username,
        plan_id=plan.id,
        plan_snapshot={**snapshot.model_dump(mode="json"), "price_paise": plan.price_paise},
        kind="period",
        amount_paise=body.amount_paise,
        currency="INR",
        mode="offline",
        status="paid",
        origin=business.origins()[0],
        expires_at=now,
        watch_until=now,
        next_check_at=None,
    )
    db.add(order)
    await db.flush()
    payment = BillingPayment(
        razorpay_payment_id=None,
        order_id=order.id,
        user_id=user.id,
        username=user.username,
        status="captured",
        amount_paise=body.amount_paise,
        currency="INR",
        method="bank_transfer",
        mode="offline",
        offline_reference=body.reference,
        recorded_by=admin.username,
        captured_at=datetime.combine(body.received_on, time(12), IST),
    )
    db.add(payment)
    await db.flush()
    period = None
    if body.apply:
        period = await periods.add_period(
            db,
            user.id,
            snapshot=snapshot,
            days=PERIOD_DAYS,
            source="payment",
            mode="offline",
            basis_paise=body.amount_paise,
            order_id=order.id,
            now=now,
        )
    await _paid(db, order, payment, period, now, source="admin")
    await audit.record_event(
        db,
        source="admin",
        type="payment.offline_recorded",
        actor=admin.username,
        user_id=user.id,
        order_id=order.id,
        payment_id=payment.id,
        detail={
            "plan": plan.code,
            "amount": body.amount_paise,
            "received_on": body.received_on,
            "reference": body.reference,
            "note": body.note,
            "apply": body.apply,
        },
    )
    await db.commit()
    return payment


# ---------------- Views ----------------


async def bought_period(db: AsyncSession, payment: BillingPayment) -> PlanPeriod | None:
    if payment.order_id is None or payment.applied_at is None:
        return None
    return await db.scalar(
        select(PlanPeriod)
        .where(PlanPeriod.order_id == payment.order_id, PlanPeriod.source == "payment")
        .order_by(PlanPeriod.id)
        .limit(1)
    )


async def credit_used(db: AsyncSession, payment: BillingPayment) -> int:
    """The credit later `now` orders took from the period this payment bought."""
    period = await bought_period(db, payment)
    if period is None:
        return 0
    used = await db.scalar(
        select(func.coalesce(func.sum(BillingOrder.credit_paise), 0)).where(
            BillingOrder.kind == "now", BillingOrder.status == "paid", BillingOrder.replaces_period_id == period.id
        )
    )
    return int(used or 0)


async def net_basis(db: AsyncSession, period: PlanPeriod) -> int:
    """The period's charge basis less the refunds being made or made of the payment that bought it (4.4)."""
    if period.order_id is None or period.source != "payment":
        return period.charge_basis_paise
    held = await db.scalar(
        select(func.coalesce(func.sum(BillingRefund.amount_paise), 0))
        .join(BillingPayment, BillingPayment.id == BillingRefund.payment_id)
        .where(
            BillingPayment.order_id == period.order_id,
            BillingPayment.applied_at.is_not(None),
            BillingRefund.status.in_(_HOLDING_STATUSES),
        )
    )
    return max(0, period.charge_basis_paise - int(held or 0))


async def upgrade_credit(db: AsyncSession, period: PlanPeriod, *, used: Fraction, at: datetime) -> int:
    """The credit a `now` order gets at `at` for the rest of `period` (4.4), whose allowances are `used`."""
    basis = await net_basis(db, period)
    return credit_paise(basis, starts_at=period.starts_at, ends_at=period.ends_at, now=at, used=used)


async def lost_disputes(db: AsyncSession, payment: BillingPayment) -> int:
    """What the payment's lost disputes took back."""
    lost = await db.scalar(
        select(func.coalesce(func.sum(BillingDispute.amount_paise), 0)).where(
            BillingDispute.payment_id == payment.id, BillingDispute.status == "lost"
        )
    )
    return int(lost or 0)


async def unbacked_credit(db: AsyncSession, payment: BillingPayment) -> int:
    """The part of the credit that upgrades still running took from this payment's period which the payer got back
    by a refund or a lost dispute (4.10): money returned that went into an upgrade as well."""
    period = await bought_period(db, payment)
    if period is None:
        return 0
    credit = await db.scalar(
        select(func.coalesce(func.sum(BillingOrder.credit_paise), 0))
        .join(PlanPeriod, PlanPeriod.order_id == BillingOrder.id)
        .where(
            BillingOrder.kind == "now",
            BillingOrder.status == "paid",
            BillingOrder.replaces_period_id == period.id,
            PlanPeriod.revoked_at.is_(None),
        )
    )
    taken = int(credit or 0)
    returned = payment.refunded_paise + await lost_disputes(db, payment)
    return max(0, min(taken, returned + taken - period.charge_basis_paise))


async def refundable(db: AsyncSession, payment: BillingPayment) -> int:
    """Its amount less refunds being made or made, and less the credit later upgrades took from its period (4.10)."""
    if payment.applied_at is None and payment.status != "captured":
        return 0
    held = await db.scalar(
        select(func.coalesce(func.sum(BillingRefund.amount_paise), 0)).where(
            BillingRefund.payment_id == payment.id, BillingRefund.status.in_(_HOLDING_STATUSES)
        )
    )
    return max(0, payment.amount_paise - int(held or 0) - await credit_used(db, payment))


def _description(order: BillingOrder | None, payment: BillingPayment) -> str:
    if order is None:
        return "Payment"
    snapshot = PlanSnapshot.model_validate(order.plan_snapshot)
    days = periods.TEST_PERIOD_DAYS if order.mode == "test" else PERIOD_DAYS
    text = f"{snapshot.name} plan, {days} day{'' if days == 1 else 's'}"
    if order.kind == "now":
        text += ", started at once"
    if payment.method == "bank_transfer":
        text += f" (bank transfer, ref {payment.offline_reference})"
    return text


async def payment_view(db: AsyncSession, payment: BillingPayment) -> PaymentView:
    order = await db.get(BillingOrder, payment.order_id) if payment.order_id else None
    period = await bought_period(db, payment)
    receipt = await db.scalar(select(BillingReceipt).where(BillingReceipt.payment_id == payment.id))
    shown = payment.status if payment.status in LISTED_STATUSES else "captured"
    return PaymentView(
        id=payment.id,
        paid_at=payment.captured_at or payment.created_at,
        plan=plans.snapshot_ref(order.plan_snapshot) if order else None,
        kind="now" if order is not None and order.kind == "now" else "period",
        description=_description(order, payment),
        amount_paise=payment.amount_paise,
        method=payment.method,
        status=shown,  # type: ignore[arg-type]
        refunded_paise=payment.refunded_paise,
        period_start=period.starts_at if period else None,
        period_end=period.ends_at if period else None,
        receipt=ReceiptRef(id=receipt.id, number=receipt.number) if receipt else None,
        test_mode=payment.mode == "test",
    )


async def user_payments(db: AsyncSession, user_id: int) -> list[PaymentView]:
    rows = await db.scalars(
        select(BillingPayment)
        .where(BillingPayment.user_id == user_id, BillingPayment.status.in_(LISTED_STATUSES))
        .order_by(BillingPayment.created_at.desc(), BillingPayment.id.desc())
        .limit(200)
    )
    return [await payment_view(db, payment) for payment in rows]


def refund_view(refund: BillingRefund) -> RefundView:
    return RefundView(
        id=refund.id,
        amount_paise=refund.amount_paise,
        status=refund.status,  # type: ignore[arg-type]
        initiated_by="razorpay" if refund.initiated_by == "razorpay" else "admin",
        admin_username=refund.admin_username,
        reason=refund.reason,
        end_access=refund.end_access,
        created_at=refund.created_at,
        processed_at=refund.processed_at,
        last_error=refund.last_error,
    )


def dispute_view(dispute: BillingDispute) -> DisputeView:
    return DisputeView(
        id=dispute.id,
        razorpay_dispute_id=dispute.razorpay_dispute_id,
        amount_paise=dispute.amount_paise,
        status=dispute.status,  # type: ignore[arg-type]
        reason_code=dispute.reason_code,
        respond_by=dispute.respond_by,
        created_at=dispute.created_at,
        updated_at=dispute.updated_at,
    )


async def admin_payment(db: AsyncSession, payment: BillingPayment) -> AdminPayment:
    view = await payment_view(db, payment)
    order = await db.get(BillingOrder, payment.order_id) if payment.order_id else None
    refunds = await db.scalars(
        select(BillingRefund).where(BillingRefund.payment_id == payment.id).order_by(BillingRefund.id)
    )
    disputes = await db.scalars(
        select(BillingDispute).where(BillingDispute.payment_id == payment.id).order_by(BillingDispute.id)
    )
    mode: Literal["test", "live", "offline"] = (
        "test" if payment.mode == "test" else "offline" if payment.mode == "offline" else "live"
    )
    return AdminPayment(
        **view.model_dump(),
        user=UserRef(id=payment.user_id, username=payment.username),
        order_public_id=order.public_id if order else None,
        razorpay_payment_id=payment.razorpay_payment_id,
        fee_paise=payment.fee_paise,
        fee_tax_paise=payment.fee_tax_paise,
        refundable_paise=await refundable(db, payment),
        credit_used_paise=await credit_used(db, payment),
        refunds=[refund_view(refund) for refund in refunds],
        disputes=[dispute_view(dispute) for dispute in disputes],
        offline_reference=payment.offline_reference,
        mode=mode,
    )
