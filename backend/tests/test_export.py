"""POST /me/export (docs/briefs/ui-shell-settings.md, 4.6): the ZIP's layout and content, only the caller's own data and
no secrets, the size and time bounds, one export per user and two building at once, the lock and the slot freed on
every path, plugin cards with their live state, and the response that owns the temp file."""

import asyncio
import io
import json
import threading
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from conftest import CLIENT_SECRET, TEST_TOKEN_KEY, ApiClient, chat, txt_bytes, upload
from gmail_support import gmail  # noqa: F401
from pydantic import ValidationError
from sqlalchemy import select
from starlette.requests import Request

from app.config import Settings, settings
from app.db import SessionLocal
from app.models import Conversation, Message, MessageFile, MessagePluginItem, PluginConnection, User, utcnow
from app.plugins.gmail import compose, sends
from app.plugins.gmail.mime import OutgoingEmail
from app.plugins.gmail.schemas import GmailSendCard
from app.services import export

CreateUser = Callable[..., Awaitable[ApiClient]]

UTC = timezone.utc
FIXED_ENTRIES = {"README.txt", "account.json", "documents.json", "conversations/index.json"}


def unzip(response: Any) -> dict[str, bytes]:
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def as_json(entries: dict[str, bytes], name: str) -> Any:
    return json.loads(entries[name])


async def export_of(client: ApiClient) -> Any:
    return await client.post("/api/me/export", json={})


async def add_conversation(
    user_id: int | None, title: str, *, updated_at: datetime | None = None, messages: list[dict[str, Any]] | None = None
) -> int:
    """A conversation with `messages` (Message fields), inserted directly; returns its id."""
    async with SessionLocal() as session:
        conversation = Conversation(user_id=user_id, title=title)
        if updated_at is not None:
            conversation.created_at = conversation.updated_at = updated_at
        session.add(conversation)
        await session.flush()
        for fields in messages or [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]:
            session.add(Message(conversation_id=conversation.id, **fields))
        await session.commit()
        return conversation.id


async def answer_of(conversation_id: int) -> int:
    async with SessionLocal() as session:
        found = await session.scalar(
            select(Message.id).where(Message.conversation_id == conversation_id, Message.role == "assistant")
        )
        assert found is not None
        return found


async def add_card(message_id: int, plugin_id: str, card_type: str, data: dict[str, Any]) -> int:
    async with SessionLocal() as session:
        item = MessagePluginItem(message_id=message_id, plugin_id=plugin_id, card_type=card_type, data=data)
        session.add(item)
        await session.commit()
        return item.id


def conversation_files(entries: dict[str, bytes], conversation_id: int) -> tuple[str, dict[str, Any]]:
    [base] = {name.rsplit(".", 1)[0] for name in entries if name.startswith(f"conversations/{conversation_id:06d}-")}
    return entries[f"{base}.md"].decode(), json.loads(entries[f"{base}.json"])


@pytest.fixture(autouse=True)
def fresh_export_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own locks and slots."""
    monkeypatch.setattr(export, "_user_locks", export.WeakValueDictionary())
    monkeypatch.setattr(export, "_building", asyncio.Semaphore(export.MAX_BUILDING))


async def assert_all_free(user_id: int | None) -> None:
    assert user_id is not None and not export.user_lock(user_id).locked()
    for _ in range(export.MAX_BUILDING):
        await asyncio.wait_for(export._building.acquire(), 0.1)
    for _ in range(export.MAX_BUILDING):
        export._building.release()


# ---------------- Layout and content ----------------


async def test_the_archive_holds_the_callers_account_documents_and_conversations(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    _, first = await chat(alice, "First question?", document_ids=[])
    _, second = await chat(alice, "Second question?", document_ids=[])
    await chat(bob, "Bob's secret question?", document_ids=[])
    assert (await upload(alice, "notes.txt", txt_bytes("Alice's notes."))).status_code == 201
    assert (await upload(bob, "bob.txt", txt_bytes("Bob's notes."))).status_code == 201
    shared = await upload(admin_client, "handbook.txt", txt_bytes("The shared handbook."), scope="shared")
    assert shared.status_code == 201
    await alice.patch("/api/me/preferences", json={"theme": "dark"})

    response = await export_of(alice)
    entries = unzip(response)

    newest, oldest = second[0][1]["conversation_id"], first[0][1]["conversation_id"]
    assert response.headers["content-type"] == "application/zip"
    today = datetime.now(UTC).date().isoformat()
    assert response.headers["content-disposition"] == f'attachment; filename="rag-assistant-export-alice-{today}.zip"'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (response.headers["x-export-conversations"], response.headers["x-export-omitted"]) == ("2", "0")
    assert int(response.headers["content-length"]) == len(response.content)
    assert set(entries) == FIXED_ENTRIES | {
        f"conversations/{newest:06d}-second-question.md",
        f"conversations/{newest:06d}-second-question.json",
        f"conversations/{oldest:06d}-first-question.md",
        f"conversations/{oldest:06d}-first-question.json",
    }
    assert all(name.isascii() for name in entries)

    profile = as_json(entries, "account.json")
    assert (profile["format"], profile["version"], profile["username"], profile["role"]) == (
        "rag-assistant-export",
        1,
        "alice",
        "member",
    )
    assert profile["preferences"]["theme"] == "dark"
    assert profile["limits"]["monthly_token_limit"] == settings.default_monthly_token_limit
    assert profile["usage_this_month"]["chat_tokens"] > 0
    assert profile["conversations"] == {"included": 2, "omitted": 0, "truncated": None}
    assert profile["plugin_connections"] == []
    [sign_in] = profile["recent_logins"]
    assert "current" not in sign_in and sign_in["ip"] == "127.0.0.x"
    [document] = as_json(entries, "documents.json")
    assert (document["filename"], document["scope"], document["generated"], document["image"]) == (
        "notes.txt",
        "private",
        False,
        None,
    )
    index = as_json(entries, "conversations/index.json")
    assert [(entry["id"], entry["title"], entry["messages"]) for entry in index] == [
        (newest, "Second question?", 2),
        (oldest, "First question?", 2),
    ]
    markdown, data = conversation_files(entries, newest)
    assert markdown.startswith("# Second question?\n\nCreated ")
    assert "## You · " in markdown and "Second question?" in markdown and "## Assistant (" in markdown
    assert [message["role"] for message in data["messages"]] == ["user", "assistant"]
    readme = entries["README.txt"].decode()
    assert "Conversations: 2 included" in readme and "download them from the documents panel" in readme
    everything = b"".join(entries.values())
    for foreign in (b"Bob's secret question", b"bob.txt", b"handbook.txt", b"The shared handbook"):
        assert foreign not in everything


async def test_messages_sources_files_cards_and_errors(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    sources = [
        {
            "index": 1,
            "document_id": 7,
            "filename": "report.pdf",
            "page": 3,
            "chunk_index": 0,
            "score": 0.9,
            "snippet": "Revenue grew.",
        },
        {
            "kind": "email",
            "index": 2,
            "plugin_id": "gmail",
            "message_id": "m1",
            "thread_id": "t1",
            "subject": "Invoice\n42",
            "sender": "Alice <alice@example.test>",
            "date": "2026-10-01T09:00:00Z",
            "snippet": "Pay by Friday.",
            "url": "https://mail.google.com/x",
        },
    ]
    conversation_id = await add_conversation(
        alice.user_id,
        "Quarterly\nreport",
        messages=[
            {"role": "user", "content": "Summarize the report"},
            {
                "role": "assistant",
                "content": "Revenue grew [1], see [2].",
                "model_name": "Claude Haiku 5.5",
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "sources": sources,
                "error": "Stopped\nearly",
            },
        ],
    )
    answer = await answer_of(conversation_id)
    async with SessionLocal() as session:
        session.add(
            MessageFile(message_id=answer, document_id=None, filename="summary.pdf", format="pdf", size_bytes=1536)
        )
        await session.commit()
    stored = {
        "plugin_id": "gmail",
        "type": "gmail_activity",
        "status": "done",
        "action": "search",
        "query": "invoice",
        "count": 3,
        "error": None,
        "card_id": "g1",
    }
    item_id = await add_card(answer, "gmail", "gmail_activity", stored)  # no plugin registered: as stored

    markdown, data = conversation_files(unzip(await export_of(alice)), conversation_id)

    assert markdown.splitlines()[0] == "# Quarterly report"
    assert "## Assistant (Claude Haiku 5.5) · " in markdown
    assert (
        'Sources:\n1. report.pdf, page 3\n2. Email: "Invoice 42" from Alice <alice@example.test> (2026-10-01)\n'
        in markdown
    )
    assert "Files:\n- summary.pdf (pdf, 1.5 KB)\n" in markdown
    assert 'Plugins:\n- Gmail search for "invoice" · 3 found\n' in markdown
    assert markdown.endswith("*Error: Stopped early*\n")
    assert data["title"] == "Quarterly\nreport"
    message = data["messages"][1]
    assert message | {"created_at": None, "id": None} == {
        "id": None,
        "role": "assistant",
        "content": "Revenue grew [1], see [2].",
        "created_at": None,
        "model_name": "Claude Haiku 5.5",
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "error": "Stopped\nearly",
        "sources": sources,
        "files": [{"document_id": None, "filename": "summary.pdf", "format": "pdf", "size_bytes": 1536}],
        "plugin_cards": [{**stored, "card_id": f"p{item_id}"}],
    }


async def test_a_card_of_an_unregistered_plugin_exports_its_stored_data(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    conversation_id = await add_conversation(alice.user_id, "Ghosts")
    item_id = await add_card(await answer_of(conversation_id), "ghost", "ghost_card", {"status": "done", "n": 2})

    markdown, data = conversation_files(unzip(await export_of(alice)), conversation_id)

    assert data["messages"][1]["plugin_cards"] == [
        {"plugin_id": "ghost", "type": "ghost_card", "status": "done", "n": 2, "card_id": f"p{item_id}"}
    ]
    assert "Plugins:\n- ghost card (ghost_card)\n" in markdown


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Über café ☕ plans", "uber-cafe-plans"),
        ("日本語のタイトル", "conversation"),
        ("", "conversation"),
        ("A" * 30 + " and " + "B" * 30, "a" * 30 + "-and-bbbbb"),  # at most 40 characters
        ("--- ?! ---", "conversation"),
    ],
)
async def test_non_ascii_titles_give_ascii_names(create_user: CreateUser, title: str, slug: str) -> None:
    alice = await create_user("alice")
    conversation_id = await add_conversation(alice.user_id, title)

    entries = unzip(await export_of(alice))

    assert f"conversations/{conversation_id:06d}-{slug}.md" in entries
    assert as_json(entries, "conversations/index.json")[0]["title"] == title


async def test_a_conversation_whose_model_and_documents_are_gone_still_exports(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    created = await admin_client.post(
        "/api/admin/models", json={"display_name": "Temp", "litellm_model": "openai/temp-model", "kind": "chat"}
    )
    model_id = created.json()["id"]
    upload_response = await upload(alice, "gone.txt", txt_bytes("Soon deleted."))
    document_id = upload_response.json()["id"]
    conversation_id = await add_conversation(
        alice.user_id,
        "Old chat",
        messages=[
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A", "model_id": model_id, "model_name": "Temp"},
        ],
    )
    async with SessionLocal() as session:
        answer = await answer_of(conversation_id)
        session.add(MessageFile(message_id=answer, document_id=document_id, filename="gone.txt", format="md"))
        await session.commit()
    assert (await admin_client.delete(f"/api/admin/models/{model_id}")).status_code == 204
    assert (await alice.delete(f"/api/documents/{document_id}")).status_code == 204

    markdown, data = conversation_files(unzip(await export_of(alice)), conversation_id)

    assert data["messages"][1]["model_name"] == "Temp" and data["messages"][1]["files"][0]["document_id"] is None
    assert "## Assistant (Temp) · " in markdown and "- gone.txt (md, 0 bytes)" in markdown


async def test_an_account_with_nothing_exports_a_valid_zip(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    entries = unzip(await export_of(alice))

    assert set(entries) == FIXED_ENTRIES
    assert as_json(entries, "documents.json") == [] and as_json(entries, "conversations/index.json") == []
    assert as_json(entries, "account.json")["conversations"] == {"included": 0, "omitted": 0, "truncated": None}


async def test_the_admins_export_holds_only_the_admins_data(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    await chat(alice, "Alice's question?", document_ids=[])
    await upload(alice, "alice.txt", txt_bytes("Alice's private notes."))
    await upload(admin_client, "handbook.txt", txt_bytes("The shared handbook."), scope="shared")
    _, events = await chat(admin_client, "Admin's question?", document_ids=[])

    entries = unzip(await export_of(admin_client))

    assert as_json(entries, "account.json")["role"] == "admin"
    assert [entry["title"] for entry in as_json(entries, "conversations/index.json")] == ["Admin's question?"]
    assert [document["filename"] for document in as_json(entries, "documents.json")] == ["handbook.txt"]
    everything = b"".join(entries.values())
    assert b"Alice's question" not in everything and b"alice.txt" not in everything


async def test_export_needs_a_session_and_a_post(client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")

    anonymous = await client.post("/api/me/export", json={})
    get = await alice.get("/api/me/export")

    assert (anonymous.status_code, anonymous.json()) == (401, {"detail": "Not authenticated"})
    assert get.status_code == 405


@pytest.mark.parametrize(
    "values",
    [
        {"export_timeout_seconds": 91},
        {"export_timeout_seconds": 9},
        {"export_max_mb": 0},
        {"export_max_mb": 1001},
    ],
)
def test_out_of_range_settings_stop_the_settings_from_loading(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[call-arg]


def test_the_default_bounds() -> None:
    defaults = Settings()  # type: ignore[call-arg]

    assert (defaults.export_max_mb, defaults.export_timeout_seconds) == (100, 80)


# ---------------- Plugin cards with their live state, and no secrets ----------------


async def test_cards_are_hydrated_and_no_secret_is_exported(
    gmail: Any,  # noqa: F811
    create_user: CreateUser,
    admin_client: ApiClient,
) -> None:
    alice = await create_user("alice")
    connection = await gmail.connect(alice)
    conversation_id = await add_conversation(alice.user_id, "Email Bob")
    answer = await answer_of(conversation_id)
    to = [{"name": "Bob", "address": "bob@company.com", "unmentioned": False}]
    email = OutgoingEmail(
        account_email=connection.account_email,
        to=compose.stored_addresses(to),
        cc=[],
        bcc=[],
        subject="Q3 numbers",
        body="Hello Bob,\nthe numbers look good.\n",
        attachments=[],
        in_reply_to=None,
        references=None,
    )
    async with SessionLocal() as session:
        user = await session.get(User, alice.user_id)
        assert user is not None
        row = await sends.propose(
            session,
            user,
            connection,
            email,
            conversation_id=conversation_id,
            to=to,
            cc=[],
            bcc=[],
            attachments_meta=[],
            thread_id=None,
            reply_to=None,
            origin="chat",
        )
    card = GmailSendCard(card_id="g1", status="proposed", pending=sends.view(row, utcnow()))
    item_id = await add_card(answer, "gmail", "gmail_send", card.model_dump(mode="json"))
    confirmed = await alice.post(f"/api/plugins/gmail/sends/{row.id}/send", json={"content_hash": row.content_hash})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "sent"

    response = await export_of(alice)
    entries = unzip(response)

    markdown, data = conversation_files(entries, conversation_id)
    [exported] = data["messages"][1]["plugin_cards"]
    assert (exported["card_id"], exported["type"], exported["pending"]["status"]) == (
        f"p{item_id}",
        "gmail_send",
        "sent",
    )
    shown = (await alice.get(f"/api/conversations/{conversation_id}")).json()["messages"][1]["plugin_cards"]
    assert [exported] == shown  # as GET /conversations/{id} shows it
    sent_at = datetime.fromisoformat(exported["pending"]["sent_at"]).isoformat(timespec="seconds")
    assert (
        f'Plugins:\n- Email sent: to bob@company.com · "Q3 numbers" · sent {sent_at.replace("+00:00", "Z")}\n'
        in markdown
    )
    [connected] = as_json(entries, "account.json")["plugin_connections"]
    assert set(connected) == {"plugin_id", "plugin_name", "account_email", "status", "chat_enabled", "connected_at"}
    assert (connected["plugin_name"], connected["account_email"]) == ("Gmail", connection.account_email)

    async with SessionLocal() as session:
        hashes = [user.password_hash for user in await session.scalars(select(User))]
        tokens = [
            token
            for stored in await session.scalars(select(PluginConnection))
            for token in (stored.refresh_token_enc, stored.access_token_enc)
            if token
        ]
    secrets = [settings.secret_key, TEST_TOKEN_KEY, CLIENT_SECRET, *hashes, *tokens]
    assert tokens and len(hashes) == 2
    for blob in (response.content, *entries.values()):
        for secret in secrets:
            assert secret.encode() not in blob
    assert b"token_version" not in b"".join(entries.values())


# ---------------- Bounds ----------------


async def test_truncation_by_size_keeps_the_newest(create_user: CreateUser, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "export_max_mb", 1)
    alice = await create_user("alice")
    start = datetime(2026, 10, 1, tzinfo=UTC)
    ids = [
        await add_conversation(
            alice.user_id,
            f"Big {n}",
            updated_at=start + timedelta(days=n),
            messages=[{"role": "user", "content": "x" * 300_000}],
        )
        for n in range(3)
    ]

    response = await export_of(alice)
    entries = unzip(response)

    assert (response.headers["x-export-conversations"], response.headers["x-export-omitted"]) == ("1", "2")
    assert [entry["id"] for entry in as_json(entries, "conversations/index.json")] == [ids[2]]
    assert as_json(entries, "account.json")["conversations"] == {"included": 1, "omitted": 2, "truncated": "size"}
    assert (
        "2 older conversation(s) were left out because the export reached its size limit (1 MB"
        in entries["README.txt"].decode()
    )


async def test_truncation_by_time(create_user: CreateUser, monkeypatch: pytest.MonkeyPatch) -> None:
    alice = await create_user("alice")
    start = datetime(2026, 10, 1, tzinfo=UTC)
    ids = [await add_conversation(alice.user_id, f"Chat {n}", updated_at=start + timedelta(days=n)) for n in range(3)]
    readings = iter([0.0, 1.0])  # the start, then the first conversation's check; later checks are past the deadline
    monkeypatch.setattr(export, "clock", lambda: next(readings, 1000.0))

    entries = unzip(await export_of(alice))

    assert [entry["id"] for entry in as_json(entries, "conversations/index.json")] == [ids[2]]
    assert as_json(entries, "account.json")["conversations"] == {"included": 1, "omitted": 2, "truncated": "time"}
    assert "building the export reached its time limit (80 seconds)" in entries["README.txt"].decode()


# ---------------- Concurrency and clean-up ----------------


@pytest.fixture
def blocked_render(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[threading.Event, threading.Event]]:
    """Rendering a conversation waits (in its worker thread) until the second event is set."""
    started, release = threading.Event(), threading.Event()
    render = export._render

    def slow(record: Any) -> tuple[bytes, bytes]:
        started.set()
        release.wait(10)
        return render(record)

    monkeypatch.setattr(export, "_render", slow)
    yield started, release
    release.set()


async def wait_for(event: threading.Event) -> None:
    for _ in range(500):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


async def test_one_export_per_user_at_a_time(
    create_user: CreateUser, blocked_render: tuple[threading.Event, threading.Event]
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    await add_conversation(alice.user_id, "Chat")
    started, release = blocked_render

    first = asyncio.create_task(export_of(alice))
    await wait_for(started)
    second = await export_of(alice)
    others = await export_of(bob)  # another user isn't blocked (bob has nothing to render)
    release.set()

    assert (second.status_code, second.json()) == (409, {"detail": export.BUSY_USER})
    assert others.status_code == 200
    unzip(await first)
    await assert_all_free(alice.user_id)
    assert (await export_of(alice)).status_code == 200


async def test_at_most_two_build_at_once(create_user: CreateUser, monkeypatch: pytest.MonkeyPatch) -> None:
    alice = await create_user("alice")
    monkeypatch.setattr(export, "SLOT_WAIT_SECONDS", 0.05)
    for _ in range(export.MAX_BUILDING):  # two exports of other users are building
        await export._building.acquire()

    busy = await export_of(alice)
    for _ in range(export.MAX_BUILDING):
        export._building.release()

    assert (busy.status_code, busy.json()) == (503, {"detail": export.BUSY_SERVER})
    await assert_all_free(alice.user_id)
    assert (await export_of(alice)).status_code == 200


async def test_a_build_that_raises_frees_the_lock_and_the_slot(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    await add_conversation(alice.user_id, "Chat")

    def broken(record: Any) -> tuple[bytes, bytes]:
        raise RuntimeError("render failed")

    monkeypatch.setattr(export, "_render", broken)
    with pytest.raises(RuntimeError, match="render failed"):
        await export_of(alice)

    await assert_all_free(alice.user_id)
    monkeypatch.undo()
    assert (await export_of(alice)).status_code == 200


@pytest.mark.parametrize("gone_after", [0, 1])  # before the first page, or only before the response starts
async def test_a_client_that_disconnects_ends_the_build_and_frees_everything(
    gone_after: int, create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    await add_conversation(alice.user_id, "Chat")
    checks: list[int] = []

    async def is_disconnected(self: Request) -> bool:
        checks.append(1)
        return len(checks) > gone_after

    with monkeypatch.context() as patched:
        patched.setattr(Request, "is_disconnected", is_disconnected)
        response = await export_of(alice)

    assert response.status_code == export.CLIENT_GONE and response.content == b""
    assert len(checks) == gone_after + 1
    await assert_all_free(alice.user_id)
    assert (await export_of(alice)).status_code == 200


async def test_the_response_releases_the_lock_and_the_file_when_it_never_starts() -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    archive = io.BytesIO(b"PK...")

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        raise OSError("the client went away")

    response = export.ExportResponse(archive, 5, lock.release, {})
    with pytest.raises(Exception):  # noqa: B017 (Starlette turns the OSError into ClientDisconnect)
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    assert archive.closed and not lock.locked()


async def test_the_response_streams_in_chunks_and_then_releases() -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    payload = bytes(range(256)) * 600  # a little over 2 chunks
    archive = io.BytesIO(payload)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    response = export.ExportResponse(archive, len(payload), lock.release, {"X-Export-Omitted": "0"})
    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    start, *bodies = sent
    assert (b"content-length", str(len(payload)).encode()) in start["headers"]
    assert b"".join(message["body"] for message in bodies) == payload
    assert max(len(message["body"]) for message in bodies) == export.CHUNK_BYTES
    assert archive.closed and not lock.locked()
