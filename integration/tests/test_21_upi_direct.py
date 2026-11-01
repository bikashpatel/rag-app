"""Flow 21: direct UPI payments to the owner's UPI ID (docs/briefs/upi-direct.md 12), through nginx, the backend, the
worker's billing and mail loops and the fake SMTP server. The stack also runs Razorpay test keys, so the billing check
names both channels (the backend tests cover direct UPI alone). No UPI app or bank is involved: the customer's UTR is
typed, the admin confirms or rejects it.

Covered: the admin saving the settings (422 without both fields); a customer without an email ordering by UPI; the QR
codes served through nginx with their headers and the app's CSP unchanged; the UTR, the "UPI payment to check" mail at
the fake and Needs attention; a second customer's same UTR refused; the admin's confirm (Me.plan active, the receipt
PDF reading "UPI (direct)" with the UTR); a bank transfer with that UTR refused; the monitor's warning about a UTR
waiting over 24 hours; a reject (Me.plan's rejected order, the customer's mail); a recorded refund ending access; the
settings put back."""

import io
import secrets
from typing import Any

import httpx
from harness import APP_URL, ENV, User, check, login, sql, unique, wait_until
from pypdf import PdfReader
from test_17_billing import new_user, own_address, plan_ids
from test_19_mail import address, wait_mail

UPI_ID = "it.owner@okicici"
PAYEE = "IT Owner & Co"
PROBLEM_WAIT = 90  # MONITOR_INTERVAL_SECONDS=2


def a_utr() -> str:
    return f"{secrets.randbelow(9 * 10**11) + 10**11}"


def grouped(utr: str) -> str:
    return " ".join(utr[index : index + 4] for index in range(0, 12, 4))


def without_email(admin: httpx.Client, name: str) -> User:
    """A user with no email at all: direct UPI needs none."""
    username = unique(f"it-{name}-")
    password = f"pw-{unique('')}-secret"
    created = check(admin.post("/api/admin/users", json={"username": username, "password": password}), 201).json()
    user = User(created["id"], username, password, login(username, password))
    user.client.headers["CF-Connecting-IP"] = own_address()
    return user


def order_by_upi(user: User, admin: httpx.Client, code: str) -> dict[str, Any]:
    body = {"plan_id": plan_ids(admin)[code], "kind": "period", "accept_terms": True}
    return check(user.client.post("/api/billing/upi/checkout", json=body, headers={"Origin": APP_URL}), 201).json()


def send_utr(user: User, public_id: str, utr: str, status: int = 200) -> dict[str, Any]:
    return check(user.client.post(f"/api/billing/orders/{public_id}/utr", json={"utr": grouped(utr)}), status).json()


def billing_check(admin: httpx.Client) -> dict[str, Any]:
    checks = check(admin.get("/api/admin/system")).json()["checks"]
    return next(item for item in checks if item["id"] == "billing")


def test_direct_upi_from_the_settings_to_a_refund(admin: httpx.Client) -> None:
    before = check(admin.get("/api/admin/billing/config")).json()["settings"]
    contact = address("owner")
    on = {**before, "contact_email": contact, "upi_enabled": True, "upi_id": UPI_ID, "upi_payee_name": PAYEE}
    refused = check(admin.put("/api/admin/billing/settings", json={**on, "upi_id": None}), 422)
    assert "Add your UPI ID and the payee name" in refused.text
    saved = check(admin.put("/api/admin/billing/settings", json={**on, "upi_id": UPI_ID.upper()})).json()
    assert saved["settings"]["upi_id"] == UPI_ID
    try:
        _pay_check_and_refund(admin, contact)
    finally:
        check(admin.put("/api/admin/billing/settings", json=before))


def _pay_check_and_refund(admin: httpx.Client, contact: str) -> None:
    maya = without_email(admin, "upi-maya")
    catalog = check(maya.client.get("/api/billing/plans")).json()
    assert catalog["payments"]["upi_direct"] is True and catalog["payments"]["available"] is False

    order = order_by_upi(maya, admin, "standard")
    public_id, reference = order["public_id"], order["upi"]["reference"]
    assert order["status"] == "pending" and order["channel"] == "upi_direct"
    assert order["upi"]["uri"] == f"upi://pay?pa={UPI_ID}&pn=IT%20Owner%20%26%20Co&am=2000.00&cu=INR&tn={reference}"

    svg = check(maya.client.get(f"/api/billing/orders/{public_id}/upi-qr.svg"))
    png = check(maya.client.get(f"/api/billing/orders/{public_id}/upi-qr.png"))
    assert svg.headers["content-type"] == "image/svg+xml" and svg.text.startswith("<svg ")
    assert "viewBox" in svg.text and "<script" not in svg.text
    assert png.content.startswith(b"\x89PNG") and png.headers["content-disposition"].endswith(f'"upi-{reference}.png"')
    for response in (svg, png):
        assert response.headers["cache-control"] == "private, no-store"
        policies = response.headers.get_list("content-security-policy")
        assert "default-src 'none'; style-src 'unsafe-inline'; sandbox" in policies
        app_policy = next(policy for policy in policies if policy.startswith("default-src 'self'"))
        assert "img-src 'self' data: blob:" in app_policy  # the app's CSP is unchanged
    other = new_user(admin, "upi-peek", tester=False)
    check(other.client.get(f"/api/billing/orders/{public_id}/upi-qr.svg"), 404)

    utr = a_utr()
    waiting = send_utr(maya, public_id, utr)
    assert waiting["status"] == "submitted" and waiting["upi"]["utr"] == utr
    (mail,) = wait_mail(contact)
    assert mail["subject"] == f"UPI payment to check: ₹2,000 ({reference})"
    assert f"UTR: {grouped(utr)}" in mail["text"] and "/admin#billing/upi" in mail["text"]
    attention = check(admin.get("/api/admin/billing/summary")).json()["attention"]
    (item,) = [item for item in attention if item["kind"] == "upi_to_check"]
    assert item["count"] >= 1 and "UPI payment" in item["message"]
    listed = check(admin.get("/api/admin/billing/orders", params={"status": "submitted"})).json()["items"]
    assert public_id in [row["public_id"] for row in listed]

    neha = new_user(admin, "upi-neha", tester=False)
    second = order_by_upi(neha, admin, "starter")
    assert send_utr(neha, second["public_id"], utr, 409)["code"] == "utr_used"

    confirmed = check(admin.post(f"/api/admin/billing/orders/{public_id}/upi-confirm", json={"utr": utr})).json()
    assert confirmed["status"] == "paid" and confirmed["upi_decided_by"] == ENV["ADMIN_USERNAME"]
    me = check(maya.client.get("/api/auth/me")).json()["plan"]
    assert me["state"] == "active" and me["plan"]["code"] == "standard"
    pdf = check(maya.client.get(f"/api/billing/receipts/{confirmed['receipt']['id']}"))
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "UPI (direct)" in text and utr in text and "UPI UTR" in text
    transfer = {
        "user_id": neha.id,
        "plan_id": plan_ids(admin)["starter"],
        "amount_paise": 50_000,
        "received_on": "2026-10-24",
        "reference": f"UPI {grouped(utr)}",
        "note": None,
        "apply": True,
    }
    assert check(admin.post("/api/admin/billing/offline-payments", json=transfer), 409).json()["code"] == "utr_used"

    neha_utr = a_utr()
    send_utr(neha, second["public_id"], neha_utr)
    sql(
        "UPDATE billing_orders SET upi_submitted_at = now() - interval '25 hours' "
        f"WHERE public_id = '{second['public_id']}'"
    )
    warned = wait_until(
        lambda: "UPI payment waiting over 24 hours" in (found := billing_check(admin))["detail"] and found,
        PROBLEM_WAIT,
        "the billing check's direct-UPI warning",
    )
    assert warned["status"] == "warning" and neha.username not in warned["detail"]
    rejected = check(
        admin.post(
            f"/api/admin/billing/orders/{second['public_id']}/upi-reject",
            json={"reason": "not_received", "note": "Nothing arrived"},
        )
    ).json()
    assert rejected["status"] == "rejected"
    shown = check(neha.client.get("/api/auth/me")).json()["plan"]["rejected_order"]
    assert shown["public_id"] == second["public_id"] and shown["rejection"]["note"] == "Nothing arrived"
    assert shown["rejection"]["message"].startswith(f"We couldn't find a payment of ₹500 with UTR {grouped(neha_utr)}.")
    (notice,) = wait_mail(f"{neha.username}@example.test")
    assert notice["subject"] == "We couldn't confirm your UPI payment"
    assert f"/billing/return?order={second['public_id']}" in notice["text"]
    wait_until(
        lambda: "UPI payment waiting" not in billing_check(admin)["detail"],
        PROBLEM_WAIT,
        "the direct-UPI warning to clear",
    )

    payment = next(
        item
        for item in check(admin.get(f"/api/admin/billing/users/{maya.id}")).json()["payments"]
        if item["channel"] == "upi_direct"
    )
    assert (payment["method"], payment["offline_reference"]) == ("upi", utr)
    refunded = check(
        admin.post(
            f"/api/admin/billing/payments/{payment['id']}/refund",
            json={"amount_paise": None, "reason": f"UPI refund {a_utr()}", "end_access": False},
        )
    ).json()
    assert refunded["status"] == "refunded" and refunded["refunds"][0]["status"] == "processed"
    assert check(maya.client.get("/api/models"), 402).json()["code"] == "plan_expired"
