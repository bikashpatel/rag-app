"""The customer's UTR (services/billing/upi.py submit_utr; docs/briefs/upi-direct.md 8.2, D5 and D8): the order waits
for the admin, the same UTR again changes nothing, a UTR held by another order is refused (on Postgres also when two
arrive at once) and a rejected one is free again, a UTR recorded for a bank transfer is refused (the UTRs a reference
carries), expired and cancelled orders refuse it with the sentence the
customer reads, the day's tries are capped, the owner is emailed through the outbox (only with a contact email and the
mailer, and nothing once the UTR no longer waits), and no UTR reaches a log."""

import asyncio
import logging
from datetime import timedelta

import pytest
from billing_support import events, give_email, make_tester, plan_id, started
from conftest import ApiClient, drain_mail
from fake_mail import FakeMail, text
from fake_razorpay import FakeRazorpay
from sqlalchemy import func, select
from upi_support import (
    CONTACT,
    OTHER_UTR,
    UTR,
    CreateUser,
    change_order,
    order_of,
    reject,
    send_utr,
    set_upi,
    submitted,
    upi_order,
)

from app import db
from app.models import EmailOutbox, utcnow
from app.services.auth import tokens
from app.services.billing import payments, upi
from app.services.billing.money import IST


async def outbox_jobs(kind: str) -> list[EmailOutbox]:
    async with db.SessionLocal() as session:
        return list(await session.scalars(select(EmailOutbox).where(EmailOutbox.kind == kind)))


async def test_a_utr_sends_the_order_for_checking(create_user: CreateUser) -> None:
    await set_upi()
    maya = await create_user("smoke-maya", plan=None)
    order = await upi_order(maya, "standard")

    response = await send_utr(maya, order["public_id"], " 6305 1234 5678 ")
    again = await send_utr(maya, order["public_id"], UTR)
    other = await send_utr(maya, order["public_id"], OTHER_UTR)

    assert response.status_code == 200, response.text
    view = response.json()
    assert view["status"] == "submitted" and view["upi"]["utr"] == UTR and view["upi"]["uri"] is None
    assert view["upi"]["submitted_at"] is not None and view["upi"]["decided_at"] is None
    assert again.status_code == 200 and again.json() == view
    assert other.status_code == 409
    assert other.json() == {"detail": "This payment was already sent for checking.", "code": "order_not_open"}
    row = await order_of(order["public_id"])
    assert (row.status, row.upi_utr, row.upi_submitted_at is not None) == ("submitted", UTR, True)
    (event,) = await events("upi.submitted")
    assert (event.source, event.actor, event.order_id) == ("user", "smoke-maya", row.id)
    assert event.detail == {"reference": row.upi_reference, "utr": UTR, "amount": 200_000}
    assert await outbox_jobs("upi_submitted") == []  # the mailer isn't configured


async def test_a_utr_is_held_by_one_order_until_it_is_rejected(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    first = await create_user("smoke-first", plan=None)
    second = await create_user("smoke-second", plan=None)
    held = await submitted(first, "starter")
    other = await upi_order(second, "starter")

    used = await send_utr(second, other["public_id"])
    assert (await reject(admin_client, held, "utr_used")).status_code == 200
    freed = await send_utr(second, other["public_id"])

    assert used.status_code == 409
    assert used.json() == {
        "detail": "This UTR is already used for another payment. Check the 12-digit number in your UPI app.",
        "code": "utr_used",
    }
    assert freed.status_code == 200 and freed.json()["upi"]["utr"] == UTR
    assert (await order_of(other["public_id"])).status == "submitted"


def test_the_utrs_a_payment_reference_carries() -> None:
    assert payments.reference_utrs(UTR) == {UTR}
    assert payments.reference_utrs("UTR 6305 1234 5678") == {UTR}
    assert payments.reference_utrs("IMPS/630512345678/ASHA") == {UTR}
    assert payments.reference_utrs("HDFC0001234 630512345678") == {UTR}
    assert payments.reference_utrs("NEFT-1") == set()
    assert payments.reference_utrs("6305123456789") == set()
    assert payments.reference_utrs("1234 5678 9012 3456") == set()


async def test_a_utr_recorded_for_a_bank_transfer_is_refused(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    fen = await create_user("smoke-fen", plan=None)
    gil = await create_user("smoke-gil", plan=None)
    transfer = {
        "user_id": fen.user_id,
        "plan_id": await plan_id("starter"),
        "amount_paise": 50_000,
        "received_on": "2026-10-24",
        "reference": "UPI 6305 1234 5678",
        "note": None,
        "apply": True,
    }
    assert (await admin_client.post("/api/admin/billing/offline-payments", json=transfer)).status_code == 201
    order = await upi_order(gil, "starter")

    used = await send_utr(gil, order["public_id"])
    other = await send_utr(gil, order["public_id"], OTHER_UTR)

    assert used.status_code == 409
    assert used.json() == {
        "detail": "This UTR is already used for another payment. Check the 12-digit number in your UPI app.",
        "code": "utr_used",
    }
    assert other.status_code == 200 and other.json()["upi"]["utr"] == OTHER_UTR


async def test_a_rejected_utr_can_be_sent_again_on_a_new_order(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    nia = await create_user("smoke-nia", plan=None)
    first = await submitted(nia, "starter")
    await reject(admin_client, first)

    second = await submitted(nia, "starter")

    assert (await order_of(second)).upi_utr == (await order_of(first)).upi_utr == UTR


async def test_expired_and_cancelled_orders_refuse_a_utr(create_user: CreateUser) -> None:
    await set_upi()
    ola, pam, quin = [await create_user(name, plan=None) for name in ("smoke-ola", "smoke-pam", "smoke-quin")]
    late = await upi_order(ola, "starter")
    await change_order(late["public_id"], expires_at=utcnow() - timedelta(seconds=1))
    gone = await upi_order(pam, "standard")
    assert (await pam.post(f"/api/billing/orders/{gone['public_id']}/cancel")).status_code == 200
    marked = await upi_order(quin, "starter")
    await change_order(marked["public_id"], status="expired")

    expired = await send_utr(ola, late["public_id"])
    cancelled = await send_utr(pam, gone["public_id"])
    written = await send_utr(quin, marked["public_id"])

    assert expired.status_code == cancelled.status_code == written.status_code == 409
    assert expired.json() == {
        "detail": "This payment request has expired. If you paid, start a new payment for the same plan and enter this "
        "UTR there.",
        "code": "order_not_open",
    }
    assert written.json() == expired.json()
    assert cancelled.json()["detail"] == (
        "This payment request was cancelled. If you paid, start a new payment for the same plan and enter this UTR "
        "there."
    )
    assert (await order_of(late["public_id"])).status == "pending"  # the billing loop writes `expired`
    view = (await ola.get(f"/api/billing/orders/{late['public_id']}")).json()
    assert view["status"] == "expired" and view["upi"]["uri"] is None


async def test_a_utr_is_for_the_callers_own_direct_upi_order(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    await set_upi()
    pia = await create_user("smoke-pia", plan=None)
    raj = await create_user("smoke-raj", plan=None)
    assert raj.user_id is not None
    await make_tester(raj.user_id)
    await give_email(raj.user_id, "smoke-raj@account.example.test")
    order = await upi_order(pia, "starter")
    online = await started(raj, "starter")

    stranger = await send_utr(raj, order["public_id"])
    razorpay = await send_utr(raj, online)
    letters = await pia.post(f"/api/billing/orders/{order['public_id']}/utr", json={"utr": "63051234567X"})

    assert stranger.status_code == razorpay.status_code == 404
    assert letters.status_code == 422


async def test_ten_tries_a_day(create_user: CreateUser, monkeypatch: pytest.MonkeyPatch) -> None:
    await set_upi()
    sia = await create_user("smoke-sia", plan=None)
    order = await upi_order(sia, "starter")
    monkeypatch.setattr(upi, "UTR_DAY_CAP", 3)
    tries = [await send_utr(sia, order["public_id"], f"11111111111{index}") for index in range(3)]

    refused = await send_utr(sia, order["public_id"])

    assert [response.status_code for response in tries] == [200, 409, 409]
    assert refused.status_code == 429
    assert refused.json() == {
        "detail": f"Too many tries today. Try again tomorrow. Or write to {CONTACT}.",
        "code": "too_many_attempts",
    }
    now = utcnow().astimezone(IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    assert abs(int(refused.headers["retry-after"]) - (midnight - now).total_seconds()) <= 5


async def test_the_owner_is_emailed_about_a_utr(create_user: CreateUser, fake_mail: FakeMail) -> None:
    await set_upi()
    tom = await create_user("smoke-tom", plan=None)
    public_id = await submitted(tom, "standard")
    row = await order_of(public_id)

    (job,) = await outbox_jobs("upi_submitted")
    await drain_mail()

    assert (job.mail_class, job.priority, job.user_id, job.params) == ("billing", 1, None, {"order_id": row.id})
    mail = fake_mail.last(CONTACT)
    assert mail["Subject"] == f"UPI payment to check: ₹2,000 ({row.upi_reference})"
    body = text(mail)
    for line in (
        "smoke-tom says they paid ₹2,000 by UPI for Standard, 30 days.",
        "UTR: 6305 1234 5678",
        f"Reference: {row.upi_reference} (in the payment's note, if their app kept it)",
        "Before you confirm, open your bank or UPI app and find a credit of exactly ₹2,000 with this UTR.",
        f"Confirm or reject it:\n{tokens.base_url()}/admin#billing/upi",
        "You get this email because your address is the contact email in Admin → Billing → Business & payments.",
    ):
        assert line in body


async def test_no_owner_email_without_a_contact_address_or_once_it_was_checked(
    create_user: CreateUser, admin_client: ApiClient, fake_mail: FakeMail
) -> None:
    await set_upi(contact=None)
    uma = await create_user("smoke-uma", plan=None)
    await submitted(uma, "starter")
    await set_upi()
    vic = await create_user("smoke-vic", plan=None)
    checked = await submitted(vic, "starter", utr=OTHER_UTR)
    await reject(admin_client, checked)

    jobs = await outbox_jobs("upi_submitted")
    await drain_mail()

    assert len(jobs) == 1
    assert fake_mail.to(CONTACT) == []
    async with db.SessionLocal() as session:
        cancelled = await session.scalar(
            select(func.count()).select_from(EmailOutbox).where(EmailOutbox.last_error == "not_waiting")
        )
    assert cancelled == 1


async def test_no_utr_reaches_a_log(create_user: CreateUser, caplog: pytest.LogCaptureFixture) -> None:
    await set_upi()
    wes = await create_user("smoke-wes", plan=None)
    other = await create_user("smoke-xan", plan=None)

    with caplog.at_level(logging.INFO):
        await submitted(wes, "starter")
        await send_utr(other, (await upi_order(other, "starter"))["public_id"])

    assert UTR not in caplog.text and "6305 1234 5678" not in caplog.text
    assert "asha.rao@okhdfcbank" not in caplog.text


@pytest.mark.postgres
async def test_two_orders_sending_one_utr_at_once_hold_it_once(create_user: CreateUser) -> None:
    await set_upi()
    clients = [await create_user(f"smoke-race-{index}", plan=None) for index in range(2)]
    orders = [await upi_order(client, "starter") for client in clients]

    responses = await asyncio.gather(
        *(send_utr(client, order["public_id"]) for client, order in zip(clients, orders, strict=True))
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert {response.json().get("code") for response in responses if response.status_code == 409} == {"utr_used"}
