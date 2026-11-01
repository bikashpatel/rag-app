"""Whether an account has paid, and whether payments are live (docs/briefs/signup-auth.md S22, S27, S31): an unpaid
account's mail is in the capped `public` class and its payment links have smaller caps."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import BillingPayment
from app.services.billing import business


async def has_paid(db: AsyncSession, user_id: int) -> bool:
    """At least one live or offline payment of the user bought a period (test payments don't count)."""
    found = await db.scalar(
        select(BillingPayment.id)
        .where(
            BillingPayment.user_id == user_id,
            BillingPayment.applied_at.is_not(None),
            BillingPayment.mode.in_(("live", "offline")),
        )
        .limit(1)
    )
    return found is not None


async def payments_live() -> bool:
    """Live Razorpay keys that Razorpay hasn't rejected."""
    return settings.razorpay_mode == "live" and await business.keys_rejected() is None
