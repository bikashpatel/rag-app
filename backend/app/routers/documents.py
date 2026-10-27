"""The caller's private documents and the shared knowledge base (SPEC section 6, "Documents").

Private documents are visible to their owner only; shared ones to everyone, but only the admin uploads,
deletes or reprocesses them. Anything else is reported as not found. Without a plan (plans-billing 5.4) a user lists,
downloads and deletes their own documents; everything else answers 402.
"""

import asyncio
import hashlib
import logging
import mimetypes
import os
import re
import unicodedata
from pathlib import Path as FilePath
from pathlib import PurePosixPath
from typing import Annotated, BinaryIO, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Path, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_entitlements, get_current_user, get_entitled_user, get_entitlements
from app.models import Document, GeneratedImage, User
from app.redis_client import forget_ingest_attempts
from app.schemas import MAX_INT, DocumentItem, GeneratedImageDetails
from app.services import storage
from app.services.billing.entitlements import Entitlements, require_access
from app.services.ingestion import NO_EMBEDDING_MODEL, enqueue_document
from app.services.model_registry import get_active_embedding_model
from app.services.parsing import LEGACY_EXTENSIONS, SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_MIB = 1024 * 1024
_MIME_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}")

DocumentId = Annotated[int, Path(ge=1, le=MAX_INT)]

# Downloads are typed by extension, never by the type an upload declared.
_DOWNLOAD_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_ASCII_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9 ._()-]")
_MAX_ASCII_FILENAME_CHARS = 150
_NOT_FOUND = "Document not found"
_FILE_MISSING = "The file is missing from storage."
_IMAGE_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}  # generated_images.format
_MAKE_ROOM = "Delete some documents or upgrade your plan."
# One entity tag of an If-None-Match list (RFC 9110 8.8.3), optionally weak, followed by the list's comma or its end.
_ENTITY_TAG = re.compile(r'[ \t]*(?:W/)?"([!#-~\x80-\xff]*)"[ \t]*(?=,|$)')
ImageRendition = Literal["full", "preview", "thumb"]


class DocumentConfig(BaseModel):
    supported_extensions: list[str]
    max_upload_mb: int
    max_documents: int
    max_storage_mb: int
    document_count: int
    storage_bytes: int
    can_upload_shared: bool


def _visible_to(user: User) -> ColumnElement[bool]:
    """The caller's private documents and every shared one."""
    return or_((Document.scope == "private") & (Document.owner_id == user.id), Document.scope == "shared")


def _listed_for(user: User, ent: Entitlements) -> ColumnElement[bool]:
    """What GET /documents lists: without access, only the caller's own documents."""
    return _visible_to(user) if ent.access else Document.owner_id == user.id


async def _readable(db: AsyncSession, user: User, ent: Entitlements, doc_id: int) -> Document:
    """A document the caller may read: 404 when it isn't visible to them; a shared one needs access (402)."""
    doc = await db.scalar(select(Document).where(Document.id == doc_id, _visible_to(user)))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    if doc.owner_id != user.id:
        require_access(ent)
    return doc


@router.get("")
async def list_documents(
    user: User = Depends(get_current_user),
    ent: Entitlements = Depends(get_entitlements),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentItem]:
    visible = _listed_for(user, ent)
    result = await db.execute(select(Document).where(visible).order_by(Document.created_at.desc(), Document.id.desc()))
    active_id = await _active_embedding_model_id(db)
    vision_pages = await storage.vision_page_counts(db, select(Document.id).where(visible, Document.status == "ready"))
    generated = await storage.generated_ids(db, select(Document.id).where(visible))
    images = await storage.generated_images(db, select(Document.id).where(visible))
    imported = await storage.imported_sources(db, select(Document.id).where(visible))
    return [
        storage.document_item(
            doc,
            viewer=user,
            active_embedding_model_id=active_id,
            vision_pages=vision_pages.get(doc.id, 0),
            generated=doc.id in generated,
            image=images.get(doc.id),
            imported_from=imported.get(doc.id),
        )
        for doc in result.scalars()
    ]


@router.get("/config")
async def document_config(
    request: Request, user: User = Depends(get_entitled_user), db: AsyncSession = Depends(get_db)
) -> DocumentConfig:
    limits = current_entitlements(request).limits
    document_count, storage_bytes = await storage.owned_totals(db, user.id)
    return DocumentConfig(
        supported_extensions=sorted(SUPPORTED_EXTENSIONS),
        max_upload_mb=limits.max_upload_mb,
        max_documents=limits.max_documents,
        max_storage_mb=limits.max_storage_mb,
        document_count=document_count,
        storage_bytes=storage_bytes,
        can_upload_shared=user.is_admin,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    scope: Literal["private", "shared"] = Form("private"),
    user: User = Depends(get_entitled_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentItem:
    """The plan's limits (documents over a lowered limit are kept; nothing more is added until under it)."""
    if scope == "shared" and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the admin can upload to the shared knowledge base."
        )
    extension = storage.extension_of(file.filename)
    _check_extension(extension)

    ent = current_entitlements(request)
    limits = ent.limits
    enforce_quotas = not ent.is_admin
    if enforce_quotas and limits.max_documents:  # fail fast; checked again before the insert
        _check_document_count(limits.max_documents, (await storage.owned_totals(db, user.id))[0])
    max_bytes = limits.max_upload_mb * _MIB
    if file.size is not None and file.size > max_bytes:
        raise _too_large(limits.max_upload_mb)

    filename = storage.display_filename(file.filename, extension)
    stored_name = storage.new_stored_name(extension)
    try:
        saved = await asyncio.to_thread(_save_upload, file.file, storage.stored_path(stored_name), max_bytes)
        if saved is None:
            raise _too_large(limits.max_upload_mb)
        size_bytes, sha256 = saved
        if size_bytes == 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The file is empty.")
        # Checked under the lock: the user's uploads running in parallel would all pass checks made before it.
        async with storage.serialized_uploads(db, user.id):
            duplicate = await db.scalar(
                select(Document.id)
                .where(Document.owner_id == user.id, Document.scope == scope, Document.sha256 == sha256)
                .limit(1)
            )
            if duplicate is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This file has already been uploaded.")
            if enforce_quotas and (limits.max_documents or limits.max_storage_mb):
                document_count, storage_bytes = await storage.owned_totals(db, user.id)
                _check_document_count(limits.max_documents, document_count)
                if limits.max_storage_mb and storage_bytes + size_bytes > limits.max_storage_mb * _MIB:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"This upload would exceed your storage limit of {limits.max_storage_mb} MB. "
                        + _MAKE_ROOM,
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
    doc_id: DocumentId, user: User = Depends(get_entitled_user), db: AsyncSession = Depends(get_db)
) -> DocumentItem:
    """No file-size check: the file was accepted when it was uploaded."""
    doc = await _get_manageable_document(db, user, doc_id)
    active = await get_active_embedding_model(db)
    if active is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=NO_EMBEDDING_MODEL)
    generated = await storage.is_generated(db, doc.id)
    this = select(Document.id).where(Document.id == doc.id)
    image = (await storage.generated_images(db, this)).get(doc.id)
    imported_from = (await storage.imported_sources(db, this)).get(doc.id)
    if doc.status != "pending":
        # When pending, it is already queued, and that run uses the active model: another queue entry would only be a
        # duplicate run. (One lost from the queue is re-queued by the worker's sweep.)
        doc.status = "pending"
        doc.error = None
        await db.commit()
        await forget_ingest_attempts([doc.id])
        await _enqueue(doc.id)
    return storage.document_item(
        doc,
        viewer=user,
        active_embedding_model_id=active.id,
        generated=generated,
        image=image,
        imported_from=imported_from,
    )


@router.get("/{doc_id}/download", response_class=FileResponse)
async def download_document(
    doc_id: DocumentId,
    user: User = Depends(get_current_user),
    ent: Entitlements = Depends(get_entitlements),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    doc = await _readable(db, user, ent, doc_id)
    path = storage.stored_path(doc.stored_name)
    if not await asyncio.to_thread(path.is_file):
        logger.warning("Document %s has no stored file %s", doc.id, doc.stored_name)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_FILE_MISSING)
    extension = PurePosixPath(doc.filename).suffix.lower()
    return FileResponse(
        path,
        media_type=_DOWNLOAD_TYPES.get(extension, "application/octet-stream"),
        headers={
            "Content-Disposition": _disposition(doc.filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get("/{doc_id}/image", response_class=FileResponse)
async def document_image(
    doc_id: DocumentId,
    size: Annotated[ImageRendition, Query()] = "full",
    if_none_match: Annotated[str | None, Header()] = None,
    user: User = Depends(get_current_user),
    ent: Entitlements = Depends(get_entitlements),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """A generated image for display: the stored image, or its WebP preview or thumbnail (the stored image when the
    rendition is missing). Revalidated on every use (no max-age): a browser's cache ignores cookies, so on a shared
    browser one user's cached image must not be shown to the next without a server check."""
    doc, image = await _visible_image(db, user, ent, doc_id)
    served: ImageRendition = size
    path = storage.stored_path(doc.stored_name)
    media_type = _IMAGE_TYPES.get(image.format, "application/octet-stream")
    if size != "full":
        name = storage.preview_name if size == "preview" else storage.thumb_name
        rendition = storage.stored_path(name(doc.stored_name))
        if await asyncio.to_thread(rendition.is_file):
            path, media_type = rendition, "image/webp"
        else:
            served = "full"
    try:
        stat = await asyncio.to_thread(os.stat, path)
    except FileNotFoundError:
        logger.warning("Document %s has no stored file %s", doc.id, doc.stored_name)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_FILE_MISSING) from None
    opaque = f"{doc.sha256[:32]}-{served}-{stat.st_size}"
    headers = {"ETag": f'"{opaque}"', "Cache-Control": "private, no-cache"}
    if if_none_match is not None and _none_match(if_none_match, opaque):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return FileResponse(
        path,
        media_type=media_type,
        stat_result=stat,
        headers={
            **headers,
            "Content-Disposition": _disposition(doc.filename, "inline"),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )


@router.get("/{doc_id}/image/details")
async def document_image_details(
    doc_id: DocumentId,
    request: Request,
    response: Response,
    user: User = Depends(get_entitled_user),
    db: AsyncSession = Depends(get_db),
) -> GeneratedImageDetails:
    """The prompt and model of a generated image (private content: the viewer loads it when it opens)."""
    _, image = await _visible_image(db, user, current_entitlements(request), doc_id)
    response.headers["Cache-Control"] = "private, no-store"
    return GeneratedImageDetails(
        prompt=image.prompt,
        revised_prompt=image.revised_prompt,
        model_name=image.model_name,
        created_at=image.created_at,
    )


async def _visible_image(
    db: AsyncSession, user: User, ent: Entitlements, doc_id: int
) -> tuple[Document, GeneratedImage]:
    """A document the caller may read (_readable) that is a generated image; 404 otherwise."""
    row = (
        await db.execute(
            select(Document, GeneratedImage)
            .outerjoin(GeneratedImage, GeneratedImage.document_id == Document.id)
            .where(Document.id == doc_id, _visible_to(user))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    doc, image = row
    if doc.owner_id != user.id:
        require_access(ent)
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This document isn't an image.")
    return doc, image


def _none_match(header: str, opaque: str) -> bool:
    """If-None-Match (RFC 9110 13.1.2): "*" matches; otherwise a comma-separated list of entity tags, compared weakly
    (W/ ignored, the opaque tags equal). Malformed entries are ignored."""
    if header.strip() == "*":
        return True
    position = 0
    while position < len(header):
        if (found := _ENTITY_TAG.match(header, position)) is not None:
            if found.group(1) == opaque:
                return True
            position = found.end() + 1
        else:
            comma = header.find(",", position)
            position = len(header) if comma < 0 else comma + 1
    return False


async def _get_manageable_document(db: AsyncSession, user: User, doc_id: int) -> Document:
    """The owner manages their private documents and the admin the shared ones; anything else is 404."""
    doc = await db.get(Document, doc_id)
    if doc is None or not (
        (doc.scope == "private" and doc.owner_id == user.id) or (doc.scope == "shared" and user.is_admin)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return doc


async def _active_embedding_model_id(db: AsyncSession) -> int | None:
    model = await get_active_embedding_model(db)
    return model.id if model is not None else None


async def _enqueue(doc_id: int) -> None:
    try:
        await enqueue_document(doc_id)
    except Exception as exc:
        # The row stays pending; the worker re-queues pending documents when it starts.
        logger.warning("Could not queue document %s for ingestion: %s", doc_id, exc)


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


def _check_document_count(max_documents: int, document_count: int) -> None:
    if max_documents and document_count >= max_documents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You've reached your limit of {max_documents} documents. {_MAKE_ROOM}",
        )


def _too_large(max_upload_mb: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"The file is too large. The limit is {max_upload_mb} MB per file.",
    )


def _disposition(filename: str, kind: Literal["attachment", "inline"] = "attachment") -> str:
    """Content-Disposition for a download (or `inline` for display): an ASCII fallback (unsafe characters as "_", at
    most 150 characters keeping the extension) and the exact name in RFC 5987 form."""
    decomposed = unicodedata.normalize("NFKD", filename)
    ascii_name = _ASCII_FILENAME_UNSAFE.sub("_", "".join(ch for ch in decomposed if not unicodedata.combining(ch)))
    if len(ascii_name) > _MAX_ASCII_FILENAME_CHARS:
        suffix = PurePosixPath(ascii_name).suffix[:20]
        ascii_name = ascii_name[: _MAX_ASCII_FILENAME_CHARS - len(suffix)] + suffix
    return f"{kind}; filename=\"{ascii_name or 'download'}\"; filename*=UTF-8''{quote(filename, safe='')}"


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
