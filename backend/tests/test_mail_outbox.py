"""The worker's side of the email outbox (app.services.mail.drain, jobs and sweeps; docs/briefs/signup-auth.md 3.7, 6.3
to 6.6): the drain order, `not_after`, retries and backoff, permanent and auth errors (the 15-minute pause), a crash
while sending recovered with the same idempotency key, the message's headers, tokens minted per attempt, addresses
cleared when a job finishes, the 2 s pacing of public jobs, each kind's resolution, preferences, missing emails and
suppressed recipients (and the one confirmation a day they may get), the daily shares, a job a stopped worker left
sending, a job that can't be prepared, the worker running the loop and the sweeps (and letting a send finish when it
stops), database errors that never log an address, and the clean-up itself. The request side is test_mail_enqueue.py."""

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import pytest
from conftest import drain_mail
from fake_mail import FakeMail, html, text
from fastapi import FastAPI
from sqlalchemy import event, func, select, update

from app import db, worker
from app.config import settings
from app.models import (
    AuthEvent,
    AuthToken,
    EmailOutbox,
    MailSuppression,
    PendingEmail,
    PendingSignup,
    User,
    UserIdentity,
    UserPreference,
    utcnow,
)
from app.redis_client import get_redis
from app.services.auth import emails, tokens
from app.services.mail import drain, outbox, sweeps, transport

pytestmark = pytest.mark.usefixtures("app")

ANN = "Ann.Lee@example.com"  # as stored: the local part as typed, the domain lowercased


# ---------------- Helpers ----------------


async def add_user(
    username: str, *, email: str | None = None, password: bool = True, active: bool = True, admin: bool = False
) -> int:
    async with db.SessionLocal() as session:
        user = User(
            username=username,
            password_hash="x" if password else None,
            is_admin=admin,
            is_active=active,
            email=email,
            email_canonical=email.lower() if email else None,
            email_verified_at=utcnow() if email else None,
        )
        session.add(user)
        await session.commit()
        return user.id


async def add_pending_signup(address: str, *, age: timedelta = timedelta(0), live: bool = True) -> int:
    now = utcnow() - age
    async with db.SessionLocal() as session:
        row = PendingSignup(
            address=address,
            canonical=address.lower(),
            password_hash="x",
            terms_version="2026-11-01",
            terms_accepted_at=now,
            created_at=now,
            expires_at=now + timedelta(days=7) if live else utcnow() - timedelta(seconds=1),
        )
        session.add(row)
        await session.commit()
        return row.id


async def add_pending_email(user_id: int, address: str, *, source: str = "user") -> int:
    async with db.SessionLocal() as session:
        row = PendingEmail(
            user_id=user_id,
            address=address,
            canonical=address.lower(),
            source=source,
            expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(row)
        await session.commit()
        return row.id


async def enqueue(kind: outbox.MailKind, to: str = ANN, **fields: Any) -> int:
    async with db.SessionLocal() as session:
        await outbox.enqueue(session, kind, to=to, **fields)
        await session.commit()
        job_id = await session.scalar(select(func.max(EmailOutbox.id)))
    assert job_id is not None
    return job_id


async def job(job_id: int) -> EmailOutbox:
    async with db.SessionLocal() as session:
        row = await session.get(EmailOutbox, job_id)
    assert row is not None
    return row


async def change(job_id: int, **values: Any) -> None:
    async with db.SessionLocal() as session:
        await session.execute(update(EmailOutbox).where(EmailOutbox.id == job_id).values(**values))
        await session.commit()


async def due_now(job_id: int) -> None:
    await change(job_id, next_attempt_at=utcnow())


async def live_token(purpose: tokens.TokenPurpose, token: str) -> AuthToken | None:
    async with db.SessionLocal() as session:
        return await tokens.find(session, [purpose], token)


async def count(model: Any) -> int:
    async with db.SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def raising(error: transport.MailError) -> Callable[[EmailMessage, transport.SmtpConfig], None]:
    def send(message: EmailMessage, smtp: transport.SmtpConfig) -> None:
        raise error

    return send


def close_to(moment: datetime, expected: datetime, seconds: float = 60) -> bool:
    return abs((moment - expected).total_seconds()) <= seconds


# ---------------- The loop ----------------


async def test_jobs_go_by_priority_then_due_time_then_id(fake_mail: FakeMail) -> None:
    first = await enqueue("test", to="a@example.com")
    second = await enqueue("test", to="b@example.com")
    third = await enqueue("test", to="c@example.com")
    await change(first, priority=2)
    await change(second, priority=1, next_attempt_at=utcnow() - timedelta(seconds=5))
    await change(third, priority=1, next_attempt_at=utcnow() - timedelta(seconds=10))

    assert await drain_mail() == 3

    assert [str(message["To"]) for message in fake_mail.sent] == ["c@example.com", "b@example.com", "a@example.com"]


async def test_nothing_runs_without_the_mailer_or_before_its_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "mail_from", "AskNuvexusDoc <no-reply@example.test>")
    later = await enqueue("test")
    await change(later, next_attempt_at=utcnow() + timedelta(minutes=5))
    monkeypatch.setattr(settings, "mail_smtp_host", "")

    assert await drain.process_one() is False
    monkeypatch.setattr(settings, "mail_smtp_host", "smtp.example.test")
    assert await drain.process_one() is False
    assert (await job(later)).status == "queued"


async def test_a_sent_job_keeps_only_the_recipients_hash(fake_mail: FakeMail, caplog: pytest.LogCaptureFixture) -> None:
    job_id = await enqueue("test")
    caplog.set_level(logging.INFO, logger="app.services.mail")

    await drain_mail()

    row = await job(job_id)
    assert (row.status, row.attempts, row.to_address, row.canonical) == ("sent", 1, None, None)
    assert row.recipient_sha256 == emails.digest(ANN.lower())
    assert row.sent_at is not None and row.finished_at is not None
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"mail {row.public_id} kind=test sent in " in logged
    assert ANN.lower() not in logged.lower()


async def test_the_message_and_its_headers(fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_reply_to", "support@example.test")
    job_id = await enqueue("test")

    await drain_mail()

    message = fake_mail.last(ANN)
    row = await job(job_id)
    assert message["From"] == "AskNuvexusDoc <no-reply@example.test>"
    assert message["Reply-To"] == "support@example.test"
    assert message.get_all("To") == [ANN]
    assert message["Subject"] == "Test email from AskNuvexusDoc"
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Message-ID"].endswith("@example.test>")
    assert message["Date"] and message["MIME-Version"] == "1.0"
    assert message["Resend-Idempotency-Key"] == f"mail-{row.public_id}"
    assert message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in message.iter_parts()] == ["text/plain", "text/html"]
    assert "Mail from AskNuvexusDoc reaches this address." in text(message)
    assert "Questions? Reply to this email." in html(message)


async def test_the_idempotency_header_can_be_renamed_or_left_out(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mail_idempotency_header", "Idempotency-Key")
    await enqueue("test", to="a@example.com")
    await drain_mail()
    monkeypatch.setattr(settings, "mail_idempotency_header", "")
    await enqueue("test", to="b@example.com")
    await drain_mail()

    assert fake_mail.last("a@example.com")["Idempotency-Key"].startswith("mail-")
    assert "Resend-Idempotency-Key" not in fake_mail.last("b@example.com")
    assert all(name.lower() != "idempotency-key" for name in fake_mail.last("b@example.com").keys())


async def test_past_not_after_a_job_expires(fake_mail: FakeMail) -> None:
    job_id = await enqueue("test")
    await change(job_id, not_after=utcnow() - timedelta(seconds=1))

    assert await drain_mail() == 1

    row = await job(job_id)
    assert (row.status, row.to_address, row.canonical, row.sent_at) == ("expired", None, None, None)
    assert fake_mail.sent == []


async def test_temporary_failures_retry_with_backoff_then_fail(fake_mail: FakeMail) -> None:
    job_id = await enqueue("test")
    fake_mail.fail_next("temporary", "connection failed", times=6)
    delays = []

    for _ in range(6):
        await due_now(job_id)
        started = utcnow()
        assert await drain_mail() == 1
        row = await job(job_id)
        if row.status == "queued":
            delays.append(round((row.next_attempt_at - started).total_seconds() / 60))
            assert row.last_error == "connection failed" and row.to_address == ANN

    row = await job(job_id)
    assert delays == [1, 5, 30, 120, 360]
    assert (row.status, row.attempts, row.last_error, row.to_address) == ("failed", 6, "connection failed", None)
    assert fake_mail.attempts == 6 and fake_mail.sent == []


async def test_a_permanent_failure_fails_at_once(fake_mail: FakeMail) -> None:
    job_id = await enqueue("test")
    fake_mail.fail_next("permanent", "message refused (552)")

    await drain_mail()

    row = await job(job_id)
    assert (row.status, row.attempts, row.last_error, row.canonical) == ("failed", 1, "message refused (552)", None)
    assert await count(MailSuppression) == 0


async def test_a_refused_recipient_is_suppressed_as_a_bounce(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.mail.transport.send_message",
        raising(transport.MailError("permanent", "recipient refused (550)", bounce=True)),
    )
    job_id = await enqueue("test")
    await drain_mail()
    later = await enqueue("test")
    monkeypatch.setattr("app.services.mail.transport.send_message", fake_mail.send)

    await drain_mail()

    assert (await job(job_id)).status == "failed"
    assert ((await job(later)).status, (await job(later)).last_error) == ("cancelled", "suppressed")
    async with db.SessionLocal() as session:
        row = await session.get(MailSuppression, emails.digest(ANN.lower()))
        status = await outbox.mail_status(session)
    assert row is not None and (row.reason, row.source) == ("bounce", "smtp")
    assert status.bounces_today == 1 and status.last_error is not None
    assert fake_mail.sent == []


async def test_a_refused_login_pauses_every_job_for_15_minutes(fake_mail: FakeMail) -> None:
    job_id = await enqueue("test", to="a@example.com")
    other = await enqueue("test", to="b@example.com")
    fake_mail.fail_next("auth", "SMTP login refused")
    started = utcnow()

    assert await drain_mail() == 1

    row = await job(job_id)
    assert (row.status, row.attempts, row.last_error) == ("queued", 0, "SMTP login refused")
    assert close_to(row.next_attempt_at, started + timedelta(minutes=15))
    assert await drain.paused() is True
    assert await drain.process_one() is False and (await job(other)).status == "queued"
    assert 800 <= await get_redis().ttl("mail:paused") <= 900

    await get_redis().delete("mail:paused")
    assert await drain_mail() == 1
    assert (await job(other)).status == "sent"


async def test_a_job_left_sending_by_a_stopped_worker_is_sent_again_with_its_key(fake_mail: FakeMail) -> None:
    stale = await enqueue("test", to="a@example.com")
    recent = await enqueue("test", to="b@example.com")
    await change(stale, status="sending", attempts=1, next_attempt_at=utcnow() - timedelta(minutes=3))
    await change(recent, status="sending", attempts=1, next_attempt_at=utcnow() - timedelta(seconds=30))

    assert await drain.recover() == 1
    await drain_mail()

    row = await job(stale)
    assert (row.status, row.attempts) == ("sent", 2)
    assert fake_mail.last("a@example.com")["Resend-Idempotency-Key"] == f"mail-{row.public_id}"
    assert (await job(recent)).status == "sending"


def held_send(entered: threading.Event, gate: threading.Event, then: Callable[..., None] | None = None) -> Any:
    """An SMTP conversation that goes on until `gate` is set (it sets `entered` when it starts)."""

    def send(message: EmailMessage, smtp: transport.SmtpConfig) -> None:
        entered.set()
        gate.wait(5)
        if then is not None:
            then(message, smtp)

    return send


async def until(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 10), "never happened"


async def test_a_job_the_stopping_worker_was_sending_goes_back_to_the_queue(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await enqueue("test", to="ops@example.com")
    entered, gate = threading.Event(), threading.Event()
    monkeypatch.setattr("app.services.mail.transport.send_message", held_send(entered, gate))
    loop = asyncio.create_task(drain.MailLoop(asyncio.Event()).run())
    await until(entered)

    loop.cancel()  # the worker cancels its loops when they don't end in time
    await asyncio.gather(loop, return_exceptions=True)
    gate.set()

    row = await job(job_id)
    assert (row.status, row.attempts, row.to_address) == ("queued", 0, "ops@example.com")
    monkeypatch.setattr("app.services.mail.transport.send_message", fake_mail.send)
    await drain_mail()  # the restarted worker
    assert (await job(job_id)).status == "sent" and len(fake_mail.to("ops@example.com")) == 1


async def test_the_running_loop_queues_again_a_job_left_sending(fake_mail: FakeMail) -> None:
    now = [0.0]
    stopping = asyncio.Event()
    running = asyncio.create_task(drain.MailLoop(stopping, clock=lambda: now[0]).run())
    await asyncio.sleep(0.05)  # it looked once at start-up
    job_id = await enqueue("test", to="left@example.com")
    await change(job_id, status="sending", attempts=1, next_attempt_at=utcnow() - timedelta(minutes=3))
    await outbox.wake()
    await asyncio.sleep(0.2)
    assert (await job(job_id)).status == "sending"  # it looks again a minute later

    now[0] += drain.RECOVER_SECONDS
    await outbox.wake()
    for _ in range(100):
        if fake_mail.sent:
            break
        await asyncio.sleep(0.05)
    stopping.set()
    await outbox.wake()
    await asyncio.wait_for(running, 10)

    row = await job(job_id)
    assert (row.status, row.attempts) == ("sent", 2)
    assert fake_mail.last("left@example.com")["Resend-Idempotency-Key"] == f"mail-{row.public_id}"


async def test_a_job_that_cant_be_prepared_is_retried_with_backoff_then_fails(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = await enqueue("test")

    async def broken(*args: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(drain.jobs, "resolve", broken)
    started = utcnow()

    with caplog.at_level(logging.INFO, logger="app.services.mail.drain"):
        assert await drain_mail() == 1
        row = await job(job_id)
        assert (row.status, row.attempts, row.last_error) == ("queued", 1, "couldn't be prepared")
        assert close_to(row.next_attempt_at, started + timedelta(minutes=1))
        for _ in range(5):
            await due_now(job_id)
            assert await drain_mail() == 1

    row = await job(job_id)
    assert (row.status, row.attempts, row.last_error) == ("failed", 6, "couldn't be prepared")
    assert (row.to_address, row.canonical) == (None, None)
    assert caplog.text.count("Traceback") == 1  # once per job, not on every retry


async def test_unexpected_transport_errors_are_temporary(fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(message: EmailMessage, smtp: transport.SmtpConfig) -> None:
        raise ValueError("unexpected")

    monkeypatch.setattr("app.services.mail.transport.send_message", broken)
    job_id = await enqueue("test")

    await drain_mail()

    row = await job(job_id)
    assert (row.status, row.last_error) == ("queued", "failed (ValueError)")


async def test_public_and_reset_jobs_take_two_seconds_of_the_loop(fake_mail: FakeMail) -> None:
    now = [100.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(round(seconds, 2))
        now[0] += seconds

    loop = drain.MailLoop(asyncio.Event(), clock=lambda: now[0], sleep=sleep)
    await add_pending_signup(ANN)
    await enqueue("signup", canonical=ANN.lower())
    await enqueue("password_reset", to="nobody@example.com")
    await enqueue("test", to="admin@example.com")

    assert [await loop.step() for _ in range(4)] == [True, True, True, False]

    assert slept == [2.0, 2.0]  # the reset (sent first) and the sign-up; the test mail isn't paced
    assert [str(message["To"]) for message in fake_mail.sent] == [ANN, "admin@example.com"]


async def test_a_paced_job_that_took_longer_isnt_slowed_further(fake_mail: FakeMail) -> None:
    times = iter([0.0, 3.5])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    await enqueue("password_reset", to="nobody@example.com")

    assert await drain.MailLoop(asyncio.Event(), clock=lambda: next(times), sleep=sleep).step() is True
    assert slept == []


async def test_the_loop_runs_when_woken_and_stops(fake_mail: FakeMail) -> None:
    stopping = asyncio.Event()
    running = asyncio.create_task(drain.MailLoop(stopping).run())
    await asyncio.sleep(0.05)
    await enqueue("test")
    await outbox.wake()

    for _ in range(100):
        if fake_mail.sent:
            break
        await asyncio.sleep(0.05)
    stopping.set()
    await outbox.wake()
    await asyncio.wait_for(running, 10)

    assert [str(message["To"]) for message in fake_mail.sent] == [ANN]
    assert drain.MailLoop.wanted() is True


async def test_a_class_over_its_share_waits_for_the_next_utc_day(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 5)  # public: 2
    for number in range(3):
        await enqueue("password_changed", to=f"user{number}@example.com", user_id=await add_user(f"u{number}"))

    await drain_mail()

    rows = [await job(job_id) for job_id in (1, 2, 3)]
    assert [row.status for row in rows] == ["cancelled", "cancelled", "queued"]  # no email on these accounts
    held = rows[2]
    assert held.attempts == 0 and held.next_attempt_at == outbox.next_day()
    assert held.next_attempt_at.astimezone(timezone.utc).hour == 0


async def test_jobs_held_over_midnight_count_on_the_day_they_go(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 10)  # public: 4
    ann = await add_user("ann", email=ANN)
    held = [await enqueue("password_changed", user_id=ann) for _ in range(6)]
    async with db.SessionLocal() as session:  # yesterday's, held until 00:00 UTC
        await session.execute(update(EmailOutbox).values(created_at=utcnow() - timedelta(days=1)))
        await session.commit()

    await drain_mail()

    assert len(fake_mail.sent) == 4
    rows = [await job(job_id) for job_id in held]
    assert [row.status for row in rows] == ["sent"] * 4 + ["queued"] * 2
    assert rows[-1].next_attempt_at == outbox.next_day()
    async with db.SessionLocal() as session:
        assert not await outbox.available(session, "public")


# ---------------- What each kind becomes (6.4) ----------------


async def test_a_sign_up_mails_a_verification_link_bound_to_its_pending_row(fake_mail: FakeMail) -> None:
    row_id = await add_pending_signup(ANN)
    await enqueue("signup", pending_signup_id=row_id)

    await drain_mail()

    message = fake_mail.last(ANN)
    assert message["Subject"] == "Finish creating your AskNuvexusDoc account"
    assert fake_mail.last_link(ANN).startswith("http://localhost:8080/verify-email#t=")
    found = await live_token("signup", fake_mail.last_token(ANN))
    assert found is not None and found.pending_signup_id == row_id
    assert tokens.address_matches(found, ANN.lower())
    async with db.SessionLocal() as session:
        stored = list(await session.scalars(select(AuthToken.token_sha256)))
    assert fake_mail.last_token(ANN) not in stored  # only its hash is kept


async def test_links_point_at_the_first_https_origin(fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_base_urls", "http://localhost:8080,https://asknuvexusdoc.com")
    await enqueue("signup", pending_signup_id=await add_pending_signup(ANN))

    await drain_mail()

    assert fake_mail.last_link(ANN).startswith("https://asknuvexusdoc.com/verify-email#t=")
    assert "localhost" not in html(fake_mail.last(ANN))


async def test_a_resend_without_its_row_uses_the_newest_live_one_or_nothing(fake_mail: FakeMail) -> None:
    await add_pending_signup(ANN, age=timedelta(hours=2))
    newest = await add_pending_signup(ANN, age=timedelta(hours=1))
    await add_pending_signup("bob@example.com", live=False)
    await enqueue("signup")
    await enqueue("signup", to="bob@example.com")

    await drain_mail()

    found = await live_token("signup", fake_mail.last_token(ANN))
    assert found is not None and found.pending_signup_id == newest
    assert fake_mail.to("bob@example.com") == []
    assert ((await job(2)).status, (await job(2)).last_error) == ("cancelled", "pending_gone")


@pytest.mark.parametrize(
    ("password", "active", "expected"),
    [
        (True, True, "Forgot your password? Reset it here:\nhttp://localhost:8080/forgot-password\n"),
        (False, True, "Sign in with Continue with Google, or set a password with Forgot password."),
        (True, False, "This account is turned off. Contact us if you need it:\nhttp://localhost:8080/contact\n"),
    ],
)
async def test_a_sign_up_for_an_address_with_an_account_says_so_and_drops_its_row(
    fake_mail: FakeMail, password: bool, active: bool, expected: str
) -> None:
    await add_user("ann", email="ann.lee@example.com", password=password, active=active)
    row_id = await add_pending_signup(ANN)
    await enqueue("signup", pending_signup_id=row_id)

    await drain_mail()

    message = fake_mail.last(ANN)
    assert message["Subject"] == "You already have an AskNuvexusDoc account"
    assert expected in text(message) and "#t=" not in text(message)
    assert await count(PendingSignup) == 0 and await count(AuthToken) == 0


async def test_a_reset_mails_a_reset_link_or_a_first_password_link(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email=ANN)
    await add_user("bob", email="bob@example.com", password=False)
    await enqueue("password_reset", canonical=ANN.lower())
    await enqueue("password_reset", to="bob@example.com")

    await drain_mail()

    found = await live_token("reset", fake_mail.last_token(ANN))
    assert found is not None and found.user_id == ann and tokens.address_matches(found, ANN.lower())
    assert fake_mail.last_link(ANN).startswith("http://localhost:8080/reset-password#t=")
    assert text(fake_mail.last(ANN)).startswith(f"Reset the password for {ANN}.")
    assert text(fake_mail.last("bob@example.com")).startswith("Set a password for bob@example.com.")


@pytest.mark.parametrize("who", ["inactive", "admin", "nobody"])
async def test_no_reset_for_a_turned_off_account_the_admin_or_nobody(fake_mail: FakeMail, who: str) -> None:
    if who != "nobody":
        await add_user("ann", email=ANN, active=who != "inactive", admin=who == "admin")
    job_id = await enqueue("password_reset")

    await drain_mail()

    assert fake_mail.sent == [] and await count(AuthToken) == 0
    assert ((await job(job_id)).status, (await job(job_id)).last_error) == ("cancelled", "no_account")


async def test_a_reset_for_an_address_with_only_a_pending_sign_up_sends_a_fresh_verification(
    fake_mail: FakeMail,
) -> None:
    row_id = await add_pending_signup(ANN)
    await enqueue("password_reset")

    await drain_mail()

    message = fake_mail.last(ANN)
    assert text(message).startswith("You asked to reset your password, but your account isn't created yet.")
    found = await live_token("signup", fake_mail.last_token(ANN))
    assert found is not None and found.pending_signup_id == row_id


async def test_an_undos_reset_goes_to_the_restored_address_only_while_it_is_the_accounts(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email=ANN, password=False)
    await enqueue("password_reset", user_id=ann)
    other = await enqueue("password_reset", to="old@example.com", user_id=ann)

    await drain_mail()

    assert text(fake_mail.last(ANN)).startswith(f"Set a password for {ANN}.")
    assert fake_mail.to("old@example.com") == [] and (await job(other)).status == "cancelled"


async def test_an_email_confirmation_mails_the_pending_address_and_restarts_its_day(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email="ann@old.example.com")
    pending = await add_pending_email(ann, ANN)
    google = await add_user("gus")
    google_pending = await add_pending_email(google, "gus@example.org", source="google")
    async with db.SessionLocal() as session:
        await session.execute(update(PendingEmail).values(expires_at=utcnow() + timedelta(hours=1)))
        await session.commit()
    await enqueue("email_confirm", user_id=ann, pending_email_id=pending)
    await enqueue("email_confirm", to="gus@example.org", user_id=google, pending_email_id=google_pending)

    await drain_mail()

    found = await live_token("email", fake_mail.last_token(ANN))
    assert found is not None and (found.user_id, found.pending_email_id) == (ann, pending)
    assert fake_mail.last_link(ANN).startswith("http://localhost:8080/confirm-email#t=")
    assert text(fake_mail.last(ANN)).startswith(f"Confirm {ANN} as the email of your AskNuvexusDoc account.")
    assert text(fake_mail.last("gus@example.org")).startswith("You signed up with Google using this address.")
    async with db.SessionLocal() as session:
        row = await session.get(PendingEmail, pending)
    assert row is not None and row.sent_at is not None
    assert close_to(row.expires_at, utcnow() + timedelta(hours=24))


async def test_a_replaced_or_expired_pending_email_gets_nothing(fake_mail: FakeMail) -> None:
    ann = await add_user("ann")
    pending = await add_pending_email(ann, ANN)
    await enqueue("email_confirm", user_id=ann, pending_email_id=pending)
    async with db.SessionLocal() as session:
        await session.execute(update(PendingEmail).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()
    await enqueue("email_confirm", to="gone@example.com", user_id=ann)

    await drain_mail()

    assert fake_mail.sent == []
    assert {(await job(1)).last_error, (await job(2)).last_error} == {"pending_gone"}


async def test_the_first_email_change_carries_the_undo_link_and_later_ones_dont(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email="ann@second.example")
    await enqueue("email_changed", to="ann@first.example", user_id=ann)
    await drain_mail()
    await enqueue("email_changed", to="ann@between.example", user_id=ann)
    await drain_mail()

    first = text(fake_mail.last("ann@first.example"))
    assert "The email of your account changed from ann@first.example to a***@second.example on " in first
    assert fake_mail.last_link("ann@first.example").startswith("http://localhost:8080/undo-email#t=")
    undo = await live_token("email_undo", fake_mail.last_token("ann@first.example"))
    assert undo is not None and undo.user_id == ann and tokens.address_matches(undo, "ann@first.example")
    later = text(fake_mail.last("ann@between.example"))
    assert "#t=" not in later and "If that wasn't you, contact" in later
    assert "ann@second.example" not in first + later  # the new address only masked


async def test_an_email_changed_back_meanwhile_needs_no_notice(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email=ANN)
    job_id = await enqueue("email_changed", user_id=ann)

    await drain_mail()

    assert fake_mail.sent == [] and (await job(job_id)).last_error == "changed_back"


async def test_an_undo_notice_names_the_restored_address_masked(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email=ANN)
    await enqueue("email_undone", to="intruder@example.net", user_id=ann)

    await drain_mail()

    message = text(fake_mail.last("intruder@example.net"))
    assert "The email of your account was changed back to A***@example.com on " in message
    assert ANN not in message


async def test_notices_go_to_the_accounts_email_now(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email=ANN)
    nobody = await add_user("nobody")
    async with db.SessionLocal() as session:
        session.add(
            UserIdentity(
                user_id=ann,
                provider="google",
                issuer="https://accounts.google.com",
                subject="1234",
                email_at_link="ann.google@gmail.com",
                email_kind="gmail",
            )
        )
        await session.commit()
    await enqueue("password_changed", to="ann@earlier.example", user_id=ann, params={"google_disconnected": True})
    await enqueue("password_added", user_id=ann)
    await enqueue("google_linked", user_id=ann)
    await enqueue("google_unlinked", user_id=ann, params={"google_email": "o***@gmail.com"})
    no_email = await enqueue("password_changed", to="old@example.com", user_id=nobody)

    await drain_mail()

    subjects = [message["Subject"] for message in fake_mail.to(ANN)]
    assert subjects == [
        "Your AskNuvexusDoc password was changed",
        "A password was added to your AskNuvexusDoc account",
        "Google sign-in was added to your AskNuvexusDoc account",
        "Google sign-in was removed from your AskNuvexusDoc account",
    ]
    changed, _, linked, unlinked = (text(message) for message in fake_mail.to(ANN))
    assert "Google sign-in was disconnected." in changed
    assert "a***@gmail.com can now sign in to your account" in linked
    assert "o***@gmail.com can no longer sign in to your account" in unlinked
    assert fake_mail.to("ann@earlier.example") == [] and fake_mail.to("old@example.com") == []
    assert (await job(no_email)).last_error == "no_email"


async def test_the_admins_test_mail_goes_to_the_typed_address(fake_mail: FakeMail) -> None:
    await enqueue("test", to="owner@example.org")

    await drain_mail()

    assert text(fake_mail.last("owner@example.org")).startswith("Mail from AskNuvexusDoc reaches this address.")


async def test_a_suppressed_recipient_is_never_mailed(fake_mail: FakeMail) -> None:
    ann = await add_user("ann", email=ANN)
    async with db.SessionLocal() as session:
        await outbox.suppress(session, emails.digest("ann.lee@example.com"), reason="complaint", source="webhook")
        await session.commit()
    direct = await enqueue("test")
    notice = await enqueue("password_added", to="someone@else.example", user_id=ann)

    await drain_mail()

    assert fake_mail.sent == [] and await count(AuthToken) == 0
    assert [(await job(job_id)).last_error for job_id in (direct, notice)] == ["suppressed", "suppressed"]


async def suppress(*addresses: str) -> None:
    async with db.SessionLocal() as session:
        for address in addresses:
            await outbox.suppress(session, emails.digest(address.lower()), reason="complaint", source="webhook")
        await session.commit()


async def test_a_suppressed_address_gets_one_confirmation_it_asked_for_a_day(fake_mail: FakeMail) -> None:
    ann = await add_user("ann")
    holder = await add_user("bob", email="bob@example.com")
    await suppress(ANN, "carol@example.com", "bob@example.com")
    pending = await add_pending_email(ann, ANN)
    signup_row = await add_pending_signup("carol@example.com")
    confirmations = [await enqueue("email_confirm", user_id=ann, pending_email_id=pending) for _ in range(2)]
    verifications = [
        await enqueue("signup", to="carol@example.com", pending_signup_id=signup_row) for _ in range(2)
    ]
    already = await enqueue("signup", to="bob@example.com")
    notice = await enqueue("password_added", to="bob@example.com", user_id=holder)

    await drain_mail()

    statuses = [(await job(job_id)).status for job_id in [*confirmations, *verifications, already, notice]]
    assert statuses == ["sent", "cancelled", "sent", "cancelled", "cancelled", "cancelled"]
    assert len(fake_mail.to(ANN)) == 1 and len(fake_mail.to("carol@example.com")) == 1
    assert fake_mail.to("bob@example.com") == []  # not an opt-in: "you already have an account", a notice


async def test_a_confirmation_retried_after_a_failure_still_reaches_the_suppressed_address(
    fake_mail: FakeMail,
) -> None:
    ann = await add_user("ann")
    await suppress(ANN)
    job_id = await enqueue("email_confirm", user_id=ann, pending_email_id=await add_pending_email(ann, ANN))
    fake_mail.fail_next("temporary")
    await drain_mail()
    await due_now(job_id)

    await drain_mail()

    assert (await job(job_id)).status == "sent"


async def test_without_redis_a_suppressed_address_gets_nothing(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    ann = await add_user("ann")
    await suppress(ANN)
    job_id = await enqueue("email_confirm", user_id=ann, pending_email_id=await add_pending_email(ann, ANN))

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise RedisConnectionError("down")

    monkeypatch.setattr(type(get_redis()), "set", unavailable)
    await drain_mail()

    assert ((await job(job_id)).status, fake_mail.sent) == ("cancelled", [])


async def test_a_database_error_in_the_mail_loop_logs_no_address(
    fake_mail: FakeMail, caplog: pytest.LogCaptureFixture
) -> None:
    await enqueue("signup", to="Victim.Person@example.com")

    def failing(cursor: Any, statement: str, parameters: Any, context: Any) -> None:
        if "users.email_canonical = " in statement:
            raise sqlite3.OperationalError("database is locked")  # as the driver raises it mid-query

    engine = db.SessionLocal.kw["bind"].sync_engine
    event.listen(engine, "do_execute", failing)
    try:
        with caplog.at_level(logging.INFO):
            await drain.process_one()
    finally:
        event.remove(engine, "do_execute", failing)

    assert "couldn't be prepared" in caplog.text and "OperationalError" in caplog.text
    assert "victim.person@example.com" not in caplog.text.lower()


async def test_a_retry_mints_a_new_token_and_the_first_still_works(fake_mail: FakeMail) -> None:
    row_id = await add_pending_signup(ANN)
    job_id = await enqueue("signup", pending_signup_id=row_id)
    fake_mail.fail_next("temporary")
    await drain_mail()
    await due_now(job_id)

    await drain_mail()

    async with db.SessionLocal() as session:
        minted = list(await session.scalars(select(AuthToken).where(AuthToken.pending_signup_id == row_id)))
    assert len(minted) == 2 and all(row.used_at is None for row in minted)
    assert await live_token("signup", fake_mail.last_token(ANN)) is not None


async def test_unknown_kinds_and_jobs_without_an_address_are_cancelled(fake_mail: FakeMail) -> None:
    job_id = await enqueue("test")
    await change(job_id, kind="welcome")

    await drain_mail()

    assert (await job(job_id)).last_error == "unknown_kind"


async def test_a_users_switch_is_read_leniently(fake_mail: FakeMail) -> None:
    from app.services.mail import jobs

    ann = await add_user("ann")
    async with db.SessionLocal() as session:
        assert await jobs._switch(session, ann, "email_receipts") is True
        session.add(UserPreference(user_id=ann, data={"email_receipts": "no", "email_plan_reminders": False}))
        await session.commit()
        assert await jobs._switch(session, ann, "email_receipts") is True
        assert await jobs._switch(session, ann, "email_plan_reminders") is False


# ---------------- The worker ----------------


async def test_the_worker_runs_the_mail_loop_and_the_hourly_clean_up(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    looped, swept = asyncio.Event(), asyncio.Event()

    async def loop_run(self: drain.MailLoop) -> None:
        looped.set()

    async def sweep(now: datetime | None = None) -> dict[str, int]:
        swept.set()
        raise RuntimeError("database gone")

    monkeypatch.setattr(drain.MailLoop, "run", loop_run)
    monkeypatch.setattr(sweeps, "run", sweep)
    monkeypatch.setattr(worker, "_MAIL_SWEEP_DELAY_SECONDS", 0)
    monkeypatch.setattr(worker, "_POLL_SECONDS", 0.05)
    instance = worker.Worker(1)
    task = asyncio.create_task(instance.run())

    await asyncio.wait_for(asyncio.gather(looped.wait(), swept.wait()), 10)
    instance.stop()
    await asyncio.wait_for(task, 10)


async def test_without_the_mailer_the_worker_runs_no_mail_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    async def loop_run(self: drain.MailLoop) -> None:
        started.append("mail")

    monkeypatch.setattr(drain.MailLoop, "run", loop_run)
    monkeypatch.setattr(worker, "_POLL_SECONDS", 0.05)
    instance = worker.Worker(1)
    task = asyncio.create_task(instance.run())
    await asyncio.sleep(0.3)
    instance.stop()
    await asyncio.wait_for(task, 10)

    assert started == [] and drain.MailLoop.wanted() is False


async def test_a_stopping_worker_lets_the_mail_loop_finish_the_mail_it_is_sending(
    fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await enqueue("test", to="ops@example.com")
    entered, gate = threading.Event(), threading.Event()
    monkeypatch.setattr("app.services.mail.transport.send_message", held_send(entered, gate, then=fake_mail.send))
    monkeypatch.setattr(worker, "_POLL_SECONDS", 0.05)
    instance = worker.Worker(1)
    task = asyncio.create_task(instance.run())
    await until(entered)

    instance.stop()  # SIGTERM while the SMTP conversation goes on
    await asyncio.sleep(0.3)
    gate.set()
    await asyncio.wait_for(task, 10)

    assert (await job(job_id)).status == "sent" and len(fake_mail.to("ops@example.com")) == 1


# ---------------- The clean-up (3.7) ----------------


async def test_the_clean_up_deletes_and_clears_by_age(app: FastAPI) -> None:
    now = utcnow()
    ann = await add_user("ann")
    old_signup = await add_pending_signup("old@example.com", live=False)
    live_signup = await add_pending_signup(ANN)
    expired_email = await add_pending_email(ann, "ann@new.example")
    async with db.SessionLocal() as session:
        await session.execute(update(PendingEmail).values(expires_at=now - timedelta(minutes=1)))
        for number, (purpose, used, expires) in enumerate(
            (
                ("reset", now - timedelta(days=2), now + timedelta(minutes=5)),  # used two days ago: deleted
                ("reset", now - timedelta(hours=2), now + timedelta(minutes=5)),  # used today: kept
                ("email", None, now - timedelta(days=2)),  # expired two days ago: deleted
                ("email_undo", None, now + timedelta(days=6)),  # live: kept
            )
        ):
            session.add(
                AuthToken(
                    purpose=purpose,
                    token_sha256=f"token-{number}",
                    address_sha256="a",
                    user_id=ann,
                    created_at=now - timedelta(days=3),
                    expires_at=expires,
                    used_at=used,
                )
            )
        session.add_all(
            [
                AuthEvent(
                    user_id=ann,
                    type="email.changed",
                    actor="self",
                    ip="203.0.113.x",
                    detail={"from": "a***@old.example", "restore": "sealed"},
                    created_at=now,
                ),
                AuthEvent(
                    user_id=ann,
                    type="email.changed",
                    actor="self",
                    detail={"from": "a***@late.example", "restore": "sealed"},
                    created_at=now - timedelta(days=10, hours=12),  # its notice may have gone 3 days late
                ),
                AuthEvent(
                    user_id=ann,
                    type="email.changed",
                    actor="self",
                    detail={"from": "a***@older.example", "restore": "sealed"},
                    created_at=now - timedelta(days=11, hours=1),
                ),
                AuthEvent(
                    user_id=ann,
                    type="password.reset",
                    actor="self",
                    ip="203.0.113.x",
                    created_at=now - timedelta(days=91),
                ),
                AuthEvent(user_id=ann, type="signup.completed", actor="self", created_at=now - timedelta(days=366)),
                MailSuppression(
                    recipient_sha256="old-bounce", reason="bounce", source="smtp", expires_at=now - timedelta(hours=1)
                ),
                MailSuppression(
                    recipient_sha256="bounce", reason="bounce", source="smtp", expires_at=now + timedelta(days=1)
                ),
                MailSuppression(recipient_sha256="complaint", reason="complaint", source="webhook", expires_at=None),
            ]
        )
        await session.commit()
    sent_long_ago = await enqueue("test", to="a@example.com")
    sent_today = await enqueue("test", to="b@example.com")
    finished_unclean = await enqueue("test", to="c@example.com")
    waiting = await enqueue("test", to="d@example.com", pending_signup_id=old_signup)
    await change(sent_long_ago, status="sent", finished_at=now - timedelta(hours=49), to_address=None, canonical=None)
    await change(sent_today, status="sent", finished_at=now - timedelta(hours=47), to_address=None, canonical=None)
    await change(finished_unclean, status="failed", finished_at=now)
    never_sent = await enqueue("test", to="e@example.com")
    await change(never_sent, not_after=now - timedelta(minutes=1))
    left_sending = await enqueue("test", to="f@example.com")  # by a worker that stopped
    await change(
        left_sending, status="sending", next_attempt_at=now - timedelta(minutes=3), not_after=now - timedelta(minutes=1)
    )
    sending_now = await enqueue("test", to="g@example.com")  # a loop may be sending it right now
    await change(
        sending_now, status="sending", next_attempt_at=now - timedelta(seconds=30), not_after=now - timedelta(seconds=1)
    )

    counts = await sweeps.run()

    assert counts == {
        "pending_signups": 1,
        "pending_emails": 1,
        "auth_tokens": 2,
        "outbox_expired": 2,
        "outbox_addresses": 1,
        "email_outbox": 1,
        "auth_event_ips": 1,
        "auth_events": 1,
        "auth_event_addresses": 1,
        "mail_suppressions": 1,
    }
    async with db.SessionLocal() as session:
        signups = set(await session.scalars(select(PendingSignup.id)))
        assert signups == {live_signup}
        assert await session.get(PendingEmail, expired_email) is None
        assert sorted(await session.scalars(select(AuthToken.purpose))) == ["email_undo", "reset"]
        assert sorted(await session.scalars(select(EmailOutbox.id))) == [
            sent_today,
            finished_unclean,
            waiting,
            never_sent,
            left_sending,
            sending_now,
        ]
        events = [(event.type, event.ip, event.detail) for event in await session.scalars(select(AuthEvent))]
        assert sorted(events, key=str) == sorted(
            [
                ("email.changed", "203.0.113.x", {"from": "a***@old.example", "restore": "sealed"}),
                ("email.changed", None, {"from": "a***@late.example", "restore": "sealed"}),
                ("email.changed", None, {"from": "a***@older.example"}),
                ("password.reset", None, {}),
            ],
            key=str,
        )
        assert set(await session.scalars(select(MailSuppression.recipient_sha256))) == {"bounce", "complaint"}
    row = await job(finished_unclean)
    assert (row.to_address, row.canonical) == (None, None)
    for expired in (await job(never_sent), await job(left_sending)):
        assert (expired.status, expired.to_address, expired.canonical) == ("expired", None, None)
    assert (await job(sending_now)).status == "sending"
    assert (await job(waiting)).pending_signup_id is None  # its pending sign-up expired
    assert await sweeps.run() == dict.fromkeys(counts, 0)


# ---------------- Postgres ----------------


@pytest.mark.postgres
async def test_two_loops_never_send_one_job_twice(fake_mail: FakeMail) -> None:
    for number in range(6):
        await enqueue("test", to=f"user{number}@example.com")

    async def drain_all() -> None:
        while await drain.process_one():
            await asyncio.sleep(0)

    await asyncio.gather(drain_all(), drain_all())

    assert sorted(str(message["To"]) for message in fake_mail.sent) == [f"user{n}@example.com" for n in range(6)]
    async with db.SessionLocal() as session:
        attempts = list(await session.scalars(select(EmailOutbox.attempts)))
    assert attempts == [1] * 6
