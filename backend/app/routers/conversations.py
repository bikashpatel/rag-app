"""The caller's conversations. Other users' conversations are reported as not found. Without a plan, the list and the
deletes stay open; reading or renaming one needs access (402)."""

import logging
from collections import defaultdict
from collections.abc import Collection
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, get_entitled_user
from app.models import Conversation, Document, GeneratedImage, Message, MessageFile, MessagePluginItem, User
from app.plugins import registry
from app.schemas import MAX_INT, AnswerSource, GeneratedFile
from app.schemas_account import ConversationsDeleted
from app.schemas_plugins import PluginCard
from app.services.storage import image_item

logger = logging.getLogger(__name__)

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
    sources: list[AnswerSource]  # document passages, then the emails the answer cites
    model_name: str | None
    prompt_tokens: int
    completion_tokens: int
    error: str | None
    files: list[GeneratedFile]  # in creation order; [] for user messages
    plugin_cards: list[PluginCard] = Field(default_factory=list)  # in order; [] for user messages
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


async def message_files(db: AsyncSession, message_ids: Collection[int]) -> dict[int, list[GeneratedFile]]:
    """message id -> the files and images that answer created, in creation order, with each document's status now
    (and an image's display details while its document exists)."""
    if not message_ids:
        return {}
    rows = await db.execute(
        select(MessageFile, Document.status, GeneratedImage)
        .outerjoin(Document, Document.id == MessageFile.document_id)
        .outerjoin(GeneratedImage, GeneratedImage.document_id == MessageFile.document_id)
        .where(MessageFile.message_id.in_(message_ids))
        .order_by(MessageFile.id)
    )
    files: defaultdict[int, list[GeneratedFile]] = defaultdict(list)
    for row, status_now, image in rows.tuples():
        exists = row.document_id is not None
        files[row.message_id].append(
            GeneratedFile(
                document_id=row.document_id,
                filename=row.filename,
                format=row.format,  # type: ignore[arg-type]
                size_bytes=row.size_bytes,
                status=status_now if exists else None,  # type: ignore[arg-type]
                image=image_item(image) if exists and image is not None else None,
            )
        )
    return files


async def plugin_cards(db: AsyncSession, user: User, message_ids: Collection[int]) -> dict[int, list[PluginCard]]:
    """message id -> the cards plugins added to that answer, in order, with their live state (one query, then one
    hydration per plugin). Cards of an unknown or failing plugin are left out."""
    if not message_ids:
        return {}
    items = list(
        await db.scalars(
            select(MessagePluginItem)
            .where(MessagePluginItem.message_id.in_(message_ids))
            .order_by(MessagePluginItem.id)
        )
    )
    hydrated = await registry.hydrate_cards(db, user, items)
    found: defaultdict[int, list[PluginCard]] = defaultdict(list)
    for item in items:
        if item.id in hydrated:
            found[item.message_id].append(hydrated[item.id])
    return found


def _chat_message(message: Message, files: list[GeneratedFile], cards: list[PluginCard]) -> ChatMessage:
    return ChatMessage(
        id=message.id,
        role=message.role,  # type: ignore[arg-type]
        content=message.content,
        sources=message.sources or [],
        model_name=message.model_name,
        prompt_tokens=message.prompt_tokens,
        completion_tokens=message.completion_tokens,
        error=message.error,
        files=files,
        plugin_cards=cards,
        created_at=message.created_at,
    )


async def delete_conversation_completely(db: AsyncSession, user: User, conversation: Conversation) -> None:
    """Delete one of the user's conversations and commit: the plugins clean up first (e.g. Gmail cancels and scrubs its
    proposed emails; a hook that fails has the uncommitted changes rolled back and the deletion still happens), then
    its cards, messages and the row. Documents (generated files and images included) and usage stay."""
    conversation_id = conversation.id
    for plugin in registry.all_plugins():
        try:
            await plugin.on_conversation_deleted(db, user, conversation_id)
        except Exception:
            logger.exception("The %s plugin failed to clean up conversation %s", plugin.id, conversation_id)
            await db.rollback()  # its partial changes go; the deletion below still happens
            await db.refresh(user)
            await db.refresh(conversation)
    # Explicit, so these go even where the database doesn't enforce ON DELETE CASCADE (SQLite).
    message_ids = select(Message.id).where(Message.conversation_id == conversation_id)
    await db.execute(delete(MessagePluginItem).where(MessagePluginItem.message_id.in_(message_ids)))
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await db.delete(conversation)
    await db.commit()


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
    conversation_id: ConversationId, user: User = Depends(get_entitled_user), db: AsyncSession = Depends(get_db)
) -> ConversationDetail:
    conversation = await get_owned_conversation(db, user, conversation_id)
    result = await db.execute(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id))
    messages = list(result.scalars())
    answers = [message.id for message in messages if message.role == "assistant"]
    files = await message_files(db, answers)
    cards = await plugin_cards(db, user, answers)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            _chat_message(message, files.get(message.id, []), cards.get(message.id, [])) for message in messages
        ],
    )


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: ConversationId,
    body: ConversationUpdate,
    user: User = Depends(get_entitled_user),
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
    await delete_conversation_completely(db, user, await get_owned_conversation(db, user, conversation_id))


@router.delete("")
async def delete_all_conversations(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> ConversationsDeleted:
    """Every conversation of the caller, each exactly as DELETE /conversations/{id} and committed on its own, so a
    plugin's failing clean-up never undoes another conversation's deletion. A failing commit stops with the error; the
    ones before it stay deleted and a retry deletes the rest."""
    ids = list(
        await db.scalars(select(Conversation.id).where(Conversation.user_id == user.id).order_by(Conversation.id))
    )
    deleted = 0
    for conversation_id in ids:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user.id:  # deleted meanwhile
            continue
        await delete_conversation_completely(db, user, conversation)
        deleted += 1
    return ConversationsDeleted(deleted=deleted)
