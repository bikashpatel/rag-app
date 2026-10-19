"""/api/conversations: listing, reading, renaming and deleting the caller's own conversations."""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from conftest import ApiClient, FakeLLM, chat, ingest, sse_events, txt_bytes
from fake_plugin import FakePlugin
from gmail_support import gmail, pending_sends  # noqa: F401
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import (
    Conversation,
    Document,
    GeneratedDocument,
    GeneratedImage,
    Message,
    MessageFile,
    MessagePluginItem,
    PluginAuditEvent,
    UsageEvent,
    User,
)
from app.plugins import registry
from app.plugins.gmail import compose, sends
from app.plugins.gmail.mime import OutgoingEmail
from app.routers import chat as chat_router
from app.routers.chat import _stream_answer, _Turn

CreateUser = Callable[..., Awaitable[ApiClient]]

ANSWER = "Hello from the fake model."
NOT_FOUND = {"detail": "Conversation not found"}
SUMMARY_FIELDS = {"id", "title", "created_at", "updated_at"}
MESSAGE_FIELDS = {
    "id",
    "role",
    "content",
    "sources",
    "model_name",
    "prompt_tokens",
    "completion_tokens",
    "error",
    "files",
    "plugin_cards",
    "created_at",
}


async def _start_conversation(client: ApiClient, message: str) -> int:
    response, events = await chat(client, message, document_ids=[])
    assert response.status_code == 200, response.text
    return events[0][1]["conversation_id"]


async def _message_count(conversation_id: int) -> int:
    query = select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    async with SessionLocal() as session:
        return (await session.execute(query)).scalar_one()


async def _listed_ids(client: ApiClient) -> list[int]:
    response = await client.get("/api/conversations")
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()]


async def test_list_is_most_recent_activity_first_and_only_the_callers(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    first = await _start_conversation(alice, "First topic?")
    second = await _start_conversation(alice, "Second topic?")
    third = await _start_conversation(alice, "Third topic?")
    bobs = await _start_conversation(bob, "Bob's topic?")
    before = await _listed_ids(alice)

    await chat(alice, "Back to the first topic?", conversation_id=first, document_ids=[])

    assert before == [third, second, first]
    response = await alice.get("/api/conversations")
    listing = response.json()
    assert [item["id"] for item in listing] == [first, third, second]
    assert all(set(item) == SUMMARY_FIELDS for item in listing)
    assert listing[0]["title"] == "First topic?"
    assert listing[0]["created_at"].endswith("Z") and listing[0]["updated_at"].endswith("Z")
    assert datetime.fromisoformat(listing[0]["updated_at"]) > datetime.fromisoformat(listing[0]["created_at"])
    assert await _listed_ids(bob) == [bobs]


async def test_list_returns_the_200_most_recent(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with SessionLocal() as session:
        # Inserted out of time order, so the listing must sort by updated_at rather than id.
        conversations = [
            Conversation(user_id=alice.user_id, title=f"Chat {n}", updated_at=start + timedelta(minutes=(n * 37) % 205))
            for n in range(205)
        ]
        session.add_all([*conversations, Conversation(user_id=bob.user_id, title="Bob's", updated_at=start)])
        await session.commit()
        expected = [c.id for c in sorted(conversations, key=lambda c: c.updated_at, reverse=True)][:200]

    assert await _listed_ids(alice) == expected


async def test_get_returns_the_messages_oldest_first_with_sources(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    document = await ingest(
        alice, "orchids.txt", txt_bytes("The orchid greenhouse is kept at 24 degrees all year round.")
    )
    _, first = await chat(alice, "How warm is the orchid greenhouse?")
    conversation_id = first[0][1]["conversation_id"]
    _, second = await chat(alice, "Thanks, anything else?", conversation_id=conversation_id, document_ids=[])

    response = await alice.get(f"/api/conversations/{conversation_id}")

    assert response.status_code == 200
    detail = response.json()
    assert set(detail) == SUMMARY_FIELDS | {"messages"}
    assert (detail["id"], detail["title"]) == (conversation_id, "How warm is the orchid greenhouse?")
    messages = detail["messages"]
    assert all(set(message) == MESSAGE_FIELDS for message in messages)
    assert [message["id"] for message in messages] == [
        first[0][1]["user_message_id"],
        first[-1][1]["assistant_message_id"],
        second[0][1]["user_message_id"],
        second[-1][1]["assistant_message_id"],
    ]
    question, answer, follow_up, second_answer = messages
    assert question == {
        **question,
        "role": "user",
        "content": "How warm is the orchid greenhouse?",
        "sources": [],
        "model_name": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "error": None,
        "files": [],
    }
    sources = next(data["sources"] for name, data in first if name == "sources")
    assert [source["document_id"] for source in sources] == [document["id"]]
    assert answer == {
        **answer,
        "role": "assistant",
        "content": ANSWER,
        "sources": sources,
        "model_name": "Claude Opus 5.5",
        "prompt_tokens": 42,
        "completion_tokens": 5,
        "error": None,
        "files": [],
    }
    assert (follow_up["role"], follow_up["content"]) == ("user", "Thanks, anything else?")
    assert (second_answer["role"], second_answer["sources"]) == ("assistant", [])
    assert all(message["created_at"].endswith("Z") for message in messages)


async def test_get_shows_the_error_of_an_interrupted_answer(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    alice = await create_user("alice")
    fake_llm.fail_after = 1
    conversation_id = await _start_conversation(alice, "Tell me a story?")

    detail = (await alice.get(f"/api/conversations/{conversation_id}")).json()

    answer = detail["messages"][-1]
    assert (answer["content"], answer["error"]) == ("Hello", "Model call failed: The provider stream broke")


async def test_rename_normalizes_and_stores_the_title(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    conversation_id = await _start_conversation(alice, "Original question?")
    before = (await alice.get(f"/api/conversations/{conversation_id}")).json()

    response = await alice.patch(
        f"/api/conversations/{conversation_id}", json={"title": "  Quarterly   planning\n\tnotes  "}
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": conversation_id,
        "title": "Quarterly planning notes",
        "created_at": before["created_at"],
        "updated_at": before["updated_at"],
    }
    assert (await alice.get("/api/conversations")).json()[0]["title"] == "Quarterly planning notes"
    _, events = await chat(alice, "Next question?", conversation_id=conversation_id, document_ids=[])
    assert events[0][1]["title"] == "Quarterly planning notes"


@pytest.mark.parametrize(
    ("title", "stored"),
    [("x" * 200, "x" * 200), ("a" + " " * 300 + "b", "a b"), ("Budget\x00 review", "Budget review")],
    ids=["200-chars", "collapses-before-length-check", "drops-nul"],
)
async def test_rename_accepts_titles_up_to_200_characters(create_user: CreateUser, title: str, stored: str) -> None:
    alice = await create_user("alice")
    conversation_id = await _start_conversation(alice, "Original question?")

    response = await alice.patch(f"/api/conversations/{conversation_id}", json={"title": title})

    assert response.status_code == 200
    assert response.json()["title"] == stored
    assert (await alice.get(f"/api/conversations/{conversation_id}")).json()["title"] == stored


@pytest.mark.parametrize(
    "body",
    [{"title": ""}, {"title": " \n\t "}, {"title": "x" * 201}, {}, {"title": None}, {"title": 42}, {"title": ["a"]}],
    ids=["empty", "blank", "201-chars", "missing", "null", "number", "list"],
)
async def test_rename_rejects_invalid_titles(create_user: CreateUser, body: dict[str, Any]) -> None:
    alice = await create_user("alice")
    conversation_id = await _start_conversation(alice, "Original question?")

    response = await alice.patch(f"/api/conversations/{conversation_id}", json=body)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert (await alice.get(f"/api/conversations/{conversation_id}")).json()["title"] == "Original question?"


async def test_delete_removes_the_conversation_and_its_messages(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    doomed = await _start_conversation(alice, "Doomed question?")
    await chat(alice, "Follow-up?", conversation_id=doomed, document_ids=[])
    kept = await _start_conversation(alice, "Kept question?")
    async with SessionLocal() as session:
        usage_before = await session.scalar(select(func.count()).select_from(UsageEvent))

    response = await alice.delete(f"/api/conversations/{doomed}")

    assert response.status_code == 204
    assert response.content == b""
    assert (await alice.get(f"/api/conversations/{doomed}")).status_code == 404
    assert await _listed_ids(alice) == [kept]
    assert await _message_count(doomed) == 0
    assert await _message_count(kept) == 2
    async with SessionLocal() as session:
        assert await session.get(Conversation, doomed) is None
        assert await session.scalar(select(func.count()).select_from(UsageEvent)) == usage_before
    again = await alice.delete(f"/api/conversations/{doomed}")
    assert (again.status_code, again.json()) == (404, NOT_FOUND)


async def test_other_users_conversations_are_not_found(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    conversation_id = await _start_conversation(alice, "Alice's private question?")
    path = f"/api/conversations/{conversation_id}"

    responses = [await bob.get(path), await bob.patch(path, json={"title": "Hijacked"}), await bob.delete(path)]

    assert [(response.status_code, response.json()) for response in responses] == [(404, NOT_FOUND)] * 3
    detail = (await alice.get(path)).json()
    assert detail["title"] == "Alice's private question?"
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert await _listed_ids(bob) == []


async def test_missing_conversations_look_like_other_users(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    path = "/api/conversations/999999"

    responses = [await alice.get(path), await alice.patch(path, json={"title": "Nope"}), await alice.delete(path)]

    assert [(response.status_code, response.json()) for response in responses] == [(404, NOT_FOUND)] * 3


@pytest.mark.parametrize("conversation_id", ["0", "-1", "2147483648", "abc"])
async def test_invalid_conversation_ids_are_rejected(create_user: CreateUser, conversation_id: str) -> None:
    alice = await create_user("alice")
    path = f"/api/conversations/{conversation_id}"

    responses = [await alice.get(path), await alice.patch(path, json={"title": "New"}), await alice.delete(path)]

    assert [response.status_code for response in responses] == [422, 422, 422]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/conversations"),
        ("GET", "/api/conversations/{id}"),
        ("PATCH", "/api/conversations/{id}"),
        ("DELETE", "/api/conversations/{id}"),
        ("DELETE", "/api/conversations"),
    ],
)
async def test_conversations_require_login(create_user: CreateUser, client: ApiClient, method: str, path: str) -> None:
    alice = await create_user("alice")
    conversation_id = await _start_conversation(alice, "Alice's question?")

    response = await client.request(
        method, path.format(id=conversation_id), json={"title": "New"} if method == "PATCH" else None
    )

    assert (response.status_code, response.json()) == (401, {"detail": "Not authenticated"})
    detail = (await alice.get(f"/api/conversations/{conversation_id}")).json()
    assert (detail["title"], len(detail["messages"])) == ("Alice's question?", 2)


# ---------------- Delete all ----------------


async def _add_conversations(user_id: int | None, count: int) -> list[int]:
    """`count` conversations with a question and an answer each, inserted directly; their ids in order."""
    async with SessionLocal() as session:
        conversations = [Conversation(user_id=user_id, title=f"Chat {n}") for n in range(count)]
        session.add_all(conversations)
        await session.flush()
        for conversation in conversations:
            session.add_all(
                [
                    Message(conversation_id=conversation.id, role="user", content="Q"),
                    Message(conversation_id=conversation.id, role="assistant", content="A"),
                ]
            )
        await session.commit()
        return [conversation.id for conversation in conversations]


async def _count(model: Any, *where: Any) -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def test_delete_all_removes_every_conversation_of_the_caller(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    mine = [await _start_conversation(alice, f"Question {n}?") for n in range(3)]
    theirs = await _start_conversation(bob, "Bob's question?")
    usage_before = await _count(UsageEvent)

    response = await alice.delete("/api/conversations")

    assert (response.status_code, response.json()) == (200, {"deleted": 3})
    assert await _listed_ids(alice) == []
    assert all([await _message_count(conversation_id) == 0 for conversation_id in mine])
    assert await _listed_ids(bob) == [theirs] and await _message_count(theirs) == 2
    assert await _count(UsageEvent) == usage_before
    again = await alice.delete("/api/conversations")
    assert (again.status_code, again.json()) == (200, {"deleted": 0})


async def test_delete_all_keeps_documents_generated_files_and_images(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    [conversation_id] = await _add_conversations(alice.user_id, 1)
    async with SessionLocal() as session:
        answer = await session.scalar(select(Message.id).where(Message.role == "assistant"))
        assert answer is not None
        file, image = (
            Document(owner_id=alice.user_id, scope="private", filename=name, stored_name=name, sha256="0" * 64)
            for name in ("report.pdf", "cat.png")
        )
        session.add_all([file, image])
        await session.flush()
        session.add_all(
            [
                GeneratedDocument(document_id=file.id),
                GeneratedImage(
                    document_id=image.id, conversation_id=conversation_id, model_name="openai/gpt-image-2",
                    prompt="A cat", alt_text="A cat", format="png", quality="low", width=8, height=8,
                ),
                MessageFile(message_id=answer, document_id=file.id, filename="report.pdf", format="pdf"),
                MessagePluginItem(message_id=answer, plugin_id="gmail", card_type="gmail_activity", data={}),
            ]
        )
        await session.commit()
        file_id, image_id = file.id, image.id

    response = await alice.delete("/api/conversations")

    assert response.json() == {"deleted": 1}
    assert await _count(Document) == 2 and await _count(GeneratedDocument) == 1
    async with SessionLocal() as session:
        kept = await session.get(GeneratedImage, image_id)
        assert kept is not None and kept.conversation_id is None
        assert await session.get(Document, file_id) is not None
    assert await _count(MessageFile) == 0 and await _count(MessagePluginItem) == 0


class _CleaningPlugin(FakePlugin):
    """Records its clean-up in the database (an audit row per conversation) and fails on one conversation after it."""

    def __init__(self, failing: int) -> None:
        super().__init__()
        self.failing = failing

    async def on_conversation_deleted(self, db: AsyncSession, user: User, conversation_id: int) -> None:
        db.add(
            PluginAuditEvent(
                user_id=user.id, plugin_id=self.id, action="send_cancelled", outcome="ok", origin="system",
                items=conversation_id,
            )
        )
        await db.flush()
        if conversation_id == self.failing:
            raise RuntimeError("broken hook")


async def test_a_failing_hook_never_undoes_another_conversations_deletion(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    ids = await _add_conversations(alice.user_id, 60)
    plugin = _CleaningPlugin(failing=ids[23])

    with registry.use_plugins([plugin]):
        response = await alice.delete("/api/conversations")

    assert (response.status_code, response.json()) == (200, {"deleted": 60})
    assert await _count(Conversation, Conversation.user_id == alice.user_id) == 0
    assert await _count(Message) == 0
    async with SessionLocal() as session:
        cleaned = set(await session.scalars(select(PluginAuditEvent.items)))
    assert cleaned == set(ids) - {ids[23]}  # the failing hook's own change went; the other 59 stayed


async def test_delete_all_cancels_and_scrubs_pending_emails(gmail: Any, create_user: CreateUser) -> None:  # noqa: F811
    alice = await create_user("alice")
    connection = await gmail.connect(alice)
    conversation_id = await _start_conversation(alice, "Email Bob?")
    to = [{"name": "Bob", "address": "bob@company.com", "unmentioned": False}]
    email = OutgoingEmail(
        account_email=connection.account_email, to=compose.stored_addresses(to), cc=[], bcc=[], subject="Q3",
        body="Hello Bob", attachments=[], in_reply_to=None, references=None,
    )
    async with SessionLocal() as session:
        user = await session.get(User, alice.user_id)
        assert user is not None
        await sends.propose(
            session, user, connection, email, conversation_id=conversation_id, to=to, cc=[], bcc=[],
            attachments_meta=[], thread_id=None, reply_to=None, origin="chat",
        )

    assert (await alice.delete("/api/conversations")).json() == {"deleted": 1}

    [row] = await pending_sends(alice.user_id)
    assert (row.status, row.subject, row.body, row.to_addrs, row.conversation_id) == ("cancelled", "", "", [], None)


async def _no_events() -> AsyncIterator[str]:
    for event in ():
        yield event


async def test_an_answer_streaming_into_a_conversation_deleted_by_delete_all_saves_nothing(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    turns: list[_Turn] = []

    def capture(turn: _Turn) -> AsyncIterator[str]:
        turns.append(turn)
        return _no_events()

    monkeypatch.setattr(chat_router, "_stream_answer", capture)
    response, _ = await chat(alice, "Tell me a story?", document_ids=[])
    assert response.status_code == 200
    stream = _stream_answer(turns[0])
    received = [await anext(stream) for _ in range(3)]

    assert (await alice.delete("/api/conversations")).json() == {"deleted": 1}
    received += [event async for event in stream]

    events = sse_events(httpx.Response(200, text="".join(received)))
    assert events[-1] == ("error", {"message": "The answer could not be saved.", "assistant_message_id": None})
    assert await _message_count(turns[0].conversation_id) == 0
    assert await _listed_ids(alice) == []
