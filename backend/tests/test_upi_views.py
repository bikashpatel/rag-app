"""What direct UPI shows elsewhere (docs/briefs/upi-direct.md 8.5, 8.7, D9, D10 and D17): the billing loop expiring
orders without a UTR, Needs attention's UTRs to check, the monitor's billing check with direct UPI on, the admin's order
list by channel, receipts (PDF and email facts), the CSV, the data export, a user's deletion, and a refund recorded by
the admin ending what the payment bought."""

import asyncio
import csv
import io
import json
import zipfile
from datetime import timedelta

import pytest
from billing_support import events, periods_of
from conftest import ApiClient
from pypdf import PdfReader
from upi_support import (
    UTR,
    CreateUser,
    change_order,
    confirm,
    order_of,
    set_upi,
    submitted,
    upi_order,
)

from app import db
from app.models import BillingReceipt, utcnow
from app.ops import checks
from app.services.billing import receipts, reconcile, summary, upi
from app.services.billing.money import ist_date


async def test_the_billing_loop_expires_orders_without_a_utr(create_user: CreateUser) -> None:
    await set_upi()
    ada, bea = await create_user("smoke-ada", plan=None), await create_user("smoke-bea", plan=None)
    late = (await upi_order(ada, "starter"))["public_id"]
    waiting = await submitted(bea, "starter")
    await change_order(late, expires_at=utcnow() - timedelta(minutes=1))
    await change_order(waiting, expires_at=utcnow() - timedelta(minutes=1))
    before = (await ada.get("/api/auth/me")).json()["plan"]["pending_order"]

    await reconcile.BillingLoop(asyncio.Event()).tick()

    assert before is None  # a direct-UPI order past its expiry is no longer open
    assert (await order_of(late)).status == "expired"
    assert (await order_of(waiting)).status == "submitted"  # a UTR waits for the admin whatever the time
    (expired,) = await events("order.expired")
    assert (expired.source, expired.detail) == ("system", {"public_id": late, "channel": "upi_direct"})
    assert await upi.expire_due() == 0


async def test_needs_attention_counts_the_utrs_to_check(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    cal, dee = await create_user("smoke-cal", plan=None), await create_user("smoke-dee", plan=None)
    oldest = await submitted(cal, "standard")
    await submitted(dee, "starter", utr="630500000009")
    await change_order(oldest, upi_submitted_at=utcnow() - timedelta(hours=25))

    found = (await admin_client.get("/api/admin/billing/summary")).json()["attention"]

    (item,) = [item for item in found if item["kind"] == "upi_to_check"]
    sent = (await order_of(oldest)).upi_submitted_at
    assert sent is not None
    assert item["message"] == "2 UPI payments to check" and item["count"] == 2
    assert item["order_public_id"] == oldest
    assert item["detail"] == (
        f"The oldest: ₹2,000 from smoke-cal, sent {summary._sent(sent)} (over a day ago). Find each credit in your "
        "bank or UPI app before you confirm it."
    )
    assert summary._sent(utcnow().replace(year=2026, month=11, day=1, hour=9, minute=40)) == "1 Nov, 15:10 IST"
    await confirm(admin_client, oldest)
    (single,) = [i for i in (await admin_client.get("/api/admin/billing/summary")).json()["attention"] if i["count"]]
    assert single["message"] == "1 UPI payment to check" and "(over a day ago)" not in single["detail"]


@pytest.mark.usefixtures("app")
async def test_the_billing_check_runs_with_direct_upi_alone(create_user: CreateUser) -> None:
    assert (await checks.check_billing()).detail == "Payments not set up"
    await set_upi()
    assert (await checks.check_billing()).detail == "Direct UPI on; nothing needs attention"
    eve = await create_user("smoke-eve", plan=None)
    public_id = await submitted(eve, "starter")
    assert (await checks.check_billing()).status == "ok"
    await change_order(public_id, upi_submitted_at=utcnow() - timedelta(hours=25))

    found = await checks.check_billing()

    assert (found.status, found.detail) == ("warning", "1 UPI payment waiting over 24 hours")


@pytest.mark.usefixtures("app", "fake_razorpay")
async def test_the_billing_check_names_both_channels() -> None:
    await set_upi()

    assert (await checks.check_billing()).detail == "Payments in test mode; direct UPI on; nothing needs attention"


async def test_the_admins_order_list_by_channel(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    fay, gus = await create_user("smoke-fay", plan=None), await create_user("smoke-gus", plan=None)
    later = await submitted(fay, "starter")
    first = await submitted(gus, "standard", utr="630500000010")
    await change_order(first, upi_submitted_at=utcnow() - timedelta(hours=1))
    await admin_client.post(
        "/api/admin/billing/offline-payments",
        json={
            "user_id": fay.user_id,
            "plan_id": (await order_of(later)).plan_id,
            "amount_paise": 50_000,
            "received_on": "2026-10-24",
            "reference": "NEFT-9",
            "note": None,
            "apply": False,
        },
    )

    async def listed(**params: str) -> list[str]:
        response = await admin_client.get("/api/admin/billing/orders", params=params)
        assert response.status_code == 200, response.text
        return [item["public_id"] for item in response.json()["items"]]

    to_check = await listed(status="submitted")
    direct = await listed(channel="upi_direct")
    transfers = (await admin_client.get("/api/admin/billing/orders", params={"channel": "bank_transfer"})).json()
    online = await listed(channel="razorpay")
    bad = await admin_client.get("/api/admin/billing/orders", params={"channel": "cash"})

    assert to_check == [first, later]  # the oldest submission first
    assert set(direct) == {first, later}
    assert [item["channel"] for item in transfers["items"]] == ["bank_transfer"]
    assert online == []
    assert bad.status_code == 422


async def test_receipts_say_upi_direct_with_the_utr(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    hal = await create_user("smoke-hal", plan=None)
    public_id = await submitted(hal, "standard")
    receipt_id = (await confirm(admin_client, public_id)).json()["receipt"]["id"]

    pdf = await hal.get(f"/api/billing/receipts/{receipt_id}")
    async with db.SessionLocal() as session:
        receipt = await session.get(BillingReceipt, receipt_id)
        assert receipt is not None
        facts = await receipts.facts(session, receipt)

    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "UPI (direct)" in text and "UPI UTR" in text and UTR in text
    assert f"UPI (direct), UTR {UTR}" in text
    assert facts.method == "UPI (direct)"
    assert receipts.method_label("upi", offline=False) == "UPI" and receipts.method_label(None, offline=True) is None


async def test_the_csv_names_direct_upi_and_its_utr(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    ivy = await create_user("smoke-ivy", plan=None)
    await confirm(admin_client, await submitted(ivy, "standard"))

    month = ist_date(utcnow()).strftime("%Y-%m")
    response = await admin_client.get("/api/admin/billing/export.csv", params={"month": month})

    header, row = list(csv.reader(io.StringIO(response.text)))
    assert header[8] == "Razorpay payment id, bank reference or UTR"
    assert (row[1], row[7], row[8]) == ("payment", "upi_direct", UTR)


async def test_the_data_export_holds_the_upi_orders(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    jay = await create_user("smoke-jay", plan=None)
    paid = await submitted(jay, "starter")
    await confirm(admin_client, paid)
    waiting = (await upi_order(jay, "standard"))["public_id"]

    response = await jay.post("/api/me/export", json={})

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        billing = json.loads(archive.read("billing.json"))
    orders = {order["id"]: order for order in billing["orders"]}
    row = await order_of(paid)
    assert orders[paid]["channel"] == "upi_direct" and orders[paid]["status"] == "paid"
    assert (orders[paid]["reference"], orders[paid]["utr"], orders[paid]["rejection"]) == (row.upi_reference, UTR, None)
    assert orders[paid]["submitted_at"] is not None and orders[paid]["decided_at"] is not None
    assert orders[waiting]["utr"] is None and orders[waiting]["status"] == "pending"
    assert "upi_vpa" not in json.dumps(billing) and "decided_by" not in json.dumps(billing)


async def test_deleting_a_user_cancels_a_pending_order_and_keeps_a_submitted_one(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    kim, lee = await create_user("smoke-kim", plan=None), await create_user("smoke-lee", plan=None)
    pending = (await upi_order(kim, "starter"))["public_id"]
    waiting = await submitted(lee, "starter")

    for client in (kim, lee):
        assert (await admin_client.delete(f"/api/admin/users/{client.user_id}")).status_code == 204

    assert (await order_of(pending)).status == "cancelled"
    kept = await order_of(waiting)
    assert (kept.status, kept.user_id, kept.username) == ("submitted", None, "smoke-lee")
    listed = (await admin_client.get("/api/admin/billing/orders", params={"status": "submitted"})).json()["items"]
    assert [item["user"]["id"] for item in listed] == [None]


async def test_a_recorded_refund_of_a_upi_payment_ends_access(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    max_ = await create_user("smoke-max", plan=None)
    await confirm(admin_client, await submitted(max_, "standard"))
    payment = (await admin_client.get("/api/admin/billing/payments")).json()["items"][0]
    assert (payment["method"], payment["channel"], payment["offline_reference"]) == ("upi", "upi_direct", UTR)

    response = await admin_client.post(
        f"/api/admin/billing/payments/{payment['id']}/refund",
        json={"amount_paise": None, "reason": "UTR 630599999999", "end_access": False},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "refunded" and body["refunds"][0]["status"] == "processed"
    (period,) = await periods_of(max_.user_id or 0)
    assert period.revoked_at is not None
    assert (await max_.get("/api/auth/me")).json()["plan"]["access"] is False
