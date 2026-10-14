"""The caller's private documents and the shared knowledge base (SPEC section 6, "Documents").

Private documents are visible to their owner only; shared ones to everyone, but only the admin uploads,
deletes or reprocesses them. Anything else is reported as not found.
"""

import asyncio
import hashlib
import logging
import mimetypes
import re
import unicodedata
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from pathlib import PurePosixPath
from typing import Annotated, BinaryIO, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Document, User
from app.redis_client import forget_ingest_attempts
from app.schemas import MAX_INT, DocumentItem
from app.services import storage
from app.services.ingestion import NO_EMBEDDING_MODEL, enqueue_document
from app.services.model_registry import get_active_embedding_model
from app.services.parsing import LEGACY_EXTENSIONS, SUPPORTED_EXTENSIONS
from app.services.usage import user_limits

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_MIB = 1024 * 1024
_MAX_FILENAME_CHARS = 255
# Control, format (e.g. bidi overrides) and surrogate code points never belong in a display name.
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_MIME_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}")

DocumentId = Annotated[int, Path(ge=1, le=MAX_INT)]

_upload_locks: dict[int, asyncio.Lock] = {}
_upload_lock_users: Counter[int] = Counter()


class DocumentConfig(BaseModel):
    supported_extensions: list[str]
    max_upload_mb: int
    max_documents: int
    max_storage_mb: int
    document_count: int
    storage_bytes: int
    can_upload_shared: bool


@router.get("")
async def list_documents(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[DocumentItem]:
    visible = or_((Document.scope == "private") & (Document.owner_id == user.id), Document.scope == "shared")
    result = await db.execute(select(Document).where(visible).order_by(Document.created_at.desc(), Document.id.desc()))
    active_id = await _active_embedding_model_id(db)
    vision_pages = await storage.vision_page_counts(db, select(Document.id).where(visible, Document.status == "ready"))
    return [
        storage.document_item(
            doc, viewer=user, active_embedding_model_id=active_id, vision_pages=vision_pages.get(doc.id, 0)
        )
        for doc in result.scalars()
    ]


@router.get("/config")
async def document_config(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> DocumentConfig:
    limits = user_limits(user)
    document_count, storage_bytes = await _owned_totals(db, user.id)
    return DocumentConfig(
        supported_extensions=sorted(SUPPORTED_EXTENSIONS),
        max_upload_mb=limits["max_upload_mb"],
        max_documents=limits["max_documents"],
        max_storage_mb=limits["max_storage_mb"],
        document_count=document_count,
        storage_bytes=storage_bytes,
        can_upload_shared=user.is_admin,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    scope: Literal["private", "shared"] = Form("private"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentItem:
    if scope == "shared" and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the admin can upload to the shared knowledge base."
        )
    extension = _extension(file.filename)
    _check_extension(extension)

    limits = user_limits(user)
    enforce_quotas = not user.is_admin
    if enforce_quotas and limits["max_documents"]:  # fail fast; checked again before the insert
        _check_document_count(limits, (await _owned_totals(db, user.id))[0])
    max_bytes = limits["max_upload_mb"] * _MIB
    if file.size is not None and file.size > max_bytes:
        raise _too_large(limits["max_upload_mb"])

    filename = _display_filename(file.filename, extension)
    stored_name = storage.new_stored_name(extension)
    try:
        saved = await asyncio.to_thread(_save_upload, file.file, storage.stored_path(stored_name), max_bytes)
        if saved is None:
            raise _too_large(limits["max_upload_mb"])
        size_bytes, sha256 = saved
        if size_bytes == 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The file is empty.")
        # Checked under the lock: the user's uploads running in parallel would all pass checks made before it.
        async with _serialized_uploads(db, user.id):
            duplicate = await db.scalar(
                select(Document.id)
                .where(Document.owner_id == user.id, Document.scope == scope, Document.sha256 == sha256)
                .limit(1)
            )
            if duplicate is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file has already been uploaded.")
            if enforce_quotas and (limits["max_documents"] or limits["max_storage_mb"]):
                document_count, storage_bytes = await _owned_totals(db, user.id)
                _check_document_count(limits, document_count)
                if limits["max_storage_mb"] and storage_bytes + size_bytes > limits["max_storage_mb"] * _MIB:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"This upload would exceed your storage limit of {limits['max_storage_mb']} MB. "
                        "Delete some documents to free up space.",
                    )
            doc = Document(
                owner=user,
                scope=scope,
                filename=filename,
                stored_name=stored_name,
                content_type=_content_type(file.content_type, filename),
                size_bytes=size_bytes,
                sha256=sha256,
                status="pending",
            )
            db.add(doc)
            await db.commit()
    except BaseException:
        storage.remove_file(stored_name)
        raise

    await _enqueue(doc.id)
    return storage.document_item(doc, viewer=user, active_embedding_model_id=await _active_embedding_model_id(db))


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: DocumentId, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    doc = await _get_manageable_document(db, user, doc_id)
    await storage.delete_document_completely(db, doc)


@router.post("/{doc_id}/reprocess")
async def reprocess_document(
    doc_id: DocumentId, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DocumentItem:
    doc = await _get_manageable_document(db, user, doc_id)
    active = await get_active_embedding_model(db)
    if active is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=NO_EMBEDDING_MODEL)
    if doc.status == "pending":
        # Already queued, and that run uses the active model: another queue entry would only be a duplicate run.
        # (One lost from the queue is re-queued by the worker's sweep.)
        return storage.document_item(doc, viewer=user, active_embedding_model_id=active.id)
    doc.status = "pending"
    doc.error = None
    await db.commit()
    await forget_ingest_attempts([doc.id])
    await _enqueue(doc.id)
    return storage.document_item(doc, viewer=user, active_embedding_model_id=active.id)


async def _get_manageable_document(db: AsyncSession, user: User, doc_id: int) -> Document:
    """The owner manages their private documents and the admin the shared ones; anything else is 404."""
    doc = await db.get(Document, doc_id)
    if doc is None or not (
        (doc.scope == "private" and doc.owner_id == user.id) or (doc.scope == "shared" and user.is_admin)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@asynccontextmanager
async def _serialized_uploads(db: AsyncSession, user_id: int) -> AsyncIterator[None]:
    """One upload per user at a time, from the quota and duplicate checks to the commit.

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


def _check_document_count(limits: dict, document_count: int) -> None:
    if limits["max_documents"] and document_count >= limits["max_documents"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You've reached your limit of {limits['max_documents']} documents. "
            "Delete a document to upload another.",
        )


async def _owned_totals(db: AsyncSession, user_id: int) -> tuple[int, int]:
    """(document count, total bytes) of the documents the user owns."""
    row = (
        await db.execute(
            select(func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0)).where(
                Document.owner_id == user_id
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def _active_embedding_model_id(db: AsyncSession) -> int | None:
    model = await get_active_embedding_model(db)
    return model.id if model is not None else None


async def _enqueue(doc_id: int) -> None:
    try:
        await enqueue_document(doc_id)
    except Exception as exc:
        # The row stays pending; the worker re-queues pending documents when it starts.
        logger.warning("Could not queue document %s for ingestion: %s", doc_id, exc)


def _basename(filename: str | None) -> str:
    return (filename or "").replace("\\", "/").rsplit("/", 1)[-1]


def _extension(filename: str | None) -> str:
    return PurePosixPath(_basename(filename)).suffix.lower()


def _check_extension(extension: str) -> None:
    if extension in SUPPORTED_EXTENSIONS:
        return
    if extension in LEGACY_EXTENSIONS:
        detail = (
            f"Legacy {extension} files aren't supported. Save it as {LEGACY_EXTENSIONS[extension]} and upload again."
        )
    else:
        detail = f"This file type isn't supported. Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
    raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=detail)


def _too_large(max_upload_mb: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"The file is too large. The limit is {max_upload_mb} MB per file.",
    )


def _display_filename(filename: str | None, extension: str) -> str:
    """Basename without control/format characters, whitespace collapsed, at most 255 chars keeping the extension."""
    name = unicodedata.normalize("NFC", _basename(filename))
    visible = "".join(ch for ch in name if ch.isspace() or unicodedata.category(ch) not in _HIDDEN_CATEGORIES)
    name = " ".join(visible.split())
    stem, suffix = name[: len(name) - len(extension)], name[len(name) - len(extension) :]
    if not stem or suffix.lower() != extension:
        return f"document{extension}"
    return stem[: _MAX_FILENAME_CHARS - len(suffix)] + suffix


def _content_type(declared: str | None, filename: str) -> str:
    """The client's media type when it is a plausible one, else a guess from the extension."""
    candidate = (declared or "").split(";", 1)[0].strip().lower()
    if _MIME_TYPE.fullmatch(candidate) and candidate != "application/octet-stream":
        return candidate
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _save_upload(source: BinaryIO, destination: FilePath, max_bytes: int) -> tuple[int, str] | None:
    """Copy the upload in 1 MiB chunks while hashing it.

    Returns (size, sha256 hex), or None as soon as more than `max_bytes` were read (the partial file is left
    for the caller to remove).
    """
    source.seek(0)
    digest = hashlib.sha256()
    size = 0
    with destination.open("xb") as target:
        while chunk := source.read(_MIB):
            size += len(chunk)
            if size > max_bytes:
                return None
            digest.update(chunk)
            target.write(chunk)
    return size, digest.hexdigest()
