"""Uploaded-file storage and whole-document lifecycle helpers."""

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Document, User, VisionPage
from app.redis_client import forget_ingest_attempts
from app.schemas import DocumentItem
from app.services import vectorstore
from app.services.model_registry import get_active_embedding_model

logger = logging.getLogger(__name__)


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


def remove_file(stored_name: str) -> None:
    try:
        stored_path(stored_name).unlink(missing_ok=True)
    except Exception:
        logger.exception("Could not remove uploaded file %s", stored_name)


def document_item(
    doc: Document,
    *,
    viewer: User,
    active_embedding_model_id: int | None,
    admin_view: bool = False,
    vision_pages: int = 0,
) -> DocumentItem:
    """Build the DocumentItem response. `doc.owner` must be loaded (it is selectin by default).
    `vision_pages`: the document's vision_pages rows (see vision_page_counts)."""
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
