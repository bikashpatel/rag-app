"""The admin rejecting a direct-UPI payment (services/billing/upi.py reject; docs/briefs/upi-direct.md 8.4, D6 and
D14): each reason's sentence (with and without the business email) and the admin's note, the event, a second reject
changing nothing, orders that don't wait, the customer's email (with a verified address and the mailer; skipped with
receipt emails off), Me.plan.rejected_order for 7 days and until a newer order, and a new payment after it."""

from datetime import timedelta

import pytest
from billing_support import events, give_email
from conftest import ApiClient, drain_mail
from fake_mail import FakeMail, text
from sqlalchemy import select
from upi_support import (
    CONTACT,
    UTR,
    CreateUser,
    change_order,
    order_of,
    reject,
    set_upi,
    submitted,
    upi_checkout,
    upi_order,
)

from app import db
from app.models import EmailOutbox, utcnow
from app.services.auth import tokens

MESSAGES = {
    "not_received": "We couldn't find a payment of ₹2,000 with UTR 6305 1234 5678. If money left your account, write "
    f"to {CONTACT} with a screenshot of the payment.",
    "amount_differs": "The payment with UTR 6305 1234 5678 wasn't for ₹2,000. We'll send what you paid back to you by "
    "UPI. Start a new payment for the exact amount.",
    "utr_used": "UTR 6305 1234 5678 belongs to another payment. Check the 12-digit number in your UPI app, then start "
    "a new payment.",
    "other": "We couldn't confirm your payment of ₹2,000.",
}


@pytest.mark.parametrize("reason", list(MESSAGES))
async def test_each_reason_tells_the_customer_why(
    create_user: CreateUser, admin_client: ApiClient, reason: str
) -> None:
    await set_upi()
    maya = await create_user("smoke-maya", plan=None)
    public_id = await submitted(maya, "standard")
    note = "Paid twice by mistake" if reason == "other" else None

    response = await reject(admin_client, public_id, reason, note)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["upi_decided_by"]) == ("rejected", "admin")
    assert body["upi"]["rejection"] == {"reason": reason, "message": MESSAGES[reason], "note": note}
    view = (await maya.get(f"/api/billing/orders/{public_id}")).json()
    assert view["status"] == "rejected" and view["upi"]["rejection"]["message"] == MESSAGES[reason]
    rejected = (await maya.get("/api/auth/me")).json()["plan"]["rejected_order"]
    assert (rejected["public_id"], rejected["utr"], rejected["amount_paise"]) == (public_id, UTR, 200_000)
    assert rejected["rejection"]["message"] == MESSAGES[reason] and rejected["plan"]["code"] == "standard"
    row = await order_of(public_id)
    (event,) = await events("upi.rejected")
    assert (event.actor, event.order_id) == ("admin", row.id)
    assert event.detail == {
        "reference": row.upi_reference,
        "utr": UTR,
        "amount": 200_000,
        "reason": reason,
        "note": note,
    }


async def test_without_a_business_email_the_customer_writes_from_settings(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi(contact=None)
    nia = await create_user("smoke-nia", plan=None)
    public_id = await submitted(nia, "standard")

    body = (await reject(admin_client, public_id, "not_received", "Checked twice")).json()

    assert body["upi"]["rejection"] == {
        "reason": "not_received",
        "message": "We couldn't find a payment of ₹2,000 with UTR 6305 1234 5678. If money left your account, write "
        "to us from Settings.",
        "note": "Checked twice",
    }


async def test_a_second_reject_changes_nothing_and_only_waiting_orders_are_rejected(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    ola = await create_user("smoke-ola", plan=None)
    pam = await create_user("smoke-pam", plan=None)
    public_id = await submitted(ola, "starter")
    pending = (await upi_order(pam, "starter"))["public_id"]

    first = await reject(admin_client, public_id, "not_received")
    second = await reject(admin_client, public_id, "amount_differs")
    early = await reject(admin_client, pending)
    other = await admin_client.post(f"/api/admin/billing/orders/{public_id}/upi-reject", json={"reason": "other"})
    unknown = await reject(admin_client, "nope")

    assert second.status_code == 200 and second.json() == first.json()
    assert early.status_code == 409 and early.json()["code"] == "upi_not_waiting"
    assert early.json()["detail"] == "This payment isn't waiting for a check: it is waiting for UTR."
    assert other.status_code == 422 and unknown.status_code == 404
    assert len(await events("upi.rejected")) == 1


async def test_the_customer_is_emailed_with_a_verified_address(
    create_user: CreateUser, admin_client: ApiClient, fake_mail: FakeMail
) -> None:
    await set_upi()
    quin = await create_user("smoke-quin", plan=None)
    rex = await create_user("smoke-rex", plan=None)
    assert quin.user_id is not None and rex.user_id is not None
    await give_email(quin.user_id, "quin@account.example.test")
    await give_email(rex.user_id, "rex@account.example.test")
    assert (await rex.patch("/api/me/preferences", json={"email_receipts": False})).status_code == 200
    sia = await create_user("smoke-sia", plan=None)
    mailed = await submitted(quin, "standard")
    off = await submitted(rex, "standard", utr="630500000001")
    silent = await submitted(sia, "standard", utr="630500000002")

    for public_id in (mailed, off, silent):
        await reject(admin_client, public_id, "other", "Paid twice by mistake")
    async with db.SessionLocal() as session:
        jobs = list(await session.scalars(select(EmailOutbox).where(EmailOutbox.kind == "upi_rejected")))
    await drain_mail()

    assert sorted(job.user_id or 0 for job in jobs) == sorted([quin.user_id, rex.user_id])
    assert {(job.mail_class, job.priority) for job in jobs} == {("billing", 2)}
    assert fake_mail.to("rex@account.example.test") == []
    mail = fake_mail.last("quin@account.example.test")
    assert mail["Subject"] == "We couldn't confirm your UPI payment"
    body = text(mail)
    for line in (
        "We couldn't confirm your payment of ₹2,000.",
        "Note from AskNuvexusDoc: Paid twice by mistake",
        f"Open the payment:\n{tokens.base_url()}/billing/return?order={mailed}",
        f"Questions? Contact us:\n{CONTACT}",
        "Turn receipt emails off in Settings → Plan & billing.",
    ):
        assert line in body


async def test_the_rejection_shows_for_seven_days_and_until_a_newer_order(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    tom = await create_user("smoke-tom", plan=None)
    uma = await create_user("smoke-uma", plan=None)
    shown = await submitted(tom, "starter")
    old = await submitted(uma, "starter", utr="630500000003")
    for public_id in (shown, old):
        await reject(admin_client, public_id)
    await change_order(old, upi_decided_at=utcnow() - timedelta(days=7, minutes=1))

    async def rejected_order(client: ApiClient) -> object:
        return (await client.get("/api/auth/me")).json()["plan"]["rejected_order"]

    assert (await rejected_order(tom)) is not None
    assert await rejected_order(uma) is None
    again = await upi_checkout(tom, "starter")  # a new payment after a rejection
    assert again.status_code == 201
    assert await rejected_order(tom) is None
    assert (await tom.get("/api/billing/status")).json()["open_order"]["public_id"] == again.json()["public_id"]
