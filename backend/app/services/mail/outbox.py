"""The email outbox (docs/briefs/signup-auth.md 6.3, 6.5 and 6.6): requests only add a row in their own transaction
(enqueue) and wake the worker after their commit; the worker resolves and sends it. MAIL_DAILY_LIMIT per UTC day is
shared by who can trigger the mail: `public` (visitors and unpaid accounts) up to 40% and `reset` up to 30%, both
counted when created whatever they became (and an earlier day's job again on the day it is sent); with `account` at most
80% together, so `billing` always has 20%: what a class has past its share is held, so it takes no other class's room.
Suppressed addresses (bounces, complaints) aren't mailed, but for one verification or confirmation a day that someone
just asked for at the address (drain.py), and bounces can pause the `public` class for the day."""

import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Final, Literal

from redis.exceptions import RedisError
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EmailOutbox, MailSuppression, utcnow
from app.redis_client import get_redis
from app.services.auth import emails
from app.services.billing import paid
from app.services.billing.money import IST

logger = logging.getLogger(__name__)

MailKind = Literal[
    "signup",
    "password_reset",
    "password_changed",
    "password_added",
    "email_confirm",
    "email_changed",
    "email_undone",
    "google_linked",
    "google_unlinked",
    "receipt",
    "plan_reminder",
    "test",
]
MailClass = Literal["public", "reset", "account", "billing"]


@dataclass(frozen=True)
class KindRule:
    mail_class: MailClass | None  # None: "public" until the user has paid, then "account"
    priority: int  # 0 goes first
    not_after: timedelta  # unsent this long after it was created, the job expires


_NOTICE = KindRule(None, 0, timedelta(days=3))
KINDS: Final[Mapping[MailKind, KindRule]] = MappingProxyType(
    {
        "signup": KindRule("public", 1, timedelta(hours=6)),
        "password_reset": KindRule("reset", 0, timedelta(hours=6)),
        "email_confirm": KindRule(None, 0, timedelta(hours=6)),
        "email_changed": _NOTICE,
        "email_undone": _NOTICE,
        "password_changed": _NOTICE,
        "password_added": _NOTICE,
        "google_linked": _NOTICE,
        "google_unlinked": _NOTICE,
        "receipt": KindRule("billing", 2, timedelta(days=3)),
        "plan_reminder": KindRule("billing", 2, timedelta(days=1)),
        "test": KindRule("account", 2, timedelta(hours=1)),
    }
)
PUBLIC_SHARE, RESET_SHARE, NON_BILLING_SHARE = 40, 30, 80  # percent of MAIL_DAILY_LIMIT
PAUSE_BOUNCES, PAUSE_RATE = 3, 0.02  # today's bounces, and their share of today's sent mail, that pause `public`
BOUNCE_SUPPRESSION: Final = timedelta(days=30)
PENDING: Final = ("queued", "sending")
WAKE_QUEUE: Final = "rag:mail_wake"
_WAKE_BACKLOG = 100  # wake-ups are only hints: the worker also looks every 5 s
_LINK_OR_TOKEN = re.compile(r"://|[A-Za-z0-9_-]{32,}")
_MASKED_ADDRESS = re.compile(r"[^@\s]?\*\*\*@[^@\s/:#]+")  # emails.mask(); a domain label may be 63 characters


@dataclass(frozen=True)
class MailStatus:
    sent_today: int
    queued: int
    limit: int
    public_left: int
    public_paused: bool
    bounces_today: int
    complaints_7d: int
    last_error: str | None


def configured() -> bool:
    """MAIL_SMTP_HOST and MAIL_FROM are set."""
    return settings.mail_configured


async def class_of(db: AsyncSession, kind: MailKind, user_id: int | None = None) -> MailClass:
    """The kind's class: a notice or a confirmation is `public` until the user has paid, then `account`."""
    mail_class = KINDS[kind].mail_class
    if mail_class is not None:
        return mail_class
    return "account" if user_id is not None and await paid.has_paid(db, user_id) else "public"


async def enqueue(
    db: AsyncSession,
    kind: MailKind,
    *,
    to: str,
    canonical: str | None = None,
    user_id: int | None = None,
    pending_signup_id: int | None = None,
    pending_email_id: int | None = None,
    params: Mapping[str, str | int | bool | None] | None = None,
) -> None:
    """Adds a job in the caller's transaction (never commits; call wake() after the commit). `to` is a stored or
    parsed address; `canonical` defaults to it lowercased. `params` holds ids, a plan name or a reminder kind, never a
    token, link or secret: the worker mints links when it sends."""
    for name, value in (params or {}).items():
        if isinstance(value, str) and not _MASKED_ADDRESS.fullmatch(value) and _LINK_OR_TOKEN.search(value):
            raise ValueError(f"params[{name!r}] looks like a link or a token")
    rule = KINDS[kind]
    mail_class = await class_of(db, kind, user_id)
    canonical = canonical or to.lower()
    now = utcnow()
    db.add(
        EmailOutbox(
            public_id=secrets.token_urlsafe(18),
            kind=kind,
            priority=rule.priority,
            mail_class=mail_class,
            to_address=to,
            canonical=canonical,
            recipient_sha256=emails.digest(canonical),
            user_id=user_id,
            pending_signup_id=pending_signup_id,
            pending_email_id=pending_email_id,
            params=dict(params or {}),
            status="queued",
            attempts=0,
            next_attempt_at=now,
            not_after=now + rule.not_after,
            created_at=now,
        )
    )


async def wake() -> None:
    """Tells the worker a job is waiting (after the caller's commit). Best effort: it looks every 5 s anyway."""
    try:
        async with get_redis().pipeline(transaction=True) as pipe:
            pipe.rpush(WAKE_QUEUE, "1")
            pipe.ltrim(WAKE_QUEUE, -_WAKE_BACKLOG, -1)
            await pipe.execute()
    except RedisError as exc:
        logger.warning("Could not wake the mail worker (%s); it finds the job within seconds", type(exc).__name__)


# ---------------- The daily cap (6.5) ----------------


def _day(now: datetime) -> tuple[datetime, datetime]:
    """Today in UTC: [00:00, the next 00:00)."""
    start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _share(percent: int) -> int:
    return settings.mail_daily_limit * percent // 100


@dataclass(frozen=True)
class _Used:
    public: int  # created today, whatever they became, and an earlier day's jobs sent today or due today
    reset: int  # likewise
    account: int  # sent today, or queued or sending and due today
    billing: int  # likewise

    @property
    def non_billing(self) -> int:
        """`public` and `reset` within their shares, and the three within their 80%: a job past its share is held, so
        it never takes another class's room."""
        within = min(self.public, _share(PUBLIC_SHARE)) + min(self.reset, _share(RESET_SHARE)) + self.account
        return min(within, _share(NON_BILLING_SHARE))


async def _used(db: AsyncSession, now: datetime, before: EmailOutbox | None = None) -> _Used:
    """Today's use of the cap by class. With `before` (a job the worker is about to send), its own class counts what
    was sent today and, of the rest, only the jobs ahead of it, so the first ones within a share go even when more were
    queued."""
    start, end = _day(now)
    sent = and_(EmailOutbox.sent_at >= start, EmailOutbox.sent_at < end)
    due = and_(EmailOutbox.status.in_(PENDING), EmailOutbox.next_attempt_at < end)
    made = or_(
        and_(EmailOutbox.created_at >= start, EmailOutbox.created_at < end),
        and_(EmailOutbox.created_at < start, due),  # held over from an earlier day: it counts the day it goes
    )
    if before is not None:
        ahead = or_(EmailOutbox.mail_class != before.mail_class, EmailOutbox.id < before.id)
        made, due = and_(made, ahead), and_(due, ahead)
    rows = await db.execute(
        select(
            EmailOutbox.mail_class,
            func.sum(case((sent, 1), (made, 1), else_=0)),
            func.sum(case((sent, 1), (due, 1), else_=0)),
        ).group_by(EmailOutbox.mail_class)
    )
    counts = {mail_class: (int(made or 0), int(used or 0)) for mail_class, made, used in rows}
    return _Used(
        public=counts.get("public", (0, 0))[0],
        reset=counts.get("reset", (0, 0))[0],
        account=counts.get("account", (0, 0))[1],
        billing=counts.get("billing", (0, 0))[1],
    )


def _fits(mail_class: str, used: _Used) -> bool:
    """One more job of the class stays within its share, the 80% of the non-billing classes and the limit."""
    limit, non_billing = settings.mail_daily_limit, used.non_billing
    if mail_class == "public":
        return used.public < _share(PUBLIC_SHARE) and non_billing < _share(NON_BILLING_SHARE)
    if mail_class == "reset":
        return used.reset < _share(RESET_SHARE) and non_billing < _share(NON_BILLING_SHARE)
    if mail_class == "account":
        return non_billing < _share(NON_BILLING_SHARE) and non_billing + used.billing < limit
    return non_billing + used.billing < limit


async def available(db: AsyncSession, cls: MailClass) -> bool:
    """Whether a request may add a job of the class now (else 503 mail_unavailable): the mailer is configured, the
    class has room today, and `public` isn't paused by bounces."""
    if not configured() or (cls == "public" and await public_paused(db)):
        return False
    return _fits(cls, await _used(db, utcnow()))


async def within_share(db: AsyncSession, job: EmailOutbox) -> bool:
    """For the worker, before it sends `job`: its class still has room today counting what was sent today and the jobs
    ahead of it, and a `public` job isn't paused. False: hold it until the next 00:00 UTC (next_day())."""
    if job.mail_class == "public" and await public_paused(db):
        return False
    return _fits(job.mail_class, await _used(db, utcnow(), before=job))


def next_day(now: datetime | None = None) -> datetime:
    """The next 00:00 UTC, when the shares start again."""
    return _day(now or utcnow())[1]


async def public_left(db: AsyncSession) -> int:
    """About how many `public` jobs (sign-up mails) still fit today; 0 while unconfigured or paused."""
    if not configured() or await public_paused(db):
        return 0
    used = await _used(db, utcnow())
    return max(0, min(_share(PUBLIC_SHARE) - used.public, _share(NON_BILLING_SHARE) - used.non_billing))


async def _sent_today(db: AsyncSession, now: datetime) -> int:
    start, end = _day(now)
    count = await db.scalar(
        select(func.count()).select_from(EmailOutbox).where(EmailOutbox.sent_at >= start, EmailOutbox.sent_at < end)
    )
    return int(count or 0)


async def _suppressions(db: AsyncSession, reason: str, since: datetime) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(MailSuppression)
        .where(MailSuppression.reason == reason, MailSuppression.created_at >= since)
    )
    return int(count or 0)


async def public_paused(db: AsyncSession) -> bool:
    """Today's bounces reached 3 and at least 2% of today's sent mail: `public` waits for the next 00:00 UTC."""
    now = utcnow()
    bounces = await _suppressions(db, "bounce", _day(now)[0])
    return bounces >= PAUSE_BOUNCES and bounces >= PAUSE_RATE * await _sent_today(db, now)


# ---------------- Suppressions (6.6) ----------------


async def suppressed(db: AsyncSession, recipient_sha256: str) -> bool:
    row = await db.get(MailSuppression, recipient_sha256)
    return row is not None and (row.expires_at is None or row.expires_at > utcnow())


async def suppress(
    db: AsyncSession,
    recipient_sha256: str,
    *,
    reason: Literal["bounce", "complaint"],
    source: Literal["webhook", "smtp"],
) -> None:
    """Suppresses an address (a bounce for 30 days, a complaint until the address is confirmed again) and counts it as
    today's (the bounce pause, the monitor). A complaint stays a complaint when a bounce follows. The caller commits."""
    now = utcnow()
    expires_at = now + BOUNCE_SUPPRESSION if reason == "bounce" else None
    row = await db.get(MailSuppression, recipient_sha256, with_for_update=True, populate_existing=True)
    if row is None:
        try:
            async with db.begin_nested():
                db.add(
                    MailSuppression(
                        recipient_sha256=recipient_sha256,
                        reason=reason,
                        source=source,
                        created_at=now,
                        expires_at=expires_at,
                    )
                )
            return
        except IntegrityError:  # a report for the same address arrived at the same time
            row = await db.get(MailSuppression, recipient_sha256, with_for_update=True, populate_existing=True)
            assert row is not None
    if row.reason == "complaint" and reason == "bounce":
        return
    row.reason, row.source, row.created_at, row.expires_at = reason, source, now, expires_at


async def unsuppress(db: AsyncSession, recipient_sha256: str) -> None:
    """A user confirmed the address (delivery proven): mail may go there again. The caller commits."""
    await db.execute(delete(MailSuppression).where(MailSuppression.recipient_sha256 == recipient_sha256))


# ---------------- Status (Admin → Users → Sign-up, the `mail` check) ----------------


def _ist(instant: datetime) -> str:
    local = instant.astimezone(IST)
    return f"{local.day} {local:%b %Y, %H:%M} IST"


async def mail_status(db: AsyncSession) -> MailStatus:
    """Counts only, never addresses. `last_error`: the newest failed or retrying job's short reason, with when it
    failed for good."""
    now = utcnow()
    queued = await db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status.in_(PENDING)))
    newest = (
        await db.execute(
            select(EmailOutbox.last_error, EmailOutbox.finished_at)
            .where(EmailOutbox.last_error.is_not(None), EmailOutbox.status.in_(("failed", "queued")))
            .order_by(EmailOutbox.id.desc())
            .limit(1)
        )
    ).first()
    last_error = None
    if newest is not None:
        last_error = f"{newest.last_error} ({_ist(newest.finished_at)})" if newest.finished_at else newest.last_error
    return MailStatus(
        sent_today=await _sent_today(db, now),
        queued=int(queued or 0),
        limit=settings.mail_daily_limit,
        public_left=await public_left(db),
        public_paused=await public_paused(db),
        bounces_today=await _suppressions(db, "bounce", _day(now)[0]),
        complaints_7d=await _suppressions(db, "complaint", now - timedelta(days=7)),
        last_error=last_error,
    )
