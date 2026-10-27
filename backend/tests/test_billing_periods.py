"""Plan periods (app/services/billing/periods.py; docs/briefs/plans-billing.md 4.2, 4.4, 4.8 and 4.10): stacking,
re-chaining, complimentary grants, changing the plan, test-mode periods, applying a `now` order and its review cases,
and giving the replaced period back after a refund."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any

import pytest
from conftest import ADMIN_USERNAME, ApiClient
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import BillingEvent, BillingOrder, Plan, PlanPeriod, User, utcnow
from app.schemas_billing import PlanSnapshot
from app.services.billing import money, periods
from app.services.billing.periods import PeriodChanged, period_end

CreateUser = Callable[..., Awaitable[ApiClient]]
NOW = datetime.fromisoformat("2026-10-26T03:12:00+00:00")


@pytest.fixture
async def alice(create_user: CreateUser) -> int:
    """A user without a plan."""
    client = await create_user("alice", plan=None, login=False)
    assert client.user_id is not None
    return client.user_id


async def plan(db: AsyncSession, code: str) -> Plan:
    found = await db.scalar(select(Plan).where(Plan.code == code))
    assert found is not None
    return found


async def user(db: AsyncSession, user_id: int) -> User:
    found = await db.get(User, user_id)
    assert found is not None
    return found


async def paid(
    db: AsyncSession, user_id: int, code: str, *, now: datetime = NOW, mode: str = "live", days: int = 30
) -> PlanPeriod:
    """A paid period, added as a payment adds it: at max(now, paid through)."""
    found = await plan(db, code)
    return await periods.add_period(
        db,
        user_id,
        snapshot=periods.snapshot_of(found),
        days=days,
        source="payment",
        mode=mode,  # type: ignore[arg-type]
        basis_paise=found.price_paise,
        now=now,
    )


async def order(db: AsyncSession, user_id: int) -> int:
    row = BillingOrder(
        public_id=f"ord-{utcnow().timestamp()}",
        user_id=user_id,
        username="alice",
        plan_snapshot={},
        kind="now",
        amount_paise=100,
        mode="live",
        status="pending",
        origin="http://testserver",
        expires_at=NOW,
        watch_until=NOW,
    )
    db.add(row)
    await db.flush()
    return row.id


async def rows(user_id: int) -> list[PlanPeriod]:
    async with SessionLocal() as db:
        return list(
            await db.scalars(select(PlanPeriod).where(PlanPeriod.user_id == user_id).order_by(PlanPeriod.starts_at))
        )


async def events(kind: str) -> list[BillingEvent]:
    async with SessionLocal() as db:
        return list(await db.scalars(select(BillingEvent).where(BillingEvent.type == kind).order_by(BillingEvent.id)))


def spans(found: list[PlanPeriod]) -> list[tuple[datetime, datetime]]:
    return [(period.starts_at, period.ends_at) for period in found]


def state(found: list[PlanPeriod]) -> list[tuple[Any, ...]]:
    return [(p.id, p.starts_at, p.ends_at, p.plan_id, p.revoked_at, p.history, p.snapshot) for p in found]


# ---------------- Stacking and re-chaining ----------------


async def test_periods_stack_after_the_paid_time(alice: int) -> None:
    async with SessionLocal() as db:
        first = await paid(db, alice, "starter")
        second = await paid(db, alice, "standard")
        third = await paid(db, alice, "starter", now=NOW + timedelta(days=3))  # a late payment is never refused
        await db.commit()

    assert spans(await rows(alice)) == [
        (NOW, first.ends_at),
        (first.ends_at, second.ends_at),
        (second.ends_at, third.ends_at),
    ]
    assert first.ends_at == period_end(NOW, 30) and second.ends_at == period_end(first.ends_at, 30)
    async with SessionLocal() as db:
        found = await periods.user_periods(db, alice)
    assert [p.id for p in periods.chain(found, NOW)] == [first.id, second.id, third.id]
    assert periods.paid_through(found, NOW) == third.ends_at
    assert periods.current_period(found, NOW - timedelta(seconds=1)) is None
    assert periods.chain(found, NOW - timedelta(seconds=1)) == []
    assert periods.paid_through(found, NOW - timedelta(seconds=1)) is None


async def test_a_revoke_moves_the_later_periods_up(alice: int) -> None:
    async with SessionLocal() as db:
        first = await paid(db, alice, "starter")
        second = await paid(db, alice, "standard")
        third = await paid(db, alice, "starter")
        await periods.revoke(db, second, reason="Refunded", actor=ADMIN_USERNAME, source="admin", now=NOW)
        await db.commit()
    assert spans(await rows(alice)) == [
        (NOW, first.ends_at),
        (first.ends_at, second.ends_at),  # revoked, left where it was
        (first.ends_at, period_end(first.ends_at, 30)),
    ]

    later = NOW + timedelta(days=2)
    async with SessionLocal() as db:
        first = await db.get_one(PlanPeriod, first.id)
        await periods.revoke(db, first, reason="Granted by mistake", actor=ADMIN_USERNAME, source="admin", now=later)
        await periods.revoke(db, first, reason="again", actor=ADMIN_USERNAME, source="admin", now=later)  # once
        await db.commit()

    found = await rows(alice)
    assert [(p.id, p.revoked_at, p.revoked_reason) for p in found if p.id != third.id] == [
        (first.id, later, "Granted by mistake"),
        (second.id, NOW, "Refunded"),
    ]
    assert spans([p for p in found if p.id == third.id]) == [(later, period_end(later, 30))]
    assert [event.detail["reason"] for event in await events("period.revoked")] == ["Refunded", "Granted by mistake"]


async def test_test_mode_periods_count_only_under_test_keys(alice: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc")
    monkeypatch.setattr(settings, "razorpay_key_secret", "secret")
    async with SessionLocal() as db:
        trial = await paid(db, alice, "standard", mode="test", days=periods.TEST_PERIOD_DAYS)
        after = await paid(db, alice, "starter")
        await db.commit()
    assert trial.ends_at == period_end(NOW, 1) and after.starts_at == trial.ends_at
    assert "test" in periods.counting_modes()

    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_live_abc")
    async with SessionLocal() as db:
        found = await periods.user_periods(db, alice)
        live = await paid(db, alice, "starter", now=NOW + timedelta(hours=1))
        await db.commit()

    assert [p.id for p in found] == [after.id] and "test" not in periods.counting_modes()
    assert periods.current_period(found, NOW + timedelta(hours=1)) is None  # the test period doesn't count
    assert live.starts_at == after.ends_at  # never overlapping the period still to come


# ---------------- Complimentary plans ----------------


async def test_a_dated_grant_is_split_into_rows_and_the_short_one_is_scaled(alice: int) -> None:
    async with SessionLocal() as db:
        professional = await plan(db, "professional")
        await periods.grant(db, await user(db, alice), professional, days=75, reason="Trial", actor="maya", start="now")
        await db.commit()

    found = await rows(alice)
    assert [p.days for p in found] == [30, 30, 15]
    assert [p.starts_at for p in found[1:]] == [p.ends_at for p in found[:-1]]  # one unbroken run
    assert {(p.source, p.mode, p.charge_basis_paise, p.reason, p.granted_by) for p in found} == {
        ("comp", "none", 0, "Trial", "maya")
    }
    limits = [periods.snapshot(p).limits for p in found]
    assert [(lim.monthly_token_limit, lim.monthly_image_limit, lim.vision_monthly_token_limit) for lim in limits] == [
        (5_000_000, 60, 1_000_000),
        (5_000_000, 60, 1_000_000),
        (2_500_000, 30, 500_000),
    ]
    assert {lim.max_documents for lim in limits} == {500}  # other limits stay the plan's
    [granted] = await events("period.granted")
    assert (granted.source, granted.actor, granted.user_id) == ("admin", "maya", alice)
    assert granted.detail == {
        "plan_id": found[0].plan_id,
        "plan": "professional",
        "days": 75,
        "start": "now",
        "reason": "Trial",
        "period_ids": [p.id for p in found],
    }


async def test_a_one_day_row_carries_a_thirtieth_of_each_allowance(alice: int) -> None:
    """SEC-1: a short complimentary row can't carry a whole period's allowance."""
    async with SessionLocal() as db:
        await periods.grant(
            db, await user(db, alice), await plan(db, "professional"), days=1, reason="Demo", actor="maya", start="now"
        )
        await db.commit()

    [row] = await rows(alice)
    limits = periods.snapshot(row).limits
    assert (limits.monthly_token_limit, limits.monthly_image_limit, limits.vision_monthly_token_limit) == (
        166_667,
        2,
        33_334,
    )
    assert (limits.rate_limit_per_min, limits.max_storage_mb) == (30, 3000)


async def test_a_grant_starts_now_only_without_a_current_period(alice: int) -> None:
    async with SessionLocal() as db:
        current = await paid(db, alice, "starter", now=utcnow())
        await db.commit()

    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as refused:
            await periods.grant(
                db, await user(db, alice), await plan(db, "standard"), days=10, reason="x", actor="maya", start="now"
            )
        assert refused.value.status_code == 409
        with pytest.raises(ValueError):
            await periods.grant(
                db, await user(db, alice), await plan(db, "standard"), days=91, reason="x", actor="maya", start="now"
            )
        await periods.grant(
            db,
            await user(db, alice),
            await plan(db, "standard"),
            days=10,
            reason="Goodwill",
            actor="maya",
            start="after_current",
        )
        await db.commit()

    first, second = await rows(alice)
    assert (first.id, second.starts_at, second.days) == (current.id, current.ends_at, 10)


async def test_an_open_ended_plan_replaces_an_earlier_one_and_can_end(alice: int) -> None:
    async with SessionLocal() as db:
        member = await user(db, alice)
        await periods.grant(
            db, member, await plan(db, "starter"), days=None, reason="Family", actor="system", start="now"
        )
        await periods.grant(
            db, member, await plan(db, "professional"), days=None, reason="Staff", actor="maya", start="after_current"
        )
        await db.commit()
        starter, professional = await plan(db, "starter"), await plan(db, "professional")

    async with SessionLocal() as db:
        member = await user(db, alice)
        assert (member.comp_plan_id, member.comp_reason, member.comp_granted_by) == (professional.id, "Staff", "maya")
        assert member.comp_granted_at is not None
        await periods.end_complimentary(db, member, reason="Left", actor="maya")
        await db.commit()
        with pytest.raises(HTTPException) as refused:
            await periods.end_complimentary(db, member, reason="Again", actor="maya")
        assert refused.value.status_code == 409

    async with SessionLocal() as db:
        member = await user(db, alice)
    assert (member.comp_plan_id, member.comp_reason, member.comp_granted_by, member.comp_granted_at) == (None,) * 4
    assert await rows(alice) == []
    granted = await events("complimentary.granted")
    assert [(e.source, e.actor, e.detail["plan"], e.detail["replaced_plan_id"]) for e in granted] == [
        ("system", "system", "starter", None),
        ("admin", "maya", "professional", starter.id),
    ]
    [ended] = await events("complimentary.ended")
    assert ended.detail == {"plan_id": professional.id, "reason": "Left"}


# ---------------- Changing the plan at no charge ----------------


async def test_changing_the_plan_rewrites_every_unexpired_period_and_keeps_its_basis(alice: int) -> None:
    async with SessionLocal() as db:
        past = await paid(db, alice, "starter", now=NOW - timedelta(days=40))
        current = await paid(db, alice, "starter")
        scheduled = await paid(db, alice, "starter")
        await db.commit()
        old_snapshot = current.snapshot

    async with SessionLocal() as db:
        changed = await periods.change_plan(
            db, await user(db, alice), await plan(db, "standard"), reason="Goodwill", actor="maya", now=NOW
        )
        await db.commit()
        standard = await plan(db, "standard")

    assert changed == 2
    found = {p.id: p for p in await rows(alice)}
    assert periods.snapshot(found[past.id]).code == "starter" and found[past.id].history == []
    for period_id in (current.id, scheduled.id):
        period = found[period_id]
        assert (period.plan_id, periods.snapshot(period).code) == (standard.id, "standard")
        assert period.charge_basis_paise == 50_000
        assert period.history == [
            {
                "at": NOW.isoformat(),
                "kind": "change",
                "by": "maya",
                "order_id": None,
                "snapshot_before": old_snapshot,
            }
        ]
    [event] = await events("period.plan_changed")
    assert event.detail == {
        "plan_id": standard.id,
        "plan": "standard",
        "reason": "Goodwill",
        "period_ids": [current.id, scheduled.id],
    }

    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as refused:
            await periods.change_plan(
                db, await user(db, alice), await plan(db, "standard"), reason="x", actor="maya", now=NOW + timedelta(90)
            )
    assert refused.value.status_code == 409


# ---------------- A `now` order (4.4) ----------------


async def professional_now(db: AsyncSession, user_id: int, replaced: PlanPeriod, *, now: datetime) -> PlanPeriod:
    target = await plan(db, "professional")
    return await periods.start_now(
        db,
        user_id,
        snapshot=periods.snapshot_of(target),
        days=30,
        basis_paise=target.price_paise,
        mode="live",
        order_id=await order(db, user_id),
        replaces_period_id=replaced.id,
        replaces_plan_id=replaced.plan_id,
        replaces_ends_at=replaced.ends_at,
        now=now,
    )


async def test_a_now_order_ends_the_current_period_and_moves_the_scheduled_one(alice: int) -> None:
    later = NOW + timedelta(days=12)
    async with SessionLocal() as db:
        current = await paid(db, alice, "starter")
        scheduled = await paid(db, alice, "standard")
        await db.commit()
        quoted_end = current.ends_at

    async with SessionLocal() as db:
        new = await professional_now(db, alice, current, now=later)
        await db.commit()

    found = {p.id: p for p in await rows(alice)}
    assert (found[current.id].starts_at, found[current.id].ends_at) == (NOW, later)
    assert found[current.id].history == [
        {
            "at": later.isoformat(),
            "kind": "ended_by_order",
            "by": None,
            "order_id": new.order_id,
            "ends_at_before": quoted_end.isoformat(),
        }
    ]
    assert (new.starts_at, new.ends_at, new.days, new.source, new.mode) == (
        later,
        period_end(later, 30),
        30,
        "payment",
        "live",
    )
    assert (new.charge_basis_paise, periods.snapshot(new).code) == (500_000, "professional")
    moved = found[scheduled.id]
    assert (moved.starts_at, moved.ends_at, periods.snapshot(moved).code) == (
        new.ends_at,
        period_end(new.ends_at, 30),
        "standard",
    )


async def change_quoted_period(db: AsyncSession, user_id: int, current: PlanPeriod, how: str) -> datetime:
    """Changes the period a `now` order was quoted against; returns when the order then arrives."""
    if how == "revoked":
        await periods.revoke(db, current, reason="Refunded", actor=None, source="admin", now=NOW + timedelta(days=1))
    elif how == "another plan":
        await periods.change_plan(
            db, await user(db, user_id), await plan(db, "standard"), reason="x", actor="maya", now=NOW + timedelta(1)
        )
    elif how == "replaced":
        await professional_now(db, user_id, current, now=NOW + timedelta(days=1))
    if how == "ended":
        return current.ends_at + timedelta(seconds=1)
    return NOW + timedelta(days=2)


@pytest.mark.parametrize("how", ["revoked", "ended", "another plan", "replaced"])
async def test_a_now_order_goes_to_review_when_its_period_changed(alice: int, how: str) -> None:
    async with SessionLocal() as db:
        current = await paid(db, alice, "starter")
        await paid(db, alice, "standard")
        quote = (current.id, current.plan_id, current.ends_at)
        arrives = await change_quoted_period(db, alice, current, how)
        await db.commit()
    before = state(await rows(alice))

    async with SessionLocal() as db:
        target = await plan(db, "professional")
        order_id = await order(db, alice)
        await db.commit()
        with pytest.raises(PeriodChanged, match="The plan this payment was priced against has changed."):
            await periods.start_now(
                db,
                alice,
                snapshot=periods.snapshot_of(target),
                days=30,
                basis_paise=target.price_paise,
                mode="live",
                order_id=order_id,
                replaces_period_id=quote[0],
                replaces_plan_id=quote[1],
                replaces_ends_at=quote[2],
                now=arrives,
            )

    assert state(await rows(alice)) == before


async def test_an_upgrade_on_the_last_day_costs_nearly_the_full_price(alice: int) -> None:
    """SEC-1: the credit for a Starter period with a day left and nothing used is at most 1/30 of what it was worth."""
    async with SessionLocal() as db:
        current = await paid(db, alice, "starter")
        target = await plan(db, "professional")
        await db.commit()
    last_day = current.ends_at - timedelta(days=1)

    credit = money.credit_paise(
        current.charge_basis_paise, starts_at=current.starts_at, ends_at=current.ends_at, now=last_day, used=Fraction(0)
    )

    assert money.now_charge(target.price_paise, credit) >= target.price_paise - current.charge_basis_paise // 30


# ---------------- A refunded `now` order gives the replaced period back (4.10) ----------------


async def test_a_refunded_now_order_gives_the_replaced_period_back_once(alice: int) -> None:
    later = NOW + timedelta(days=5)
    async with SessionLocal() as db:
        current = await paid(db, alice, "starter")
        scheduled = await paid(db, alice, "standard")
        new = await professional_now(db, alice, current, now=later)
        await db.commit()
        original_end = period_end(NOW, 30)

    refund_at = later + timedelta(days=1)
    async with SessionLocal() as db:
        new = await db.get_one(PlanPeriod, new.id)
        replaced = await db.get_one(PlanPeriod, current.id)
        assert new.order_id is not None
        await periods.revoke(db, new, reason="Refunded", actor=ADMIN_USERNAME, source="admin", now=refund_at)
        assert await periods.restore(
            db, replaced, order_id=new.order_id, actor=ADMIN_USERNAME, source="admin", now=refund_at
        )
        assert not await periods.restore(
            db, replaced, order_id=new.order_id, actor=ADMIN_USERNAME, source="admin", now=refund_at
        )
        await db.commit()

    found = {p.id: p for p in await rows(alice)}
    assert (found[current.id].starts_at, found[current.id].ends_at) == (NOW, original_end)
    assert [entry["kind"] for entry in found[current.id].history] == ["ended_by_order", "restored"]
    assert (found[scheduled.id].starts_at, found[scheduled.id].ends_at) == (original_end, period_end(original_end, 30))
    [restored] = await events("period.restored")
    assert restored.detail == {"period_id": current.id, "ends_at": original_end.isoformat()}


async def test_a_replaced_period_is_not_given_back_when_its_time_is_over_or_it_was_revoked(alice: int) -> None:
    later = NOW + timedelta(days=5)
    async with SessionLocal() as db:
        current = await paid(db, alice, "starter")
        new = await professional_now(db, alice, current, now=later)
        await db.commit()
    assert new.order_id is not None

    async with SessionLocal() as db:
        replaced = await db.get_one(PlanPeriod, current.id)
        too_late = replaced.ends_at + timedelta(days=40)
        assert not await periods.restore(db, replaced, order_id=new.order_id, actor=None, source="admin", now=too_late)
        assert not await periods.restore(db, replaced, order_id=new.order_id + 1, actor=None, source="admin", now=later)
        await periods.revoke(db, replaced, reason="x", actor=None, source="admin", now=later)
        assert not await periods.restore(db, replaced, order_id=new.order_id, actor=None, source="admin", now=later)


async def billing_events(user_id: int | None = None) -> int:
    async with SessionLocal() as db:
        query = select(func.count()).select_from(BillingEvent)
        return await db.scalar(query if user_id is None else query.where(BillingEvent.user_id == user_id)) or 0


async def test_a_user_who_never_ordered_leaves_no_billing_events(
    admin_client: ApiClient, create_user: CreateUser
) -> None:
    bob = await create_user("bob", login=False)
    assert bob.user_id is not None
    his, total = await billing_events(bob.user_id), await billing_events()
    assert his > 0  # created by the admin, his plan granted

    assert (await admin_client.delete(f"/api/admin/users/{bob.user_id}")).status_code == 204

    assert await billing_events() == total - his


async def test_a_user_with_an_order_keeps_their_billing_events(
    admin_client: ApiClient, create_user: CreateUser
) -> None:
    carol = await create_user("carol", login=False)
    assert carol.user_id is not None
    async with SessionLocal() as db:
        await order(db, carol.user_id)
        await db.commit()
        hers = set(await db.scalars(select(BillingEvent.id).where(BillingEvent.user_id == carol.user_id)))
    assert hers

    assert (await admin_client.delete(f"/api/admin/users/{carol.user_id}")).status_code == 204

    async with SessionLocal() as db:  # kept for the accounts, no longer tied to her
        kept = await db.scalars(select(BillingEvent).where(BillingEvent.id.in_(hers)))
        assert {(event.id, event.user_id) for event in kept} == {(event_id, None) for event_id in hers}


def test_a_snapshot_reads_back_as_it_was_written() -> None:
    snapshot = PlanSnapshot(
        plan_id=1,
        code="starter",
        name="Starter",
        features={"files": False, "images": False, "plugins": []},  # type: ignore[arg-type]
        limits={  # type: ignore[arg-type]
            "monthly_token_limit": 1,
            "rate_limit_per_min": 2,
            "max_upload_mb": 3,
            "max_documents": 4,
            "max_storage_mb": 5,
            "monthly_image_limit": 6,
            "vision_monthly_token_limit": 7,
        },
        restrict_models=True,
        chat_model_ids=[3],
        default_chat_model_id=3,
    )
    row = PlanPeriod(snapshot=snapshot.model_dump(mode="json"))
    assert periods.snapshot(row) == snapshot
    assert row.snapshot["limits"]["vision_monthly_token_limit"] == 7


def test_the_chain_stops_at_a_gap() -> None:
    """A period after a gap (one behind a test-mode period, once live keys run) isn't paid time yet."""
    day = timedelta(days=1)
    current = PlanPeriod(id=1, starts_at=NOW - day, ends_at=NOW + day, revoked_at=None)
    later = PlanPeriod(id=2, starts_at=NOW + 2 * day, ends_at=NOW + 3 * day, revoked_at=None)

    assert periods.chain([current, later], NOW) == [current]
    assert periods.paid_through([current, later], NOW) == NOW + day
    assert periods.next_start([current, later], NOW) == NOW + 3 * day  # never overlapping either
