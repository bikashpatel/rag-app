"""Streaming RAG chat: POST /api/chat/stream answers with server-sent events."""

import asyncio
import json
import logging
import re
from collections import defaultdict
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, get_db
from app.deps import current_entitlements, get_entitled_user
from app.models import (
    Conversation,
    Document,
    GeneratedImage,
    LLMModel,
    Message,
    MessageFile,
    MessagePluginItem,
    User,
    utcnow,
)
from app.plugins import registry
from app.plugins.answer import AnswerContext, CardSink, SourceRegistry, Taint
from app.plugins.base import LoopEvent, PluginEvent, SideEffect, ToolRunner, tool_names
from app.plugins.intent import RequestCheck, earlier_user_messages, previous_reply
from app.routers.conversations import get_owned_conversation
from app.schemas import MAX_INT, AnswerSource, GeneratedFile, Source
from app.services import document_tool, image_tool, llm, rag
from app.services.billing.entitlements import Entitlements
from app.services.billing.money import allowance_tokens
from app.services.model_registry import resolve_chat_model
from app.services.usage import (
    acquire_chat_slot,
    check_budget,
    check_rate_limit,
    record_usage,
    refresh_chat_slot,
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
_BUDGET_SPENT = (
    "This period's chat allowance ran out while this answer was being written, so it stops here. Files created above "
    "are in your documents."
)
_DECLINED = "The model declined to continue, so the file wasn't created."
_INCOMPLETE = "The model's response ended before the file was complete, so it wasn't created."
# What an open call of each tool says when its response broke off ("failed"), was refused or didn't finish.
_OPEN_CALL_ERRORS = {
    document_tool.TOOL_NAME: {"failed": document_tool.CALL_FAILED, "declined": _DECLINED, "incomplete": _INCOMPLETE},
    image_tool.TOOL_NAME: {
        "failed": image_tool.CALL_FAILED,
        "declined": image_tool.DECLINED,
        "incomplete": image_tool.INCOMPLETE,
    },
}
# An SSE comment after this much silence: proxies (a Cloudflare tunnel drops a response silent for about 100 s) must
# not cut an answer that is thinking, writing a long file or rendering it.
_KEEPALIVE_SECONDS = 15.0
_KEEPALIVE = ": keepalive\n\n"
_SLOT_REFRESH_SECONDS = 60.0
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
# A narrowed selection lists every other ready document, so it grows with the knowledge base.
_MAX_DOCUMENT_IDS = 10_000
# A provider's complaint about the output size asked for: max_tokens, max_completion_tokens, max_output_tokens,
# maxOutputTokens, max_new_tokens, "maximum tokens", "output tokens", "completion tokens".
_OUTPUT_LIMIT = re.compile(
    r"max(?:imum)?[\s_-]*(?:new|output|completion)?[\s_-]*tokens|(?:output|completion)[\s_-]*tokens", re.IGNORECASE
)

_IMAGE_FORMATS = frozenset({"png", "jpeg", "webp"})  # message_files.format of a generated image
# The built-in tools that need the request check in an answer that has seen untrusted text (brief P11).
_SIDE_EFFECTS: dict[str, SideEffect] = {document_tool.TOOL_NAME: "file", image_tool.TOOL_NAME: "image"}
_EARLIER_IMAGES_LISTED = 20  # the conversation's newest images listed when their answers left the history window

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
    ent: Entitlements  # loaded when the request arrived: it holds for the whole answer
    model: LLMModel
    conversation_id: int
    title: str
    user_message_id: int
    message: str
    history: list[Message]
    retrieval_query: str
    document_ids: list[int] | None
    slot: str | None = None  # see usage.acquire_chat_slot; released once the answer is saved
    file_plan: document_tool.FilePlan = field(default_factory=lambda: document_tool.FilePlan([], None, None))
    image_plan: image_tool.ImagePlan = field(default_factory=lambda: image_tool.ImagePlan([], None))
    history_files: dict[int, list[rag.HistoryFile]] = field(default_factory=dict)  # message id -> its files
    # The conversation's images that no answer in `history` lists, so create_document can still embed them.
    earlier_images: list[rag.HistoryFile] = field(default_factory=list)
    budget_left: int | None = None  # the chat allowance left when the answer started (None: unlimited)
    plugin_plan: registry.PluginChatPlan = field(default_factory=registry.PluginChatPlan)
    plugin_notes: dict[int, str] = field(default_factory=dict)  # message id -> the plugins' App note
    history_tainted: bool = False  # an answer of the conversation saw untrusted text (email, plugin output)


@dataclass
class _Answer:
    parts: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    ctx: AnswerContext | None = None  # the plugins' state of this answer, once generation started
    usage: llm.Usage = field(default_factory=llm.Usage)
    error: str | None = None
    # Files and images created this answer, in order (see document_tool and image_tool).
    files: list[GeneratedFile] = field(default_factory=list)
    storing: "asyncio.Task[Any] | None" = None  # a file being stored; Stop waits for it
    closed: bool = False  # being saved with the files it holds: an image finished later links itself to the message
    # The saved message's id, or None when it wasn't stored (an image job of a stopped answer waits for it).
    saved: "asyncio.Future[int | None]" = field(default_factory=lambda: asyncio.get_running_loop().create_future())

    def close_files(self) -> list[GeneratedFile]:
        """The files to save with the message; files added later must link themselves (synchronous, so an image job
        sees either the files before the copy or `closed`)."""
        self.closed = True
        return list(self.files)


# Strong references to in-flight saves: the event loop only keeps weak ones.
_pending_saves: set[asyncio.Task[int | None]] = set()
# One future per answer stream still running, resolved when it ends (see drain_answers).
_open_streams: set[asyncio.Future[None]] = set()
# Strong references to the tasks that produce answers (see _sse_with_keepalive).
_answer_tasks: set[asyncio.Task[None]] = set()


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _loop_event(event: LoopEvent) -> str:
    """A runner's event: a plugin's own (`plugin`, `sources`), else a `file` event."""
    if isinstance(event, PluginEvent):
        return _event(event.name, event.data)
    return _event("file", event)


def _sources_data(sources: Sequence[AnswerSource]) -> dict[str, Any]:
    return {"sources": [source.model_dump(mode="json") for source in sources]}


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
    """Record the chat usage, then store the assistant message with the files it created. Returns the message id, or
    None if it wasn't stored.

    Frees the turn's answer slot afterwards, so the user's next budget check sees this answer's tokens. Never waits
    for an image job: one still running links its image to the message once `answer.saved` resolves.
    """
    message_id: int | None = None
    try:
        if (storing := answer.storing) is not None:  # a file was being stored at Stop: it links itself to the answer
            await asyncio.wait({storing})
            if not storing.cancelled():
                storing.exception()  # retrieved: a file that couldn't be stored just isn't linked
        if answer.ctx is not None:  # drafts, imports and proposals still being stored: their cards show the outcome
            await answer.ctx.cards.settle()
        files = answer.close_files()
        text = "".join(answer.parts)
        sources: Sequence[AnswerSource] = answer.ctx.sources.final(text) if answer.ctx is not None else answer.sources
        cards = answer.ctx.cards.cards() if answer.ctx is not None else []
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
                content=text,
                sources=[source.model_dump(mode="json") for source in sources],
                model_id=await _existing_model_id(db, turn.model.id),
                model_name=turn.model.display_name,
                prompt_tokens=answer.usage.prompt_tokens,
                completion_tokens=answer.usage.completion_tokens,
                error=answer.error.replace("\x00", "") if answer.error is not None else None,
            )
            db.add(message)
            conversation.updated_at = utcnow()
            if files or cards:
                await db.flush()
            db.add_all(
                MessagePluginItem(
                    message_id=message.id, plugin_id=plugin_id, card_type=card.type, data=card.model_dump(mode="json")
                )
                for plugin_id, card in cards
            )
            if files:
                existing = await _existing_document_ids(db, [file.document_id for file in files])
                db.add_all(
                    MessageFile(
                        message_id=message.id,
                        document_id=file.document_id if file.document_id in existing else None,
                        filename=file.filename,
                        format=file.format,
                        size_bytes=file.size_bytes,
                    )
                    for file in files
                )
            await db.commit()
            message_id = message.id
            return message_id
    except Exception:
        logger.exception("Could not save the answer in conversation %s", turn.conversation_id)
        return None
    finally:
        if not answer.saved.done():
            answer.saved.set_result(message_id)
        await release_chat_slot(turn.user.id, turn.slot)


async def _existing_model_id(db: AsyncSession, model_id: int) -> int | None:
    """The model's id if it still exists (the admin may have deleted it while the answer streamed, and a new row
    can't reference it then). FOR KEY SHARE keeps it from being deleted until the transaction ends."""
    return await db.scalar(
        select(LLMModel.id).where(LLMModel.id == model_id).with_for_update(read=True, key_share=True)
    )


async def _existing_document_ids(db: AsyncSession, ids: list[int | None]) -> set[int]:
    """The documents that still exist (one may be deleted while the answer streams); FOR KEY SHARE keeps them until
    the message is stored."""
    wanted = [doc_id for doc_id in ids if doc_id is not None]
    if not wanted:
        return set()
    return set(
        await db.scalars(select(Document.id).where(Document.id.in_(wanted)).with_for_update(read=True, key_share=True))
    )


async def _save_answer_shielded(turn: _Turn, answer: _Answer) -> int | None:
    """Save in a task of its own, so cancelling the stream can't interrupt the save halfway."""
    task = asyncio.create_task(_save_answer(turn, answer))
    _pending_saves.add(task)
    task.add_done_callback(_pending_saves.discard)
    return await asyncio.shield(task)


async def drain_answers(timeout: float = 10.0) -> None:
    """On shutdown, give the answer streams uvicorn cancelled (after its graceful timeout) up to `timeout` seconds to
    store their partial answers and usage, and running image jobs to store their images, before the database and
    Redis connections close."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while pending := _open_streams | _pending_saves | image_tool.running_jobs():
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
            yield _event("sources", _sources_data(retrieval.sources))
            if retrieval.warning:
                yield _event("warning", {"message": retrieval.warning})
            # aclosing: if this generator is closed mid-answer, the model stream is closed (and its usage
            # filled in) before the answer is saved.
            async with aclosing(_generate(turn, retrieval, answer)) as events:
                async for event in events:
                    yield event
        except llm.LLMError as exc:
            answer.error = str(exc)
        except (asyncio.CancelledError, GeneratorExit):
            # The client went away (e.g. the Stop button). Keep the partial answer, then stop without yielding.
            answer.error = _STOPPED
            if answer.ctx is not None:
                answer.ctx.cards.stopping.set()  # long plugin runs (imports) stop between items
            await _save_answer_shielded(turn, answer)
            raise
        except Exception:
            logger.exception("Answer generation failed in conversation %s", turn.conversation_id)
            answer.error = _FAILED

        if answer.ctx is not None:  # emails the answer doesn't cite are dropped
            final = answer.ctx.sources.final("".join(answer.parts))
            if len(final) != len(answer.ctx.sources.entries):
                yield _event("sources", _sources_data(final))
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


async def _generate(turn: _Turn, retrieval: rag.RetrievalResult, answer: _Answer) -> AsyncGenerator[str]:
    """The model calls of one answer: text deltas, and while the model creates files or images or uses plugin tools,
    the calls that run its tools and the follow-up calls with the results (docs/briefs/document-generation.md, section
    7, docs/briefs/image-generation.md, section 6, and docs/briefs/plugins-gmail.md, section 7). Raises LLMError when a
    call fails; budget, refusal and cut-off endings set answer.error instead."""
    plan, images, plugins = turn.file_plan, turn.image_plan, turn.plugin_plan
    ctx = answer.ctx = AnswerContext(
        user=turn.user,
        chat_model=turn.model,
        conversation_id=turn.conversation_id,
        user_message=turn.message,
        sources=SourceRegistry(retrieval.sources),
        cards=CardSink(),
        taint=Taint(bool(retrieval.imported)),
        request_check=RequestCheck(
            turn.model,
            latest=turn.message,
            earlier=earlier_user_messages(turn.history),
            previous_reply=None if turn.history_tainted else previous_reply(turn.history),
            usage=answer.usage,
        ),
        created_files=answer.files,
    )

    def prompt(
        file_instructions: str | None,
        image_instructions: str | None,
        plugin_instructions: str | None,
        *,
        file_tool: bool = True,
    ) -> list[dict]:
        return rag.build_messages(
            sources=retrieval.sources,
            contexts=retrieval.contexts,
            history=turn.history,
            user_message=turn.message,
            documents_requested=turn.document_ids != [],
            search_failed=retrieval.warning is not None,
            generated=retrieval.generated,
            file_instructions=file_instructions,
            image_instructions=image_instructions,
            history_files=turn.history_files,
            generated_images=retrieval.generated_images,
            earlier_images=turn.earlier_images if file_tool else (),
            plugin_instructions=plugin_instructions,
            history_notes=turn.plugin_notes,
            imported=retrieval.imported,
        )

    def with_tools(file_instructions: str | None, image_instructions: str | None) -> list[dict]:
        other = "\n\n".join(text for text in (file_instructions, image_instructions) if text)
        return prompt(file_instructions, image_instructions, plugins.instructions(other))

    plugin_runners: dict[str, ToolRunner] = {}
    errors: dict[str, Mapping[str, str]] = dict(_OPEN_CALL_ERRORS)
    plugin_tools: list[dict[str, Any]] = []
    plugin_calls = 0
    for toolset in plugins.toolsets:
        try:
            session = toolset.start(ctx)
            own = {name: session.runner(name) for name in tool_names(toolset)}
        except Exception:  # a broken plugin never breaks the answer: its tools just aren't offered
            logger.exception("The %s plugin failed to start its tools", toolset.plugin_id)
            continue
        plugin_runners.update(own)
        errors.update((name, toolset.open_call_errors) for name in own)
        plugin_tools.extend(toolset.tools)
        plugin_calls += toolset.max_calls

    messages = with_tools(plan.instructions, images.instructions)
    # create_document, create_image, then the plugins' tools; the same list for every call of the answer
    all_tools = plan.tools + images.tools + plugin_tools
    tools = all_tools
    file_max_tokens = plan.max_tokens  # sent by the calls that offer create_document; the max_tokens recovery lowers it
    default_max_tokens = llm.resolve_max_tokens(turn.model)  # what a call without the file tool sends
    files = document_tool.FileSession(
        turn.user, ctx.sources.entries, answer, file_max_tokens, conversation_id=turn.conversation_id, ent=turn.ent
    )
    pictures = image_tool.ImageSession(turn.user, turn.conversation_id, answer, turn.ent)
    all_runners: dict[str, ToolRunner] = {
        name: runner
        for name, runner, offered in (
            (document_tool.TOOL_NAME, files, bool(plan.tools)),
            (image_tool.TOOL_NAME, pictures, bool(images.tools)),
        )
        if offered
    }
    all_runners.update(plugin_runners)
    runners = all_runners
    caps = (
        (settings.generated_files_per_answer if plan.tools else 0)
        + (settings.generated_images_per_answer if images.tools else 0)
        + plugin_calls
    )
    all_calls = caps + 2 if tools else 1
    max_calls = all_calls
    first_added = len(messages)  # the messages this answer appends start here
    recovered: set[str] = set()  # each recovery at most once per answer
    retrying: str | None = None  # call 1 is being retried by this recovery: "max_tokens", "context" or "tools"
    rejection = ""  # the provider's message that started the retry (for the log)
    call_no = 0
    while call_no < max_calls:
        call_no += 1
        spent = allowance_tokens(answer.usage.cost_usd, answer.usage.total_tokens)
        if call_no > 1 and turn.budget_left is not None and spent >= turn.budget_left:
            answer.error = _BUDGET_SPENT
            return
        offer = bool(tools) and any(runner.can_create() for runner in runners.values()) and call_no < max_calls
        step, call_usage = llm.ChatStep(), llm.Usage()
        for runner in runners.values():
            runner.begin_call()
        separate = bool(answer.parts)  # a blank line before this call's text
        try:
            async with aclosing(
                llm.stream_chat(
                    turn.model,
                    messages,
                    call_usage,
                    step=step,
                    tools=tools or None,
                    tool_choice=("auto" if offer else "none") if tools else None,
                    parallel_tool_calls=False if offer else None,
                    # The retry without the tools keeps the max_tokens of the call it retries: only the tools change.
                    max_tokens=file_max_tokens if (offer or retrying == "tools") else None,
                )
            ) as items:
                async for item in items:
                    if isinstance(item, llm.ToolCallStarted):
                        announcer = runners.get(item.name)
                        if offer and announcer is not None and (started := announcer.started(item.index)) is not None:
                            yield _loop_event(started)
                        continue
                    if isinstance(item, llm.ToolCallProgress):
                        if offer:
                            for runner in runners.values():
                                if (progress := runner.progress(item.index, step)) is not None:
                                    yield _loop_event(progress)
                        continue
                    text = item.replace("\x00", "")  # Postgres text can't hold NUL characters; models can emit them
                    if not text:
                        continue
                    if separate:
                        answer.parts.append("\n\n")
                        yield _event("delta", {"content": "\n\n"})
                        separate = False
                    answer.parts.append(text)
                    yield _event("delta", {"content": text})
        except llm.LLMError as exc:
            if step.received or not call_usage.estimated:  # a call that failed before any output costs nothing
                answer.usage.add(call_usage)
            first_call = call_no == 1 and bool(all_tools) and not step.received
            can_lower = (
                "max_tokens" not in recovered and file_max_tokens is not None and default_max_tokens < file_max_tokens
            )
            output_rejected = exc.bad_request and (retrying == "tools" or _OUTPUT_LIMIT.search(exc.detail) is not None)
            if first_call and can_lower and (exc.context_window or output_rejected):
                # The output reserved for a file is more than the endpoint takes (or than the context window has room
                # for): retry with the tools and the usual max_tokens. Also when the retry without the tools failed
                # too, since that didn't show the tools were the problem.
                recovered.add("max_tokens")
                recovered.discard("tools")  # the tools may still be rejected at the lower max_tokens
                file_max_tokens = files.max_tokens = default_max_tokens
                tools, runners, max_calls = all_tools, all_runners, all_calls
                retrying, rejection = ("context" if exc.context_window else "max_tokens"), exc.detail
                file_text = document_tool.file_instructions(file_max_tokens, settings.chat_image_tools)
                messages = with_tools(file_text, images.instructions)
                call_no -= 1
                continue
            if first_call and exc.bad_request and tools and "tools" not in recovered:
                # The endpoint rejects the tools (e.g. a schema): answer without them, as models without tools do.
                # Nothing else changes, so when this works the tools were the problem.
                recovered.add("tools")
                tools, runners, max_calls = [], {}, 1
                retrying, rejection = "tools", exc.detail
                messages = prompt(
                    document_tool.NO_FILE_TOOLS_NOTE if plan.tools else plan.instructions,
                    image_tool.no_tools_note() if images.tools else images.instructions,
                    plugins.no_tools_instructions(),
                    file_tool=False,
                )
                call_no -= 1
                continue
            signature_rejected = "thinking" in exc.detail.lower() and "signature" in exc.detail.lower()
            if exc.bad_request and signature_rejected and call_no > 1 and "thinking" not in recovered:
                logger.warning("%s rejected a replayed thinking block; retrying without them", turn.model.litellm_model)
                recovered.add("thinking")
                added = messages[first_added:]
                llm.strip_thinking(added)
                messages[first_added:] = added
                call_no -= 1
                continue
            for event in _open_calls(runners, errors, "failed"):
                yield _loop_event(event)
            raise
        except BaseException:  # Stop: the call's usage is billed
            answer.usage.add(call_usage)
            raise
        answer.usage.add(call_usage)
        if retrying is not None and step.received:
            if retrying == "tools":
                llm.mark_tools_rejected(turn.model, rejection)
            elif retrying == "max_tokens":
                logger.warning(
                    "%s rejected max_tokens=%s with the file tool (%s) and accepted %s; set the model's Max output "
                    "tokens in the admin page",
                    turn.model.litellm_model,
                    plan.max_tokens,
                    rejection,
                    file_max_tokens,
                )
            retrying = None
        if not offer or not step.tool_calls:
            if step.tool_calls:
                logger.info("Ignored %d tool call(s) of a response sent without tools", len(step.tool_calls))
            return
        if step.finish_reason not in ("tool_calls", "length"):
            ending = "declined" if step.finish_reason == "content_filter" else "incomplete"
            for event in _open_calls(runners, errors, ending):
                yield _loop_event(event)
            answer.error = errors[_wording_tool(step.tool_calls, errors)][ending]
            return
        ids = [call.id or f"call_{call_no}_{call.index}" for call in step.tool_calls]
        messages.extend(llm.assistant_turn(step, ids))
        for call, call_id in zip(step.tool_calls, ids, strict=True):
            result: dict[str, Any] = {"ok": False, "error": document_tool.SERVER_ERROR}
            if (tool := runners.get(call.name)) is None:
                result = {"ok": False, "error": _unknown_tool(call.name, list(runners))}
            elif (
                (action := _SIDE_EFFECTS.get(call.name)) is not None
                and ctx.taint.tainted
                and step.finish_reason == "tool_calls"
                and not await ctx.request_check.allows(action)
            ):
                # The answer has seen untrusted text: a file or image only when the user asked for one (P11).
                for event in tool.open_calls(ctx.request_check.card_reason(action)):
                    yield _loop_event(event)
                result = {"ok": False, "error": ctx.request_check.reason(action)}
            else:
                outcomes = tool.run(call) if step.finish_reason == "tool_calls" else tool.cut_off(call)
                async with aclosing(outcomes) as running:
                    async for outcome in running:
                        if isinstance(outcome, document_tool.ToolResult):
                            result = outcome.content
                        else:
                            yield _loop_event(outcome)
            messages.append(llm.tool_result_message(call_id, result))


def _open_calls(
    runners: Mapping[str, ToolRunner], errors: Mapping[str, Mapping[str, str]], ending: str
) -> list[LoopEvent]:
    """Failed events for every announced call that hasn't ended, each with its tool's wording of `ending`."""
    return [event for name, runner in runners.items() for event in runner.open_calls(errors[name][ending])]


def _wording_tool(calls: Sequence[llm.ToolCall], errors: Mapping[str, Mapping[str, str]]) -> str:
    """Whose wording the answer's error uses when a response with tool calls didn't finish: the first call's tool
    other than create_image, or create_image when every call is one, else create_document."""
    for call in calls:
        if call.name != image_tool.TOOL_NAME and call.name in errors:
            return call.name
    if calls and all(call.name == image_tool.TOOL_NAME for call in calls):
        return image_tool.TOOL_NAME
    return document_tool.TOOL_NAME


def _unknown_tool(name: str, offered: list[str]) -> str:
    if len(offered) == 1:
        return f"Unknown tool: {name}. The only tool is {offered[0]}."
    return f"Unknown tool: {name}. The tools are {', '.join(offered[:-1])} and {offered[-1]}."


async def _produce(events: AsyncIterator[str], queue: "asyncio.Queue[str | None]") -> None:
    try:
        async for event in events:  # no await outside the generator: a cancel always lands inside it
            queue.put_nowait(event)
    finally:
        queue.put_nowait(None)


def _answer_task_done(task: "asyncio.Task[None]") -> None:
    _answer_tasks.discard(task)
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("An answer stream failed", exc_info=exc)


async def _sse_with_keepalive(turn: _Turn) -> AsyncIterator[str]:
    """The answer's events, produced by a task of its own, with a keepalive comment after _KEEPALIVE_SECONDS of
    silence. Closing this generator (the client went away) cancels the producer once, which then saves the partial
    answer; the task isn't inside the server's cancel scope, so nothing interrupts that save."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    producer = asyncio.create_task(_produce(_stream_answer(turn), queue))
    _answer_tasks.add(producer)
    producer.add_done_callback(_answer_task_done)
    refreshed = loop.time()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), _KEEPALIVE_SECONDS)
            except TimeoutError:
                yield _KEEPALIVE
            else:
                if event is None:
                    return
                yield event
            if turn.slot is not None and loop.time() - refreshed >= _SLOT_REFRESH_SECONDS:
                refreshed = loop.time()
                await refresh_chat_slot(turn.user.id, turn.slot)
    finally:
        producer.cancel()  # a no-op once it finished; otherwise this is Stop


@router.post("/stream")
async def chat_stream(
    body: ChatRequest, request: Request, user: User = Depends(get_entitled_user), db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    """402 without access (a conversation started before the plan ended included), then the rate limit, the chat
    allowance and the answer slot, all by the entitlements loaded now."""
    ent = current_entitlements(request)
    await check_rate_limit(user, ent)
    budget_left = await check_budget(db, user, ent)
    slot = await acquire_chat_slot(user, ent)
    try:
        return await _start_turn(body, user, ent, db, slot, budget_left)
    except BaseException:
        await release_chat_slot(user.id, slot)
        raise


async def _start_turn(
    body: ChatRequest, user: User, ent: Entitlements, db: AsyncSession, slot: str | None, budget_left: int | None
) -> StreamingResponse:
    model = await resolve_chat_model(db, user, body.model_id, ent)
    file_plan = await document_tool.plan(db, user, model, ent)
    image_plan = await image_tool.plan(db, user, model, ent)
    plugin_plan = await registry.plan_chat(db, user, model, ent)

    history: list[Message] = []
    if body.conversation_id is None:
        conversation = Conversation(user_id=user.id, title=_title_for(body.message))
        db.add(conversation)
        await db.flush()
    else:
        conversation = await get_owned_conversation(db, user, body.conversation_id)
        history = await _recent_messages(db, conversation.id)
        conversation.updated_at = utcnow()
    history_files = await _history_files(db, history)
    plugin_notes: dict[int, str] = {}
    history_tainted = False
    if history and registry.all_plugins():
        items = await registry.history_items(db, history)
        plugin_notes = await registry.history_notes(db, user, history, items)
        history_tainted = bool(await registry.tainted_messages(db, history, items))
        if not history_tainted and len(history) >= settings.history_messages:
            # Older answers left the window; one of them may have read email that the answers since repeat (P11).
            history_tainted = await registry.conversation_tainted(db, conversation.id)
    earlier_images: list[rag.HistoryFile] = []
    if body.conversation_id is not None and file_plan.tools and settings.chat_image_tools:
        earlier_images = await _earlier_images(db, user, conversation.id, history_files)
    user_message = Message(conversation_id=conversation.id, role="user", content=body.message)
    db.add(user_message)
    await db.commit()

    # The stream runs after this handler returns and uses sessions of its own. These ORM objects stay
    # readable without the request's session because SessionLocal doesn't expire them on commit.
    turn = _Turn(
        user=user,
        ent=ent,
        model=model,
        conversation_id=conversation.id,
        title=conversation.title,
        user_message_id=user_message.id,
        message=body.message,
        history=history,
        retrieval_query=_retrieval_query(history, body.message),
        document_ids=body.document_ids,
        slot=slot,
        file_plan=file_plan,
        image_plan=image_plan,
        history_files=history_files,
        earlier_images=earlier_images,
        budget_left=budget_left,
        plugin_plan=plugin_plan,
        plugin_notes=plugin_notes,
        history_tainted=history_tainted,
    )
    return StreamingResponse(_sse_with_keepalive(turn), media_type="text/event-stream", headers=_SSE_HEADERS)


async def _earlier_images(
    db: AsyncSession, user: User, conversation_id: int, history_files: dict[int, list[rag.HistoryFile]]
) -> list[rag.HistoryFile]:
    """The conversation's newest images, oldest first, that no answer in the history lists: their answers left the
    history window (HISTORY_MESSAGES), and the model never guesses a reference, so without this it couldn't embed
    them although they belong to this conversation. Deleted images aren't listed."""
    listed = {file.image_id for files in history_files.values() for file in files if file.image_id is not None}
    rows = await db.execute(
        select(GeneratedImage.document_id, Document.filename)
        .join(Document, Document.id == GeneratedImage.document_id)
        .where(GeneratedImage.conversation_id == conversation_id, Document.owner_id == user.id)
        .order_by(GeneratedImage.document_id.desc())
        .limit(_EARLIER_IMAGES_LISTED + len(listed))
    )
    found = [
        rag.HistoryFile(filename, image_id=document_id, image=True)
        for document_id, filename in rows.tuples()
        if document_id not in listed
    ]
    return found[:_EARLIER_IMAGES_LISTED][::-1]


async def _history_files(db: AsyncSession, history: list[Message]) -> dict[int, list[rag.HistoryFile]]:
    """message id -> the files and images that answer created, in creation order, each marked deleted once its document
    is gone (the link's document_id is then NULL); an image still there carries its reference id. One query."""
    ids = [message.id for message in history if message.role == "assistant"]
    if not ids:
        return {}
    rows = await db.execute(
        select(MessageFile.message_id, MessageFile.filename, MessageFile.document_id, MessageFile.format)
        .where(MessageFile.message_id.in_(ids))
        .order_by(MessageFile.id)
    )
    files: defaultdict[int, list[rag.HistoryFile]] = defaultdict(list)
    for message_id, filename, document_id, file_format in rows.tuples():
        image = file_format in _IMAGE_FORMATS
        files[message_id].append(
            rag.HistoryFile(
                filename,
                deleted=document_id is None,
                image_id=document_id if image else None,
                image=image,
            )
        )
    return dict(files)
