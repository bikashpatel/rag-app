"""The email outbox's request side (app.services.mail.outbox; docs/briefs/signup-auth.md 6.3, 6.5 and 6.6): enqueue
adds a row in the caller's transaction with no token or link, the class follows the kind and whether the user has paid,
the daily cap's shares (a class past its share never takes another's room; an earlier day's job counts the day it goes),
the security notices' limits, suppressions, the bounce pause and the status counts. The worker's side:
test_mail_outbox.py."""

import asyncio
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import pytest
from fake_mail import FakeMail, html, svix_sign
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, update

from app import db
from app.config import settings
from app.models import BillingPayment, EmailOutbox, MailSuppression, User, utcnow
from app.redis_client import get_redis
from app.services.auth import emails, login
from app.services.billing import business, paid
from app.services.mail import outbox

TO = "John.Smith@Example.com"
LIMIT = 10  # shares: public 4, reset 3, the non-billing classes 8, billing the rest


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch, mailer: None) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", LIMIT)


async def enqueue(kind: outbox.MailKind, to: str = TO, **fields: Any) -> None:
    async with db.SessionLocal() as session:
        await outbox.enqueue(session, kind, to=to, **fields)
        await session.commit()


async def jobs() -> list[EmailOutbox]:
    async with db.SessionLocal() as session:
        return list(await session.scalars(select(EmailOutbox).order_by(EmailOutbox.id)))


async def change(*, where: Any = None, **values: Any) -> None:
    async with db.SessionLocal() as session:
        query = update(EmailOutbox).values(**values)
        await session.execute(query if where is None else query.where(where))
        await session.commit()


async def available(cls: outbox.MailClass) -> bool:
    async with db.SessionLocal() as session:
        return await outbox.available(session, cls)


async def shares() -> dict[str, bool]:
    return {cls: await available(cls) for cls in ("public", "reset", "account", "billing")}


async def add_user(username: str, *, paid_mode: str | None = None) -> int:
    async with db.SessionLocal() as session:
        user = User(username=username, password_hash="x", is_admin=False)
        session.add(user)
        await session.flush()
        if paid_mode is not None:
            session.add(
                BillingPayment(
                    user_id=user.id,
                    username=username,
                    status="captured",
                    amount_paise=50_000,
                    currency="INR",
                    mode=paid_mode,
                    applied_at=utcnow(),
                )
            )
        await session.commit()
        return user.id


# ---------------- Enqueue ----------------


async def test_enqueue_adds_a_queued_job_in_the_callers_transaction_without_committing(app: FastAPI) -> None:
    async with db.SessionLocal() as session:
        await outbox.enqueue(session, "signup", to=TO, pending_signup_id=None, params={"attempt": 1})
        async with db.SessionLocal() as other:
            assert list(await other.scalars(select(EmailOutbox))) == []
        await session.rollback()
    assert await jobs() == []

    before = utcnow()
    await enqueue("signup", params={"attempt": 1, "plan": "Starter", "flag": True, "none": None})

    [job] = await jobs()
    assert (job.kind, job.mail_class, job.priority, job.status, job.attempts) == ("signup", "public", 1, "queued", 0)
    assert (job.to_address, job.canonical) == (TO, "john.smith@example.com")
    assert job.recipient_sha256 == emails.digest("john.smith@example.com")
    assert job.params == {"attempt": 1, "plan": "Starter", "flag": True, "none": None}
    assert before <= job.created_at == job.next_attempt_at <= utcnow()
    assert job.not_after - job.created_at == timedelta(hours=6)
    assert len(job.public_id) == 24 and job.sent_at is None and job.finished_at is None and job.last_error is None


async def test_the_canonical_form_given_is_the_one_counted(app: FastAPI) -> None:
    await enqueue("password_reset", to="Typed@Example.com", canonical="typed@example.com")

    [job] = await jobs()
    assert (job.to_address, job.canonical, job.recipient_sha256) == (
        "Typed@Example.com",
        "typed@example.com",
        emails.digest("typed@example.com"),
    )


async def test_every_job_gets_its_own_idempotency_key(app: FastAPI) -> None:
    for _ in range(5):
        await enqueue("signup")

    assert len({job.public_id for job in await jobs()}) == 5


@pytest.mark.parametrize(
    "value",
    [
        "https://asknuvexusdoc.com/reset-password#t=abc",
        "http://x",
        "s3cr3t-T0ken_s3cr3t-T0ken_s3cr3t-T0k",  # token-shaped: 32 or more base64url characters
        "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_AbCdE",
    ],
)
async def test_params_never_hold_a_link_or_a_token(app: FastAPI, value: str) -> None:
    async with db.SessionLocal() as session:
        with pytest.raises(ValueError, match="link or a token"):
            await outbox.enqueue(session, "signup", to=TO, params={"value": value})

    assert await jobs() == []


async def test_a_masked_address_with_a_long_domain_label_is_a_param(app: FastAPI) -> None:
    masked = emails.mask("j@" + "a" * 63 + ".example.com")
    await enqueue("google_linked", params={"google_email": masked})

    [job] = await jobs()
    assert job.params == {"google_email": masked}
    async with db.SessionLocal() as session:
        with pytest.raises(ValueError, match="link or a token"):
            await outbox.enqueue(session, "signup", to=TO, params={"value": "j***@x.com/" + "a" * 32})


@pytest.mark.parametrize(
    ("kind", "mail_class", "priority", "not_after"),
    [
        ("signup", "public", 1, timedelta(hours=6)),
        ("password_reset", "reset", 0, timedelta(hours=6)),
        ("email_confirm", "public", 0, timedelta(hours=6)),
        ("email_changed", "public", 0, timedelta(days=3)),
        ("email_undone", "public", 0, timedelta(days=3)),
        ("password_changed", "public", 0, timedelta(days=3)),
        ("password_added", "public", 0, timedelta(days=3)),
        ("google_linked", "public", 0, timedelta(days=3)),
        ("google_unlinked", "public", 0, timedelta(days=3)),
        ("receipt", "billing", 2, timedelta(days=3)),
        ("plan_reminder", "billing", 2, timedelta(days=1)),
        ("test", "account", 2, timedelta(hours=1)),
    ],
)
async def test_each_kind_has_its_class_priority_and_lifetime(
    app: FastAPI, kind: outbox.MailKind, mail_class: str, priority: int, not_after: timedelta
) -> None:
    user_id = await add_user("smoke-unpaid")

    await enqueue(kind, user_id=user_id)

    [job] = await jobs()
    assert (job.mail_class, job.priority, job.not_after - job.created_at) == (mail_class, priority, not_after)
    assert set(outbox.KINDS) == set(outbox.MailKind.__args__)  # type: ignore[attr-defined]


@pytest.mark.parametrize(("paid_mode", "mail_class"), [(None, "public"), ("test", "public"), ("live", "account")])
async def test_an_accounts_notices_leave_the_public_class_once_it_has_paid(
    app: FastAPI, paid_mode: str | None, mail_class: str
) -> None:
    user_id = await add_user("smoke-payer", paid_mode=paid_mode)

    for kind in ("email_confirm", "email_changed", "password_changed", "google_linked"):
        await enqueue(kind, user_id=user_id)  # type: ignore[arg-type]
    await enqueue("signup", user_id=user_id)
    await enqueue("receipt", user_id=user_id)

    assert [job.mail_class for job in await jobs()] == [mail_class] * 4 + ["public", "billing"]


async def test_an_offline_payment_counts_as_paid(app: FastAPI) -> None:
    user_id = await add_user("smoke-bank", paid_mode="offline")

    await enqueue("password_changed", user_id=user_id)

    assert [job.mail_class for job in await jobs()] == ["account"]


# ---------------- The daily cap ----------------


async def test_nothing_is_available_without_the_mailer(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_smtp_host", "")

    assert await shares() == {"public": False, "reset": False, "account": False, "billing": False}
    async with db.SessionLocal() as session:
        assert await outbox.public_left(session) == 0


async def test_public_mail_has_40_percent_counted_when_created_whatever_it_became(app: FastAPI) -> None:
    for _ in range(3):
        await enqueue("signup")
    await change(status="cancelled", to_address=None, canonical=None)  # e.g. "no account" or suppressed
    assert await available("public")

    await enqueue("signup")

    assert await shares() == {"public": False, "reset": True, "account": True, "billing": True}
    async with db.SessionLocal() as session:
        assert await outbox.public_left(session) == 0


async def test_reset_mail_has_30_percent(app: FastAPI) -> None:
    for _ in range(2):
        await enqueue("password_reset")
    await change(status="sent", sent_at=utcnow())
    assert await available("reset")

    await enqueue("password_reset")

    assert await shares() == {"public": True, "reset": False, "account": True, "billing": True}


async def test_the_non_billing_classes_share_80_percent_and_billing_keeps_the_rest(app: FastAPI) -> None:
    for kind, count in (("signup", 4), ("password_reset", 3), ("test", 1)):
        for _ in range(count):
            await enqueue(kind)  # type: ignore[arg-type]

    assert await shares() == {"public": False, "reset": False, "account": False, "billing": True}
    await enqueue("receipt")
    assert await available("billing")
    await enqueue("plan_reminder")
    assert not await available("billing")


async def test_account_and_billing_count_what_was_sent_today_or_is_due_today(app: FastAPI) -> None:
    now = utcnow()
    for _ in range(8):
        await enqueue("test")
    assert not await available("account")

    first, second, *_ = await jobs()
    await change(where=EmailOutbox.id == first.id, next_attempt_at=outbox.next_day(now) + timedelta(minutes=5))
    assert await available("account"), "a job held until tomorrow doesn't count today"
    await change(where=EmailOutbox.id == second.id, status="sent", sent_at=now - timedelta(days=1))
    await change(where=EmailOutbox.id == first.id, status="failed")
    assert await available("account")


async def test_a_class_past_its_share_takes_no_other_classs_room(app: FastAPI) -> None:
    for _ in range(12):
        await enqueue("password_changed")  # an unpaid account's notices, past the public share
    assert await shares() == {"public": False, "reset": True, "account": True, "billing": True}

    for _ in range(5):
        await enqueue("password_reset")
    await enqueue("test")
    assert await shares() == {"public": False, "reset": False, "account": False, "billing": True}

    for _ in range(2):
        await enqueue("receipt")
    assert not await available("billing")
    async with db.SessionLocal() as session:
        fits = [await outbox.within_share(session, job) for job in await jobs() if job.mail_class == "billing"]
    assert fits == [True, True]  # billing keeps its 20%


async def test_an_earlier_days_job_counts_on_the_day_it_goes(app: FastAPI) -> None:
    for _ in range(4):
        await enqueue("signup")
    await change(created_at=utcnow() - timedelta(days=1))  # yesterday's, held until today
    assert not await available("public")

    first, *_ = await jobs()
    await change(where=EmailOutbox.id == first.id, next_attempt_at=outbox.next_day())  # held again
    assert await available("public")
    await change(where=EmailOutbox.id == first.id, status="sent", sent_at=utcnow())
    assert not await available("public")


async def test_yesterdays_jobs_dont_count_today(app: FastAPI) -> None:
    for _ in range(4):
        await enqueue("signup")
    assert not await available("public")

    yesterday = utcnow() - timedelta(days=1)
    await change(created_at=yesterday, next_attempt_at=yesterday, status="expired")

    assert await available("public")


async def test_public_left_is_the_public_share_bounded_by_the_non_billing_share(app: FastAPI) -> None:
    async with db.SessionLocal() as session:
        assert await outbox.public_left(session) == 4
    await enqueue("signup")
    for _ in range(3):
        await enqueue("password_reset")
    async with db.SessionLocal() as session:
        assert await outbox.public_left(session) == 3
    for _ in range(3):
        await enqueue("test")
    async with db.SessionLocal() as session:
        assert await outbox.public_left(session) == 1  # 7 of the 8 non-billing jobs used


async def test_the_worker_sends_the_jobs_ahead_within_a_share_and_holds_the_rest(app: FastAPI) -> None:
    for _ in range(6):
        await enqueue("password_changed")  # notices are enqueued without checking the share
    for _ in range(3):
        await enqueue("receipt")

    async with db.SessionLocal() as session:
        fits = [await outbox.within_share(session, job) for job in await jobs()]

    assert fits == [True] * 4 + [False] * 2 + [True] * 3
    assert outbox.next_day(datetime(2026, 11, 1, 23, 59, tzinfo=timezone.utc)) == datetime(
        2026, 11, 2, tzinfo=timezone.utc
    )


async def test_the_worker_holds_billing_only_past_the_daily_limit(app: FastAPI) -> None:
    for _ in range(8):
        await enqueue("test")
    await change(status="sent", sent_at=utcnow())
    for _ in range(3):
        await enqueue("receipt")

    async with db.SessionLocal() as session:
        fits = [await outbox.within_share(session, job) for job in await jobs() if job.mail_class == "billing"]

    assert fits == [True, True, False]


# ---------------- Security notices (login.notice) ----------------


async def mailed_user(username: str, *, paid_mode: str | None = None) -> User:
    user_id = await add_user(username, paid_mode=paid_mode)
    async with db.SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.email = user.email_canonical = f"{username}@example.com"
        await session.commit()
    return user


async def notice(user: User) -> bool:
    async with db.SessionLocal() as session:
        queued = await login.notice(session, "password_changed", user)
        await session.commit()
    return queued


async def test_an_account_gets_3_notices_an_hour_and_10_a_day(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 100)  # room enough
    ann, bob = await mailed_user("ann"), await mailed_user("bob")

    queued = []
    for _ in range(4):  # four hours
        await get_redis().delete(f"auth:notice:3600:user:{ann.id}")
        queued += [await notice(ann) for _ in range(3)]

    assert queued == [True] * 10 + [False] * 2
    assert await notice(bob) is True
    assert len(await jobs()) == 11


async def test_a_notice_is_skipped_while_its_class_has_no_room_today(app: FastAPI) -> None:
    for _ in range(4):
        await enqueue("signup")  # the public share used
    ann, paying = await mailed_user("ann"), await mailed_user("payer", paid_mode="live")

    assert await notice(ann) is False
    assert await notice(paying) is True  # the account class has room

    assert [job.kind for job in await jobs()] == ["signup"] * 4 + ["password_changed"]
    assert await get_redis().get(f"auth:notice:3600:user:{ann.id}") is None  # nor counted


# ---------------- Suppressions and the bounce pause ----------------


async def suppress(address: str, reason: str, source: str = "webhook") -> None:
    async with db.SessionLocal() as session:
        await outbox.suppress(session, emails.digest(address), reason=reason, source=source)  # type: ignore[arg-type]
        await session.commit()


async def is_suppressed(address: str) -> bool:
    async with db.SessionLocal() as session:
        return await outbox.suppressed(session, emails.digest(address))


async def suppression(address: str) -> MailSuppression | None:
    async with db.SessionLocal() as session:
        return await session.get(MailSuppression, emails.digest(address))


async def test_a_bounce_suppresses_an_address_for_30_days(app: FastAPI) -> None:
    assert not await is_suppressed("bad@example.com")

    await suppress("bad@example.com", "bounce", "smtp")

    row = await suppression("bad@example.com")
    assert row is not None and (row.reason, row.source) == ("bounce", "smtp")
    assert row.expires_at is not None and row.expires_at - row.created_at == timedelta(days=30)
    assert await is_suppressed("bad@example.com") and not await is_suppressed("good@example.com")
    async with db.SessionLocal() as session:
        await session.execute(update(MailSuppression).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()
    assert not await is_suppressed("bad@example.com")


async def test_a_complaint_lasts_until_the_address_is_confirmed_again(app: FastAPI) -> None:
    await suppress("angry@example.com", "complaint")
    await suppress("angry@example.com", "bounce")  # a later bounce doesn't shorten it

    row = await suppression("angry@example.com")
    assert row is not None and (row.reason, row.expires_at) == ("complaint", None)
    assert await is_suppressed("angry@example.com")

    async with db.SessionLocal() as session:
        await outbox.unsuppress(session, emails.digest("angry@example.com"))
        await session.commit()
    assert await suppression("angry@example.com") is None


async def test_a_repeated_bounce_renews_the_suppression(app: FastAPI) -> None:
    await suppress("bad@example.com", "bounce")
    old = utcnow() - timedelta(days=20)
    async with db.SessionLocal() as session:
        await session.execute(update(MailSuppression).values(created_at=old, expires_at=old + timedelta(days=30)))
        await session.commit()

    await suppress("bad@example.com", "bounce", "smtp")

    row = await suppression("bad@example.com")
    assert row is not None and row.created_at > old + timedelta(days=19) and row.source == "smtp"
    async with db.SessionLocal() as session:
        assert len(list(await session.scalars(select(MailSuppression)))) == 1


async def sent_today(count: int) -> None:
    now = utcnow()
    async with db.SessionLocal() as session:
        for _ in range(count):
            await outbox.enqueue(session, "receipt", to=TO)
        await session.flush()
        await session.execute(update(EmailOutbox).values(status="sent", sent_at=now, created_at=now))
        await session.commit()


async def paused() -> bool:
    async with db.SessionLocal() as session:
        return await outbox.public_paused(session)


async def test_three_bounces_that_are_2_percent_of_todays_mail_pause_public_mail(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 1000)
    await sent_today(150)
    for number in range(2):
        await suppress(f"bad{number}@example.com", "bounce")
    assert not await paused()

    await suppress("bad2@example.com", "bounce")

    assert await paused()  # 3 of 150 is 2%
    assert await shares() == {"public": False, "reset": True, "account": True, "billing": True}
    async with db.SessionLocal() as session:
        assert await outbox.public_left(session) == 0
        await outbox.enqueue(session, "signup", to=TO)
        await session.commit()
        [job] = [job for job in await jobs() if job.kind == "signup"]
        assert not await outbox.within_share(session, job)


async def test_bounces_below_2_percent_or_from_yesterday_dont_pause(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 1000)
    await sent_today(151)
    for number in range(3):
        await suppress(f"bad{number}@example.com", "bounce")
    assert not await paused()  # 3 of 151 is under 2%

    await suppress("bad3@example.com", "bounce")
    assert await paused()
    async with db.SessionLocal() as session:
        await session.execute(update(MailSuppression).values(created_at=utcnow() - timedelta(days=1)))
        await session.commit()
    assert not await paused()


async def test_complaints_dont_pause_but_are_counted(app: FastAPI) -> None:
    for number in range(4):
        await suppress(f"angry{number}@example.com", "complaint")

    assert not await paused()
    async with db.SessionLocal() as session:
        status = await outbox.mail_status(session)
    assert (status.complaints_7d, status.bounces_today) == (4, 0)


# ---------------- Status and wake-ups ----------------


async def test_the_status_counts_without_addresses(app: FastAPI) -> None:
    await sent_today(2)
    await enqueue("signup")
    await enqueue("test")
    await suppress("bad@example.com", "bounce")
    old_complaint = utcnow() - timedelta(days=8)
    await suppress("angry@example.com", "complaint")
    async with db.SessionLocal() as session:
        await session.execute(
            update(MailSuppression).where(MailSuppression.reason == "complaint").values(created_at=old_complaint)
        )
        await session.commit()

    async with db.SessionLocal() as session:
        status = await outbox.mail_status(session)

    assert status == outbox.MailStatus(
        sent_today=2,
        queued=2,
        limit=LIMIT,
        public_left=3,
        public_paused=False,
        bounces_today=1,
        complaints_7d=0,
        last_error=None,
    )


async def test_the_status_names_the_newest_error_with_when_a_job_failed(app: FastAPI) -> None:
    await enqueue("signup")
    await enqueue("receipt")
    first, second = await jobs()
    failed_at = datetime(2026, 11, 25, 8, 35, tzinfo=timezone.utc)  # 14:05 IST
    await change(
        where=EmailOutbox.id == first.id, status="failed", last_error="recipient refused (550)", finished_at=failed_at
    )

    async with db.SessionLocal() as session:
        assert (await outbox.mail_status(session)).last_error == "recipient refused (550) (25 Nov 2026, 14:05 IST)"
    await change(where=EmailOutbox.id == second.id, last_error="SMTP login refused")
    async with db.SessionLocal() as session:
        assert (await outbox.mail_status(session)).last_error == "SMTP login refused"
    await change(where=EmailOutbox.id == second.id, status="sent", sent_at=utcnow())
    async with db.SessionLocal() as session:
        assert (await outbox.mail_status(session)).last_error == "recipient refused (550) (25 Nov 2026, 14:05 IST)"


async def test_wake_tells_the_worker_and_keeps_the_list_short() -> None:
    redis = get_redis()

    for _ in range(105):
        await outbox.wake()

    assert await redis.llen(outbox.WAKE_QUEUE) == 100


@pytest.fixture
def broken_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    class Broken:
        def pipeline(self, **_: Any) -> Any:
            raise RedisConnectionError("down")

    monkeypatch.setattr(outbox, "get_redis", lambda: Broken())
    yield


async def test_wake_is_best_effort(broken_redis: None, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.services.mail.outbox"):
        await outbox.wake()

    assert "Could not wake the mail worker (ConnectionError)" in caplog.text


# ---------------- Paid accounts and live payments (app.services.billing.paid) ----------------


async def test_only_an_applied_live_or_offline_payment_makes_an_account_paid(app: FastAPI) -> None:
    unpaid, tester, payer = (
        await add_user("smoke-unpaid"),
        await add_user("smoke-tester", paid_mode="test"),
        await add_user("smoke-payer", paid_mode="live"),
    )
    async with db.SessionLocal() as session:
        failed = {"status": "failed", "amount_paise": 50_000, "currency": "INR", "mode": "live"}
        session.add(BillingPayment(user_id=unpaid, username="smoke-unpaid", **failed))
        await session.commit()

        assert [await paid.has_paid(session, user_id) for user_id in (unpaid, tester, payer)] == [False, False, True]


@pytest.mark.parametrize(
    ("key_id", "rejected", "live"),
    [
        ("rzp_live_AbCd", False, True),
        ("rzp_live_AbCd", True, False),
        ("rzp_test_AbCd", False, False),
        ("", False, False),
    ],
)
async def test_payments_are_live_with_live_keys_razorpay_accepts(
    monkeypatch: pytest.MonkeyPatch, key_id: str, rejected: bool, live: bool
) -> None:
    monkeypatch.setattr(settings, "razorpay_key_id", key_id)
    monkeypatch.setattr(settings, "razorpay_key_secret", "secret")
    if rejected:
        await business.set_keys_rejected("Authentication failed")

    assert await paid.payments_live() is live


# ---------------- The test fakes (tests/fake_mail.py) ----------------


def test_svix_sign_matches_svix_s_documented_example() -> None:
    headers = svix_sign(
        "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
        b'{"test": 2432232314}',
        msg_id="msg_p5jXN8AQM9LWM0D4loKWxJek",
        timestamp=1614265330,
    )

    assert headers == {
        "svix-id": "msg_p5jXN8AQM9LWM0D4loKWxJek",
        "svix-timestamp": "1614265330",
        "svix-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=",
    }
    assert svix_sign("whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", b"{}")["svix-id"].startswith("msg_")


def test_the_mail_sink_finds_the_last_link_to_an_address() -> None:
    sink = FakeMail()
    for token in ("first-token", "second-token"):
        message = EmailMessage()
        link = f"https://asknuvexusdoc.com/verify-email#t={token}"
        message["To"] = "John@example.com"
        message.set_content(f"Finish signing up:\n{link}\n")
        message.add_alternative(f'<a href="{link}">Finish sign-up</a>', "html")
        sink.send(message, None)

    assert len(sink.to("john@example.com")) == 2 and sink.to("jane@example.com") == []
    assert sink.last_link("john@example.com") == "https://asknuvexusdoc.com/verify-email#t=second-token"
    assert sink.last_token("john@example.com") == "second-token"
    assert "Finish sign-up" in html(sink.last("john@example.com"))


@pytest.mark.postgres
async def test_two_reports_for_one_address_at_once_leave_one_suppression(app: FastAPI) -> None:
    recipient = emails.digest("bad@example.com")

    async def report(reason: str) -> None:
        async with db.SessionLocal() as session:
            await outbox.suppress(session, recipient, reason=reason, source="webhook")  # type: ignore[arg-type]
            await asyncio.sleep(0.2)  # the other report's insert waits on this one's
            await session.commit()

    await asyncio.gather(report("bounce"), report("bounce"))

    async with db.SessionLocal() as session:
        rows = list(await session.scalars(select(MailSuppression)))
    assert [(row.recipient_sha256, row.reason) for row in rows] == [(recipient, "bounce")]
