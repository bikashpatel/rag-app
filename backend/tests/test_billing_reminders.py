"""Plan reminder emails and the worker's billing loop (docs/briefs/plans-billing.md 4.7 and 6.4; services/billing/
reminders.py and reconcile.BillingLoop): "ends in 5 days", "ends tomorrow" and "has ended", once each per chain, only
with SMTP, BILLING_EMAIL_FROM and the user's address, none when something follows; failures retried; the loop's tick
and its processing of woken webhook events."""

import asyncio
from datetime import timedelta

import pytest
from billing_support import CreateUser, link_of, new_tester, order_row, started
from conftest import ApiClient, grant_plan
from fake_razorpay import FakeRazorpay
from sqlalchemy import select, update

from app import db
from app.config import settings
from app.models import BillingOrder, PlanPeriod, utcnow
from app.ops.alerts import Notification
from app.ops.channels import ChannelError, EmailChannel
from app.redis_client import get_redis
from app.services.billing import business, reconcile, reminders

Sent = list[tuple[list[str], Notification]]


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> Sent:
    """SMTP and BILLING_EMAIL_FROM set; every email sent lands here instead."""
    sent: Sent = []
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "billing_email_from", "billing@example.test")

    async def send(channel: EmailChannel, notification: Notification) -> None:
        sent.append((channel.recipients, notification))

    monkeypatch.setattr(EmailChannel, "send", send)
    return sent


async def with_email(create_user: CreateUser, name: str, days: int | None = 3) -> ApiClient:
    client = await create_user(name, plan=None)
    await client.put("/api/billing/profile", json={"name": name.title(), "email": f"{name}@example.test"})
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


async def test_no_emails_without_smtp(create_user: CreateUser) -> None:
    await with_email(create_user, "alice")

    assert business.email_reminders() is False
    assert await reminders.send_due() == 0


async def test_ends_soon_tomorrow_and_ended_once_each(create_user: CreateUser, mailbox: Sent) -> None:
    bob = await with_email(create_user, "bob")

    assert await reminders.send_due() == 1
    assert await reminders.send_due() == 0
    await end_in(bob.user_id or 0, timedelta(hours=20))
    assert await reminders.send_due() == 1
    await end_in(bob.user_id or 0, -timedelta(minutes=10))
    assert await reminders.send_due() == 1
    assert await reminders.send_due() == 0
    await end_in(bob.user_id or 0, -timedelta(hours=7))
    assert await reminders.send_due() == 0

    subjects = [notification.title for _, notification in mailbox]
    assert subjects == [
        "Your Standard plan ends in 5 days",
        "Your Standard plan ends tomorrow",
        "Your Standard plan has ended",
    ]
    recipients, first = mailbox[0]
    assert recipients == ["bob@example.test"]
    assert first.lines[0] == "Hello Bob,"
    assert first.lines[2].startswith("Your Standard plan on AskNuvexusDoc ends on ")
    assert first.lines[2].endswith(" (IST).")
    assert "http://localhost:8080/plans" in first.lines
    assert first.lines[-2:] == [
        "You get this email because you gave this address for plan reminders.",
        "Remove it in Settings → Plan & billing to stop them.",
    ]
    assert "ended on" in mailbox[2][1].lines[2]


async def test_no_reminder_when_something_follows_or_without_an_address(create_user: CreateUser, mailbox: Sent) -> None:
    carol = await with_email(create_user, "carol")
    await grant_plan(carol.user_id or 0, "starter", days=30, start="after_current")
    dave = await with_email(create_user, "dave", days=None)
    await grant_plan(dave.user_id or 0, "standard", days=None)
    erin = await create_user("erin", plan=None)
    await grant_plan(erin.user_id or 0, "standard", days=2)

    assert await reminders.send_due() == 0
    assert mailbox == []


async def test_a_failed_send_is_retried(
    create_user: CreateUser, mailbox: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    await with_email(create_user, "fay")

    async def down(channel: EmailChannel, notification: Notification) -> None:
        raise ChannelError("connection refused")

    monkeypatch.setattr(EmailChannel, "send", down)
    assert await reminders.send_due() == 0
    monkeypatch.undo()
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "billing_email_from", "billing@example.test")

    async def send(channel: EmailChannel, notification: Notification) -> None:
        mailbox.append((channel.recipients, notification))

    monkeypatch.setattr(EmailChannel, "send", send)
    assert await reminders.send_due() == 1


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
