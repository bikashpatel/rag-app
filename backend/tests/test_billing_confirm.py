"""Confirming payments (docs/briefs/plans-billing.md 6.4 to 6.6; services/billing/webhooks.py, reconcile.py and
payments.py): the signed return from Razorpay's page, webhooks recorded and processed by the worker only after a
fetch, the reconciler's schedule and backoff, rejected keys, orders of the other mode, late captures, and every case
that goes to review instead of applying."""

import json
import time
from datetime import datetime, timedelta
from typing import Any

import pytest
from billing_support import (
    CreateUser,
    buy,
    come_back,
    events,
    link_of,
    new_tester,
    order_row,
    payments_of,
    periods_of,
    run_worker,
    send_webhook,
    started,
)
from conftest import ApiClient
from fake_razorpay import FakeRazorpay, hmac_hex
from sqlalchemy import update

from app import db
from app.config import settings
from app.models import BillingOrder, PlanPeriod, utcnow
from app.redis_client import get_redis
from app.services.billing import business, payments, periods, reconcile, summary, webhooks

pytestmark = pytest.mark.usefixtures("fake_razorpay")


async def set_order(public_id: str, **values: Any) -> None:
    async with db.SessionLocal() as session:
        await session.execute(update(BillingOrder).where(BillingOrder.public_id == public_id).values(**values))
        await session.commit()


async def check(public_id: str) -> bool:
    order = await order_row(public_id)
    async with db.SessionLocal() as session:
        return await reconcile.check_order(session, order.id, source="reconcile")


# ---------------- The return from Razorpay's page ----------------


async def test_a_signed_return_applies_the_payment_without_a_session(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    alice = await new_tester(create_user, "alice")
    public_id = await started(alice, "starter")

    response = await come_back(client, fake_razorpay.pay(await link_of(public_id)))

    assert response.status_code == 303
    assert response.headers["location"] == f"http://localhost:8080/billing/return?order={public_id}"
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    order = await order_row(public_id)
    assert order.status == "paid" and order.next_check_at is None
    (period,) = await periods_of(alice.user_id or 0)
    assert period.mode == "test" and period.days == 1 and period.order_id == order.id
    assert period.ends_at - period.starts_at <= timedelta(days=2)
    assert period.snapshot["limits"]["monthly_token_limit"] == 500_000  # test periods keep the whole allowance
    (payment,) = await payments_of(alice.user_id or 0)
    assert payment.applied_at is not None and payment.method == "upi" and payment.fee_paise == 1_180
    assert [event.type for event in await events() if event.order_id == order.id][-2:] == [
        "payment.captured",
        "period.granted",
    ]


async def test_a_bad_signature_changes_nothing_and_is_sampled(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    bob = await new_tester(create_user, "bob")
    public_id = await started(bob, "starter")
    query = fake_razorpay.pay(await link_of(public_id))

    first = await come_back(client, {**query, "razorpay_signature": "0" * 64})
    second = await come_back(client, {**query, "razorpay_payment_link_status": "created"})

    target = f"http://localhost:8080/billing/return?order={public_id}&check=failed"
    assert first.headers["location"] == second.headers["location"] == target
    assert (await order_row(public_id)).status == "pending"
    assert await business.counted("billing:cb:badsig") == 2
    assert len(await events("callback.bad_signature")) == 1  # one sample a minute
    assert await periods_of(bob.user_id or 0) == []


async def test_an_unknown_link_goes_to_the_plan_page(client: ApiClient) -> None:
    for link in ("plink_Unknown000001", "../etc", "x" * 65):
        response = await come_back(client, {"razorpay_payment_link_id": link, "razorpay_signature": "x"})
        assert response.status_code == 303
        assert response.headers["location"] == "http://localhost:8080/plans?payment=unknown"
    assert await business.counted("billing:cb:unknown") == 3
    (sample,) = await events("callback.unknown")
    assert sample.detail == {"link": "plink_Unknown000001"}


async def test_an_applied_payment_isnt_fetched_again(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    carol = await new_tester(create_user, "carol")
    public_id = await started(carol, "starter")
    query = fake_razorpay.pay(await link_of(public_id))
    await come_back(client, query)
    fake_razorpay.requests.clear()

    again = await come_back(client, query)

    assert again.status_code == 303 and fake_razorpay.requests == []
    assert len(await periods_of(carol.user_id or 0)) == 1


async def test_a_running_check_keeps_the_return_from_fetching(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    dave = await new_tester(create_user, "dave")
    public_id = await started(dave, "starter")
    query = fake_razorpay.pay(await link_of(public_id))
    order = await order_row(public_id)
    await get_redis().set(f"billing:refresh:{order.id}", "1")
    fake_razorpay.requests.clear()

    response = await come_back(client, query)

    assert response.status_code == 303 and fake_razorpay.requests == []
    late = await order_row(public_id)
    assert late.status == "pending" and late.next_check_at is not None and late.next_check_at <= utcnow()


async def test_a_failed_fetch_leaves_it_to_the_reconciler(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    erin = await new_tester(create_user, "erin")
    public_id = await started(erin, "starter")
    query = fake_razorpay.pay(await link_of(public_id))
    fake_razorpay.fail_next(503, path="/v1/payment_links/")

    response = await come_back(client, query)
    checked = await reconcile.run_due()

    assert response.status_code == 303
    assert checked == 1 and (await order_row(public_id)).status == "paid"


# ---------------- Webhooks ----------------


async def test_webhooks_need_a_secret(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "")

    response = await send_webhook(client, b"{}", {"X-Razorpay-Signature": "x"})

    assert response.status_code == 503
    assert await business.counted("billing:wh:unconfigured") == 1


async def test_a_bad_webhook_signature_is_400(client: ApiClient, fake_razorpay: FakeRazorpay) -> None:
    body = json.dumps({"event": "payment.captured", "payload": {}, "created_at": int(time.time())}).encode()
    for _ in range(2):
        response = await send_webhook(client, body, {"X-Razorpay-Signature": hmac_hex("wrong", body)})
        assert response.status_code == 400
    assert await business.counted("billing:wh:badsig") == 2
    assert len(await events("webhook.bad_signature")) == 1


async def test_a_body_over_256_kb_is_413(client: ApiClient, fake_razorpay: FakeRazorpay) -> None:
    body = b" " * (256 * 1024 + 1)

    response = await send_webhook(client, body, {"X-Razorpay-Signature": hmac_hex(fake_razorpay.webhook_secret, body)})

    assert response.status_code == 413


async def test_a_stale_body_is_acknowledged_and_ignored(
    create_user: CreateUser, client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    fiona = await new_tester(create_user, "fiona")
    link = await link_of(await started(fiona, "starter"))
    body, headers = fake_razorpay.webhook(
        "payment_link.paid", link_id=link, created_at=int(time.time()) - 3 * 86_400 - 60
    )

    response = await send_webhook(client, body, headers)

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert [event for event in await events() if event.source == "webhook"] == []
    assert await business.counted("billing:wh:stale") == 1


async def test_a_webhook_is_recorded_then_processed_by_the_worker_after_a_fetch(
    create_user: CreateUser, client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    gina = await new_tester(create_user, "gina")
    public_id = await started(gina, "standard")
    link = await link_of(public_id)
    fake_razorpay.pay(link)
    body, headers = fake_razorpay.webhook("payment_link.paid", link_id=link)

    response = await send_webhook(client, body, headers)

    assert response.status_code == 200 and response.json() == {"ok": True}
    (event,) = [event for event in await events() if event.source == "webhook"]
    assert event.processed is False and event.razorpay_event_id == headers["X-Razorpay-Event-Id"]
    assert event.detail["payment_link"] == link and event.detail["status"] == "paid"
    assert not {"email", "contact", "vpa", "bank"} & set(json.dumps(event.detail).split('"'))
    assert (await order_row(public_id)).status == "pending"  # nothing changes from the payload
    fake_razorpay.requests.clear()

    assert await run_worker() == [event.id]

    assert ("GET", f"/v1/payment_links/{link}") in fake_razorpay.requests
    assert (await order_row(public_id)).status == "paid"
    (done,) = [event for event in await events() if event.razorpay_event_id]
    assert done.processed is True and done.order_id == (await order_row(public_id)).id


async def test_duplicates_by_event_id_or_body_are_recorded_once(
    create_user: CreateUser, client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    hana = await new_tester(create_user, "hana")
    link = await link_of(await started(hana, "starter"))
    fake_razorpay.pay(link)
    body, headers = fake_razorpay.webhook("payment_link.paid", link_id=link)
    replayed = {**headers, "X-Razorpay-Event-Id": "evt_Replayed000001"}
    other_body, other_headers = fake_razorpay.webhook(
        "payment.captured", link_id=link, event_id=headers["X-Razorpay-Event-Id"]
    )

    responses = [
        await send_webhook(client, body, headers),
        await send_webhook(client, body, headers),
        await send_webhook(client, body, replayed),
        await send_webhook(client, other_body, other_headers),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert len([event for event in await events() if event.source == "webhook"]) == 1


async def test_the_previous_webhook_secret_still_works(
    client: ApiClient, fake_razorpay: FakeRazorpay, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "razorpay_webhook_secret_previous", "old-webhook-secret")
    body, headers = fake_razorpay.webhook("payment_link.expired", secret="old-webhook-secret")

    response = await send_webhook(client, body, headers)

    assert response.status_code == 200
    (event,) = [event for event in await events() if event.source == "webhook"]
    await run_worker()
    assert (await events("payment_link.expired"))[0].processed is True  # no order matches: nothing to do
    assert event.type == "payment_link.expired"


async def test_an_unmatched_payment_event_is_matched_once_the_order_is_known(
    create_user: CreateUser, client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    ivy = await new_tester(create_user, "ivy")
    public_id = await started(ivy, "starter")
    link = await link_of(public_id)
    await set_order(public_id, razorpay_order_id=None)
    query = fake_razorpay.pay(link)
    body, headers = fake_razorpay.webhook("payment.captured", payment_id=query["razorpay_payment_id"])
    await send_webhook(client, body, headers)

    await run_worker()
    (waiting,) = await events("payment.captured")
    assert waiting.source == "webhook" and waiting.processed is False and waiting.attempts == 0
    await set_order(public_id, razorpay_link_id=None)  # the reconciler finds the link by our reference
    assert await check(public_id) is True
    await run_worker()

    assert [event.processed for event in await events("payment.captured") if event.source == "webhook"] == [True]
    assert (await order_row(public_id)).status == "paid" and (await order_row(public_id)).razorpay_link_id == link


async def test_a_failing_event_goes_to_attention_after_three_attempts(
    client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    body, headers = fake_razorpay.webhook("refund.processed")
    payload = json.loads(body)
    payload["payload"] = {"refund": {"entity": {"id": "rfnd_Missing000001", "payment_id": "pay_X"}}}
    body = json.dumps(payload).encode()
    headers["X-Razorpay-Signature"] = hmac_hex(fake_razorpay.webhook_secret, body)
    await send_webhook(client, body, headers)
    (event,) = [event for event in await events() if event.source == "webhook"]

    for _ in range(4):
        await webhooks.process(event.id)

    (failed,) = [event for event in await events() if event.source == "webhook"]
    assert failed.processed is False and failed.attempts == 3 and failed.error == "The id provided does not exist"
    async with db.SessionLocal() as session:
        items = await summary.attention(session)
    assert [item.message for item in items if item.kind == "webhook_errors"] == ["1 webhook event failed 3 times"]
    assert await webhooks.pending() == []


async def test_other_events_are_ignored(client: ApiClient, fake_razorpay: FakeRazorpay) -> None:
    body, headers = fake_razorpay.webhook("order.paid")

    await send_webhook(client, body, headers)
    await run_worker()

    (event,) = await events("order.paid")
    assert event.processed is True


# ---------------- The reconciler ----------------


def order_at(expires: datetime, **values: Any) -> BillingOrder:
    return BillingOrder(
        status=values.pop("status", "pending"),
        expires_at=expires,
        watch_until=expires + timedelta(days=5),
        **values,
    )


@pytest.mark.parametrize(
    ("after_expiry", "in_flight", "expected"),
    [
        (timedelta(minutes=-10), False, timedelta(minutes=-10, seconds=30)),  # every 30 s while the link lives
        (timedelta(seconds=-10), False, timedelta(minutes=1)),
        (timedelta(seconds=10), False, timedelta(minutes=1)),
        (timedelta(minutes=2), False, timedelta(hours=1)),
        (timedelta(hours=2), False, timedelta(hours=6)),
        (timedelta(hours=7), False, timedelta(hours=24)),
        (timedelta(hours=25), False, timedelta(hours=48)),
        (timedelta(hours=2), True, timedelta(hours=3)),  # hourly while a payment is in flight
        (timedelta(days=4, hours=23), False, timedelta(days=5)),  # the last check at watch_until
    ],
)
def test_the_reconcilers_schedule(after_expiry: timedelta, in_flight: bool, expected: timedelta) -> None:
    expires = datetime.fromisoformat("2026-10-26T10:00:00+00:00")

    found = reconcile.next_check(order_at(expires), expires + after_expiry, in_flight=in_flight)

    assert found == expires + expected


def test_final_orders_are_no_longer_checked() -> None:
    expires = datetime.fromisoformat("2026-10-26T10:00:00+00:00")
    assert reconcile.next_check(order_at(expires, status="paid"), expires, in_flight=False) is None
    assert reconcile.next_check(order_at(expires, status="review"), expires, in_flight=True) is None
    assert reconcile.next_check(order_at(expires), expires + timedelta(days=5), in_flight=True) is None


async def test_errors_back_off_up_to_15_minutes(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    jay = await new_tester(create_user, "jay")
    public_id = await started(jay, "starter")
    delays = []
    for _ in range(7):
        fake_razorpay.fail_next(503, path="/v1/payment_links/")
        await check(public_id)
        order = await order_row(public_id)
        assert order.next_check_at is not None and order.last_checked_at is not None
        delays.append(round((order.next_check_at - order.last_checked_at).total_seconds()))
    assert delays == [60, 120, 240, 480, 900, 900, 900]
    assert order.last_error == "Razorpay answered 503 (SERVER_ERROR)"
    await check(public_id)
    order = await order_row(public_id)
    assert order.check_step == 0 and order.last_error is None and order.checks == 8


async def test_rejected_keys_stop_every_check_until_the_admin_retries(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    kim = await new_tester(create_user, "kim")
    lee = await new_tester(create_user, "lee")
    first, second = await started(kim, "starter"), await started(lee, "starter")
    await set_order(first, next_check_at=utcnow())
    await set_order(second, next_check_at=utcnow())
    fake_razorpay.fail_next(401, "Authentication failed")

    assert await reconcile.run_due() == 1  # the second order isn't checked
    assert await reconcile.run_due() == 0
    assert await business.keys_rejected() is not None
    config = (await admin_client.get("/api/admin/billing/config")).json()
    assert config["payments"]["last_error"] == "Razorpay rejected the API keys: Authentication failed"

    await set_order(first, next_check_at=utcnow())  # it backed off after the 401
    retried = await admin_client.post("/api/admin/billing/reconcile")

    assert retried.status_code == 200 and retried.json() == {"checked": 2}
    assert await business.keys_rejected() is None


async def test_orders_of_the_other_mode_are_paused(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    mia = await new_tester(create_user, "mia")
    public_id = await started(mia, "starter")
    await set_order(public_id, mode="live", next_check_at=utcnow())
    fake_razorpay.requests.clear()

    assert await reconcile.run_due() == 0
    assert await check(public_id) is False
    assert fake_razorpay.requests == []
    order = (await admin_client.get(f"/api/admin/billing/orders/{public_id}")).json()
    assert order["paused"] is True and order["mode"] == "live"
    assert (await admin_client.get("/api/admin/billing/config")).json()["payments"]["paused_orders"] == 1


async def test_an_abandoned_link_expires_and_a_late_capture_is_still_applied(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    ned = await new_tester(create_user, "ned")
    public_id = await started(ned, "starter")
    link = await link_of(public_id)
    fake_razorpay.expire(link)
    await set_order(public_id, expires_at=utcnow() - timedelta(minutes=2))
    await check(public_id)
    expired = await order_row(public_id)
    assert expired.status == "expired" and expired.next_check_at is not None
    payment = fake_razorpay.authorize(link)
    await check(public_id)
    assert (await order_row(public_id)).status == "expired"
    fake_razorpay.capture(payment["id"])

    await check(public_id)

    order = await order_row(public_id)
    assert order.status == "paid" and order.next_check_at is None
    (period,) = await periods_of(ned.user_id or 0)
    assert period.starts_at >= order.created_at


async def test_a_failure_then_a_payment_on_the_same_link(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    olga = await new_tester(create_user, "olga")
    public_id = await started(olga, "starter")
    link = await link_of(public_id)
    fake_razorpay.fail(link)
    await check(public_id)
    order = await order_row(public_id)
    assert order.status == "pending" and order.last_payment_error == "The bank declined it."
    fake_razorpay.pay(link)

    await check(public_id)

    statuses = sorted(payment.status for payment in await payments_of(olga.user_id or 0))
    assert statuses == ["captured", "failed"] and (await order_row(public_id)).status == "paid"
    assert len(await periods_of(olga.user_id or 0)) == 1
    view = (await olga.get(f"/api/billing/orders/{public_id}")).json()
    assert view["status"] == "paid" and view["last_payment_error"] == "The bank declined it."


# ---------------- Review ----------------


async def test_a_tampered_amount_goes_to_review(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    pia = await new_tester(create_user, "pia")
    public_id = await started(pia, "starter")

    await come_back(client, fake_razorpay.pay(await link_of(public_id), amount=100))

    order = await order_row(public_id)
    assert order.status == "review" and order.review_reason == "Amount differs: paid ₹1, expected ₹500"
    assert await periods_of(pia.user_id or 0) == []
    assert [event.type for event in await events("payment.mismatch")] == ["payment.mismatch"]
    async with db.SessionLocal() as session:
        items = await summary.attention(session)
    review = next(item for item in items if item.kind == "review")
    assert review.order_public_id == public_id and review.detail == "pia · Starter · ₹500"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"currency": "USD"}, "Currency differs: paid in USD, expected INR"),
        ({"order_id": "order_Other00000001"}, "The payment belongs to another Razorpay order"),
    ],
)
async def test_a_mismatched_payment_goes_to_review(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, change: dict[str, str], reason: str
) -> None:
    quinn = await new_tester(create_user, "quinn")
    public_id = await started(quinn, "starter")
    query = fake_razorpay.pay(await link_of(public_id))
    entity = {**fake_razorpay.payments[query["razorpay_payment_id"]], **change}
    order = await order_row(public_id)

    async with db.SessionLocal() as session:
        outcome = await payments.apply_payment(session, order.id, entity, source="reconcile")

    assert outcome == "review" and (await order_row(public_id)).review_reason == reason


async def test_a_payment_of_the_other_mode_goes_to_review(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    rita = await new_tester(create_user, "rita")
    public_id = await started(rita, "starter")
    query = fake_razorpay.pay(await link_of(public_id))
    await set_order(public_id, mode="live")
    order = await order_row(public_id)

    async with db.SessionLocal() as session:
        entity = fake_razorpay.payments[query["razorpay_payment_id"]]
        assert await payments.apply_payment(session, order.id, entity, source="webhook") == "review"

    assert (await order_row(public_id)).review_reason == "Paid in live mode while the server runs test keys"


async def test_a_second_payment_for_one_checkout_goes_to_review(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    sam = await new_tester(create_user, "sam")
    public_id = await buy(sam, fake_razorpay, "starter")
    query = fake_razorpay.pay(await link_of(public_id))

    await send_webhook(client, *fake_razorpay.webhook("payment.captured", payment_id=query["razorpay_payment_id"]))
    await run_worker()

    order = await order_row(public_id)
    assert order.status == "review" and order.review_reason == "A second payment for one checkout"
    assert len(await periods_of(sam.user_id or 0)) == 1


async def test_a_payment_for_a_deleted_user_goes_to_review(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    tom = await new_tester(create_user, "tom")
    public_id = await started(tom, "starter")
    fake_razorpay.pay(await link_of(public_id))
    await set_order(public_id, user_id=None)

    await check(public_id)

    assert (await order_row(public_id)).review_reason == "The user was deleted"


async def test_an_upgrade_now_ends_the_current_period_and_starts_the_new_one(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    uma = await new_tester(create_user, "uma")
    await buy(uma, fake_razorpay, "starter")
    await buy(uma, fake_razorpay, "starter")  # a period paid ahead

    public_id = await buy(uma, fake_razorpay, "standard", "now")

    order = await order_row(public_id)
    assert order.status == "paid" and order.amount_paise == 150_100 and order.credit_paise == 49_900
    starter, standard, ahead = await periods_of(uma.user_id or 0)
    assert standard.snapshot["code"] == "standard" and standard.charge_basis_paise == 200_000
    assert starter.ends_at == standard.starts_at and starter.history[-1]["kind"] == "ended_by_order"
    assert ahead.snapshot["code"] == "starter" and ahead.starts_at == standard.ends_at


async def test_an_upgrade_priced_against_a_changed_period_goes_to_review(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    vic = await new_tester(create_user, "vic")
    await buy(vic, fake_razorpay, "starter")
    public_id = await started(vic, "standard", "now")
    async with db.SessionLocal() as session:
        period = await session.get(PlanPeriod, (await order_row(public_id)).replaces_period_id)
        assert period is not None
        await periods.revoke(session, period, reason="Test", actor="admin", source="admin")
        await session.commit()

    await come_back(client, fake_razorpay.pay(await link_of(public_id)))

    order = await order_row(public_id)
    assert order.status == "review"
    assert order.review_reason == (
        "The plan this payment was priced against has changed. Suggested: refund ₹1,501 or grant instead."
    )


# ---------------- Odd requests ----------------


@pytest.mark.parametrize("body", [b"not json", b'{"no": "event"}', b"[1, 2]"])
async def test_a_signed_body_that_isnt_an_event_is_400(
    client: ApiClient, fake_razorpay: FakeRazorpay, body: bytes
) -> None:
    response = await send_webhook(client, body, {"X-Razorpay-Signature": hmac_hex(fake_razorpay.webhook_secret, body)})

    assert response.status_code == 400 and response.json() == {"detail": "Not a Razorpay event"}


async def test_a_declared_length_over_256_kb_is_refused_before_reading(client: ApiClient) -> None:
    response = await client.post(
        "/api/billing/razorpay/webhook", content=b"{}", headers={"Content-Length": str(256 * 1024 + 1)}
    )

    assert response.status_code == 413


async def test_a_webhook_is_recorded_even_when_the_worker_cant_be_woken(
    client: ApiClient, fake_razorpay: FakeRazorpay, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(event_id: int) -> None:
        raise ConnectionError("Redis is down")

    monkeypatch.setattr(business, "wake", down)
    body, headers = fake_razorpay.webhook("payment_link.expired")

    response = await send_webhook(client, body, headers)

    assert response.status_code == 200
    (event,) = [event for event in await events() if event.razorpay_event_id]
    assert event.id in await webhooks.pending()


async def test_a_return_whose_check_breaks_still_redirects(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    wes = await new_tester(create_user, "wes")
    public_id = await started(wes, "starter")
    query = fake_razorpay.pay(await link_of(public_id))

    async def broken(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(webhooks, "check_order", broken)
    response = await come_back(client, query)

    assert response.headers["location"] == f"http://localhost:8080/billing/return?order={public_id}"
    order = await order_row(public_id)
    assert order.status == "pending" and order.next_check_at is not None and order.next_check_at <= utcnow()


async def test_a_return_that_cant_be_handled_goes_to_the_plan_page(
    client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any) -> str:
        raise RuntimeError("database down")

    monkeypatch.setattr(webhooks, "returned", broken)

    response = await come_back(client, {"razorpay_payment_link_id": "plink_X"})

    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:8080/plans?payment=unknown"
