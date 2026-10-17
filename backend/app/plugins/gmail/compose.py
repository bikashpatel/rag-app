"""Composing drafts and proposed emails (docs/briefs/plugins-gmail.md, 10.4, 12 and 13): the email replied to,
attachments from the user's own documents, and the flags the cards show for what the server can't verify (P24)."""

import asyncio
import hashlib
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Document, GeneratedDocument, GeneratedImage, Message, MessageFile
from app.plugins.gmail.client import GmailClient
from app.plugins.gmail.mime import (
    Address,
    EmailInputError,
    OutgoingAttachment,
    address_key,
    content_type_for,
    mentioned_addresses,
    parse_message,
    reply_headers,
    reply_subject,
    view_address,
)
from app.plugins.gmail.schemas import EmailAddress, EmailAttachment, EmailRecipient, EmailReplyInfo
from app.services import storage

MAX_ATTACHMENTS = 5
_MIB = 1024 * 1024


@dataclass(frozen=True)
class ReplyContext:
    """What a reply takes from the email it answers."""

    thread_id: str
    in_reply_to: str | None
    references: str | None
    subject: str  # "Re: " + the original's
    participants: set[str]  # address keys of its From, Reply-To, To and Cc
    info: dict[str, Any]  # the stored reply_to: {message_id, subject, sender: {name, address}, date}


async def reply_context(client: GmailClient, message_id: str) -> ReplyContext:
    resource = await client.get_message(message_id, "metadata")
    original = await asyncio.to_thread(parse_message, resource)
    subject = reply_subject(original.subject)
    in_reply_to, references = reply_headers(original)
    sender = original.sender or Address(None, "")
    info = {
        "message_id": original.message_id,
        "subject": original.subject,
        "sender": {"name": sender.name, "address": sender.address},
        "date": original.date.isoformat() if original.date else None,
    }
    return ReplyContext(original.thread_id, in_reply_to, references, subject, original.participants, info)


def reply_view(info: Mapping[str, Any] | None) -> EmailReplyInfo | None:
    if not info:
        return None
    sender = info.get("sender") or {}
    return EmailReplyInfo(
        message_id=str(info.get("message_id") or ""),
        subject=str(info.get("subject") or ""),
        sender=_address_view(sender.get("name"), str(sender.get("address") or "")),
        date=info.get("date"),
    )


def _address_view(name: str | None, address: str) -> EmailAddress:
    return view_address(Address(name, address))


# ---------------- Attachments ----------------


@dataclass(frozen=True)
class ResolvedAttachment:
    document_id: int
    filename: str
    size_bytes: int
    sha256: str
    content_type: str
    data: bytes

    def meta(self, unrequested: bool) -> dict[str, Any]:
        """As stored in gmail_pending_sends.attachments."""
        return {
            "document_id": self.document_id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "unrequested": unrequested,
        }

    def outgoing(self) -> OutgoingAttachment:
        return OutgoingAttachment(self.filename, self.content_type, self.data)


def name_key(text: str) -> str:
    """NFC, case-folded, whitespace collapsed: how attachment names are matched."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


async def attachments_by_name(user_id: int, names: Sequence[str]) -> list[ResolvedAttachment]:
    """The user's own documents with these names (the newest of equal names), read and hashed. EmailInputError lists
    the names that match none."""
    wanted = list(dict.fromkeys(name_key(name) for name in names if name.strip()))
    if len(wanted) > MAX_ATTACHMENTS:
        raise EmailInputError(f"At most {MAX_ATTACHMENTS} attachments.")
    if not wanted:
        return []
    async with SessionLocal() as db:
        rows = await db.execute(
            select(Document.id, Document.filename)
            .where(Document.owner_id == user_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
        found: dict[str, int] = {}
        for document_id, filename in rows.tuples():
            found.setdefault(name_key(filename), document_id)
    missing = [name for name in names if name.strip() and name_key(name) not in found]
    if missing:
        listed = ", ".join(name[:80] for name in missing)
        raise EmailInputError(
            f"No document of the user has this exact name: {listed}. Use the exact file name of one of their own "
            "documents."
        )
    return await attachments_by_id(user_id, list(dict.fromkeys(found[key] for key in wanted)))


async def attachments_by_id(user_id: int, document_ids: Sequence[int]) -> list[ResolvedAttachment]:
    """The user's own documents, in this order, read and hashed (within GMAIL_MAX_ATTACHMENT_MB in total)."""
    ids = list(dict.fromkeys(document_ids))
    if len(ids) > MAX_ATTACHMENTS:
        raise EmailInputError(f"At most {MAX_ATTACHMENTS} attachments.")
    if not ids:
        return []
    async with SessionLocal() as db:
        documents = {
            document.id: document
            for document in await db.scalars(
                select(Document).where(Document.id.in_(ids), Document.owner_id == user_id)
            )
        }
    if missing := [document_id for document_id in ids if document_id not in documents]:
        raise EmailInputError(f"These attachments aren't among your documents: {', '.join(map(str, missing))}.")
    limit = settings.gmail_max_attachment_mb * _MIB
    if sum(documents[document_id].size_bytes for document_id in ids) > limit:
        raise EmailInputError(f"The attachments are larger than {settings.gmail_max_attachment_mb} MB together.")
    resolved = []
    for document_id in ids:
        document = documents[document_id]
        try:
            data, digest = await asyncio.to_thread(read_hashed, document.stored_name)
        except (OSError, ValueError) as exc:
            raise EmailInputError(f"The document {document.filename[:80]} can't be read.") from exc
        resolved.append(
            ResolvedAttachment(
                document_id, document.filename, len(data), digest, content_type_for(document.filename), data
            )
        )
    if sum(len(item.data) for item in resolved) > limit:
        raise EmailInputError(f"The attachments are larger than {settings.gmail_max_attachment_mb} MB together.")
    return resolved


def read_hashed(stored_name: str) -> tuple[bytes, str]:
    data = storage.stored_path(stored_name).read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def attachment_view(meta: Mapping[str, Any], existing: set[int] | None = None) -> EmailAttachment:
    document_id = meta.get("document_id")
    if existing is not None and document_id not in existing:
        document_id = None
    return EmailAttachment(
        document_id=document_id,
        filename=str(meta.get("filename") or ""),
        size_bytes=int(meta.get("size_bytes") or 0),
        unrequested=bool(meta.get("unrequested")),
    )


# ---------------- What the user asked for (P24) ----------------


@dataclass(frozen=True)
class Requested:
    """What the user's own messages of the conversation name, and what was made in it."""

    mentioned: set[str]  # address keys the user typed
    texts: list[str]  # the user's messages, as name_key
    created: set[int]  # documents the assistant created in this conversation, earlier in the current answer included


async def requested(conversation_id: int, latest: str, current: Iterable[int | None] = ()) -> Requested:
    """`current`: the documents of the files and images the current answer created so far (not yet linked to a saved
    message). Only those the assistant made (generated_documents, generated_images) count, as for earlier answers: a
    file that matched an upload's bytes stays the upload."""
    answer_ids = {document_id for document_id in current if document_id is not None}
    async with SessionLocal() as db:
        texts = list(
            await db.scalars(
                select(Message.content).where(Message.conversation_id == conversation_id, Message.role == "user")
            )
        )
        files = await db.scalars(
            select(MessageFile.document_id)
            .join(Message, Message.id == MessageFile.message_id)
            .join(GeneratedDocument, GeneratedDocument.document_id == MessageFile.document_id)
            .where(Message.conversation_id == conversation_id)
        )
        images = await db.scalars(
            select(GeneratedImage.document_id).where(GeneratedImage.conversation_id == conversation_id)
        )
        made: list[int] = []
        if answer_ids:
            made = list(
                await db.scalars(
                    select(GeneratedDocument.document_id).where(GeneratedDocument.document_id.in_(answer_ids))
                )
            )
        created = {document_id for document_id in [*files, *images, *made] if document_id is not None}
    texts.append(latest)
    return Requested(mentioned_addresses(texts), [name_key(text) for text in texts], created)


def recipients(
    addresses: Iterable[Address], requested: Requested, participants: set[str], account_email: str, *, bcc: bool
) -> list[dict[str, Any]]:
    """As stored: {name, address, unmentioned}. Unmentioned: the user never typed the exact address and, except in
    Bcc, it isn't a participant of the email replied to (the user's own address never is)."""
    own = address_key(account_email)
    stored = []
    for address in addresses:
        key = address_key(address.address)
        known = key in requested.mentioned or key == own or (not bcc and key in participants)
        stored.append({"name": address.name, "address": address.address, "unmentioned": not known})
    return stored


def recipient_view(item: Mapping[str, Any]) -> EmailRecipient:
    view = _address_view(item.get("name"), str(item.get("address") or ""))
    return EmailRecipient(**view.model_dump(), unmentioned=bool(item.get("unmentioned")))


def stored_addresses(items: Iterable[Mapping[str, Any]]) -> list[Address]:
    return [Address(item.get("name"), str(item.get("address") or "")) for item in items]


def unrequested(attachment: ResolvedAttachment, requested: Requested) -> bool:
    """Not named (with or without its extension) in the user's messages and not created in this conversation (earlier
    in the current answer included)."""
    if attachment.document_id in requested.created:
        return False
    name = name_key(attachment.filename)
    stem = name_key(PurePosixPath(attachment.filename).stem)
    return not any(name in text or (len(stem) >= 3 and stem in text) for text in requested.texts)
