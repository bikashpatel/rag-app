"""Chat models the caller may use."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services import image_tool, llm
from app.services.model_registry import list_chat_models_for_user, provider_of

router = APIRouter(prefix="/models", tags=["models"])


class ChatModel(BaseModel):
    id: int
    display_name: str
    provider: str
    is_default: bool
    can_create_files: bool  # CHAT_FILE_TOOLS is on and LiteLLM says the model supports function calling
    # CHAT_IMAGE_TOOLS is on, a usable image model is active and the model gets tools (docs/briefs/image-generation.md)
    can_create_images: bool = False


@router.get("")
async def list_models(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[ChatModel]:
    # Quotas and the monthly image limit are ignored here: the answer explains them.
    images = settings.chat_image_tools and await image_tool.usable_image_model(db) is not None
    models = []
    for model in await list_chat_models_for_user(db, user):
        tools = await llm.supports_tools(model)
        models.append(
            ChatModel(
                id=model.id,
                display_name=model.display_name,
                provider=provider_of(model.litellm_model),
                is_default=model.is_default,
                can_create_files=settings.chat_file_tools and tools,
                can_create_images=images and tools,
            )
        )
    return models
