"""Receipts (docs/briefs/plans-billing.md 9; services/billing/receipts.py): numbering per mode and Indian financial year
(the IST boundary), counters created when missing and without gaps under concurrency (Postgres), the PDF's text
(seller, buyer, lines, the credit line, ₹, the test watermark, refunds and lost disputes, no GST), the download
headers and who may download. The receipt email (docs/briefs/signup-auth.md S25): through the outbox to the account's
verified email with the PDF attached, honouring the switch; nothing without an email or the mailer; the buyer email is
the account's; billing mail goes out when the other classes have used their share of the day."""

import asyncio
import io
from datetime import datetime

import pytest
from billing_support import CreateUser, buy, give_email, new_tester, order_row, payments_of
from conftest import ApiClient, drain_mail
from fake_mail import FakeMail
from fake_mail import text as mail_text
from fake_razorpay import FakeRazorpay
from pypdf import PdfReader
from sqlalchemy import delete, select

from app import db
from app.config import settings
from app.models import (
    AppState,
    BillingOrder,
    BillingPayment,
    BillingReceipt,
    EmailOutbox,
    User,
    UserBillingProfile,
    utcnow,
)
from app.services.billing import disputes, receipts
from app.services.billing.money import financial_year
from app.services.mail import outbox

pytestmark = pytest.mark.usefixtures("fake_razorpay")


async def receipt_rows() -> list[BillingReceipt]:
    async with db.SessionLocal() as session:
        return list(await session.scalars(select(BillingReceipt).order_by(BillingReceipt.id)))


async def offline(admin: ApiClient, user_id: int, amount: int = 200_000) -> dict:
    plans = (await admin.get("/api/admin/plans")).json()
    response = await admin.post(
        "/api/admin/billing/offline-payments",
        json={
            "user_id": user_id,
            "plan_id": plans[1]["id"],
            "amount_paise": amount,
            "received_on": "2026-10-24",
            "reference": "UTR0001",
            "note": "Paid less by agreement" if amount < 200_000 else None,
            "apply": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


async def test_numbers_run_per_mode_and_financial_year(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    alice = await new_tester(create_user, "alice")
    bob = await create_user("bob", plan=None)
    year = financial_year(utcnow())

    await buy(alice, fake_razorpay, "starter")
    await buy(alice, fake_razorpay, "starter")
    await offline(admin_client, bob.user_id or 0)
    settings = (await admin_client.get("/api/admin/billing/config")).json()["settings"]
    await admin_client.put("/api/admin/billing/settings", json={**settings, "receipt_prefix": "XY"})
    await offline(admin_client, bob.user_id or 0)

    assert [row.number for row in await receipt_rows()] == [
        f"TEST/{year}/000001",
        f"TEST/{year}/000002",
        f"RA/{year}/000001",
        f"XY/{year}/000002",
    ]
    assert [row.mode for row in await receipt_rows()] == ["test", "test", "live", "live"]


async def _payment(user_id: int) -> tuple[BillingOrder, BillingPayment]:
    async with db.SessionLocal() as session:
        order = BillingOrder(
            public_id=f"o{utcnow().timestamp()}",
            user_id=user_id,
            username="carl",
            plan_snapshot={
                "plan_id": 1,
                "code": "starter",
                "name": "Starter",
                "features": {"files": False, "images": False, "plugins": []},
                "limits": {
                    "monthly_token_limit": 1,
                    "rate_limit_per_min": 1,
                    "max_upload_mb": 1,
                    "max_documents": 1,
                    "max_storage_mb": 1,
                    "monthly_image_limit": 1,
                    "vision_monthly_token_limit": 1,
                },
                "restrict_models": False,
                "chat_model_ids": [],
                "default_chat_model_id": None,
            },
            kind="period",
            amount_paise=50_000,
            mode="offline",
            status="paid",
            origin="http://localhost:8080",
            expires_at=utcnow(),
            watch_until=utcnow(),
        )
        session.add(order)
        await session.flush()
        payment = BillingPayment(
            order_id=order.id,
            user_id=user_id,
            username="carl",
            status="captured",
            amount_paise=50_000,
            currency="INR",
            method="bank_transfer",
            mode="offline",
            offline_reference="REF",
        )
        session.add(payment)
        await session.commit()
        return order, payment


async def test_the_financial_year_turns_at_midnight_ist_on_1_april(create_user: CreateUser) -> None:
    carl = await create_user("carl", plan=None)
    numbers = []
    for moment in ("2027-03-31T18:29:59+00:00", "2027-03-31T18:30:00+00:00", "2027-04-01T00:00:00+00:00"):
        order, payment = await _payment(carl.user_id or 0)
        async with db.SessionLocal() as session:
            receipt = await receipts.issue(session, payment, order, None, now=datetime.fromisoformat(moment))
            await session.commit()
            numbers.append(receipt.number)

    assert numbers == ["RA/26-27/000001", "RA/27-28/000001", "RA/27-28/000002"]


async def test_a_missing_counter_is_created(create_user: CreateUser) -> None:
    dina = await create_user("dina", plan=None)
    async with db.SessionLocal() as session:
        await session.execute(delete(AppState).where(AppState.key.like("receipt_seq:%")))
        await session.commit()
    order, payment = await _payment(dina.user_id or 0)

    async with db.SessionLocal() as session:
        receipt = await receipts.issue(session, payment, order, None, now=utcnow())
        await session.commit()

    assert receipt.seq == 1
    async with db.SessionLocal() as session:
        counter = await session.get(AppState, f"receipt_seq:live:{financial_year(utcnow())}")
    assert counter is not None and counter.value == "1"


async def test_the_pdf_holds_the_snapshot_the_credit_and_what_happened_since(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    erin = await new_tester(create_user, "erin")
    await admin_client.put(
        "/api/admin/billing/settings",
        json={
            "business_name": "Example Labs",
            "address": "12 MG Road\nBengaluru 560001",
            "contact_email": "billing@example.com",
            "contact_phone": "+91 80 0000 0000",
            "receipt_prefix": "RA",
            "upi_limit_paise": 200_000,
        },
    )
    await erin.put("/api/billing/profile", json={"name": "Erin Example"})
    await buy(erin, fake_razorpay, "starter")
    order = await order_row(await buy(erin, fake_razorpay, "standard", "now"))
    upgrade = next(payment for payment in await payments_of(erin.user_id or 0) if payment.order_id == order.id)
    await admin_client.post(
        f"/api/admin/billing/payments/{upgrade.id}/refund",
        json={"amount_paise": 10_000, "reason": "Goodwill", "end_access": False},
    )
    (_, receipt) = await receipt_rows()

    response = await erin.get(f"/api/billing/receipts/{receipt.id}")

    assert response.status_code == 200
    text = text_of(response.content)
    for expected in (
        "Example Labs",
        "12 MG Road",
        "billing@example.com · +91 80 0000 0000",
        receipt.number,
        "Erin Example",
        "erin (account)",
        "erin@account.example.test",
        "Standard plan, 1 day",
        "Credit for unused Starter",
        "−₹499.00",
        "₹2,000.00",
        "Total paid",
        "₹1,501.00",
        "TEST: not a real payment",
        "Refunded on",
        "−₹100.00",
        "Goodwill",
        "This receipt was generated electronically and needs no signature.",
    ):
        assert expected in text, expected
    assert "GST" not in text and "Disputed" not in text


async def test_a_lost_dispute_shows_on_the_receipt(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    fay = await new_tester(create_user, "fay")
    await buy(fay, fake_razorpay, "starter")
    (payment,) = await payments_of(fay.user_id or 0)
    dispute = fake_razorpay.raise_dispute(payment.razorpay_payment_id or "", status="lost")
    async with db.SessionLocal() as session:
        await disputes.apply(session, dispute, source="webhook")
    (receipt,) = await receipt_rows()

    text = text_of((await admin_client.get(f"/api/admin/billing/receipts/{receipt.id}")).content)

    assert "Disputed, lost on" in text and "DISPUTED" in text and "−₹500.00" in text


async def test_download_headers_and_who_may_download(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    gus = await new_tester(create_user, "gus")
    hal = await new_tester(create_user, "hal")
    await buy(gus, fake_razorpay, "starter")
    (receipt,) = await receipt_rows()

    mine = await gus.get(f"/api/billing/receipts/{receipt.id}")
    theirs = await hal.get(f"/api/billing/receipts/{receipt.id}")
    admins = await admin_client.get(f"/api/admin/billing/receipts/{receipt.id}")

    assert mine.status_code == 200 and mine.content.startswith(b"%PDF")
    assert mine.headers["content-type"] == "application/pdf"
    filename = receipt.number.replace("/", "-")
    assert mine.headers["content-disposition"] == f'attachment; filename="receipt-{filename}.pdf"'
    assert mine.headers["cache-control"] == "private, no-store"
    assert mine.headers["x-content-type-options"] == "nosniff"
    assert mine.headers["content-security-policy"] == "sandbox"
    assert theirs.status_code == 404 and (await hal.get("/api/billing/receipts/999")).status_code == 404
    assert admins.status_code == 200 and admins.content == mine.content
    assert (await gus.get(f"/api/admin/billing/receipts/{receipt.id}")).status_code == 403


async def test_the_name_on_the_receipt_is_the_username_without_a_profile(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    ian = await new_tester(create_user, "ian")
    await buy(ian, fake_razorpay, "starter")

    (receipt,) = await receipt_rows()

    assert receipt.buyer == {"name": "ian", "username": "ian", "email": "ian@account.example.test"}
    assert receipt.seller["name"] == "AskNuvexusDoc" and receipt.lines[0]["description"] == "Starter plan, 1 day"


@pytest.mark.postgres
async def test_no_gaps_under_concurrency_across_the_year_boundary(create_user: CreateUser) -> None:
    jan = await create_user("jan", plan=None)
    paid = [await _payment(jan.user_id or 0) for _ in range(12)]
    moments = ["2027-03-31T18:29:59+00:00", "2027-03-31T18:30:00+00:00"] * 6

    async def issue(order: BillingOrder, payment: BillingPayment, moment: str) -> None:
        async with db.SessionLocal() as session:
            await receipts.issue(session, payment, order, None, now=datetime.fromisoformat(moment))
            await session.commit()

    await asyncio.gather(
        *(issue(order, payment, moment) for (order, payment), moment in zip(paid, moments, strict=True))
    )

    rows = await receipt_rows()
    assert sorted(row.seq for row in rows if row.financial_year == "26-27") == [1, 2, 3, 4, 5, 6]
    assert sorted(row.seq for row in rows if row.financial_year == "27-28") == [1, 2, 3, 4, 5, 6]
    async with db.SessionLocal() as session:
        assert await session.scalar(select(User.id).where(User.username == "jan")) == jan.user_id
        assert await session.get(UserBillingProfile, jan.user_id) is None


async def test_a_bank_transfer_receipt_names_the_reference(create_user: CreateUser, admin_client: ApiClient) -> None:
    kay = await create_user("kay", plan=None)
    payment = await offline(admin_client, kay.user_id or 0, amount=150_000)
    await admin_client.post(
        f"/api/admin/billing/payments/{payment['id']}/refund",
        json={"amount_paise": 50_000, "reason": "Overpaid", "end_access": False},
    )

    text = text_of((await kay.get(f"/api/billing/receipts/{payment['receipt']['id']}")).content)

    for expected in ("Bank reference", "UTR0001", "Bank transfer, ref UTR0001", "PARTIALLY REFUNDED", "₹1,500.00"):
        assert expected in text, expected
    assert "TEST: not a real payment" not in text


# ---------------- The receipt email ----------------


async def mail_jobs() -> list[EmailOutbox]:
    async with db.SessionLocal() as session:
        return list(await session.scalars(select(EmailOutbox).order_by(EmailOutbox.id)))


async def test_a_receipt_is_mailed_with_its_pdf_to_the_accounts_email(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, fake_mail: FakeMail
) -> None:
    jan = await new_tester(create_user, "jan")
    await buy(jan, fake_razorpay, "starter", method="upi")
    (receipt,) = await receipt_rows()

    assert await drain_mail() == 1

    message = fake_mail.last("jan@account.example.test")
    assert message["Subject"] == f"Receipt {receipt.number} for your Starter plan"
    assert message.get_content_type() == "multipart/mixed"
    body = mail_text(message)
    assert "Amount: ₹500.00" in body and "Method: UPI" in body and f"Period: {receipt.lines[0]['period']}" in body
    assert "The receipt is attached and in Settings → Plan & billing." in body
    assert "Turn receipt emails off in Settings → Plan & billing." in body
    (attachment,) = list(message.iter_attachments())
    assert attachment.get_filename() == f"receipt-{receipt.number.replace('/', '-')}.pdf"
    assert attachment.get_content_type() == "application/pdf"
    downloaded = await jan.get(f"/api/billing/receipts/{receipt.id}")
    assert attachment.get_content() == downloaded.content
    (row,) = await mail_jobs()
    assert (row.kind, row.mail_class, row.params) == ("receipt", "billing", {"receipt_id": receipt.id})


async def test_a_bank_transfer_receipt_is_mailed_too(
    create_user: CreateUser, admin_client: ApiClient, fake_mail: FakeMail
) -> None:
    kim = await create_user("kim", plan=None)
    await give_email(kim.user_id or 0, "kim@example.org")
    await offline(admin_client, kim.user_id or 0)

    await drain_mail()

    assert "Method: Bank transfer" in mail_text(fake_mail.last("kim@example.org"))


async def test_the_switch_turns_receipt_emails_off(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, fake_mail: FakeMail
) -> None:
    lea = await new_tester(create_user, "lea")
    response = await lea.patch("/api/me/preferences", json={"email_receipts": False})
    assert response.status_code == 200, response.text
    await buy(lea, fake_razorpay, "starter")

    await drain_mail()

    assert fake_mail.sent == []
    assert [(row.status, row.last_error) for row in await mail_jobs()] == [("cancelled", "pref_off")]


async def test_no_receipt_email_without_a_verified_email_or_the_mailer(
    create_user: CreateUser, admin_client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    max_ = await create_user("max", plan=None)
    await offline(admin_client, max_.user_id or 0)  # no email
    ned = await new_tester(create_user, "ned")
    await buy(ned, fake_razorpay, "starter")  # no mailer

    assert len(await receipt_rows()) == 2
    assert await mail_jobs() == []


async def test_receipts_go_out_when_the_other_classes_used_their_share(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, fake_mail: FakeMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 10)
    async with db.SessionLocal() as session:
        for number in range(8):
            await outbox.enqueue(session, "test", to=f"tester{number}@example.org")
        await session.commit()
    await drain_mail()
    async with db.SessionLocal() as session:
        assert await outbox.available(session, "account") is False
        assert await outbox.available(session, "billing") is True
    ola = await new_tester(create_user, "ola")

    await buy(ola, fake_razorpay, "starter")
    await drain_mail()

    assert fake_mail.last("ola@account.example.test")["Subject"].startswith("Receipt TEST/")
