"""The worker's mail loop (docs/briefs/signup-auth.md 6.3): one job at a time, in priority order. A claimed job past its
`not_after` expires; one over its class's share of the day waits for the next UTC day; a suppressed recipient (but for
one verification or confirmation a day that someone just asked for there) or a job that resolves to nothing is
cancelled; otherwise the token is minted and committed, then the mail is sent outside any transaction. Temporary
failures, and a job that couldn't be prepared, retry after 1 min, 5 min, 30 min, 2 h and 6 h, then fail; a permanent
one fails at once (a refused recipient is also a bounce); a refused login pauses every job for 15 minutes. A job a
stopped worker left "sending" is queued again (a stopping loop puts its own back; else after 2 minutes, at start-up and
every minute). A finished job keeps only the recipient's hash. `public` and `reset` jobs take at least 2 s of the
loop's time, sent or not (S6). Logs name the job, never the address."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Any, Final

from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import EmailOutbox, PendingEmail, utcnow
from app.redis_client import get_redis
from app.services.auth import emails
from app.services.mail import jobs, outbox, transport

logger = logging.getLogger(__name__)

WAIT_SECONDS: Final = 5
STALE_SENDING: Final = timedelta(minutes=2)  # a job left "sending" this long by a stopped worker is queued again
RECOVER_SECONDS: Final = 60.0  # how often the running loop looks for them
BACKOFF: Final = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)
AUTH_PAUSE: Final = timedelta(minutes=15)
PACED: Final = ("public", "reset")
PACE_SECONDS: Final = 2.0
SEND_TIMEOUT_SECONDS: Final = 90.0  # the whole message; each SMTP operation has its own 15 s
CONFIRM_LIFETIME: Final = timedelta(hours=24)  # a pending email lives this long after its last mail
PREPARE_FAILED: Final = "couldn't be prepared"
OPT_IN_EVERY: Final = timedelta(days=1)  # a suppressed address gets one opt-in mail (jobs.Outgoing.opt_in) this often
_PAUSE_KEY = "mail:paused"
_OPT_IN_KEY = "mail:opt_in:"


async def paused() -> bool:
    """Sending is paused after a refused SMTP login (the `mail` check warns)."""
    try:
        return bool(await get_redis().exists(_PAUSE_KEY))
    except RedisError:
        return False


async def _pause() -> None:
    with contextlib.suppress(RedisError):
        await get_redis().set(_PAUSE_KEY, "SMTP login refused", ex=int(AUTH_PAUSE.total_seconds()))


async def recover() -> int:
    """Jobs a stopped worker left "sending" for over 2 minutes (longer than a send takes) are queued again, at start-up
    and every minute: their idempotency key lets Resend drop a duplicate of a message that did go out."""
    async with SessionLocal() as db:
        result = await db.execute(
            update(EmailOutbox)
            .where(EmailOutbox.status == "sending", EmailOutbox.next_attempt_at < utcnow() - STALE_SENDING)
            .values(status="queued")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    count = int(getattr(result, "rowcount", 0) or 0)
    if count:
        logger.info("Queued %d mail job(s) left sending by a previous run", count)
    return count


async def _claim(db: AsyncSession) -> EmailOutbox | None:
    """The next due job, marked "sending" (its next_attempt_at becomes the claim time) and committed. Two loops never
    claim one job: the row is locked (skipped by the other on Postgres) and the update is conditional."""
    now = utcnow()
    for _ in range(3):
        job_id = await db.scalar(
            select(EmailOutbox.id)
            .where(EmailOutbox.status == "queued", EmailOutbox.next_attempt_at <= now)
            .order_by(EmailOutbox.priority, EmailOutbox.next_attempt_at, EmailOutbox.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job_id is None:
            await db.rollback()
            return None
        claimed = await db.execute(
            update(EmailOutbox)
            .where(EmailOutbox.id == job_id, EmailOutbox.status == "queued")
            .values(status="sending", attempts=EmailOutbox.attempts + 1, next_attempt_at=now)
            .returning(EmailOutbox.id)
            .execution_options(synchronize_session=False)
        )
        if claimed.first() is not None:
            await db.commit()
            job: EmailOutbox | None = await db.get(EmailOutbox, job_id, populate_existing=True)
            if job is not None:
                db.expunge(job)  # a snapshot: rolling back what resolving wrote leaves it readable
            return job
        await db.rollback()
    return None


async def _finish(db: AsyncSession, job: EmailOutbox, status: str, reason: str | None = None) -> None:
    """Ends the job, clearing its address (the hash stays for counting); commits."""
    now = utcnow()
    await db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.id == job.id)
        .values(
            status=status,
            finished_at=now,
            sent_at=now if status == "sent" else None,
            to_address=None,
            canonical=None,
            last_error=reason,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()


async def _requeue(db: AsyncSession, job: EmailOutbox, at: datetime, *, counted: bool, reason: str | None) -> None:
    """Back to the queue until `at`; an attempt that never reached the server (`counted` false) isn't counted."""
    values: dict[str, Any] = {"status": "queued", "next_attempt_at": at}
    if not counted:
        values["attempts"] = EmailOutbox.attempts - 1
    if reason is not None:
        values["last_error"] = reason
    await db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.id == job.id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await db.commit()


async def _release(job: EmailOutbox) -> None:
    """A stopping loop's claimed job back to the queue, the attempt not counted (only while it is still "sending": a
    job the stop caught after its result was written stays as it is)."""
    try:
        async with SessionLocal() as db:
            result = await db.execute(
                update(EmailOutbox)
                .where(EmailOutbox.id == job.id, EmailOutbox.status == "sending")
                .values(status="queued", attempts=EmailOutbox.attempts - 1, next_attempt_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            await db.commit()
    except Exception as exc:
        name = type(exc).__name__
        logger.warning("mail %s kind=%s stays sending until it is recovered (%s)", job.public_id, job.kind, name)
        return
    if getattr(result, "rowcount", 0):
        logger.info("mail %s kind=%s queued again: the worker is stopping", job.public_id, job.kind)


async def _opt_in(recipient_sha256: str, job: EmailOutbox) -> bool:
    """Whether a suppressed address may get this confirmation: one a day (its retries included), since confirming the
    address is what ends its suppression (6.6). Without Redis: no."""
    key = f"{_OPT_IN_KEY}{recipient_sha256}"
    try:
        redis = get_redis()
        if await redis.set(key, job.public_id, nx=True, ex=int(OPT_IN_EVERY.total_seconds())):
            return True
        return bool(await redis.get(key) == job.public_id)
    except RedisError:
        return False


async def _prepare(db: AsyncSession, job: EmailOutbox) -> jobs.Outgoing | None:
    """The claimed job's mail, its token committed; None when the job ended or waits."""
    if job.not_after <= utcnow():
        await _finish(db, job, "expired")
        logger.info("mail %s kind=%s expired", job.public_id, job.kind)
        return None
    if not await outbox.within_share(db, job):
        await _requeue(db, job, outbox.next_day(), counted=False, reason=None)
        logger.info("mail %s kind=%s waits for the next day: its class's share is used", job.public_id, job.kind)
        return None
    suppressed = await outbox.suppressed(db, job.recipient_sha256)
    result: jobs.Result
    if suppressed and job.kind not in jobs.OPT_IN:
        result = jobs.Skip("suppressed")
    else:
        result = await jobs.resolve(db, job)
    if isinstance(result, jobs.Outgoing):
        hashed = emails.digest(result.to.lower())
        if hashed != job.recipient_sha256:
            suppressed = await outbox.suppressed(db, hashed)
        if suppressed and not (result.opt_in and await _opt_in(hashed, job)):
            await db.rollback()
            result = jobs.Skip("suppressed")
    if isinstance(result, jobs.Skip):
        await _finish(db, job, "cancelled", result.reason)
        logger.info("mail %s kind=%s cancelled: %s", job.public_id, job.kind, result.reason)
        return None
    await db.commit()
    return result


def _domain() -> str:
    return parseaddr(settings.mail_from)[1].rpartition("@")[2] or "localhost"


def message(job: EmailOutbox, outgoing: jobs.Outgoing) -> EmailMessage:
    """One To; text first, then HTML (multipart/mixed with attachments); the idempotency header."""
    mail = EmailMessage()
    mail["Subject"] = outgoing.mail.subject
    mail["From"] = settings.mail_from
    if settings.mail_reply_to:
        mail["Reply-To"] = settings.mail_reply_to
    mail["To"] = outgoing.to
    mail["Date"] = formatdate(usegmt=True)
    mail["Message-ID"] = make_msgid(domain=_domain())
    mail["Auto-Submitted"] = "auto-generated"
    if settings.mail_idempotency_header:
        mail[settings.mail_idempotency_header] = f"mail-{job.public_id}"
    mail.set_content(outgoing.mail.text)
    mail.add_alternative(outgoing.mail.html, subtype="html")
    for attachment in outgoing.attachments:
        mail.add_attachment(
            attachment.content, maintype=attachment.maintype, subtype=attachment.subtype, filename=attachment.filename
        )
    return mail


async def _deliver(mail: EmailMessage) -> transport.MailError | None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(transport.send_message, mail, transport.mail_config()), SEND_TIMEOUT_SECONDS
        )
    except transport.MailError as exc:
        return exc
    except TimeoutError:
        return transport.MailError("temporary", "timed out")
    except Exception as exc:
        logger.warning("The SMTP transport failed unexpectedly (%s)", type(exc).__name__)
        return transport.MailError("temporary", f"failed ({type(exc).__name__})")
    return None


async def _send(job: EmailOutbox, outgoing: jobs.Outgoing) -> None:
    started = time.monotonic()
    error = await _deliver(message(job, outgoing))
    elapsed = round((time.monotonic() - started) * 1000)
    now = utcnow()
    async with SessionLocal() as db:
        if error is None:
            if outgoing.pending_email_id is not None:
                await db.execute(
                    update(PendingEmail)
                    .where(PendingEmail.id == outgoing.pending_email_id)
                    .values(sent_at=now, expires_at=now + CONFIRM_LIFETIME)
                    .execution_options(synchronize_session=False)
                )
            await _finish(db, job, "sent")
            logger.info("mail %s kind=%s sent in %d ms", job.public_id, job.kind, elapsed)
        elif error.kind == "auth":
            await _requeue(db, job, now + AUTH_PAUSE, counted=False, reason=error.reason)
            await _pause()
            logger.warning(
                "mail %s kind=%s not sent: %s; sending paused for 15 minutes", job.public_id, job.kind, error
            )
        elif error.kind == "permanent" or job.attempts > len(BACKOFF):
            if error.bounce:
                await outbox.suppress(db, emails.digest(outgoing.to.lower()), reason="bounce", source="smtp")
            await _finish(db, job, "failed", error.reason)
            logger.warning("mail %s kind=%s failed: %s", job.public_id, job.kind, error)
        else:
            await _requeue(db, job, now + BACKOFF[job.attempts - 1], counted=True, reason=error.reason)
            logger.info("mail %s kind=%s will be retried: %s", job.public_id, job.kind, error)


async def _not_prepared(db: AsyncSession, job: EmailOutbox, error: Exception) -> None:
    """Resolving or rendering raised: an attempt like a temporary failure (the traceback logged once per job)."""
    await db.rollback()
    if job.last_error == PREPARE_FAILED:
        name = type(error).__name__
        logger.warning("mail %s kind=%s couldn't be prepared again (%s)", job.public_id, job.kind, name)
    else:
        logger.error("mail %s kind=%s couldn't be prepared", job.public_id, job.kind, exc_info=error)
    if job.attempts > len(BACKOFF):
        await _finish(db, job, "failed", PREPARE_FAILED)
        logger.warning("mail %s kind=%s failed: it couldn't be prepared", job.public_id, job.kind)
    else:
        await _requeue(db, job, utcnow() + BACKOFF[job.attempts - 1], counted=True, reason=PREPARE_FAILED)


async def next_job() -> EmailOutbox | None:
    """Claims, resolves and sends the next due job (or ends it); returns it, or None when none is due, the mailer
    isn't configured or sending is paused. Cancelled (the worker stopping) after the claim, the job goes back to the
    queue."""
    if not outbox.configured() or await paused():
        return None
    async with SessionLocal() as db:
        job = await _claim(db)
        if job is None:
            return None
        try:
            outgoing = await _prepare(db, job)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await db.rollback()  # its locks, before the job is put back
            await _release(job)
            raise
        except Exception as exc:
            await _not_prepared(db, job, exc)
            return job
    if outgoing is not None:
        try:
            await _send(job, outgoing)
        except asyncio.CancelledError:
            await _release(job)
            raise
    return job


async def process_one() -> bool:
    """One job without the loop's pacing (the tests' drain_mail); False when none was due."""
    return await next_job() is not None


class MailLoop:
    """Runs in the worker beside the ingestion jobs while the mailer is configured: woken by `rag:mail_wake`, it also
    looks every 5 s. `clock` and `sleep` are the pacing's (tests pass fakes)."""

    def __init__(
        self,
        stopping: asyncio.Event,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._stopping = stopping
        self._clock = clock
        self._sleep = sleep

    @staticmethod
    def wanted() -> bool:
        return outbox.configured()

    async def run(self) -> None:
        recovered_at: float | None = None
        while not self._stopping.is_set():
            if recovered_at is None or self._clock() - recovered_at >= RECOVER_SECONDS:
                await self._recover()
                recovered_at = self._clock()
            if not await self.step():
                await self._wait()

    @staticmethod
    async def _recover() -> None:
        try:
            await recover()
        except Exception as exc:
            logger.warning("Could not look for mail jobs left sending (%s)", type(exc).__name__)

    async def step(self) -> bool:
        """One job, paced; False when none was due (or it failed unexpectedly)."""
        started = self._clock()
        try:
            job = await next_job()
        except Exception:
            logger.exception("The mail loop failed")
            return False
        if job is None:
            return False
        if job.mail_class in PACED:
            left = PACE_SECONDS - (self._clock() - started)
            if left > 0:
                await self._sleep(left)
        return True

    async def _wait(self) -> None:
        try:
            await get_redis().blpop([outbox.WAKE_QUEUE], timeout=WAIT_SECONDS)
        except (RedisError, OSError) as exc:
            logger.warning("Redis unavailable for the mail loop (%s)", type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), WAIT_SECONDS)
