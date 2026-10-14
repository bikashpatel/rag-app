"""Streaming RAG chat: POST /api/chat/stream answers with server-sent events."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.models import Conversation, LLMModel, Message, User, utcnow
from app.routers.conversations import get_owned_conversation
from app.schemas import MAX_INT, Source
from app.services import llm, rag
from app.services.model_registry import resolve_chat_model
from app.services.usage import (
    acquire_chat_slot,
    check_budget,
    check_rate_limit,
    record_usage,
    release_chat_slot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_TITLE_CHARS = 60
# Embedding models reject over-long inputs; the tail is kept because it holds the new message.
_MAX_QUERY_CHARS = 4000
_STOPPED = "Stopped before completion"
_FAILED = "Something went wrong while generating the answer."
_NOT_SAVED = "The answer could not be saved."
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
# A narrowed selection lists every other ready document, so it grows with the knowledge base.
_MAX_DOCUMENT_IDS = 10_000

_Id = Annotated[int, Field(ge=1, le=MAX_INT)]


class ChatRequest(BaseModel):
    conversation_id: _Id | None = None
    message: str = Field(min_length=1, max_length=20_000)
    model_id: _Id | None = None
    document_ids: Annotated[list[_Id], Field(max_length=_MAX_DOCUMENT_IDS)] | None = None

    @field_validator("message", mode="before")
    @classmethod
    def clean_message(cls, value: object) -> object:
        # Postgres text can't hold NUL characters.
        return value.replace("\x00", "").strip() if isinstance(value, str) else value


@dataclass(frozen=True)
class _Turn:
    """Everything the stream needs, captured while handling the request."""

    user: User
    model: LLMModel
    conversation_id: int
    title: str
    user_message_id: int
    message: str
    history: list[Message]
    retrieval_query: str
    document_ids: list[int] | None
    slot: str | None = None  # see usage.acquire_chat_slot; released once the answer is saved


@dataclass
class _Answer:
    parts: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    usage: llm.Usage = field(default_factory=llm.Usage)
    error: str | None = None


# Strong references to in-flight saves: the event loop only keeps weak ones.
_pending_saves: set[asyncio.Task[int | None]] = set()
# One future per answer stream still running, resolved when it ends (see drain_answers).
_open_streams: set[asyncio.Future[None]] = set()


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _title_for(message: str) -> str:
    title = " ".join(message.split())
    return title if len(title) <= _TITLE_CHARS else title[:_TITLE_CHARS] + "…"


def _retrieval_query(history: list[Message], message: str) -> str:
    previous = next((m.content for m in reversed(history) if m.role == "user" and m.content), None)
    query = f"{previous}\n{message}" if previous else message
    return query[-_MAX_QUERY_CHARS:]


async def _recent_messages(db: AsyncSession, conversation_id: int) -> list[Message]:
    """The conversation's last HISTORY_MESSAGES messages, oldest first."""
    if settings.history_messages <= 0:
        return []
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(settings.history_messages)
    )
    return list(reversed(result.scalars().all()))


async def _save_answer(turn: _Turn, answer: _Answer) -> int | None:
    """Record the chat usage, then store the assistant message. Returns the message id, or None if it wasn't stored.

    Frees the turn's answer slot afterwards, so the user's next budget check sees this answer's tokens.
    """
    try:
        async with SessionLocal() as db:
            if answer.usage.total_tokens > 0:
                # Committed on its own: the answer is billed even if storing the message fails.
                model = turn.model if await _existing_model_id(db, turn.model.id) is not None else None
                await record_usage(
                    db,
                    user_id=turn.user.id,
                    model=model,
                    model_name=turn.model.litellm_model,
                    kind="chat",
                    usage=answer.usage,
                )
            conversation = await db.get(Conversation, turn.conversation_id)
            if conversation is None:  # deleted while the answer streamed
                return None
            message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="".join(answer.parts),
                sources=[source.model_dump() for source in answer.sources],
                model_id=await _existing_model_id(db, turn.model.id),
                model_name=turn.model.display_name,
                prompt_tokens=answer.usage.prompt_tokens,
                completion_tokens=answer.usage.completion_tokens,
                error=answer.error.replace("\x00", "") if answer.error is not None else None,
            )
            db.add(message)
            conversation.updated_at = utcnow()
            await db.commit()
            return message.id
    except Exception:
        logger.exception("Could not save the answer in conversation %s", turn.conversation_id)
        return None
    finally:
        await release_chat_slot(turn.user.id, turn.slot)


async def _existing_model_id(db: AsyncSession, model_id: int) -> int | None:
    """The model's id if it still exists (the admin may have deleted it while the answer streamed, and a new row
    can't reference it then). FOR KEY SHARE keeps it from being deleted until the transaction ends."""
    return await db.scalar(
        select(LLMModel.id).where(LLMModel.id == model_id).with_for_update(read=True, key_share=True)
    )


async def _save_answer_shielded(turn: _Turn, answer: _Answer) -> int | None:
    """Save in a task of its own, so cancelling the stream can't interrupt the save halfway."""
    task = asyncio.create_task(_save_answer(turn, answer))
    _pending_saves.add(task)
    task.add_done_callback(_pending_saves.discard)
    return await asyncio.shield(task)


async def drain_answers(timeout: float = 10.0) -> None:
    """On shutdown, give the answer streams uvicorn cancelled (after its graceful timeout) up to `timeout` seconds to
    store their partial answers and usage, before the database and Redis connections close."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while pending := _open_streams | _pending_saves:
        remaining = deadline - loop.time()
        if remaining <= 0:
            logger.warning("Shutting down with %d answer(s) not saved", len(pending))
            return
        await asyncio.wait(pending, timeout=remaining)


async def _stream_answer(turn: _Turn) -> AsyncIterator[str]:
    ended: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    _open_streams.add(ended)
    try:
        answer = _Answer()
        try:
            yield _event(
                "meta",
                {
                    "conversation_id": turn.conversation_id,
                    "title": turn.title,
                    "user_message_id": turn.user_message_id,
                    "model": {"id": turn.model.id, "display_name": turn.model.display_name},
                },
            )
            async with SessionLocal() as db:
                retrieval = await rag.retrieve(db, turn.user, turn.retrieval_query, turn.document_ids)
            answer.sources = retrieval.sources
            yield _event("sources", {"sources": [source.model_dump() for source in retrieval.sources]})
            if retrieval.warning:
                yield _event("warning", {"message": retrieval.warning})

            messages = rag.build_messages(
                sources=retrieval.sources,
                contexts=retrieval.contexts,
                history=turn.history,
                user_message=turn.message,
                documents_requested=turn.document_ids != [],
                search_failed=retrieval.warning is not None,
            )
            # aclosing: if this generator is closed mid-answer, the model stream is closed (and its usage
            # filled in) before the answer is saved.
            async with aclosing(llm.stream_chat(turn.model, messages, answer.usage)) as deltas:
                async for delta in deltas:
                    delta = delta.replace("\x00", "")  # Postgres text can't hold NUL characters; models can emit them
                    if not delta:
                        continue
                    answer.parts.append(delta)
                    yield _event("delta", {"content": delta})
        except llm.LLMError as exc:
            answer.error = str(exc)
            if not answer.parts and answer.usage.estimated:
                # Failed before producing any output and the provider reported no usage: nothing was billed.
                answer.usage = llm.Usage()
        except (asyncio.CancelledError, GeneratorExit):
            # The client went away (e.g. the Stop button). Keep the partial answer, then stop without yielding.
            answer.error = _STOPPED
            await _save_answer_shielded(turn, answer)
            raise
        except Exception:
            logger.exception("Answer generation failed in conversation %s", turn.conversation_id)
            answer.error = _FAILED

        assistant_message_id = await _save_answer_shielded(turn, answer)
        if answer.error is None and assistant_message_id is not None:
            usage = answer.usage
            yield _event(
                "done",
                {
                    "assistant_message_id": assistant_message_id,
                    "usage": {
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "total_tokens": usage.total_tokens,
                        "cost_usd": usage.cost_usd,
                    },
                },
            )
        else:
            yield _event("error", {"message": answer.error or _NOT_SAVED, "assistant_message_id": assistant_message_id})
    finally:
        _open_streams.discard(ended)
        ended.set_result(None)


@router.post("/stream")
async def chat_stream(
    body: ChatRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    await check_rate_limit(user)
    await check_budget(db, user)
    slot = await acquire_chat_slot(user)
    try:
        return await _start_turn(body, user, db, slot)
    except BaseException:
        await release_chat_slot(user.id, slot)
        raise


async def _start_turn(body: ChatRequest, user: User, db: AsyncSession, slot: str | None) -> StreamingResponse:
    model = await resolve_chat_model(db, user, body.model_id)

    history: list[Message] = []
    if body.conversation_id is None:
        conversation = Conversation(user_id=user.id, title=_title_for(body.message))
        db.add(conversation)
        await db.flush()
    else:
        conversation = await get_owned_conversation(db, user, body.conversation_id)
        history = await _recent_messages(db, conversation.id)
        conversation.updated_at = utcnow()
    user_message = Message(conversation_id=conversation.id, role="user", content=body.message)
    db.add(user_message)
    await db.commit()

    # The stream runs after this handler returns and uses sessions of its own. These ORM objects stay
    # readable without the request's session because SessionLocal doesn't expire them on commit.
    turn = _Turn(
        user=user,
        model=model,
        conversation_id=conversation.id,
        title=conversation.title,
        user_message_id=user_message.id,
        message=body.message,
        history=history,
        retrieval_query=_retrieval_query(history, body.message),
        document_ids=body.document_ids,
        slot=slot,
    )
    return StreamingResponse(_stream_answer(turn), media_type="text/event-stream", headers=_SSE_HEADERS)
