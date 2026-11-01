"""Plan reminder emails and the worker's billing loop (docs/briefs/plans-billing.md 4.7 and 6.4,
docs/briefs/signup-auth.md S25; services/billing/reminders.py and reconcile.BillingLoop): "ends in 5 days", "ends
tomorrow" and "has ended", once each per chain, queued as outbox jobs to the account's verified email while the mailer
is set up, none when something follows or without an email; the switch and a renewal checked when the mail goes; the
loop's tick and its processing of woken webhook events."""

import asyncio
from datetime import timedelta

import pytest
from billing_support import CreateUser, give_email, link_of, new_tester, order_row, started
from conftest import ApiClient, drain_mail, grant_plan
from fake_mail import FakeMail, text
from fake_razorpay import FakeRazorpay
from sqlalchemy import select, update

from app import db
from app.config import settings
from app.models import BillingEvent, BillingOrder, EmailOutbox, PlanPeriod, utcnow
from app.redis_client import get_redis
from app.services.billing import business, reconcile, reminders


async def with_email(create_user: CreateUser, name: str, days: int | None = 3) -> ApiClient:
    client = await create_user(name, plan=None)
    await give_email(client.user_id or 0, f"{name}@example.org")
    if days is not None:
        await grant_plan(client.user_id or 0, "standard", days=days)
    return client


async def end_in(user_id: int, left: timedelta) -> None:
    """Moves the user's period so that it ends `left` from now."""
    async with db.SessionLocal() as session:
        (period,) = await session.scalars(select(PlanPeriod).where(PlanPeriod.user_id == user_id))
        by = utcnow() + left - period.ends_at
        period.starts_at, period.ends_at = period.starts_at + by, period.ends_at + by
        await session.commit()


async def mail_jobs() -> list[EmailOutbox]:
    async with db.SessionLocal() as session:
        return list(await session.scalars(select(EmailOutbox).order_by(EmailOutbox.id)))


async def test_no_emails_without_the_mailer(create_user: CreateUser) -> None:
    await with_email(create_user, "alice")

    assert business.email_reminders() is False
    assert await reminders.send_due() == 0
    assert await mail_jobs() == []


async def test_ends_soon_tomorrow_and_ended_once_each(create_user: CreateUser, fake_mail: FakeMail) -> None:
    bob = await with_email(create_user, "bob")

    assert business.email_reminders() is True
    assert await reminders.send_due() == 1
    assert await reminders.send_due() == 0
    await drain_mail()
    await end_in(bob.user_id or 0, timedelta(hours=20))
    assert await reminders.send_due() == 1
    await drain_mail()
    await end_in(bob.user_id or 0, -timedelta(minutes=10))
    assert await reminders.send_due() == 1
    assert await reminders.send_due() == 0
    await drain_mail()
    await end_in(bob.user_id or 0, -timedelta(hours=7))
    assert await reminders.send_due() == 0

    sent = fake_mail.to("bob@example.org")
    assert [message["Subject"] for message in sent] == [
        "Your Standard plan ends in 5 days",
        "Your Standard plan ends tomorrow",
        "Your Standard plan has ended",
    ]
    first = text(sent[0])
    assert first.startswith("Your Standard plan on AskNuvexusDoc ends on ") and " (IST).\n" in first
    assert "Renew it, or choose another plan, here:\nhttp://localhost:8080/plans\n" in first
    assert "Your chats and documents are kept." in first
    assert "Turn plan reminder emails off in Settings → Plan & billing." in first
    assert "bob" not in first.replace("bob@example.org", "")  # never the username
    assert "ended on" in text(sent[2])
    assert {job.mail_class for job in await mail_jobs()} == {"billing"}
    async with db.SessionLocal() as session:
        events = list(await session.scalars(select(BillingEvent.detail).where(BillingEvent.type == "reminder.sent")))
    assert [event["reminder"] for event in events] == ["soon", "tomorrow", "ended"]


async def test_no_reminder_when_something_follows_or_without_an_email(
    create_user: CreateUser, fake_mail: FakeMail
) -> None:
    carol = await with_email(create_user, "carol")
    await grant_plan(carol.user_id or 0, "starter", days=30, start="after_current")
    dave = await with_email(create_user, "dave", days=None)
    await grant_plan(dave.user_id or 0, "standard", days=None)
    erin = await create_user("erin", plan=None)
    await grant_plan(erin.user_id or 0, "standard", days=2)

    assert await reminders.send_due() == 0
    assert await mail_jobs() == []


async def test_the_switch_is_read_when_the_mail_goes(create_user: CreateUser, fake_mail: FakeMail) -> None:
    fay = await with_email(create_user, "fay")
    assert await reminders.send_due() == 1
    response = await fay.patch("/api/me/preferences", json={"email_plan_reminders": False})
    assert response.status_code == 200, response.text

    await drain_mail()

    assert fake_mail.sent == []
    assert [(job.status, job.last_error) for job in await mail_jobs()] == [("cancelled", "pref_off")]


async def test_a_renewal_before_the_mail_goes_cancels_it(create_user: CreateUser, fake_mail: FakeMail) -> None:
    gil = await with_email(create_user, "gil")
    assert await reminders.send_due() == 1
    await grant_plan(gil.user_id or 0, "standard", days=30, start="after_current")

    await drain_mail()

    assert fake_mail.sent == []
    assert [job.last_error for job in await mail_jobs()] == ["not_due"]


async def test_a_reminder_job_with_odd_params_is_cancelled(create_user: CreateUser, fake_mail: FakeMail) -> None:
    await with_email(create_user, "hil")
    assert await reminders.send_due() == 1
    async with db.SessionLocal() as session:
        await session.execute(update(EmailOutbox).values(params={"reminder": "someday"}))
        await session.commit()

    await drain_mail()

    assert fake_mail.sent == [] and [job.last_error for job in await mail_jobs()] == ["not_due"]


@pytest.mark.parametrize(
    ("left", "which"),
    [
        (timedelta(days=6), None),
        (timedelta(days=5), "soon"),
        (timedelta(hours=23), "tomorrow"),
        (timedelta(0), "ended"),
        (-timedelta(hours=5), "ended"),
        (-timedelta(hours=6), None),
    ],
)
def test_which_reminder_is_due(left: timedelta, which: str | None) -> None:
    now = utcnow()

    assert reminders.due(now + left, now) == which


# ---------------- The worker's billing loop ----------------


def test_the_loop_runs_only_with_something_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    assert reconcile.BillingLoop.wanted() is False
    monkeypatch.setattr(settings, "mail_smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "mail_from", "no-reply@example.test")
    assert reconcile.BillingLoop.wanted() is True  # reminders can be emailed
    monkeypatch.setattr(settings, "mail_smtp_host", "")
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_AbCd")
    monkeypatch.setattr(settings, "razorpay_key_secret", "secret")
    assert reconcile.BillingLoop.wanted() is True


@pytest.mark.usefixtures("fake_razorpay")
async def test_the_tick_checks_due_orders_and_records_its_run(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    gus = await new_tester(create_user, "gus")
    public_id = await started(gus, "starter")
    fake_razorpay.pay(await link_of(public_id))
    async with db.SessionLocal() as session:
        await session.execute(update(BillingOrder).values(next_check_at=utcnow()))
        await session.commit()

    await reconcile.BillingLoop(asyncio.Event()).tick()

    assert (await order_row(public_id)).status == "paid"
    assert await business.reconciler_last_run() is not None


@pytest.mark.usefixtures("fake_razorpay")
async def test_the_loop_processes_woken_events_until_it_stops(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    hal = await new_tester(create_user, "hal")
    public_id = await started(hal, "starter")
    link = await link_of(public_id)
    fake_razorpay.pay(link)
    await business.set_keys_rejected("Authentication failed")
    stopping = asyncio.Event()
    loop = reconcile.BillingLoop(stopping)
    running = asyncio.create_task(loop.run())
    await client.post(
        "/api/billing/razorpay/webhook",
        content=(webhook := fake_razorpay.webhook("payment_link.paid", link_id=link))[0],
        headers=webhook[1],
    )

    for _ in range(100):
        if (await order_row(public_id)).status == "paid":
            break
        await asyncio.sleep(0.05)
    stopping.set()
    await get_redis().rpush(business.WAKE_QUEUE, "not-a-number")
    await asyncio.wait_for(running, 10)

    assert (await order_row(public_id)).status == "paid"
    assert await business.keys_rejected() is None  # a new start tries the keys again
