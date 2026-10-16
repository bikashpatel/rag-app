"""Model lookup helpers shared by chat, ingestion and admin code."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LLMModel, User


def provider_of(litellm_model: str) -> str:
    return litellm_model.split("/", 1)[0] if "/" in litellm_model else "openai"


async def _active_model(db: AsyncSession, kind: str) -> LLMModel | None:
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.kind == kind, LLMModel.enabled.is_(True), LLMModel.is_default.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_active_embedding_model(db: AsyncSession) -> LLMModel | None:
    return await _active_model(db, "embedding")


async def get_active_vision_model(db: AsyncSession) -> LLMModel | None:
    """The enabled default vision model, which ingestion uses to read PDF pages; None turns that off."""
    return await _active_model(db, "vision")


async def get_active_image_model(db: AsyncSession) -> LLMModel | None:
    """The enabled default image model, which chat uses to generate images; None turns that off."""
    return await _active_model(db, "image")


async def list_chat_models_for_user(db: AsyncSession, user: User) -> list[LLMModel]:
    """Enabled chat models the user may use; the default first, then by name."""
    result = await db.execute(select(LLMModel).where(LLMModel.kind == "chat", LLMModel.enabled.is_(True)))
    models = list(result.scalars())
    if not user.is_admin and user.restrict_models:
        allowed = {m.id for m in user.allowed_models}
        models = [m for m in models if m.id in allowed]
    models.sort(key=lambda m: (not m.is_default, m.display_name.lower()))
    return models


async def resolve_chat_model(db: AsyncSession, user: User, model_id: int | None) -> LLMModel:
    """The model to use for a chat request. 400 if none is available, 403 if not allowed."""
    models = await list_chat_models_for_user(db, user)
    if model_id is None:
        if not models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No chat model is available to you. Ask the admin to enable one.",
            )
        return models[0]
    for model in models:
        if model.id == model_id:
            return model
    requested = await db.get(LLMModel, model_id)
    if requested is not None and requested.kind == "chat" and requested.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not allowed to use this model.")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This model is not available.")
