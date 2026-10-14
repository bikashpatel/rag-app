"""Startup sync: the admin account from .env and the initial model catalogue."""

import logging
import os
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import LLMModel, User
from app.security import hash_password, verify_password

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
_GPT_MINI = "openai/gpt-5.4-mini"

_SEED_MODELS = (
    _SeedModel("Claude Opus 5.5", _OPUS, "chat", "ANTHROPIC_API_KEY", 4.0, 20.0),
    _SeedModel("Claude Sonnet 5.5", "anthropic/claude-sonnet-5-5", "chat", "ANTHROPIC_API_KEY", 2.0, 10.0),
    _SeedModel("Claude Haiku 5.5", "anthropic/claude-haiku-5-5", "chat", "ANTHROPIC_API_KEY", 0.10, 0.50),
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


async def bootstrap(db: AsyncSession) -> None:
    """Idempotent; runs on every backend start."""
    await _sync_admin(db)
    await _seed_models(db)
    await db.commit()
