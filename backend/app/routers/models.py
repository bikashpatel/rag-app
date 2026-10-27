"""Chat models the caller may use."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import current_entitlements, get_entitled_user
from app.models import User
from app.services import image_tool, llm
from app.services.billing import plans
from app.services.billing.money import allowance_rate
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
    # LiteLLM says the model supports function calling and it hasn't rejected tools since the backend started
    # (llm.supports_tools): plugin tools such as Gmail can be offered to it
    supports_tools: bool = False
    # ×rate against the chat allowance (plans-billing 3.3): the blended price ÷ $3.20 per million; None: no price, it
    # counts plain tokens
    allowance_rate: float | None


@router.get("")
async def list_models(
    request: Request, user: User = Depends(get_entitled_user), db: AsyncSession = Depends(get_db)
) -> list[ChatModel]:
    """The plan's models the user may use, the default first (402 without access). can_create_files and
    can_create_images are what the server can do; the client combines them with Me.plan.features."""
    # Quotas and the image allowance are ignored here: the answer explains them.
    images = settings.chat_image_tools and await image_tool.usable_image_model(db) is not None
    models = []
    for index, model in enumerate(await list_chat_models_for_user(db, user, current_entitlements(request))):
        tools = await llm.supports_tools(model)
        models.append(
            ChatModel(
                id=model.id,
                display_name=model.display_name,
                provider=provider_of(model.litellm_model),
                is_default=index == 0,
                can_create_files=settings.chat_file_tools and tools,
                can_create_images=images and tools,
                supports_tools=tools,
                allowance_rate=allowance_rate(plans.blended(model)),
            )
        )
    return models
