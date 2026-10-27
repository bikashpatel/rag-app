"""Disputes, chargebacks (docs/briefs/plans-billing.md 6.8): fetched dispute entities are upserted by Razorpay's id
under the user, payment and dispute locks; a status change applies once. A lost dispute ends what the payment bought,
as a full refund does. Disputes are answered in Razorpay's Dashboard; the app only tracks them."""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingDispute, BillingPayment, utcnow
from app.services.billing import audit, razorpay, refunds
from app.services.billing.locks import lock_user
from app.services.billing.refunds import Seen

STATUSES: Final = ("open", "under_review", "action_required", "won", "lost", "closed")
NEEDS_ACTION: Final = ("open", "under_review", "action_required")


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _respond_by(value: object) -> datetime | None:
    seconds = _int(value)
    return datetime.fromtimestamp(seconds, timezone.utc) if seconds else None


async def apply(db: AsyncSession, entity: Mapping[str, Any], *, source: str) -> Seen:
    """Records a dispute entity fetched from Razorpay; "unknown" for a payment we don't know. Commits."""
    dispute_id = razorpay.entity_id(entity.get("id"))
    payment_ref = razorpay.entity_id(entity.get("payment_id"))
    known = await db.scalar(select(BillingPayment).where(BillingPayment.razorpay_payment_id == payment_ref))
    if dispute_id is None or payment_ref is None or known is None:
        await db.commit()
        return "unknown"
    if known.user_id is not None:
        await lock_user(db, known.user_id)
    payment = await db.scalar(
        select(BillingPayment)
        .where(BillingPayment.id == known.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert payment is not None
    fetched = entity.get("status")
    new = fetched if fetched in STATUSES else "open"
    query = (
        select(BillingDispute)
        .where(BillingDispute.razorpay_dispute_id == dispute_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    dispute = await db.scalar(query)
    if dispute is None:
        try:
            async with db.begin_nested():
                db.add(
                    BillingDispute(
                        razorpay_dispute_id=dispute_id,
                        payment_id=payment.id,
                        amount_paise=_int(entity.get("amount")) or payment.amount_paise,
                        status="new",
                    )
                )
        except IntegrityError:
            pass
        dispute = await db.scalar(query)
        assert dispute is not None
    dispute.amount_paise = _int(entity.get("amount")) or dispute.amount_paise
    dispute.reason_code = str(entity.get("reason_code"))[:64] if entity.get("reason_code") else dispute.reason_code
    dispute.respond_by = _respond_by(entity.get("respond_by")) or dispute.respond_by
    if dispute.status == new:
        await db.commit()
        return "same"
    dispute.status = new
    await audit.record_event(
        db,
        source=source,
        type=f"dispute.{new}",
        user_id=payment.user_id,
        order_id=payment.order_id,
        payment_id=payment.id,
        detail={"dispute_id": dispute.id, "razorpay_dispute_id": dispute_id, "amount": dispute.amount_paise},
    )
    if new == "lost" and dispute.access_ended_at is None:
        dispute.access_ended_at = utcnow()
        payment.status = "disputed"
        await refunds.end_access(db, payment, reason="Dispute lost", source=source, actor=None)
    await db.commit()
    return "changed"
