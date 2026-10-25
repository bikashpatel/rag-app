"""process_document end to end: every format to Qdrant, failures, races with delete/reprocess, model switches."""

import asyncio
import io
import logging
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import litellm
import pytest
from conftest import (
    ApiClient,
    FakeLLM,
    fake_embedding,
    ingest,
    pdf_bytes,
    process,
    sample_file,
    txt_bytes,
    upload,
)
from pypdf import PdfWriter
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.models import Record
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import SessionLocal
from app.models import Document, LLMModel, UsageEvent
from app.redis_client import INGEST_QUEUE, get_redis, ingest_attempts_key
from app.services import ingestion, llm, storage, vectorstore
from app.services.ingestion import NO_EMBEDDING_MODEL, process_document
from app.services.model_registry import get_active_embedding_model
from app.services.parsing import SUPPORTED_EXTENSIONS, DocumentParseError

CreateUser = Callable[..., Awaitable[ApiClient]]

PARAGRAPH = (
    "The lighthouse keeper records the tides every evening and notes the colour of the sky. "
    "Ships approaching the harbour signal twice before they enter the narrow channel. "
)


@pytest.fixture
async def alice(create_user: CreateUser) -> ApiClient:
    return await create_user("alice")


async def active_model() -> LLMModel:
    async with SessionLocal() as session:
        model = await get_active_embedding_model(session)
    assert model is not None
    return model


async def model_named(litellm_model: str) -> LLMModel:
    async with SessionLocal() as session:
        model = await session.scalar(select(LLMModel).where(LLMModel.litellm_model == litellm_model))
    assert model is not None
    return model


async def points(model_id: int, doc_id: int) -> list[Record]:
    """The document's points in the model's collection, in chunk order."""
    client = vectorstore.get_qdrant()
    name = vectorstore.collection_name(model_id)
    if not await client.collection_exists(name):
        return []
    records, _ = await client.scroll(
        name,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]),
        limit=10_000,
        with_payload=True,
        with_vectors=True,
    )
    return sorted(records, key=lambda record: record.payload["chunk_index"] if record.payload else -1)


async def all_point_count(doc_id: int) -> int:
    client = vectorstore.get_qdrant()
    only_doc = qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])
    total = 0
    for collection in (await client.get_collections()).collections:
        total += (await client.count(collection.name, count_filter=only_doc, exact=True)).count
    return total


async def embedding_events() -> list[UsageEvent]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(UsageEvent).where(UsageEvent.kind == "embedding")))


async def uploaded_id(client: ApiClient, filename: str, data: bytes) -> int:
    response = await upload(client, filename, data)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def wrap_embedding(
    monkeypatch: pytest.MonkeyPatch, fake_llm: FakeLLM, before: Callable[[int], Awaitable[None]]
) -> None:
    """Run `before(call_number)` (1-based) ahead of every embedding call."""
    calls = 0

    async def aembedding(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        await before(calls)
        return await fake_llm.aembedding(**kwargs)

    monkeypatch.setattr(litellm, "aembedding", aembedding)


def encrypted_pdf(text: str) -> bytes:
    writer = PdfWriter(clone_from=io.BytesIO(pdf_bytes(text)))
    writer.encrypt(user_password="open-sesame", owner_password="owner-secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ---------------- Successful indexing ----------------


@pytest.mark.parametrize("extension", SUPPORTED_EXTENSIONS)
async def test_every_supported_type_is_indexed_with_the_right_payload(
    alice: ApiClient, fake_llm: FakeLLM, extension: str
) -> None:
    sentence = f"The {extension[1:]} handbook explains how the lighthouse keeper logs the evening tides."
    model = await active_model()

    item = await ingest(alice, f"handbook{extension}", sample_file(extension, sentence))

    assert item["status"] == "ready", item["error"]
    assert item["error"] is None
    assert item["num_chunks"] >= 1
    assert item["num_pages"] == (1 if extension in (".pdf", ".pptx") else None)
    assert item["is_stale"] is False
    stored = await points(model.id, item["id"])
    assert len(stored) == item["num_chunks"]
    texts: list[str] = []
    for index, record in enumerate(stored):
        payload = dict(record.payload or {})
        texts.append(payload.pop("text"))
        assert record.id == vectorstore.point_id(item["id"], index)
        assert payload == {
            "doc_id": item["id"],
            "owner_id": alice.user_id,
            "scope": "private",
            "filename": f"handbook{extension}",
            "chunk_index": index,
            "page": 1 if extension in (".pdf", ".pptx") else None,
        }
        assert record.vector == pytest.approx(fake_embedding(texts[-1]), abs=1e-6)
    text = "\n".join(texts)
    assert sentence in text
    assert "SCRIPT_MARKER" not in text
    if extension == ".docx":
        assert "Column A | Column B\ncell one | cell two" in text
    assert fake_llm.embedding_calls[-1]["model"] == model.litellm_model
    async with SessionLocal() as session:
        document = await session.get(Document, item["id"])
    assert document is not None and document.embedding_model_id == model.id


async def test_shared_documents_are_indexed_as_shared(admin_client: ApiClient, create_user: CreateUser) -> None:
    bob = await create_user("bob")
    item = await ingest(
        admin_client, "policy.md", sample_file(".md", "Shared travel policy for every employee."), "shared"
    )

    stored = await points((await active_model()).id, item["id"])

    assert item["status"] == "ready", item["error"]
    assert {(record.payload or {})["scope"] for record in stored} == {"shared"}
    assert {(record.payload or {})["owner_id"] for record in stored} == {admin_client.user_id}
    assert [doc["id"] for doc in (await bob.get("/api/documents")).json()] == [item["id"]]


async def test_pdf_chunks_carry_their_page_numbers(alice: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chunk_size", 300)
    monkeypatch.setattr(settings, "chunk_overlap", 50)
    pages = [PARAGRAPH * 3, "", PARAGRAPH.replace("lighthouse", "windmill") * 3]

    item = await ingest(alice, "logbook.pdf", pdf_bytes(*pages))

    stored = await points((await active_model()).id, item["id"])
    assert item["status"] == "ready", item["error"]
    assert item["num_pages"] == 3
    assert [(record.payload or {})["chunk_index"] for record in stored] == list(range(item["num_chunks"]))
    page_numbers = [(record.payload or {})["page"] for record in stored]
    assert page_numbers == sorted(page_numbers) and set(page_numbers) == {1, 3}
    for record in stored:
        payload = record.payload or {}
        assert len(payload["text"]) <= 300
        assert ("windmill" in payload["text"]) == (payload["page"] == 3)


async def test_embedding_usage_is_recorded_once_for_the_owner(alice: ApiClient, fake_llm: FakeLLM) -> None:
    model = await active_model()

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH * 2))

    stored = await points(model.id, item["id"])
    tokens = sum(len((record.payload or {})["text"].split()) for record in stored)
    [event] = await embedding_events()
    assert (event.user_id, event.model_id, event.model_name) == (alice.user_id, model.id, model.litellm_model)
    assert (event.prompt_tokens, event.completion_tokens, event.total_tokens) == (tokens, 0, tokens)
    assert event.cost_usd == pytest.approx(llm.compute_cost(model, tokens, 0))
    me = (await alice.get("/api/auth/me")).json()
    assert me["usage"]["embedding_tokens"] == tokens
    assert me["usage"]["chat_tokens"] == 0


async def test_embedding_runs_in_batches_with_one_usage_event(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chunk_size", 200)
    monkeypatch.setattr(settings, "chunk_overlap", 0)
    monkeypatch.setattr(settings, "embed_batch_size", 2)

    item = await ingest(alice, "long.txt", txt_bytes(PARAGRAPH * 6))

    assert item["status"] == "ready", item["error"]
    assert item["num_chunks"] >= 5
    assert [len(call["input"]) for call in fake_llm.embedding_calls] == [
        min(2, item["num_chunks"] - start) for start in range(0, item["num_chunks"], 2)
    ]
    embedded = [text for call in fake_llm.embedding_calls for text in call["input"]]
    stored = await points((await active_model()).id, item["id"])
    assert embedded == [(record.payload or {})["text"] for record in stored]
    [event] = await embedding_events()
    assert event.prompt_tokens == sum(len(text.split()) for text in embedded)


async def test_unreported_embedding_usage_is_estimated(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.embedding_usage = False

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    [event] = await embedding_events()
    texts = [text for call in fake_llm.embedding_calls for text in call["input"]]
    assert item["status"] == "ready", item["error"]
    assert event.prompt_tokens == sum(max(1, len(text) // 4) for text in texts)


async def test_reprocessing_replaces_old_vectors(alice: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    model = await active_model()
    monkeypatch.setattr(settings, "chunk_size", 200)
    monkeypatch.setattr(settings, "chunk_overlap", 0)
    item = await ingest(alice, "long.txt", txt_bytes(PARAGRAPH * 6))
    assert item["num_chunks"] >= 5
    monkeypatch.setattr(settings, "chunk_size", 1800)

    assert (await alice.post(f"/api/documents/{item['id']}/reprocess")).status_code == 200
    document = await process(item["id"])

    assert document is not None and (document.status, document.num_chunks) == ("ready", 1)
    assert [(record.payload or {})["chunk_index"] for record in await points(model.id, item["id"])] == [0]


async def test_only_pending_or_processing_documents_are_processed(alice: ApiClient, fake_llm: FakeLLM) -> None:
    ready = await ingest(alice, "ready.txt", txt_bytes(PARAGRAPH))
    failed = await ingest(alice, "scan.pdf", pdf_bytes(""))
    calls = len(fake_llm.embedding_calls)

    await process_document(ready["id"])
    await process_document(failed["id"])
    await process_document(424242)

    assert len(fake_llm.embedding_calls) == calls
    listing = {item["id"]: item for item in (await alice.get("/api/documents")).json()}
    assert listing[ready["id"]] == ready
    assert listing[failed["id"]] == failed


async def test_duplicate_queue_entries_are_harmless(alice: ApiClient, fake_llm: FakeLLM) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    await get_redis().rpush(INGEST_QUEUE, str(doc_id))

    await asyncio.gather(process_document(doc_id), process_document(doc_id))
    await process_document(doc_id)

    assert len(fake_llm.embedding_calls) == 1
    assert len(await embedding_events()) == 1
    document = await process(doc_id)
    assert document is not None and document.status == "ready"
    assert len(await points((await active_model()).id, doc_id)) == document.num_chunks


# ---------------- Failures ----------------


@pytest.mark.parametrize(
    ("filename", "data", "error"),
    [
        ("scan.pdf", pdf_bytes(""), "No extractable text: this PDF has neither a text layer nor page images to read."),
        ("locked.pdf", encrypted_pdf("Top secret figures."), "Password-protected PDFs aren't supported."),
        ("blank.txt", b"  \n\n\t ", "The document contains no text."),
        ("empty.html", b"<html><script>var x = 1;</script></html>", "The document contains no text."),
        ("broken.docx", b"not a zip archive", "The file is corrupted or not a valid .docx file."),
        ("broken.pdf", b"%PDF-1.4 truncated", "The file is corrupted or not a valid .pdf file."),
    ],
    ids=["pdf-without-text", "encrypted-pdf", "blank-text", "empty-html", "corrupt-docx", "corrupt-pdf"],
)
async def test_unreadable_documents_fail_with_a_friendly_message(
    alice: ApiClient, fake_llm: FakeLLM, filename: str, data: bytes, error: str
) -> None:
    item = await ingest(alice, filename, data)

    assert item == {**item, "status": "failed", "error": error, "num_chunks": 0, "is_stale": False}
    assert fake_llm.embedding_calls == []
    assert await embedding_events() == []
    assert await all_point_count(item["id"]) == 0


async def test_zip_bomb_fails(alice: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_uncompressed_mb", 1)
    buffer = io.BytesIO(sample_file(".docx", "An innocent looking memo."))
    with zipfile.ZipFile(buffer, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/media/padding.bin", b"\0" * (3 * 1024 * 1024))

    item = await ingest(alice, "memo.docx", buffer.getvalue())

    assert item["status"] == "failed"
    assert item["error"] == "The file expands to more than 1 MB when uncompressed, which isn't allowed."


async def test_too_much_text_fails(alice: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_extracted_chars", 50)

    item = await ingest(alice, "essay.md", sample_file(".md", PARAGRAPH))

    assert item["status"] == "failed"
    assert item["error"] == "The document has too much text (over 50 characters)."


async def test_missing_file_fails(alice: ApiClient) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    for path in Path(settings.upload_dir).iterdir():
        path.unlink()

    document = await process(doc_id)

    assert document is not None
    assert (document.status, document.error) == ("failed", "The uploaded file is missing from storage.")


async def test_no_active_embedding_model_fails_the_document(alice: ApiClient, fake_llm: FakeLLM) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    async with SessionLocal() as session:
        await session.execute(update(LLMModel).where(LLMModel.kind == "embedding").values(is_default=False))
        await session.commit()

    document = await process(doc_id)

    assert document is not None and (document.status, document.error) == ("failed", NO_EMBEDDING_MODEL)
    assert fake_llm.embedding_calls == []


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            litellm.AuthenticationError(message="bad key", llm_provider="openai", model="text-embedding-3-small"),
            "The model provider rejected the API key. Check the provider key in .env.",
        ),
        (
            litellm.RateLimitError(message="slow down", llm_provider="openai", model="text-embedding-3-small"),
            "The model provider is rate limiting requests. Please try again shortly.",
        ),
        (RuntimeError("socket closed"), "Model call failed: socket closed"),
    ],
    ids=["auth", "rate-limit", "other"],
)
async def test_embedding_provider_errors_fail_with_a_friendly_message(
    alice: ApiClient, fake_llm: FakeLLM, error: BaseException, message: str
) -> None:
    fake_llm.embedding_error = error

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert item == {**item, "status": "failed", "error": message, "num_chunks": 0}
    assert await all_point_count(item["id"]) == 0
    assert await embedding_events() == []


async def test_a_failing_later_batch_keeps_the_usage_and_leaves_no_vectors(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, ingestion_sleeps: list[float]
) -> None:
    monkeypatch.setattr(settings, "chunk_size", 200)
    monkeypatch.setattr(settings, "chunk_overlap", 0)
    monkeypatch.setattr(settings, "embed_batch_size", 1)

    async def fail_from_the_second_call(call: int) -> None:
        if call >= 2:
            raise litellm.APIConnectionError(message="connection reset", llm_provider="openai", model="x")

    wrap_embedding(monkeypatch, fake_llm, fail_from_the_second_call)

    item = await ingest(alice, "long.txt", txt_bytes(PARAGRAPH * 3))

    assert item["status"] == "failed"
    assert item["error"] == "Could not connect to the model provider."
    assert ingestion_sleeps == [1.0, 4.0]  # the second batch, tried three times
    assert await all_point_count(item["id"]) == 0
    [event] = await embedding_events()
    assert event.prompt_tokens == len(fake_llm.embedding_calls[0]["input"][0].split())
    assert len(fake_llm.embedding_calls) == 1


async def test_vector_size_mismatch_fails_with_a_clear_message(alice: ApiClient, fake_llm: FakeLLM) -> None:
    model = await active_model()
    first = await ingest(alice, "first.txt", txt_bytes(PARAGRAPH))
    fake_llm.embedding_dim = 32

    second = await ingest(alice, "second.txt", txt_bytes(PARAGRAPH.replace("lighthouse", "windmill")))

    assert first["status"] == "ready"
    assert second["status"] == "failed"
    assert second["error"] == (
        f"Processing failed: Vector collection {vectorstore.collection_name(model.id)} has dimension 64 but the "
        "embedding model returned 32. Delete and re-add the embedding model, then re-index."
    )
    assert await all_point_count(second["id"]) == 0
    assert len(await embedding_events()) == 2


async def test_unexpected_errors_are_summarised_and_truncated(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any) -> None:
        raise RuntimeError("x" * 2000 + "\nTraceback line that must not be shown")

    monkeypatch.setattr(ingestion, "chunk_segments", explode)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert item["status"] == "failed"
    assert item["error"] == "Processing failed: " + "x" * 300


async def test_long_user_facing_errors_are_capped_at_1000_characters(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any) -> None:
        raise DocumentParseError("y" * 5000)

    monkeypatch.setattr(ingestion, "parse_file", refuse)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert item["error"] == "y" * 1000


# ---------------- Races with delete and reprocess ----------------


async def test_document_deleted_while_embedding_keeps_no_vectors(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))

    async def delete_document(call: int) -> None:
        assert (await alice.delete(f"/api/documents/{doc_id}")).status_code == 204

    wrap_embedding(monkeypatch, fake_llm, delete_document)

    assert await process(doc_id) is None
    assert len(fake_llm.embedding_calls) == 1
    assert await all_point_count(doc_id) == 0
    assert list(Path(settings.upload_dir).iterdir()) == []


async def test_document_deleted_after_its_vectors_were_written_keeps_none(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    upsert = vectorstore.upsert_chunks
    written: list[int] = []

    async def upsert_then_delete(name: str, chunk_points: list[vectorstore.ChunkPoint]) -> None:
        await upsert(name, chunk_points)
        written.append(len(chunk_points))
        assert (await alice.delete(f"/api/documents/{doc_id}")).status_code == 204

    monkeypatch.setattr(vectorstore, "upsert_chunks", upsert_then_delete)

    assert await process(doc_id) is None
    assert written and written[0] > 0
    assert await all_point_count(doc_id) == 0


async def test_reprocess_requested_while_processing_leaves_the_document_queued(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = await active_model()
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))

    async def reprocess(call: int) -> None:
        if call == 1:
            assert (await alice.post(f"/api/documents/{doc_id}/reprocess")).status_code == 200

    wrap_embedding(monkeypatch, fake_llm, reprocess)

    first = await process(doc_id)
    queued = await get_redis().lrange(INGEST_QUEUE, 0, -1)
    second = await process(doc_id)

    assert first is not None and (first.status, first.embedding_model_id) == ("pending", model.id)
    assert queued == [str(doc_id)]
    assert second is not None and second.status == "ready"
    assert len(await points(model.id, doc_id)) == second.num_chunks


async def test_a_cancelled_run_records_what_it_embedded_and_stays_processing(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chunk_size", 200)
    monkeypatch.setattr(settings, "chunk_overlap", 0)
    monkeypatch.setattr(settings, "embed_batch_size", 1)
    doc_id = await uploaded_id(alice, "long.txt", txt_bytes(PARAGRAPH * 3))
    second_batch = asyncio.Event()

    async def hang_on_the_second_batch(call: int) -> None:
        if call == 2:
            second_batch.set()
            await asyncio.Event().wait()

    wrap_embedding(monkeypatch, fake_llm, hang_on_the_second_batch)
    await get_redis().lrem(INGEST_QUEUE, 0, str(doc_id))
    job = asyncio.create_task(process_document(doc_id))
    await asyncio.wait_for(second_batch.wait(), 10)
    job.cancel()  # what the worker does to jobs still running when its shutdown grace period ends
    with pytest.raises(asyncio.CancelledError):
        await job

    [event] = await embedding_events()
    assert event.prompt_tokens == len(fake_llm.embedding_calls[0]["input"][0].split())
    async with SessionLocal() as session:
        document = await session.get(Document, doc_id)
    assert document is not None and document.status == "processing"  # retried on the next start
    # A graceful stop after the parse isn't a crash: it doesn't count toward MAX_INTERRUPTED_RUNS.
    assert await get_redis().exists(ingest_attempts_key(doc_id)) == 0


async def test_a_finished_run_is_not_counted_as_interrupted(alice: ApiClient) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    await get_redis().set(ingest_attempts_key(doc_id), "2")

    document = await process(doc_id)

    assert document is not None and document.status == "ready"
    assert await get_redis().exists(ingest_attempts_key(doc_id)) == 0


# ---------------- Switching the active embedding model ----------------


async def collection_size(model_id: int) -> int | None:
    client = vectorstore.get_qdrant()
    name = vectorstore.collection_name(model_id)
    if not await client.collection_exists(name):
        return None
    return (await client.get_collection(name)).config.params.vectors.size  # type: ignore[union-attr]


async def test_editing_the_embedding_model_mid_run_queues_the_document_for_the_new_model(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = await active_model()
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))

    async def edit_the_model(call: int) -> None:
        if call == 1:
            edited = await admin_client.patch(
                f"/api/admin/models/{model.id}", json={"litellm_model": "openai/text-embedding-3-large"}
            )
            assert edited.status_code == 200

    wrap_embedding(monkeypatch, fake_llm, edit_the_model)
    interrupted = await process(doc_id)
    queued = await get_redis().lrange(INGEST_QUEUE, 0, -1)
    size_meanwhile = await collection_size(model.id)
    fake_llm.embedding_dim = 128  # the new model's vectors
    document = await process(doc_id)

    assert interrupted is not None and interrupted.status == "pending"
    assert queued == [str(doc_id)]
    # The old model's 64-d vectors never came back: they would break every later upload and search.
    assert size_meanwhile is None
    assert document is not None and (document.status, document.embedding_model_id) == ("ready", model.id)
    assert await collection_size(model.id) == 128
    assert (await model_named("openai/text-embedding-3-large")).embedding_dim in (None, 128)
    assert all(len(record.vector or []) == 128 for record in await points(model.id, doc_id))
    assert fake_llm.embedding_calls[-1]["model"] == "openai/text-embedding-3-large"
    assert (await alice.get("/api/documents")).json()[0]["is_stale"] is False


async def test_editing_the_embedding_model_after_the_vectors_were_written_queues_the_document_again(
    alice: ApiClient, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = await active_model()
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    upsert = vectorstore.upsert_chunks

    async def upsert_then_edit(name: str, chunk_points: list[vectorstore.ChunkPoint]) -> None:
        await upsert(name, chunk_points)
        edited = await admin_client.patch(f"/api/admin/models/{model.id}", json={"api_base": "https://emb.example/v1"})
        assert edited.status_code == 200

    monkeypatch.setattr(vectorstore, "upsert_chunks", upsert_then_edit)
    interrupted = await process(doc_id)

    assert interrupted is not None and interrupted.status == "pending"  # not ready without any vectors
    assert await all_point_count(doc_id) == 0
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == [str(doc_id)]


async def test_deleting_the_embedding_model_mid_run_queues_the_document_for_the_new_default(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = await active_model()
    new = await model_named("openai/text-embedding-3-large")
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))

    async def replace_the_model(call: int) -> None:
        if call == 1:
            switched = await admin_client.patch(
                f"/api/admin/models/{new.id}", json={"enabled": True, "is_default": True}
            )
            deleted = await admin_client.delete(f"/api/admin/models/{old.id}")
            assert (switched.status_code, deleted.status_code) == (200, 204)

    wrap_embedding(monkeypatch, fake_llm, replace_the_model)
    interrupted = await process(doc_id)
    document = await process(doc_id)

    assert interrupted is not None and interrupted.status == "pending"  # used to stay "processing" for good
    assert await collection_size(old.id) is None  # no orphan collection of the deleted model
    assert document is not None and (document.status, document.embedding_model_id) == ("ready", new.id)
    first, second = await embedding_events()
    assert (first.model_id, first.model_name) == (None, old.litellm_model)  # recorded by name
    assert (second.model_id, second.model_name) == (new.id, new.litellm_model)


async def test_reprocess_after_switching_the_embedding_model_moves_the_vectors(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    old = await active_model()
    new = await model_named("openai/text-embedding-3-large")
    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH * 2))
    assert len(await points(old.id, item["id"])) == item["num_chunks"]

    switched = await admin_client.patch(f"/api/admin/models/{new.id}", json={"enabled": True, "is_default": True})
    stale = (await alice.get("/api/documents")).json()[0]
    fake_llm.embedding_dim = 32
    reprocess = await alice.post(f"/api/documents/{item['id']}/reprocess")
    document = await process(item["id"])
    fresh = (await alice.get("/api/documents")).json()[0]

    assert switched.status_code == 200, switched.text
    assert stale["is_stale"] is True
    assert reprocess.status_code == 200
    assert reprocess.json()["status"] == "pending"
    assert document is not None
    assert (document.status, document.embedding_model_id) == ("ready", new.id)
    assert fresh["is_stale"] is False
    assert await points(old.id, item["id"]) == []
    moved = await points(new.id, item["id"])
    assert len(moved) == document.num_chunks
    assert all(len(record.vector or []) == 32 for record in moved)
    assert fake_llm.embedding_calls[-1]["model"] == "openai/text-embedding-3-large"
    assert (await model_named("openai/text-embedding-3-large")).embedding_dim == 32
    events = await embedding_events()
    assert [event.model_id for event in events] == [old.id, new.id]


async def test_switching_the_model_marks_old_documents_stale_and_indexes_new_ones_with_it(
    alice: ApiClient, admin_client: ApiClient
) -> None:
    old = await active_model()
    new = await model_named("openai/text-embedding-3-large")
    first = await ingest(alice, "first.txt", txt_bytes(PARAGRAPH))
    await admin_client.patch(f"/api/admin/models/{new.id}", json={"enabled": True, "is_default": True})

    second = await ingest(alice, "second.txt", txt_bytes(PARAGRAPH.replace("lighthouse", "windmill")))

    assert (first["is_stale"], second["is_stale"]) == (False, False)
    listing = {item["id"]: item["is_stale"] for item in (await alice.get("/api/documents")).json()}
    assert listing == {first["id"]: True, second["id"]: False}
    assert len(await points(old.id, first["id"])) == first["num_chunks"]
    assert len(await points(new.id, second["id"])) == second["num_chunks"]


# ---------------- Best-effort steps that fail ----------------


class RedisDown:
    """A Redis client whose pipelines fail, as while the server is unreachable."""

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("Connection refused")


async def test_a_run_finishes_when_redis_cant_count_the_attempt(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    monkeypatch.setattr(ingestion, "get_redis", RedisDown)

    with caplog.at_level(logging.WARNING, logger="app.services.ingestion"):
        document = await process(doc_id)

    assert document is not None and document.status == "ready"
    assert f"Could not count the ingestion attempt of document {doc_id}" in caplog.text


async def test_a_stored_file_that_cant_be_read_fails_the_document(alice: ApiClient) -> None:
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    async with SessionLocal() as session:
        stored_name = await session.scalar(select(Document.stored_name).where(Document.id == doc_id))
    assert stored_name is not None
    path = storage.stored_path(stored_name)
    path.unlink()
    path.mkdir()  # still there, but not readable as a file

    document = await process(doc_id)

    assert document is not None
    assert (document.status, document.error) == ("failed", "The uploaded file could not be read.")
    assert await all_point_count(doc_id) == 0


async def test_usage_that_cant_be_recorded_doesnt_fail_the_document(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def database_down(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("INSERT INTO usage_events", {}, Exception("server closed the connection"))

    monkeypatch.setattr(ingestion, "record_usage", database_down)

    with caplog.at_level(logging.ERROR, logger="app.services.ingestion"):
        item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert item["status"] == "ready" and item["num_chunks"] > 0
    assert await embedding_events() == []
    assert f"Could not record the usage of document {item['id']}" in caplog.text


async def test_a_document_queued_again_while_redis_is_down_is_left_for_the_sweep(
    alice: ApiClient,
    admin_client: ApiClient,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = await active_model()
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))

    async def edit_the_model(call: int) -> None:
        if call == 1:
            edited = await admin_client.patch(f"/api/admin/models/{model.id}", json={"api_base": "https://emb.example/v1"})
            assert edited.status_code == 200

    async def redis_down(doc_id: int) -> None:
        raise RedisConnectionError("Connection refused")

    wrap_embedding(monkeypatch, fake_llm, edit_the_model)
    monkeypatch.setattr(ingestion, "enqueue_document", redis_down)
    with caplog.at_level(logging.WARNING, logger="app.services.ingestion"):
        interrupted = await process(doc_id)

    assert interrupted is not None and interrupted.status == "pending"
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == []
    assert f"Could not queue document {doc_id} again" in caplog.text
    assert await ingestion.requeue_orphans(0) == 1
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == [str(doc_id)]


async def test_a_deleted_models_collection_that_cant_be_dropped_doesnt_stop_the_retry(
    alice: ApiClient,
    admin_client: ApiClient,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    old = await active_model()
    new = await model_named("openai/text-embedding-3-large")
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))

    async def qdrant_down(name: str) -> None:
        raise ConnectionError("Qdrant is unreachable")

    async def replace_the_model(call: int) -> None:
        if call == 1:
            switched = await admin_client.patch(
                f"/api/admin/models/{new.id}", json={"enabled": True, "is_default": True}
            )
            deleted = await admin_client.delete(f"/api/admin/models/{old.id}")
            assert (switched.status_code, deleted.status_code) == (200, 204)
            monkeypatch.setattr(vectorstore, "drop_collection", qdrant_down)

    wrap_embedding(monkeypatch, fake_llm, replace_the_model)
    with caplog.at_level(logging.ERROR, logger="app.services.ingestion"):
        interrupted = await process(doc_id)

    assert interrupted is not None and interrupted.status == "pending"
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == [str(doc_id)]
    assert f"Could not drop the vector collection {vectorstore.collection_name(old.id)}" in caplog.text
    document = await process(doc_id)
    assert document is not None and (document.status, document.embedding_model_id) == ("ready", new.id)


# ---------------- Dropped connections and timeouts ----------------

DISCONNECTED = "Server disconnected without sending a response."


def dropped_connection() -> Exception:
    return litellm.APIConnectionError(message=DISCONNECTED, llm_provider="openai", model="text-embedding-3-small")


def timed_out() -> Exception:
    return litellm.Timeout(message="Request timed out.", model="text-embedding-3-small", llm_provider="openai")


def caused_by(error: Exception, cause: BaseException) -> Exception:
    error.__cause__ = cause
    return error


def cause_cycle() -> Exception:
    first, second = ValueError("first"), ValueError("second")
    first.__cause__, second.__cause__ = second, first
    return first


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (httpx.RemoteProtocolError(DISCONNECTED), True),
        (httpx.ConnectError("Connection refused"), True),
        (httpx.ReadError("Connection reset by peer"), True),
        (httpx.ReadTimeout("timed out"), True),
        (ConnectionResetError(), True),
        (TimeoutError(), True),
        (dropped_connection(), True),
        (timed_out(), True),
        (caused_by(llm.LLMError("Could not connect to the model provider."), dropped_connection()), True),
        (ResponseHandlingException(httpx.RemoteProtocolError(DISCONNECTED)), True),
        (httpx.UnsupportedProtocol("Request URL has an unsupported protocol 'ftp://'."), False),
        (httpx.LocalProtocolError("Illegal header value"), False),
        (litellm.AuthenticationError(message="bad key", llm_provider="openai", model="m"), False),
        (litellm.RateLimitError(message="insufficient_quota", llm_provider="openai", model="m"), False),
        (litellm.BadRequestError(message="input too long", llm_provider="openai", model="m"), False),
        (litellm.InternalServerError(message="server error", llm_provider="openai", model="m"), False),
        (llm.LLMError("The embedding provider returned an unexpected response."), False),
        (UnexpectedResponse(400, "Bad Request", b"{}", httpx.Headers()), False),
        (ResponseHandlingException(ValueError("not a valid response")), False),
        (RuntimeError("socket closed"), False),
        (cause_cycle(), False),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, BaseException) else str(value),
)
def test_only_dropped_connections_and_timeouts_are_retried(error: BaseException, transient: bool) -> None:
    assert ingestion._is_transient(error) is transient


@pytest.mark.parametrize("error", [dropped_connection, timed_out])
async def test_an_embedding_call_that_drops_its_connection_is_retried_and_the_document_gets_ready(
    alice: ApiClient,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    ingestion_sleeps: list[float],
    error: Callable[[], Exception],
) -> None:
    async def fail_twice(call: int) -> None:
        if call <= 2:
            raise error()

    wrap_embedding(monkeypatch, fake_llm, fail_twice)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert (item["status"], item["error"]) == ("ready", None)
    assert ingestion_sleeps == [1.0, 4.0]
    assert len(fake_llm.embedding_calls) == 1 and await all_point_count(item["id"]) == item["num_chunks"]
    assert len(await embedding_events()) == 1


async def test_an_embedding_provider_that_stays_unreachable_fails_the_document_after_three_tries(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, ingestion_sleeps: list[float]
) -> None:
    calls: list[int] = []

    async def unreachable(call: int) -> None:
        calls.append(call)
        raise dropped_connection()

    wrap_embedding(monkeypatch, fake_llm, unreachable)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert (item["status"], item["error"]) == ("failed", "Could not connect to the model provider.")
    assert calls == [1, 2, 3] and ingestion_sleeps == [1.0, 4.0]
    # One run with a result: the retries don't count toward MAX_INTERRUPTED_RUNS.
    assert await get_redis().exists(ingest_attempts_key(item["id"])) == 0


@pytest.mark.parametrize(
    "error",
    [
        litellm.AuthenticationError(message="bad key", llm_provider="openai", model="text-embedding-3-small"),
        litellm.RateLimitError(message="insufficient_quota", llm_provider="openai", model="text-embedding-3-small"),
        litellm.BadRequestError(message="input too long", llm_provider="openai", model="text-embedding-3-small"),
        litellm.InternalServerError(message="server error", llm_provider="openai", model="text-embedding-3-small"),
        RuntimeError("socket closed"),
    ],
    ids=["auth", "quota", "bad-request", "server-error", "other"],
)
async def test_other_embedding_errors_fail_the_document_at_once(
    alice: ApiClient,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    ingestion_sleeps: list[float],
    error: Exception,
) -> None:
    calls: list[int] = []

    async def refuse(call: int) -> None:
        calls.append(call)
        raise error

    wrap_embedding(monkeypatch, fake_llm, refuse)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert item["status"] == "failed"
    assert calls == [1] and ingestion_sleeps == []


@pytest.mark.parametrize("method", ["create_collection", "delete", "upsert"])
async def test_a_qdrant_call_that_drops_its_connection_is_retried(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch, ingestion_sleeps: list[float], method: str
) -> None:
    client = vectorstore.get_qdrant()
    real = getattr(client, method)
    calls = 0

    async def drop_the_first(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ResponseHandlingException(httpx.RemoteProtocolError(DISCONNECTED))
        return await real(*args, **kwargs)

    monkeypatch.setattr(client, method, drop_the_first)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert (item["status"], item["error"]) == ("ready", None)
    assert calls == 2 and ingestion_sleeps == [1.0]
    assert await all_point_count(item["id"]) == item["num_chunks"]


async def test_a_qdrant_error_response_fails_the_document_at_once(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch, ingestion_sleeps: list[float]
) -> None:
    client = vectorstore.get_qdrant()
    calls = 0

    async def reject(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise UnexpectedResponse(400, "Bad Request", b"{}", httpx.Headers())

    monkeypatch.setattr(client, "upsert", reject)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert item["status"] == "failed" and item["error"].startswith("Processing failed: Unexpected Response: 400")
    assert calls == 1 and ingestion_sleeps == []


async def test_no_retry_starts_more_than_two_minutes_after_the_first_try(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, ingestion_sleeps: list[float]
) -> None:
    now = 1000.0
    monkeypatch.setattr(ingestion, "_clock", lambda: now)
    calls: list[int] = []

    async def time_out_after_100_seconds(call: int) -> None:
        nonlocal now
        calls.append(call)
        now += 100
        raise timed_out()

    wrap_embedding(monkeypatch, fake_llm, time_out_after_100_seconds)

    item = await ingest(alice, "notes.txt", txt_bytes(PARAGRAPH))

    assert (item["status"], item["error"]) == ("failed", "The model provider timed out.")
    # The retry after 1 s starts at 101 s; the next would start at 204 s.
    assert calls == [1, 2] and ingestion_sleeps == [1.0]


async def test_a_shutdown_during_a_retry_wait_leaves_the_document_for_the_next_start(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = asyncio.Event()

    async def wait_forever(seconds: float) -> None:
        waiting.set()
        await asyncio.Event().wait()

    async def unreachable(call: int) -> None:
        raise dropped_connection()

    monkeypatch.setattr(ingestion, "_sleep", wait_forever)
    wrap_embedding(monkeypatch, fake_llm, unreachable)
    doc_id = await uploaded_id(alice, "notes.txt", txt_bytes(PARAGRAPH))
    await get_redis().lrem(INGEST_QUEUE, 0, str(doc_id))
    job = asyncio.create_task(process_document(doc_id))
    await asyncio.wait_for(waiting.wait(), 10)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    async with SessionLocal() as session:
        document = await session.get(Document, doc_id)
    assert document is not None and document.status == "processing"  # retried on the next start
