"""The monitor's `billing` check, "Payments" (docs/briefs/plans-billing.md 11.3; app/ops/checks.py and
services/billing/summary.problems): ok without keys or problems; a warning, with counts only, for orders to review, a
stalled reconciler, rejected keys, stuck or failed refunds, bad signatures, failing or silent webhooks and risky key
setups; unknown when the records can't be read."""

from datetime import timedelta

import pytest
from billing_support import CreateUser, buy, new_tester, order_row, started
from fake_razorpay import FakeRazorpay
from sqlalchemy import select, update

from app import db
from app.config import settings
from app.models import BillingEvent, BillingOrder, BillingPayment, BillingRefund, utcnow
from app.ops import checks
from app.redis_client import get_redis
from app.services.billing import business, summary


async def detail() -> tuple[str, str]:
    found = await checks.check_billing()
    assert found.id == "billing" and found.label == "Payments"
    return found.status, found.detail


@pytest.mark.usefixtures("app")
async def test_ok_while_payments_arent_set_up() -> None:
    assert await detail() == ("ok", "Payments not set up")


@pytest.mark.usefixtures("app", "fake_razorpay")
async def test_ok_when_nothing_needs_attention() -> None:
    assert await detail() == ("ok", "Payments in test mode; nothing needs attention")


@pytest.mark.usefixtures("fake_razorpay")
async def test_orders_to_review_and_a_stalled_reconciler(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    alice = await new_tester(create_user, "alice")
    reviewed = await started(alice, "starter")
    waiting = await started(alice, "standard")
    async with db.SessionLocal() as session:
        await session.execute(
            update(BillingOrder).where(BillingOrder.public_id == reviewed).values(status="review", next_check_at=None)
        )
        await session.execute(
            update(BillingOrder)
            .where(BillingOrder.public_id == waiting)
            .values(next_check_at=utcnow() - timedelta(minutes=20))
        )
        await session.commit()

    status, text = await detail()

    assert status == "warning"
    assert text == "1 payment to review; The payment checks are stalled (1 order overdue)"
    assert "alice" not in text and "₹" not in text
    await business.set_keys_rejected("Authentication failed")
    assert (await detail())[1] == "1 payment to review; Razorpay rejected the API keys"
    assert (await order_row(waiting)).status == "pending"


@pytest.mark.usefixtures("fake_razorpay")
async def test_refunds_signatures_and_webhooks(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    bob = await new_tester(create_user, "bob")
    await buy(bob, fake_razorpay, "starter")
    async with db.SessionLocal() as session:
        payment_id = await session.scalar(select(BillingPayment.id))
        assert payment_id is not None
        session.add(
            BillingRefund(
                payment_id=payment_id,
                idempotency_key="rfd_failed_one",
                amount_paise=100,
                status="failed",
                initiated_by="admin",
                end_access=False,
            )
        )
        session.add(BillingEvent(source="webhook", type="refund.processed", processed=False, attempts=3, detail={}))
        await session.commit()
    redis = get_redis()
    await redis.set(business.hour_key("billing:wh:badsig"), "15")
    await redis.set(business.hour_key("billing:cb:badsig"), "5")

    status, text = await detail()

    assert status == "warning"
    assert text == (
        "1 refund stuck or failed; 1 webhook event failed 3 times; 20 bad webhook or return signatures in the last hour"
    )


@pytest.mark.usefixtures("fake_razorpay")
async def test_a_capture_no_webhook_reported(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    cara = await new_tester(create_user, "cara")
    await buy(cara, fake_razorpay, "starter")
    async with db.SessionLocal() as session:
        await session.execute(update(BillingPayment).values(captured_at=utcnow() - timedelta(hours=1)))
        await session.commit()

    assert await summary.problems() == [
        "No webhook reported 1 payment of the last 24 hours (Razorpay may have disabled the webhook)"
    ]


@pytest.mark.usefixtures("app")
async def test_risky_key_setups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_live_AbCd")
    monkeypatch.setattr(settings, "razorpay_key_secret", "secret")
    assert await detail() == ("warning", "Live keys run without RAZORPAY_WEBHOOK_SECRET")
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_AbCd")
    monkeypatch.setattr(settings, "public_base_urls", "http://localhost:8080,https://rag.example.com")
    assert await detail() == ("warning", "Test keys run on a public https address")


@pytest.mark.usefixtures("app", "fake_razorpay")
async def test_unknown_when_the_records_cant_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken() -> list[str]:
        raise RuntimeError("database down")

    monkeypatch.setattr(summary, "problems", broken)

    assert await detail() == ("unknown", "The billing records can't be read: failed (RuntimeError)")
