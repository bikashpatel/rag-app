"""Refunds (docs/briefs/plans-billing.md 4.10 and 6.7; services/billing/refunds.py): full and partial refunds with the
idempotency key, a retried timeout, a refund seen by the API, a webhook, the reconciler and a replayed body counted
once, the refundable amount without the credit a later upgrade used, a full refund of an upgrade giving the replaced
period back (unless something changed it since, or its own payment was refunded too), refunds made in Razorpay's
Dashboard, bank transfers, and the upgrade credit computed from what is left after refunds."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from billing_support import CreateUser, buy, events, new_tester, order_row, payments_of, periods_of, run_worker
from conftest import ApiClient
from fake_razorpay import FakeRazorpay
from sqlalchemy import select, update

from app import db
from app.models import BillingPayment, BillingRefund, PlanPeriod, User, utcnow
from app.schemas_billing import AttentionItem
from app.services.billing import entitlements, razorpay, refunds, summary
from app.services.billing.webhooks import process

pytestmark = pytest.mark.usefixtures("fake_razorpay")


async def refund(admin: ApiClient, payment_id: int, **body: Any) -> Any:
    response = await admin.post(
        f"/api/admin/billing/payments/{payment_id}/refund",
        json={"amount_paise": None, "reason": "Asked for it", "end_access": False, **body},
    )
    return response


async def state(user_id: int) -> str:
    async with db.SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return (await entitlements.load(session, user)).state


async def refund_rows(payment_id: int) -> list[BillingRefund]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(select(BillingRefund).where(BillingRefund.payment_id == payment_id))
        return list(rows)


async def attention(kind: str) -> list[AttentionItem]:
    async with db.SessionLocal() as session:
        return [item for item in await summary.attention(session) if item.kind == kind]


async def bought(user: ApiClient, fake: FakeRazorpay, code: str = "starter", kind: str = "period") -> BillingPayment:
    public_id = await buy(user, fake, code, kind)
    order = await order_row(public_id)
    return next(payment for payment in await payments_of(user.user_id or 0) if payment.order_id == order.id)


async def test_a_full_refund_ends_what_the_payment_bought(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    alice = await new_tester(create_user, "alice")
    payment = await bought(alice, fake_razorpay)
    assert await state(alice.user_id or 0) == "active"

    response = await refund(admin_client, payment.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "refunded" and body["refunded_paise"] == 50_000 and body["refundable_paise"] == 0
    (row,) = body["refunds"]
    assert row["status"] == "processed" and row["initiated_by"] == "admin" and row["admin_username"] == "admin"
    (made,) = fake_razorpay.refunds.values()
    assert made["amount"] == 50_000 and made["notes"] == {"refund": str(row["id"])} and made["speed_requested"]
    (key,) = fake_razorpay.idempotency
    assert key.startswith("rfd_") and len(key) >= 10
    (period,) = await periods_of(alice.user_id or 0)
    assert period.revoked_at is not None and period.revoked_reason == "Refunded"
    assert await state(alice.user_id or 0) == "expired"
    assert (await alice.get("/api/billing/payments")).json()[0]["status"] == "refunded"


async def test_a_partial_refund_keeps_access_unless_asked(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    bob = await new_tester(create_user, "bob")
    payment = await bought(bob, fake_razorpay)

    kept = await refund(admin_client, payment.id, amount_paise=10_000)
    ended = await refund(admin_client, payment.id, amount_paise=10_000, end_access=True)

    assert kept.json()["status"] == "partially_refunded" and kept.json()["refundable_paise"] == 40_000
    assert ended.json()["refunded_paise"] == 20_000 and ended.json()["refundable_paise"] == 30_000
    rows = await refund_rows(payment.id)
    assert [row.access_ended_at is not None for row in rows] == [False, True]
    assert await state(bob.user_id or 0) == "expired"


async def test_refund_requests_that_cant_be_made(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    carol = await new_tester(create_user, "carol")
    payment = await bought(carol, fake_razorpay)

    too_much = await refund(admin_client, payment.id, amount_paise=60_000)
    unknown = await refund(admin_client, 999)
    invalid = await refund(admin_client, payment.id, amount_paise=50)
    await refund(admin_client, payment.id)
    nothing_left = await refund(admin_client, payment.id)

    assert too_much.status_code == 409 and too_much.json()["detail"] == "At most ₹500 of this payment can be refunded."
    assert unknown.status_code == 404 and invalid.status_code == 422
    assert nothing_left.status_code == 409
    assert nothing_left.json()["detail"] == "Nothing is left to refund on this payment."
    assert (await carol.post(f"/api/admin/billing/payments/{payment.id}/refund", json={})).status_code == 403


async def test_a_timeout_is_retried_with_the_same_key_and_body(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dave = await new_tester(create_user, "dave")
    payment = await bought(dave, fake_razorpay)
    create = razorpay.create_refund
    sent: list[tuple[dict[str, Any], str]] = []

    async def lost(payment_id: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        sent.append((body, key))
        await create(payment_id, body, key)
        raise razorpay.RazorpayError(None, "UNREACHABLE", "Razorpay unreachable (ReadTimeout)")

    monkeypatch.setattr(razorpay, "create_refund", lost)
    response = await refund(admin_client, payment.id)
    another = await refund(admin_client, payment.id, amount_paise=100)
    monkeypatch.setattr(razorpay, "create_refund", lambda *args: (sent.append(args[1:]), create(*args))[1])
    async with db.SessionLocal() as session:
        await session.execute(update(BillingRefund).values(created_at=utcnow() - timedelta(minutes=2)))
        await session.commit()

    assert response.json()["refunds"][0]["status"] == "creating"
    assert response.json()["refunds"][0]["last_error"] == "Razorpay unreachable; the refund is sent again shortly"
    assert another.status_code == 409 and another.json()["detail"] == "Another refund of this payment is being made."
    assert await refunds.retry_creating() == 1

    (row,) = await refund_rows(payment.id)
    assert row.status == "processed" and row.last_error is None
    assert len(fake_razorpay.refunds) == 1 and sent[0] == sent[1]
    assert await state(dave.user_id or 0) == "expired"


async def test_a_refused_refund_fails_and_needs_attention(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    erin = await new_tester(create_user, "erin")
    payment = await bought(erin, fake_razorpay)
    fake_razorpay.fail_next(400, "Normal refunds are not possible for this payment", path="/v1/payments/")

    response = await refund(admin_client, payment.id)

    (row,) = response.json()["refunds"]
    assert row["status"] == "failed" and row["last_error"] == "Normal refunds are not possible for this payment"
    assert response.json()["refundable_paise"] == 50_000 and await state(erin.user_id or 0) == "active"
    async with db.SessionLocal() as session:
        items = [item for item in await summary.attention(session) if item.kind == "refund_failed"]
    assert [item.message for item in items] == ["Refund of ₹500 to erin failed"]


async def test_a_refund_seen_from_every_side_counts_once(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient, client: ApiClient
) -> None:
    fiona = await new_tester(create_user, "fiona")
    payment = await bought(fiona, fake_razorpay)
    fake_razorpay.refund_status = "pending"
    await refund(admin_client, payment.id)
    (made,) = fake_razorpay.refunds.values()
    fake_razorpay.set_refund_status(made["id"], "processed")
    body, headers = fake_razorpay.webhook(
        "refund.processed", refund_id=made["id"], payment_id=payment.razorpay_payment_id
    )
    again_body, again_headers = fake_razorpay.webhook(
        "refund.processed", refund_id=made["id"], payment_id=payment.razorpay_payment_id, created_at=1
    )

    await client.post("/api/billing/razorpay/webhook", content=body, headers=headers)
    await client.post(
        "/api/billing/razorpay/webhook", content=body, headers={**headers, "X-Razorpay-Event-Id": "evt_New"}
    )
    await run_worker()
    async with db.SessionLocal() as session:
        await refunds.apply(session, fake_razorpay.refunds[made["id"]], source="reconcile")
    assert (
        await client.post("/api/billing/razorpay/webhook", content=again_body, headers=again_headers)
    ).status_code == 200
    await run_worker()

    (row,) = await refund_rows(payment.id)
    assert row.status == "processed" and row.access_ended_at is not None
    (paid,) = await payments_of(fiona.user_id or 0)
    assert paid.refunded_paise == 50_000 and paid.status == "refunded"
    processed = [event for event in await events("refund.processed") if event.body_sha256 is None]  # not the inbox
    assert len(processed) == 1 and len(await events("period.revoked")) == 1


async def test_pending_refunds_are_fetched_after_30_minutes(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    gina = await new_tester(create_user, "gina")
    payment = await bought(gina, fake_razorpay)
    fake_razorpay.refund_status = "pending"
    await refund(admin_client, payment.id)
    (made,) = fake_razorpay.refunds.values()

    assert await refunds.refresh_pending() == 0  # too recent
    async with db.SessionLocal() as session:
        await session.execute(update(BillingRefund).values(updated_at=utcnow() - timedelta(minutes=31)))
        await session.commit()
    assert await refunds.refresh_pending() == 1  # still pending at Razorpay
    (row,) = await refund_rows(payment.id)
    assert row.status == "pending" and row.updated_at > utcnow() - timedelta(minutes=1)
    fake_razorpay.set_refund_status(made["id"], "processed")
    async with db.SessionLocal() as session:
        await session.execute(update(BillingRefund).values(updated_at=utcnow() - timedelta(minutes=31)))
        await session.commit()
    await refunds.refresh_pending()

    assert (await refund_rows(payment.id))[0].status == "processed"


async def test_the_credit_an_upgrade_took_isnt_refundable(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    hana = await new_tester(create_user, "hana")
    starter = await bought(hana, fake_razorpay)
    await bought(hana, fake_razorpay, "standard", "now")

    view = (await admin_client.get(f"/api/admin/billing/users/{hana.user_id}")).json()
    first = next(item for item in view["payments"] if item["id"] == starter.id)
    response = await refund(admin_client, starter.id)

    assert first["credit_used_paise"] == 49_900 and first["refundable_paise"] == 100
    assert response.json()["refunded_paise"] == 100 and response.json()["status"] == "partially_refunded"


async def test_refunding_an_upgrade_gives_the_replaced_period_back(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    ivy = await new_tester(create_user, "ivy")
    await bought(ivy, fake_razorpay)
    starter_before = (await periods_of(ivy.user_id or 0))[0].ends_at
    upgrade = await bought(ivy, fake_razorpay, "standard", "now")

    await refund(admin_client, upgrade.id)

    starter, standard = await periods_of(ivy.user_id or 0)
    assert standard.revoked_at is not None
    assert starter.ends_at == starter_before and starter.history[-1]["kind"] == "restored"
    async with db.SessionLocal() as session:
        user = await session.get(User, ivy.user_id)
        assert user is not None
        assert (await entitlements.load(session, user)).plan_name == "Starter"


async def test_a_replaced_period_changed_since_isnt_given_back(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    jay = await new_tester(create_user, "jay")
    await bought(jay, fake_razorpay)
    upgrade = await bought(jay, fake_razorpay, "standard", "now")
    async with db.SessionLocal() as session:
        starter = (await session.scalars(select(PlanPeriod).order_by(PlanPeriod.starts_at))).first()
        assert starter is not None
        starter.history = [*starter.history, {"kind": "change", "by": "admin"}]
        await session.commit()

    await refund(admin_client, upgrade.id)

    starter_after, standard = await periods_of(jay.user_id or 0)
    assert standard.revoked_at is not None and starter_after.ends_at <= utcnow()
    assert [event.detail["period_id"] for event in await events("period.not_restored")] == [starter_after.id]
    assert await state(jay.user_id or 0) == "expired"


async def test_dashboard_refunds_are_recorded_once_under_a_race(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    kim = await new_tester(create_user, "kim")
    payment = await bought(kim, fake_razorpay)
    made = fake_razorpay.refund_in_dashboard(payment.razorpay_payment_id or "", amount=20_000)

    async def seen() -> str:
        async with db.SessionLocal() as session:
            return await refunds.apply(session, made, source="webhook")

    outcomes = await asyncio.gather(seen(), seen())

    assert sorted(outcomes) == ["changed", "same"]
    (row,) = await refund_rows(payment.id)
    assert row.initiated_by == "razorpay" and row.razorpay_refund_id == made["id"] and row.status == "processed"
    assert await state(kim.user_id or 0) == "active"  # partial: access unchanged
    async with db.SessionLocal() as session:
        items = [item for item in await summary.attention(session) if item.kind == "partial_refund"]
    assert [item.payment_id for item in items] == [payment.id]


async def test_a_full_dashboard_refund_ends_access(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    lee = await new_tester(create_user, "lee")
    payment = await bought(lee, fake_razorpay)
    made = fake_razorpay.refund_in_dashboard(payment.razorpay_payment_id or "")

    async with db.SessionLocal() as session:
        await refunds.apply(session, made, source="webhook")

    assert await state(lee.user_id or 0) == "expired"


@pytest.mark.usefixtures("app")
async def test_a_refund_of_an_unknown_payment_is_unknown() -> None:
    async with db.SessionLocal() as session:
        outcome = await refunds.apply(
            session, {"id": "rfnd_X", "payment_id": "pay_Unknown", "status": "processed"}, source="webhook"
        )

    assert outcome == "unknown"


async def test_a_dashboard_refund_webhook_of_an_unknown_payment_needs_attention(
    client: ApiClient, fake_razorpay: FakeRazorpay, create_user: CreateUser
) -> None:
    mia = await new_tester(create_user, "mia")
    payment = await bought(mia, fake_razorpay)
    made = fake_razorpay.refund_in_dashboard(payment.razorpay_payment_id or "")
    async with db.SessionLocal() as session:
        await session.execute(update(BillingPayment).values(razorpay_payment_id="pay_Renamed0000001"))
        await session.commit()
    body, headers = fake_razorpay.webhook("refund.processed", refund_id=made["id"])
    await client.post("/api/billing/razorpay/webhook", content=body, headers=headers)
    (event,) = [event for event in await events() if event.razorpay_event_id]

    for _ in range(3):
        await process(event.id)

    (failed,) = [event for event in await events() if event.razorpay_event_id]
    assert failed.attempts == 3 and failed.error == "The refund's payment isn't known here"


async def test_a_bank_transfer_refund_is_recorded_only(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    ned = await create_user("ned", plan=None)
    plan_id = (await admin_client.get("/api/admin/plans")).json()[1]["id"]
    recorded = await admin_client.post(
        "/api/admin/billing/offline-payments",
        json={
            "user_id": ned.user_id,
            "plan_id": plan_id,
            "amount_paise": 200_000,
            "received_on": "2026-10-25",
            "reference": "UTR123456",
            "note": None,
            "apply": True,
        },
    )
    fake_razorpay.requests.clear()

    response = await refund(admin_client, recorded.json()["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "refunded" and response.json()["refunds"][0]["status"] == "processed"
    assert fake_razorpay.requests == []
    assert await state(ned.user_id or 0) == "expired"


async def test_a_test_payment_cant_be_refunded_under_live_keys(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    olga = await new_tester(create_user, "olga")
    payment = await bought(olga, fake_razorpay)
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_live_Fake")

    response = await refund(admin_client, payment.id)

    assert response.status_code == 409
    assert response.json()["detail"] == "This test payment can't be refunded while the server runs live keys."


async def test_refunding_a_purchase_then_its_upgrade_gives_nothing_back(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    pat = await new_tester(create_user, "pat")
    first = await bought(pat, fake_razorpay, "standard")
    upgrade = await bought(pat, fake_razorpay, "professional", "now")
    made = fake_razorpay.refund_in_dashboard(first.razorpay_payment_id or "")
    async with db.SessionLocal() as session:
        await refunds.apply(session, made, source="webhook")
    credit_items = await attention("credit_reversed")

    await refund(admin_client, upgrade.id)

    assert [item.message.split(" went ")[0] for item in credit_items] == ["₹1,999 of a refunded payment by pat"]
    assert await state(pat.user_id or 0) == "expired"
    standard, professional = await periods_of(pat.user_id or 0)
    assert standard.revoked_reason == "Refunded" and professional.revoked_reason == "Refunded"
    assert await events("period.restored") == []
    assert await attention("credit_reversed") == []  # the upgrade ended too


async def test_the_credit_is_what_is_left_after_refunds(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient
) -> None:
    quin = await new_tester(create_user, "quin")
    first = await bought(quin, fake_razorpay, "standard")
    await refund(admin_client, first.id, amount_paise=100_000)

    catalog = (await quin.get("/api/billing/plans")).json()
    professional = next(plan for plan in catalog["plans"] if plan["code"] == "professional")
    upgrade = next(option for option in professional["options"] if option["kind"] == "now")
    await bought(quin, fake_razorpay, "professional", "now")

    assert upgrade["credit"]["amount_paise"] == 99_900 and upgrade["amount_paise"] == 400_100
    view = (await admin_client.get(f"/api/admin/billing/payments/{first.id}")).json()
    assert view["refunded_paise"] + view["credit_used_paise"] <= view["amount_paise"]
