"""Applying payments under races, on Postgres (docs/briefs/plans-billing.md B42 and 6.5): the callback, the webhook's
processing and the reconciler applying one payment at once give one period and one receipt; two orders of one user,
or a grant and a payment, applied at once never overlap; checkouts at once keep to the caps and leave one payable
link; and the exclusion constraint refuses a forced overlap."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from billing_support import CreateUser, checkout, link_of, new_tester, order_row, periods_of, started
from conftest import ADMIN_USERNAME
from fake_razorpay import FakeRazorpay
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import BillingOrder, BillingReceipt, Plan, PlanPeriod, User, utcnow
from app.services.billing import checkout as checkout_service
from app.services.billing import payments, periods

pytestmark = [pytest.mark.postgres, pytest.mark.usefixtures("fake_razorpay")]


async def apply(order_id: int, entity: dict[str, Any], source: str) -> str:
    async with db.SessionLocal() as session:
        return await payments.apply_payment(session, order_id, entity, source=source)


def no_overlap(rows: list[PlanPeriod]) -> bool:
    live = sorted((row for row in rows if row.revoked_at is None), key=lambda row: row.starts_at)
    return all(earlier.ends_at <= later.starts_at for earlier, later in zip(live, live[1:], strict=False))


async def test_one_payment_applied_from_three_sides_at_once(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    alice = await new_tester(create_user, "alice")
    public_id = await started(alice, "starter")
    query = fake_razorpay.pay(await link_of(public_id))
    entity = fake_razorpay.payments[query["razorpay_payment_id"]]
    order = await order_row(public_id)

    outcomes = await asyncio.gather(
        *(apply(order.id, entity, source) for source in ("callback", "webhook", "reconcile"))
    )

    assert sorted(outcomes) == ["applied", "done", "done"]
    assert len(await periods_of(alice.user_id or 0)) == 1
    async with db.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(BillingReceipt)) == 1


async def test_two_orders_of_one_user_applied_at_once_dont_overlap(
    create_user: CreateUser, fake_razorpay: FakeRazorpay
) -> None:
    bob = await new_tester(create_user, "bob")
    first, second = await started(bob, "starter"), await started(bob, "standard")
    paid = [fake_razorpay.pay(await link_of(public_id)) for public_id in (first, second)]
    orders = [await order_row(public_id) for public_id in (first, second)]

    outcomes = await asyncio.gather(
        *(
            apply(order.id, fake_razorpay.payments[query["razorpay_payment_id"]], "reconcile")
            for order, query in zip(orders, paid, strict=True)
        )
    )

    assert outcomes == ["applied", "applied"]
    rows = await periods_of(bob.user_id or 0)
    assert len(rows) == 2 and no_overlap(rows)


async def test_a_grant_racing_a_payment_doesnt_overlap(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    carol = await new_tester(create_user, "carol")
    public_id = await started(carol, "starter")
    query = fake_razorpay.pay(await link_of(public_id))
    order = await order_row(public_id)

    async def grant() -> None:
        async with db.SessionLocal() as session:
            user = await session.get(User, carol.user_id)
            plan = await session.scalar(select(Plan).where(Plan.code == "professional"))
            assert user is not None and plan is not None
            await periods.grant(
                session, user, plan, days=10, reason="Goodwill", actor=ADMIN_USERNAME, start="after_current"
            )
            await session.commit()

    await asyncio.gather(grant(), apply(order.id, fake_razorpay.payments[query["razorpay_payment_id"]], "callback"))

    rows = await periods_of(carol.user_id or 0)
    assert len(rows) == 2 and no_overlap(rows)


async def test_checkouts_at_once_leave_one_payable_link(create_user: CreateUser, fake_razorpay: FakeRazorpay) -> None:
    erin = await new_tester(create_user, "erin")

    responses = await asyncio.gather(*(checkout(erin, code) for code in ("starter", "standard", "professional") * 3))

    assert {response.status_code for response in responses} <= {201, 409, 429}
    assert len(fake_razorpay.links) <= checkout_service.HOUR_CAP
    live = [link["id"] for link in fake_razorpay.links.values() if link["status"] == "created"]
    async with db.SessionLocal() as session:
        pending = list(
            await session.scalars(
                select(BillingOrder.razorpay_link_id).where(
                    BillingOrder.user_id == erin.user_id, BillingOrder.status == "pending"
                )
            )
        )
    assert live == pending and len(live) == 1


async def test_the_constraint_refuses_a_forced_overlap(create_user: CreateUser) -> None:
    dave = await create_user("dave", plan=None)
    now = utcnow()

    def row(start_hours: int) -> PlanPeriod:
        start = now + timedelta(hours=start_hours)
        return PlanPeriod(
            user_id=dave.user_id,
            starts_at=start,
            ends_at=start + timedelta(days=30),
            days=30,
            source="payment",
            mode="live",
            snapshot={},
            charge_basis_paise=0,
            history=[],
        )

    async with db.SessionLocal() as session:
        session.add_all([row(0), row(1)])
        with pytest.raises(IntegrityError, match="ex_plan_periods_no_overlap"):
            await session.commit()
