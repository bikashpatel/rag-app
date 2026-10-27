"""What a user may do now: the one source of every plan check (docs/briefs/plans-billing.md 4.1 and 5). `load` gives
a request's Entitlements; `plan_access` the Me.plan the UI mirrors.

- The admin is exempt: everything, unlimited, the window is the UTC calendar month.
- Active by a period (a counting, unrevoked one containing now): its snapshot, merged with the user's overrides; the
  window is the period. A period takes precedence over an open-ended complimentary plan.
- Active by an open-ended plan: the plan's current values merged with the overrides; the window is the calendar month.
- Expired (had periods, none now) or none: no access, no features, no models; limits for information only (the last
  period's, or the cheapest active plan's)."""

import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models import BillingOrder, LLMModel, Plan, PlanPeriod, UsageEvent, User, utcnow
from app.schemas import UserLimits
from app.schemas_billing import PendingOrder, PlanAccess, PlanFeatures, PlanRef, PlanSnapshot, PlanUnlock
from app.services.billing import periods
from app.services.billing.money import format_ist_date, used_share

PlanState = Literal["exempt", "none", "active", "expired"]
NEW_PERIOD_SHARE = Fraction(9, 10)  # "Start a new period now" once this share of an allowance is used
_CACHE_SECONDS = 60.0


@dataclass(frozen=True)
class Limits:
    monthly_token_limit: int
    rate_limit_per_min: int
    max_upload_mb: int
    max_documents: int
    max_storage_mb: int
    monthly_image_limit: int
    vision_monthly_token_limit: int


@dataclass(frozen=True)
class Entitlements:
    user_id: int
    is_admin: bool
    state: PlanState
    access: bool
    plan_id: int | None
    plan_name: str | None
    period_id: int | None
    complimentary: bool
    open_ended: bool
    test_mode: bool
    files: bool
    images: bool
    plugins: frozenset[str]
    chat_model_ids: frozenset[int] | None  # effective set (plan ∩ user list); None: every enabled chat model
    plan_model_ids: frozenset[int] | None  # the plan's set alone: outside it → 402, inside but not effective → 403
    default_chat_model_id: int | None
    limits: Limits
    window_start: datetime
    window_end: datetime
    paid_through: datetime | None  # active by period: end of the chain; expired: when access ended
    valid_through: date | None  # IST date; None for open-ended plans and the admin


@dataclass(frozen=True)
class _CachedPlan:
    snapshot: PlanSnapshot  # the plan's values now, as a 30-day period would get them
    price_paise: int
    rank: int
    active: bool


@dataclass
class _Standing:
    run: list[PlanPeriod]  # the current period and the chain after it
    comp: _CachedPlan | None  # the open-ended plan
    last: PlanPeriod | None  # without a current period: the last one that gave access


_plans_cache: tuple[float, list[_CachedPlan]] | None = None


def invalidate_unlock() -> None:
    """Forget the cached plans (every plan change calls it)."""
    global _plans_cache
    _plans_cache = None


async def _plans(db: AsyncSession) -> list[_CachedPlan]:
    """Every plan, cheapest first (then by rank); cached in the process for 60 s."""
    global _plans_cache
    if _plans_cache is not None and time.monotonic() - _plans_cache[0] < _CACHE_SECONDS:
        return _plans_cache[1]
    rows = await db.scalars(select(Plan).order_by(Plan.price_paise, Plan.rank))
    plans = [
        _CachedPlan(periods.snapshot_of(plan), plan.price_paise, plan.rank, plan.status == "active") for plan in rows
    ]
    _plans_cache = (time.monotonic(), plans)
    return plans


def month_window(now: datetime) -> tuple[datetime, datetime]:
    """The UTC calendar month around `now`: the window of open-ended plans and the admin."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def _plugin_ids() -> frozenset[str]:
    from app.plugins import registry  # registry imports app.deps, which imports this module

    return frozenset(plugin.id for plugin in registry.all_plugins())


def _merge(limits: UserLimits, user: User) -> Limits:
    """The user's overrides where set, else the plan's; max_upload_mb capped at MAX_UPLOAD_MB_HARD (5.2)."""
    values = {name: getattr(limits, name) for name in periods.LIMIT_NAMES}
    values.update({name: own for name in periods.LIMIT_NAMES if (own := getattr(user, name)) is not None})
    values["max_upload_mb"] = min(values["max_upload_mb"], settings.max_upload_mb_hard)
    return Limits(**values)


def _access_end(period: PlanPeriod) -> datetime:
    return min(period.ends_at, period.revoked_at) if period.revoked_at is not None else period.ends_at


async def _standing(db: AsyncSession, user: User, user_periods: Sequence[PlanPeriod], now: datetime) -> _Standing:
    run = periods.chain(user_periods, now)
    comp = None
    if user.comp_plan_id is not None:
        comp = next((plan for plan in await _plans(db) if plan.snapshot.plan_id == user.comp_plan_id), None)
    gave_access = [p for p in user_periods if p.starts_at <= now and _access_end(p) > p.starts_at]
    last = max(gave_access, key=_access_end, default=None) if not run else None
    return _Standing(run, comp, last)


def _exempt(user: User, now: datetime) -> Entitlements:
    start, end = month_window(now)
    return Entitlements(
        user_id=user.id,
        is_admin=True,
        state="exempt",
        access=True,
        plan_id=None,
        plan_name=None,
        period_id=None,
        complimentary=False,
        open_ended=False,
        test_mode=False,
        files=True,
        images=True,
        plugins=_plugin_ids(),
        chat_model_ids=None,
        plan_model_ids=None,
        default_chat_model_id=None,
        limits=Limits(0, 0, settings.max_upload_mb_hard, 0, 0, 0, 0),
        window_start=start,
        window_end=end,
        paid_through=None,
        valid_through=None,
    )


def _build(user: User, standing: _Standing, fallback: UserLimits, now: datetime) -> Entitlements:
    current = standing.run[0] if standing.run else None
    if current is not None:
        snap, window = periods.snapshot(current), (current.starts_at, current.ends_at)
        through: datetime | None = standing.run[-1].ends_at
    elif standing.comp is not None:
        snap, window, through = standing.comp.snapshot, month_window(now), None
    elif standing.last is not None:
        last = periods.snapshot(standing.last)
        ended = _access_end(standing.last)
        return _no_access(user, "expired", last, _merge(last.limits, user), standing.last, ended, now)
    else:
        return _no_access(user, "none", None, _merge(fallback, user), None, None, now)
    plan_models = frozenset(snap.chat_model_ids) if snap.restrict_models else None
    models = plan_models
    if user.restrict_models:
        allowed = frozenset(model.id for model in user.allowed_models)
        models = allowed if plan_models is None else plan_models & allowed
    return Entitlements(
        user_id=user.id,
        is_admin=False,
        state="active",
        access=True,
        plan_id=snap.plan_id,
        plan_name=snap.name,
        period_id=current.id if current else None,
        complimentary=current is None or current.source == "comp",
        open_ended=current is None,
        test_mode=current is not None and current.mode == "test",
        files=snap.features.files,
        images=snap.features.images,
        plugins=frozenset(snap.features.plugins),
        chat_model_ids=models,
        plan_model_ids=plan_models,
        default_chat_model_id=snap.default_chat_model_id,
        limits=_merge(snap.limits, user),
        window_start=window[0],
        window_end=window[1],
        paid_through=through,
        valid_through=periods.valid_through(through) if through else None,
    )


def _no_access(
    user: User,
    state: PlanState,
    snap: PlanSnapshot | None,
    limits: Limits,
    last: PlanPeriod | None,
    ended: datetime | None,
    now: datetime,
) -> Entitlements:
    window = (last.starts_at, last.ends_at) if last is not None else month_window(now)
    return Entitlements(
        user_id=user.id,
        is_admin=False,
        state=state,
        access=False,
        plan_id=snap.plan_id if snap else None,
        plan_name=snap.name if snap else None,
        period_id=None,
        complimentary=False,
        open_ended=False,
        test_mode=False,
        files=False,
        images=False,
        plugins=frozenset(),
        chat_model_ids=frozenset(),
        plan_model_ids=frozenset(),
        default_chat_model_id=None,
        limits=limits,
        window_start=window[0],
        window_end=window[1],
        paid_through=ended,
        valid_through=periods.valid_through(ended) if ended else None,
    )


def _fallback(plans: Sequence[_CachedPlan]) -> UserLimits:
    """The cheapest active plan's limits (all 0 without one): what a user who never had a plan is shown."""
    cheapest = next((plan for plan in plans if plan.active), None)
    return cheapest.snapshot.limits if cheapest else UserLimits(**dict.fromkeys(periods.LIMIT_NAMES, 0))


async def load(db: AsyncSession, user: User, *, now: datetime | None = None) -> Entitlements:
    now = now or utcnow()
    if user.is_admin:
        return _exempt(user, now)
    standing = await _standing(db, user, await periods.user_periods(db, user.id), now)
    return _build(user, standing, _fallback(await _plans(db)), now)


async def load_many(db: AsyncSession, users: Sequence[User], *, now: datetime | None = None) -> dict[int, Entitlements]:
    """load() for many users at once (Admin → Users): one query for every user's periods."""
    now = now or utcnow()
    by_user: dict[int, list[PlanPeriod]] = {user.id: [] for user in users}
    ids = [user.id for user in users if not user.is_admin]
    if ids:
        rows = await db.scalars(
            select(PlanPeriod)
            .where(PlanPeriod.user_id.in_(ids), PlanPeriod.mode.in_(periods.counting_modes()))
            .order_by(PlanPeriod.starts_at, PlanPeriod.id)
        )
        for period in rows:
            by_user[period.user_id].append(period)
    fallback = _fallback(await _plans(db))
    return {
        user.id: _exempt(user, now)
        if user.is_admin
        else _build(user, await _standing(db, user, by_user[user.id], now), fallback, now)
        for user in users
    }


def require_access(ent: Entitlements) -> None:
    """402 plan_required or plan_expired without access."""
    if ent.access:
        return
    if ent.state == "expired" and ent.valid_through is not None:
        raise AppError(
            402,
            f"Your {ent.plan_name} plan ended on {format_ist_date(ent.valid_through)}. Renew it to continue; your "
            "chats and documents are kept.",
            "plan_expired",
            plan=ent.plan_name,
        )
    raise AppError(402, "Choose a plan to start using the assistant.", "plan_required")


def allows_plugin(ent: Entitlements, plugin_id: str) -> bool:
    return ent.is_admin or plugin_id in ent.plugins


def allows_model(ent: Entitlements, model_id: int) -> bool:
    """Whether the user may use this chat model (whether it is enabled is the caller's check)."""
    if not ent.access:
        return False
    return ent.chat_model_ids is None or model_id in ent.chat_model_ids


def user_limits(ent: Entitlements) -> UserLimits:
    return UserLimits(**asdict(ent.limits))


# ---------------- Unlocking features ----------------


def _cheapest(plans: Sequence[_CachedPlan], includes: str) -> str | None:
    """The name of the cheapest active plan that includes `includes`: "files", "images", "plugin:<id>", "model:<id>"
    (a chat model) or "model" (every chat model)."""
    for plan in plans:
        snap = plan.snapshot
        if not plan.active:
            continue
        if includes in ("files", "images"):
            found = getattr(snap.features, includes)
        elif includes.startswith("plugin:"):
            found = includes.removeprefix("plugin:") in snap.features.plugins
        elif includes.startswith("model:"):
            found = not snap.restrict_models or int(includes.removeprefix("model:")) in snap.chat_model_ids
        else:
            found = not snap.restrict_models
        if found:
            return snap.name
    return None


async def unlock(db: AsyncSession) -> PlanUnlock:
    """The cheapest active plan including each feature (from the plans cached for 60 s); `models`: the cheapest one
    with every chat model (plan_access narrows it to the user)."""
    plans = await _plans(db)
    return PlanUnlock(
        files=_cheapest(plans, "files"),
        images=_cheapest(plans, "images"),
        plugins={plugin_id: _cheapest(plans, f"plugin:{plugin_id}") for plugin_id in sorted(_plugin_ids())},
        models=_cheapest(plans, "model"),
    )


async def feature_error(db: AsyncSession, ent: Entitlements, feature: str, label: str) -> AppError:
    """402 feature_not_in_plan naming the cheapest active plan that has the feature: "files", "images",
    "plugin:<id>", or "model:<id>" (reported as feature "model"). `label` names it in the message ("Gmail")."""
    plan = _cheapest(await _plans(db), feature)
    detail = f"Your {ent.plan_name} plan" if ent.plan_name else "Your plan"
    detail += f" doesn't include {label}." + (f" It's included in {plan}." if plan else "")
    reported = "model" if feature.startswith("model:") else feature
    return AppError(402, detail, "feature_not_in_plan", feature=reported, plan=plan)


async def _models_unlock(db: AsyncSession, ent: Entitlements, plans: Sequence[_CachedPlan]) -> str | None:
    """The cheapest active plan above the user's whose models include an enabled chat model the plan withholds."""
    if ent.is_admin:
        return None
    enabled = set(await db.scalars(select(LLMModel.id).where(LLMModel.kind == "chat", LLMModel.enabled.is_(True))))
    withheld = enabled - ent.plan_model_ids if ent.plan_model_ids is not None else set()
    rank = next((plan.rank for plan in plans if plan.snapshot.plan_id == ent.plan_id), None) if ent.access else None
    for plan in plans:
        if not plan.active or (rank is not None and plan.rank <= rank):
            continue
        snap = plan.snapshot
        if withheld & (enabled if not snap.restrict_models else set(snap.chat_model_ids)):
            return snap.name
    return None


# ---------------- Me.plan ----------------


async def allowance_used(db: AsyncSession, ent: Entitlements) -> Fraction:
    """The largest share used of the user's limited allowances in the window (0..1): the chat allowance, images (when
    the plan includes them) and vision tokens."""
    chat, images, vision = (
        await db.execute(
            select(
                func.coalesce(func.sum(case((UsageEvent.kind == "chat", UsageEvent.allowance_tokens))), 0),
                func.count(case((UsageEvent.kind == "image", UsageEvent.id))),
                func.coalesce(func.sum(case((UsageEvent.kind == "vision", UsageEvent.total_tokens))), 0),
            ).where(
                UsageEvent.user_id == ent.user_id,
                UsageEvent.created_at >= ent.window_start,
                UsageEvent.created_at < ent.window_end,
            )
        )
    ).one()
    limits = ent.limits
    pairs = [(int(chat), limits.monthly_token_limit), (int(vision), limits.vision_monthly_token_limit)]
    if ent.images:
        pairs.append((int(images), limits.monthly_image_limit))
    return used_share(pairs)


def _ref(snap: PlanSnapshot) -> PlanRef:
    return PlanRef(id=snap.plan_id, code=snap.code, name=snap.name)


async def _pending_order(db: AsyncSession, user_id: int, now: datetime) -> PendingOrder | None:
    order = await db.scalar(
        select(BillingOrder)
        .where(BillingOrder.user_id == user_id, BillingOrder.status == "pending")
        .order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
        .limit(1)
    )
    if order is None:
        return None
    quoted = order.plan_snapshot
    return PendingOrder(
        public_id=order.public_id,
        kind="now" if order.kind == "now" else "period",
        plan=PlanRef(id=quoted["plan_id"], code=quoted["code"], name=quoted["name"]),
        amount_paise=order.amount_paise,
        created_at=order.created_at,
        expires_at=order.expires_at,
        pay_url=order.pay_url if order.expires_at > now else None,
    )


async def plan_access(db: AsyncSession, user: User, ent: Entitlements) -> PlanAccess:
    """Me.plan (5.9): what the UI gates on."""
    now = utcnow()
    plans = await _plans(db)
    unlocked = (await unlock(db)).model_copy(update={"models": await _models_unlock(db, ent, plans)})
    if ent.is_admin:
        return PlanAccess(
            state="exempt",
            access=True,
            plan=None,
            complimentary=False,
            open_ended=False,
            test_mode=False,
            period_start=None,
            period_end=None,
            paid_through=None,
            valid_through=None,
            next_plan=None,
            expiring_soon=False,
            ended_at=None,
            features=PlanFeatures(files=True, images=True, plugins=sorted(ent.plugins)),
            pending_order=None,
            can_start_new_period=False,
            unlock=unlocked,
        )
    standing = await _standing(db, user, await periods.user_periods(db, user.id), now)
    plan: PlanRef | None = None
    next_plan: PlanRef | None = None
    if standing.run:
        plan = _ref(periods.snapshot(standing.run[0]))
        if len(standing.run) > 1:
            next_plan = _ref(periods.snapshot(standing.run[1]))
        elif standing.comp is not None:
            next_plan = _ref(standing.comp.snapshot)
    elif standing.comp is not None:
        plan = _ref(standing.comp.snapshot)
    elif standing.last is not None:
        plan = _ref(periods.snapshot(standing.last))
    current = standing.run[0] if standing.run else None
    expiring = (
        current is not None
        and standing.comp is None
        and ent.paid_through is not None
        and ent.paid_through - now <= timedelta(days=settings.billing_reminder_days)
    )
    return PlanAccess(
        state=ent.state,
        access=ent.access,
        plan=plan,
        complimentary=ent.complimentary,
        open_ended=ent.open_ended,
        test_mode=ent.test_mode,
        period_start=current.starts_at if current else None,
        period_end=current.ends_at if current else None,
        paid_through=ent.paid_through,
        valid_through=ent.valid_through,
        next_plan=next_plan,
        expiring_soon=expiring,
        ended_at=ent.paid_through if ent.state == "expired" else None,
        features=PlanFeatures(files=ent.files, images=ent.images, plugins=sorted(ent.plugins)),
        pending_order=await _pending_order(db, user.id, now),
        can_start_new_period=current is not None and await allowance_used(db, ent) >= NEW_PERIOD_SHARE,
        unlock=unlocked,
    )
