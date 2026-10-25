"""importing.store_import (docs/briefs/plugins-gmail.md, section 4.6): supported extensions, the user's upload limits
(the admin exempt), duplicates linking the existing document, provenance ("Imported from …"), ingestion, and a
cancelled caller still committing."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest
from conftest import ADMIN_USERNAME, ApiClient, pdf_bytes, process_queue, upload
from fake_plugin import FakePlugin
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import Document, ImportedDocument, User
from app.plugins import importing, registry, tasks
from app.plugins.importing import ImportFile
from app.redis_client import INGEST_QUEUE, get_redis
from app.services import storage
from app.services.ingestion import requeue_orphans

USER = Callable[..., Awaitable[ApiClient]]
THREAD = "# Invoice 42\n\nEmail conversation imported from Gmail (alice@example.test), 1 message.\n\nPay by Friday.\n"


@pytest.fixture
def fake() -> Iterator[FakePlugin]:
    plugin = FakePlugin()
    with registry.use_plugins([plugin]):
        yield plugin


def _thread(data: str = THREAD, name: str = "Invoice 42 (Gmail 2026-10-12).md") -> ImportFile:
    return ImportFile(filename=name, data=data.encode(), kind="email", external_ref="18c0a1b2c3d4e5f6")


async def _store(user: ApiClient, plugin: FakePlugin, file: ImportFile) -> importing.ImportOutcome:
    assert user.user_id is not None
    return await importing.store_import(user.user_id, plugin, "alice@example.test", file)


async def _documents() -> int:
    async with SessionLocal() as db:
        return int(await db.scalar(select(func.count()).select_from(Document)) or 0)


def _stored_files(upload_dir: Path) -> list[str]:
    return sorted(path.name for path in upload_dir.iterdir()) if upload_dir.exists() else []


async def test_import_creates_a_private_document_with_provenance(
    fake: FakePlugin, app: object, create_user: USER, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    outcome = await _store(alice, fake, _thread())
    assert outcome.status == "imported" and outcome.reason is None and outcome.document is not None
    item = outcome.document
    assert (item.filename, item.scope, item.status, item.imported_from) == (
        "Invoice 42 (Gmail 2026-10-12).md",
        "private",
        "pending",
        "Fake Mail",
    )
    assert item.content_type == "text/markdown" and item.generated is False
    async with SessionLocal() as db:
        row = await db.get(ImportedDocument, item.id)
        assert row is not None
        assert (row.plugin_id, row.kind, row.external_ref, row.account_email) == (
            "fake",
            "email",
            "18c0a1b2c3d4e5f6",
            "alice@example.test",
        )
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == [str(item.id)]
    assert await process_queue() == [item.id]
    [listed] = (await alice.get("/api/documents")).json()
    assert (listed["status"], listed["imported_from"]) == ("ready", "Fake Mail")
    admin_listed = (await admin_client.get("/api/admin/documents")).json()
    assert [doc["imported_from"] for doc in admin_listed] == ["Fake Mail"]
    reprocessed = await alice.post(f"/api/documents/{item.id}/reprocess")
    assert reprocessed.json()["imported_from"] == "Fake Mail"


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("archive.zip", ".zip files can't be imported."),
        ("old.doc", "Legacy .doc files can't be imported. Save it as .docx first."),
        ("noextension", "Files without an extension can't be imported."),
    ],
)
async def test_unsupported_files_are_skipped(
    fake: FakePlugin, create_user: USER, name: str, reason: str
) -> None:
    alice = await create_user("alice")
    outcome = await _store(alice, fake, ImportFile(name, b"data", "attachment", "m1/2"))
    assert (outcome.status, outcome.reason, outcome.document) == ("skipped", reason, None)
    empty = await _store(alice, fake, ImportFile("empty.pdf", b"", "attachment", "m1/3"))
    assert (empty.status, empty.reason) == ("skipped", "The file is empty.")
    assert await _documents() == 0


async def test_display_name_is_sanitized(fake: FakePlugin, create_user: USER) -> None:
    alice = await create_user("alice")
    outcome = await _store(alice, fake, _thread(name="../../etc/‮evil\x00 name.md"))
    assert outcome.document is not None and outcome.document.filename == "evil name.md"


async def test_limits_apply_and_the_admin_is_exempt(
    fake: FakePlugin, isolated_state: Path, create_user: USER, admin_client: ApiClient
) -> None:
    alice = await create_user("alice", max_upload_mb=1, max_documents=2, max_storage_mb=2)
    big = ImportFile("big.txt", b"x" * (1024 * 1024 + 1), "attachment", "m1/2")
    too_big = await _store(alice, fake, big)
    assert (too_big.status, too_big.reason) == ("failed", "The file is 1.0 MB, over your limit of 1 MB per file.")
    assert (await _store(alice, fake, _thread("one"))).status == "imported"
    assert (await _store(alice, fake, _thread("two"))).status == "imported"
    full = await _store(alice, fake, _thread("three"))
    assert (full.status, full.reason) == (
        "failed",
        "You've reached your limit of 2 documents. Delete a document to import another.",
    )
    assert await _documents() == 2
    assert len(_stored_files(isolated_state / "uploads")) == 2  # no orphan file

    bob = await create_user("bob", max_upload_mb=2, max_storage_mb=2)
    assert (await _store(bob, fake, ImportFile("a.txt", b"a" * (1536 * 1024), "attachment", "m/1"))).status == (
        "imported"
    )
    over = await _store(bob, fake, ImportFile("b.txt", b"b" * (1024 * 1024), "attachment", "m/2"))
    assert (over.status, over.reason) == (
        "failed",
        "This would exceed your storage limit of 2 MB. Delete some documents to free up space.",
    )

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.username == ADMIN_USERNAME))
        assert admin is not None
        admin.max_upload_mb, admin.max_documents = 1, 1
        await db.commit()
        admin_id = admin.id
    for number in range(2):
        file = ImportFile(f"{number}.txt", big.data + bytes([number]), "attachment", "x")
        assert (await importing.store_import(admin_id, fake, "a@example.test", file)).status == "imported"


async def test_identical_bytes_link_the_existing_private_document(
    fake: FakePlugin, isolated_state: Path, create_user: USER
) -> None:
    alice = await create_user("alice")
    uploaded = await upload(alice, "invoice.pdf", pdf_bytes("Invoice 42"))
    assert uploaded.status_code == 201
    outcome = await _store(alice, fake, ImportFile("Invoice-42.pdf", pdf_bytes("Invoice 42"), "attachment", "m1/2"))
    assert outcome.status == "already_existed" and outcome.document is not None
    assert outcome.document.id == uploaded.json()["id"] and outcome.document.imported_from is None
    first = await _store(alice, fake, _thread())
    again = await _store(alice, fake, _thread())  # an unchanged thread renders to the same bytes
    assert again.status == "already_existed" and again.document is not None and first.document is not None
    assert again.document.id == first.document.id and again.document.imported_from == "Fake Mail"
    assert await _documents() == 2
    assert len(_stored_files(isolated_state / "uploads")) == 2


async def test_another_users_identical_document_isnt_linked(fake: FakePlugin, create_user: USER) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    first = await _store(alice, fake, _thread())
    second = await _store(bob, fake, _thread())
    assert first.status == second.status == "imported"
    assert first.document is not None and second.document is not None
    assert first.document.id != second.document.id and second.document.owner_id == bob.user_id


async def test_cancelling_the_caller_still_commits(
    monkeypatch: pytest.MonkeyPatch, fake: FakePlugin, isolated_state: Path, create_user: USER
) -> None:
    alice = await create_user("alice", max_documents=10)
    reached, gate = asyncio.Event(), asyncio.Event()
    totals = storage.owned_totals

    async def slow_totals(db: object, user_id: int) -> tuple[int, int]:
        reached.set()
        await gate.wait()
        return await totals(db, user_id)  # type: ignore[arg-type]

    monkeypatch.setattr(storage, "owned_totals", slow_totals)
    caller = asyncio.create_task(_store(alice, fake, _thread()))
    await reached.wait()  # inside the locked check, the file written
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    gate.set()
    await tasks.drain(5.0)
    async with SessionLocal() as db:
        documents = list(await db.scalars(select(Document)))
        assert len(documents) == 1
        assert await db.get(ImportedDocument, documents[0].id) is not None
    assert _stored_files(isolated_state / "uploads") == [documents[0].stored_name]
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == [str(documents[0].id)]


async def test_an_import_the_database_fails_to_store_leaves_no_file(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakePlugin,
    isolated_state: Path,
    create_user: USER,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alice = await create_user("alice")

    async def database_down(*args: object) -> None:
        raise OperationalError("SELECT", {}, Exception("server closed the connection"))

    monkeypatch.setattr(importing, "_check_quota", database_down)
    with caplog.at_level(logging.ERROR, logger="app.plugins.importing"):
        outcome = await _store(alice, fake, _thread())

    assert (outcome.status, outcome.document, outcome.reason) == (
        "failed",
        None,
        "It couldn't be saved because of a server error.",
    )
    assert await _documents() == 0
    assert _stored_files(isolated_state / "uploads") == []
    assert f"Storing a fake import for user {alice.user_id} failed" in caplog.text


async def test_an_import_while_redis_is_down_is_left_for_the_sweep(
    monkeypatch: pytest.MonkeyPatch, fake: FakePlugin, create_user: USER
) -> None:
    alice = await create_user("alice")

    async def redis_down(doc_id: int) -> None:
        raise RedisConnectionError("Connection refused")

    monkeypatch.setattr(importing, "enqueue_document", redis_down)
    outcome = await _store(alice, fake, _thread())

    assert outcome.status == "imported" and outcome.document is not None
    assert outcome.document.status == "pending"
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == []
    assert await requeue_orphans(0) == 1
    assert await process_queue() == [outcome.document.id]


async def test_an_import_for_a_deleted_account_stores_nothing(
    fake: FakePlugin, app: object, isolated_state: Path
) -> None:
    outcome = await importing.store_import(10_000, fake, "alice@example.test", _thread())

    assert (outcome.status, outcome.reason) == ("failed", "The account no longer exists.")
    assert await _documents() == 0 and _stored_files(isolated_state / "uploads") == []
