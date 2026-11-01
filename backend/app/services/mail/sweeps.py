"""The worker's hourly clean-up (docs/briefs/signup-auth.md 3.7 and 8.6): expired pending sign-ups and emails, tokens
used or expired over a day ago, jobs past their `not_after` expired when queued or left "sending" by a stopped worker
(while no mail loop runs, their addresses would stay), finished outbox rows after 48 hours (their addresses already
cleared), the sealed address an email change's undo restores once no link can use it, approximate IPs of account
changes after 90 days and the rows after 365, and bounce suppressions after their 30 days. Counts only in the log."""

import logging
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete, Update

from app.db import SessionLocal
from app.models import AuthEvent, AuthToken, EmailOutbox, MailSuppression, PendingEmail, PendingSignup, utcnow
from app.services.auth import tokens
from app.services.mail import drain, outbox

logger = logging.getLogger(__name__)

TOKEN_KEEP: Final = timedelta(days=1)  # used or expired tokens stay a day, so a late click reads "already used"
OUTBOX_KEEP: Final = timedelta(hours=48)
EVENT_IP_KEEP: Final = timedelta(days=90)
EVENT_KEEP: Final = timedelta(days=365)
# As long as an undo link can still be clicked: its notice may go out up to its not_after after the change.
RESTORE_KEEP: Final = outbox.KINDS["email_changed"].not_after + tokens.TTL["email_undo"] + TOKEN_KEEP
FINISHED: Final = ("sent", "failed", "cancelled", "expired")


async def _count(db: AsyncSession, statement: Delete | Update) -> int:
    result = await db.execute(statement.execution_options(synchronize_session=False))
    return int(getattr(result, "rowcount", 0) or 0)


async def _clear_restores(db: AsyncSession, before: datetime) -> int:
    """Drops the sealed old address from email.changed events no undo link can use any more (4.8)."""
    cleared = 0
    events = await db.scalars(select(AuthEvent).where(AuthEvent.type == "email.changed", AuthEvent.created_at < before))
    for event in events:
        if "restore" in (event.detail or {}):
            event.detail = {name: value for name, value in event.detail.items() if name != "restore"}
            cleared += 1
    return cleared


async def run(now: datetime | None = None) -> dict[str, int]:
    """One sweep; returns what it deleted or cleared, by kind."""
    now = now or utcnow()
    async with SessionLocal() as db:
        counts = {
            "pending_signups": await _count(db, delete(PendingSignup).where(PendingSignup.expires_at <= now)),
            "pending_emails": await _count(db, delete(PendingEmail).where(PendingEmail.expires_at <= now)),
            "auth_tokens": await _count(
                db,
                delete(AuthToken).where(
                    or_(AuthToken.used_at < now - TOKEN_KEEP, AuthToken.expires_at < now - TOKEN_KEEP)
                ),
            ),
            "outbox_expired": await _count(
                db,
                update(EmailOutbox)
                .where(
                    or_(
                        EmailOutbox.status == "queued",
                        and_(
                            EmailOutbox.status == "sending",
                            EmailOutbox.next_attempt_at < now - drain.STALE_SENDING,
                        ),
                    ),
                    EmailOutbox.not_after < now,
                )
                .values(status="expired", finished_at=now, to_address=None, canonical=None),
            ),
            "outbox_addresses": await _count(
                db,
                update(EmailOutbox)
                .where(
                    EmailOutbox.status.in_(FINISHED),
                    or_(EmailOutbox.to_address.is_not(None), EmailOutbox.canonical.is_not(None)),
                )
                .values(to_address=None, canonical=None),
            ),
            "email_outbox": await _count(db, delete(EmailOutbox).where(EmailOutbox.finished_at < now - OUTBOX_KEEP)),
            "auth_event_ips": await _count(
                db,
                update(AuthEvent)
                .where(AuthEvent.created_at < now - EVENT_IP_KEEP, AuthEvent.ip.is_not(None))
                .values(ip=None),
            ),
            "auth_events": await _count(db, delete(AuthEvent).where(AuthEvent.created_at < now - EVENT_KEEP)),
            "auth_event_addresses": await _clear_restores(db, now - RESTORE_KEEP),
            "mail_suppressions": await _count(
                db,
                delete(MailSuppression).where(
                    MailSuppression.expires_at.is_not(None), MailSuppression.expires_at <= now
                ),
            ),
        }
        await db.commit()
    done = {name: count for name, count in counts.items() if count}
    if done:
        logger.info("Sign-up and mail clean-up: %s", ", ".join(f"{name} {count}" for name, count in done.items()))
    return counts
