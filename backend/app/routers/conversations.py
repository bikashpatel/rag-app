"""The caller's conversations. Other users' conversations are reported as not found."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Conversation, Message, User
from app.schemas import MAX_INT, Source

router = APIRouter(prefix="/conversations", tags=["conversations"])

_LIST_LIMIT = 200

ConversationId = Annotated[int, Path(ge=1, le=MAX_INT)]


class ConversationSummary(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    sources: list[Source]
    model_name: str | None
    prompt_tokens: int
    completion_tokens: int
    error: str | None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[ChatMessage]


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        # Collapse whitespace; Postgres text can't hold NUL characters.
        return " ".join(value.replace("\x00", "").split()) if isinstance(value, str) else value


async def get_owned_conversation(db: AsyncSession, user: User, conversation_id: int) -> Conversation:
    """The user's conversation; 404 if it doesn't exist or belongs to someone else."""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def _summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _chat_message(message: Message) -> ChatMessage:
    return ChatMessage(
        id=message.id,
        role=message.role,  # type: ignore[arg-type]
        content=message.content,
        sources=message.sources or [],
        model_name=message.model_name,
        prompt_tokens=message.prompt_tokens,
        completion_tokens=message.completion_tokens,
        error=message.error,
        created_at=message.created_at,
    )


@router.get("")
async def list_conversations(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[ConversationSummary]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(_LIST_LIMIT)
    )
    return [_summary(conversation) for conversation in result.scalars()]


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: ConversationId, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> ConversationDetail:
    conversation = await get_owned_conversation(db, user, conversation_id)
    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    )
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_chat_message(message) for message in result.scalars()],
    )


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: ConversationId,
    body: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    conversation = await get_owned_conversation(db, user, conversation_id)
    conversation.title = body.title
    await db.commit()
    return _summary(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: ConversationId, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    conversation = await get_owned_conversation(db, user, conversation_id)
    # Explicit, so messages go even where the database doesn't enforce ON DELETE CASCADE (SQLite).
    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    await db.delete(conversation)
    await db.commit()
