"""Admin → Billing and a user's plan (docs/briefs/plans-billing.md 4.8, 4.9, 7.2 and 8.11; routers/admin_billing.py,
services/billing/summary.py and csv_export.py): dated and open-ended grants and their end, changing the plan, revoking
a period, bank transfers, the billing settings and setup, orders and payments lists, resolving a review, the month's
summary with the expiring and lapsed lists, "Needs attention" (duplicates only from checkouts open at once), and the
CSV export."""

import csv
import io
from datetime import timedelta
from typing import Any

import pytest
from billing_support import CreateUser, buy, come_back, events, link_of, new_tester, order_row, periods_of, started
from conftest import ApiClient, end_plan
from fake_razorpay import FakeRazorpay
from sqlalchemy import update

from app import db
from app.config import settings
from app.models import BillingOrder, BillingPayment, PlanPeriod, utcnow
from app.schemas_billing import AttentionItem
from app.services.billing import summary
from app.services.billing.money import ist_date


async def plan_ids(admin: ApiClient) -> dict[str, int]:
    return {plan["code"]: plan["id"] for plan in (await admin.get("/api/admin/plans")).json()}


async def grant(admin: ApiClient, user_id: int, **body: Any) -> Any:
    ids = await plan_ids(admin)
    payload = {"plan_id": ids["standard"], "days": 45, "reason": "Trial", "start": "now", **body}
    return await admin.post(f"/api/admin/billing/users/{user_id}/grants", json=payload)


# ---------------- A user's plan ----------------


async def test_a_dated_grant_is_rows_of_at_most_30_days(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice", plan=None)

    response = await grant(admin_client, alice.user_id or 0)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["state"] == "active" and body["plan"]["complimentary"] is True
    assert [(item["source"], item["plan_name"]) for item in body["periods"]] == [
        ("comp", "Standard"),
        ("comp", "Standard"),
    ]
    first, second = await periods_of(alice.user_id or 0)
    assert (first.days, second.days) == (30, 15) and second.starts_at == first.ends_at
    assert second.snapshot["limits"]["monthly_token_limit"] == 1_000_000  # half of 2,000,000 for 15 days
    assert first.granted_by == "admin" and first.reason == "Trial"
    assert [event["type"] for event in body["events"]][:1] == ["period.granted"]


async def test_grants_that_cant_be_made(admin_client: ApiClient, create_user: CreateUser) -> None:
    bob = await create_user("bob", plan=None)
    await grant(admin_client, bob.user_id or 0, days=10)

    now_again = await grant(admin_client, bob.user_id or 0, days=10)
    too_long = await grant(admin_client, bob.user_id or 0, days=91)
    admin = await grant(admin_client, 1)
    unknown = await grant(admin_client, 999)
    unknown_plan = await grant(admin_client, bob.user_id or 0, plan_id=999)
    after = await grant(admin_client, bob.user_id or 0, days=10, start="after_current")

    assert now_again.status_code == 409 and too_long.status_code == 422
    assert admin.status_code == 404 and unknown.status_code == 404 and unknown_plan.status_code == 404
    assert after.status_code == 200
    first, second = await periods_of(bob.user_id or 0)
    assert second.starts_at == first.ends_at


async def test_an_open_ended_plan_and_its_end(admin_client: ApiClient, create_user: CreateUser) -> None:
    carol = await create_user("carol", plan=None)

    granted = await grant(admin_client, carol.user_id or 0, days=None, reason="Family")
    ended = await admin_client.post(
        f"/api/admin/billing/users/{carol.user_id}/end-complimentary", json={"reason": "No longer"}
    )
    again = await admin_client.post(
        f"/api/admin/billing/users/{carol.user_id}/end-complimentary", json={"reason": "No longer"}
    )

    complimentary = granted.json()["complimentary"]
    assert complimentary["plan"]["code"] == "standard" and complimentary["reason"] == "Family"
    assert complimentary["granted_by"] == "admin" and granted.json()["plan"]["open_ended"] is True
    assert ended.json()["complimentary"] is None and ended.json()["plan"]["state"] == "none"
    assert again.status_code == 409
    assert [event.type for event in await events() if event.user_id == carol.user_id][-2:] == [
        "complimentary.granted",
        "complimentary.ended",
    ]


async def test_change_plan_and_revoke(admin_client: ApiClient, create_user: CreateUser) -> None:
    dina = await create_user("dina", plan=None)
    ids = await plan_ids(admin_client)
    nothing = await admin_client.post(
        f"/api/admin/billing/users/{dina.user_id}/change-plan", json={"plan_id": ids["starter"], "reason": "x"}
    )
    await grant(admin_client, dina.user_id or 0, days=30)

    changed = await admin_client.post(
        f"/api/admin/billing/users/{dina.user_id}/change-plan",
        json={"plan_id": ids["professional"], "reason": "Goodwill"},
    )
    period_id = changed.json()["periods"][0]["id"]
    revoked = await admin_client.post(f"/api/admin/billing/periods/{period_id}/revoke", json={"reason": "Mistake"})
    missing = await admin_client.post("/api/admin/billing/periods/999/revoke", json={"reason": "Mistake"})

    assert nothing.status_code == 409
    assert changed.json()["plan"]["plan"]["code"] == "professional"
    assert revoked.json()["plan"]["state"] == "expired" and missing.status_code == 404
    (period,) = await periods_of(dina.user_id or 0)
    assert period.history[-1]["kind"] == "change" and period.revoked_reason == "Mistake"


async def test_a_users_billing_page(admin_client: ApiClient, create_user: CreateUser) -> None:
    eve = await create_user("eve", plan=None)
    await eve.put("/api/billing/profile", json={"name": " Eve E ", "email": ""})

    body = (await admin_client.get(f"/api/admin/billing/users/{eve.user_id}")).json()

    assert body["plan"]["state"] == "none" and body["periods"] == [] and body["orders"] == []
    assert body["profile"] == {"name": "Eve E", "email": None}
    assert (await eve.get(f"/api/admin/billing/users/{eve.user_id}")).status_code == 403


# ---------------- Bank transfers ----------------


async def offline(admin: ApiClient, user_id: int, **body: Any) -> Any:
    ids = await plan_ids(admin)
    payload = {
        "user_id": user_id,
        "plan_id": ids["standard"],
        "amount_paise": 200_000,
        "received_on": "2026-10-24",
        "reference": "UTR123",
        "note": None,
        "apply": True,
        **body,
    }
    return await admin.post("/api/admin/billing/offline-payments", json=payload)


async def test_a_bank_transfer_buys_a_period_with_a_receipt(admin_client: ApiClient, create_user: CreateUser) -> None:
    fred = await create_user("fred", plan=None)

    response = await offline(admin_client, fred.user_id or 0)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["method"] == "bank_transfer" and body["mode"] == "offline" and body["status"] == "captured"
    assert body["offline_reference"] == "UTR123" and body["receipt"]["number"].startswith("RA/")
    assert body["description"] == "Standard plan, 30 days (bank transfer, ref UTR123)"
    assert body["period_start"] is not None and body["refundable_paise"] == 200_000
    (period,) = await periods_of(fred.user_id or 0)
    assert period.mode == "offline" and period.charge_basis_paise == 200_000 and period.days == 30
    (recorded,) = await events("payment.offline_recorded")
    assert recorded.actor == "admin" and recorded.detail["reference"] == "UTR123"


async def test_bank_transfer_rules(admin_client: ApiClient, create_user: CreateUser) -> None:
    gina = await create_user("gina", plan=None)

    below = await offline(admin_client, gina.user_id or 0, amount_paise=150_000)
    noted = await offline(admin_client, gina.user_id or 0, amount_paise=150_000, note="Discount agreed")
    receipt_only = await offline(admin_client, gina.user_id or 0, apply=False)
    too_small = await offline(admin_client, gina.user_id or 0, amount_paise=99)
    admin = await offline(admin_client, 1)

    assert below.status_code == 422
    assert below.json()["detail"] == "Add a note: ₹1,500 is below the plan's price of ₹2,000."
    assert noted.status_code == 201 and receipt_only.status_code == 201
    assert receipt_only.json()["period_start"] is None and receipt_only.json()["receipt"] is not None
    assert too_small.status_code == 422 and admin.status_code == 404
    assert len(await periods_of(gina.user_id or 0)) == 1


# ---------------- Settings and setup ----------------


async def test_billing_settings_and_the_payments_setup(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_AbCdEfGh1234")
    monkeypatch.setattr(settings, "razorpay_key_secret", "secret-never-shown")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "whsec-never-shown")

    config = (await admin_client.get("/api/admin/billing/config")).json()
    bad = await admin_client.put("/api/admin/billing/settings", json={**config["settings"], "receipt_prefix": "TEST"})
    saved = await admin_client.put(
        "/api/admin/billing/settings",
        json={**config["settings"], "contact_email": "billing@example.com", "upi_limit_paise": 0},
    )

    assert config["settings"] == {
        "business_name": "AskNuvexusDoc",
        "address": "",
        "contact_email": None,
        "contact_phone": None,
        "receipt_prefix": "RA",
        "upi_limit_paise": 200_000,
    }
    payments = config["payments"]
    assert payments["configured"] is True and payments["mode"] == "test"
    assert payments["key_id_hint"] == "rzp_test_…1234" and payments["webhook_secret_set"] is True
    assert payments["webhook_url"] == "http://localhost:8080/api/billing/razorpay/webhook"
    assert len(payments["webhook_events"]) == 14 and payments["email_reminders"] is False
    assert config["estimate"] == {
        "usd_inr": 88.0,
        "standard_usd_per_mtok": pytest.approx(3.2),
        "vision_usd_per_mtok": 2.34,
        "image_usd": 0.042,
        "fee_bp": 236,
    }
    assert "never-shown" not in str(config)
    assert bad.status_code == 422
    assert saved.json()["settings"]["contact_email"] == "billing@example.com"
    (changed,) = await events("settings.updated")
    assert set(changed.detail["changed"]) == {"contact_email", "upi_limit_paise"}


# ---------------- Orders and payments ----------------


@pytest.mark.usefixtures("fake_razorpay")
async def test_orders_and_payments_lists(
    admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    hal = await new_tester(create_user, "hal")
    ivy = await new_tester(create_user, "ivy")
    paid = await buy(hal, fake_razorpay, "starter")
    pending = await started(ivy, "standard")

    everything = (await admin_client.get("/api/admin/billing/orders")).json()
    waiting = (await admin_client.get("/api/admin/billing/orders", params={"status": "pending"})).json()
    hals = (await admin_client.get("/api/admin/billing/orders", params={"user_id": hal.user_id})).json()
    detail = (await admin_client.get(f"/api/admin/billing/orders/{paid}")).json()
    checked = await admin_client.post(f"/api/admin/billing/orders/{pending}/check")
    payments = (await admin_client.get("/api/admin/billing/payments")).json()
    month = ist_date(utcnow()).strftime("%Y-%m")
    in_month = (await admin_client.get("/api/admin/billing/payments", params={"month": month})).json()
    refunded = (await admin_client.get("/api/admin/billing/payments", params={"status": "refunded"})).json()

    assert everything["total"] == 2 and [item["public_id"] for item in everything["items"]] == [pending, paid]
    assert [item["public_id"] for item in waiting["items"]] == [pending]
    assert [item["public_id"] for item in hals["items"]] == [paid]
    assert [event["type"] for event in detail["events"]][:1] == ["order.created"]
    assert detail["user"] == {"id": hal.user_id, "username": "hal"} and detail["mode"] == "test"
    assert checked.status_code == 200 and checked.json()["checks"] == 1
    assert payments["total"] == 1 and payments["items"][0]["razorpay_payment_id"].startswith("pay_")
    assert in_month["total"] == 1 and refunded["total"] == 0
    one = await admin_client.get(f"/api/admin/billing/payments/{payments['items'][0]['id']}")
    assert one.status_code == 200 and one.json() == payments["items"][0]
    assert (await admin_client.get("/api/admin/billing/payments/999999")).status_code == 404
    assert (await admin_client.get("/api/admin/billing/orders/nope")).status_code == 404


@pytest.mark.usefixtures("fake_razorpay")
async def test_resolving_a_review(
    admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    jay = await new_tester(create_user, "jay")
    public_id = await started(jay, "starter")
    order = await order_row(public_id)
    fake_razorpay.pay(order.razorpay_link_id or "", amount=100)
    await admin_client.post(f"/api/admin/billing/orders/{public_id}/check")
    to_review = (await admin_client.get("/api/admin/billing/orders", params={"status": "review"})).json()

    not_review = await admin_client.post(
        f"/api/admin/billing/orders/{(await started(jay, 'standard'))}/resolve", json={"note": "x"}
    )
    resolved = await admin_client.post(
        f"/api/admin/billing/orders/{public_id}/resolve", json={"note": "Refunded by hand"}
    )

    assert not_review.status_code == 409
    assert resolved.json()["review_note"] == "Refunded by hand" and resolved.json()["status"] == "review"
    assert [item["public_id"] for item in to_review["items"]] == [public_id] and to_review["total"] == 1
    left = (await admin_client.get("/api/admin/billing/orders", params={"status": "review"})).json()
    assert left == {"items": [], "total": 0}  # the review filter lists the orders still to review
    everything = (await admin_client.get("/api/admin/billing/orders")).json()
    assert public_id in [item["public_id"] for item in everything["items"]]
    async with db.SessionLocal() as session:
        assert [item for item in await summary.attention(session) if item.kind == "review"] == []
    assert (await events("order.resolved"))[0].detail["note"] == "Refunded by hand"


# ---------------- The summary ----------------


@pytest.mark.usefixtures("fake_razorpay")
async def test_the_months_summary(
    admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    kim = await new_tester(create_user, "kim")
    lee = await new_tester(create_user, "lee")
    await create_user("mia")  # open-ended Professional
    await create_user("ned", plan=None)
    await buy(kim, fake_razorpay, "starter")
    await buy(lee, fake_razorpay, "standard")
    async with db.SessionLocal() as session:
        await session.execute(
            update(PlanPeriod).where(PlanPeriod.user_id == lee.user_id).values(ends_at=utcnow() + timedelta(hours=2))
        )
        await session.commit()
    payment = (await admin_client.get("/api/admin/billing/payments", params={"user_id": kim.user_id})).json()["items"][
        0
    ]
    await admin_client.post(
        f"/api/admin/billing/payments/{payment['id']}/refund",
        json={"amount_paise": 10_000, "reason": "x", "end_access": False},
    )
    olga = await create_user("olga", plan=None)
    await admin_client.post(
        f"/api/admin/billing/users/{olga.user_id}/grants",
        json={"plan_id": (await plan_ids(admin_client))["starter"], "days": 5, "reason": "x", "start": "now"},
    )
    await end_plan(olga.user_id or 0)

    report = (await admin_client.get("/api/admin/billing/summary")).json()

    assert report["month"] == ist_date(utcnow()).strftime("%Y-%m")
    assert report["gross_paise"] == 250_000 and report["payments"] == 2
    assert report["fee_paise"] == 1_180 + 4_720
    assert report["refunded_paise"] == 10_000 and report["refunds"] == 1
    assert report["net_paise"] == 250_000 - 5_900 - 10_000
    assert sorted((item["plan"], item["payments"]) for item in report["by_plan"]) == [("Standard", 1), ("Starter", 1)]
    assert {item["plan"]: (item["paid"], item["complimentary"]) for item in report["current_users"]} == {
        "Starter": (1, 0),
        "Standard": (1, 0),
        "Professional": (0, 1),
    }
    assert report["users_without_plan"] == 2
    assert len(report["months"]) == 12 and report["months"][-1]["gross_paise"] == 250_000
    assert sorted(row["user"]["username"] for row in report["expiring"]) == ["kim", "lee"]
    assert [row["date"] for row in report["expiring"]] == sorted(row["date"] for row in report["expiring"])
    assert [row["user"]["username"] for row in report["lapsed"]] == ["olga"]
    assert all(row["last_payment_at"] is not None for row in report["expiring"])
    assert report["lapsed"][0]["last_payment_at"] is None
    assert {item["kind"] for item in report["attention"]} == {"test_mode"}
    assert (await admin_client.get("/api/admin/billing/summary", params={"month": "2026-13"})).status_code == 422
    earlier = (await admin_client.get("/api/admin/billing/summary", params={"month": "2020-01"})).json()
    assert earlier["gross_paise"] == 0 and earlier["months"][0]["month"] == "2019-02"


async def test_attention_without_keys_and_with_live_keys(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def kinds() -> list[str]:
        async with db.SessionLocal() as session:
            return [item.kind for item in await summary.attention(session)]

    assert await kinds() == ["payments_not_configured"]
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_live_AbCd")
    monkeypatch.setattr(settings, "razorpay_key_secret", "secret")
    assert await kinds() == ["live_without_webhook"]
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_AbCd")
    monkeypatch.setattr(settings, "public_base_urls", "https://rag.example.com")
    assert await kinds() == ["test_mode", "test_keys_public"]


async def duplicates() -> list[AttentionItem]:
    async with db.SessionLocal() as session:
        return [item for item in await summary.attention(session) if item.kind == "duplicate"]


@pytest.mark.usefixtures("fake_razorpay")
async def test_two_checkouts_open_at_once_and_both_paid_are_a_possible_duplicate(
    admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    pia = await new_tester(create_user, "pia")
    first = await started(pia, "starter")
    first_link = await link_of(first)
    fake_razorpay.fail_next(500, path=f"/v1/payment_links/{first_link}/cancel")  # the old link stays payable
    second = await started(pia, "standard")
    for link in (first_link, await link_of(second)):
        await come_back(pia, fake_razorpay.pay(link))

    (item,) = await duplicates()

    assert item.message == "Possible duplicate payment by pia"
    assert item.detail == "₹500 and ₹2,000 within 24 hours from checkouts open at the same time; both added a period."
    assert [order.status for order in (await order_row(first), await order_row(second))] == ["paid", "paid"]


@pytest.mark.usefixtures("fake_razorpay")
async def test_a_renewal_or_an_upgrade_after_a_payment_isnt_a_duplicate(
    admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    pia = await new_tester(create_user, "pia")
    await buy(pia, fake_razorpay, "starter")
    await buy(pia, fake_razorpay, "starter")
    await buy(pia, fake_razorpay, "professional", "now")

    assert await duplicates() == []


# ---------------- The CSV export ----------------


@pytest.mark.usefixtures("fake_razorpay")
async def test_the_csv_export(admin_client: ApiClient, create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    quin = await create_user("-dash", plan=None)
    await offline(admin_client, quin.user_id or 0, reference="+REF-1")
    payment_id = (await admin_client.get("/api/admin/billing/payments")).json()["items"][0]["id"]
    await admin_client.post(
        f"/api/admin/billing/payments/{payment_id}/refund",
        json={"amount_paise": 50_000, "reason": "x", "end_access": False},
    )
    async with db.SessionLocal() as session:
        await session.execute(update(BillingPayment).values(captured_at=utcnow()))
        await session.commit()
    month = ist_date(utcnow()).strftime("%Y-%m")

    response = await admin_client.get("/api/admin/billing/export.csv", params={"month": month})

    assert response.status_code == 200 and response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="payments-{month}.csv"'
    header, *rows = list(csv.reader(io.StringIO(response.text)))
    assert header[:4] == ["Date (IST)", "Type", "Receipt", "User"]
    payment_row, refund_row = sorted(rows, key=lambda row: row[1])
    assert payment_row[1:] == [
        "payment",
        payment_row[2],
        "'-dash",
        "Standard",
        "period",
        "2000.00",
        "bank_transfer",
        "'+REF-1",
        "",
        "",
        "500.00",
    ]
    assert refund_row[1] == "refund" and refund_row[6] == "500.00"
    assert (await admin_client.get("/api/admin/billing/export.csv", params={"month": "26-10"})).status_code == 422
    async with db.SessionLocal() as session:
        assert await session.get(BillingOrder, 1) is not None
