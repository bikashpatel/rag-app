"""process_document end to end: every format to Qdrant, failures, races with delete/reprocess, model switches."""

import asyncio
import io
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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
from qdrant_client.models import Record
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import Document, LLMModel, UsageEvent
from app.redis_client import INGEST_QUEUE, get_redis, ingest_attempts_key
from app.services import ingestion, llm, vectorstore
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
        ("scan.pdf", pdf_bytes(""), "No extractable text (scanned PDFs need OCR, which isn't supported yet)."),
        ("locked.pdf", encrypted_pdf("Top secret figures."), "Password-protected PDFs aren't supported."),
        ("blank.txt", b"  \n\n\t ", "The document contains no text."),
        ("empty.html", b"<html><script>var x = 1;</script></html>", "The document contains no text."),
        ("broken.docx", b"not a zip archive", "The file is corrupted or not a valid .docx file."),
        ("broken.pdf", b"%PDF-1.4 truncated", "The file is corrupted or not a valid .pdf file."),
    ],
    ids=["scanned-pdf", "encrypted-pdf", "blank-text", "empty-html", "corrupt-docx", "corrupt-pdf"],
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
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chunk_size", 200)
    monkeypatch.setattr(settings, "chunk_overlap", 0)
    monkeypatch.setattr(settings, "embed_batch_size", 1)

    async def fail_second_call(call: int) -> None:
        if call == 2:
            raise litellm.APIConnectionError(message="connection reset", llm_provider="openai", model="x")

    wrap_embedding(monkeypatch, fake_llm, fail_second_call)

    item = await ingest(alice, "long.txt", txt_bytes(PARAGRAPH * 3))

    assert item["status"] == "failed"
    assert item["error"] == "Could not connect to the model provider."
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
    assert await get_redis().get(ingest_attempts_key(doc_id)) == "1"


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
