"""Uploaded-file storage and whole-document lifecycle helpers."""

import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Document, User
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
    doc: Document, *, viewer: User, active_embedding_model_id: int | None, admin_view: bool = False
) -> DocumentItem:
    """Build the DocumentItem response. `doc.owner` must be loaded (it is selectin by default)."""
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
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


async def delete_document_completely(db: AsyncSession, doc: Document, *, commit: bool = True) -> None:
    """Remove a document's vectors (old and active collections), its file and its row."""
    collections: set[str] = set()
    if doc.embedding_model_id is not None:
        collections.add(vectorstore.collection_name(doc.embedding_model_id))
    active = await get_active_embedding_model(db)
    if active is not None:
        collections.add(vectorstore.collection_name(active.id))
    for name in collections:
        try:
            await vectorstore.delete_document_points(name, doc.id)
        except Exception:
            logger.exception("Could not delete vectors of document %s from %s", doc.id, name)
    remove_file(doc.stored_name)
    await db.delete(doc)
    if commit:
        await db.commit()
