"""Plan periods (docs/briefs/plans-billing.md 4.2, 4.4, 4.8 and 4.10): their dates, snapshots and chains, and every
change to them. Each change takes the user's row lock first (locks.lock_user) and leaves the commit to the caller."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    BillingDispute,
    BillingEvent,
    BillingOrder,
    BillingPayment,
    BillingRefund,
    Plan,
    PlanPeriod,
    User,
    utcnow,
)
from app.schemas import UserLimits
from app.schemas_billing import PlanFeatures, PlanSnapshot
from app.services.billing import audit
from app.services.billing.locks import lock_user
from app.services.billing.money import IST, PERIOD_DAYS, ist_date, scaled_limits

TEST_PERIOD_DAYS = 1  # a period paid with test keys
MAX_GRANT_DAYS = 90
LIMIT_NAMES: tuple[str, ...] = tuple(UserLimits.model_fields)


class PeriodChanged(Exception):
    """A `now` order can't apply: the period it was priced against is no longer the user's current one as quoted."""


def period_end(start: datetime, days: int) -> datetime:
    """The first midnight IST (18:30 UTC) at or after `start` + `days`: "valid through" is then a whole IST day."""
    end = (start + timedelta(days=days)).astimezone(IST)
    midnight = end.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight if midnight == end else midnight + timedelta(days=1)).astimezone(start.tzinfo)


def valid_through(ends_at: datetime) -> date:
    """The IST date of the last day with access."""
    return ist_date(ends_at - timedelta(seconds=1))


def counting_modes() -> tuple[str, ...]:
    """Periods of these modes count: test-mode ones only while the server runs test keys."""
    return ("live", "offline", "none", "test") if settings.razorpay_mode == "test" else ("live", "offline", "none")


def plan_limits(plan: Plan) -> UserLimits:
    return UserLimits(**{name: getattr(plan, name) for name in LIMIT_NAMES})


def snapshot_of(plan: Plan, days: int = PERIOD_DAYS) -> PlanSnapshot:
    """What a period of `days` gets of the plan now: allowances scaled for rows shorter than 30 days (4.2)."""
    return PlanSnapshot(
        plan_id=plan.id,
        code=plan.code,
        name=plan.name,
        features=PlanFeatures.model_validate(plan.features),
        limits=scaled_limits(plan_limits(plan), days),
        restrict_models=plan.restrict_models,
        chat_model_ids=sorted(model.id for model in plan.chat_models) if plan.restrict_models else [],
        default_chat_model_id=plan.default_chat_model_id,
    )


def snapshot(period: PlanPeriod) -> PlanSnapshot:
    return PlanSnapshot.model_validate(period.snapshot)


async def user_periods(db: AsyncSession, user_id: int) -> list[PlanPeriod]:
    """The user's periods that count under the current keys, revoked ones included, in order."""
    rows = await db.scalars(
        select(PlanPeriod)
        .where(PlanPeriod.user_id == user_id, PlanPeriod.mode.in_(counting_modes()))
        .order_by(PlanPeriod.starts_at, PlanPeriod.id)
    )
    return list(rows)


def current_period(periods: Sequence[PlanPeriod], now: datetime) -> PlanPeriod | None:
    return next((p for p in periods if p.revoked_at is None and p.starts_at <= now < p.ends_at), None)


def chain(periods: Sequence[PlanPeriod], now: datetime) -> list[PlanPeriod]:
    """The current period and the unbroken run of periods after it; empty without a current period."""
    current = current_period(periods, now)
    if current is None:
        return []
    run = [current]
    for period in periods:
        if period.revoked_at is None and period.starts_at > current.starts_at:
            if period.starts_at > run[-1].ends_at:
                break
            run.append(period)
    return run


def paid_through(periods: Sequence[PlanPeriod], now: datetime) -> datetime | None:
    """The end of the chain from the current period; None without one."""
    run = chain(periods, now)
    return run[-1].ends_at if run else None


def next_start(periods: Sequence[PlanPeriod], now: datetime) -> datetime:
    """Where a new period starts: now, or when the last period still to end ends (the paid time), so periods never
    overlap."""
    return max([now, *(p.ends_at for p in periods if p.revoked_at is None and p.ends_at > now)])


def _source(actor: str) -> str:
    return "system" if actor == "system" else "admin"


def _history(period: PlanPeriod, now: datetime, kind: str, by: str | None, **detail: Any) -> None:
    period.history = [*period.history, {"at": now.isoformat(), "kind": kind, "by": by, **detail}]


async def add_period(
    db: AsyncSession,
    user_id: int,
    *,
    snapshot: PlanSnapshot,
    days: int,
    source: Literal["payment", "comp"],
    mode: Literal["test", "live", "offline", "none"],
    basis_paise: int,
    order_id: int | None = None,
    reason: str | None = None,
    granted_by: str | None = None,
    now: datetime | None = None,
) -> PlanPeriod:
    """A period starting at max(now, paid through): now without a current period, else after the paid time."""
    now = now or utcnow()
    await lock_user(db, user_id)
    start = next_start(await user_periods(db, user_id), now)
    period = PlanPeriod(
        user_id=user_id,
        plan_id=snapshot.plan_id,
        starts_at=start,
        ends_at=period_end(start, days),
        days=days,
        source=source,
        mode=mode,
        order_id=order_id,
        snapshot=snapshot.model_dump(mode="json"),
        charge_basis_paise=basis_paise,
        history=[],
        reason=reason,
        granted_by=granted_by,
    )
    db.add(period)
    await db.flush()
    return period


async def rechain(db: AsyncSession, user_id: int, now: datetime) -> None:
    """After a current or scheduled period was revoked, ended early or restored: every later period moves to start at
    max(now, the previous end), its end recomputed from its days. The current period and past ones stay."""
    await db.flush()
    current_seen, previous_end = False, now
    for period in await user_periods(db, user_id):
        if period.revoked_at is not None or period.ends_at <= now:
            continue
        if not current_seen and period.starts_at <= now:
            current_seen = True
        elif period.starts_at != (start := max(now, previous_end)):
            period.starts_at, period.ends_at = start, period_end(start, period.days)
        previous_end = period.ends_at
    await db.flush()


async def grant(
    db: AsyncSession,
    user: User,
    plan: Plan,
    *,
    days: int | None,
    reason: str,
    actor: str,
    start: Literal["now", "after_current"],
    now: datetime | None = None,
) -> None:
    """A complimentary plan (4.8). With `days` (1 to 90): rows of at most 30 days from now (409 when the user has a
    current period) or after the paid time, the last one shorter with scaled allowances. Without: the open-ended plan,
    replacing an earlier one (`start` is ignored)."""
    now = now or utcnow()
    await lock_user(db, user.id)
    if days is None:
        replaced = user.comp_plan_id
        user.comp_plan_id, user.comp_reason, user.comp_granted_by, user.comp_granted_at = plan.id, reason, actor, now
        await audit.record_event(
            db,
            source=_source(actor),
            type="complimentary.granted",
            actor=actor,
            user_id=user.id,
            detail={"plan_id": plan.id, "plan": plan.code, "reason": reason, "replaced_plan_id": replaced},
        )
        return
    if not 1 <= days <= MAX_GRANT_DAYS:
        raise ValueError(f"A dated grant lasts 1 to {MAX_GRANT_DAYS} days")
    if start == "now" and current_period(await user_periods(db, user.id), now) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The user already has a current period: start the grant after it.",
        )
    rows = [PERIOD_DAYS] * (days // PERIOD_DAYS) + ([days % PERIOD_DAYS] if days % PERIOD_DAYS else [])
    added = [
        await add_period(
            db,
            user.id,
            snapshot=snapshot_of(plan, row),
            days=row,
            source="comp",
            mode="none",
            basis_paise=0,
            reason=reason,
            granted_by=actor,
            now=now,
        )
        for row in rows
    ]
    await audit.record_event(
        db,
        source=_source(actor),
        type="period.granted",
        actor=actor,
        user_id=user.id,
        detail={
            "plan_id": plan.id,
            "plan": plan.code,
            "days": days,
            "start": start,
            "reason": reason,
            "period_ids": [period.id for period in added],
        },
    )


async def end_complimentary(db: AsyncSession, user: User, *, reason: str, actor: str) -> None:
    """Ends the open-ended plan (409 without one)."""
    await lock_user(db, user.id)
    if user.comp_plan_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The user has no open-ended plan.")
    ended = user.comp_plan_id
    user.comp_plan_id = user.comp_reason = user.comp_granted_by = user.comp_granted_at = None
    await audit.record_event(
        db,
        source=_source(actor),
        type="complimentary.ended",
        actor=actor,
        user_id=user.id,
        detail={"plan_id": ended, "reason": reason},
    )


def resnapshot(
    period: PlanPeriod, plan: Plan, *, kind: Literal["change", "plan_applied"], by: str, now: datetime
) -> None:
    """The period gets the plan's snapshot now (scaled for a short row), keeping its basis; the old one goes into its
    history."""
    before = period.snapshot
    period.plan_id = plan.id
    days = PERIOD_DAYS if period.mode == "test" else period.days  # test periods keep the whole allowance (4.2)
    period.snapshot = snapshot_of(plan, days).model_dump(mode="json")
    _history(period, now, kind, by, order_id=None, snapshot_before=before)


async def change_plan(
    db: AsyncSession, user: User, plan: Plan, *, reason: str, actor: str, now: datetime | None = None
) -> int:
    """Moves every unexpired period of the user to the plan at no charge (4.8); 409 without one. Returns how many."""
    now = now or utcnow()
    await lock_user(db, user.id)
    periods = [p for p in await user_periods(db, user.id) if p.revoked_at is None and p.ends_at > now]
    if not periods:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The user has no current or scheduled period.")
    for period in periods:
        resnapshot(period, plan, kind="change", by=actor, now=now)
    await audit.record_event(
        db,
        source=_source(actor),
        type="period.plan_changed",
        actor=actor,
        user_id=user.id,
        detail={"plan_id": plan.id, "plan": plan.code, "reason": reason, "period_ids": [p.id for p in periods]},
    )
    return len(periods)


async def revoke(
    db: AsyncSession, period: PlanPeriod, *, reason: str, actor: str | None, source: str, now: datetime | None = None
) -> None:
    """Revokes the period (once); when it hadn't ended, the periods after it move up (re-chained)."""
    now = now or utcnow()
    await lock_user(db, period.user_id)
    if period.revoked_at is not None:
        return
    period.revoked_at, period.revoked_reason = now, reason
    if period.ends_at > now:
        await rechain(db, period.user_id, now)
    await audit.record_event(
        db,
        source=source,
        type="period.revoked",
        actor=actor,
        user_id=period.user_id,
        order_id=period.order_id,
        detail={"period_id": period.id, "plan": snapshot(period).code, "reason": reason},
    )


async def start_now(
    db: AsyncSession,
    user_id: int,
    *,
    snapshot: PlanSnapshot,
    days: int,
    basis_paise: int,
    mode: Literal["test", "live"],
    order_id: int,
    replaces_period_id: int | None,
    replaces_plan_id: int | None,
    replaces_ends_at: datetime | None,
    now: datetime | None = None,
) -> PlanPeriod:
    """Applies a `now` order (4.4): the replaced period ends now, a new one starts now with a fresh allowance, and the
    periods scheduled after it move after the new one. Raises PeriodChanged (nothing changed) unless the replaced
    period is still current, unrevoked, with the quoted plan and end."""
    now = now or utcnow()
    await lock_user(db, user_id)
    current = current_period(await user_periods(db, user_id), now)
    if (
        current is None
        or current.id != replaces_period_id
        or current.plan_id != replaces_plan_id
        or current.ends_at != replaces_ends_at
    ):
        raise PeriodChanged("The plan this payment was priced against has changed.")
    _history(current, now, "ended_by_order", None, order_id=order_id, ends_at_before=current.ends_at.isoformat())
    current.ends_at = now
    period = PlanPeriod(
        user_id=user_id,
        plan_id=snapshot.plan_id,
        starts_at=now,
        ends_at=period_end(now, days),
        days=days,
        source="payment",
        mode=mode,
        order_id=order_id,
        snapshot=snapshot.model_dump(mode="json"),
        charge_basis_paise=basis_paise,
        history=[],
    )
    db.add(period)
    await rechain(db, user_id, now)
    return period


def ended_by_order(period: PlanPeriod, order_id: int | None = None) -> bool:
    """Whether the period's last change is a `now` order ending it (that order's, when given)."""
    last = period.history[-1] if period.history else {}
    return last.get("kind") == "ended_by_order" and (order_id is None or last.get("order_id") == order_id)


async def _payment_reversed(db: AsyncSession, period: PlanPeriod) -> bool:
    """Whether the payment that bought the period was refunded in full, lost a dispute, or had its access ended."""
    if period.order_id is None or period.source != "payment":
        return False
    payment = await db.scalar(
        select(BillingPayment).where(BillingPayment.order_id == period.order_id, BillingPayment.applied_at.is_not(None))
    )
    if payment is None:
        return False
    if payment.status in ("refunded", "disputed"):
        return True
    refunds = select(func.count()).where(
        BillingRefund.payment_id == payment.id, BillingRefund.access_ended_at.is_not(None)
    )
    disputes = select(func.count()).where(
        BillingDispute.payment_id == payment.id, BillingDispute.access_ended_at.is_not(None)
    )
    return bool(await db.scalar(refunds) or await db.scalar(disputes))


async def restore(
    db: AsyncSession, period: PlanPeriod, *, order_id: int, actor: str | None, source: str, now: datetime | None = None
) -> bool:
    """A full refund of a `now` order (4.10), after its own period was revoked: the period it replaced gets its end
    back when that is still ahead, the period is unrevoked, its last change is this order's and its own payment wasn't
    refunded or charged back; later periods re-chain. False (nothing changed) otherwise."""
    now = now or utcnow()
    await lock_user(db, period.user_id)
    if period.revoked_at is not None or not ended_by_order(period, order_id) or await _payment_reversed(db, period):
        return False
    last = period.history[-1]
    before = datetime.fromisoformat(last["ends_at_before"])
    if before <= now:
        return False
    _history(period, now, "restored", actor, order_id=order_id, ends_at_before=period.ends_at.isoformat())
    period.ends_at = before
    await rechain(db, period.user_id, now)
    await audit.record_event(
        db,
        source=source,
        type="period.restored",
        actor=actor,
        user_id=period.user_id,
        order_id=order_id,
        detail={"period_id": period.id, "ends_at": before},
    )
    return True


async def on_user_deleted(db: AsyncSession, user: User) -> None:
    """Before a user is deleted (4.12): their pending checkouts are cancelled, and their payment links at Razorpay
    after the request (best effort). A user who never had an order leaves no billing trail: their events (the admin's
    grants and edits) go with them. The caller commits."""
    from app.services.billing import checkout  # checkout builds on this module

    await checkout.cancel_user_links(db, user)
    if await db.scalar(select(BillingOrder.id).where(BillingOrder.user_id == user.id).limit(1)) is None:
        await db.execute(delete(BillingEvent).where(BillingEvent.user_id == user.id))
