"""Images from chat through POST /api/chat/stream (docs/briefs/image-generation.md, section 18.3): the create_image
loop and its events, storing, serving and indexing images, embedding them in files, who is offered the tool, the
limits, failures, Stop, deletes, and the admin side (models, Test, users, usage, seeding)."""

import asyncio
import hashlib
import io
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import litellm
import pytest
from conftest import (
    USER_PASSWORD,
    ApiClient,
    FakeLLM,
    ScriptedCall,
    SseEvent,
    chat,
    fake_image,
    md_bytes,
    process,
    refusal,
    sse_events,
    tool_call,
    upload,
)
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import delete, select, update
from sqlalchemy.event import listen, remove

from app import db
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Document,
    GeneratedDocument,
    GeneratedImage,
    LLMModel,
    Message,
    MessageFile,
    UsageEvent,
    UserImageLimit,
)
from app.routers import chat as chat_router
from app.routers.chat import _Turn
from app.schemas import MAX_INT
from app.services import document_tool, image_tool, llm, rag, storage

CreateUser = Callable[..., Awaitable[ApiClient]]
StartTurn = Callable[..., Awaitable[_Turn]]

PROMPT = "A minimalist orange fox head, flat vector logo on a white background."
ALT = "An orange fox head logo."
STOPPED = "Stopped before completion"
APP_NOTE = 'Notes in parentheses that start with "App note:" in earlier answers were added by the app.'
IMAGE_PROMPT_START = "You can generate images with the create_image tool."
SAVED = (
    "The image is shown to the user in the chat, below your reply, and saved to their private documents. You can't "
    "see it, so don't describe details the prompt didn't ask for. To put it in a pdf or docx file, use create_document "
    "with the markdown reference on a line of its own."
)
NO_IMAGE_MODEL = image_tool._NO_IMAGE_MODEL_NOTE
BROKEN = image_tool._IMAGE_BROKEN_NOTE
REFUSED = "The image model's safety system declined this request, so no image was generated."
HALTED = "Image generation stopped for this answer after the error above, so this image wasn't generated."
GPT_IMAGE_COST = 50 * 5e-6 + 272 * 3e-5  # LiteLLM's prices for gpt-image-2, FakeLLM's default usage


def image_call(title: str = "Fox logo", prompt: str = PROMPT, **extra: Any) -> tuple[str, Any]:
    return tool_call("create_image", prompt=prompt, title=title, alt_text=ALT, **extra)


def names(events: list[SseEvent]) -> list[str]:
    return [name for name, _ in events]


def file_events(events: list[SseEvent]) -> list[dict[str, Any]]:
    return [data for name, data in events if name == "file"]


def created_events(events: list[SseEvent]) -> list[dict[str, Any]]:
    return [data for data in file_events(events) if data["status"] == "created"]


def failed_events(events: list[SseEvent]) -> list[dict[str, Any]]:
    return [data for data in file_events(events) if data["status"] == "failed"]


def tool_results(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [json.loads(message["content"]) for message in call["messages"] if message["role"] == "tool"]


def system_prompt(call: dict[str, Any]) -> str:
    return call["messages"][0]["content"]


def tool_names(call: dict[str, Any]) -> list[str]:
    return [tool["function"]["name"] for tool in call.get("tools") or []]


async def rows(model: Any, *conditions: Any) -> list[Any]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(model).where(*conditions)))


async def image_usage(user_id: int | None = None) -> list[UsageEvent]:
    conditions = [UsageEvent.kind == "image"]
    if user_id is not None:
        conditions.append(UsageEvent.user_id == user_id)
    return await rows(UsageEvent, *conditions)


def stored_files() -> list[str]:
    return sorted(path.name for path in storage.upload_dir().iterdir()) if storage.upload_dir().exists() else []


async def jobs_settled() -> None:
    """Wait for the image jobs and the releases they spawned."""
    for _ in range(3):
        await asyncio.gather(*image_tool.running_jobs(), *list(image_tool._spawned), return_exceptions=True)
        await asyncio.sleep(0)


async def answers_settled() -> None:
    await asyncio.gather(*list(chat_router._answer_tasks), return_exceptions=True)
    await asyncio.gather(*list(chat_router._pending_saves), return_exceptions=True)


async def eventually(condition: Callable[[], Any], seconds: float = 30) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not condition():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def read_until(stream: AsyncIterator[str], marker: str) -> list[str]:
    received: list[str] = []
    while marker not in "".join(received):
        received.append(await asyncio.wait_for(anext(stream), 30))
    return received


async def consume(stream: AsyncIterator[str]) -> list[SseEvent]:
    """Read a whole answer stream (one started with `start_turn`) and parse its events."""
    return sse_events(httpx.Response(200, text="".join([event async for event in stream])))


async def _no_events() -> AsyncIterator[str]:
    for event in ():
        yield event


_real_sse_with_keepalive = chat_router._sse_with_keepalive


@pytest.fixture
def start_turn(monkeypatch: pytest.MonkeyPatch) -> StartTurn:
    """POST /api/chat/stream without consuming its stream; the test drives `_sse_with_keepalive(turn)` (closing it
    is a client disconnect, which the server treats as Stop)."""
    turns: list[_Turn] = []

    def capture(turn: _Turn) -> AsyncIterator[str]:
        turns.append(turn)
        return _no_events()

    monkeypatch.setattr(chat_router, "_sse_with_keepalive", capture)

    async def start(client: ApiClient, message: str, **fields: Any) -> _Turn:
        response, _ = await chat(client, message, **fields)
        assert response.status_code == 200, response.text
        return turns[-1]

    return start


async def image_model_row() -> LLMModel:
    [model] = await rows(LLMModel, LLMModel.kind == "image")
    return model


async def models_by_name(client: ApiClient) -> dict[str, dict[str, Any]]:
    return {model["display_name"]: model for model in (await client.get("/api/models")).json()}


async def one_image(client: ApiClient, fake_llm: FakeLLM, **call: Any) -> tuple[list[SseEvent], dict[str, Any]]:
    """An answer that generates one image; returns its events and the created event."""
    fake_llm.script = [ScriptedCall([image_call(**call)]), ScriptedCall([("text", "Here it is.")])]
    response, events = await chat(client, "Draw a fox logo", document_ids=[])
    assert response.status_code == 200, response.text
    [created] = created_events(events)
    return events, created


# ---------------- One image, end to end ----------------


async def test_one_image_is_generated_shown_saved_and_indexed(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient
) -> None:
    fake_llm = image_tools
    alice = await create_user("alice")
    fake_llm.image_revised_prompt = "A fox, revised by the model"
    fake_llm.script = [
        ScriptedCall([("text", "Drawing it."), image_call()], usage=(900, 80)),
        ScriptedCall([("text", "I made a fox logo.")], usage=(1200, 20)),
    ]

    response, events = await chat(alice, "Draw a fox logo", document_ids=[])

    assert names(events) == ["meta", "sources", "delta", "file", "file", "file", "file", "delta", "delta", "done"]
    started, validated, generating, created = file_events(events)
    assert started == {"call_id": "i1", "kind": "image", "status": "writing", "title": None, "format": None, "chars": 0}
    assert validated == {
        "call_id": "i1",
        "kind": "image",
        "status": "writing",
        "title": "Fox logo",
        "format": None,
        "chars": len(PROMPT),
    }
    assert generating == {
        "call_id": "i1",
        "kind": "image",
        "status": "generating",
        "title": "Fox logo",
        "width": 1024,
        "height": 1024,
        "quality": "medium",
    }
    shown = {"width": 64, "height": 64, "alt_text": ALT, "quality": "medium"}
    assert (created["call_id"], created["kind"], created["status"], created["already_existed"]) == (
        "i1",
        "image",
        "created",
        False,
    )
    file, document = created["file"], created["document"]
    doc_id = file["document_id"]
    [doc] = await rows(Document, Document.id == doc_id)
    assert file == {
        "document_id": doc_id,
        "filename": "Fox logo.png",
        "format": "png",
        "size_bytes": doc.size_bytes,
        "status": "pending",
        "image": shown,
    }
    assert (document["id"], document["generated"], document["image"], document["content_type"]) == (
        doc_id,
        True,
        shown,
        "image/png",
    )
    assert [data["content"] for name, data in events if name == "delta"] == [
        "Drawing it.",
        "\n\n",
        "I made a fox logo.",
    ]

    [image_request] = fake_llm.image_calls
    timeout = image_request.pop("timeout")  # the time left of IMAGE_TIMEOUT_SECONDS, plus 30 s
    assert settings.image_timeout_seconds + 29 < timeout <= settings.image_timeout_seconds + 30
    assert image_request == {
        "model": "openai/gpt-image-2",
        "prompt": PROMPT,
        "n": 1,
        "size": "1024x1024",
        "quality": "medium",
        "output_format": "png",
    }
    first, second = fake_llm.chat_calls
    assert tool_names(first) == ["create_document", "create_image"] and first["tools"] == second["tools"]
    assert (first["tool_choice"], first["parallel_tool_calls"], first["max_tokens"]) == ("auto", False, 48_000)
    assert IMAGE_PROMPT_START in system_prompt(first) and system_prompt(first) == system_prompt(second)
    assert "The user can generate 30 more images this month." in system_prompt(first)
    [result] = tool_results(second)
    assert result == {
        "ok": True,
        "image_id": doc_id,
        "filename": "Fox logo.png",
        "width": 64,
        "height": 64,
        "quality": "medium",
        "markdown": f"![Fox logo](image:{doc_id})",
        "note": f"{SAVED} The user can generate 29 more images this month.",
    }

    [event] = await image_usage(alice.user_id)
    model = await image_model_row()
    assert (event.model_id, event.model_name, event.prompt_tokens, event.completion_tokens) == (
        model.id,
        "openai/gpt-image-2",
        50,
        272,
    )
    assert event.cost_usd == pytest.approx(GPT_IMAGE_COST)
    [chat_event] = await rows(UsageEvent, UsageEvent.kind == "chat")
    assert (chat_event.prompt_tokens, chat_event.completion_tokens) == (2100, 100)
    assert events[-1][1]["usage"]["total_tokens"] == 2200  # the chat calls only

    assert (doc.owner_id, doc.scope, doc.filename, doc.content_type, doc.status) == (
        alice.user_id,
        "private",
        "Fox logo.png",
        "image/png",
        "pending",
    )
    data = storage.stored_path(doc.stored_name).read_bytes()
    assert (len(data), hashlib.sha256(data).hexdigest()) == (doc.size_bytes, doc.sha256)
    assert doc.stored_name.endswith(".png") and b"trainedAlgorithmicMedia" in data
    with Image.open(io.BytesIO(data)) as stored:
        assert (stored.format, stored.size) == ("PNG", (64, 64))
    for sidecar in (storage.source_name, storage.preview_name, storage.thumb_name):
        assert storage.stored_path(sidecar(doc.stored_name)).is_file()
    source = storage.stored_path(storage.source_name(doc.stored_name)).read_text()
    assert source.startswith("# Fox logo\n\nAI-generated image, made in chat with GPT Image 2 on ")
    assert (
        ALT in source and "Prompt: A minimalist orange fox head" in source and "A fox, revised by the model" in source
    )
    assert len(await rows(GeneratedDocument, GeneratedDocument.document_id == doc_id)) == 1
    [row] = await rows(GeneratedImage, GeneratedImage.document_id == doc_id)
    assert (row.conversation_id, row.prompt, row.revised_prompt, row.alt_text) == (
        events[0][1]["conversation_id"],
        PROMPT,
        "A fox, revised by the model",
        ALT,
    )
    assert (row.format, row.quality, row.width, row.height, row.model_name) == (
        "png",
        "medium",
        64,
        64,
        "openai/gpt-image-2",
    )

    listed = next(item for item in (await alice.get("/api/documents")).json() if item["id"] == doc_id)
    assert listed["generated"] is True and listed["image"] == shown
    assert "prompt" not in json.dumps(listed)
    detail = (await alice.get(f"/api/conversations/{events[0][1]['conversation_id']}")).json()
    assert detail["messages"][1]["files"] == [file]
    me = (await alice.get("/api/auth/me")).json()
    assert (me["limits"]["monthly_image_limit"], me["usage"]["images"]) == (30, 1)
    assert me["usage"]["chat_tokens"] == 2200 and me["usage"]["cost_usd"] > GPT_IMAGE_COST

    indexed = await process(doc_id)
    assert indexed is not None and (indexed.status, indexed.num_pages) == ("ready", None)
    assert fake_llm.vision_calls == []
    fake_llm.script = []
    await chat(alice, "Do I have an orange fox head logo?")
    prompt = system_prompt(fake_llm.chat_calls[-1])
    assert "(source: Fox logo.png, an image the assistant generated on " in prompt
    assert "its title, description and prompt, not a factual source" in prompt
    assert rag._IMAGE_PASSAGES_NOTE in prompt and rag._GENERATED_PASSAGES_NOTE not in prompt


async def test_jpeg_and_webp_are_stored_as_returned(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    for output_format, extension, content_type in (("jpeg", ".jpg", "image/jpeg"), ("webp", ".webp", "image/webp")):
        monkeypatch.setattr(settings, "image_output_format", output_format)
        _, created = await one_image(alice, image_tools, title=f"Fox {output_format}", aspect="portrait")
        assert created["file"]["format"] == output_format
        assert created["file"]["filename"] == f"Fox {output_format}{extension}"
        assert created["document"]["content_type"] == content_type
        assert image_tools.image_calls[-1]["output_compression"] == 90
        assert created["file"]["image"]["width"] == 64 and created["file"]["image"]["height"] == 96
        [doc] = await rows(Document, Document.id == created["file"]["document_id"])
        with Image.open(storage.stored_path(doc.stored_name)) as stored:
            assert stored.format == {"jpeg": "JPEG", "webp": "WEBP"}[output_format]


# ---------------- Serving images ----------------


async def test_the_image_endpoint_serves_renditions_with_revalidation(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    _, created = await one_image(alice, image_tools)
    doc_id = created["file"]["document_id"]
    [doc] = await rows(Document, Document.id == doc_id)
    url = f"/api/documents/{doc_id}/image"

    full, preview, thumb = [await alice.get(url, params={"size": size}) for size in ("full", "preview", "thumb")]
    default = await alice.get(url)
    assert default.content == full.content == storage.stored_path(doc.stored_name).read_bytes()
    assert preview.content == storage.stored_path(storage.preview_name(doc.stored_name)).read_bytes()
    assert thumb.content == storage.stored_path(storage.thumb_name(doc.stored_name)).read_bytes()
    assert [response.headers["content-type"] for response in (full, preview, thumb)] == [
        "image/png",
        "image/webp",
        "image/webp",
    ]
    for response in (full, preview, thumb):
        assert response.status_code == 200
        assert response.headers["content-disposition"] == (
            "inline; filename=\"Fox logo.png\"; filename*=UTF-8''Fox%20logo.png"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-cache"
        assert response.headers["content-security-policy"] == "sandbox"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
    tags = [response.headers["etag"] for response in (full, preview, thumb)]
    assert len(set(tags)) == 3
    assert tags[0] == f'"{doc.sha256[:32]}-full-{len(full.content)}"'
    assert tags[2] == f'"{doc.sha256[:32]}-thumb-{len(thumb.content)}"'

    for header in (tags[0], f"W/{tags[0]}", f'"other", {tags[0]}', f'W/"x" ,W/{tags[0]} ', "*"):
        cached = await alice.get(url, headers={"If-None-Match": header})
        assert cached.status_code == 304, header
        assert cached.content == b"" and cached.headers["etag"] == tags[0]
        assert cached.headers["cache-control"] == "private, no-cache"
    for header in ('"other", "nope"', tags[1], f"{tags[0][1:-1]}", f'"{tags[0]}"'):
        assert (await alice.get(url, headers={"If-None-Match": header})).status_code == 200, header

    not_found = {"detail": "Document not found"}
    for client in (bob, admin_client):
        response = await client.get(url)
        assert (response.status_code, response.json()) == (404, not_found)
        assert (await client.get(f"{url}/details")).status_code == 404
    assert (await alice.get(url, params={"size": "huge"})).status_code == 422
    uploaded = (await upload(alice, "notes.md", md_bytes("Some notes"))).json()
    response = await alice.get(f"/api/documents/{uploaded['id']}/image")
    assert (response.status_code, response.json()) == (404, {"detail": "This document isn't an image."})

    storage.stored_path(storage.preview_name(doc.stored_name)).unlink()
    fallback = await alice.get(url, params={"size": "preview"})
    assert fallback.content == full.content and fallback.headers["content-type"] == "image/png"
    assert fallback.headers["etag"] == tags[0]

    download = await alice.get(f"/api/documents/{doc_id}/download")
    assert download.headers["content-type"] == "image/png" and download.content == full.content
    assert download.headers["content-disposition"].startswith("attachment;")

    storage.stored_path(doc.stored_name).unlink()
    missing = await alice.get(url)
    assert (missing.status_code, missing.json()) == (404, {"detail": "The file is missing from storage."})


async def test_the_details_are_the_owners_only(create_user: CreateUser, image_tools: FakeLLM) -> None:
    alice = await create_user("alice")
    image_tools.image_revised_prompt = "Revised"
    _, created = await one_image(alice, image_tools)
    doc_id = created["file"]["document_id"]

    response = await alice.get(f"/api/documents/{doc_id}/image/details")

    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    details = response.json()
    assert (details["prompt"], details["revised_prompt"], details["model_name"]) == (
        PROMPT,
        "Revised",
        "openai/gpt-image-2",
    )
    assert details["created_at"].endswith("Z")


async def test_admin_documents_list_images_without_their_details(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    _, created = await one_image(alice, image_tools)
    [item] = (await admin_client.get("/api/admin/documents")).json()
    assert (item["id"], item["generated"], item["image"]) == (created["file"]["document_id"], True, None)


# ---------------- Both tools ----------------


async def test_both_tools_are_offered_together(create_user: CreateUser, image_tools: FakeLLM) -> None:
    fake_llm = image_tools
    alice = await create_user("alice")
    fake_llm.script = [ScriptedCall([tool_call("search_web", query="x")]) for _ in range(7)]
    fake_llm.script.append(ScriptedCall([("text", "Done.")]))

    response, events = await chat(alice, "Search the web", document_ids=[])

    calls = fake_llm.chat_calls
    assert len(calls) == 3 + 3 + 2
    assert all(tool_names(call) == ["create_document", "create_image"] for call in calls)
    assert [call["tool_choice"] for call in calls] == ["auto"] * 7 + ["none"]
    assert all(call["max_tokens"] == 48_000 for call in calls[:7])
    prompt = system_prompt(calls[0])
    assert "You can create files with the create_document tool" in prompt and IMAGE_PROMPT_START in prompt
    assert prompt.index("create_document tool") < prompt.index(IMAGE_PROMPT_START)
    assert prompt.count(APP_NOTE) == 1
    assert "embedded in pdf and docx files with their reference from create_image" in prompt
    assert tool_results(calls[1]) == [
        {"ok": False, "error": "Unknown tool: search_web. The tools are create_document and create_image."}
    ]
    assert all(system_prompt(call) == prompt for call in calls)


async def test_an_image_is_embedded_in_a_pdf_of_the_same_answer(create_user: CreateUser, image_tools: FakeLLM) -> None:
    fake_llm = image_tools
    alice = await create_user("alice")
    fake_llm.script = [
        ScriptedCall([image_call()]),
        ScriptedCall(
            [
                tool_call(
                    title="Logo sheet",
                    format="pdf",
                    content="# Logo sheet\n\nThe concept:\n\n![The fox logo](image:1)\n\nAlso ![x](image:999)\n",
                )
            ]
        ),
        ScriptedCall([("text", "Both are in your documents.")]),
    ]

    response, events = await chat(alice, "Draw a fox logo and put it in a PDF", document_ids=[])

    image, pdf = created_events(events)
    assert (image["kind"], image["file"]["document_id"], pdf["kind"], pdf["file"]["format"]) == (
        "image",
        1,
        "file",
        "pdf",
    )
    results = tool_results(fake_llm.chat_calls[2])
    assert results[1]["ok"] is True
    assert "Embedded 1 image." in results[1]["note"]
    assert (
        "These image references don't match an image generated in this conversation and were left as text: "
        "image:999." in results[1]["note"]
    )
    [pdf_doc] = await rows(Document, Document.id == pdf["file"]["document_id"])
    reader = PdfReader(storage.stored_path(pdf_doc.stored_name))
    assert sum(len(page.images) for page in reader.pages) == 1
    source = storage.stored_path(storage.source_name(pdf_doc.stored_name)).read_text()
    assert "\\[Image: The fox logo\\] (Fox logo.png)" in source  # a text reference in the indexed Markdown

    answer = max(await rows(Message, Message.role == "assistant"), key=lambda message: message.id)
    linked = await rows(MessageFile, MessageFile.message_id == answer.id)
    assert [(row.filename, row.format) for row in linked] == [("Fox logo.png", "png"), ("Logo sheet.pdf", "pdf")]

    fake_llm.script = [ScriptedCall([("text", "Sure.")])]
    await chat(alice, "Thanks", conversation_id=events[0][1]["conversation_id"], document_ids=[])
    history = fake_llm.chat_calls[-1]["messages"]
    assert history[-2]["content"].endswith(
        '(App note: this answer created these files, which are in the user\'s documents: "Logo sheet.pdf". It '
        'generated these images, which are in the user\'s documents: "Fox logo.png" (image:1).)'
    )


async def test_references_to_other_conversations_and_users_stay_text(
    create_user: CreateUser, image_tools: FakeLLM
) -> None:
    fake_llm = image_tools
    alice, bob = await create_user("alice"), await create_user("bob")
    _, bobs = await one_image(bob, fake_llm)
    _, earlier = await one_image(alice, fake_llm)
    ids = [bobs["file"]["document_id"], earlier["file"]["document_id"]]
    content = "# Sheet\n\n" + "\n\n".join(f"![x](image:{image_id})" for image_id in ids)
    fake_llm.script = [
        ScriptedCall([tool_call(title="Sheet", format="docx", content=content)]),
        ScriptedCall([("text", "Done.")]),
    ]

    await chat(alice, "Put my logo in a Word file", document_ids=[])  # a new conversation

    [result] = tool_results(fake_llm.chat_calls[-1])
    listed = ", ".join(f"image:{image_id}" for image_id in ids)
    assert result["ok"] is True and "Embedded" not in result["note"]
    assert f"were left as text: {listed}." in result["note"]


async def test_references_beyond_the_integer_range_stay_text(create_user: CreateUser, image_tools: FakeLLM) -> None:
    """documents.id is a Postgres INTEGER and asyncpg refuses to send a larger value, which used to fail the whole
    file with a server error. SQLite takes such values, so a listener stands in for asyncpg."""
    alice = await create_user("alice")
    queried: list[Any] = []

    def like_asyncpg(_conn: Any, _cursor: Any, statement: str, parameters: Any, _context: Any, _many: bool) -> None:
        if "generated_images" not in statement:
            return
        values = list(parameters.values()) if isinstance(parameters, dict) else list(parameters or ())
        queried.extend(values)
        if any(type(value) is int and value > MAX_INT for value in values):
            raise OverflowError("invalid input for query argument: value out of int32 range")

    content = "# Logo sheet\n\n![Fox](image:1)\n\n![Big](image:2147483648)\n\n![Bigger](image:9999999999)"
    image_tools.script = [
        ScriptedCall([image_call()]),
        ScriptedCall([tool_call(title="Logo sheet", format="pdf", content=content)]),
        ScriptedCall([("text", "Done.")]),
    ]
    listen(db.engine.sync_engine, "before_cursor_execute", like_asyncpg)
    try:
        response, events = await chat(alice, "Draw a fox logo and put it in a PDF", document_ids=[])
    finally:
        remove(db.engine.sync_engine, "before_cursor_execute", like_asyncpg)

    image, pdf = created_events(events)
    [_, result] = tool_results(image_tools.chat_calls[-1])
    assert result["ok"] is True and "Embedded 1 image." in result["note"]
    assert result["note"].endswith(
        "These image references don't match an image generated in this conversation and were left as text: "
        "image:2147483648, image:9999999999."
    )
    assert 1 in queried and all(type(value) is not int or value <= MAX_INT for value in queried)


async def test_out_of_range_ids_are_never_queried(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")

    def no_session() -> None:
        raise AssertionError("queried")

    monkeypatch.setattr(image_tool, "SessionLocal", no_session)
    assert await image_tool.resolve_images(alice.user_id, 1, [0, MAX_INT + 1, 9_999_999_999]) == []


async def test_many_references_are_resolved_in_chunks(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    events, created = await one_image(alice, image_tools)
    image_id, conversation_id = created["file"]["document_id"], events[0][1]["conversation_id"]
    monkeypatch.setattr(image_tool, "_RESOLVE_CHUNK", 2)

    ids = [500, 501, 502, 503, image_id, 504, image_id]
    resolved = await image_tool.resolve_images(alice.user_id, conversation_id, ids)

    assert [image.id for image in resolved] == [image_id, image_id]


# ---------------- Images only (CHAT_FILE_TOOLS off) ----------------


async def test_images_only(create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_file_tools", False)
    alice = await create_user("alice")
    image_tools.script = [ScriptedCall([tool_call("search_web", query="x")]) for _ in range(4)]
    image_tools.script.append(ScriptedCall([("text", "Done.")]))

    await chat(alice, "Search", document_ids=[])

    calls = image_tools.chat_calls
    assert len(calls) == 3 + 2
    assert all(tool_names(call) == ["create_image"] for call in calls)
    assert [call["tool_choice"] for call in calls] == ["auto"] * 4 + ["none"]
    assert all(call["max_tokens"] == 16_000 for call in calls)
    prompt = system_prompt(calls[0])
    assert prompt.count(APP_NOTE) == 1 and "create_document" not in prompt
    assert prompt.index(IMAGE_PROMPT_START) < prompt.index(APP_NOTE)
    assert tool_results(calls[1]) == [
        {"ok": False, "error": "Unknown tool: search_web. The only tool is create_image."}
    ]


async def test_images_only_tools_rejected_are_retried_without_them(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_file_tools", False)
    alice = await create_user("alice")
    rejected = litellm.BadRequestError(message="tools: unsupported", model="claude-opus-5-5", llm_provider="anthropic")
    image_tools.script = [ScriptedCall(error=rejected, before_output=True), ScriptedCall([("text", "Answer.")])]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    assert events[-1][0] == "done"
    first, retry = image_tools.chat_calls
    assert tool_names(first) == ["create_image"] and "tools" not in retry
    assert first["max_tokens"] == retry["max_tokens"] == 16_000
    prompt = system_prompt(retry)
    assert image_tool.NO_IMAGE_TOOLS_NOTE in prompt and prompt.count(APP_NOTE) == 1
    assert IMAGE_PROMPT_START not in prompt
    models = await models_by_name(alice)
    assert models["Claude Opus 5.5"]["can_create_images"] is False
    assert models["Claude Sonnet 5.5"]["can_create_images"] is True


async def test_a_rejected_max_tokens_keeps_both_tools(create_user: CreateUser, image_tools: FakeLLM) -> None:
    alice = await create_user("alice")
    rejected = litellm.BadRequestError(
        message="max_tokens: 48000 > 32000, which is the maximum allowed", model="claude-opus-5-5", llm_provider="x"
    )
    image_tools.script = [ScriptedCall(error=rejected, before_output=True), ScriptedCall([("text", "Answer.")])]

    await chat(alice, "Hello", document_ids=[])

    first, retry = image_tools.chat_calls
    assert tool_names(retry) == ["create_document", "create_image"] and retry["max_tokens"] == 16_000
    prompt = system_prompt(retry)
    assert document_tool.file_instructions(16_000, True) in prompt and IMAGE_PROMPT_START in prompt


# ---------------- Who is offered the tool ----------------


async def test_the_tool_is_not_offered_when_switched_off(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    alice = await create_user("alice")
    await chat(alice, "Draw a fox", document_ids=[])
    [call] = fake_llm.chat_calls
    assert tool_names(call) == ["create_document"]
    assert "image" not in system_prompt(call).lower().replace("images, html", "")
    assert all(model["can_create_images"] is False for model in (await models_by_name(alice)).values())


@pytest.mark.parametrize("situation", ["disabled", "unsupported", "unusable"])
async def test_without_a_usable_image_model_the_prompt_says_why(
    create_user: CreateUser, image_tools: FakeLLM, situation: str
) -> None:
    alice = await create_user("alice")
    model = await image_model_row()
    async with SessionLocal() as db:
        if situation == "disabled":
            await db.execute(update(LLMModel).where(LLMModel.id == model.id).values(enabled=False))
        elif situation == "unsupported":  # only possible by editing the database: the admin API refuses it
            await db.execute(update(LLMModel).where(LLMModel.id == model.id).values(litellm_model="openai/dall-e-3"))
        await db.commit()
    if situation == "unusable":
        image_tool.mark_unusable(model, "The image model returned no image data.")

    await chat(alice, "Draw a fox", document_ids=[])

    [call] = image_tools.chat_calls
    assert tool_names(call) == ["create_document"]
    assert (NO_IMAGE_MODEL if situation == "disabled" else BROKEN) in system_prompt(call)
    assert all(model["can_create_images"] is False for model in (await models_by_name(alice)).values())


async def test_a_model_without_tools_gets_a_note(create_user: CreateUser, image_tools: FakeLLM) -> None:
    alice = await create_user("alice")
    llm._tool_support["anthropic/claude-opus-5-5"] = False
    await chat(alice, "Draw a fox", document_ids=[])
    [call] = image_tools.chat_calls
    assert "tools" not in call and image_tool.NO_IMAGE_TOOLS_NOTE in system_prompt(call)
    assert (await models_by_name(alice))["Claude Opus 5.5"]["can_create_images"] is False


@pytest.mark.parametrize(
    ("limits", "uploads", "note"),
    [
        ({"max_storage_mb": 8}, 1, "their storage is almost full"),
        ({"max_documents": 1}, 1, "they have reached their limit of 1 documents"),
        ({"monthly_image_limit": 2}, 0, "The user has used all 2 images of their monthly image allowance."),
    ],
)
async def test_users_without_room_or_images_left_are_told_why(
    create_user: CreateUser, image_tools: FakeLLM, limits: dict[str, int], uploads: int, note: str
) -> None:
    alice = await create_user("alice", **limits)
    for number in range(uploads):
        assert (await upload(alice, f"n{number}.md", md_bytes(f"note {number}"))).status_code == 201
    if "monthly_image_limit" in limits:
        async with SessionLocal() as db:
            for _ in range(2):
                db.add(UsageEvent(user_id=alice.user_id, model_name="openai/gpt-image-2", kind="image"))
            await db.commit()

    await chat(alice, "Draw a fox", document_ids=[])

    call = image_tools.chat_calls[-1]
    assert tool_names(call) == ([] if "max_documents" in limits else ["create_document"])
    assert note in system_prompt(call) and IMAGE_PROMPT_START not in system_prompt(call)
    assert (await models_by_name(alice))["Claude Opus 5.5"]["can_create_images"] is True  # quotas are ignored there


async def test_the_admin_is_always_offered_the_tool(admin_client: ApiClient, image_tools: FakeLLM) -> None:
    await chat(admin_client, "Draw a fox", document_ids=[])
    [call] = image_tools.chat_calls
    assert tool_names(call) == ["create_document", "create_image"]
    assert "more images this month" not in system_prompt(call)


# ---------------- Limits ----------------


async def test_at_most_three_images_per_answer(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_file_tools", False)  # so no tool is left to offer after three images
    alice = await create_user("alice")
    image_tools.script = [
        ScriptedCall([image_call("One")]),
        ScriptedCall([image_call("Two")]),
        ScriptedCall([image_call("Three"), image_call("Four")]),
        ScriptedCall([("text", "Three images.")]),
    ]

    response, events = await chat(alice, "Draw four foxes", document_ids=[])

    assert [data["file"]["filename"] for data in created_events(events)] == ["One.png", "Two.png", "Three.png"]
    [failed] = failed_events(events)
    assert (failed["call_id"], failed["error"]) == (
        "i4",
        "Only 3 images can be generated per answer; this one wasn't generated.",
    )
    assert len(image_tools.image_calls) == 3
    assert [call["tool_choice"] for call in image_tools.chat_calls] == ["auto", "auto", "auto", "none"]
    assert tool_results(image_tools.chat_calls[-1])[-1] == {
        "ok": False,
        "error": "Only 3 images can be generated per answer; this one wasn't generated. Tell the user.",
    }


async def test_the_monthly_limit_reached_mid_answer(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice", monthly_image_limit=1)
    real_completion = litellm.acompletion

    async def another_answer_used_it(**kwargs: Any) -> Any:
        if len(image_tools.chat_calls) == 0:
            async with SessionLocal() as db:
                db.add(UsageEvent(user_id=alice.user_id, model_name="openai/gpt-image-2", kind="image"))
                await db.commit()
        return await real_completion(**kwargs)

    monkeypatch.setattr(litellm, "acompletion", another_answer_used_it)
    image_tools.script = [
        ScriptedCall([image_call("One")]),
        ScriptedCall([image_call("Two")]),
        ScriptedCall([("text", "Sorry.")]),
    ]

    response, events = await chat(alice, "Draw two foxes", document_ids=[])

    first, second = failed_events(events)
    assert first["error"] == (
        "The user has used all 1 images of their monthly allowance, so the image wasn't generated. It resets on the "
        "1st of next month (UTC); the admin can raise it."
    )
    assert second["error"] == HALTED
    assert image_tools.image_calls == [] and len(await image_usage(alice.user_id)) == 1


async def test_images_dont_count_toward_the_token_budget(create_user: CreateUser, image_tools: FakeLLM) -> None:
    alice = await create_user("alice", monthly_token_limit=1000)
    image_tools.image_usage = (5000, 50_000)
    image_tools.script = [
        ScriptedCall([image_call()], usage=(100, 20)),
        ScriptedCall([("text", "Done.")], usage=(100, 5)),
    ]
    await chat(alice, "Draw a fox", document_ids=[])
    me = (await alice.get("/api/auth/me")).json()
    assert (me["usage"]["chat_tokens"], me["usage"]["images"]) == (225, 1)
    response, _ = await chat(alice, "Another question", document_ids=[])
    assert response.status_code == 200  # 775 chat tokens left; the image's 55,000 tokens don't count


async def test_a_paid_image_is_stored_even_over_the_document_limit(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient
) -> None:
    alice = await create_user("alice", max_documents=5)
    assert (await upload(alice, "notes.md", md_bytes("notes"))).status_code == 201
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([("text", "Done.")])]

    answer = asyncio.create_task(chat(alice, "Draw a fox", document_ids=[]))
    await eventually(lambda: image_tools.image_calls)
    lowered = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"max_documents": 1})
    assert lowered.status_code == 200
    image_tools.image_gate.set()
    response, events = await answer

    [created] = created_events(events)
    [result] = tool_results(image_tools.chat_calls[-1])
    assert result["ok"] is True and result["note"].endswith(
        "The user's documents are now over their limit of 1 documents, so they must delete documents before they can "
        "add more."
    )
    assert len(await rows(Document, Document.owner_id == alice.user_id)) == 2


# ---------------- Failures (section 17) ----------------


async def test_a_refusal_is_released_and_a_second_image_still_works(
    create_user: CreateUser, image_tools: FakeLLM
) -> None:
    alice = await create_user("alice")
    image_tools.image_error = lambda number: refusal() if number == 1 else None
    image_tools.script = [
        ScriptedCall([image_call("Fox")]),
        ScriptedCall([image_call("Calmer fox")]),
        ScriptedCall([("text", "Done.")]),
    ]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    [failed] = failed_events(events)
    assert (failed["call_id"], failed["kind"], failed["error"]) == ("i1", "image", REFUSED)
    assert [data["file"]["filename"] for data in created_events(events)] == ["Calmer fox.png"]
    first_result = tool_results(image_tools.chat_calls[1])[0]
    assert first_result == {
        "ok": False,
        "error": f"{REFUSED} Tell the user; don't retry with a reworded prompt unless they ask.",
    }
    [event] = await image_usage(alice.user_id)  # the refused one was released
    assert event.cost_usd == pytest.approx(GPT_IMAGE_COST)


async def test_a_timeout_is_kept_with_an_estimated_cost_and_halts_the_answer(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "image_timeout_seconds", 0.3)
    alice = await create_user("alice")
    image_tools.image_delay = 30
    image_tools.script = [
        ScriptedCall([image_call("Fox")]),
        ScriptedCall([image_call("Fox again")]),
        ScriptedCall([("text", "Sorry.")]),
    ]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    first, second = failed_events(events)
    assert first["error"] == (
        "Generating the image took longer than 0 seconds, so it was abandoned. The provider may still have charged for "
        "it, so it counts toward the user's monthly images."
    )
    assert second["error"] == HALTED
    assert len(image_tools.image_calls) == 1
    [event] = await image_usage(alice.user_id)
    assert event.completion_tokens == 1056 and event.cost_usd > 0  # estimated: square, medium
    results = [result["error"] for result in tool_results(image_tools.chat_calls[-1])]
    assert results[0].endswith("Tell the user; don't retry in this answer.")
    assert results[1] == f"{HALTED} Tell the user; they can ask again in a new message."


async def test_a_rate_limit_is_released_and_halts(create_user: CreateUser, image_tools: FakeLLM) -> None:
    alice = await create_user("alice")
    image_tools.image_error = litellm.RateLimitError(message="slow down", model="gpt-image-2", llm_provider="openai")
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([image_call()]), ScriptedCall([("text", ".")])]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    first, second = failed_events(events)
    assert first["error"] == "The image model is busy right now (rate limited), so no image was generated."
    assert second["error"] == HALTED and len(image_tools.image_calls) == 1
    assert await image_usage(alice.user_id) == []


async def test_a_url_only_response_is_kept_and_marks_the_model_unusable(
    create_user: CreateUser, image_tools: FakeLLM
) -> None:
    alice = await create_user("alice")
    image_tools.image_url_only = True
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([("text", "Sorry.")])]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    [failed] = failed_events(events)
    assert failed["error"] == (
        "The image model returned no image data. It may still have been charged, so it counts toward the user's "
        "monthly images."
    )
    [event] = await image_usage(alice.user_id)
    assert event.cost_usd == pytest.approx(GPT_IMAGE_COST)
    assert await rows(Document) == []

    image_tools.script = []
    await chat(alice, "Try again", document_ids=[])
    assert BROKEN in system_prompt(image_tools.chat_calls[-1])
    assert all(not model["can_create_images"] for model in (await models_by_name(alice)).values())


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 32, "isn't a PNG, JPEG or WebP image"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "isn't a PNG, JPEG or WebP image"),
        (fake_image(8, 8), "smaller than"),
    ],
)
async def test_bytes_that_arent_a_usable_image_are_kept_with_their_cost(
    create_user: CreateUser, image_tools: FakeLLM, data: bytes, message: str
) -> None:
    alice = await create_user("alice")
    image_tools.image_reply = data
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([("text", "Sorry.")])]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    [failed] = failed_events(events)
    assert message in failed["error"] and failed["error"].endswith("It counts toward the user's monthly images.")
    [event] = await image_usage(alice.user_id)
    assert event.cost_usd == pytest.approx(GPT_IMAGE_COST)
    assert await rows(Document) == [] and stored_files() == []
    assert image_tool.is_unusable(await image_model_row())


async def test_a_crashed_image_child_is_reported_and_kept(
    create_user: CreateUser, image_tools: FakeLLM, file_child: Callable[..., Any]
) -> None:
    alice = await create_user("alice")
    file_child("crash")
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([("text", "Sorry.")])]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    [failed] = failed_events(events)
    assert failed["error"] == (
        "The image model returned an image this app couldn't process. It counts toward the user's monthly images."
    )
    assert len(await image_usage(alice.user_id)) == 1 and await rows(Document) == []
    assert not image_tool.is_unusable(await image_model_row())  # a crash isn't the model's fault


async def test_no_image_slot_before_the_deadline(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "image_concurrency", 1)
    alice, bob = await create_user("alice"), await create_user("bob")
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call("Alice's fox")]), ScriptedCall([("text", "Done.")])]
    first = asyncio.create_task(chat(alice, "Draw a fox", document_ids=[]))
    await eventually(lambda: image_tools.image_calls)

    monkeypatch.setattr(settings, "image_timeout_seconds", 0.3)
    image_tools.script = [ScriptedCall([image_call("Bob's fox")]), ScriptedCall([("text", "Busy.")])]
    response, events = await chat(bob, "Draw a fox", document_ids=[])

    [failed] = failed_events(events)
    assert failed["error"] == "Too many images are being generated right now, so this one wasn't started."
    assert len(image_tools.image_calls) == 1 and await image_usage(bob.user_id) == []
    image_tools.image_gate.set()
    _, alice_events = await first
    assert len(created_events(alice_events)) == 1


async def test_a_slot_freed_shortly_before_the_deadline_isnt_used(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slot wait ends min(60 s, IMAGE_TIMEOUT_SECONDS / 2) before the deadline: a call sent with less time left
    would be abandoned at the deadline and still be billed and counted."""
    monkeypatch.setattr(settings, "image_concurrency", 1)
    alice, bob = await create_user("alice"), await create_user("bob")
    alices_gate = image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call("Alice's fox")]), ScriptedCall([("text", "Done.")])]
    first = asyncio.create_task(chat(alice, "Draw a fox", document_ids=[]))
    await eventually(lambda: image_tools.image_calls)

    monkeypatch.setattr(settings, "image_timeout_seconds", 3)  # Bob waits for a slot until 1.5 s
    image_tools.image_gate, image_tools.image_delay = None, 10  # a call sent now would time out
    image_tools.script = [ScriptedCall([image_call("Bob's fox")]), ScriptedCall([("text", "Busy.")])]
    answer = asyncio.create_task(chat(bob, "Draw a fox", document_ids=[]))
    await eventually(lambda: len(image_tool.running_jobs()) == 2)
    await asyncio.sleep(2.25)  # after Bob's slot wait, before his deadline
    alices_gate.set()
    response, events = await answer

    [failed] = failed_events(events)
    assert failed["error"] == "Too many images are being generated right now, so this one wasn't started."
    assert len(image_tools.image_calls) == 1 and await image_usage(bob.user_id) == []
    _, alice_events = await first
    assert len(created_events(alice_events)) == 1


async def test_the_timeout_message_names_the_whole_limit(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part of the limit may go to waiting for a slot; the message names IMAGE_TIMEOUT_SECONDS, not what was left."""
    monkeypatch.setattr(settings, "image_timeout_seconds", 2)
    real_generate = llm.generate_image

    async def after_a_wait(*args: Any, **kwargs: Any) -> llm.ImageReply:
        assert kwargs["limit_seconds"] == 2 and kwargs["timeout"] <= 2
        return await real_generate(*args, **{**kwargs, "timeout": 0.2})  # as if 1.8 s went to the slot wait

    monkeypatch.setattr(llm, "generate_image", after_a_wait)
    alice = await create_user("alice")
    image_tools.image_delay = 10
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([("text", "Sorry.")])]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    [failed] = failed_events(events)
    assert failed["error"].startswith("Generating the image took longer than 2 seconds, so it was abandoned.")


@pytest.mark.parametrize("change", ["disabled", "deleted"])
async def test_the_model_turned_off_before_the_call(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    alice = await create_user("alice")
    model = await image_model_row()
    real_completion = litellm.acompletion

    async def admin_acts_first(**kwargs: Any) -> Any:
        if not image_tools.chat_calls:  # after planning, before the tool call runs
            if change == "disabled":
                await admin_client.patch(f"/api/admin/models/{model.id}", json={"enabled": False})
            else:
                await admin_client.delete(f"/api/admin/models/{model.id}")
        return await real_completion(**kwargs)

    monkeypatch.setattr(litellm, "acompletion", admin_acts_first)
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([image_call()]), ScriptedCall([("text", ".")])]

    response, events = await chat(alice, "Draw a fox", document_ids=[])

    first, second = failed_events(events)
    assert first["error"] == "Image generation isn't available right now, so the image wasn't generated."
    assert second["error"] == HALTED
    assert image_tools.image_calls == [] and await image_usage(alice.user_id) == []


async def test_the_model_deleted_after_the_reservation_is_still_settled(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    model = await image_model_row()
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([("text", "Done.")])]
    answer = asyncio.create_task(chat(alice, "Draw a fox", document_ids=[]))
    await eventually(lambda: image_tools.image_calls)

    assert (await admin_client.delete(f"/api/admin/models/{model.id}")).status_code == 204
    image_tools.image_gate.set()
    response, events = await answer

    assert len(created_events(events)) == 1
    [event] = await image_usage(alice.user_id)
    assert (event.model_id, event.model_name, event.completion_tokens) == (None, "openai/gpt-image-2", 272)


async def test_a_user_deleted_while_their_image_is_generated(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call()]), ScriptedCall([("text", "Done.")])]
    answer = asyncio.create_task(chat(alice, "Draw a fox", document_ids=[]))
    await eventually(lambda: image_tools.image_calls)

    assert (await admin_client.delete(f"/api/admin/users/{alice.user_id}")).status_code == 204
    image_tools.image_gate.set()
    response, events = await answer

    [failed] = failed_events(events)
    assert failed["error"] == "The user's account no longer exists; the image wasn't saved."
    assert await rows(Document) == [] and stored_files() == []


# ---------------- Stop (section 7.4) ----------------


async def test_stop_while_the_arguments_stream_reserves_nothing(
    create_user: CreateUser, image_tools: FakeLLM, start_turn: StartTurn
) -> None:
    alice = await create_user("alice")
    image_tools.chunk_delay = 0.02
    image_tools.script = [ScriptedCall([image_call(prompt="A fox " * 50)], fragment=5)]
    turn = await start_turn(alice, "Draw a fox", document_ids=[])
    stream = _real_sse_with_keepalive(turn)

    received = await read_until(stream, '"status": "writing"')
    await stream.aclose()
    await answers_settled()
    await jobs_settled()

    assert '"kind": "image"' in "".join(received)
    assert image_tools.image_calls == [] and await image_usage() == []
    [answer] = await rows(Message, Message.role == "assistant")
    assert answer.error == STOPPED


async def test_stop_during_the_reservation_releases_it(
    create_user: CreateUser, image_tools: FakeLLM, start_turn: StartTurn, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    gate, reached = asyncio.Event(), asyncio.Event()
    real_reserve = image_tool.reserve

    async def gated_reserve(*args: Any) -> Any:
        reached.set()
        await gate.wait()
        return await real_reserve(*args)

    monkeypatch.setattr(image_tool, "reserve", gated_reserve)
    image_tools.script = [ScriptedCall([image_call()])]
    turn = await start_turn(alice, "Draw a fox", document_ids=[])
    stream = _real_sse_with_keepalive(turn)
    await read_until(stream, '"title": "Fox logo"')
    await asyncio.wait_for(reached.wait(), 30)

    await stream.aclose()
    await answers_settled()
    gate.set()
    await eventually(lambda: not image_tool._spawned and not any(not task.done() for task in image_tool._spawned))
    await jobs_settled()
    await asyncio.sleep(0.05)

    assert image_tools.image_calls == [] and await image_usage() == []


async def test_stop_while_waiting_for_an_image_slot_releases_it(
    create_user: CreateUser, image_tools: FakeLLM, start_turn: StartTurn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "image_concurrency", 1)
    alice, bob = await create_user("alice"), await create_user("bob")
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call("Alice's fox")]), ScriptedCall([("text", "Done.")])]
    alice_turn = await start_turn(alice, "Draw a fox", document_ids=[])
    first = asyncio.create_task(consume(_real_sse_with_keepalive(alice_turn)))
    await eventually(lambda: image_tools.image_calls)

    image_tools.script.insert(0, ScriptedCall([image_call("Bob's fox")]))
    turn = await start_turn(bob, "Draw a fox", document_ids=[])
    stream = _real_sse_with_keepalive(turn)
    await read_until(stream, '"status": "generating"')
    await stream.aclose()
    while not await rows(Message, Message.conversation_id == turn.conversation_id, Message.role == "assistant"):
        await asyncio.sleep(0.01)  # Bob's stopped answer is saved (Alice's answer is still running)
    assert len(await image_usage(bob.user_id)) == 1  # reserved, waiting for the slot

    image_tools.image_gate.set()
    assert len(created_events(await first)) == 1
    await jobs_settled()

    assert len(image_tools.image_calls) == 1 and await image_usage(bob.user_id) == []


async def test_stop_during_the_image_call_keeps_the_image_and_links_it_once(
    create_user: CreateUser, image_tools: FakeLLM, start_turn: StartTurn
) -> None:
    alice = await create_user("alice")
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([("text", "Drawing it."), image_call()])]
    turn = await start_turn(alice, "Draw a fox", document_ids=[])
    stream = _real_sse_with_keepalive(turn)
    await read_until(stream, '"status": "generating"')
    await eventually(lambda: image_tools.image_calls)

    await stream.aclose()
    await answers_settled()

    [answer] = await rows(Message, Message.role == "assistant")  # saved at once, before the image arrives
    assert (answer.content, answer.error) == ("Drawing it.", STOPPED)
    assert await rows(MessageFile) == []
    image_tools.script = [ScriptedCall([("text", "Meanwhile.")])]
    next_turn = await start_turn(alice, "Another question", conversation_id=turn.conversation_id, document_ids=[])
    assert names(await consume(_real_sse_with_keepalive(next_turn)))[-1] == "done"

    image_tools.image_gate.set()
    await jobs_settled()

    [doc] = await rows(Document)
    [link] = await rows(MessageFile)
    assert (link.message_id, link.document_id, link.format) == (answer.id, doc.id, "png")
    [event] = await image_usage(alice.user_id)
    assert event.cost_usd == pytest.approx(GPT_IMAGE_COST)
    [row] = await rows(GeneratedImage)
    assert row.conversation_id == turn.conversation_id
    detail = (await alice.get(f"/api/conversations/{turn.conversation_id}")).json()
    assert [(message["role"], message["content"]) for message in detail["messages"]] == [
        ("user", "Draw a fox"),
        ("assistant", "Drawing it."),
        ("user", "Another question"),
        ("assistant", "Meanwhile."),
    ]
    [card] = detail["messages"][1]["files"]
    assert (card["document_id"], card["filename"], card["image"]["width"]) == (doc.id, "Fox logo.png", 64)


async def test_stop_during_normalization_keeps_the_image(
    create_user: CreateUser, image_tools: FakeLLM, start_turn: StartTurn, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    gate, reached = asyncio.Event(), asyncio.Event()
    real_run_child = document_tool.run_child

    async def gated_child(job: dict[str, Any], **kwargs: Any) -> Any:
        if job.get("kind") == "image":
            reached.set()
            await gate.wait()
        return await real_run_child(job, **kwargs)

    monkeypatch.setattr(document_tool, "run_child", gated_child)
    image_tools.script = [ScriptedCall([image_call()])]
    turn = await start_turn(alice, "Draw a fox", document_ids=[])
    stream = _real_sse_with_keepalive(turn)
    await read_until(stream, '"status": "generating"')
    await asyncio.wait_for(reached.wait(), 30)

    await stream.aclose()
    await answers_settled()
    gate.set()
    await jobs_settled()

    [answer] = await rows(Message, Message.role == "assistant")
    [doc] = await rows(Document)
    assert [(link.message_id, link.document_id) for link in await rows(MessageFile)] == [(answer.id, doc.id)]


async def test_a_conversation_deleted_before_the_job_finishes(
    create_user: CreateUser, image_tools: FakeLLM, start_turn: StartTurn
) -> None:
    alice = await create_user("alice")
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call()])]
    turn = await start_turn(alice, "Draw a fox", document_ids=[])
    stream = _real_sse_with_keepalive(turn)
    await read_until(stream, '"status": "generating"')
    await eventually(lambda: image_tools.image_calls)
    await stream.aclose()
    await answers_settled()

    assert (await alice.delete(f"/api/conversations/{turn.conversation_id}")).status_code == 204
    image_tools.image_gate.set()
    await jobs_settled()

    [doc] = await rows(Document)
    [row] = await rows(GeneratedImage)
    assert (row.document_id, row.conversation_id) == (doc.id, None)
    assert await rows(MessageFile) == []
    assert len(await image_usage(alice.user_id)) == 1


async def test_shutdown_waits_for_image_jobs(
    create_user: CreateUser, image_tools: FakeLLM, start_turn: StartTurn
) -> None:
    alice = await create_user("alice")
    image_tools.image_gate = asyncio.Event()
    image_tools.script = [ScriptedCall([image_call()])]
    turn = await start_turn(alice, "Draw a fox", document_ids=[])
    stream = _real_sse_with_keepalive(turn)
    await read_until(stream, '"status": "generating"')
    await eventually(lambda: image_tools.image_calls)
    await stream.aclose()

    drained = asyncio.create_task(chat_router.drain_answers(timeout=10))
    await asyncio.sleep(0.1)
    assert not drained.done()  # the image job is still running
    image_tools.image_gate.set()
    await asyncio.wait_for(drained, 10)

    assert len(await rows(Document)) == 1 and len(await rows(MessageFile)) == 1


# ---------------- Deletes ----------------


async def test_deleting_an_image_document_removes_its_sidecars_and_turns_its_card_into_deleted(
    create_user: CreateUser, image_tools: FakeLLM
) -> None:
    alice = await create_user("alice")
    events, created = await one_image(alice, image_tools)
    doc_id = created["file"]["document_id"]
    [doc] = await rows(Document, Document.id == doc_id)
    assert len(stored_files()) == 4

    assert (await alice.delete(f"/api/documents/{doc_id}")).status_code == 204

    assert stored_files() == [] and await rows(GeneratedImage) == []
    detail = (await alice.get(f"/api/conversations/{events[0][1]['conversation_id']}")).json()
    assert detail["messages"][1]["files"] == [
        {
            "document_id": None,
            "filename": "Fox logo.png",
            "format": "png",
            "size_bytes": doc.size_bytes,
            "status": None,
            "image": None,
        }
    ]
    image_tools.script = [ScriptedCall([("text", "OK.")])]
    await chat(alice, "Thanks", conversation_id=events[0][1]["conversation_id"], document_ids=[])
    assert image_tools.chat_calls[-1]["messages"][-2]["content"].endswith(
        "(App note: this answer generated these images, which have since been deleted from the user's documents: "
        '"Fox logo.png".)'
    )


async def test_deleting_the_conversation_keeps_the_image_but_it_can_no_longer_be_embedded(
    create_user: CreateUser, image_tools: FakeLLM
) -> None:
    alice = await create_user("alice")
    events, created = await one_image(alice, image_tools)
    doc_id = created["file"]["document_id"]

    assert (await alice.delete(f"/api/conversations/{events[0][1]['conversation_id']}")).status_code == 204

    [row] = await rows(GeneratedImage)
    assert (row.document_id, row.conversation_id) == (doc_id, None)
    assert (await alice.get(f"/api/documents/{doc_id}/image")).status_code == 200
    assert await image_tool.resolve_images(alice.user_id, events[0][1]["conversation_id"], [doc_id]) == []


async def test_images_older_than_the_history_window_are_listed_so_files_can_still_embed_them(
    create_user: CreateUser, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found in the live run: after HISTORY_MESSAGES more messages, the answer that generated an image (with its App
    note and reference) leaves the history, and the model said it had no reference for the image. The system prompt
    now lists the conversation's images that the history doesn't, while they exist and create_document is offered."""
    monkeypatch.setattr(settings, "history_messages", 2)
    alice = await create_user("alice")
    events, created = await one_image(alice, image_tools)
    image_id = created["file"]["document_id"]
    conversation_id = events[0][1]["conversation_id"]
    note = (
        "Earlier in this conversation, before the messages below, the assistant generated these images, which are in "
        f'the user\'s documents and can be embedded in pdf and docx files with their reference: "Fox logo.png" '
        f"(image:{image_id})."
    )

    image_tools.script = [ScriptedCall([("text", "Sure.")])]
    await chat(alice, "Thanks", conversation_id=conversation_id, document_ids=[])
    call = image_tools.chat_calls[-1]
    assert note not in system_prompt(call)  # the image's answer is still in the history, with its App note
    assert f"(image:{image_id})" in call["messages"][2]["content"]

    image_tools.script = [
        ScriptedCall([tool_call(title="Sheet", format="pdf", content=f"# Sheet\n\n![Fox](image:{image_id})\n")]),
        ScriptedCall([("text", "Done.")]),
    ]
    _, events = await chat(alice, "Put the fox logo in a PDF", conversation_id=conversation_id, document_ids=[])
    first, follow_up = image_tools.chat_calls[-2:]
    assert note in system_prompt(first) and system_prompt(follow_up) == system_prompt(first)
    assert not any(f"image:{image_id}" in message["content"] for message in first["messages"][1:])
    [pdf] = created_events(events)
    assert pdf["file"]["format"] == "pdf"
    assert "Embedded 1 image." in tool_results(follow_up)[-1]["note"]

    monkeypatch.setattr(settings, "chat_file_tools", False)  # nothing can embed it: not listed
    image_tools.script = [ScriptedCall([("text", "OK.")])]
    await chat(alice, "Thanks again", conversation_id=conversation_id, document_ids=[])
    assert "Earlier in this conversation" not in system_prompt(image_tools.chat_calls[-1])

    monkeypatch.setattr(settings, "chat_file_tools", True)
    assert (await alice.delete(f"/api/documents/{image_id}")).status_code == 204  # deleted images aren't listed
    image_tools.script = [ScriptedCall([("text", "OK.")])]
    await chat(alice, "And now?", conversation_id=conversation_id, document_ids=[])
    assert "Earlier in this conversation" not in system_prompt(image_tools.chat_calls[-1])


async def test_deleting_the_user_removes_their_limit_and_image_files(
    create_user: CreateUser, image_tools: FakeLLM, admin_client: ApiClient
) -> None:
    alice = await create_user("alice", monthly_image_limit=5)
    await one_image(alice, image_tools)
    assert len(await rows(UserImageLimit, UserImageLimit.user_id == alice.user_id)) == 1

    assert (await admin_client.delete(f"/api/admin/users/{alice.user_id}")).status_code == 204

    assert await rows(UserImageLimit) == [] and await rows(GeneratedImage) == [] and stored_files() == []


# ---------------- Admin ----------------


@pytest.mark.parametrize(
    ("litellm_model", "api_base", "problem"),
    [
        ("openai/dall-e-3", None, "This isn't a GPT image model."),
        ("gemini/imagen-4", None, "Image models must be OpenAI or Azure OpenAI GPT image models"),
        ("vertex_ai/imagen-4", None, "Image models must be OpenAI or Azure OpenAI GPT image models"),
    ],
)
async def test_unsupported_image_models_are_refused(
    admin_client: ApiClient, litellm_model: str, api_base: str | None, problem: str
) -> None:
    body = {"display_name": "Images", "litellm_model": litellm_model, "kind": "image"}
    created = await admin_client.post("/api/admin/models", json=body)
    assert created.status_code == 422 and created.json()["detail"].startswith(problem)
    model = await image_model_row()
    edited = await admin_client.patch(f"/api/admin/models/{model.id}", json={"litellm_model": litellm_model})
    assert edited.status_code == 422 and edited.json()["detail"].startswith(problem)
    assert (await image_model_row()).litellm_model == "openai/gpt-image-2"


async def test_image_models_are_managed_like_vision_models(
    admin_client: ApiClient, create_user: CreateUser, image_tools: FakeLLM
) -> None:
    created = await admin_client.post(
        "/api/admin/models",
        json={
            "display_name": "Azure images",
            "litellm_model": "azure/my-images",
            "kind": "image",
            "api_key_env": "AZURE_API_KEY",
            "is_default": True,
        },
    )
    assert created.status_code == 201, created.text
    model = created.json()
    stats = (await admin_client.get("/api/admin/stats")).json()
    assert stats["active_image_model"] == {"id": model["id"], "display_name": "Azure images"}
    listed = {item["display_name"]: item for item in (await admin_client.get("/api/admin/models")).json()}
    assert listed["GPT Image 2"]["is_default"] is False and listed["Azure images"]["kind"] == "image"

    proxy = await admin_client.patch(
        f"/api/admin/models/{model['id']}", json={"litellm_model": "openai/flux", "api_base": "http://127.0.0.1:9/v1"}
    )
    assert proxy.status_code == 200
    assert (await admin_client.patch(f"/api/admin/models/{model['id']}", json={"enabled": False})).status_code == 200
    assert (await admin_client.get("/api/admin/stats")).json()["active_image_model"] is None

    alice = await create_user("alice")
    assert all(not item["can_create_images"] for item in (await models_by_name(alice)).values())
    refused = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"allowed_model_ids": [model["id"]]})
    assert refused.status_code == 422
    assert "Azure images" not in await models_by_name(alice)
    assert (await admin_client.delete(f"/api/admin/models/{model['id']}")).status_code == 204


async def test_the_image_model_test_generates_a_small_image_and_records_it(
    admin_client: ApiClient, image_tools: FakeLLM
) -> None:
    model = await image_model_row()

    response = await admin_client.post(f"/api/admin/models/{model.id}/test")

    result = response.json()
    assert response.status_code == 200 and result["ok"] is True
    assert result["message"].startswith("OK: generated a 64×64 PNG (")
    assert result["message"].endswith(f", about ${GPT_IMAGE_COST:.4f}")
    assert result["embedding_dim"] is None and result["image_data_url"].startswith("data:image/webp;base64,")
    [call] = image_tools.image_calls
    assert (call["size"], call["quality"], call["prompt"]) == (
        "1024x1024",
        "low",
        "A small flat icon of a blue paper airplane on a plain white background.",
    )
    [event] = await image_usage(admin_client.user_id)
    assert (event.model_id, event.completion_tokens) == (model.id, 272)
    assert await rows(Document) == []

    image_tools.image_error = refusal()
    refused = (await admin_client.post(f"/api/admin/models/{model.id}/test")).json()
    assert (refused["ok"], refused["message"], refused["image_data_url"]) == (
        False,
        "The image model's safety system declined the test prompt.",
        None,
    )
    assert len(await image_usage(admin_client.user_id)) == 1  # a refusal isn't billed or recorded
    report = (await admin_client.get("/api/admin/usage")).json()
    assert report["totals"]["images"] == 1 and report["totals"]["requests"] == 0


async def test_users_monthly_image_limit_is_created_patched_and_reported(
    admin_client: ApiClient, create_user: CreateUser, image_tools: FakeLLM
) -> None:
    alice = await create_user("alice", monthly_image_limit=4)
    users = {user["username"]: user for user in (await admin_client.get("/api/admin/users")).json()}
    assert users["alice"]["monthly_image_limit"] == 4 and users["admin"]["monthly_image_limit"] == 0

    await one_image(alice, image_tools)
    users = {user["username"]: user for user in (await admin_client.get("/api/admin/users")).json()}
    assert users["alice"]["usage"]["images"] == 1
    me = (await alice.get("/api/auth/me")).json()
    assert (me["limits"]["monthly_image_limit"], me["usage"]["images"]) == (4, 1)

    patched = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"monthly_image_limit": 0})
    assert patched.status_code == 200 and patched.json()["monthly_image_limit"] == 0
    assert (await alice.get("/api/auth/me")).json()["limits"]["monthly_image_limit"] == 0
    untouched = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"rate_limit_per_min": 5})
    assert untouched.json()["monthly_image_limit"] == 0

    async with SessionLocal() as db:  # a user without a row (the backfill failed) gets the env default
        await db.execute(delete(UserImageLimit).where(UserImageLimit.user_id == alice.user_id))
        await db.commit()
    assert (await alice.get("/api/auth/me")).json()["limits"]["monthly_image_limit"] == 30
    again = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"monthly_image_limit": 2})
    assert again.json()["monthly_image_limit"] == 2
    assert [row.monthly_image_limit for row in await rows(UserImageLimit)] == [2]
    invalid = {"username": "bob", "password": USER_PASSWORD, "monthly_image_limit": -1}
    assert (await admin_client.post("/api/admin/users", json=invalid)).status_code == 422
