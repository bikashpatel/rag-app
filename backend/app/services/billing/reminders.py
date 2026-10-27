"""Plan reminder emails (docs/briefs/plans-billing.md 4.7; the text of plans-billing-visual.md 12): "ends in 5 days",
"ends tomorrow" and "has ended", once each per chain, only when SMTP and BILLING_EMAIL_FROM are set and the user gave
an address; none when a later period or an open-ended plan follows. Plain text, no tracking; the address is never
logged."""

import logging
from datetime import date, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import BillingEvent, PlanPeriod, User, UserBillingProfile, utcnow
from app.ops.alerts import Notification
from app.ops.channels import ChannelError, EmailChannel
from app.ops.settings import AlertSettings, load
from app.services.billing import audit, business, periods
from app.services.billing.money import format_ist_date

logger = logging.getLogger(__name__)

ENDED_WINDOW: Final = timedelta(hours=6)  # "has ended" is sent (or retried) this long after the end


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


def message(
    which: str, *, name: str, plan: str, last_day: date, business_name: str, contact: str | None
) -> tuple[str, list[str]]:
    """The subject and the lines of a reminder."""
    days = settings.billing_reminder_days
    subject = {
        "soon": f"Your {plan} plan ends in {days} day{'' if days == 1 else 's'}",
        "tomorrow": f"Your {plan} plan ends tomorrow",
        "ended": f"Your {plan} plan has ended",
    }[which]
    verb = "ended" if which == "ended" else "ends"
    lines = [
        f"Hello {name},",
        "",
        f"Your {plan} plan on {business_name} {verb} on {format_ist_date(last_day)} (IST).",
        "",
        "Renew it, or choose another plan, here:",
        f"{business.origins()[0]}/plans",
        "",
        "Your chats and documents are kept.",
        "",
        " · ".join(part for part in (business_name, contact) if part),
        "You get this email because you gave this address for plan reminders.",
        "Remove it in Settings → Plan & billing to stop them.",
    ]
    return subject, lines


async def _chain_end(db: AsyncSession, user: User, now: datetime) -> PlanPeriod | None:
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
    """Sends the reminders due now (the worker's billing loop, hourly). Returns how many were sent."""
    if not business.email_reminders():
        return 0
    now = now or utcnow()
    alerts = load(AlertSettings)
    sent = 0
    async with SessionLocal() as db:
        seller = await business.get_settings(db)
        rows = (
            await db.execute(
                select(User, UserBillingProfile)
                .join(UserBillingProfile, UserBillingProfile.user_id == User.id)
                .where(UserBillingProfile.email.is_not(None), User.is_active.is_(True), User.is_admin.is_(False))
            )
        ).tuples()
        for user, profile in list(rows):
            last = await _chain_end(db, user, now)
            which = due(last.ends_at, now) if last is not None else None
            if last is None or which is None or not profile.email:
                continue
            done = await db.scalars(
                select(BillingEvent.detail).where(BillingEvent.type == "reminder.sent", BillingEvent.user_id == user.id)
            )
            if any(item.get("period_id") == last.id and item.get("reminder") == which for item in done):
                continue
            subject, lines = message(
                which,
                name=profile.name or user.username,
                plan=periods.snapshot(last).name,
                last_day=periods.valid_through(last.ends_at),
                business_name=seller.business_name,
                contact=seller.contact_email,
            )
            channel = EmailChannel(
                alerts.alert_smtp_host,
                alerts.alert_smtp_port,
                settings.billing_email_from,
                [profile.email],
                username=alerts.alert_smtp_username,
                password=alerts.alert_smtp_password,
                security=alerts.alert_smtp_security,
            )
            try:
                await channel.send(Notification(kind="reminder", title=subject, lines=lines, check_ids=[]))
            except ChannelError as exc:
                logger.warning("A plan reminder couldn't be sent (%s); it is retried on the next run", exc)
                continue
            await audit.record_event(
                db,
                source="system",
                type="reminder.sent",
                user_id=user.id,
                detail={"period_id": last.id, "reminder": which},
            )
            await db.commit()
            sent += 1
    return sent
