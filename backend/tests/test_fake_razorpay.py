"""The fake Razorpay the billing tests pay with (tests/fake_razorpay.py, the conftest fixture `fake_razorpay`): reached
by any httpx client at https://api.razorpay.com, with Razorpay's authentication, signatures, idempotency and errors."""

import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fake_razorpay import PAYER, FakeRazorpay

from app.config import settings

BASE = "https://api.razorpay.com"


def client(fake: FakeRazorpay, key_secret: str | None = None) -> httpx.AsyncClient:
    token = base64.b64encode(f"{fake.key_id}:{key_secret or fake.key_secret}".encode()).decode()
    return httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Basic {token}"}, timeout=5)


def link_body(**fields: object) -> dict[str, object]:
    return {
        "amount": 200_000,
        "currency": "INR",
        "accept_partial": False,
        "reference_id": "rag-abc",
        "description": "Standard plan, 30 days",
        "expire_by": int(time.time()) + 20 * 60,
        "notify": {"sms": False, "email": False},
        "notes": {"order": "abc"},
        "callback_url": "http://testserver/api/billing/razorpay/return",
        "callback_method": "get",
        **fields,
    }


async def test_the_fixture_sets_test_keys_and_routes_razorpays_host_only(fake_razorpay: FakeRazorpay) -> None:
    assert (settings.razorpay_mode, settings.razorpay_webhook_secret) == ("test", fake_razorpay.webhook_secret)
    async with client(fake_razorpay, key_secret="wrong") as api:
        refused = await api.get("/v1/payments/pay_x")
    assert refused.status_code == 401 and refused.json()["error"]["description"] == "Authentication failed"
    async with httpx.AsyncClient(timeout=2) as other:
        with pytest.raises(httpx.ConnectError):
            await other.get("http://127.0.0.1:1/")


async def test_a_link_is_paid_and_the_callback_is_signed_as_razorpay_signs_it(fake_razorpay: FakeRazorpay) -> None:
    async with client(fake_razorpay) as api:
        created = (await api.post("/v1/payment_links", json=link_body())).json()
        duplicate = await api.post("/v1/payment_links", json=link_body())
        query = fake_razorpay.pay(created["id"])
        link = (await api.get(f"/v1/payment_links/{created['id']}")).json()
        payment = (await api.get(f"/v1/payments/{query['razorpay_payment_id']}")).json()
        found = (await api.get("/v1/payment_links", params={"reference_id": "rag-abc"})).json()
        cancel = await api.post(f"/v1/payment_links/{created['id']}/cancel")

    assert created["short_url"].startswith("https://rzp.io/i/") and created["order_id"].startswith("order_")
    assert duplicate.status_code == 400
    assert (link["status"], link["amount_paid"], link["payments"][0]["payment_id"]) == ("paid", 200_000, payment["id"])
    assert (payment["status"], payment["amount"], payment["order_id"], payment["method"]) == (
        "captured",
        200_000,
        created["order_id"],
        "upi",
    )
    assert payment["fee"] == 4720 and payment["tax"] == 720 and payment["email"] == PAYER["email"]
    message = f"{created['id']}|rag-abc|paid|{payment['id']}".encode()
    expected = hmac.new(fake_razorpay.key_secret.encode(), message, hashlib.sha256).hexdigest()
    assert query["razorpay_signature"] == expected and query["razorpay_payment_link_status"] == "paid"
    assert [item["id"] for item in found["payment_links"]] == [created["id"]]
    assert cancel.status_code == 400  # paid links can't be cancelled


@pytest.mark.parametrize(
    "fields",
    [{"amount": 99}, {"amount": "200000"}, {"expire_by": int(time.time()) + 60}, {"callback_method": "post"}],
)
async def test_links_are_checked_as_razorpay_checks_them(
    fake_razorpay: FakeRazorpay, fields: dict[str, object]
) -> None:
    async with client(fake_razorpay) as api:
        response = await api.post("/v1/payment_links", json=link_body(**fields))

    assert response.status_code == 400 and response.json()["error"]["code"] == "BAD_REQUEST_ERROR"


async def test_failed_late_and_tampered_payments(fake_razorpay: FakeRazorpay) -> None:
    async with client(fake_razorpay) as api:
        link = (await api.post("/v1/payment_links", json=link_body())).json()
        failed = fake_razorpay.fail(link["id"])
        late = fake_razorpay.authorize(link["id"])
        assert (await api.get(f"/v1/payment_links/{link['id']}")).json()["status"] == "created"
        fake_razorpay.capture(late["id"])
        other = (await api.post("/v1/payment_links", json=link_body(reference_id="rag-def"))).json()
        tampered = fake_razorpay.pay(other["id"], amount=100)
        expiring = (await api.post("/v1/payment_links", json=link_body(reference_id="rag-ghi"))).json()
        fake_razorpay.expire(expiring["id"])
        cancelled = (await api.post(f"/v1/payment_links/{expiring['id']}/cancel")).status_code

    assert (failed["status"], failed["error_reason"]) == ("failed", "payment_failed")
    assert fake_razorpay.payments[late["id"]]["status"] == "captured"
    assert fake_razorpay.links[link["id"]]["status"] == "paid"
    assert fake_razorpay.payments[tampered["razorpay_payment_id"]]["amount"] == 100
    assert cancelled == 400


async def test_refunds_are_idempotent_by_key_and_body(fake_razorpay: FakeRazorpay) -> None:
    async with client(fake_razorpay) as api:
        link = (await api.post("/v1/payment_links", json=link_body())).json()
        payment_id = fake_razorpay.pay(link["id"])["razorpay_payment_id"]
        body = {"amount": 50_000, "speed": "normal", "notes": {"refund": "7"}}
        headers = {"X-Refund-Idempotency": "rfd_abcdefghij"}
        first = (await api.post(f"/v1/payments/{payment_id}/refund", json=body, headers=headers)).json()
        again = (await api.post(f"/v1/payments/{payment_id}/refund", json=body, headers=headers)).json()
        changed = await api.post(f"/v1/payments/{payment_id}/refund", json={**body, "amount": 100}, headers=headers)
        too_much = await api.post(f"/v1/payments/{payment_id}/refund", json={"amount": 200_000})
        fetched = (await api.get(f"/v1/refunds/{first['id']}")).json()

    assert first == again == fetched and (first["status"], first["notes"]) == ("processed", {"refund": "7"})
    assert changed.status_code == 400 and too_much.status_code == 400
    assert fake_razorpay.payments[payment_id]["amount_refunded"] == 50_000
    rest = fake_razorpay.refund_in_dashboard(payment_id, status="pending")
    assert rest["amount"] == 150_000 and fake_razorpay.payments[payment_id]["status"] == "refunded"


async def test_webhooks_are_signed_over_the_raw_body(fake_razorpay: FakeRazorpay) -> None:
    async with client(fake_razorpay) as api:
        link = (await api.post("/v1/payment_links", json=link_body())).json()
        payment_id = fake_razorpay.pay(link["id"])["razorpay_payment_id"]
        dispute = fake_razorpay.raise_dispute(payment_id)
        fake_razorpay.set_dispute_status(dispute["id"], "lost")
        fetched = (await api.get(f"/v1/disputes/{dispute['id']}")).json()

    body, headers = fake_razorpay.webhook("payment_link.paid", link_id=link["id"], created_at=1_800_000_000)
    signature = hmac.new(fake_razorpay.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    event = json.loads(body)
    assert headers["X-Razorpay-Signature"] == signature and headers["X-Razorpay-Event-Id"].startswith("evt_")
    assert (event["event"], event["contains"], event["created_at"]) == (
        "payment_link.paid",
        ["payment_link", "order", "payment"],
        1_800_000_000,
    )
    assert event["payload"]["payment"]["entity"]["vpa"] == PAYER["vpa"]
    old_body, old_headers = fake_razorpay.webhook("payment.dispute.lost", dispute_id=dispute["id"], secret="previous")
    assert old_headers["X-Razorpay-Signature"] == hmac.new(b"previous", old_body, hashlib.sha256).hexdigest()
    assert fetched["status"] == "lost"


async def test_failures_and_timeouts_can_be_injected(fake_razorpay: FakeRazorpay) -> None:
    fake_razorpay.fail_next(503, path="/v1/payment_links")
    fake_razorpay.timeout_next()
    async with client(fake_razorpay) as api:
        unavailable = await api.post("/v1/payment_links", json=link_body())
        with pytest.raises(httpx.ReadTimeout):
            await api.get("/v1/payments/pay_x")
        missing = await api.get("/v1/payments/pay_x")

    assert unavailable.status_code == 503 and unavailable.json()["error"]["code"] == "SERVER_ERROR"
    assert missing.status_code == 400
    assert fake_razorpay.requests == [
        ("POST", "/v1/payment_links"),
        ("GET", "/v1/payments/pay_x"),
        ("GET", "/v1/payments/pay_x"),
    ]
