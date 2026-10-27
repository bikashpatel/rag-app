"""Disputes, chargebacks (docs/briefs/plans-billing.md 6.8; services/billing/disputes.py): every status from the
webhooks through a fetch, a lost dispute ending access once and counted in the summary, won and closed resolving, the
"Needs attention" item with the deadline and the monitor's warning near it; lost disputes on a purchase and on its
upgrade giving nothing back, and the credit an upgrade took from a lost payment listed under "Needs attention"."""

import time
from typing import Any

import pytest
from billing_support import (
    CreateUser,
    buy,
    events,
    new_tester,
    order_row,
    payments_of,
    periods_of,
    run_worker,
    send_webhook,
)
from conftest import ApiClient
from fake_razorpay import FakeRazorpay
from sqlalchemy import select, update

from app import db
from app.models import BillingDispute, BillingOrder, BillingPayment, PlanPeriod, User
from app.services.billing import disputes, entitlements, periods, summary

pytestmark = pytest.mark.usefixtures("fake_razorpay")


async def paid(user: ApiClient, fake: FakeRazorpay) -> BillingPayment:
    order = await order_row(await buy(user, fake, "standard"))
    return next(payment for payment in await payments_of(user.user_id or 0) if payment.order_id == order.id)


async def raised(client: ApiClient, fake: FakeRazorpay, dispute: dict[str, Any], event: str) -> None:
    await send_webhook(client, *fake.webhook(event, dispute_id=dispute["id"]))
    await run_worker()


async def dispute_rows() -> list[BillingDispute]:
    async with db.SessionLocal() as session:
        return list(await session.scalars(select(BillingDispute)))


async def state(user_id: int) -> str:
    async with db.SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return (await entitlements.load(session, user)).state


async def attention_kinds() -> list[tuple[str, str]]:
    async with db.SessionLocal() as session:
        return [(item.kind, item.message) for item in await summary.attention(session)]


async def test_a_dispute_needing_an_answer_is_listed_with_its_deadline(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    maya = await new_tester(create_user, "maya.chen")
    payment = await paid(maya, fake_razorpay)
    deadline = int(time.time()) + 2 * 86_400
    dispute = fake_razorpay.raise_dispute(payment.razorpay_payment_id or "", respond_by=deadline)

    await raised(client, fake_razorpay, dispute, "payment.dispute.created")

    (row,) = await dispute_rows()
    assert row.status == "open" and row.amount_paise == 200_000 and row.reason_code == "chargeback"
    assert row.respond_by is not None and int(row.respond_by.timestamp()) == deadline
    (item,) = [message for kind, message in await attention_kinds() if kind == "dispute"]
    assert item.startswith("Dispute of ₹2,000 by maya.chen: respond in Razorpay's Dashboard by ")
    assert "1 dispute to answer within 3 days" in await summary.problems()
    assert await state(maya.user_id or 0) == "active"


@pytest.mark.parametrize("status", ["under_review", "action_required"])
async def test_a_dispute_moving_on_is_recorded_once(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient, status: str
) -> None:
    ben = await new_tester(create_user, "ben")
    dispute = fake_razorpay.raise_dispute((await paid(ben, fake_razorpay)).razorpay_payment_id or "")
    await raised(client, fake_razorpay, dispute, "payment.dispute.created")
    fake_razorpay.set_dispute_status(dispute["id"], status)

    await raised(client, fake_razorpay, dispute, f"payment.dispute.{status}")
    await raised(client, fake_razorpay, dispute, f"payment.dispute.{status}")

    assert [row.status for row in await dispute_rows()] == [status]
    assert len(await events(f"dispute.{status}")) == 1


async def test_a_lost_dispute_ends_access_once_and_is_counted(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient, admin_client: ApiClient
) -> None:
    cara = await new_tester(create_user, "cara")
    payment = await paid(cara, fake_razorpay)
    dispute = fake_razorpay.raise_dispute(payment.razorpay_payment_id or "", status="lost")

    await raised(client, fake_razorpay, dispute, "payment.dispute.lost")
    async with db.SessionLocal() as session:
        assert await disputes.apply(session, fake_razorpay.disputes[dispute["id"]], source="reconcile") == "same"

    (row,) = await dispute_rows()
    assert row.status == "lost" and row.access_ended_at is not None
    assert await state(cara.user_id or 0) == "expired"
    (lost,) = await payments_of(cara.user_id or 0)
    assert lost.status == "disputed"
    assert len(await events("period.revoked")) == 1
    report = (await admin_client.get("/api/admin/billing/summary")).json()
    assert (
        report["disputed_paise"] == 200_000
        and report["net_paise"] == report["gross_paise"] - 200_000 - report["fee_paise"]
    )
    assert not [kind for kind, _ in await attention_kinds() if kind == "dispute"]
    assert (await cara.get("/api/billing/payments")).json()[0]["status"] == "disputed"


@pytest.mark.parametrize("status", ["won", "closed"])
async def test_a_dispute_won_or_closed_is_resolved(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient, status: str
) -> None:
    dina = await new_tester(create_user, "dina")
    dispute = fake_razorpay.raise_dispute((await paid(dina, fake_razorpay)).razorpay_payment_id or "")
    await raised(client, fake_razorpay, dispute, "payment.dispute.created")
    fake_razorpay.set_dispute_status(dispute["id"], status)

    await raised(client, fake_razorpay, dispute, f"payment.dispute.{status}")

    assert [row.status for row in await dispute_rows()] == [status]
    assert not [kind for kind, _ in await attention_kinds() if kind == "dispute"]
    assert await state(dina.user_id or 0) == "active"


async def test_a_dispute_of_an_unknown_payment_is_counted_as_a_failure(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    eve = await new_tester(create_user, "eve")
    dispute = fake_razorpay.raise_dispute((await paid(eve, fake_razorpay)).razorpay_payment_id or "")
    async with db.SessionLocal() as session:
        assert await disputes.apply(session, {**dispute, "payment_id": "pay_Unknown0000001"}, source="x") == "unknown"
    fake_razorpay.disputes[dispute["id"]]["payment_id"] = "pay_Unknown0000001"

    await raised(client, fake_razorpay, dispute, "payment.dispute.created")

    (event,) = [event for event in await events() if event.razorpay_event_id]
    assert event.processed is False and event.attempts == 1
    assert event.error == "The dispute's payment isn't known here"
    assert await dispute_rows() == []


async def test_a_lost_dispute_is_a_row_of_the_csv_export(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient, admin_client: ApiClient
) -> None:
    fay = await new_tester(create_user, "fay")
    dispute = fake_razorpay.raise_dispute((await paid(fay, fake_razorpay)).razorpay_payment_id or "", status="lost")
    await raised(client, fake_razorpay, dispute, "payment.dispute.lost")

    exported = (await admin_client.get("/api/admin/billing/export.csv")).text

    assert [line.split(",")[1] for line in exported.splitlines()[1:]] == ["payment", "dispute lost"]


async def upgraded(user: ApiClient, fake: FakeRazorpay) -> tuple[BillingPayment, BillingPayment]:
    """Standard bought, then Professional now with the credit for the unused Standard."""
    first = await paid(user, fake)
    order = await order_row(await buy(user, fake, "professional", "now"))
    upgrade = next(payment for payment in await payments_of(user.user_id or 0) if payment.order_id == order.id)
    return first, upgrade


async def test_losing_the_disputes_of_a_purchase_and_its_upgrade_leaves_no_plan(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    gus = await new_tester(create_user, "gus")
    first, upgrade = await upgraded(gus, fake_razorpay)

    for payment in (first, upgrade):
        dispute = fake_razorpay.raise_dispute(payment.razorpay_payment_id or "", status="lost")
        await raised(client, fake_razorpay, dispute, "payment.dispute.lost")

    assert await state(gus.user_id or 0) == "expired"
    standard, professional = await periods_of(gus.user_id or 0)
    assert standard.revoked_at is not None and standard.revoked_reason == "Dispute lost"
    assert standard.ends_at <= professional.starts_at  # ended by the upgrade, never given back
    assert professional.revoked_at is not None
    assert await events("period.restored") == []


async def test_a_lost_dispute_whose_credit_paid_for_an_upgrade_needs_attention(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    hal = await new_tester(create_user, "hal")
    first, upgrade = await upgraded(hal, fake_razorpay)
    order = await order_by_payment(upgrade)
    dispute = fake_razorpay.raise_dispute(first.razorpay_payment_id or "", status="lost")

    await raised(client, fake_razorpay, dispute, "payment.dispute.lost")

    async with db.SessionLocal() as session:
        items = {item.kind: item for item in await summary.attention(session)}
    assert "duplicate" not in items  # an upgrade isn't a duplicate payment
    assert items["dispute_lost"].message == "Dispute of ₹2,000 by hal was lost"
    assert items["dispute_lost"].payment_id == first.id
    credit = items["credit_reversed"]
    assert credit.message.startswith(f"₹{order.credit_paise // 100:,} of a disputed payment by hal went as credit")
    assert credit.payment_id == upgrade.id and credit.order_public_id == order.public_id
    assert await state(hal.user_id or 0) == "active"  # the admin decides about the upgrade
    assert [event.detail["credit"] for event in await events("credit.reversed")] == [order.credit_paise]


async def order_by_payment(payment: BillingPayment) -> BillingOrder:
    async with db.SessionLocal() as session:
        found = await session.get(BillingOrder, payment.order_id)
    assert found is not None
    return found


async def test_restoring_a_period_whose_own_payment_was_reversed_is_refused(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    ida = await new_tester(create_user, "ida")
    first, upgrade = await upgraded(ida, fake_razorpay)
    async with db.SessionLocal() as session:
        await session.execute(update(BillingPayment).where(BillingPayment.id == first.id).values(status="disputed"))
        await session.commit()
        standard = (await periods_of(ida.user_id or 0))[0]
        period = await session.get(PlanPeriod, standard.id)
        assert period is not None and period.revoked_at is None

        restored = await periods.restore(
            session, period, order_id=upgrade.order_id or 0, actor=None, source="admin"
        )

    assert restored is False
