"""Chat models the caller may use."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.services import llm
from app.services.model_registry import list_chat_models_for_user, provider_of

router = APIRouter(prefix="/models", tags=["models"])


class ChatModel(BaseModel):
    id: int
    display_name: str
    provider: str
    is_default: bool
    can_create_files: bool  # CHAT_FILE_TOOLS is on and LiteLLM says the model supports function calling


@router.get("")
async def list_models(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[ChatModel]:
    return [
        ChatModel(
            id=model.id,
            display_name=model.display_name,
            provider=provider_of(model.litellm_model),
            is_default=model.is_default,
            can_create_files=settings.chat_file_tools and await llm.supports_tools(model),
        )
        for model in await list_chat_models_for_user(db, user)
    ]
