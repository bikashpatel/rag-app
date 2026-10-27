"""Startup sync: the admin account from .env, the initial model catalogue and plans, and giving existing users a plan
once (docs/briefs/plans-billing.md 4.11)."""

import logging
import os
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppState, LLMModel, Plan, PlanPeriod, User, utcnow
from app.security import hash_password, verify_password
from app.services.billing import entitlements, periods
from app.services.billing.money import financial_year, receipt_seq_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SeedModel:
    display_name: str
    litellm_model: str
    kind: str
    key_env: str
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None
    is_default: bool = False
    always_disabled: bool = False


_OPUS = "anthropic/claude-opus-5-5"
_SONNET = "anthropic/claude-sonnet-5-5"
_GPT_MINI = "openai/gpt-5.4-mini"
_VISION_SEEDED = "vision_seeded"  # app_state key: the vision model was seeded (or one existed) once
_GPT_IMAGE = "openai/gpt-image-2"
_IMAGE_SEEDED = "image_seeded"  # app_state key: the image model was seeded (or one existed) once
_HAIKU = "anthropic/claude-haiku-5-5"
_PLANS_SEEDED = "plans_seeded"  # app_state key: the plans were seeded (or some existed) once
_PLANS_ROLLOUT = "plans_rollout"  # app_state key: existing users got the open-ended Professional plan once
_ROLLOUT_PLAN = "professional"
_ROLLOUT_REASON = "Existing account when plans were introduced"

_SEED_MODELS = (
    _SeedModel("Claude Opus 5.5", _OPUS, "chat", "ANTHROPIC_API_KEY", 4.0, 20.0),
    _SeedModel("Claude Sonnet 5.5", _SONNET, "chat", "ANTHROPIC_API_KEY", 2.0, 10.0),
    _SeedModel("Claude Haiku 5.5", _HAIKU, "chat", "ANTHROPIC_API_KEY", 0.10, 0.50),
    _SeedModel("GPT-5.5", "openai/gpt-5.5", "chat", "OPENAI_API_KEY"),
    _SeedModel("GPT-5.4 mini", _GPT_MINI, "chat", "OPENAI_API_KEY"),
    _SeedModel(
        "OpenAI text-embedding-3-small", "openai/text-embedding-3-small", "embedding", "OPENAI_API_KEY", is_default=True
    ),
    _SeedModel(
        "OpenAI text-embedding-3-large",
        "openai/text-embedding-3-large",
        "embedding",
        "OPENAI_API_KEY",
        always_disabled=True,
    ),
)


def _has_key(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


async def _sync_admin(db: AsyncSession) -> None:
    username = settings.admin_username
    admin = await db.scalar(select(User).where(User.username == username))
    if admin is None:
        db.add(
            User(
                username=username,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
                is_active=True,
            )
        )
        logger.info("Created the admin account %r", username)
    else:
        # Sessions issued before a promotion or a password change must not carry admin rights.
        if not admin.is_admin or not verify_password(settings.admin_password, admin.password_hash):
            admin.password_hash = hash_password(settings.admin_password)
            admin.token_version += 1
            logger.info("Updated the admin account %r from .env; its existing sessions were signed out", username)
        admin.is_admin = True
        admin.is_active = True

    former_admins = await db.scalars(select(User).where(User.is_admin.is_(True), User.username != username))
    for user in former_admins:
        user.is_admin = False
        user.is_active = False
        user.token_version += 1
        logger.warning(
            "User %r was an admin but ADMIN_USERNAME is now %r: demoted and deactivated", user.username, username
        )


async def _seed_models(db: AsyncSession) -> None:
    if await db.scalar(select(func.count()).select_from(LLMModel)):
        return
    default_chat = _GPT_MINI if _has_key("OPENAI_API_KEY") and not _has_key("ANTHROPIC_API_KEY") else _OPUS
    for seed in _SEED_MODELS:
        db.add(
            LLMModel(
                display_name=seed.display_name,
                litellm_model=seed.litellm_model,
                kind=seed.kind,
                enabled=_has_key(seed.key_env) and not seed.always_disabled,
                is_default=seed.is_default or seed.litellm_model == default_chat,
                input_cost_per_mtok=seed.input_cost_per_mtok,
                output_cost_per_mtok=seed.output_cost_per_mtok,
            )
        )
    logger.info("Seeded %d models", len(_SEED_MODELS))


async def _seed_vision_model(db: AsyncSession) -> None:
    """Add the default vision model once per database (an existing one gets it on its next start), unless a vision
    model exists. Recorded in app_state, so a vision model the admin deletes isn't added again."""
    if await db.get(AppState, _VISION_SEEDED) is not None:
        return
    seeded = "existing"
    if await db.scalar(select(LLMModel.id).where(LLMModel.kind == "vision").limit(1)) is None:
        if _has_key("OPENAI_API_KEY") and not _has_key("ANTHROPIC_API_KEY"):
            # LiteLLM's model map prices it and lists it as reading images.
            model = LLMModel(
                display_name="GPT-5.4 mini (vision)",
                litellm_model=_GPT_MINI,
                kind="vision",
                enabled=True,
                is_default=True,
            )
        else:
            model = LLMModel(
                display_name="Claude Sonnet 5.5 (vision)",
                litellm_model=_SONNET,
                kind="vision",
                enabled=_has_key("ANTHROPIC_API_KEY"),
                is_default=True,
                input_cost_per_mtok=2.0,
                output_cost_per_mtok=10.0,
            )
        db.add(model)
        seeded = model.litellm_model
        logger.info("Seeded the vision model %r", model.display_name)
    db.add(AppState(key=_VISION_SEEDED, value=seeded))
    try:
        await db.commit()
    except IntegrityError:  # another backend starting at the same time seeded it first
        await db.rollback()
        logger.info("The vision model was seeded by another process")


async def _seed_image_model(db: AsyncSession) -> None:
    """Add GPT Image 2 as the active image model once per database (an existing one gets it on its next start), unless
    an image model exists; enabled only when OPENAI_API_KEY is set. Recorded in app_state, so an image model the admin
    deletes isn't added again. Prices come from LiteLLM's model map."""
    if await db.get(AppState, _IMAGE_SEEDED) is not None:
        return
    seeded = "existing"
    if await db.scalar(select(LLMModel.id).where(LLMModel.kind == "image").limit(1)) is None:
        db.add(
            LLMModel(
                display_name="GPT Image 2",
                litellm_model=_GPT_IMAGE,
                kind="image",
                enabled=_has_key("OPENAI_API_KEY"),
                is_default=True,
            )
        )
        seeded = _GPT_IMAGE
        logger.info("Seeded the image model 'GPT Image 2'")
    db.add(AppState(key=_IMAGE_SEEDED, value=seeded))
    try:
        await db.commit()
    except IntegrityError:  # another backend starting at the same time seeded it first
        await db.rollback()
        logger.info("The image model was seeded by another process")


@dataclass(frozen=True)
class _SeedPlan:
    code: str
    name: str
    description: str
    price_paise: int
    rank: int
    files: bool
    images: bool
    limits: tuple[int, int, int, int, int, int, int]  # periods.LIMIT_NAMES
    recommended: bool = False
    plugins: tuple[str, ...] = ()
    chat_models: tuple[str, ...] | None = None  # litellm_model of each; None: every enabled chat model
    default_chat_model: str | None = None


# The three plans of plans-billing 3.2; the limits are per period (0 = unlimited).
_SEED_PLANS = (
    _SeedPlan(
        "starter",
        "Starter",
        "Ask questions across your documents, for everyday use.",
        50_000,
        10,
        files=False,
        images=False,
        limits=(500_000, 10, 10, 50, 200, 30, 150_000),
        chat_models=(_HAIKU, _GPT_MINI),
        default_chat_model=_HAIKU,
    ),
    _SeedPlan(
        "standard",
        "Standard",
        "Adds files from chat and Claude Sonnet for regular work.",
        200_000,
        20,
        files=True,
        images=False,
        limits=(2_000_000, 20, 25, 200, 1000, 30, 500_000),
        recommended=True,
        chat_models=(_HAIKU, _GPT_MINI, _SONNET),
        default_chat_model=_SONNET,
    ),
    _SeedPlan(
        "professional",
        "Professional",
        "Everything: files, images and Gmail, with every model.",
        500_000,
        30,
        files=True,
        images=True,
        limits=(5_000_000, 30, 50, 500, 3000, 60, 1_000_000),
        plugins=("gmail",),
    ),
)


async def _seed_plans(db: AsyncSession) -> None:
    """The three plans, once per database, when there are none; their model lists from the chat models that exist."""
    if await db.get(AppState, _PLANS_SEEDED) is not None:
        return
    seeded = "existing"
    if await db.scalar(select(func.count()).select_from(Plan)) == 0:
        chat_models = await db.scalars(select(LLMModel).where(LLMModel.kind == "chat"))
        chat = {model.litellm_model: model for model in chat_models}
        for seed in _SEED_PLANS:
            limits = dict(zip(periods.LIMIT_NAMES, seed.limits, strict=True))
            limits["max_upload_mb"] = min(limits["max_upload_mb"], settings.max_upload_mb_hard)
            models = [chat[name] for name in seed.chat_models or () if name in chat]
            default = chat.get(seed.default_chat_model or "")
            db.add(
                Plan(
                    code=seed.code,
                    name=seed.name,
                    description=seed.description,
                    price_paise=seed.price_paise,
                    rank=seed.rank,
                    status="active",
                    recommended=seed.recommended,
                    features={"files": seed.files, "images": seed.images, "plugins": list(seed.plugins)},
                    **limits,
                    restrict_models=seed.chat_models is not None,
                    chat_models=models,
                    default_chat_model_id=default.id if default in models else None,
                )
            )
        seeded = ",".join(seed.code for seed in _SEED_PLANS)
    db.add(AppState(key=_PLANS_SEEDED, value=seeded))
    try:
        await db.commit()
    except IntegrityError:  # another backend starting at the same time seeded them first
        await db.rollback()
        logger.info("The plans were seeded by another process")
        return
    entitlements.invalidate_unlock()
    if seeded != "existing":
        logger.info("Seeded the plans %s", ", ".join(seed.name for seed in _SEED_PLANS))


async def _seed_receipt_counters(db: AsyncSession) -> None:
    """The receipt number counters of the current and the next financial year, in both modes, when missing (a receipt
    then only ever updates its row)."""
    now = utcnow()
    years = {financial_year(now), financial_year(now + timedelta(days=366))}
    keys = {receipt_seq_key(mode, year) for mode in ("live", "test") for year in years}
    existing = set(await db.scalars(select(AppState.key).where(AppState.key.in_(keys))))
    for key in sorted(keys - existing):
        db.add(AppState(key=key, value="0"))
    try:
        await db.commit()
    except IntegrityError:  # another backend starting at the same time added them
        await db.rollback()


async def _rollout_plans(db: AsyncSession) -> None:
    """Once per database: every active non-admin user without a period or an open-ended plan gets an open-ended
    complimentary Professional plan (4.11). The admin can grant inactive users a plan when reactivating them."""
    if await db.get(AppState, _PLANS_ROLLOUT) is not None:
        return
    plan = await db.scalar(select(Plan).where(Plan.code == _ROLLOUT_PLAN))
    if plan is None:
        logger.warning("No %r plan to give existing users: they have no plan until the admin grants one", _ROLLOUT_PLAN)
        return
    users = list(
        await db.scalars(
            select(User)
            .where(
                User.is_admin.is_(False),
                User.is_active.is_(True),
                User.comp_plan_id.is_(None),
                User.id.not_in(select(PlanPeriod.user_id)),
            )
            .order_by(User.id)
        )
    )
    for user in users:
        await periods.grant(db, user, plan, days=None, reason=_ROLLOUT_REASON, actor="system", start="now")
    db.add(AppState(key=_PLANS_ROLLOUT, value=str(len(users))))
    try:
        await db.commit()
    except IntegrityError:  # another backend starting at the same time rolled out first
        await db.rollback()
        logger.info("The plans were rolled out by another process")
        return
    logger.info("Gave %d existing user(s) the open-ended %s plan", len(users), plan.name)


async def bootstrap(db: AsyncSession) -> None:
    """Idempotent; runs on every backend start."""
    await _sync_admin(db)
    await _seed_models(db)
    await db.commit()
    await _seed_vision_model(db)
    await _seed_image_model(db)
    await _seed_plans(db)
    await _seed_receipt_counters(db)
    await _rollout_plans(db)
