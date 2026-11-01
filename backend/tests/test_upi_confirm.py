"""The admin confirming a direct-UPI payment (services/billing/upi.py confirm, payments.apply_matched;
docs/briefs/upi-direct.md 8.3, D6, D13 and D15): the payment recorded and applied once (a period at max(now, paid
through), mode offline, 30 days, a receipt in the live series, the events, the user counted as paid, paid on the UTR's
day), a second confirm changing nothing (also two at once, on Postgres), the UTR echo, orders that don't wait, a
deleted user, a UTR recorded for a bank transfer (refused both ways), and upgrades: usage while the admin checks
ignored only at the plan's pace (using the allowance up goes to review), usage before the UTR lowering the credit to
review, a replaced period that ended by itself adding 30 days (basis the amount paid) unless usage since the quote
lowered its credit, no credit from a test-mode period, a revoked period going to review."""

import asyncio
from datetime import datetime, timedelta
from fractions import Fraction

import httpx
import pytest
from billing_support import events, payments_of, periods_of, plan_id
from conftest import ApiClient, grant_plan
from fake_razorpay import FakeRazorpay
from sqlalchemy import func, select, update
from upi_support import (
    OTHER_UTR,
    UTR,
    CreateUser,
    change_order,
    confirm,
    make_paid,
    order_of,
    reject,
    set_upi,
    submitted,
    upi_order,
)

from app import db
from app.models import BillingPayment, BillingReceipt, PlanPeriod, UsageEvent, utcnow
from app.services.billing import paid
from app.services.billing.money import PERIOD_DAYS, credit_paise


async def bought_by_upi(client: ApiClient, admin: ApiClient, code: str, utr: str = UTR) -> str:
    public_id = await submitted(client, code, utr=utr)
    response = await confirm(admin, public_id, utr)
    assert response.status_code == 200, response.text
    return public_id


async def test_a_confirmed_payment_buys_its_period_with_a_receipt(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    maya = await create_user("smoke-maya", plan=None)
    assert maya.user_id is not None
    public_id = await submitted(maya, "standard")
    order = await order_of(public_id)

    response = await confirm(admin_client, public_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["channel"], body["method"], body["upi_decided_by"]) == (
        "paid",
        "upi_direct",
        "upi",
        "admin",
    )
    assert body["receipt"]["number"].startswith("RA/") and body["upi"]["decided_at"] is not None
    (payment,) = await payments_of(maya.user_id)
    assert (payment.method, payment.mode, payment.status, payment.razorpay_payment_id) == (
        "upi",
        "offline",
        "captured",
        None,
    )
    assert (payment.offline_reference, payment.recorded_by, payment.amount_paise) == (UTR, "admin", 200_000)
    assert payment.captured_at == order.upi_submitted_at and payment.applied_at is not None
    (period,) = await periods_of(maya.user_id)
    assert (period.mode, period.days, period.source, period.charge_basis_paise) == ("offline", 30, "payment", 200_000)
    assert abs((period.starts_at - utcnow()).total_seconds()) < 60
    async with db.SessionLocal() as session:
        receipt = await session.scalar(select(BillingReceipt))
        assert await paid.has_paid(session, maya.user_id)
    assert receipt is not None and receipt.method == "upi" and receipt.mode == "live"
    assert receipt.lines[0]["detail"] == f"UPI (direct), UTR {UTR}"
    (confirmed,) = await events("upi.confirmed")
    assert (confirmed.actor, confirmed.payment_id) == ("admin", payment.id)
    assert confirmed.detail == {"reference": order.upi_reference, "utr": UTR, "amount": 200_000, "outcome": "applied"}
    assert [event.type for event in await events() if event.order_id == order.id][-3:] == [
        "payment.captured",
        "period.granted",
        "upi.confirmed",
    ]
    me = (await maya.get("/api/auth/me")).json()["plan"]
    assert me["access"] is True and me["plan"]["code"] == "standard" and me["pending_order"] is None
    history = (await maya.get("/api/billing/payments")).json()
    assert history[0]["channel"] == "upi_direct"
    assert history[0]["description"] == f"Standard plan, 30 days (UPI direct, UTR {UTR})"


async def test_a_period_bought_while_one_runs_starts_when_it_ends(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    nia = await create_user("smoke-nia", plan=None)
    assert nia.user_id is not None
    await grant_plan(nia.user_id, "starter", days=10)

    await bought_by_upi(nia, admin_client, "starter")

    granted, bought = await periods_of(nia.user_id)
    assert bought.starts_at == granted.ends_at and bought.days == PERIOD_DAYS


async def test_a_second_confirm_changes_nothing(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    ola = await create_user("smoke-ola", plan=None)
    public_id = await submitted(ola, "starter")
    first = await confirm(admin_client, public_id)

    second = await confirm(admin_client, public_id)
    mismatch = await confirm(admin_client, public_id, OTHER_UTR)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(await payments_of(ola.user_id or 0)) == 1 and len(await periods_of(ola.user_id or 0)) == 1
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "upi_not_waiting"
    assert mismatch.json()["detail"] == "This payment isn't waiting for a check: it is paid."


async def test_a_confirm_needs_the_orders_utr_and_a_waiting_order(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    pam = await create_user("smoke-pam", plan=None)
    quin = await create_user("smoke-quin", plan=None)
    waiting = await submitted(pam, "starter")
    pending = (await upi_order(quin, "starter"))["public_id"]

    wrong = await confirm(admin_client, waiting, OTHER_UTR)
    early = await confirm(admin_client, pending)
    await change_order(pending, expires_at=utcnow() - timedelta(seconds=1))
    late = await confirm(admin_client, pending)
    await reject(admin_client, waiting)
    rejected = await confirm(admin_client, waiting)
    unknown = await confirm(admin_client, "nope")
    await make_paid(admin_client, quin.user_id or 0)
    transfers = await admin_client.get("/api/admin/billing/orders", params={"channel": "bank_transfer"})
    transfer = await confirm(admin_client, transfers.json()["items"][0]["public_id"])
    bad = await admin_client.post(f"/api/admin/billing/orders/{waiting}/upi-confirm", json={"utr": "123"})
    user = await pam.post(f"/api/admin/billing/orders/{waiting}/upi-confirm", json={"utr": UTR})

    assert wrong.status_code == 409
    assert wrong.json() == {
        "detail": "The UTR on this payment is 6305 1234 5678. Check it again.",
        "code": "utr_mismatch",
    }
    assert early.json() == {
        "detail": "This payment isn't waiting for a check: it is waiting for UTR.",
        "code": "upi_not_waiting",
    }
    assert late.json()["detail"] == "This payment isn't waiting for a check: it is expired."
    assert rejected.json()["detail"] == "This payment isn't waiting for a check: it is rejected."
    assert transfer.json()["detail"] == "This payment isn't waiting for a check: it is a bank transfer."
    assert unknown.status_code == 404 and bad.status_code == 422 and user.status_code == 403
    assert await payments_of(pam.user_id or 0) == []


async def transfer(admin: ApiClient, user_id: int, reference: str) -> httpx.Response:
    body = {
        "user_id": user_id,
        "plan_id": await plan_id("starter"),
        "amount_paise": 50_000,
        "received_on": "2026-10-24",
        "reference": reference,
        "note": None,
        "apply": True,
    }
    return await admin.post("/api/admin/billing/offline-payments", json=body)


async def test_a_bank_transfer_cant_use_the_utr_of_a_upi_payment(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    ida = await create_user("smoke-ida", plan=None)
    jon = await create_user("smoke-jon", plan=None)
    kai = await create_user("smoke-kai", plan=None)
    waiting = await submitted(ida, "starter")
    rejected = await submitted(kai, "starter", utr=OTHER_UTR)
    await reject(admin_client, rejected)
    reference = (await order_of(waiting)).upi_reference

    held = await transfer(admin_client, jon.user_id or 0, "UPI 6305 1234 5678")
    await confirm(admin_client, waiting)
    confirmed = await transfer(admin_client, jon.user_id or 0, UTR)
    released = await transfer(admin_client, jon.user_id or 0, f"IMPS/{OTHER_UTR}")

    assert held.status_code == 409
    assert held.json() == {
        "detail": f"This reference is the UTR of a UPI payment from smoke-ida ({reference}), which waits for your "
        "check in Billing → UPI. The same payment can't be recorded twice.",
        "code": "utr_used",
    }
    assert confirmed.status_code == 409 and "which you confirmed." in confirmed.json()["detail"]
    assert released.status_code == 201  # a rejected UTR is free: the owner records the late credit
    assert len(await payments_of(jon.user_id or 0)) == 1


async def test_a_confirm_refuses_a_utr_recorded_for_a_bank_transfer(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    lea = await create_user("smoke-lea", plan=None)
    max_ = await create_user("smoke-max", plan=None)
    public_id = await submitted(lea, "starter")
    await make_paid(admin_client, max_.user_id or 0)
    async with db.SessionLocal() as session:  # recorded while the UTR was sent
        await session.execute(
            update(BillingPayment).where(BillingPayment.user_id == max_.user_id).values(offline_reference=f"UTR {UTR}")
        )
        await session.commit()

    response = await confirm(admin_client, public_id)

    assert response.status_code == 409
    assert response.json() == {
        "detail": 'UTR 6305 1234 5678 is already recorded for a bank transfer from smoke-max. Reject this payment with '
        '"This UTR belongs to another payment".',
        "code": "utr_used",
    }
    assert (await order_of(public_id)).status == "submitted"
    assert await payments_of(lea.user_id or 0) == []


async def test_a_deleted_users_payment_cant_be_confirmed_but_can_be_rejected(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    rex = await create_user("smoke-rex", plan=None)
    public_id = await submitted(rex, "starter")
    assert (await admin_client.delete(f"/api/admin/users/{rex.user_id}")).status_code == 204

    refused = await confirm(admin_client, public_id)
    rejected = await reject(admin_client, public_id, "not_received")

    assert refused.status_code == 409
    assert refused.json() == {
        "detail": "The account was deleted. If you received this payment, pay it back by UPI, then reject it here.",
        "code": "user_deleted",
    }
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert rejected.json()["user"] == {"id": None, "username": "smoke-rex", "email": None}


async def upgrade_waiting(create_user: CreateUser, admin: ApiClient, name: str) -> tuple[ApiClient, str]:
    """A user who bought Starter by UPI and sent the UTR of an upgrade to Professional (credit ₹499)."""
    await set_upi()
    client = await create_user(name, plan=None)
    await bought_by_upi(client, admin, "starter", utr=OTHER_UTR)
    public_id = await submitted(client, "professional", kind="now")
    order = await order_of(public_id)
    assert (order.kind, order.credit_paise, order.amount_paise) == ("now", 49_900, 450_100)
    return client, public_id


async def use_allowance(user_id: int, public_id: str, *, before_utr: bool, tokens: int = 500_000) -> None:
    """Chat usage (by default the whole Starter allowance) between the quote and the UTR, or a second after the UTR."""
    order = await order_of(public_id)
    assert order.upi_submitted_at is not None
    at = order.created_at if before_utr else order.upi_submitted_at + timedelta(seconds=1)
    async with db.SessionLocal() as session:
        session.add(
            UsageEvent(
                user_id=user_id,
                model_name="m",
                kind="chat",
                total_tokens=10,
                allowance_tokens=tokens,
                created_at=at,
            )
        )
        await session.commit()


async def checked_after(user_id: int, public_id: str, wait: timedelta) -> None:
    """The admin checks the upgrade `wait` after its quote and UTR: the user's periods and the order's times move back
    by it."""
    async with db.SessionLocal() as session:
        for period in await session.scalars(select(PlanPeriod).where(PlanPeriod.user_id == user_id)):
            period.starts_at, period.ends_at = period.starts_at - wait, period.ends_at - wait
        await session.commit()
    order = await order_of(public_id)
    assert order.upi_submitted_at is not None and order.replaces_ends_at is not None
    await change_order(
        public_id,
        created_at=order.created_at - wait,
        upi_submitted_at=order.upi_submitted_at - wait,
        replaces_ends_at=order.replaces_ends_at - wait,
    )


async def ended_before_the_check(user_id: int, public_id: str) -> datetime:
    """The Starter period ended a day before the admin's check; the upgrade was quoted six days before that end (its
    credit for those days) and its UTR sent a minute after the quote. Returns the end."""
    async with db.SessionLocal() as session:
        (starter,) = await session.scalars(select(PlanPeriod).where(PlanPeriod.user_id == user_id))
        shift = starter.ends_at - utcnow() + timedelta(days=1)
        starter.starts_at, starter.ends_at = starter.starts_at - shift, starter.ends_at - shift
        ended, starts_at, basis = starter.ends_at, starter.starts_at, starter.charge_basis_paise
        await session.commit()
    quoted = ended - timedelta(days=6)
    credit = credit_paise(basis, starts_at=starts_at, ends_at=ended, now=quoted, used=Fraction(0))
    await change_order(
        public_id,
        created_at=quoted,
        upi_submitted_at=quoted + timedelta(minutes=1),
        replaces_ends_at=ended,
        credit_paise=credit,
        amount_paise=500_000 - credit,
    )
    return ended


async def test_usage_after_the_utr_that_uses_up_the_allowance_sends_the_upgrade_to_review(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    sia, public_id = await upgrade_waiting(create_user, admin_client, "smoke-sia")
    await use_allowance(sia.user_id or 0, public_id, before_utr=False)

    response = await confirm(admin_client, public_id)

    assert response.json()["status"] == "review"
    assert response.json()["review_reason"] == (
        "Usage or a refund since the quote lowered the credit to ₹0 (quoted ₹499). Suggested: refund ₹4,501 or grant "
        "instead."
    )
    (starter,) = await periods_of(sia.user_id or 0)
    assert starter.history == []


async def test_usage_while_the_admin_checks_at_the_plans_pace_is_not_held_against_an_upgrade(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    eli, public_id = await upgrade_waiting(create_user, admin_client, "smoke-eli")
    await checked_after(eli.user_id or 0, public_id, timedelta(days=3))
    await use_allowance(eli.user_id or 0, public_id, before_utr=False, tokens=25_000)  # 5% in three days

    response = await confirm(admin_client, public_id)

    assert response.json()["status"] == "paid"
    starter, professional = await periods_of(eli.user_id or 0)
    assert starter.ends_at == professional.starts_at and starter.history[-1]["kind"] == "ended_by_order"
    assert (professional.mode, professional.charge_basis_paise, professional.days) == ("offline", 500_000, 30)
    (started,) = await events("period.started_now")
    assert started.detail["plan"] == "professional"


async def test_usage_while_the_admin_checks_beyond_the_plans_pace_sends_the_upgrade_to_review(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    fay, public_id = await upgrade_waiting(create_user, admin_client, "smoke-fay")
    await checked_after(fay.user_id or 0, public_id, timedelta(days=3))
    await use_allowance(fay.user_id or 0, public_id, before_utr=False, tokens=100_000)  # 20% in three days

    response = await confirm(admin_client, public_id)

    assert response.json()["status"] == "review"
    reason = response.json()["review_reason"]
    assert reason.startswith("Usage or a refund since the quote lowered the credit to ₹4")  # about ₹450
    assert "(quoted ₹499)" in reason
    assert len(await periods_of(fay.user_id or 0)) == 1


async def test_usage_before_the_utr_lowers_the_credit_to_review(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    tom, public_id = await upgrade_waiting(create_user, admin_client, "smoke-tom")
    await use_allowance(tom.user_id or 0, public_id, before_utr=True)

    response = await confirm(admin_client, public_id)
    again = await confirm(admin_client, public_id)

    body = response.json()
    assert body["status"] == "review" and body["upi_decided_by"] == "admin"
    assert body["review_reason"] == (
        "Usage or a refund since the quote lowered the credit to ₹0 (quoted ₹499). Suggested: refund ₹4,501 or grant "
        "instead."
    )
    assert again.status_code == 200 and again.json()["status"] == "review"
    (starter,) = await periods_of(tom.user_id or 0)
    assert starter.history == []
    payments = await payments_of(tom.user_id or 0)
    assert len(payments) == 2 and payments[-1].applied_at is None and payments[-1].status == "captured"
    (confirmed,) = [event for event in await events("upi.confirmed") if event.detail["outcome"] == "review"]
    assert confirmed.detail["utr"] == UTR


async def test_a_replaced_period_that_ended_by_itself_gets_thirty_days_added(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    uma, public_id = await upgrade_waiting(create_user, admin_client, "smoke-uma")
    ended = await ended_before_the_check(uma.user_id or 0, public_id)
    order = await order_of(public_id)
    assert 9_000 <= order.credit_paise <= 10_000

    response = await confirm(admin_client, public_id)

    assert response.json()["status"] == "paid"
    starter, professional = await periods_of(uma.user_id or 0)
    assert starter.ends_at == ended and starter.history == []
    assert abs((professional.starts_at - utcnow()).total_seconds()) < 60
    assert (professional.days, professional.mode) == (30, "offline")
    assert professional.charge_basis_paise == order.amount_paise  # what was paid: the credit backs nothing now


async def test_usage_after_the_quote_counts_when_the_replaced_period_ended_by_itself(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    gus, public_id = await upgrade_waiting(create_user, admin_client, "smoke-gus")
    await ended_before_the_check(gus.user_id or 0, public_id)
    await use_allowance(gus.user_id or 0, public_id, before_utr=True)

    response = await confirm(admin_client, public_id)

    assert response.json()["status"] == "review"
    assert response.json()["review_reason"].startswith("Usage or a refund since the quote lowered the credit to ₹0")
    assert len(await periods_of(gus.user_id or 0)) == 1


async def test_no_credit_comes_from_a_period_paid_in_test_mode(
    create_user: CreateUser, admin_client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    hal, public_id = await upgrade_waiting(create_user, admin_client, "smoke-hal")
    async with db.SessionLocal() as session:  # an order made before test-mode periods gave no direct-UPI credit
        await session.execute(update(PlanPeriod).where(PlanPeriod.user_id == hal.user_id).values(mode="test"))
        await session.commit()

    response = await confirm(admin_client, public_id)

    assert response.json()["status"] == "review"
    assert response.json()["review_reason"] == (
        "The credit of ₹499 came from a period paid in test mode. Suggested: refund ₹4,501 or grant instead."
    )
    assert len(await periods_of(hal.user_id or 0)) == 1


async def test_a_revoked_replaced_period_sends_the_upgrade_to_review(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    vic, public_id = await upgrade_waiting(create_user, admin_client, "smoke-vic")
    (starter,) = await periods_of(vic.user_id or 0)
    revoked = await admin_client.post(f"/api/admin/billing/periods/{starter.id}/revoke", json={"reason": "Test"})
    assert revoked.status_code == 200

    response = await confirm(admin_client, public_id)

    assert response.json()["status"] == "review"
    assert response.json()["review_reason"] == (
        "The plan this payment was priced against has changed. Suggested: refund ₹4,501 or grant instead."
    )
    assert len(await periods_of(vic.user_id or 0)) == 1


@pytest.mark.postgres
async def test_two_confirms_at_once_apply_once(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    wes = await create_user("smoke-wes", plan=None)
    public_id = await submitted(wes, "standard")

    responses = await asyncio.gather(*(confirm(admin_client, public_id) for _ in range(3)))

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert {response.json()["status"] for response in responses} == {"paid"}
    async with db.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(BillingPayment)) == 1
        assert await session.scalar(select(func.count()).select_from(BillingReceipt)) == 1
    assert len(await periods_of(wes.user_id or 0)) == 1
    assert len(await events("upi.confirmed")) == 1
