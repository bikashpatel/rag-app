"""Uploaded-file storage and whole-document lifecycle helpers."""

import asyncio
import logging
import unicodedata
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Document, GeneratedDocument, GeneratedImage, ImportedDocument, User, VisionPage
from app.redis_client import forget_ingest_attempts
from app.schemas import DocumentItem
from app.schemas import GeneratedImage as GeneratedImageItem
from app.services import vectorstore
from app.services.model_registry import get_active_embedding_model

logger = logging.getLogger(__name__)

_upload_locks: dict[int, asyncio.Lock] = {}
_upload_lock_users: Counter[int] = Counter()

_MAX_FILENAME_CHARS = 255
# Control, format (e.g. bidi overrides) and surrogate code points never belong in a display name, nor U+FFFE and
# U+FFFF, which XML can't hold (the name can be listed in the Sources of a generated Word or Excel file).
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_NONCHARACTERS = frozenset("\ufffe\uffff")


def upload_dir() -> Path:
    path = Path(settings.upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_stored_name(extension: str) -> str:
    """Random on-disk name; the user's filename is never used as a path."""
    return f"{uuid.uuid4().hex}{extension.lower()}"


def stored_path(stored_name: str) -> Path:
    if not stored_name or "/" in stored_name or "\\" in stored_name or stored_name.startswith("."):
        raise ValueError("invalid stored file name")
    return upload_dir() / stored_name


def source_name(stored_name: str) -> str:
    """The Markdown source stored next to a PDF or an image the assistant created (indexed instead of the file)."""
    return f"{stored_name}.source.md"


def preview_name(stored_name: str) -> str:
    """The WebP preview (long edge 1024) stored next to an image the assistant generated."""
    return f"{stored_name}.preview.webp"


def thumb_name(stored_name: str) -> str:
    """The WebP thumbnail (long edge 256) stored next to an image the assistant generated."""
    return f"{stored_name}.thumb.webp"


def basename(filename: str | None) -> str:
    return (filename or "").replace("\\", "/").rsplit("/", 1)[-1]


def extension_of(filename: str | None) -> str:
    """The lowercased extension of the name's last path segment ("" when it has none)."""
    return PurePosixPath(basename(filename)).suffix.lower()


def display_filename(filename: str | None, extension: str) -> str:
    """Basename without control/format characters, whitespace collapsed, at most 255 chars keeping the extension."""
    name = unicodedata.normalize("NFC", basename(filename))
    visible = "".join(
        ch
        for ch in name
        if ch.isspace() or (unicodedata.category(ch) not in _HIDDEN_CATEGORIES and ch not in _NONCHARACTERS)
    )
    name = " ".join(visible.split())
    stem, suffix = name[: len(name) - len(extension)], name[len(name) - len(extension) :]
    if not stem or suffix.lower() != extension:
        return f"document{extension}"
    return stem[: _MAX_FILENAME_CHARS - len(suffix)] + suffix


def file_names(stored_name: str) -> tuple[str, ...]:
    """A document's file and the sidecars it may have (Markdown source, image renditions)."""
    return stored_name, source_name(stored_name), preview_name(stored_name), thumb_name(stored_name)


def remove_file(stored_name: str) -> None:
    """Remove a stored file and its sidecars (Markdown source, image renditions), if any."""
    for name in file_names(stored_name):
        try:
            stored_path(name).unlink(missing_ok=True)
        except Exception:
            logger.exception("Could not remove uploaded file %s", name)


def document_item(
    doc: Document,
    *,
    viewer: User,
    active_embedding_model_id: int | None,
    admin_view: bool = False,
    vision_pages: int = 0,
    generated: bool = False,
    image: GeneratedImageItem | None = None,
    imported_from: str | None = None,
) -> DocumentItem:
    """Build the DocumentItem response. `doc.owner` must be loaded (it is selectin by default).
    `vision_pages`: the document's vision_pages rows (see vision_page_counts); `generated`: it has a
    generated_documents row (see generated_ids); `image`: its display details when it is a generated image (see
    generated_images), never in the admin view (prompts and descriptions are the owner's private content);
    `imported_from`: the plugin it was imported through (see imported_sources)."""
    if admin_view:
        can_delete = viewer.is_admin
    else:
        can_delete = (doc.scope == "private" and doc.owner_id == viewer.id) or (
            doc.scope == "shared" and viewer.is_admin
        )
    return DocumentItem(
        id=doc.id,
        filename=doc.filename,
        scope=doc.scope,  # type: ignore[arg-type]
        status=doc.status,  # type: ignore[arg-type]
        error=doc.error,
        size_bytes=doc.size_bytes,
        content_type=doc.content_type,
        num_chunks=doc.num_chunks,
        num_pages=doc.num_pages,
        owner_id=doc.owner_id,
        owner_username=doc.owner.username if doc.owner is not None else "",
        can_delete=can_delete,
        is_stale=doc.status == "ready" and doc.embedding_model_id != active_embedding_model_id,
        vision_pages=vision_pages if doc.status == "ready" else 0,
        generated=generated,
        image=None if admin_view else image,
        imported_from=imported_from,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


async def vision_page_counts(db: AsyncSession, documents: Select[tuple[int]]) -> dict[int, int]:
    """document id -> number of vision_pages rows, for the ids `documents` selects (one grouped query)."""
    rows = await db.execute(
        select(VisionPage.document_id, func.count())
        .where(VisionPage.document_id.in_(documents))
        .group_by(VisionPage.document_id)
    )
    return {doc_id: count for doc_id, count in rows.tuples()}


async def generated_ids(db: AsyncSession, documents: Select[tuple[int]]) -> set[int]:
    """The ids among those `documents` selects that the assistant created (one query)."""
    return set(
        await db.scalars(select(GeneratedDocument.document_id).where(GeneratedDocument.document_id.in_(documents)))
    )


def image_item(row: GeneratedImage) -> GeneratedImageItem:
    return GeneratedImageItem(
        width=row.width,
        height=row.height,
        alt_text=row.alt_text,
        quality=row.quality,  # type: ignore[arg-type]
    )


async def generated_images(db: AsyncSession, documents: Select[tuple[int]]) -> dict[int, GeneratedImageItem]:
    """document id -> display details, for the ids `documents` selects that are generated images (one query)."""
    rows = await db.scalars(select(GeneratedImage).where(GeneratedImage.document_id.in_(documents)))
    return {row.document_id: image_item(row) for row in rows}


async def imported_sources(db: AsyncSession, documents: Select[tuple[int]]) -> dict[int, str]:
    """document id -> the display name of the plugin it was imported through ("Gmail"), for the ids `documents`
    selects that were imported (one query)."""
    from app.plugins import registry  # it imports services that import this module

    rows = await db.execute(
        select(ImportedDocument.document_id, ImportedDocument.plugin_id).where(
            ImportedDocument.document_id.in_(documents)
        )
    )
    return {doc_id: registry.plugin_name(plugin_id) for doc_id, plugin_id in rows.tuples()}


async def is_generated(db: AsyncSession, doc_id: int) -> bool:
    row = await db.scalar(select(GeneratedDocument.document_id).where(GeneratedDocument.document_id == doc_id))
    return row is not None


@asynccontextmanager
async def serialized_uploads(db: AsyncSession, user_id: int) -> AsyncIterator[None]:
    """One new document per user at a time (uploads and files the assistant creates), from the quota and duplicate
    checks to the commit.

    The asyncio lock covers this process (and SQLite, which has no row locks); the lock on the user's row
    covers several backend processes on Postgres and is held until the transaction ends.
    """
    lock = _upload_locks.setdefault(user_id, asyncio.Lock())
    _upload_lock_users[user_id] += 1
    try:
        async with lock:
            # FOR NO KEY UPDATE: doesn't block inserts that reference the user (e.g. their chat usage).
            await db.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
            yield
    finally:
        _upload_lock_users[user_id] -= 1
        if not _upload_lock_users[user_id]:
            del _upload_lock_users[user_id], _upload_locks[user_id]


async def owned_totals(db: AsyncSession, user_id: int) -> tuple[int, int]:
    """(document count, total bytes) of the documents the user owns."""
    row = (
        await db.execute(
            select(func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0)).where(
                Document.owner_id == user_id
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def remove_document_vectors(db: AsyncSession, doc: Document) -> None:
    """Remove a document's vectors from its own and the active collection.

    Raises 503 when they can't be removed. Call it before deleting anything else, so the delete can be retried.
    """
    collections: set[str] = set()
    if doc.embedding_model_id is not None:
        collections.add(vectorstore.collection_name(doc.embedding_model_id))
    active = await get_active_embedding_model(db)
    if active is not None:
        collections.add(vectorstore.collection_name(active.id))
    for name in collections:
        try:
            await vectorstore.delete_document_points(name, doc.id)
        except Exception as exc:
            logger.exception("Could not delete vectors of document %s from %s", doc.id, name)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The vector database is unavailable. Try again shortly.",
            ) from exc


async def lock_documents(db: AsyncSession, *conditions: Any) -> list[Document]:
    """Lock (and refresh) documents before removing their vectors: a worker finishing one meanwhile then waits
    for the delete, finds its row gone and drops the vectors it wrote, instead of marking it ready in between."""
    return list(
        await db.scalars(
            select(Document).where(*conditions).with_for_update().execution_options(populate_existing=True)
        )
    )


async def delete_document_completely(db: AsyncSession, doc: Document) -> None:
    """Remove a document's vectors, row and file. The file goes after the commit, so a row never loses its file."""
    if not await lock_documents(db, Document.id == doc.id):  # deleted meanwhile
        return
    await remove_document_vectors(db, doc)
    await db.delete(doc)
    await db.commit()
    remove_file(doc.stored_name)
    await forget_ingest_attempts([doc.id])
