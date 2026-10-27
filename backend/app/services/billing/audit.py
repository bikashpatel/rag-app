"""The billing audit trail (billing_events): every change that grants or removes value, with who made it (B45)."""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingEvent

# Payer details Razorpay's entities carry: never stored, events included (plans-billing 6.12).
PAYER_KEYS = frozenset({"email", "contact", "vpa", "card", "card_id", "bank", "bank_account", "upi", "acquirer_data"})


def _clean(value: Any) -> Any:
    """JSON-ready, without payer details at any depth."""
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items() if str(key) not in PAYER_KEYS}
    if isinstance(value, list | tuple | set | frozenset):
        return [_clean(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


async def record_event(
    db: AsyncSession,
    *,
    source: str,
    type: str,
    actor: str | None = None,
    user_id: int | None = None,
    order_id: int | None = None,
    payment_id: int | None = None,
    detail: Mapping[str, object] | None = None,
) -> None:
    """Adds an event to the session; the caller's transaction commits it (never commits)."""
    db.add(
        BillingEvent(
            source=source,
            type=type,
            actor=actor,
            user_id=user_id,
            order_id=order_id,
            payment_id=payment_id,
            detail=_clean(detail or {}),
        )
    )
