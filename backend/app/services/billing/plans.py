"""Plans as users, the public pages and the admin see them, and the admin's plan editor (docs/briefs/plans-billing.md
3 and 7): the resolved model lists with an answers figure per model, the plugins, the cost estimate, the editor's
validation, applying an edit to current periods, and the audit of every change."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import BillingOrder, LLMModel, Plan, PlanPeriod, User, utcnow
from app.plugins import registry
from app.schemas_billing import (
    AdminPlan,
    PlanCreate,
    PlanFeatures,
    PlanInput,
    PlanModel,
    PlanPlugin,
    PlanRef,
    PlanSaved,
    PlanSnapshot,
    PlanUpdate,
    PublicPlan,
    check_plan,
)
from app.services import llm
from app.services.billing import audit, entitlements, money, periods
from app.services.billing.locks import lock_user

_INPUT_FIELDS = tuple(PlanInput.model_fields)


@dataclass(frozen=True)
class Catalog:
    """What every plan view needs once per request: the enabled chat models and the plugins' availability."""

    models: list[LLMModel]  # enabled chat models, by display name
    plugins: list[PlanPlugin]  # every registered plugin, in registry order


async def catalog(db: AsyncSession) -> Catalog:
    models = list(
        await db.scalars(
            select(LLMModel)
            .where(LLMModel.kind == "chat", LLMModel.enabled.is_(True))
            .order_by(LLMModel.display_name, LLMModel.id)
        )
    )
    plugins = []
    for plugin in registry.all_plugins():
        available = not registry.configured(plugin) and (await registry.state(db, plugin)).enabled
        plugins.append(PlanPlugin(id=plugin.id, name=plugin.name, available=available))
    return Catalog(models, plugins)


def ref(plan: Plan) -> PlanRef:
    return PlanRef(id=plan.id, code=plan.code, name=plan.name)


def snapshot_ref(snapshot: PlanSnapshot | dict[str, Any]) -> PlanRef:
    snap = snapshot if isinstance(snapshot, PlanSnapshot) else PlanSnapshot.model_validate(snapshot)
    return PlanRef(id=snap.plan_id, code=snap.code, name=snap.name)


def blended(model: LLMModel) -> float | None:
    """The model's blended price at the prices an answer is charged with (the model's own, else LiteLLM's cost map;
    llm.compute_cost), per million tokens priced from a thousand: a million at once would hit long-context tiers."""
    input_usd, output_usd = llm.compute_cost(model, 1000, 0), llm.compute_cost(model, 0, 1000)
    return money.blended_price(input_usd * 1000, output_usd * 1000)


def plan_model(model: LLMModel, allowance: int, features: PlanFeatures) -> PlanModel:
    price = blended(model)
    return PlanModel(
        id=model.id,
        display_name=model.display_name,
        allowance_rate=money.allowance_rate(price),
        answers=money.answers(allowance, price, files=features.files, images=features.images),
    )


def models_of(plan: Plan, cat: Catalog) -> list[LLMModel]:
    """The plan's enabled chat models: every one when unrestricted."""
    if not plan.restrict_models:
        return list(cat.models)
    ids = {model.id for model in plan.chat_models}
    return [model for model in cat.models if model.id in ids]


def default_model(plan: Plan, models: Sequence[LLMModel]) -> LLMModel | None:
    """The plan's default, else the server's default chat model when the plan has it."""
    by_id = {model.id: model for model in models}
    if plan.default_chat_model_id in by_id:
        return by_id[plan.default_chat_model_id]
    return next((model for model in models if model.is_default), None)


def public_plan(plan: Plan, cat: Catalog) -> PublicPlan:
    features = PlanFeatures.model_validate(plan.features)
    allowance = plan.monthly_token_limit
    models = models_of(plan, cat)
    default = default_model(plan, models)
    default_view = plan_model(default, allowance, features) if default is not None else None
    return PublicPlan(
        code=plan.code,
        name=plan.name,
        description=plan.description,
        price_paise=plan.price_paise,
        recommended=plan.recommended,
        features=features,
        plugins=cat.plugins,
        limits=periods.plan_limits(plan),
        chat_models=[plan_model(model, allowance, features) for model in models],
        all_chat_models=not plan.restrict_models,
        default_chat_model=default_view,
        answers_estimate=default_view.answers if default_view is not None else None,
    )


async def active_plans(db: AsyncSession) -> list[Plan]:
    return list(await db.scalars(select(Plan).where(Plan.status == "active").order_by(Plan.rank)))


# ---------------- The admin's plans ----------------


@dataclass(frozen=True)
class _Counts:
    current_users: int
    scheduled_periods: int
    complimentary_users: int
    used: bool


async def _counts(db: AsyncSession, plan_ids: Sequence[int]) -> dict[int, _Counts]:
    now = utcnow()
    live = (PlanPeriod.revoked_at.is_(None), PlanPeriod.mode.in_(periods.counting_modes()))
    current = dict(
        (
            await db.execute(
                select(PlanPeriod.plan_id, func.count(func.distinct(PlanPeriod.user_id)))
                .where(*live, PlanPeriod.starts_at <= now, PlanPeriod.ends_at > now)
                .group_by(PlanPeriod.plan_id)
            )
        )
        .tuples()
        .all()
    )
    scheduled = dict(
        (
            await db.execute(
                select(PlanPeriod.plan_id, func.count())
                .where(*live, PlanPeriod.starts_at > now)
                .group_by(PlanPeriod.plan_id)
            )
        )
        .tuples()
        .all()
    )
    comps = dict(
        (
            await db.execute(
                select(User.comp_plan_id, func.count())
                .where(User.comp_plan_id.is_not(None))
                .group_by(User.comp_plan_id)
            )
        )
        .tuples()
        .all()
    )
    used_by_period = set(await db.scalars(select(PlanPeriod.plan_id).where(PlanPeriod.plan_id.in_(plan_ids))))
    used_by_order = set(await db.scalars(select(BillingOrder.plan_id).where(BillingOrder.plan_id.in_(plan_ids))))
    return {
        plan_id: _Counts(
            current_users=current.get(plan_id, 0),
            scheduled_periods=scheduled.get(plan_id, 0),
            complimentary_users=comps.get(plan_id, 0),
            used=plan_id in used_by_period or plan_id in used_by_order or plan_id in comps,
        )
        for plan_id in plan_ids
    }


def _admin_plan(plan: Plan, counts: _Counts) -> AdminPlan:
    features = PlanFeatures.model_validate(plan.features)
    limits = periods.plan_limits(plan)
    return AdminPlan(
        id=plan.id,
        code=plan.code,
        name=plan.name,
        description=plan.description,
        price_paise=plan.price_paise,
        rank=plan.rank,
        status="retired" if plan.status == "retired" else "active",
        recommended=plan.recommended,
        features=features,
        limits=limits,
        chat_model_ids=sorted(model.id for model in plan.chat_models) if plan.restrict_models else None,
        default_chat_model_id=plan.default_chat_model_id,
        current_users=counts.current_users,
        scheduled_periods=counts.scheduled_periods,
        complimentary_users=counts.complimentary_users,
        used=counts.used,
        cost=money.cost_estimate(plan.price_paise, limits, images=features.images, inputs=money.cost_inputs()),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


async def admin_plans(db: AsyncSession) -> list[AdminPlan]:
    rows = list(await db.scalars(select(Plan).order_by(Plan.rank)))
    counts = await _counts(db, [plan.id for plan in rows])
    return [_admin_plan(plan, counts[plan.id]) for plan in rows]


async def admin_plan(db: AsyncSession, plan: Plan) -> AdminPlan:
    return _admin_plan(plan, (await _counts(db, [plan.id]))[plan.id])


def _plan_values(plan: Plan) -> dict[str, Any]:
    """The editable fields as PlanInput holds them, for the audit's before and after."""
    return {
        "name": plan.name,
        "description": plan.description,
        "price_paise": plan.price_paise,
        "rank": plan.rank,
        "status": plan.status,
        "recommended": plan.recommended,
        "features": dict(plan.features),
        "limits": periods.plan_limits(plan).model_dump(),
        "chat_model_ids": sorted(model.id for model in plan.chat_models) if plan.restrict_models else None,
        "default_chat_model_id": plan.default_chat_model_id,
    }


async def _validate(db: AsyncSession, values: dict[str, Any], plan_id: int | None) -> list[LLMModel]:
    """The checks that need the database (422 or 409); returns the plan's chat models."""
    registered = {plugin.id for plugin in registry.all_plugins()}
    unknown = [plugin for plugin in values["features"]["plugins"] if plugin not in registered]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown plugin: {', '.join(unknown)}.")
    ids = set(values["chat_model_ids"] or [])
    if values["default_chat_model_id"] is not None:
        ids.add(values["default_chat_model_id"])
    found = {
        model.id: model
        for model in await db.scalars(select(LLMModel).where(LLMModel.id.in_(ids), LLMModel.kind == "chat"))
    }
    missing = sorted(ids - found.keys())
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown chat model: {', '.join(map(str, missing))}."
        )
    others = list(await db.scalars(select(Plan).where(Plan.id != (plan_id or 0))))
    if any(other.rank == values["rank"] for other in others):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Another plan has rank {values['rank']}.")
    ladder = sorted(
        [(other.rank, other.price_paise) for other in others if other.status == "active"]
        + ([(values["rank"], values["price_paise"])] if values["status"] == "active" else [])
    )
    if any(higher[1] <= lower[1] for lower, higher in zip(ladder, ladder[1:], strict=False)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A higher plan must cost more.")
    return [found[model_id] for model_id in values["chat_model_ids"] or []]


def _assign(plan: Plan, values: dict[str, Any], models: list[LLMModel]) -> None:
    plan.name, plan.description = values["name"], values["description"]
    plan.price_paise, plan.rank, plan.status = values["price_paise"], values["rank"], values["status"]
    plan.recommended = values["recommended"]
    plan.features = dict(values["features"])
    for name, value in values["limits"].items():
        setattr(plan, name, value)
    plan.restrict_models = values["chat_model_ids"] is not None
    plan.chat_models = models
    plan.default_chat_model_id = values["default_chat_model_id"]


async def _clear_other_recommended(db: AsyncSession, plan: Plan) -> None:
    if plan.recommended:
        await db.execute(update(Plan).where(Plan.id != plan.id, Plan.recommended.is_(True)).values(recommended=False))


async def create_plan(db: AsyncSession, body: PlanCreate, *, actor: str) -> PlanSaved:
    if await db.scalar(select(Plan.id).where(Plan.code == body.code)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A plan with the code {body.code} exists.")
    values = body.model_dump(exclude={"code"})
    models = await _validate(db, values, None)
    plan = Plan(code=body.code)
    _assign(plan, values, models)
    db.add(plan)
    await db.flush()
    await _clear_other_recommended(db, plan)
    await audit.record_event(
        db, source="admin", type="plan.created", actor=actor, detail={"plan_id": plan.id, "code": plan.code, **values}
    )
    await db.commit()
    entitlements.invalidate_unlock()
    await db.refresh(plan)
    return PlanSaved(plan=await admin_plan(db, plan), applied_periods=0)


def _snapshot_days(period: PlanPeriod) -> int:
    """The length a period's snapshot is scaled for: test periods keep the whole allowance (4.2)."""
    return money.PERIOD_DAYS if period.mode == "test" else period.days


def _enabled_ids(models: Sequence[LLMModel]) -> set[int]:
    return {model.id for model in models}


def lowers(before: PlanSnapshot, after: PlanSnapshot, enabled: set[int]) -> bool:
    """Whether `after` gives less than `before`: a lower limit (0 is unlimited), a feature, plugin or chat model
    taken away."""
    for name in periods.LIMIT_NAMES:
        old, new = getattr(before.limits, name), getattr(after.limits, name)
        if new != 0 and (old == 0 or new < old):
            return True
    if (before.features.files and not after.features.files) or (before.features.images and not after.features.images):
        return True
    if set(before.features.plugins) - set(after.features.plugins):
        return True
    old_models = set(before.chat_model_ids) if before.restrict_models else enabled
    new_models = set(after.chat_model_ids) if after.restrict_models else enabled
    return bool(old_models - new_models)


async def update_plan(db: AsyncSession, plan: Plan, body: PlanUpdate, *, actor: str) -> PlanSaved:
    before = _plan_values(plan)
    given = body.model_dump(include=body.model_fields_set & set(_INPUT_FIELDS))
    values = {**before, **given}
    try:
        check_plan(values["status"], values["recommended"], values["chat_model_ids"], values["default_chat_model_id"])
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    models = await _validate(db, values, plan.id)
    now = utcnow()
    affected: list[PlanPeriod] = []
    if body.apply_to_current:
        affected = list(
            await db.scalars(
                select(PlanPeriod)
                .where(PlanPeriod.plan_id == plan.id, PlanPeriod.revoked_at.is_(None), PlanPeriod.ends_at > now)
                .order_by(PlanPeriod.user_id, PlanPeriod.starts_at)
            )
        )
    _assign(plan, values, models)
    await db.flush()
    await _clear_other_recommended(db, plan)
    if affected:
        enabled = _enabled_ids((await catalog(db)).models)
        lowered = {
            period.user_id
            for period in affected
            if period.source == "payment"
            and lowers(periods.snapshot(period), periods.snapshot_of(plan, _snapshot_days(period)), enabled)
        }
        if lowered and not body.confirm_reduction:
            await db.rollback()
            count = len(lowered)
            raise AppError(
                409,
                f"This lowers what {count} user{'' if count == 1 else 's'} paid for until their periods end.",
                "confirmation_required",
            )
        for user_id in sorted({period.user_id for period in affected}):
            await lock_user(db, user_id)
        for period in affected:
            periods.resnapshot(period, plan, kind="plan_applied", by=actor, now=now)
        await audit.record_event(
            db,
            source="admin",
            type="plan.applied_to_current",
            actor=actor,
            detail={"plan_id": plan.id, "count": len(affected), "period_ids": [period.id for period in affected]},
        )
    after = _plan_values(plan)
    changed = {name: {"before": before[name], "after": after[name]} for name in before if before[name] != after[name]}
    await audit.record_event(
        db, source="admin", type="plan.updated", actor=actor, detail={"plan_id": plan.id, "changed": changed}
    )
    await db.commit()
    entitlements.invalidate_unlock()
    await db.refresh(plan)
    return PlanSaved(plan=await admin_plan(db, plan), applied_periods=len(affected))


async def delete_plan(db: AsyncSession, plan: Plan, *, actor: str) -> None:
    used = await db.scalar(
        select(
            or_(
                exists().where(PlanPeriod.plan_id == plan.id),
                exists().where(BillingOrder.plan_id == plan.id),
                exists().where(User.comp_plan_id == plan.id),
            )
        )
    )
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, "This plan has been used. Retire it instead.")
    await audit.record_event(
        db, source="admin", type="plan.deleted", actor=actor, detail={"plan_id": plan.id, "code": plan.code}
    )
    await db.delete(plan)
    await db.commit()
    entitlements.invalidate_unlock()
