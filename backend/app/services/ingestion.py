"""Ingestion queue and the parse -> chunk -> embed -> index pipeline (SPEC section 7)."""

import asyncio
import logging
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from pypdf import PdfReader
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Document, LLMModel, User, VisionPage, utcnow
from app.redis_client import INGEST_QUEUE, forget_ingest_attempts, get_redis, ingest_attempts_key
from app.services import llm, storage, vectorstore, vision
from app.services.chunking import Chunk, chunk_segments
from app.services.model_registry import get_active_embedding_model, get_active_vision_model
from app.services.parsing import DocumentParseError, ParsedDocument, Segment, VisionRequest, parse_file
from app.services.usage import record_usage, vision_tokens_this_month

logger = logging.getLogger(__name__)

NO_EMBEDDING_MODEL = "No active embedding model is configured. Ask the admin to enable one."
_MISSING_FILE = "The uploaded file is missing from storage."
_MAX_ERROR_CHARS = 1000
_MAX_REASON_CHARS = 300
_RUNNABLE = ("pending", "processing")
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".webp"})  # images the assistant generated (never uploads)
# A document whose runs keep ending without a result (e.g. the worker is killed for running out of memory
# while parsing it) fails after this many, instead of being retried on every start forever.
MAX_INTERRUPTED_RUNS = 3
_ATTEMPTS_TTL_SECONDS = 86_400
_INTERRUPTED = (
    f"Processing was interrupted {MAX_INTERRUPTED_RUNS} times; the file may be too large or complex to process."
)

_locks: dict[int, asyncio.Lock] = {}
_lock_users: Counter[int] = Counter()
# Strong references to in-flight usage records: the event loop only keeps weak ones.
_pending_records: set[asyncio.Task[None]] = set()
# Told when a job enters (True) and leaves (False) the vision phase, from inside the job's task (app.worker).
_vision_phase_listener: Callable[[bool], None] | None = None
# At most WORKER_CONCURRENCY documents read pages at once; one semaphore per event loop (asyncio primitives bind to
# the loop that first waits on them) and configured size.
_vision_phase_limits: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = WeakKeyDictionary()


class _IngestionError(Exception):
    """A failure whose message is safe to show to the document's owner."""


class _ModelChanged(Exception):
    """The embedding model was edited (litellm_model/api_base) or deleted during the run."""


@dataclass(frozen=True)
class _Job:
    doc_id: int
    owner_id: int
    scope: str
    filename: str
    stored_name: str
    previous_model_id: int | None
    model_target: tuple[str, str | None]  # the model's (litellm_model, api_base) when the run started
    vision_model: LLMModel | None = None  # the active vision model when the run started (detached)
    vision_target: tuple[str, str | None] | None = None  # its (litellm_model, api_base)
    vision_blocked: str | None = None  # the owner's vision allowance is used up: the error for a scanned page


@dataclass
class _Usage:
    """What a run has used so far. Recorded on every path: success, failure, stop, re-queue and cancellation."""

    embedding: llm.Usage = field(default_factory=llm.Usage)
    vision: llm.Usage = field(default_factory=llm.Usage)
    parsed: bool = False  # the parse finished: a cancellation after it isn't the file's fault


def set_vision_phase_listener(listener: Callable[[bool], None] | None) -> None:
    """The worker frees a job's slot while it reads pages with the vision model (network waits, possibly minutes)."""
    global _vision_phase_listener
    _vision_phase_listener = listener


async def enqueue_document(doc_id: int) -> None:
    await get_redis().rpush(INGEST_QUEUE, str(doc_id))


async def recover_pending() -> int:
    """Rebuild the queue from the database: documents left pending or processing are queued again.

    The queue is cleared first, so a restart doesn't pile up duplicates. Documents uploaded meanwhile are
    still covered: their row is committed before they are queued. A document left processing for the
    MAX_INTERRUPTED_RUNS-th time fails instead (see _reset_interrupted).
    """
    redis = get_redis()
    await redis.delete(INGEST_QUEUE)
    async with SessionLocal() as db:
        interrupted = list(await db.scalars(select(Document.id).where(Document.status == "processing")))
    await _reset_interrupted(interrupted)
    async with SessionLocal() as db:
        doc_ids = list(await db.scalars(select(Document.id).where(Document.status == "pending").order_by(Document.id)))
    if doc_ids:
        await redis.rpush(INGEST_QUEUE, *(str(doc_id) for doc_id in doc_ids))
    return len(doc_ids)


async def reclaim_interrupted(running: Collection[int], min_age_seconds: float) -> int:
    """Treat processing documents that no job of this worker is running like recover_pending() does, and queue
    the ones set back to pending. Their job ended without recording a result, e.g. the database was down for its
    final update. Assumes one worker process, like recover_pending(). Returns the number queued.
    """
    cutoff = utcnow() - timedelta(seconds=min_age_seconds)
    async with SessionLocal() as db:
        candidates = await db.scalars(
            select(Document.id)
            .where(Document.status == "processing", Document.updated_at <= cutoff)
            .order_by(Document.id)
        )
        doc_ids = [doc_id for doc_id in candidates if doc_id not in running]
    reset = await _reset_interrupted(doc_ids)
    if reset:
        await get_redis().rpush(INGEST_QUEUE, *(str(doc_id) for doc_id in reset))
    return len(reset)


async def _reset_interrupted(doc_ids: list[int]) -> list[int]:
    """Set processing documents whose run ended without a result back to pending (returned), except those
    interrupted MAX_INTERRUPTED_RUNS times: they fail, so a file that crashes the worker can't stop ingestion for
    everyone by being retried on every start."""
    if not doc_ids:
        return []
    counts = [int(count or 0) for count in await get_redis().mget([ingest_attempts_key(i) for i in doc_ids])]
    given_up = {doc_id for doc_id, count in zip(doc_ids, counts, strict=True) if count >= MAX_INTERRUPTED_RUNS}
    retried = [doc_id for doc_id in doc_ids if doc_id not in given_up]
    failed: list[tuple[int, int | None]] = []
    async with SessionLocal() as db:
        if given_up:
            result = await db.execute(
                update(Document)
                .where(Document.id.in_(given_up), Document.status == "processing")
                .values(status="failed", error=_INTERRUPTED)
                .returning(Document.id, Document.embedding_model_id)
                .execution_options(synchronize_session=False)
            )
            failed = list(result.tuples())
        if retried:
            result = await db.execute(
                update(Document)
                .where(Document.id.in_(retried), Document.status == "processing")
                .values(status="pending")
                .returning(Document.id)
                .execution_options(synchronize_session=False)
            )
            retried = sorted(result.scalars())
        active = await get_active_embedding_model(db) if failed else None
        await db.commit()
    for doc_id, model_id in failed:
        logger.warning("Document %s was interrupted %d times; marking it failed", doc_id, MAX_INTERRUPTED_RUNS)
        # The interrupted run may have written some vectors.
        collections = {vectorstore.collection_name(i) for i in (model_id, active.id if active else None) if i}
        await _discard_points(doc_id, collections)
    await forget_ingest_attempts(given_up)
    return retried


async def requeue_orphans(min_age_seconds: float) -> int:
    """Queue pending documents missing from the queue, e.g. uploaded while Redis was down (the upload still
    succeeds). Only documents pending for at least `min_age_seconds` count, so one whose request is about to
    queue it isn't queued twice (a duplicate would be harmless anyway). Running documents are left alone.
    """
    cutoff = utcnow() - timedelta(seconds=min_age_seconds)
    async with SessionLocal() as db:
        doc_ids = list(
            await db.scalars(
                select(Document.id)
                .where(Document.status == "pending", Document.updated_at <= cutoff)
                .order_by(Document.id)
            )
        )
    if not doc_ids:
        return 0
    redis = get_redis()
    queued = set(await redis.lrange(INGEST_QUEUE, 0, -1))
    missing = [str(doc_id) for doc_id in doc_ids if str(doc_id) not in queued]
    if missing:
        await redis.rpush(INGEST_QUEUE, *missing)
    return len(missing)


async def process_document(doc_id: int) -> None:
    """Parse, chunk, embed and index one document; failures are stored on the document.

    Only pending/processing documents are touched, so duplicate queue entries are harmless. A document
    deleted meanwhile keeps no vectors; one re-queued meanwhile stays pending for the next run, and one whose
    embedding model was edited or deleted meanwhile is queued again for the model that is active now.
    """
    async with _exclusive(doc_id):
        claimed = await _claim(doc_id)
        if claimed is None:
            return
        await _run(*claimed)
        # The run finished; one that never gets here (killed, cancelled, failed unexpectedly) counts as interrupted.
        await forget_ingest_attempts([doc_id])


async def _run(job: _Job, model: LLMModel) -> None:
    usage = _Usage()
    try:
        num_chunks, num_pages, vision_keys = await _index(job, model, usage)
    except asyncio.CancelledError:
        # Shutdown cancelled the job: it stays "processing" for recover_pending(), but the calls already made were
        # paid for. A stop after the parse isn't a crash, so it doesn't count toward MAX_INTERRUPTED_RUNS.
        task = asyncio.create_task(_after_cancel(job, model, usage))
        _pending_records.add(task)
        task.add_done_callback(_pending_records.discard)
        await asyncio.shield(task)
        raise
    except vision.Stopped:
        logger.info("Document %s stopped: the document was deleted or queued again", job.doc_id)
        await _record_usage_in_new_session(job, model, usage)
        return
    except Exception as exc:
        if isinstance(exc, (_ModelChanged, vision.ModelChanged)) or not await _model_is_current(job, model):
            await _retry_later(job, model, usage)
        else:
            await _fail(job, model, usage, _failure_message(job.doc_id, exc))
        return

    try:
        await _finish(job, model, usage, num_chunks, num_pages, vision_keys)
    except _ModelChanged:
        await _retry_later(job, model, None)
    except Exception as exc:
        # E.g. the database failed the final update: don't leave the document "processing" if it can be helped
        # (if this fails too, the worker's sweep picks the document up again).
        await _fail(job, model, None, _failure_message(job.doc_id, exc))


async def _after_cancel(job: _Job, model: LLMModel, usage: _Usage) -> None:
    await _record_usage_in_new_session(job, model, usage)
    if usage.parsed:
        await forget_ingest_attempts([job.doc_id])


async def _finish(
    job: _Job,
    model: LLMModel,
    usage: _Usage,
    num_chunks: int,
    num_pages: int | None,
    vision_keys: set[tuple[int, str]],
) -> None:
    async with SessionLocal() as db:
        await _record_usage(db, job, model, usage)
    async with SessionLocal() as db:
        # The lock keeps the admin from dropping the model's vectors until the document is marked ready.
        if not await _model_current_in(db, job, model.id, lock=True):
            raise _ModelChanged
        if await _update_document(
            db,
            job.doc_id,
            ("processing",),
            commit=False,
            status="ready",
            error=None,
            num_chunks=num_chunks,
            num_pages=num_pages,
            embedding_model_id=model.id,
        ):
            # The cached pages are now exactly the ones this run read or reused.
            await _prune_vision_pages(db, job.doc_id, vision_keys)
            await db.commit()
            return
        # No longer processing: either re-queued (it stays pending; record where its vectors now live so the
        # next run replaces them) or deleted (drop the vectors just written).
        if not await _update_document(db, job.doc_id, None, embedding_model_id=model.id):
            await _discard_points(job.doc_id, {vectorstore.collection_name(model.id)})


async def _fail(job: _Job, model: LLMModel, usage: _Usage | None, error: str) -> None:
    await _discard_points(job.doc_id, _collections(job, model))
    async with SessionLocal() as db:
        if usage is not None:
            await _record_usage(db, job, model, usage)
        await _update_document(db, job.doc_id, ("processing",), status="failed", error=error)


async def _retry_later(job: _Job, model: LLMModel, usage: _Usage | None) -> None:
    """The embedding or vision model was edited, deleted or (vision) turned off during the run: drop what was
    written and queue the document again, so it is processed with the models that are active now."""
    logger.info("The embedding or vision model changed while document %s was processed; queuing it again", job.doc_id)
    collection = vectorstore.collection_name(model.id)
    await _discard_points(job.doc_id, {collection})
    async with SessionLocal() as db:
        if usage is not None:
            await _record_usage(db, job, model, usage)
        model_deleted = await db.get(LLMModel, model.id) is None
        queued_again = await _update_document(db, job.doc_id, ("processing",), status="pending", error=None)
    if model_deleted:  # the run may have recreated its collection
        try:
            await vectorstore.drop_collection(collection)
        except Exception:
            logger.exception("Could not drop the vector collection %s", collection)
    if queued_again:
        try:
            await enqueue_document(job.doc_id)
        except Exception as exc:  # the worker's sweep queues pending documents missing from the queue
            logger.warning("Could not queue document %s again: %s", job.doc_id, exc)


async def _model_current_in(db: AsyncSession, job: _Job, model_id: int, *, lock: bool = False) -> bool:
    """Whether the model still exists with the litellm_model/api_base the run started with."""
    statement = select(LLMModel.litellm_model, LLMModel.api_base).where(LLMModel.id == model_id)
    if lock:  # FOR NO KEY UPDATE: conflicts with the admin's edit or delete, not with usage rows referencing it
        statement = statement.with_for_update(key_share=True)
    row = (await db.execute(statement)).first()
    return row is not None and tuple(row) == job.model_target


async def _model_is_current(job: _Job, model: LLMModel) -> bool:
    try:
        async with SessionLocal() as db:
            return await _model_current_in(db, job, model.id)
    except Exception:
        logger.exception("Could not check the embedding model of document %s", job.doc_id)
        return True  # fail the document as usual


@asynccontextmanager
async def _exclusive(doc_id: int) -> AsyncIterator[None]:
    """Serialize runs for the same document in this process, so duplicate queue entries never overlap."""
    lock = _locks.setdefault(doc_id, asyncio.Lock())
    _lock_users[doc_id] += 1
    try:
        async with lock:
            yield
    finally:
        _lock_users[doc_id] -= 1
        if not _lock_users[doc_id]:
            del _lock_users[doc_id], _locks[doc_id]


async def _claim(doc_id: int) -> tuple[_Job, LLMModel] | None:
    async with SessionLocal() as db:
        doc = await db.get(Document, doc_id)
        if doc is None or doc.status not in _RUNNABLE:
            return None
        model = await get_active_embedding_model(db)
        if model is None:
            await _update_document(db, doc_id, _RUNNABLE, status="failed", error=NO_EMBEDDING_MODEL)
            return None
        vision_model = await get_active_vision_model(db) if _is_pdf(doc.stored_name) else None
        vision_blocked = await _vision_allowance(db, doc) if vision_model is not None else None
        if not await _update_document(db, doc_id, _RUNNABLE, status="processing", error=None):
            return None
        job = _Job(
            doc_id=doc.id,
            owner_id=doc.owner_id,
            scope=doc.scope,
            filename=doc.filename,
            stored_name=doc.stored_name,
            previous_model_id=doc.embedding_model_id,
            model_target=(model.litellm_model, model.api_base),
            vision_model=vision_model,
            vision_target=(vision_model.litellm_model, vision_model.api_base) if vision_model is not None else None,
            vision_blocked=vision_blocked,
        )
    await _count_attempt(doc_id)
    return job, model


def _is_pdf(stored_name: str) -> bool:
    return stored_name.lower().endswith(".pdf")


async def _vision_allowance(db: AsyncSession, doc: Document) -> str | None:
    """The error for scanned pages when the owner has used up VISION_MONTHLY_TOKEN_LIMIT this month, else None.
    Checked when the run starts, like the chat budget; the admin (and so the shared documents) is exempt."""
    limit = settings.vision_monthly_token_limit
    if limit <= 0 or doc.owner.is_admin:
        return None
    used = await vision_tokens_this_month(db, doc.owner_id)
    if used < limit:
        return None
    return (
        f"This PDF has scanned pages, but your monthly allowance for the vision model is used up ({used:,} of "
        f"{limit:,} tokens). Ask the admin to raise VISION_MONTHLY_TOKEN_LIMIT, or try again next month."
    )


async def _count_attempt(doc_id: int) -> None:
    """Count the run as interrupted until it finishes (see recover_pending). Best effort."""
    key = ingest_attempts_key(doc_id)
    try:
        async with get_redis().pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, _ATTEMPTS_TTL_SECONDS)
            await pipe.execute()
    except Exception as exc:
        logger.warning("Could not count the ingestion attempt of document %s: %s", doc_id, exc)


async def _update_document(
    db: AsyncSession, doc_id: int, statuses: Collection[str] | None, *, commit: bool = True, **values: Any
) -> bool:
    """Update the row only while its status is one of `statuses` (any status if None). False if no row matched.
    With commit=False the caller commits (or rolls back)."""
    statement = update(Document).where(Document.id == doc_id)
    if statuses is not None:
        statement = statement.where(Document.status.in_(statuses))
    result = await db.execute(
        statement.values(**values).returning(Document.id).execution_options(synchronize_session=False)
    )
    updated = result.first() is not None
    if commit or not updated:
        await db.commit()
    return updated


async def _prune_vision_pages(db: AsyncSession, doc_id: int, used: set[tuple[int, str]]) -> None:
    for mode in ("transcribe", "describe"):
        pages = sorted(page for page, used_mode in used if used_mode == mode)
        await db.execute(
            delete(VisionPage).where(
                VisionPage.document_id == doc_id, VisionPage.mode == mode, VisionPage.page.not_in(pages)
            )
        )


async def _index(job: _Job, model: LLMModel, usage: _Usage) -> tuple[int, int | None, set[tuple[int, str]]]:
    """Returns the chunk count, the page count and the (page, mode) cache keys the run read or reused."""
    vision_keys: set[tuple[int, str]] = set()
    if job.vision_model is not None and _is_pdf(job.stored_name):
        fingerprint = vision.fingerprint(job.vision_model)
        cache = await vision.load_cache(job.doc_id, fingerprint)
        with tempfile.TemporaryDirectory(prefix="rag-vision-", ignore_cleanup_errors=True) as tmp:
            request = VisionRequest(Path(tmp), frozenset(cache), job.vision_blocked)
            parsed = await asyncio.to_thread(_parse, job.stored_name, request)
            usage.parsed = True
            segments = parsed.segments
            if parsed.page_images:
                segments = await _read_with_vision(job, parsed, fingerprint, cache, usage, vision_keys)
    else:
        parsed = await asyncio.to_thread(_parse, job.stored_name, None)
        usage.parsed = True
        segments = parsed.segments
    chunks = await asyncio.to_thread(_chunk, segments)
    collection = vectorstore.collection_name(model.id)
    batch_size = max(1, settings.embed_batch_size)
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        result = await llm.embed_texts(model, [chunk.text for chunk in batch])
        usage.embedding.prompt_tokens += result.usage.prompt_tokens
        usage.embedding.cost_usd += result.usage.cost_usd
        if start == 0:
            await _prepare_collection(job, model, collection, len(result.vectors[0]))
        await vectorstore.upsert_chunks(
            collection,
            [
                vectorstore.ChunkPoint(
                    doc_id=job.doc_id,
                    chunk_index=chunk.index,
                    vector=vector,
                    owner_id=job.owner_id,
                    scope=job.scope,
                    filename=job.filename,
                    page=chunk.page,
                    text=chunk.text,
                )
                for chunk, vector in zip(batch, result.vectors, strict=True)
            ],
        )
    return len(chunks), parsed.num_pages, vision_keys


async def _read_with_vision(
    job: _Job,
    parsed: ParsedDocument,
    fingerprint: str,
    cache: dict[tuple[int, str], str],
    usage: _Usage,
    used: set[tuple[int, str]],
) -> list[Segment]:
    assert job.vision_model is not None
    async with _vision_phase():
        run = vision.VisionRun(
            doc_id=job.doc_id,
            model=job.vision_model,
            fingerprint=fingerprint,
            cached=cache,
            usage=usage.vision,
            checkpoint=lambda: _vision_checkpoint(job),
            deadline=time.monotonic() + settings.vision_timeout_seconds,
            used=used,
        )
        return await vision.read_pages(run, parsed, job.filename)


@asynccontextmanager
async def _vision_phase() -> AsyncIterator[None]:
    """At most WORKER_CONCURRENCY documents read pages at once; while a job does, it doesn't hold a worker slot."""
    loop = asyncio.get_running_loop()
    size = max(1, settings.worker_concurrency)
    current = _vision_phase_limits.get(loop)
    if current is None or current[0] != size:
        current = (size, asyncio.Semaphore(size))
        _vision_phase_limits[loop] = current
    async with current[1]:
        listener = _vision_phase_listener
        if listener is not None:
            listener(True)
        try:
            yield
        finally:
            if listener is not None:
                listener(False)


async def _vision_checkpoint(job: _Job) -> None:
    """Before each uncached vision call: Stopped if the document was deleted or queued again, ModelChanged if the
    vision model is no longer the active one as it was. A database error is logged and the run goes on."""
    assert job.vision_model is not None
    try:
        async with SessionLocal() as db:
            status = await db.scalar(select(Document.status).where(Document.id == job.doc_id))
            model = (
                await db.execute(
                    select(
                        LLMModel.kind, LLMModel.enabled, LLMModel.is_default, LLMModel.litellm_model, LLMModel.api_base
                    ).where(LLMModel.id == job.vision_model.id)
                )
            ).first()
    except Exception as exc:
        logger.warning("Could not check document %s before a vision call: %s", job.doc_id, exc)
        return
    if status != "processing":
        raise vision.Stopped
    if model is None or (tuple(model[:3]), tuple(model[3:])) != (("vision", True, True), job.vision_target):
        raise vision.ModelChanged


def _parse(stored_name: str, request: VisionRequest | None) -> ParsedDocument:
    path = storage.stored_path(stored_name)
    source = storage.stored_path(storage.source_name(stored_name))
    try:
        if path.suffix.lower() == ".pdf" and source.is_file() and (pages := _pdf_page_count(path)) is not None:
            # A PDF the assistant created: its text layer is lossy for shaped Indian scripts, so it is indexed from
            # the Markdown it was rendered from (no page numbers, no vision model).
            return ParsedDocument(segments=parse_file(source, ".md", None).segments, num_pages=pages, page_images=[])
        if path.suffix.lower() in _IMAGE_SUFFIXES and source.is_file():
            # An image the assistant generated: indexed from its title, description and prompt (no OCR, no vision).
            return ParsedDocument(segments=parse_file(source, ".md", None).segments, num_pages=None, page_images=[])
        return parse_file(path, path.suffix, request)
    except FileNotFoundError as exc:
        raise _IngestionError(_MISSING_FILE) from exc
    except OSError as exc:
        logger.warning("Could not read %s: %r", stored_name, exc)
        raise _IngestionError("The uploaded file could not be read.") from exc


def _pdf_page_count(path: Path) -> int | None:
    """None when the PDF can't be read (it is then parsed like any upload, which reports why)."""
    try:
        return len(PdfReader(path).pages)
    except Exception:
        return None


def _chunk(segments: Sequence[Segment]) -> list[Chunk]:
    chunks = chunk_segments(list(segments), settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise DocumentParseError("The document contains no text.")
    return chunks


async def _prepare_collection(job: _Job, model: LLMModel, collection: str, dim: int) -> None:
    """Create the target collection, remember the model's dimension and drop the document's old vectors.

    Raises _ModelChanged instead when the model was edited or deleted since the run started: its collection
    was dropped, and must not come back with the old model's dimension.
    """
    async with SessionLocal() as db:
        # The lock keeps the admin from dropping the collection between this check and its creation.
        if not await _model_current_in(db, job, model.id, lock=True):
            raise _ModelChanged
        await vectorstore.ensure_collection(collection, dim)
        litellm_model, api_base = job.model_target
        await db.execute(
            update(LLMModel)
            .where(
                LLMModel.id == model.id,
                LLMModel.embedding_dim.is_(None),
                LLMModel.litellm_model == litellm_model,
                LLMModel.api_base.is_(None) if api_base is None else LLMModel.api_base == api_base,
            )
            .values(embedding_dim=dim)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    for name in _collections(job, model):
        await vectorstore.delete_document_points(name, job.doc_id)


def _collections(job: _Job, model: LLMModel) -> set[str]:
    """The target collection plus the one holding the document's previous vectors."""
    names = {vectorstore.collection_name(model.id)}
    if job.previous_model_id is not None:
        names.add(vectorstore.collection_name(job.previous_model_id))
    return names


async def _discard_points(doc_id: int, collections: set[str]) -> None:
    for name in collections:
        try:
            await vectorstore.delete_document_points(name, doc_id)
        except Exception:
            logger.exception("Could not delete vectors of document %s from %s", doc_id, name)


async def _record_usage(db: AsyncSession, job: _Job, model: LLMModel, usage: _Usage) -> None:
    """One embedding event for everything embedded, even when a later batch failed, and one vision event for every
    finished vision call, committed together. A model deleted meanwhile is recorded by name."""
    events: list[tuple[str, LLMModel, llm.Usage]] = []
    if usage.embedding.prompt_tokens:
        events.append(("embedding", model, usage.embedding))
    if usage.vision.total_tokens and job.vision_model is not None:
        events.append(("vision", job.vision_model, usage.vision))
    if not events:
        return
    try:
        if await db.get(User, job.owner_id) is None:  # the owner's usage rows are gone with them
            for kind, event_model, event_usage in events:
                logger.warning(
                    "The owner of document %s was deleted, so its %s usage isn't recorded: %s, %d tokens, $%.6f",
                    job.doc_id,
                    kind,
                    event_model.litellm_model,
                    event_usage.total_tokens,
                    event_usage.cost_usd,
                )
            return
        for kind, event_model, event_usage in events:
            exists = await db.scalar(select(LLMModel.id).where(LLMModel.id == event_model.id)) is not None
            await record_usage(
                db,
                user_id=job.owner_id,
                model=event_model if exists else None,
                model_name=event_model.litellm_model,
                kind=kind,
                usage=event_usage,
                commit=False,
            )
        await db.commit()
    except Exception:
        logger.exception("Could not record the usage of document %s", job.doc_id)
        await db.rollback()


async def _record_usage_in_new_session(job: _Job, model: LLMModel, usage: _Usage) -> None:
    try:
        async with SessionLocal() as db:
            await _record_usage(db, job, model, usage)
    except Exception:
        logger.exception("Could not record the usage of document %s", job.doc_id)


def _failure_message(doc_id: int, exc: Exception) -> str:
    if isinstance(exc, (DocumentParseError, llm.LLMError, _IngestionError)):
        logger.info("Document %s failed: %s", doc_id, exc)
        message = str(exc)
    else:
        logger.exception("Processing document %s failed", doc_id)
        lines = str(exc).strip().splitlines()
        message = f"Processing failed: {(lines[0] if lines else type(exc).__name__)[:_MAX_REASON_CHARS]}"
    return message[:_MAX_ERROR_CHARS]
