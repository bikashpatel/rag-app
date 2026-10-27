"""python -m app.ops.reconcile: after a restore, every document agrees with its file and its vectors again."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from conftest import ApiClient, ingest, process_queue, txt_bytes
from qdrant_client import models as qm
from sqlalchemy import Connection, update

from app import db
from app.db import SchemaMismatchError
from app.models import Document
from app.ops import reconcile
from app.services import ingestion, storage, vectorstore
from app.services.vectorstore import ChunkPoint, get_qdrant

LONG_TEXT = " ".join(f"Sentence {number} talks about the lighthouse keeper and the harbour." for number in range(400))


@pytest.fixture
async def user(create_user: Callable[..., Awaitable[ApiClient]]) -> ApiClient:
    return await create_user("owner")


async def row(doc_id: int) -> Document:
    async with db.SessionLocal() as session:
        found = await session.get(Document, doc_id)
    assert found is not None
    return found


async def points_of(doc_id: int, model_id: int) -> int:
    name = vectorstore.collection_name(model_id)
    if not await get_qdrant().collection_exists(name):
        return 0
    selector = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
    return (await get_qdrant().count(name, count_filter=selector, exact=True)).count


async def documents(client: ApiClient, *texts: str) -> list[dict[str, Any]]:
    return [await ingest(client, f"doc{index}.txt", txt_bytes(text)) for index, text in enumerate(texts)]


async def test_a_consistent_stack_is_left_as_it_is(user: ApiClient) -> None:
    first, second = await documents(user, LONG_TEXT, "A short note about the harbour.")
    assert first["num_chunks"] > 1

    report = await reconcile.reconcile()

    assert report.lines() == ["Checked 2 documents: each has its file and its vectors."]
    for item in (first, second):
        assert (await row(item["id"])).status == "ready"


async def test_documents_restored_without_some_of_their_parts_are_fixed(user: ApiClient) -> None:
    ready, lost, truncated, partial, other = await documents(
        user, LONG_TEXT, "The lost file.", "The truncated file.", LONG_TEXT + " Partial.", "Untouched."
    )
    model_id = (await row(ready["id"])).embedding_model_id
    assert model_id is not None
    collection = vectorstore.collection_name(model_id)

    # The archive lacked one file and caught another while it was being written.
    storage.stored_path((await row(lost["id"])).stored_name).unlink()
    path = storage.stored_path((await row(truncated["id"])).stored_name)
    path.write_bytes(path.read_bytes()[:5])
    # The snapshot was taken before a document's last chunks were written.
    selector = qm.Filter(
        must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=partial["id"])),
            qm.FieldCondition(key="chunk_index", match=qm.MatchValue(value=0)),
        ]
    )
    await get_qdrant().delete(collection, points_selector=qm.FilterSelector(filter=selector), wait=True)
    # The vectors of a document deleted during the backup, and stray ones of a failed document.
    stray = [ChunkPoint(9999, index, [0.1] * 64, 1, "private", "gone.txt", None, "gone") for index in range(3)]
    await vectorstore.upsert_chunks(collection, stray)
    async with db.SessionLocal() as session:
        await session.execute(update(Document).where(Document.id == other["id"]).values(status="failed", error="x"))
        await session.commit()

    report = await reconcile.reconcile()

    assert report.failed == sorted([lost["id"], truncated["id"]])
    assert report.reindexed == [partial["id"]]
    assert report.removed_from == sorted([lost["id"], truncated["id"], other["id"], 9999])
    lines = report.lines()
    assert lines[0] == "Checked 5 documents:"
    assert lines[1] == f"- set to re-index, their vectors didn't match: {partial['id']}"
    assert lines[2] == f"- marked failed, their file wasn't in the backup: {lost['id']}, {truncated['id']}"
    assert lines[3].startswith(f"- removed {report.removed_points} vectors of documents that are deleted or failed: ")

    for item in (lost, truncated):
        restored = await row(item["id"])
        assert (restored.status, restored.error, restored.num_chunks) == ("failed", reconcile.MISSING_FILE, 0)
        assert await points_of(item["id"], model_id) == 0
    assert await points_of(9999, model_id) == 0 and await points_of(other["id"], model_id) == 0
    assert (await row(ready["id"])).status == "ready"
    assert await points_of(ready["id"], model_id) == ready["num_chunks"]

    # The worker re-indexes the document set back to pending (it queues pending documents when it starts).
    assert (await row(partial["id"])).status == "pending"
    assert await ingestion.recover_pending() == 1
    assert await process_queue() == [partial["id"]]
    assert (await row(partial["id"])).status == "ready"
    assert await points_of(partial["id"], model_id) == partial["num_chunks"]

    again = await reconcile.reconcile()
    assert (again.failed, again.reindexed, again.removed_points) == ([], [], 0)


async def test_a_missing_collection_means_re_indexing_but_a_stale_document_is_left_alone(user: ApiClient) -> None:
    indexed, stale = await documents(user, "The first note.", "The second note.")
    model_id = (await row(indexed["id"])).embedding_model_id
    assert model_id is not None
    async with db.SessionLocal() as session:  # the admin dropped the vectors of its model: stale, on purpose
        await session.execute(update(Document).where(Document.id == stale["id"]).values(embedding_model_id=None))
        await session.commit()
    await get_qdrant().delete_collection(vectorstore.collection_name(model_id))

    report = await reconcile.reconcile()

    assert report.reindexed == [indexed["id"]]
    assert (await row(stale["id"])).status == "ready"


async def test_pending_documents_keep_their_vectors_and_wait_for_the_worker(user: ApiClient) -> None:
    (item,) = await documents(user, "A note.")
    model_id = (await row(item["id"])).embedding_model_id
    assert model_id is not None
    async with db.SessionLocal() as session:
        await session.execute(update(Document).where(Document.id == item["id"]).values(status="pending"))
        await session.commit()

    report = await reconcile.reconcile()

    assert (report.failed, report.reindexed, report.removed_points) == ([], [], 0)
    assert await points_of(item["id"], model_id) == item["num_chunks"]


async def test_the_command_prints_the_report_and_names_no_error_detail(
    user: ApiClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    await documents(user, "A note.")
    assert await reconcile.main() == 0
    assert capsys.readouterr().out == "Checked 1 document: each has its file and its vectors.\n"

    async def broken() -> reconcile.Report:
        raise ConnectionRefusedError("qdrant:6333 with api-key secret-123")

    monkeypatch.setattr(reconcile, "reconcile", broken)
    assert await reconcile.main() == 1
    captured = capsys.readouterr()
    assert captured.err == "Reconciling the documents failed: connection refused\n"
    assert "secret" not in captured.err


async def test_files_no_document_has_are_removed(user: ApiClient) -> None:
    """The file of a document deleted while the backup ran is in the archive, its row isn't in the dump."""
    (item,) = await documents(user, "A note.")
    stored = (await row(item["id"])).stored_name
    source = storage.stored_path(storage.source_name(stored))
    source.write_text("# The document's own Markdown source, kept")
    orphans = [storage.stored_path(storage.new_stored_name(extension)) for extension in (".pdf", ".png.thumb.webp")]
    for orphan in orphans:
        orphan.write_bytes(b"a file whose document was deleted during the backup")
    staging = storage.upload_dir() / ".restore-staging"
    staging.mkdir()
    (staging / "kept").write_bytes(b"a restore's own folder")

    report = await reconcile.reconcile()

    assert report.removed_files == 2
    assert report.lines() == ["Checked 1 document:", "- removed 2 files that no document has"]
    assert not any(orphan.exists() for orphan in orphans)
    assert storage.stored_path(stored).exists() and source.exists() and (staging / "kept").exists()
    assert (await row(item["id"])).status == "ready"


def revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


async def test_the_command_migrates_the_database_before_it_reconciles(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A restore with --migrate brings an older schema: the documents are read only once it is this release's."""
    head = ScriptDirectory(str(db.MIGRATIONS_DIR)).get_current_head()
    async with db.schema_transaction(db.engine) as connection:  # an older release's database (revision 0002)
        await connection.run_sync(lambda sync: command.upgrade(db._alembic_config(sync), "0002"))
    seen: list[str | None] = []
    checked = reconcile.reconcile

    async def after_the_migration() -> reconcile.Report:
        async with db.engine.connect() as connection:
            seen.append(await connection.run_sync(revision))
        return await checked()

    monkeypatch.setattr(reconcile, "reconcile", after_the_migration)
    with caplog.at_level(logging.INFO, logger="app.db"):
        assert await reconcile.main() == 0

    assert seen == [head]
    assert f"Database schema migrated from 0002 to revision {head}" in caplog.messages


async def test_a_schema_this_release_cannot_migrate_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def mismatch() -> None:
        raise SchemaMismatchError("The database differs from the baseline in 2 way(s); nothing was changed")

    async def unexpected() -> reconcile.Report:
        raise AssertionError("reconciled a schema that wasn't migrated")

    monkeypatch.setattr(reconcile, "init_db", mismatch)
    monkeypatch.setattr(reconcile, "reconcile", unexpected)

    assert await reconcile.main() == 1
    assert capsys.readouterr().err == (
        "The restored database can't be migrated to this release's schema: The database differs from the baseline in "
        "2 way(s); nothing was changed\n"
    )
