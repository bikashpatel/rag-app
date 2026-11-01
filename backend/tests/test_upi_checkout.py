"""Starting a direct-UPI order (services/billing/upi.py start; docs/briefs/upi-direct.md 8.1, 8.6, D2, D7 and D12):
options buyable with direct UPI alone, no email needed, the order's fields, unavailable without the settings, an open
order reused, the day's caps, the unpaid users' cap on UTRs waiting (orders without one hold no place; the billing
check's warning) and their network's cap, a pause after three rejections, a pending checkout of either channel replaced
(a Razorpay link cancelled after the commit), no checkout of either channel while a UTR waits, the admin, a tester
under test keys (no credit from a test-mode period), and the errors of the request."""

from datetime import timedelta
from typing import Any

import pytest
from billing_support import buy, checkout, events, give_email, link_of, make_tester, new_tester, order_row, plan_id
from conftest import ApiClient, grant_plan
from fake_razorpay import FakeRazorpay
from sqlalchemy import func, select, update
from upi_support import (
    CONTACT,
    PAYEE,
    UPI_ID,
    CreateUser,
    change_order,
    confirm,
    make_paid,
    order_of,
    reject,
    send_utr,
    set_upi,
    submitted,
    upi_checkout,
    upi_order,
)

from app import db
from app.models import BillingOrder, utcnow
from app.services.auth import throttle
from app.services.auth.throttle import Limit, Window
from app.services.billing import summary, upi
from app.services.billing.money import IST, ist_date


async def catalog(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/billing/plans")
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def period_option(found: dict[str, Any], code: str) -> dict[str, Any]:
    plan = next(plan for plan in found["plans"] if plan["code"] == code)
    return next(item for item in plan["options"] if item["kind"] == "period")


async def options(client: ApiClient, code: str) -> list[dict[str, Any]]:
    plan = next(plan for plan in (await catalog(client))["plans"] if plan["code"] == code)
    found: list[dict[str, Any]] = plan["options"]
    return found


async def test_direct_upi_alone_makes_the_plans_buyable(create_user: CreateUser) -> None:
    rita = await create_user("smoke-rita", plan=None)
    closed = await catalog(rita)
    await set_upi()

    found = await catalog(rita)

    assert period_option(closed, "standard")["unavailable_reason"] == "Paid plans open soon. Check back in a few days."
    assert closed["payments"]["upi_direct"] is False
    payments = found["payments"]
    assert (payments["available"], payments["mode"], payments["upi_direct"]) == (False, None, True)
    choose = period_option(found, "standard")
    assert choose["unavailable_reason"] is None and choose["amount_paise"] == 200_000
    assert choose["valid_through"] == ist_date(utcnow() + timedelta(days=30)).isoformat()


async def test_upi_is_unavailable_until_switched_on(create_user: CreateUser) -> None:
    sam = await create_user("smoke-sam", plan=None)
    never = await upi_checkout(sam, "starter")
    await set_upi(enabled=False)

    off = await upi_checkout(sam, "starter")

    assert never.status_code == off.status_code == 503
    assert never.json() == {"detail": "Paying by UPI isn't available right now.", "code": "upi_unavailable"}
    assert off.json()["detail"] == f"Paying by UPI isn't available right now. Contact {CONTACT}."


async def test_an_order_needs_no_email_and_waits_for_its_utr(create_user: CreateUser) -> None:
    await set_upi()
    tara = await create_user("smoke-tara", plan=None)

    response = await upi_checkout(tara, "standard", origin="http://localhost:8080")

    assert response.status_code == 201, response.text
    order = response.json()
    assert (order["status"], order["channel"], order["amount_paise"], order["pay_url"]) == (
        "pending",
        "upi_direct",
        200_000,
        None,
    )
    info = order["upi"]
    assert (info["upi_id"], info["payee_name"], info["utr"], info["rejection"]) == (UPI_ID, PAYEE, None, None)
    assert info["uri"] == f"upi://pay?pa={UPI_ID}&pn=Asha%20Rao&am=2000.00&cu=INR&tn={info['reference']}"
    row = await order_of(order["public_id"])
    assert (row.mode, row.upi_reference, row.next_check_at, row.origin) == (
        "offline",
        info["reference"],
        None,
        "http://localhost:8080",
    )
    assert row.expires_at - row.created_at == timedelta(hours=24) and row.watch_until == row.expires_at
    assert row.terms_accepted_at == row.created_at
    (created,) = await events("order.created")
    assert created.detail == {
        "public_id": order["public_id"],
        "channel": "upi_direct",
        "reference": info["reference"],
        "plan": "standard",
        "kind": "period",
        "amount": 200_000,
    }
    me = (await tara.get("/api/auth/me")).json()["plan"]["pending_order"]
    assert (me["public_id"], me["channel"], me["status"], me["pay_url"]) == (
        order["public_id"],
        "upi_direct",
        "pending",
        None,
    )


async def test_an_open_order_is_reused_while_an_hour_is_left(create_user: CreateUser) -> None:
    await set_upi()
    uma = await create_user("smoke-uma", plan=None)
    first = await upi_order(uma, "starter")

    again = await upi_order(uma, "starter")
    await change_order(first["public_id"], expires_at=utcnow() + timedelta(minutes=59))
    fresh = await upi_order(uma, "starter")
    await set_upi(upi_id="other.id@ybl")
    moved = await upi_order(uma, "starter")

    assert again["public_id"] == first["public_id"]
    assert len({first["public_id"], fresh["public_id"], moved["public_id"]}) == 3
    assert (await order_of(first["public_id"])).status == "cancelled"
    assert (await order_of(fresh["public_id"])).status == "cancelled"
    assert moved["upi"]["upi_id"] == "other.id@ybl"
    cancelled = await events("order.cancelled")
    assert [event.detail["replaced_by"] for event in cancelled] == [fresh["public_id"], moved["public_id"]]


async def test_an_unpaid_user_makes_three_orders_a_day_and_a_paid_one_five(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    vera = await create_user("smoke-vera", plan=None)
    will = await create_user("smoke-will", plan=None)
    assert will.user_id is not None
    await make_paid(admin_client, will.user_id)

    unpaid = [await upi_checkout(vera, code) for code in ("starter", "standard", "starter", "standard")]
    paid = [await upi_checkout(will, code) for code in ("starter", "standard") * 3]

    assert [response.status_code for response in unpaid] == [201, 201, 201, 429]
    assert [response.status_code for response in paid] == [201] * 5 + [429]
    refused = unpaid[-1]
    assert refused.json() == {
        "detail": "Too many payment requests today. Try again tomorrow.",
        "code": "too_many_checkouts",
    }
    now = utcnow().astimezone(IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    assert abs(int(refused.headers["retry-after"]) - (midnight - now).total_seconds()) <= 5


async def test_yesterdays_orders_dont_count(create_user: CreateUser) -> None:
    await set_upi()
    xia = await create_user("smoke-xia", plan=None)
    for code in ("starter", "standard", "starter"):
        await upi_order(xia, code)
    async with db.SessionLocal() as session:
        await session.execute(update(BillingOrder).values(created_at=utcnow() - timedelta(days=1)))
        await session.commit()

    assert (await upi_checkout(xia, "standard")).status_code == 201


async def test_unpaid_users_together_have_a_limited_number_of_utrs_waiting(
    create_user: CreateUser, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch, fake_razorpay: FakeRazorpay
) -> None:
    await set_upi()
    monkeypatch.setattr(upi, "UNPAID_WAITING_CAP", 2)
    idle = [await create_user(f"smoke-idle-{index}", plan=None) for index in range(3)]
    first, second, third = [await create_user(f"smoke-open-{index}", plan=None) for index in range(3)]
    payer = await create_user("smoke-payer", plan=None)
    assert payer.user_id is not None and third.user_id is not None
    await make_paid(admin_client, payer.user_id)
    for client in idle:  # orders that got no UTR hold no place
        await upi_order(client, "starter")
    waiting = await submitted(first, "starter")
    await submitted(second, "starter", utr="630500000002")
    assert not await upi.unpaid_cap_reached()

    busy = await upi_checkout(third, "starter")
    allowed = await upi_checkout(payer, "starter")
    await make_tester(third.user_id)
    online = await upi_checkout(third, "starter")

    assert busy.status_code == 503 and busy.headers["retry-after"] == "3600"
    assert busy.json() == {
        "detail": "Many UPI payments are waiting to be checked. Try again in a few hours.",
        "code": "checkout_busy",
    }
    assert online.json()["detail"].endswith(" Or pay online.")
    assert allowed.status_code == 201  # a user who has paid isn't held by the cap
    assert await upi.unpaid_cap_reached()
    assert (
        "UPI payments of unpaid users were refused in the last hour: 2 of their UTRs waited for your check"
        in await summary.problems()
    )
    assert (await reject(admin_client, waiting)).status_code == 200  # a checked UTR frees its place
    assert (await upi_checkout(third, "starter")).status_code == 201


async def test_unpaid_accounts_of_one_network_make_a_limited_number_of_orders(
    create_user: CreateUser, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await set_upi()
    monkeypatch.setitem(throttle.RULES, "upi_unpaid", Limit(True, (Window(3600, 2),)))
    first, second, third = [await create_user(f"smoke-net-{index}", plan=None) for index in range(3)]
    payer = await create_user("smoke-net-payer", plan=None)
    assert payer.user_id is not None
    await make_paid(admin_client, payer.user_id)

    made = [await upi_checkout(client, "starter") for client in (first, second)]
    reused = await upi_checkout(first, "starter")
    refused = await upi_checkout(third, "starter")
    allowed = await upi_checkout(payer, "starter")

    assert [response.status_code for response in made] == [201, 201]
    assert reused.json()["public_id"] == made[0].json()["public_id"]  # a reused order isn't counted again
    assert refused.status_code == 429
    assert refused.json()["code"] == "too_many_attempts" and int(refused.headers["retry-after"]) > 0
    assert allowed.status_code == 201  # a user who has paid isn't limited by the network
    async with db.SessionLocal() as session:
        assert await session.scalar(select(func.count()).where(BillingOrder.username == "smoke-net-2")) == 0


async def test_three_rejections_in_thirty_days_pause_direct_upi(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    await set_upi()
    yan = await create_user("smoke-yan", plan=None)
    assert yan.user_id is not None
    await make_paid(admin_client, yan.user_id)  # the day's cap of an unpaid user would refuse the fourth order
    rejected = []
    for index, code in enumerate(("starter", "standard", "starter")):
        public_id = await submitted(yan, code, utr=f"63051234567{index}")
        assert (await reject(admin_client, public_id)).status_code == 200
        rejected.append(public_id)

    paused = await upi_checkout(yan, "standard")
    await change_order(rejected[0], upi_decided_at=utcnow() - timedelta(days=30, minutes=1))
    lifted = await upi_checkout(yan, "standard")

    assert paused.status_code == 409
    assert paused.json() == {
        "detail": "Paying by UPI is paused for your account because recent payments couldn't be confirmed. Write to "
        f"{CONTACT}.",
        "code": "upi_paused",
    }
    assert lifted.status_code == 201


async def test_a_pending_checkout_of_either_channel_is_replaced(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    await set_upi()
    zoe = await create_user("smoke-zoe", plan=None)
    assert zoe.user_id is not None
    await make_tester(zoe.user_id)
    await give_email(zoe.user_id, "smoke-zoe@account.example.test")
    online = (await checkout(zoe, "starter")).json()["order"]["public_id"]
    link = await link_of(online)

    by_upi = await upi_order(zoe, "standard")
    by_link = await checkout(zoe, "professional")

    assert fake_razorpay.links[link]["status"] == "cancelled"  # after the commit
    assert (await order_row(online)).status == "cancelled"
    assert by_link.status_code == 201
    assert (await order_of(by_upi["public_id"])).status == "cancelled"
    replaced = [event.detail for event in await events("order.cancelled")]
    assert replaced == [
        {"public_id": online, "replaced_by": by_upi["public_id"]},
        {"public_id": by_upi["public_id"], "replaced_by": by_link.json()["order"]["public_id"]},
    ]


async def test_no_checkout_starts_while_a_utr_waits(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    await set_upi()
    amy = await create_user("smoke-amy", plan=None)
    assert amy.user_id is not None
    await make_tester(amy.user_id)
    await give_email(amy.user_id, "smoke-amy@account.example.test")
    public_id = await submitted(amy, "standard")
    reference = (await order_of(public_id)).upi_reference

    by_upi = await upi_checkout(amy, "starter")
    by_link = await checkout(amy, "starter")
    cancel = await amy.post(f"/api/billing/orders/{public_id}/cancel")

    expected = {
        "detail": f"We're still checking your UPI payment of ₹2,000 ({reference}). You can start another payment once "
        "it's confirmed or rejected.",
        "code": "upi_waiting",
    }
    assert (by_upi.status_code, by_upi.json()) == (409, expected)
    assert (by_link.status_code, by_link.json()) == (409, expected)
    assert cancel.status_code == 409
    assert (await order_of(public_id)).status == "submitted"


async def test_the_admin_and_unavailable_options_are_refused(create_user: CreateUser, admin_client: ApiClient) -> None:
    await set_upi()
    ben = await create_user("smoke-ben", plan=None)
    assert ben.user_id is not None
    await grant_plan(ben.user_id, "starter", days=30)
    await grant_plan(ben.user_id, "starter", days=30, start="after_current")

    admin = await upi_checkout(admin_client, "starter")
    ahead = await upi_checkout(ben, "starter")
    unknown = await ben.post(
        "/api/billing/upi/checkout", json={"plan_id": 999_999, "kind": "period", "accept_terms": True}
    )
    terms = await ben.post(
        "/api/billing/upi/checkout", json={"plan_id": await plan_id("starter"), "kind": "period", "accept_terms": False}
    )

    assert admin.status_code == 403 and admin.json()["code"] == "admin_exempt"
    assert ahead.status_code == 409
    assert ahead.json() == {"detail": "You already have a period paid ahead.", "code": "checkout_unavailable"}
    assert unknown.status_code == 404
    assert terms.status_code == 422


async def test_a_tester_under_test_keys_is_quoted_one_day_but_buys_thirty(
    create_user: CreateUser, admin_client: ApiClient, fake_razorpay: FakeRazorpay
) -> None:
    await set_upi()
    cal = await create_user("smoke-cal", plan=None)
    assert cal.user_id is not None
    await make_tester(cal.user_id)
    quoted = period_option(await catalog(cal), "starter")
    public_id = await submitted(cal, "starter")

    confirmed = await confirm(admin_client, public_id)

    assert quoted["valid_through"] == ist_date(utcnow() + timedelta(days=1)).isoformat()
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "paid" and body["test_mode"] is False
    assert body["valid_through"] == ist_date(utcnow() + timedelta(days=30)).isoformat()


async def test_a_period_paid_in_test_mode_gives_no_credit_towards_a_upi_upgrade(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    await set_upi()
    dee = await new_tester(create_user, "smoke-dee")
    await buy(dee, fake_razorpay, "standard")
    quoted = next(item for item in await options(dee, "professional") if item["kind"] == "now")
    assert quoted["credit"] is not None

    upgrade = await upi_checkout(dee, "professional", kind="now")
    later = await upi_checkout(dee, "professional")

    assert upgrade.status_code == 409
    assert upgrade.json() == {
        "detail": "Your current period was paid in test mode, so it gives no credit towards a UPI payment. Pay online "
        "instead, or choose the plan to start after this period.",
        "code": "checkout_unavailable",
    }
    assert later.status_code == 201 and later.json()["amount_paise"] == 500_000


async def test_an_order_and_its_status_through_the_user_endpoints(create_user: CreateUser) -> None:
    await set_upi()
    dev = await create_user("smoke-dev", plan=None)
    order = await upi_order(dev, "starter")

    status = (await dev.get("/api/billing/status")).json()
    response = await send_utr(dev, order["public_id"])
    waiting = (await dev.get("/api/billing/status")).json()

    assert status["open_order"]["public_id"] == order["public_id"] and status["payments"]["upi_direct"] is True
    assert response.status_code == 200
    assert waiting["open_order"]["status"] == "submitted"
    assert waiting["plan"]["pending_order"]["status"] == "submitted"
