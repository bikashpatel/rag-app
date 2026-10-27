"""The user's row lock, taken first by every change to a user's periods, plan or money (docs/briefs/plans-billing.md
B42). Lock order everywhere: user → order → payment → refund or dispute → receipt counter; no Razorpay call is made
inside a transaction or under a lock."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def lock_user(db: AsyncSession, user_id: int) -> None:
    """SELECT ... FOR NO KEY UPDATE on the user's row until the transaction ends (it doesn't block rows that only refer
    to the user). SQLite has no row locks: its single writer serialises the writes."""
    await db.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
