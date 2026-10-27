"""What a user can buy and starting a checkout (app/services/billing/checkout.py, routers/billing.py;
docs/briefs/plans-billing.md 4.3 to 4.5 and 6.3): options, labels, changes and reasons; amounts and the credit computed
on the server; testers only under test keys; reusing an open checkout; the new link before the old one is cancelled;
the caps; a create that times out or answers an error; the pay_url check; the admin; no keys; refreshes throttled;
checkouts at once and a checkout replaced while its link is made; the credit checked again when an upgrade applies."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from billing_support import (
    CreateUser,
    buy,
    checkout,
    come_back,
    link_of,
    new_tester,
    order_row,
    periods_of,
    plan_id,
    started,
)
from conftest import ApiClient, end_plan
from fake_razorpay import FakeRazorpay
from sqlalchemy import select, update

from app import db
from app.models import BillingEvent, BillingOrder, Document, Plan, UsageEvent, User, utcnow
from app.redis_client import get_redis
from app.services.billing import business, periods, razorpay, reconcile
from app.services.billing import checkout as checkout_service
from app.services.billing.money import format_ist_date, ist_date

pytestmark = pytest.mark.usefixtures("fake_razorpay")


def offer(catalog: dict[str, Any], code: str) -> dict[str, Any]:
    return next(plan for plan in catalog["plans"] if plan["code"] == code)


def option(catalog: dict[str, Any], code: str, kind: str) -> dict[str, Any] | None:
    return next((item for item in offer(catalog, code)["options"] if item["kind"] == kind), None)


async def catalog(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/billing/plans")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------- Options ----------------


async def test_a_new_tester_can_choose_any_plan_starting_now(create_user: CreateUser) -> None:
    alice = await new_tester(create_user, "alice")

    found = await catalog(alice)

    assert [plan["code"] for plan in found["plans"]] == ["starter", "standard", "professional"]
    assert found["payments"]["available"] is True and found["payments"]["mode"] == "test"
    standard = offer(found, "standard")
    assert standard["recommended"] is True and standard["current"] is False
    assert [item["kind"] for item in standard["options"]] == ["period"]
    choose = standard["options"][0]
    assert choose["label"] == "Choose Standard" and choose["unavailable_reason"] is None
    assert choose["amount_paise"] == choose["plan_price_paise"] == 200_000 and choose["credit"] is None
    assert choose["changes"] == [] and choose["notes"] == []
    assert choose["valid_through"] == ist_date(utcnow() + timedelta(days=1)).isoformat()  # 1 day under test keys


async def test_someone_who_isnt_a_tester_cant_pay_with_test_keys(create_user: CreateUser) -> None:
    bob = await create_user("bob", plan=None)

    found = await catalog(bob)
    response = await checkout(bob, "starter")

    assert found["payments"] == {
        "available": False,
        "mode": None,
        "contact_email": None,
        "upi_limit_paise": 200_000,
        "email_reminders": False,
    }
    assert {item["unavailable_reason"] for item in offer(found, "starter")["options"]} == {
        "Online payment isn't set up yet."
    }
    assert response.status_code == 503
    assert response.json() == {"detail": "Online payment isn't set up yet.", "code": "payments_unavailable"}


async def test_the_admin_sees_plans_without_options_and_cant_buy(admin_client: ApiClient) -> None:
    found = await catalog(admin_client)
    response = await checkout(admin_client, "starter")

    assert all(plan["options"] == [] and not plan["current"] for plan in found["plans"])
    assert response.status_code == 403 and response.json()["code"] == "admin_exempt"


async def test_an_open_ended_plan_continues_after_a_paid_period(create_user: CreateUser) -> None:
    carol = await new_tester(create_user, "carol", plan="professional")

    found = await catalog(carol)

    assert offer(found, "professional")["current"] is True
    starter = option(found, "starter", "period")
    assert starter is not None and starter["label"] == "Choose Starter"
    assert starter["notes"] == ["Your complimentary plan continues after it."]
    assert option(found, "professional", "now") is None
    assert starter["changes"] == [
        "Gmail isn't included.",
        "Creating files and images from chat isn't included. Your existing ones stay.",
        "Claude Opus 5.5, Claude Sonnet 5.5 and GPT-5.5 won't be available.",
    ]


async def test_a_switch_lists_documents_over_the_new_limit(create_user: CreateUser) -> None:
    dave = await new_tester(create_user, "dave", plan="professional")
    assert dave.user_id is not None
    async with db.SessionLocal() as session:
        for index in range(51):
            session.add(
                Document(
                    owner_id=dave.user_id,
                    scope="private",
                    filename=f"doc-{index}.txt",
                    stored_name=f"dave-{index}",
                    sha256=f"{index:064d}",
                    status="ready",
                    size_bytes=4 * 1024 * 1024,
                )
            )
        await session.commit()

    starter = option(await catalog(dave), "starter", "period")

    assert starter is not None
    assert starter["changes"][-1] == (
        "You have 51 documents (204 MB); Starter allows 50 (200 MB). You keep them all but can't add more until "
        "you're under the limit."
    )


async def test_options_on_a_current_period(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    erin = await new_tester(create_user, "erin")
    await buy(erin, fake_razorpay, "starter")
    period = (await periods_of(erin.user_id or 0))[0]
    on = format_ist_date(ist_date(period.ends_at))

    found = await catalog(erin)

    assert offer(found, "starter")["current"] is True
    renew = option(found, "starter", "period")
    assert renew is not None and renew["label"] == "Renew"
    assert datetime.fromisoformat(renew["starts_at"]) == period.ends_at
    upgrade = option(found, "standard", "now")
    assert upgrade is not None and upgrade["label"] == "Upgrade now · ₹1,501"
    assert upgrade["amount_paise"] == 150_100 and upgrade["plan_price_paise"] == 200_000
    assert upgrade["credit"]["amount_paise"] == 49_900
    assert upgrade["credit"]["description"].startswith("Credit for unused Starter, ")
    later = option(found, "standard", "period")
    assert later is not None and later["label"] == f"or switch on {on}"
    assert option(found, "starter", "now") is None  # nothing used yet: no new period now


async def test_one_period_ahead_at_most(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    frank = await new_tester(create_user, "frank")
    await buy(frank, fake_razorpay, "starter")
    await buy(frank, fake_razorpay, "starter")

    found = await catalog(frank)
    response = await checkout(frank, "standard")

    assert {item["unavailable_reason"] for item in offer(found, "standard")["options"] if item["kind"] == "period"} == {
        "You already have a period paid ahead."
    }
    upgrade = option(found, "standard", "now")
    assert upgrade is not None and upgrade["unavailable_reason"] is None
    assert upgrade["notes"] == ["The period you paid ahead moves to start after this one."]
    assert response.status_code == 409
    assert response.json() == {"detail": "You already have a period paid ahead.", "code": "checkout_unavailable"}
    assert len(await periods_of(frank.user_id or 0)) == 2


async def test_an_expired_user_renews_their_last_plan(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    gina = await new_tester(create_user, "gina")
    await buy(gina, fake_razorpay, "standard")
    await end_plan(gina.user_id or 0)

    found = await catalog(gina)

    assert option(found, "standard", "period")["label"] == "Renew Standard"  # type: ignore[index]
    assert option(found, "starter", "period")["label"] == "Choose Starter"  # type: ignore[index]


async def test_a_retired_plan_stays_listed_for_its_holder_but_cant_be_bought(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    hana = await new_tester(create_user, "hana")
    other = await new_tester(create_user, "other")
    await buy(hana, fake_razorpay, "professional")
    async with db.SessionLocal() as session:
        await session.execute(update(Plan).where(Plan.code == "professional").values(status="retired"))
        await session.commit()

    mine, theirs = await catalog(hana), await catalog(other)
    response = await checkout(hana, "professional")

    assert option(mine, "professional", "period")["unavailable_reason"] == "This plan is no longer offered."  # type: ignore[index]
    assert offer(mine, "professional")["status"] == "retired"
    assert "professional" not in [plan["code"] for plan in theirs["plans"]]
    assert response.status_code == 409 and response.json()["detail"] == "This plan is no longer offered."


async def test_a_new_period_now_once_an_allowance_is_nearly_used(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    ivy = await new_tester(create_user, "ivy")
    await buy(ivy, fake_razorpay, "starter")
    async with db.SessionLocal() as session:
        session.add(
            UsageEvent(user_id=ivy.user_id, model_name="m", kind="chat", total_tokens=10, allowance_tokens=450_000)
        )
        await session.commit()

    again = option(await catalog(ivy), "starter", "now")

    assert again is not None and again["label"] == "Start a new period now · ₹450"
    assert again["credit"]["amount_paise"] == 5_000  # 10% of the allowance left: ₹50


async def test_a_lower_plan_now_is_refused(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    jay = await new_tester(create_user, "jay")
    await buy(jay, fake_razorpay, "standard")

    response = await checkout(jay, "starter", "now")

    assert response.status_code == 409
    assert response.json()["detail"] == "You're already on this plan or a higher one."


# ---------------- Starting a checkout ----------------


async def test_a_checkout_creates_a_payment_link_from_the_servers_figures(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    kim = await new_tester(create_user, "kim")

    response = await checkout(kim, "standard")

    assert response.status_code == 201, response.text
    body = response.json()
    order = await order_row(body["order"]["public_id"])
    link = fake_razorpay.links[order.razorpay_link_id or ""]
    assert body["pay_url"] == link["short_url"] == order.pay_url and body["pay_url"].startswith("https://rzp.io/")
    assert body["order"]["status"] == "pending" and body["order"]["amount_paise"] == 200_000
    assert body["order"]["test_mode"] is True and body["order"]["pay_url"] == body["pay_url"]
    assert link["amount"] == 200_000 and link["reference_id"] == f"rag-{order.public_id}"
    assert link["callback_url"] == "http://localhost:8080/api/billing/razorpay/return"
    assert link["notes"] == {
        "order": order.public_id,
        "user_id": str(kim.user_id),
        "plan": "standard",
        "kind": "period",
    }
    assert link["expire_by"] - int(order.created_at.timestamp()) == 20 * 60
    assert "customer" not in link and link["description"] == "Standard plan, 1 day"
    assert order.terms_accepted_at is not None and order.razorpay_order_id == link["order_id"]
    assert order.watch_until - order.expires_at == timedelta(days=5)


async def test_the_origin_is_the_requests_when_it_is_one_of_the_apps(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "public_base_urls", "https://rag.example.com,http://localhost:8080")
    lee = await new_tester(create_user, "lee")
    body = {"plan_id": await plan_id("starter"), "kind": "period", "accept_terms": True}

    known = await lee.post("/api/billing/checkout", json=body, headers={"Origin": "http://localhost:8080"})
    await lee.post(f"/api/billing/orders/{known.json()['order']['public_id']}/cancel")
    foreign = await lee.post("/api/billing/checkout", json=body, headers={"Origin": "https://evil.example"})

    assert (await order_row(known.json()["order"]["public_id"])).origin == "http://localhost:8080"
    assert (await order_row(foreign.json()["order"]["public_id"])).origin == "https://rag.example.com"


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "period", "accept_terms": False},
        {"kind": "period"},
        {"kind": "period", "accept_terms": True, "amount_paise": 100},
        {"kind": "forever", "accept_terms": True},
    ],
)
async def test_the_request_is_only_a_plan_a_kind_and_consent(create_user: CreateUser, body: dict[str, Any]) -> None:
    mia = await new_tester(create_user, "mia")

    response = await mia.post("/api/billing/checkout", json={"plan_id": await plan_id("starter"), **body})

    assert response.status_code == 422


async def test_an_unknown_plan_is_404(create_user: CreateUser) -> None:
    ned = await new_tester(create_user, "ned")

    response = await ned.post("/api/billing/checkout", json={"plan_id": 999, "kind": "period", "accept_terms": True})

    assert response.status_code == 404


async def test_an_open_checkout_of_the_same_plan_is_reused(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    olga = await new_tester(create_user, "olga")

    first = await started(olga, "starter")
    second = await checkout(olga, "starter")
    async with db.SessionLocal() as session:
        await session.execute(
            update(BillingOrder)
            .where(BillingOrder.public_id == first)
            .values(expires_at=utcnow() + timedelta(minutes=9))
        )
        await session.commit()
    third = await checkout(olga, "starter")

    assert second.status_code == 201 and second.json()["order"]["public_id"] == first
    assert third.json()["order"]["public_id"] != first  # less than 10 minutes left: a new link
    assert len(fake_razorpay.links) == 2


async def test_the_new_link_comes_before_the_old_one_is_cancelled(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    pia = await new_tester(create_user, "pia")
    first = await started(pia, "starter")
    first_link = await link_of(first)
    fake_razorpay.requests.clear()

    second = await started(pia, "standard")

    second_link = await link_of(second)
    assert fake_razorpay.requests == [
        ("POST", "/v1/payment_links"),
        ("POST", f"/v1/payment_links/{first_link}/cancel"),
    ]
    assert fake_razorpay.links[first_link]["status"] == "cancelled"
    assert fake_razorpay.links[second_link]["status"] == "created"
    old = await order_row(first)
    assert old.status == "cancelled" and old.next_check_at is not None  # still watched for a late payment


async def test_a_refused_create_keeps_the_old_checkout(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    quinn = await new_tester(create_user, "quinn")
    first = await started(quinn, "starter")
    fake_razorpay.fail_next(400, "The amount must be atleast INR 1.00", path="/v1/payment_links")

    response = await checkout(quinn, "standard")

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The payment service didn't answer. Try again in a minute.",
        "code": "payment_provider_error",
    }
    assert (await order_row(first)).status == "pending"
    assert fake_razorpay.links[await link_of(first)]["status"] == "created"
    async with db.SessionLocal() as session:
        failed = await session.scalar(select(BillingEvent).where(BillingEvent.type == "order.create_failed"))
    assert failed is not None and failed.detail["description"] == "The amount must be atleast INR 1.00"
    async with db.SessionLocal() as session:
        order = await session.get(BillingOrder, failed.order_id)
    assert order is not None and order.status == "cancelled" and order.razorpay_link_id is None


async def test_a_timed_out_create_is_found_by_its_reference_and_cancelled(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, monkeypatch: pytest.MonkeyPatch
) -> None:
    rita = await new_tester(create_user, "rita")
    create = razorpay.create_link

    async def lost(body: dict[str, Any]) -> dict[str, Any]:
        await create(body)
        raise razorpay.RazorpayError(None, "UNREACHABLE", "Razorpay unreachable (ReadTimeout)")

    monkeypatch.setattr(razorpay, "create_link", lost)

    response = await checkout(rita, "starter")

    assert response.status_code == 502
    (link,) = fake_razorpay.links.values()
    assert link["status"] == "cancelled"
    order = await order_row(link["notes"]["order"])
    assert order.status == "cancelled" and order.razorpay_link_id == link["id"]
    assert order.last_error == "Razorpay unreachable"


async def test_a_server_error_with_no_link_made_is_502(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    sam = await new_tester(create_user, "sam")
    fake_razorpay.fail_next(500, path="/v1/payment_links")

    response = await checkout(sam, "starter")

    assert response.status_code == 502
    assert ("GET", "/v1/payment_links") in fake_razorpay.requests


async def test_a_pay_url_that_isnt_razorpays_is_refused(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, monkeypatch: pytest.MonkeyPatch
) -> None:
    tom = await new_tester(create_user, "tom")
    create = fake_razorpay.create_link

    def elsewhere(body: dict[str, Any]) -> dict[str, Any]:
        link = create(body)
        fake_razorpay.links[link["id"]]["short_url"] = "https://pay.evil.example/i/x"
        return {**link, "short_url": "https://pay.evil.example/i/x"}

    monkeypatch.setattr(fake_razorpay, "create_link", elsewhere)

    response = await checkout(tom, "starter")

    assert response.status_code == 502
    (link,) = fake_razorpay.links.values()
    assert link["status"] == "cancelled"


async def test_rejected_keys_stop_the_payment_checks(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    uma = await new_tester(create_user, "uma")
    fake_razorpay.fail_next(401, "Authentication failed", path="/v1/payment_links")

    response = await checkout(uma, "starter")

    assert response.status_code == 502
    assert await business.keys_rejected() == "Razorpay rejected the API keys: Authentication failed"


async def test_without_keys_nobody_can_pay(create_user: CreateUser, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "razorpay_key_id", "")
    vic = await new_tester(create_user, "vic")
    async with db.SessionLocal() as session:
        await business.save_settings(
            session,
            (await business.get_settings(session)).model_copy(update={"contact_email": "billing@example.com"}),
            actor="admin",
        )
        await session.commit()

    response = await checkout(vic, "starter")

    assert response.status_code == 503
    assert response.json()["detail"] == "Online payment isn't set up yet. Contact billing@example.com."


# ---------------- Caps ----------------


async def test_five_links_an_hour_counted_only_when_one_is_made(create_user: CreateUser) -> None:
    wes = await new_tester(create_user, "wes")
    for code in ("starter", "standard", "professional", "starter"):
        await started(wes, code)
    assert (await checkout(wes, "starter")).status_code == 201  # reused: not counted
    assert (await checkout(wes, "professional", "now")).status_code == 409  # refused: not counted
    await started(wes, "standard")

    response = await checkout(wes, "professional")

    assert response.status_code == 429
    assert response.json()["detail"] == "Too many payment attempts. Try again in an hour."
    assert response.json()["code"] == "too_many_checkouts"
    assert 3500 <= int(response.headers["retry-after"]) <= 3600


async def test_fifteen_links_an_ist_day(create_user: CreateUser) -> None:
    xia = await new_tester(create_user, "xia")
    await get_redis().set(f"rl:checkout-day:{xia.user_id}:{ist_date(utcnow()).isoformat()}", "15")

    response = await checkout(xia, "starter")

    assert response.status_code == 429
    assert response.json()["detail"] == "Too many payment attempts. Try again tomorrow."
    assert 0 < int(response.headers["retry-after"]) <= 86_400


# ---------------- Orders ----------------


async def test_cancelling_a_checkout(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    yan = await new_tester(create_user, "yan")
    zed = await new_tester(create_user, "zed")
    public_id = await started(yan, "starter")

    foreign = await zed.post(f"/api/billing/orders/{public_id}/cancel")
    cancelled = await yan.post(f"/api/billing/orders/{public_id}/cancel")
    again = await yan.post(f"/api/billing/orders/{public_id}/cancel")

    assert foreign.status_code == 404
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["pay_url"] is None
    assert fake_razorpay.links[await link_of(public_id)]["status"] == "cancelled"
    assert again.status_code == 409


async def test_another_users_order_is_404(create_user: CreateUser) -> None:
    amy = await new_tester(create_user, "amy")
    ben = await new_tester(create_user, "ben")
    public_id = await started(amy, "starter")

    assert (await ben.get(f"/api/billing/orders/{public_id}")).status_code == 404
    assert (await amy.get(f"/api/billing/orders/{public_id}")).status_code == 200
    assert (await amy.get("/api/billing/orders/not..valid")).status_code == 422


async def test_refresh_checks_with_razorpay_when_the_return_was_lost(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, client: ApiClient
) -> None:
    cal = await new_tester(create_user, "cal")
    public_id = await started(cal, "starter")
    fake_razorpay.pay(await link_of(public_id))

    plain = (await cal.get(f"/api/billing/orders/{public_id}")).json()
    refreshed = (await cal.get(f"/api/billing/orders/{public_id}", params={"refresh": "true"})).json()

    assert plain["status"] == "pending"
    assert refreshed["status"] == "paid" and refreshed["method"] == "upi"
    assert refreshed["receipt"]["number"].startswith("TEST/") and refreshed["valid_through"] is not None


async def test_a_deleted_users_links_are_cancelled(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    dan = await new_tester(create_user, "dan")
    public_id = await started(dan, "starter")
    link = await link_of(public_id)
    async with db.SessionLocal() as session:
        user = await session.get(User, dan.user_id)
        assert user is not None
        await periods.on_user_deleted(session, user)
        await session.commit()
    await asyncio.gather(*checkout_service._background)

    order = await order_row(public_id)
    assert order.status == "cancelled" and order.next_check_at is not None
    assert fake_razorpay.links[link]["status"] == "cancelled"


async def test_refreshes_of_an_order_are_throttled(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    lou = await new_tester(create_user, "lou")
    public_id = await started(lou, "starter")
    order = await order_row(public_id)
    fetch = ("GET", f"/v1/payment_links/{order.razorpay_link_id}")
    path = f"/api/billing/orders/{public_id}"

    first = await lou.get(path, params={"refresh": "true"})
    second = await lou.get(path, params={"refresh": "true"})
    assert first.status_code == second.status_code == 200
    assert fake_razorpay.requests.count(fetch) == 1  # the second answered from the database
    for _ in range(40):
        await get_redis().delete(f"billing:refresh-recent:{order.id}")
        assert (await lou.get(path, params={"refresh": "true"})).status_code == 200

    assert fake_razorpay.requests.count(fetch) == reconcile.REFRESH_BUDGET


# ---------------- Races ----------------


async def test_checkouts_at_once_keep_to_the_caps_and_leave_one_live_link(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, monkeypatch: pytest.MonkeyPatch
) -> None:
    max_ = await new_tester(create_user, "max")
    create = razorpay.create_link

    async def slow(body: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return await create(body)

    monkeypatch.setattr(razorpay, "create_link", slow)

    responses = await asyncio.gather(
        *(checkout(max_, code) for code in ("starter", "standard", "professional") * 4)
    )

    assert {response.status_code for response in responses} <= {201, 409, 429}
    assert len(fake_razorpay.links) <= checkout_service.HOUR_CAP
    live = [link for link in fake_razorpay.links.values() if link["status"] == "created"]
    async with db.SessionLocal() as session:
        pending = list(
            await session.scalars(
                select(BillingOrder).where(BillingOrder.user_id == max_.user_id, BillingOrder.status == "pending")
            )
        )
    assert [link["id"] for link in live] == [order.razorpay_link_id for order in pending]
    answered = {response.json()["pay_url"] for response in responses if response.status_code == 201}
    assert {link["short_url"] for link in live} <= answered


async def test_a_checkout_cancelled_while_its_link_is_made_cancels_the_link(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, monkeypatch: pytest.MonkeyPatch
) -> None:
    ned = await new_tester(create_user, "ned")
    create = razorpay.create_link
    later: list[httpx.Response] = []

    async def overtaken(body: dict[str, Any]) -> dict[str, Any]:
        monkeypatch.setattr(razorpay, "create_link", create)
        later.append(await checkout(ned, "standard"))  # another tab, done first
        return await create(body)

    monkeypatch.setattr(razorpay, "create_link", overtaken)

    response = await checkout(ned, "starter")

    assert response.status_code == 409 and response.json()["code"] == "checkout_unavailable"
    assert later[0].status_code == 201
    first, second = sorted(fake_razorpay.links.values(), key=lambda link: link["description"])
    assert (first["description"], first["status"]) == ("Standard plan, 1 day", "created")
    assert (second["description"], second["status"]) == ("Starter plan, 1 day", "cancelled")
    async with db.SessionLocal() as session:
        orders = list(await session.scalars(select(BillingOrder).order_by(BillingOrder.id)))
    assert [(order.status, order.pay_url is not None) for order in orders] == [("cancelled", False), ("pending", True)]


async def test_the_reconciler_cancels_a_live_link_of_a_cancelled_order(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    ola = await new_tester(create_user, "ola")
    public_id = await started(ola, "starter")
    link = await link_of(public_id)
    fake_razorpay.fail_next(500, path=f"/v1/payment_links/{link}/cancel")
    assert (await ola.post(f"/api/billing/orders/{public_id}/cancel")).status_code == 200
    assert fake_razorpay.links[link]["status"] == "created"

    async with db.SessionLocal() as session:
        await reconcile.check_order(session, (await order_row(public_id)).id, source="reconcile")

    assert fake_razorpay.links[link]["status"] == "cancelled"


async def test_an_upgrade_paid_after_the_allowance_was_used_goes_to_review(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    kai = await new_tester(create_user, "kai")
    await buy(kai, fake_razorpay, "starter")
    public_id = await started(kai, "professional", "now")
    async with db.SessionLocal() as session:
        session.add(
            UsageEvent(user_id=kai.user_id, model_name="m", kind="chat", total_tokens=10, allowance_tokens=500_000)
        )
        await session.commit()

    response = await come_back(kai, fake_razorpay.pay(await link_of(public_id)))

    assert response.status_code == 303
    order = await order_row(public_id)
    assert order.credit_paise == 49_900 and order.status == "review"
    assert order.review_reason == (
        "Usage or a refund since the quote lowered the credit to ₹0 (quoted ₹499). Suggested: refund ₹4,501 or grant "
        "instead."
    )
    (starter,) = await periods_of(kai.user_id or 0)
    assert starter.ends_at > utcnow() and starter.history == []
