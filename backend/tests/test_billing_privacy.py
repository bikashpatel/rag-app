"""No payer details anywhere (docs/briefs/plans-billing.md B30 and 6.12): Razorpay's payment, refund and dispute
entities carry the payer's email, phone, UPI id, card and bank, in fetches and in webhook bodies; none of it may reach
any billing table, the audit events included."""

import json
from typing import Any

import pytest
from billing_support import CreateUser, buy, new_tester, payments_of, run_worker, send_webhook
from conftest import ApiClient
from fake_razorpay import PAYER, FakeRazorpay
from sqlalchemy import select

from app import db
from app.models import (
    BillingDispute,
    BillingEvent,
    BillingOrder,
    BillingPayment,
    BillingReceipt,
    BillingRefund,
    PlanPeriod,
    UserBillingProfile,
)

TABLES = (BillingOrder, BillingPayment, BillingRefund, BillingDispute, BillingReceipt, BillingEvent, PlanPeriod)
SECRETS = (*PAYER.values(), "card_FakeCard0001")


async def everything_stored() -> str:
    dumped: list[dict[str, Any]] = []
    async with db.SessionLocal() as session:
        for table in (*TABLES, UserBillingProfile):
            for row in await session.scalars(select(table)):
                dumped.append({column.key: getattr(row, column.key) for column in table.__table__.columns})
    return json.dumps(dumped, default=str)


@pytest.mark.usefixtures("fake_razorpay")
async def test_payer_details_from_payments_refunds_and_disputes_are_never_stored(
    create_user: CreateUser, fake_razorpay: FakeRazorpay, admin_client: ApiClient, client: ApiClient
) -> None:
    payer = await new_tester(create_user, "payer")
    public_id = await buy(payer, fake_razorpay, "starter", method="card")
    link = fake_razorpay.links[next(iter(fake_razorpay.links))]
    (payment,) = await payments_of(payer.user_id or 0)
    pay_id = payment.razorpay_payment_id or ""
    fake_razorpay.fail(link["id"], method="upi")
    refund = fake_razorpay.refund_in_dashboard(pay_id, amount=10_000)
    refund["notes"] = {"email": PAYER["email"]}
    dispute = fake_razorpay.raise_dispute(pay_id)
    for event, ids in (
        ("payment_link.paid", {"link_id": link["id"]}),
        ("payment.captured", {"payment_id": pay_id}),
        ("payment.failed", {"payment_id": link["payments"][-1]}),
        ("refund.processed", {"refund_id": refund["id"], "payment_id": pay_id}),
        ("payment.dispute.created", {"dispute_id": dispute["id"], "payment_id": pay_id}),
    ):
        response = await send_webhook(client, *fake_razorpay.webhook(event, **ids))
        assert response.status_code == 200
    await run_worker()
    await admin_client.post(
        f"/api/admin/billing/payments/{payment.id}/refund",
        json={"amount_paise": 5_000, "reason": "Partial", "end_access": False},
    )
    await admin_client.get(f"/api/admin/billing/orders/{public_id}")

    stored = await everything_stored()

    assert '"refund"' in stored or "rfnd_" in stored  # the flows really ran
    for secret in SECRETS:
        assert secret not in stored, secret
    async with db.SessionLocal() as session:
        methods = set(await session.scalars(select(BillingPayment.method)))
    assert methods == {"card", "upi"}
