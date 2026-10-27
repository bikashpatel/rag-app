"""Flow 17: plans and payments against the fake Razorpay (backend/tests/fake_razorpay.py, run as the fake-razorpay
service with a hosted page and controls: integration/fake_razorpay/server.py). The stack runs test keys, a webhook
secret (and a previous one) and a 2-second reconciler; the paying users are testers. Real Razorpay is never contacted:
the backend has no route to the internet.

Covered: paying through the hosted page and the signed return (a non-tester gets 503); a failure then a payment on one
link; an abandoned link expiring and a late capture applied; the return and webhooks at once (duplicates, a replayed
body under a new id, a failure after the capture) applying once; a tampered amount, a forged return and a forged
webhook; refunds from the admin and from Razorpay's Dashboard, counted once; a dispute raised and lost; renewal, the
one-ahead cap and an upgrade with its credit; expiry with own data still reachable, then renewal; activation by the
reconciler alone; Gmail outside a changed plan and back; the monitor's billing warning."""

import threading
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from harness import (
    APP_URL,
    FAKE_RAZORPAY_URL,
    User,
    alerts_received,
    chat,
    check,
    client,
    documents,
    gmail_connect,
    login,
    sql,
    unique,
    upload,
    wait_document,
    wait_until,
)

# ---------------- The fake Razorpay and the app ----------------


def fake_state() -> dict[str, Any]:
    return check(httpx.get(f"{FAKE_RAZORPAY_URL}/_state", timeout=10)).json()


def control(action: str, **body: Any) -> Any:
    return check(httpx.post(f"{FAKE_RAZORPAY_URL}/_control/{action}", json=body, timeout=30)).json()


def plan_ids(admin: httpx.Client) -> dict[str, int]:
    return {plan["code"]: plan["id"] for plan in check(admin.get("/api/admin/plans")).json()}


def new_user(admin: httpx.Client, name: str, *, tester: bool = True) -> User:
    """A user without a plan; a tester may pay while test keys run."""
    username = unique(f"it-{name}-")
    password = f"pw-{unique('')}-secret"
    body = {"username": username, "password": password, "billing_tester": tester}
    created = check(admin.post("/api/admin/users", json=body), 201).json()
    return User(created["id"], username, password, login(username, password))


def checkout(user: User, admin: httpx.Client, code: str, kind: str = "period", status: int = 201) -> dict[str, Any]:
    body = {"plan_id": plan_ids(admin)[code], "kind": kind, "accept_terms": True}
    return check(user.client.post("/api/billing/checkout", json=body, headers={"Origin": APP_URL}), status).json()


def link_of(started: dict[str, Any]) -> dict[str, Any]:
    return next(link for link in fake_state()["links"] if link["short_url"] == started["pay_url"])


def page_url(pay_url: str) -> str:
    """The hosted page a pay_url stands for, on the fake's published port."""
    assert pay_url.startswith("https://rzp.io/i/"), pay_url
    return f"{FAKE_RAZORPAY_URL}{urlparse(pay_url).path}"


def come_back(callback_url: str) -> httpx.Response:
    """The browser's return to the app: a cross-site GET, so no session cookie."""
    assert callback_url.startswith(f"{APP_URL}/api/billing/razorpay/return?"), callback_url
    with client() as browser:
        return browser.get(callback_url, follow_redirects=False)


def pay_on_page(started: dict[str, Any]) -> httpx.Response:
    """Opens the hosted page, pays, and follows Razorpay's redirect back to the app."""
    page = httpx.get(page_url(started["pay_url"]), timeout=10)
    assert page.status_code == 200 and "Pay" in page.text
    paid = httpx.post(page_url(started["pay_url"]) + "/pay", data={"method": "card"}, timeout=10)
    assert paid.status_code == 303
    return come_back(paid.headers["location"])


def order(user: User, public_id: str, *, refresh: bool = False) -> dict[str, Any]:
    params = {"refresh": "true"} if refresh else {}
    return check(user.client.get(f"/api/billing/orders/{public_id}", params=params)).json()


def plan(user: User) -> dict[str, Any]:
    return check(user.client.get("/api/auth/me")).json()["plan"]


def billing(admin: httpx.Client, user: User) -> dict[str, Any]:
    return check(admin.get(f"/api/admin/billing/users/{user.id}")).json()


def buy(user: User, admin: httpx.Client, code: str, kind: str = "period") -> dict[str, Any]:
    started = checkout(user, admin, code, kind)
    back = pay_on_page(started)
    assert back.status_code == 303
    paid = wait_until(
        lambda: (found := order(user, started["order"]["public_id"]))["status"] == "paid" and found, 30, "paid"
    )
    return paid


def moved_into_the_past(user: User) -> None:
    """The user's periods move back together, the last one ending a minute ago."""
    sql(
        "UPDATE plan_periods SET starts_at = starts_at - shift.by, ends_at = ends_at - shift.by FROM (SELECT "
        f"max(ends_at) - now() + interval '1 minute' AS by FROM plan_periods WHERE user_id = {user.id}) AS shift "
        f"WHERE user_id = {user.id}"
    )


# ---------------- Paying ----------------


def test_a_tester_pays_and_chats_and_others_cant_pay(admin) -> None:
    other = new_user(admin, "pay-other", tester=False)
    catalog = check(other.client.get("/api/billing/plans")).json()
    assert catalog["payments"]["available"] is False
    refused = checkout(other, admin, "starter", status=503)
    assert refused["code"] == "payments_unavailable"

    user = new_user(admin, "pay")
    assert plan(user)["state"] == "none"
    assert check(user.client.get("/api/models"), 402).json()["code"] == "plan_required"
    started = checkout(user, admin, "professional")
    assert started["order"]["status"] == "pending" and started["order"]["test_mode"] is True
    back = pay_on_page(started)
    public_id = started["order"]["public_id"]
    assert back.status_code == 303 and back.headers["location"] == f"{APP_URL}/billing/return?order={public_id}"
    # nginx's server-wide same-origin follows the backend's no-referrer (the referrer here is Razorpay's page anyway)
    assert back.headers["cache-control"] == "no-store" and back.headers["referrer-policy"].startswith("no-referrer")

    paid = order(user, public_id)
    assert paid["status"] == "paid" and paid["method"] == "card" and paid["receipt"]["number"].startswith("TEST/")
    now = plan(user)
    assert now["state"] == "active" and now["plan"]["code"] == "professional" and now["test_mode"] is True
    result = chat(user.client, "What does my plan include?")
    assert result.status == 200 and result.names[-1] == "done"
    receipt = check(user.client.get(f"/api/billing/receipts/{paid['receipt']['id']}"))
    assert receipt.headers["content-type"] == "application/pdf" and receipt.content.startswith(b"%PDF")


def test_a_failure_then_a_payment_on_the_same_link(admin) -> None:
    user = new_user(admin, "fail-pay")
    started = checkout(user, admin, "starter")
    failed = httpx.post(page_url(started["pay_url"]) + "/fail", timeout=10)
    assert failed.status_code == 303
    assert pay_on_page(started).status_code == 303

    public_id = started["order"]["public_id"]
    assert order(user, public_id)["status"] == "paid"
    link = link_of(started)
    statuses = sorted(item["status"] for item in fake_state()["payments"] if item["id"] in link["payments"])
    assert statuses == ["captured", "failed"]
    wait_until(lambda: order(user, public_id, refresh=True)["last_payment_error"], 30, "the failure recorded")
    assert len(billing(admin, user)["periods"]) == 1


def test_an_abandoned_link_expires_and_a_late_capture_is_applied(admin) -> None:
    user = new_user(admin, "late")
    started = checkout(user, admin, "starter")
    public_id = started["order"]["public_id"]
    link = link_of(started)
    control("expire", link_id=link["id"])
    sql(
        "UPDATE billing_orders SET expires_at = now() - interval '2 minutes', next_check_at = now() "
        f"WHERE public_id = '{public_id}'"
    )
    wait_until(lambda: order(user, public_id)["status"] == "expired", 30, "the order expired by the reconciler")
    assert order(user, public_id)["pay_url"] is None

    late = control("authorize", link_id=link["id"])
    check(admin.post(f"/api/admin/billing/orders/{public_id}/check"))
    assert order(user, public_id)["status"] == "expired"
    control("capture", payment_id=late["payment_id"])
    checked = check(admin.post(f"/api/admin/billing/orders/{public_id}/check")).json()

    assert checked["status"] == "paid"
    assert plan(user)["state"] == "active"


def test_the_return_and_webhooks_at_once_apply_once(admin) -> None:
    user = new_user(admin, "race")
    started = checkout(user, admin, "standard")
    public_id = started["order"]["public_id"]
    link = link_of(started)
    paid = control("pay", link_id=link["id"])
    payment_id = paid["query"]["razorpay_payment_id"]
    failed = control("fail", link_id=link["id"])["payment_id"]
    results: list[Any] = []
    calls = [
        lambda: come_back(paid["callback_url"]).status_code,
        lambda: control("webhook", event="payment_link.paid", link_id=link["id"], copies=3, new_ids=1)["statuses"],
        lambda: control("webhook", event="payment.captured", payment_id=payment_id)["statuses"],
        lambda: control("webhook", event="payment.failed", payment_id=failed)["statuses"],
        lambda: control("webhook", event="payment.failed", payment_id=payment_id)["statuses"],  # out of order
    ]
    threads = [threading.Thread(target=lambda call=call: results.append(call())) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sorted(map(str, results)) == sorted(map(str, [303, [200, 200, 200, 200], [200], [200], [200]]))
    wait_until(lambda: order(user, public_id)["status"] == "paid", 30, "paid")
    detail = check(admin.get(f"/api/admin/billing/orders/{public_id}")).json()
    assert [event["type"] for event in detail["events"]].count("period.granted") == 1
    view = billing(admin, user)
    assert len(view["periods"]) == 1
    assert [payment["receipt"] is not None for payment in view["payments"]] == [True]


def test_tampered_and_forged_requests(admin) -> None:
    user = new_user(admin, "forged")
    started = checkout(user, admin, "starter")
    public_id = started["order"]["public_id"]
    tampered = control("pay", link_id=link_of(started)["id"], amount=100)
    query = {name: values[0] for name, values in parse_qs(urlparse(tampered["callback_url"]).query).items()}

    forged = come_back(f"{APP_URL}/api/billing/razorpay/return?{urlencode({**query, 'razorpay_signature': '0' * 64})}")
    assert forged.headers["location"] == f"{APP_URL}/billing/return?order={public_id}&check=failed"
    assert order(user, public_id)["status"] == "pending"
    assert come_back(tampered["callback_url"]).status_code == 303
    reviewed = check(admin.get(f"/api/admin/billing/orders/{public_id}")).json()
    assert reviewed["status"] == "review" and reviewed["review_reason"] == "Amount differs: paid ₹1, expected ₹500"
    assert plan(user)["state"] == "none"

    bad = control("webhook", event="payment_link.paid", link_id=link_of(started)["id"], secret="wrong")
    previous = control("webhook", event="payment_link.paid", link_id=link_of(started)["id"], secret="previous")
    assert bad["statuses"] == [400] and previous["statuses"] == [200]
    check(admin.post(f"/api/admin/billing/orders/{public_id}/resolve", json={"note": "Test: tampered amount"}))


# ---------------- Refunds and disputes ----------------


def payment_of(admin: httpx.Client, user: User) -> dict[str, Any]:
    (payment,) = billing(admin, user)["payments"]
    return payment


def test_refunds_end_access_once(admin) -> None:
    full = new_user(admin, "refund-full")
    buy(full, admin, "starter")
    refunded = check(
        admin.post(
            f"/api/admin/billing/payments/{payment_of(admin, full)['id']}/refund",
            json={"amount_paise": None, "reason": "Test refund", "end_access": False},
        )
    ).json()
    assert refunded["status"] == "refunded" and refunded["refunds"][0]["status"] == "processed"
    assert check(full.client.get("/api/models"), 402).json()["code"] == "plan_expired"

    partial = new_user(admin, "refund-part")
    buy(partial, admin, "starter")
    payment = payment_of(admin, partial)
    check(
        admin.post(
            f"/api/admin/billing/payments/{payment['id']}/refund",
            json={"amount_paise": 10_000, "reason": "Goodwill", "end_access": False},
        )
    )
    assert plan(partial)["state"] == "active"
    dashboard = control("refund", payment_id=payment["razorpay_payment_id"], amount=5_000)
    control("webhook", event="refund.processed", refund_id=dashboard["id"], payment_id=payment["razorpay_payment_id"])
    wait_until(lambda: len(payment_of(admin, partial)["refunds"]) == 2, 30, "the Dashboard refund recorded")
    assert payment_of(admin, partial)["refunded_paise"] == 15_000 and plan(partial)["state"] == "active"

    twice = new_user(admin, "refund-twice")
    buy(twice, admin, "starter")
    payment = payment_of(admin, twice)
    control("settings", refund_status="pending")
    try:
        pending = check(
            admin.post(
                f"/api/admin/billing/payments/{payment['id']}/refund",
                json={"amount_paise": None, "reason": "Test", "end_access": False},
            )
        ).json()
    finally:
        control("settings", refund_status="processed")
    assert pending["refunds"][0]["status"] == "pending"
    refund_id = next(
        item["id"] for item in fake_state()["refunds"] if item["payment_id"] == payment["razorpay_payment_id"]
    )
    control("refund-status", id=refund_id, status="processed")
    control(
        "webhook", event="refund.processed", refund_id=refund_id, payment_id=payment["razorpay_payment_id"], new_ids=1
    )
    wait_until(lambda: payment_of(admin, twice)["refunds"][0]["status"] == "processed", 30, "the refund processed")
    assert payment_of(admin, twice)["refunded_paise"] == 50_000
    assert check(twice.client.get("/api/models"), 402).status_code == 402


def test_a_dispute_raised_then_lost(admin) -> None:
    user = new_user(admin, "dispute")
    buy(user, admin, "standard")
    payment = payment_of(admin, user)
    raised = control("dispute", payment_id=payment["razorpay_payment_id"], respond_by=int(time.time()) + 86_400)
    control("webhook", event="payment.dispute.created", dispute_id=raised["id"])

    def listed() -> bool:
        summary = check(admin.get("/api/admin/billing/summary")).json()
        return any(item["kind"] == "dispute" and user.username in item["message"] for item in summary["attention"])

    wait_until(listed, 30, "the dispute under Needs attention")
    control("dispute-status", id=raised["id"], status="lost")
    control("webhook", event="payment.dispute.lost", dispute_id=raised["id"])
    wait_until(lambda: plan(user)["state"] == "expired", 30, "access ended by the lost dispute")
    assert payment_of(admin, user)["status"] == "disputed"
    attention = check(admin.get("/api/admin/billing/summary")).json()["attention"]
    assert f"Dispute of ₹2,000 by {user.username} was lost" in [
        item["message"] for item in attention if item["kind"] == "dispute_lost"
    ]


# ---------------- Renewal, upgrade and expiry ----------------


def test_renewal_one_period_ahead_and_an_upgrade_with_credit(admin) -> None:
    user = new_user(admin, "renew")
    buy(user, admin, "starter")
    renewed = buy(user, admin, "starter")
    assert renewed["period_start"] is not None
    catalog = check(user.client.get("/api/billing/plans")).json()
    starter = next(item for item in catalog["plans"] if item["code"] == "starter")
    assert [item["unavailable_reason"] for item in starter["options"] if item["kind"] == "period"] == [
        "You already have a period paid ahead."
    ]
    assert checkout(user, admin, "standard", status=409)["code"] == "checkout_unavailable"

    upgrade = checkout(user, admin, "standard", "now")
    assert upgrade["order"]["amount_paise"] == 150_100 and upgrade["order"]["credit_paise"] == 49_900
    pay_on_page(upgrade)
    wait_until(lambda: plan(user)["plan"]["code"] == "standard", 30, "the upgrade applied")
    periods = billing(admin, user)["periods"]
    assert [item["plan_name"] for item in reversed(periods)] == ["Starter", "Standard", "Starter"]


def test_expiry_locks_the_app_but_keeps_the_data_until_renewal(admin) -> None:
    user = new_user(admin, "expiry")
    buy(user, admin, "professional")
    marker = unique("lapse")
    doc = upload(user.client, "kept.txt", f"The {marker} document stays.".encode())
    wait_document(user.client, doc["id"])
    conversation = chat(user.client, f"What about {marker}?").meta["conversation_id"]
    moved_into_the_past(user)

    assert plan(user)["state"] == "expired"
    for response in (
        user.client.get("/api/models"),
        user.client.post("/api/chat/stream", json={"message": "Hello?"}),
        user.client.get(f"/api/conversations/{conversation}"),
        user.client.get("/api/documents/config"),
        user.client.post("/api/documents", files={"file": ("new.txt", b"more")}),
    ):
        assert response.status_code == 402, (response.request.url.path, response.text)
        assert response.json()["code"] in ("plan_expired", "plan_required")
    assert [item["id"] for item in documents(user.client)] == [doc["id"]]
    assert check(user.client.get(f"/api/documents/{doc['id']}/download")).content.startswith(b"The ")
    assert [item["id"] for item in check(user.client.get("/api/conversations")).json()] == [conversation]

    buy(user, admin, "professional")
    assert plan(user)["state"] == "active"
    assert check(user.client.get(f"/api/conversations/{conversation}")).json()["id"] == conversation
    assert chat(user.client, f"And {marker} again?", conversation_id=conversation).names[-1] == "done"


def test_the_reconciler_alone_activates_a_payment(admin) -> None:
    user = new_user(admin, "reconcile")
    started = checkout(user, admin, "starter")
    control("pay", link_id=link_of(started)["id"])  # no return to the app and no webhook

    wait_until(lambda: order(user, started["order"]["public_id"])["status"] == "paid", 90, "the reconciler's check")

    assert plan(user)["state"] == "active"


# ---------------- Gmail and the plan ----------------


@pytest.fixture(scope="module")
def gmail_on(admin) -> Iterator[None]:
    check(admin.patch("/api/admin/plugins/gmail", json={"enabled": True}))
    yield
    check(admin.patch("/api/admin/plugins/gmail", json={"enabled": False}))


@pytest.fixture
def standard_with_every_model(admin) -> Iterator[None]:
    """Standard's seeded models are disabled in this stack (only the fakes are on): it lists every model meanwhile."""
    standard = next(item for item in check(admin.get("/api/admin/plans")).json() if item["code"] == "standard")
    saved = {name: standard[name] for name in ("chat_model_ids", "default_chat_model_id")}
    check(
        admin.patch(f"/api/admin/plans/{standard['id']}", json={"chat_model_ids": None, "default_chat_model_id": None})
    )
    yield
    check(admin.patch(f"/api/admin/plans/{standard['id']}", json=saved))


def test_gmail_outside_a_changed_plan_and_back(admin, gmail_on, standard_with_every_model, fake) -> None:
    user = new_user(admin, "gmail-plan")
    ids = plan_ids(admin)
    check(
        admin.post(
            f"/api/admin/billing/users/{user.id}/grants",
            json={"plan_id": ids["professional"], "days": 30, "reason": "Integration", "start": "now"},
        )
    )
    assert gmail_connect(user) == "connected"
    check(
        admin.post(
            f"/api/admin/billing/users/{user.id}/change-plan", json={"plan_id": ids["standard"], "reason": "Test"}
        )
    )

    plugin = next(item for item in check(user.client.get("/api/plugins")).json() if item["id"] == "gmail")
    assert plugin["in_plan"] is False and plugin["connection"] is not None
    refused = user.client.post("/api/plugins/gmail/threads/search", json={"query": "invoice"})
    assert refused.status_code == 402 and refused.json()["code"] == "feature_not_in_plan"
    mark = fake.mark()
    assert chat(user.client, "Search my email for invoices").names[-1] == "done"
    calls = fake.log("chat", after=mark)
    assert calls and not [name for call in calls for name in call["tools"] if name.startswith("gmail_")]

    check(
        admin.post(
            f"/api/admin/billing/users/{user.id}/change-plan", json={"plan_id": ids["professional"], "reason": "Back"}
        )
    )
    plugin = next(item for item in check(user.client.get("/api/plugins")).json() if item["id"] == "gmail")
    assert plugin["in_plan"] is True
    check(user.client.post("/api/plugins/gmail/threads/search", json={"query": "invoice"}))
    check(
        admin.post(
            f"/api/admin/billing/periods/{billing(admin, user)['periods'][0]['id']}/revoke", json={"reason": "End"}
        )
    )
    check(user.client.delete("/api/plugins/gmail/connection"), 204)


# ---------------- Monitoring ----------------


def test_the_monitor_warns_about_a_review_and_a_dispute_near_its_deadline(admin) -> None:
    start = len(alerts_received())
    user = new_user(admin, "monitor")
    buy(user, admin, "starter")
    payment = payment_of(admin, user)
    raised = control("dispute", payment_id=payment["razorpay_payment_id"], respond_by=int(time.time()) + 86_400)
    control("webhook", event="payment.dispute.created", dispute_id=raised["id"])
    review = checkout(user, admin, "starter")
    come_back(control("pay", link_id=link_of(review)["id"], amount=200)["callback_url"])

    def warned() -> list[str]:
        return [
            line
            for alert in alerts_received()[start:]
            for line in alert["text"].splitlines()
            if line.startswith("PROBLEM Payments") and "dispute to answer within 3 days" in line and "to review" in line
        ]

    found = wait_until(warned, 60, "the monitor's billing warning")
    assert user.username not in " ".join(found) and "₹" not in " ".join(found)
    control("dispute-status", id=raised["id"], status="won")
    control("webhook", event="payment.dispute.won", dispute_id=raised["id"])
    check(admin.post(f"/api/admin/billing/orders/{review['order']['public_id']}/resolve", json={"note": "Test"}))
