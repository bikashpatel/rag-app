"""Plan reminder emails (docs/briefs/plans-billing.md 4.7; docs/briefs/signup-auth.md S25): "ends in N days", "ends
tomorrow" and "has ended", once each per chain, queued as outbox `plan_reminder` jobs to the account's verified email
while the mailer is set up; none when a later period or an open-ended plan follows. The worker checks the user's switch,
and that the reminder still applies, when it sends. The address is never logged."""

from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import BillingEvent, PlanPeriod, User, utcnow
from app.services.billing import audit, business, periods
from app.services.mail import outbox

ENDED_WINDOW: Final = timedelta(hours=6)  # "has ended" is queued this long after the end at most


def due(chain_end: datetime, now: datetime) -> str | None:
    """Which reminder the end of a chain calls for now: "soon", "tomorrow" or "ended"."""
    left = chain_end - now
    if left <= -ENDED_WINDOW:
        return None
    if left <= timedelta(0):
        return "ended"
    if left <= timedelta(days=1):
        return "tomorrow"
    if left <= timedelta(days=settings.billing_reminder_days):
        return "soon"
    return None


async def chain_end(db: AsyncSession, user: User, now: datetime) -> PlanPeriod | None:
    """The last period of the user's chain (the current one and those after it), or the one that ended within the
    window with nothing after it; None when an open-ended plan follows."""
    if user.comp_plan_id is not None:
        return None
    rows = await periods.user_periods(db, user.id)
    run = periods.chain(rows, now)
    if run:
        return run[-1]
    live = [row for row in rows if row.revoked_at is None]
    ended = max((row for row in live if row.ends_at <= now), key=lambda row: row.ends_at, default=None)
    if ended is None or any(row.starts_at >= ended.ends_at for row in live):
        return None
    return ended


async def send_due(now: datetime | None = None) -> int:
    """Queues the reminders due now (the worker's billing loop, hourly) for users with a verified email. Each writes a
    `reminder.sent` event (the period and which reminder), which is also the dedupe. Returns how many were queued."""
    if not business.email_reminders():
        return 0
    now = now or utcnow()
    queued = 0
    async with SessionLocal() as db:
        users = await db.scalars(
            select(User)
            .where(User.email.is_not(None), User.is_active.is_(True), User.is_admin.is_(False))
            .order_by(User.id)
        )
        for user in list(users):
            last = await chain_end(db, user, now)
            which = due(last.ends_at, now) if last is not None else None
            if last is None or which is None or not user.email:
                continue
            done = await db.scalars(
                select(BillingEvent.detail).where(BillingEvent.type == "reminder.sent", BillingEvent.user_id == user.id)
            )
            if any(item.get("period_id") == last.id and item.get("reminder") == which for item in done):
                continue
            await outbox.enqueue(
                db,
                "plan_reminder",
                to=user.email,
                canonical=user.email_canonical,
                user_id=user.id,
                params={"reminder": which, "period_id": last.id},
            )
            await audit.record_event(
                db,
                source="system",
                type="reminder.sent",
                user_id=user.id,
                detail={"period_id": last.id, "reminder": which},
            )
            await db.commit()
            queued += 1
    if queued:
        await outbox.wake()
    return queued
