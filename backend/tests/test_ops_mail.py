"""The monitor's `mail` check (app.ops.checks.check_mail; docs/briefs/signup-auth.md 14.1, SPEC section 10): "Email not
set up" while sign-up is closed and nothing waits; each warning (a failure in 24 hours, a job stuck over 10 minutes, a
job that couldn't be prepared for 10 minutes, the pause after a refused login, 80% of the day's limit, sign-up mail
paused by bounces, 3 bounces, a complaint, Turnstile errors, a sign-in surge, sign-up open without the mailer,
Turnstile or the webhook); `unknown` when the database can't be read. Counts only, never addresses."""

from datetime import timedelta
from typing import Any

import pytest
from conftest import open_signup
from fastapi import FastAPI
from sqlalchemy import func, select, update

from app import db
from app.config import settings
from app.models import EmailOutbox, utcnow
from app.ops import checks
from app.redis_client import get_redis
from app.services.auth import emails, throttle, turnstile
from app.services.mail import outbox

pytestmark = pytest.mark.usefixtures("app")

SECRET = "whsec_c2lnbmluZy1zZWNyZXQ="


async def enqueue(count: int = 1, **values: Any) -> None:
    """`count` jobs, then `values` set on them."""
    async with db.SessionLocal() as session:
        before = await session.scalar(select(func.max(EmailOutbox.id))) or 0
        for number in range(count):
            await outbox.enqueue(session, "test", to=f"person{number}@example.com")
        await session.flush()
        if values:
            await session.execute(update(EmailOutbox).where(EmailOutbox.id > before).values(**values))
        await session.commit()


async def suppress(reason: str, count: int) -> None:
    async with db.SessionLocal() as session:
        for number in range(count):
            await outbox.suppress(
                session, emails.digest(f"{reason}{number}@example.com"), reason=reason, source="webhook"
            )  # type: ignore[arg-type]
        await session.commit()


async def mail_check() -> tuple[str, str]:
    result = await checks.check_mail()
    assert result.label == "Email" and "@" not in result.detail
    return result.status, result.detail


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, mailer: None) -> None:
    monkeypatch.setattr(settings, "mail_webhook_secret", SECRET)


async def test_not_set_up_is_fine_while_sign_up_is_closed() -> None:
    assert await mail_check() == ("ok", "Email not set up")


async def test_not_set_up_warns_when_mail_waits_or_sign_up_is_open() -> None:
    await enqueue(2)
    assert await mail_check() == ("warning", "2 emails wait, but the mailer isn't set up")

    await open_signup()
    status, detail = await mail_check()
    assert status == "warning"
    assert detail.startswith(
        "Sign-up is open, but the mailer or Turnstile isn't set up; Sign-up is open without bounce reports "
        "(MAIL_WEBHOOK_SECRET); 2 emails wait"
    )


@pytest.mark.usefixtures("configured")
async def test_all_well_says_what_was_sent() -> None:
    await enqueue(1)

    assert await mail_check() == ("ok", "0 of 80 emails sent today; 1 waiting")


@pytest.mark.usefixtures("configured", "fake_turnstile")
async def test_sign_up_open_with_everything_set_up_is_fine() -> None:
    await open_signup()

    assert (await mail_check())[0] == "ok"


@pytest.mark.usefixtures("configured")
async def test_sign_up_open_without_turnstile_or_the_webhook_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    await open_signup()
    monkeypatch.setattr(settings, "mail_webhook_secret", "")

    assert await mail_check() == (
        "warning",
        "Sign-up is open, but the mailer or Turnstile isn't set up; Sign-up is open without bounce reports "
        "(MAIL_WEBHOOK_SECRET)",
    )


@pytest.mark.usefixtures("configured")
async def test_failed_and_stuck_mail() -> None:
    now = utcnow()
    await enqueue(2, status="failed", finished_at=now - timedelta(hours=2))
    await enqueue(1, status="failed", finished_at=now - timedelta(hours=25))
    await enqueue(1, next_attempt_at=now - timedelta(minutes=11))
    await enqueue(1, next_attempt_at=now - timedelta(minutes=9))

    assert await mail_check() == (
        "warning",
        "2 emails failed in the last 24 hours; 1 email due over 10 minutes ago still waiting",
    )


@pytest.mark.usefixtures("configured")
async def test_mail_that_couldnt_be_prepared_for_10_minutes() -> None:
    now = utcnow()
    unprepared = {"last_error": "couldn't be prepared", "attempts": 2}
    await enqueue(1, created_at=now - timedelta(minutes=11), next_attempt_at=now + timedelta(minutes=25), **unprepared)
    await enqueue(1, created_at=now - timedelta(minutes=9), next_attempt_at=now + timedelta(minutes=1), **unprepared)
    await enqueue(1, created_at=now - timedelta(minutes=40), next_attempt_at=now + timedelta(minutes=25), attempts=3)

    assert await mail_check() == ("warning", "1 email couldn't be prepared for over 10 minutes (retried)")


@pytest.mark.usefixtures("configured")
async def test_a_refused_login_pauses_sending() -> None:
    await get_redis().set("mail:paused", "SMTP login refused", ex=900)

    assert await mail_check() == ("warning", "Sending is paused for 15 minutes: the SMTP server refused the login")


@pytest.mark.usefixtures("configured")
async def test_80_percent_of_the_days_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 10)
    await enqueue(7, status="sent", sent_at=utcnow(), finished_at=utcnow())
    assert (await mail_check())[0] == "ok"

    await enqueue(1, status="sent", sent_at=utcnow(), finished_at=utcnow())

    assert await mail_check() == ("warning", "8 of 10 emails sent today (MAIL_DAILY_LIMIT)")


@pytest.mark.usefixtures("configured")
async def test_bounces_pause_sign_up_mail_and_complaints_warn() -> None:
    await suppress("bounce", 3)
    await suppress("complaint", 1)

    assert await mail_check() == (
        "warning",
        "Sign-up emails are paused until 00:00 UTC: too many bounces; 3 bounces today; 1 spam complaint in the last 7 "
        "days",
    )


@pytest.mark.usefixtures("configured")
async def test_bounces_below_the_pause_still_warn_at_three(monkeypatch: pytest.MonkeyPatch) -> None:
    await enqueue(200, status="sent", sent_at=utcnow(), finished_at=utcnow())
    monkeypatch.setattr(settings, "mail_daily_limit", 1000)
    await suppress("bounce", 3)

    assert await mail_check() == ("warning", "3 bounces today")


@pytest.mark.usefixtures("configured")
async def test_turnstile_errors_and_a_sign_in_surge() -> None:
    for _ in range(9):
        await turnstile._count_unavailable()
    assert (await mail_check())[0] == "ok"
    await turnstile._count_unavailable()
    await get_redis().set(throttle._SITE_SEEN, "1", ex=3600)

    assert await mail_check() == (
        "warning",
        "10 Turnstile verifications failed in the last hour; A surge of failed sign-ins turned the robot check on for "
        "everyone in the last hour",
    )


async def test_unknown_when_the_records_cant_be_read(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> None:
    def unreachable() -> Any:
        raise ConnectionRefusedError("postgres:5432")

    monkeypatch.setattr(checks, "SessionLocal", unreachable)

    assert await mail_check() == ("unknown", "The email records can't be read: connection refused")


async def test_the_check_is_part_of_every_round() -> None:
    snapshot = await checks.collect()

    assert [check.id for check in snapshot.checks][-2:] == ["billing", "mail"]
