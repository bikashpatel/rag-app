"""Importing a file from a plugin's provider into the user's private documents (brief 4.6, P16): supported extensions
only, the upload limits of the user's plan (the admin is exempt), identical bytes linking the existing private
document, and a provenance row ("Imported from Gmail"); then the normal ingestion."""

import asyncio
import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Document, ImportedDocument, User
from app.plugins import tasks
from app.plugins.base import Plugin
from app.schemas import DocumentItem
from app.services import storage
from app.services.billing.entitlements import Entitlements
from app.services.ingestion import enqueue_document
from app.services.model_registry import get_active_embedding_model
from app.services.parsing import LEGACY_EXTENSIONS, SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024
_SERVER_ERROR = "It couldn't be saved because of a server error."


@dataclass(frozen=True)
class ImportFile:
    filename: str  # display name; sanitized by storage.display_filename
    data: bytes
    kind: Literal["email", "attachment"]
    external_ref: str  # at most 255 characters: Gmail's thread id, or "<message id>/<part id>"


@dataclass(frozen=True)
class ImportOutcome:
    status: Literal["imported", "already_existed", "failed", "skipped"]
    document: DocumentItem | None
    reason: str | None  # user-facing (failed, skipped)


class _Refused(Exception):
    """A quota stops the import; str() is the reason."""


async def store_import(
    user_id: int, plugin: Plugin, account_email: str, file: ImportFile, ent: Entitlements
) -> ImportOutcome:
    """Store one file as a private document of the user, within the limits of `ent`. Never raises for user errors;
    unexpected errors are logged and become `failed`. Writing, the locked checks, the commit and the enqueue run in a
    task of their own awaited through a shield, so cancelling the caller leaves no orphan file or half-done commit."""
    extension = storage.extension_of(file.filename)
    if extension not in SUPPORTED_EXTENSIONS:
        if extension in LEGACY_EXTENSIONS:
            reason = f"Legacy {extension} files can't be imported. Save it as {LEGACY_EXTENSIONS[extension]} first."
        elif extension:
            reason = f"{extension} files can't be imported."
        else:
            reason = "Files without an extension can't be imported."
        return ImportOutcome("skipped", None, reason)
    if not file.data:
        return ImportOutcome("skipped", None, "The file is empty.")
    try:
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
        if user is None:
            return ImportOutcome("failed", None, "The account no longer exists.")
        limit = ent.limits.max_upload_mb
        if not ent.is_admin and len(file.data) > limit * _MIB:
            return ImportOutcome(
                "failed", None, f"The file is {len(file.data) / _MIB:.1f} MB, over your limit of {limit} MB per file."
            )
        sha256 = await asyncio.to_thread(_sha256, file.data)
    except Exception:
        logger.exception("Importing a %s file for user %s failed", plugin.id, user_id)
        return ImportOutcome("failed", None, _SERVER_ERROR)
    filename = storage.display_filename(file.filename, extension)
    task = tasks.spawn(
        _store(user_id, plugin, account_email, file, filename, extension, sha256, ent), name=f"{plugin.id} import"
    )
    return await asyncio.shield(task)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _store(
    user_id: int,
    plugin: Plugin,
    account_email: str,
    file: ImportFile,
    filename: str,
    extension: str,
    sha256: str,
    ent: Entitlements,
) -> ImportOutcome:
    stored_name = storage.new_stored_name(extension)
    committed = False
    try:
        await asyncio.to_thread(_write, stored_name, file.data)
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
            if user is None:
                return ImportOutcome("failed", None, "The account no longer exists.")
            async with storage.serialized_uploads(db, user.id):
                duplicate = await db.scalar(
                    select(Document)
                    .where(Document.owner_id == user.id, Document.scope == "private", Document.sha256 == sha256)
                    .order_by(Document.id)
                    .limit(1)
                )
                if duplicate is None:
                    if not ent.is_admin:
                        await _check_quota(db, user, len(file.data), ent)
                    doc = Document(
                        owner=user,
                        scope="private",
                        filename=filename,
                        stored_name=stored_name,
                        content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                        size_bytes=len(file.data),
                        sha256=sha256,
                        status="pending",
                    )
                    db.add(doc)
                    await db.flush()
                    db.add(
                        ImportedDocument(
                            document_id=doc.id,
                            plugin_id=plugin.id,
                            kind=file.kind,
                            external_ref=file.external_ref[:255],
                            account_email=account_email[:320],
                        )
                    )
                    await db.commit()
                    committed = True
                else:
                    doc = duplicate
                    await db.commit()
            if duplicate is not None:
                storage.remove_file(stored_name)
            else:
                try:
                    await enqueue_document(doc.id)
                except Exception as exc:  # the worker's sweep queues pending documents
                    logger.warning("Could not queue document %s for ingestion: %s", doc.id, exc)
            item = await _item(db, user, doc)
            return ImportOutcome("already_existed" if duplicate is not None else "imported", item, None)
    except _Refused as exc:
        if not committed:
            storage.remove_file(stored_name)
        return ImportOutcome("failed", None, str(exc))
    except Exception:
        if not committed:
            storage.remove_file(stored_name)
        logger.exception("Storing a %s import for user %s failed", plugin.id, user_id)
        return ImportOutcome("failed", None, _SERVER_ERROR)
    except BaseException:
        if not committed:
            storage.remove_file(stored_name)
        raise


def _write(stored_name: str, data: bytes) -> None:
    with storage.stored_path(stored_name).open("xb") as target:
        target.write(data)


async def _check_quota(db: AsyncSession, user: User, size: int, ent: Entitlements) -> None:
    limits = ent.limits
    if not (limits.max_documents or limits.max_storage_mb):
        return
    count, used = await storage.owned_totals(db, user.id)
    if limits.max_documents and count >= limits.max_documents:
        raise _Refused(
            f"You've reached your limit of {limits.max_documents} documents. Delete a document to import another."
        )
    if limits.max_storage_mb and used + size > limits.max_storage_mb * _MIB:
        raise _Refused(
            f"This would exceed your storage limit of {limits.max_storage_mb} MB. Delete some documents to free up "
            "space."
        )


async def _item(db: AsyncSession, user: User, doc: Document) -> DocumentItem:
    this = select(Document.id).where(Document.id == doc.id)
    active = await get_active_embedding_model(db)
    vision_pages = await storage.vision_page_counts(db, this)
    return storage.document_item(
        doc,
        viewer=user,
        active_embedding_model_id=active.id if active is not None else None,
        vision_pages=vision_pages.get(doc.id, 0),
        generated=doc.id in await storage.generated_ids(db, this),
        image=(await storage.generated_images(db, this)).get(doc.id),
        imported_from=(await storage.imported_sources(db, this)).get(doc.id),
    )
