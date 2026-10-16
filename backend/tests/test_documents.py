"""Documents API: upload validation and quotas, listing, config, delete/reprocess permissions, filenames."""

import asyncio
import hashlib
import io
import re
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import ADMIN_USERNAME, ApiClient, ingest, pdf_bytes, process, txt_bytes, upload
from fastapi import HTTPException, UploadFile
from qdrant_client import models as qm
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import Document, LLMModel, User
from app.redis_client import INGEST_QUEUE, get_redis
from app.routers import documents
from app.services import vectorstore
from app.services.ingestion import NO_EMBEDDING_MODEL
from app.services.parsing import SUPPORTED_EXTENSIONS

CreateUser = Callable[..., Awaitable[ApiClient]]

MIB = 1024 * 1024
DOCUMENT_ITEM_KEYS = {
    "id",
    "filename",
    "scope",
    "status",
    "error",
    "size_bytes",
    "content_type",
    "num_chunks",
    "num_pages",
    "owner_id",
    "owner_username",
    "can_delete",
    "is_stale",
    "vision_pages",
    "generated",
    "image",
    "created_at",
    "updated_at",
}
UNSUPPORTED_DETAIL = f"This file type isn't supported. Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
NOT_FOUND = {"detail": "Document not found"}


def stored_files() -> list[Path]:
    directory = Path(settings.upload_dir)
    return sorted(directory.iterdir()) if directory.exists() else []


async def queued() -> list[str]:
    return await get_redis().lrange(INGEST_QUEUE, 0, -1)


async def document_rows() -> list[Document]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(Document).order_by(Document.id)))


async def vector_count(doc_id: int) -> int:
    """Points of the document across every collection."""
    client = vectorstore.get_qdrant()
    only_doc = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
    total = 0
    for collection in (await client.get_collections()).collections:
        total += (await client.count(collection.name, count_filter=only_doc, exact=True)).count
    return total


async def disable_embedding_defaults() -> None:
    async with SessionLocal() as session:
        await session.execute(update(LLMModel).where(LLMModel.kind == "embedding").values(is_default=False))
        await session.commit()


async def raw_upload(client: ApiClient, filename: str, data: bytes) -> httpx.Response:
    """POST /api/documents with a hand-built multipart body, so the filename arrives byte for byte.

    httpx percent-encodes control characters in filenames, which would hide them from the server.
    """
    boundary = "rag-test-boundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="' + filename.encode() + b'"\r\n',
            b"Content-Type: text/plain\r\n\r\n",
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return await client.post(
        "/api/documents", content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )


# ---------------- Upload ----------------


async def test_upload_creates_a_pending_document_and_queues_it(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    data = txt_bytes("Field notes about the migrating cranes near the river delta.")

    response = await upload(alice, "field-notes.txt", data)

    assert response.status_code == 201, response.text
    item = response.json()
    assert set(item) == DOCUMENT_ITEM_KEYS
    assert item == {
        **item,
        "filename": "field-notes.txt",
        "scope": "private",
        "status": "pending",
        "error": None,
        "size_bytes": len(data),
        "content_type": "text/plain",
        "num_chunks": 0,
        "num_pages": None,
        "owner_id": alice.user_id,
        "owner_username": "alice",
        "can_delete": True,
        "is_stale": False,
        "vision_pages": 0,
        "generated": False,
        "image": None,
    }
    assert item["created_at"].endswith("Z") and item["updated_at"].endswith("Z")
    assert await queued() == [str(item["id"])]

    [row] = await document_rows()
    assert (row.id, row.owner_id, row.sha256) == (item["id"], alice.user_id, hashlib.sha256(data).hexdigest())
    assert re.fullmatch(r"[0-9a-f]{32}\.txt", row.stored_name)
    [stored] = stored_files()
    assert stored.name == row.stored_name
    assert stored.read_bytes() == data


@pytest.mark.parametrize("extension", SUPPORTED_EXTENSIONS)
async def test_every_supported_extension_is_accepted_in_any_case(admin_client: ApiClient, extension: str) -> None:
    lower = await upload(admin_client, f"lower{extension}", b"lower case " + extension.encode())
    upper = await upload(admin_client, f"UPPER{extension.upper()}", b"upper case " + extension.encode())

    assert lower.status_code == 201, lower.text
    assert upper.status_code == 201, upper.text
    assert upper.json()["filename"] == f"UPPER{extension.upper()}"
    assert sorted(path.suffix for path in stored_files()) == [extension, extension]


@pytest.mark.parametrize("filename", ["virus.exe", "archive.zip", "README", "notes.txt.exe", "letter.rtf", ".hidden"])
async def test_unsupported_types_are_rejected(create_user: CreateUser, filename: str) -> None:
    alice = await create_user("alice")

    response = await upload(alice, filename, b"some bytes that are never stored")

    assert response.status_code == 415
    assert response.json() == {"detail": UNSUPPORTED_DETAIL}
    assert stored_files() == [] and await document_rows() == [] and await queued() == []


@pytest.mark.parametrize(
    ("filename", "legacy", "modern"),
    [("old.doc", ".doc", ".docx"), ("slides.ppt", ".ppt", ".pptx"), ("SHEET.XLS", ".xls", ".xlsx")],
)
async def test_legacy_office_types_get_a_hint(create_user: CreateUser, filename: str, legacy: str, modern: str) -> None:
    alice = await create_user("alice")

    response = await upload(alice, filename, b"\xd0\xcf\x11\xe0 legacy compound file")

    assert response.status_code == 415
    assert response.json() == {
        "detail": f"Legacy {legacy} files aren't supported. Save it as {modern} and upload again."
    }
    assert stored_files() == [] and await document_rows() == []


async def test_files_over_the_upload_limit_are_rejected_without_leaving_a_file(create_user: CreateUser) -> None:
    alice = await create_user("alice", max_upload_mb=1)

    too_large = await upload(alice, "big.txt", b"a" * (MIB + 1))
    at_limit = await upload(alice, "exact.txt", b"b" * MIB)

    assert too_large.status_code == 413
    assert too_large.json() == {"detail": "The file is too large. The limit is 1 MB per file."}
    assert at_limit.status_code == 201, at_limit.text
    assert [path.stat().st_size for path in stored_files()] == [MIB]
    assert [row.filename for row in await document_rows()] == ["exact.txt"]
    assert await queued() == [str(at_limit.json()["id"])]


async def test_upload_limit_is_enforced_while_streaming_an_unsized_upload(create_user: CreateUser) -> None:
    alice = await create_user("alice", max_upload_mb=1)

    class CountingReader(io.BytesIO):
        bytes_read = 0

        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            self.bytes_read += len(chunk)
            return chunk

    source = CountingReader(b"s" * (8 * MIB))
    async with SessionLocal() as session:
        user = await session.get(User, alice.user_id)
        assert user is not None
        with pytest.raises(HTTPException) as caught:
            await documents.upload_document(
                file=UploadFile(file=source, filename="stream.txt"), scope="private", user=user, db=session
            )

    assert caught.value.status_code == 413
    assert caught.value.detail == "The file is too large. The limit is 1 MB per file."
    assert source.bytes_read <= 2 * MIB, "reading stops as soon as the limit is passed"
    assert stored_files() == [] and await document_rows() == []


async def test_admin_uploads_are_capped_by_the_hard_limit(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_upload_mb_hard", 1)

    response = await upload(admin_client, "big.txt", b"a" * (MIB + 1), scope="shared")

    assert response.status_code == 413
    assert response.json() == {"detail": "The file is too large. The limit is 1 MB per file."}
    assert stored_files() == []


async def test_empty_file_is_rejected(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    response = await upload(alice, "empty.txt", b"")

    assert response.status_code == 422
    assert response.json() == {"detail": "The file is empty."}
    assert stored_files() == [] and await document_rows() == [] and await queued() == []


async def test_duplicate_upload_is_rejected_per_owner_and_scope(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    data = txt_bytes("Identical content uploaded more than once by different people.")

    first = await upload(alice, "original.txt", data)
    again = await upload(alice, "renamed-copy.txt", data)
    by_bob = await upload(bob, "original.txt", data)
    admin_private = await upload(admin_client, "original.txt", data)
    admin_shared = await upload(admin_client, "original.txt", data, scope="shared")
    admin_shared_again = await upload(admin_client, "other.txt", data, scope="shared")

    assert [r.status_code for r in (first, by_bob, admin_private, admin_shared)] == [201, 201, 201, 201]
    for duplicate in (again, admin_shared_again):
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": "This file has already been uploaded."}
    assert len(stored_files()) == 4
    assert len(await document_rows()) == 4
    assert len(await queued()) == 4


async def test_document_count_quota(create_user: CreateUser) -> None:
    alice = await create_user("alice", max_documents=2)

    first = await upload(alice, "one.txt", b"first document")
    second = await upload(alice, "two.txt", b"second document")
    third = await upload(alice, "three.txt", b"third document")

    assert (first.status_code, second.status_code) == (201, 201)
    assert third.status_code == 409
    assert third.json() == {"detail": "You've reached your limit of 2 documents. Delete a document to upload another."}
    assert len(stored_files()) == 2

    assert (await alice.delete(f"/api/documents/{first.json()['id']}")).status_code == 204
    retry = await upload(alice, "three.txt", b"third document")
    assert retry.status_code == 201, retry.text


async def test_storage_quota(create_user: CreateUser) -> None:
    alice = await create_user("alice", max_storage_mb=1)

    first = await upload(alice, "a.txt", b"a" * (600 * 1024))
    over = await upload(alice, "b.txt", b"b" * (500 * 1024))
    fits = await upload(alice, "c.txt", b"c" * (400 * 1024))

    assert first.status_code == 201
    assert over.status_code == 409
    assert over.json() == {
        "detail": "This upload would exceed your storage limit of 1 MB. Delete some documents to free up space."
    }
    assert fits.status_code == 201, fits.text
    assert sorted(path.stat().st_size for path in stored_files()) == [400 * 1024, 600 * 1024]


def save_together(monkeypatch: pytest.MonkeyPatch, uploads: int) -> None:
    """Make the next `uploads` uploads finish writing their files at the same moment, then race on."""
    barrier = threading.Barrier(uploads, timeout=10)
    save = documents._save_upload

    def synchronized(*args: Any) -> Any:
        saved = save(*args)
        barrier.wait()
        return saved

    monkeypatch.setattr(documents, "_save_upload", synchronized)


async def test_parallel_uploads_cannot_exceed_the_document_count(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice", max_documents=3)
    assert (await upload(alice, "zero.txt", b"already there")).status_code == 201
    save_together(monkeypatch, 6)

    responses = await asyncio.gather(*(upload(alice, f"doc{n}.txt", f"document {n}".encode()) for n in range(6)))

    assert sorted(response.status_code for response in responses) == [201] * 2 + [409] * 4
    assert len(await document_rows()) == 3
    assert len(stored_files()) == 3


async def test_parallel_uploads_cannot_exceed_the_storage_quota(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice", max_storage_mb=1)
    save_together(monkeypatch, 4)

    responses = await asyncio.gather(*(upload(alice, f"big{n}.txt", bytes([65 + n]) * 600 * 1024) for n in range(4)))

    assert sorted(response.status_code for response in responses) == [201] + [409] * 3
    assert len(stored_files()) == 1


async def test_parallel_uploads_of_the_same_file_store_it_once(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    save_together(monkeypatch, 5)

    responses = await asyncio.gather(*(upload(alice, f"copy{n}.txt", b"the same bytes") for n in range(5)))

    assert sorted(response.status_code for response in responses) == [201] + [409] * 4
    assert len(await document_rows()) == 1
    assert len(stored_files()) == 1


async def test_zero_quotas_mean_unlimited(create_user: CreateUser) -> None:
    alice = await create_user("alice", max_documents=0, max_storage_mb=0, max_upload_mb=1)

    responses = [await upload(alice, f"doc{number}.txt", bytes([65 + number]) * 900 * 1024) for number in range(4)]

    assert [response.status_code for response in responses] == [201, 201, 201, 201]


async def test_admin_bypasses_document_quotas(admin_client: ApiClient) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(User).where(User.username == ADMIN_USERNAME).values(max_documents=1, max_storage_mb=1)
        )
        await session.commit()

    responses = [
        await upload(admin_client, "big.txt", b"x" * (2 * MIB)),
        await upload(admin_client, "second.txt", b"second"),
        await upload(admin_client, "third.txt", b"third", scope="shared"),
    ]

    assert [response.status_code for response in responses] == [201, 201, 201]


async def test_shared_upload_requires_the_admin(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")

    denied = await upload(alice, "kb.txt", b"knowledge base article", scope="shared")
    shared = await upload(admin_client, "kb.txt", b"knowledge base article", scope="shared")

    assert denied.status_code == 403
    assert denied.json() == {"detail": "Only the admin can upload to the shared knowledge base."}
    assert shared.status_code == 201
    assert shared.json() == {**shared.json(), "scope": "shared", "owner_username": ADMIN_USERNAME, "can_delete": True}
    assert len(stored_files()) == 1
    assert [row.scope for row in await document_rows()] == ["shared"]


async def test_invalid_form_fields_are_rejected(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    bad_scope = await upload(alice, "notes.txt", b"some notes", scope="public")
    no_file = await alice.post("/api/documents", data={"scope": "private"})

    assert bad_scope.status_code == 422
    assert no_file.status_code == 422
    assert stored_files() == [] and await document_rows() == []


@pytest.mark.parametrize(
    ("declared", "filename", "expected"),
    [
        ("text/markdown", "notes.md", "text/markdown"),
        ("Text/Plain; charset=utf-8", "notes.txt", "text/plain"),
        ("application/octet-stream", "report.pdf", "application/pdf"),
        ("not a media type", "data.json", "application/json"),
        ("application/octet-stream", "notes.markdown", "text/markdown"),
    ],
)
async def test_content_type_is_kept_when_plausible_else_guessed(
    create_user: CreateUser, declared: str, filename: str, expected: str
) -> None:
    alice = await create_user("alice")

    response = await upload(alice, filename, b"content", content_type=declared)

    assert response.status_code == 201, response.text
    assert response.json()["content_type"] == expected


async def test_document_routes_require_login(client: ApiClient) -> None:
    responses = [
        await client.get("/api/documents"),
        await client.get("/api/documents/config"),
        await upload(client, "notes.txt", b"anonymous upload"),
        await client.delete("/api/documents/1"),
        await client.post("/api/documents/1/reprocess"),
    ]

    assert [response.status_code for response in responses] == [401] * 5
    assert stored_files() == []


# ---------------- Filenames ----------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("../../etc/passwd.txt", "passwd.txt"),
        ("/var/data/./report.txt", "report.txt"),
        ("C:\\Users\\bob\\Desktop\\notes.txt", "notes.txt"),
        ("reports\\2026\\q1.txt", "q1.txt"),
        ("re\x00po\x07rt\x7f.txt", "report.txt"),
        ("invoice\u202etxt.exe.txt", "invoicetxt.exe.txt"),
        ("zero\u200bwidth\ufeff.txt", "zerowidth.txt"),
        ("odd\ufffe name\uffff.txt", "odd name.txt"),
        ("quarterly\t\treport   2026.txt", "quarterly report 2026.txt"),
        ("   padded   name.txt", "padded name.txt"),
        ("Cafe\u0301 menu.txt", "Caf\u00e9 menu.txt"),
        ("n" * 400 + ".txt", "n" * 251 + ".txt"),
        ("\u00e9" * 300 + ".md", "\u00e9" * 252 + ".md"),
        ("\x00\x01.txt", "document.txt"),
        ("\u202e.txt", "document.txt"),
        ("folder/\u200b\u200e.md", "document.md"),
        ("Report.TXT", "Report.TXT"),
    ],
)
async def test_display_filename_is_sanitised(
    create_user: CreateUser, isolated_state: Path, filename: str, expected: str
) -> None:
    alice = await create_user("alice")

    response = await raw_upload(alice, filename, b"Body of a document with an unusual name.")

    assert response.status_code == 201, response.text
    assert response.json()["filename"] == expected
    [row] = await document_rows()
    assert row.filename == expected
    [stored] = stored_files()
    assert re.fullmatch(r"[0-9a-f]{32}\.(txt|md)", stored.name)
    assert stored.suffix == Path(expected).suffix.lower()
    assert [path.name for path in isolated_state.rglob("*") if path.is_file() and path.suffix in (".txt", ".md")] == [
        stored.name
    ]


# ---------------- Listing and config ----------------


async def test_listing_shows_own_private_and_all_shared_documents_newest_first(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    alice_first = (await upload(alice, "alice-1.txt", b"alice one")).json()["id"]
    alice_second = (await upload(alice, "alice-2.txt", b"alice two")).json()["id"]
    bob_doc = (await upload(bob, "bob.txt", b"bob one")).json()["id"]
    shared = (await upload(admin_client, "handbook.txt", b"shared handbook", scope="shared")).json()["id"]
    admin_private = (await upload(admin_client, "admin.txt", b"admin private")).json()["id"]

    alice_view = (await alice.get("/api/documents")).json()
    bob_view = (await bob.get("/api/documents")).json()
    admin_view = (await admin_client.get("/api/documents")).json()

    assert [item["id"] for item in alice_view] == [shared, alice_second, alice_first]
    assert [item["id"] for item in bob_view] == [shared, bob_doc]
    assert [item["id"] for item in admin_view] == [admin_private, shared]
    assert all(set(item) == DOCUMENT_ITEM_KEYS for item in alice_view + bob_view + admin_view)
    assert not any(item["generated"] for item in alice_view + bob_view + admin_view)
    shared_for_alice = alice_view[0]
    assert shared_for_alice == {
        **shared_for_alice,
        "scope": "shared",
        "owner_username": ADMIN_USERNAME,
        "can_delete": False,
    }
    assert [item["can_delete"] for item in alice_view[1:]] == [True, True]
    assert admin_view[1]["can_delete"] is True


async def test_listing_is_empty_for_a_new_user(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    response = await alice.get("/api/documents")

    assert response.status_code == 200
    assert response.json() == []


async def test_config_reports_user_limits_and_owned_totals(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice", max_upload_mb=5, max_documents=7, max_storage_mb=50)
    bob = await create_user("bob")
    expected = {
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_mb": 5,
        "max_documents": 7,
        "max_storage_mb": 50,
        "document_count": 0,
        "storage_bytes": 0,
        "can_upload_shared": False,
    }

    empty = (await alice.get("/api/documents/config")).json()
    await upload(alice, "one.txt", b"x" * 100)
    await upload(alice, "two.txt", b"y" * 250)
    await upload(bob, "bob.txt", b"z" * 999)
    await upload(admin_client, "shared.txt", b"w" * 777, scope="shared")
    filled = (await alice.get("/api/documents/config")).json()

    assert empty == expected
    assert filled == {**expected, "document_count": 2, "storage_bytes": 350}


async def test_config_caps_the_upload_size_at_the_hard_limit(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice", max_upload_mb=50)
    monkeypatch.setattr(settings, "max_upload_mb_hard", 10)

    config = (await alice.get("/api/documents/config")).json()

    assert config["max_upload_mb"] == 10


async def test_config_for_the_admin_is_unlimited(admin_client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")
    await upload(alice, "alice.txt", b"a" * 500)
    await upload(admin_client, "shared.txt", b"s" * 123, scope="shared")
    await upload(admin_client, "private.txt", b"p" * 7)

    config = (await admin_client.get("/api/documents/config")).json()

    assert config == {
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_mb": settings.max_upload_mb_hard,
        "max_documents": 0,
        "max_storage_mb": 0,
        "document_count": 2,
        "storage_bytes": 130,
        "can_upload_shared": True,
    }


# ---------------- Delete and reprocess ----------------


async def test_owner_deletes_a_private_document_completely(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    item = await ingest(alice, "notes.txt", txt_bytes("Notes about the harbour lighthouse and its keeper."))
    assert item["status"] == "ready", item["error"]
    assert await vector_count(item["id"]) == item["num_chunks"] > 0

    response = await alice.delete(f"/api/documents/{item['id']}")
    again = await alice.delete(f"/api/documents/{item['id']}")

    assert response.status_code == 204
    assert again.status_code == 404
    assert (await alice.get("/api/documents")).json() == []
    assert await document_rows() == []
    assert stored_files() == []
    assert await vector_count(item["id"]) == 0


async def test_delete_keeps_the_document_while_its_vectors_cannot_be_removed(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    item = await ingest(alice, "notes.txt", txt_bytes("Notes about the harbour lighthouse and its keeper."))

    async def qdrant_down(name: str, doc_id: int) -> None:
        raise ConnectionError("Qdrant is unreachable")

    with monkeypatch.context() as patch:
        patch.setattr(vectorstore, "delete_document_points", qdrant_down)
        failed = await alice.delete(f"/api/documents/{item['id']}")
    still_listed = [doc["id"] for doc in (await alice.get("/api/documents")).json()]
    files_after_failure = len(stored_files())
    vectors_after_failure = await vector_count(item["id"])
    retried = await alice.delete(f"/api/documents/{item['id']}")

    assert failed.status_code == 503
    assert failed.json() == {"detail": "The vector database is unavailable. Try again shortly."}
    assert still_listed == [item["id"]]
    assert (files_after_failure, vectors_after_failure) == (1, item["num_chunks"])
    assert retried.status_code == 204
    assert await document_rows() == [] and stored_files() == []
    assert await vector_count(item["id"]) == 0


async def test_other_users_cannot_touch_private_documents(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    item = await ingest(alice, "diary.txt", txt_bytes("Private diary entry about the spring garden."))
    doc_id = item["id"]

    responses = [
        await bob.delete(f"/api/documents/{doc_id}"),
        await bob.post(f"/api/documents/{doc_id}/reprocess"),
        await admin_client.delete(f"/api/documents/{doc_id}"),
        await admin_client.post(f"/api/documents/{doc_id}/reprocess"),
    ]

    assert [response.status_code for response in responses] == [404] * 4
    assert all(response.json() == NOT_FOUND for response in responses)
    [row] = await document_rows()
    assert row.status == "ready"
    assert len(stored_files()) == 1
    assert await vector_count(doc_id) == item["num_chunks"]
    assert await queued() == []


async def test_only_the_admin_manages_shared_documents(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    item = await ingest(admin_client, "policy.txt", txt_bytes("Company travel policy for all employees."), "shared")
    doc_id = item["id"]

    alice_delete = await alice.delete(f"/api/documents/{doc_id}")
    alice_reprocess = await alice.post(f"/api/documents/{doc_id}/reprocess")
    admin_reprocess = await admin_client.post(f"/api/documents/{doc_id}/reprocess")
    admin_delete = await admin_client.delete(f"/api/documents/{doc_id}")

    assert (alice_delete.status_code, alice_reprocess.status_code) == (404, 404)
    assert alice_delete.json() == alice_reprocess.json() == NOT_FOUND
    assert admin_reprocess.status_code == 200
    assert admin_delete.status_code == 204
    assert await document_rows() == [] and stored_files() == []
    assert await vector_count(doc_id) == 0


@pytest.mark.parametrize(
    ("doc_id", "status_code"), [("999999", 404), ("0", 422), ("-1", 422), ("abc", 422), ("2147483648", 422)]
)
async def test_unknown_or_invalid_document_ids(create_user: CreateUser, doc_id: str, status_code: int) -> None:
    alice = await create_user("alice")

    deleted = await alice.delete(f"/api/documents/{doc_id}")
    reprocessed = await alice.post(f"/api/documents/{doc_id}/reprocess")

    assert (deleted.status_code, reprocessed.status_code) == (status_code, status_code)


async def test_reprocess_resets_the_document_and_queues_it(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    item = await ingest(alice, "scan.pdf", pdf_bytes(""))
    assert item["status"] == "failed"

    response = await alice.post(f"/api/documents/{item['id']}/reprocess")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DOCUMENT_ITEM_KEYS
    assert body == {**item, "status": "pending", "error": None, "updated_at": body["updated_at"]}
    assert body["generated"] is False
    assert await queued() == [str(item["id"])]
    [row] = await document_rows()
    assert (row.status, row.error) == ("pending", None)


async def test_reprocessed_document_becomes_ready_again(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    item = await ingest(alice, "guide.txt", txt_bytes("A guide to pruning apple trees in late winter."))

    response = await alice.post(f"/api/documents/{item['id']}/reprocess")
    document = await process(item["id"])

    assert response.json()["status"] == "pending"
    assert document is not None and document.status == "ready"
    assert document.num_chunks == item["num_chunks"]
    assert await vector_count(item["id"]) == item["num_chunks"]


async def test_reprocessing_a_queued_document_does_not_queue_it_again(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    item = (await upload(alice, "guide.txt", txt_bytes("A guide to pruning apple trees in late winter."))).json()

    responses = [await alice.post(f"/api/documents/{item['id']}/reprocess") for _ in range(5)]

    assert [response.status_code for response in responses] == [200] * 5
    assert {response.json()["status"] for response in responses} == {"pending"}
    assert await queued() == [str(item["id"])]


async def test_reprocess_without_an_active_embedding_model_is_refused(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    item = await ingest(alice, "guide.txt", txt_bytes("A guide to pruning apple trees in late winter."))
    await disable_embedding_defaults()

    response = await alice.post(f"/api/documents/{item['id']}/reprocess")

    assert response.status_code == 400
    assert response.json() == {"detail": NO_EMBEDDING_MODEL}
    [row] = await document_rows()
    assert row.status == "ready"
    assert await queued() == []


# ---------------- Download ----------------


async def test_the_owner_downloads_the_exact_bytes_with_safe_headers(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    data = pdf_bytes("Quarterly figures for the board.")
    created = await raw_upload(alice, 'Q3 "final"\nreport ✓.pdf', data)
    assert created.status_code == 201, created.text
    assert created.json()["filename"] == 'Q3 "final" report ✓.pdf'

    response = await alice.get(f"/api/documents/{created.json()['id']}/download")

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Q3 _final_ report _.pdf\"; filename*=UTF-8''Q3%20%22final%22%20report%20%E2%9C%93.pdf"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-security-policy"] == "sandbox"


@pytest.mark.parametrize(
    ("filename", "declared", "expected"),
    [
        ("page.pdf", "text/html", "application/pdf"),
        ("notes.txt", "text/plain", "application/octet-stream"),
        ("index.html", "text/html", "application/octet-stream"),
        ("sheet.xlsx", None, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
async def test_downloads_are_typed_by_extension_not_by_the_upload(
    create_user: CreateUser, filename: str, declared: str | None, expected: str
) -> None:
    from conftest import sample_file

    alice = await create_user("alice")
    data = sample_file(Path(filename).suffix, "Some text for the file.")
    created = await upload(alice, filename, data, content_type=declared)
    assert created.status_code == 201, created.text

    response = await alice.get(f"/api/documents/{created.json()['id']}/download")

    assert response.status_code == 200 and response.content == data
    assert response.headers["content-type"].split(";")[0] == expected


async def test_long_names_get_a_shortened_ascii_fallback(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    created = await upload(alice, "é" * 200 + ".txt", txt_bytes("accented"))

    response = await alice.get(f"/api/documents/{created.json()['id']}/download")

    fallback = re.search(r'filename="([^"]*)"', response.headers["content-disposition"])
    assert fallback is not None
    assert len(fallback.group(1)) == 150 and fallback.group(1) == "e" * 146 + ".txt"


async def test_downloads_follow_the_listing_rules(
    create_user: CreateUser, admin_client: ApiClient, client: ApiClient
) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    private = (await upload(alice, "private.txt", txt_bytes("Alice only."))).json()["id"]
    shared = (await upload(admin_client, "policy.txt", txt_bytes("Everyone."), "shared")).json()["id"]

    assert (await bob.get(f"/api/documents/{private}/download")).json() == NOT_FOUND
    assert (await bob.get(f"/api/documents/{private}/download")).status_code == 404
    assert (await admin_client.get(f"/api/documents/{private}/download")).status_code == 404
    assert (await bob.get(f"/api/documents/{shared}/download")).content == txt_bytes("Everyone.")
    assert (await bob.get("/api/documents/999999/download")).json() == NOT_FOUND
    assert (await client.get(f"/api/documents/{shared}/download")).status_code == 401


async def test_a_document_whose_file_is_gone_reports_it(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    created = (await upload(alice, "notes.txt", txt_bytes("Soon gone."))).json()
    [row] = await document_rows()
    (Path(settings.upload_dir) / row.stored_name).unlink()

    response = await alice.get(f"/api/documents/{created['id']}/download")

    assert (response.status_code, response.json()) == (404, {"detail": "The file is missing from storage."})


async def test_documents_download_in_every_status(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    pending = (await upload(alice, "pending.txt", txt_bytes("Not processed yet."))).json()
    failed = await ingest(alice, "scan.pdf", pdf_bytes(""))
    assert (pending["status"], failed["status"]) == ("pending", "failed")

    for doc in (pending, failed):
        assert (await alice.get(f"/api/documents/{doc['id']}/download")).status_code == 200
