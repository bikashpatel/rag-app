"""Pending sends (docs/briefs/plugins-gmail.md, P12 and section 13): the assistant only proposes an email; the user's
click on Send sends exactly the stored content, once, before it expires, within the daily cap, from the same Google
account."""

import asyncio
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, time, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Document, GmailPendingSend, User, utcnow
from app.plugins import audit, registry, tasks
from app.plugins.base import ConnectionInfo, PluginError
from app.plugins.gmail import compose
from app.plugins.gmail.client import GmailClient, GmailUncertain
from app.plugins.gmail.links import thread_url
from app.plugins.gmail.mime import (
    Address,
    EmailInputError,
    OutgoingAttachment,
    OutgoingEmail,
    address_key,
    build_mime,
    clean_body,
    content_hash,
    new_subject,
    normalize_recipients,
    parse_addresses,
)
from app.plugins.gmail.schemas import PendingSend, PendingSendRevision

logger = logging.getLogger(__name__)

PLUGIN_ID = "gmail"
STALE_SENDING = timedelta(minutes=5)  # longer than the upload's 120 s deadline plus a token refresh
UNCERTAIN = "Gmail didn't confirm the email; it may have been sent. Check your Sent folder before trying again."
NOT_FOUND = "Email not found"
_EXPIRED = "This email expired. Ask the assistant again or use Edit."
_CHANGED = "This email changed since it was shown. Reload it."
_ACCOUNT_CHANGED = "Your connected Gmail account changed since this email was prepared."
_ATTACHMENT_CHANGED = "An attachment was changed or deleted; use Edit to prepare the email again."
_SEND_FAILED = "Gmail couldn't send this email because of a server error."
_NOT_PENDING = {
    "sending": "This email is being sent.",
    "sent": "This email was already sent.",
    "cancelled": "This email was cancelled.",
    "replaced": "This email was replaced by a newer version.",
    "failed": "Sending this email failed. Use Edit to prepare it again.",
    "expired": _EXPIRED,
}
_REVISABLE = frozenset({"pending", "expired", "failed", "cancelled"})
_SCRUBBED = "This email is no longer available: its conversation was deleted."

_user_locks: dict[int, asyncio.Lock] = {}


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


# ---------------- Reading ----------------


def effective_status(row: GmailPendingSend, now: datetime) -> tuple[str, str | None]:
    """(status, error) as the user sees it: a pending email past its expiry is expired, and one stuck in sending for
    over 5 minutes (a crash between the claim and the result) failed and may have been sent."""
    if row.status == "pending" and row.expires_at <= now:
        return "expired", row.error
    if row.status == "sending" and row.attempted_at is not None and row.attempted_at <= now - STALE_SENDING:
        return "failed", UNCERTAIN
    return row.status, row.error


def _settle(row: GmailPendingSend, now: datetime) -> str:
    """Store what effective_status reports; returns the status."""
    row.status, row.error = effective_status(row, now)
    return row.status


def view(row: GmailPendingSend, now: datetime, existing: set[int] | None = None) -> PendingSend:
    """The card's state. `existing`: the ids of the user's documents that still exist (others show as deleted)."""
    current, error = effective_status(row, now)
    return PendingSend(
        id=row.id,
        status=current,  # type: ignore[arg-type]
        account_email=row.account_email,
        to=[compose.recipient_view(item) for item in row.to_addrs or []],
        cc=[compose.recipient_view(item) for item in row.cc_addrs or []],
        bcc=[compose.recipient_view(item) for item in row.bcc_addrs or []],
        subject=row.subject,
        body=row.body,
        attachments=[compose.attachment_view(item, existing) for item in row.attachments or []],
        reply_to=compose.reply_view(row.reply_to),
        content_hash=row.content_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        sent_at=row.sent_at,
        error=error,
        gmail_url=thread_url(row.gmail_thread_id) if current == "sent" and row.gmail_thread_id else None,
        replaced_by=row.replaced_by_id,
    )


async def existing_documents(db: AsyncSession, user_id: int, rows: Iterable[GmailPendingSend]) -> set[int]:
    ids = {item["document_id"] for row in rows for item in row.attachments or [] if item.get("document_id")}
    if not ids:
        return set()
    return set(await db.scalars(select(Document.id).where(Document.id.in_(ids), Document.owner_id == user_id)))


def row_hash(row: GmailPendingSend) -> str:
    return content_hash(
        account=row.account_email,
        to=compose.stored_addresses(row.to_addrs or []),
        cc=compose.stored_addresses(row.cc_addrs or []),
        bcc=compose.stored_addresses(row.bcc_addrs or []),
        subject=row.subject,
        body=row.body,
        thread_id=row.thread_id,
        in_reply_to=row.in_reply_to,
        references=row.references_header,
        reply_to=row.reply_to,
        attachments=row.attachments or [],
    )


async def owned(db: AsyncSession, user: User, send_id: int) -> GmailPendingSend:
    row = await db.get(GmailPendingSend, send_id, populate_existing=True)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return row


async def get(user: User, send_id: int) -> PendingSend:
    async with SessionLocal() as db:
        row = await owned(db, user, send_id)
        return view(row, utcnow(), await existing_documents(db, user.id, [row]))


async def latest(db: AsyncSession, user_id: int, send_ids: Iterable[int]) -> dict[int, GmailPendingSend]:
    """send id -> the newest row of its chain of edits (replaced_by, at most 20 hops), the user's rows only."""
    loaded: dict[int, GmailPendingSend] = {}
    wanted = set(send_ids)
    for _ in range(21):
        if not (missing := wanted - loaded.keys()):
            break
        rows = await db.scalars(
            select(GmailPendingSend).where(GmailPendingSend.id.in_(missing), GmailPendingSend.user_id == user_id)
        )
        found = {row.id: row for row in rows}
        loaded.update(found)
        wanted = {row.replaced_by_id for row in found.values() if row.replaced_by_id is not None}
        if not wanted:
            break
    result: dict[int, GmailPendingSend] = {}
    for send_id in send_ids:
        row = loaded.get(send_id)
        for _ in range(20):
            if row is None or row.replaced_by_id is None or row.replaced_by_id not in loaded:
                break
            row = loaded[row.replaced_by_id]
        if row is not None:
            result[send_id] = row
    return result


# ---------------- Proposing ----------------


async def propose(
    db: AsyncSession,
    user: User,
    connection: ConnectionInfo,
    email: OutgoingEmail,
    *,
    conversation_id: int | None,
    to: list[dict[str, Any]],
    cc: list[dict[str, Any]],
    bcc: list[dict[str, Any]],
    attachments_meta: list[dict[str, Any]],
    thread_id: str | None,
    reply_to: dict[str, Any] | None,
    origin: str,
) -> GmailPendingSend:
    """Store a proposal (recipients as compose.recipients made them, with their flags) and commit it, audited."""
    now = utcnow()
    row = GmailPendingSend(
        user_id=user.id,
        conversation_id=conversation_id,
        account_email=connection.account_email,
        account_subject=connection.account_subject,
        to_addrs=to,
        cc_addrs=cc,
        bcc_addrs=bcc,
        subject=email.subject,
        body=email.body,
        attachments=attachments_meta,
        thread_id=thread_id,
        in_reply_to=email.in_reply_to,
        references_header=email.references,
        reply_to=reply_to,
        content_hash="",
        status="pending",
        origin=origin,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.gmail_send_confirm_minutes),
    )
    row.content_hash = await asyncio.to_thread(row_hash, row)
    db.add(row)
    audit.add(db, user.id, PLUGIN_ID, "send_proposed", origin="chat" if origin == "chat" else "ui")
    await db.commit()
    return row


# ---------------- Sending ----------------


def _user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


def day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(timezone.utc).date(), time.min, tzinfo=timezone.utc)


async def sent_today(db: AsyncSession, user_id: int, now: datetime) -> int:
    """Emails the user tried to send since 00:00 UTC (scrubbed rows included: the cap counts attempts)."""
    count = await db.scalar(
        select(func.count())
        .select_from(GmailPendingSend)
        .where(GmailPendingSend.user_id == user_id, GmailPendingSend.attempted_at >= day_start(now))
    )
    return int(count or 0)


async def confirm(user: User, send_id: int, digest: str) -> PendingSend:
    """The user's click on Send (brief 13, steps 1 to 7). Raises HTTPException for every refusal before the claim;
    after it, returns the email's final state (sent or failed)."""
    plugin = registry.get_plugin(PLUGIN_ID)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown plugin")
    now = utcnow()
    async with SessionLocal() as db:
        row = await owned(db, user, send_id)
        stored_status = row.status
        current = _settle(row, now)
        if current != stored_status:
            await db.commit()
        if current != "pending":
            raise _conflict(UNCERTAIN if row.error == UNCERTAIN and current == "failed" else _NOT_PENDING[current])
        if digest != row.content_hash:
            raise _conflict(_CHANGED)
        access = await registry.access_for(db, user, plugin)
        if not access.enabled or "send" not in access.enabled_capabilities:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sending email with Gmail is turned off.")
        connection = access.connection
        if connection is None or connection.status != "connected" or "send" not in access.usable:
            raise _conflict("Gmail isn't connected with permission to send. Reconnect it from Connections.")
        if connection.account_subject != row.account_subject:
            raise _conflict(_ACCOUNT_CHANGED)
        attachments = await _attachment_bytes(db, user.id, row.attachments or [])
        await db.commit()
    async with _user_lock(user.id):
        async with SessionLocal() as db:
            # FOR NO KEY UPDATE on the user's row: serializes the count and the claim across processes on Postgres.
            await db.execute(select(User.id).where(User.id == user.id).with_for_update(key_share=True))
            count = await sent_today(db, user.id, now)
            if count >= settings.gmail_daily_send_limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"You've sent {count} emails today, the daily limit. Try again tomorrow (UTC).",
                )
            claimed = await db.execute(
                update(GmailPendingSend)
                .where(
                    GmailPendingSend.id == send_id,
                    GmailPendingSend.user_id == user.id,
                    GmailPendingSend.status == "pending",
                    GmailPendingSend.expires_at > now,
                    GmailPendingSend.content_hash == digest,
                )
                .values(status="sending", attempted_at=now)
            )
            if claimed.rowcount != 1:  # type: ignore[attr-defined]
                await db.rollback()
                raise _conflict(_NOT_PENDING["sending"])
            await db.commit()
    task = tasks.spawn(_deliver(user.id, send_id, connection, attachments), name=f"gmail-send-{send_id}")
    result, error = await asyncio.shield(task)
    if error is not None:
        raise _conflict(error)
    return result


async def _attachment_bytes(
    db: AsyncSession, user_id: int, attachments: Sequence[Mapping[str, Any]]
) -> dict[int, bytes]:
    """document id -> the bytes to attach; 409 when a document is gone, not the user's, or its bytes changed."""
    found: dict[int, bytes] = {}
    for item in attachments:
        document = await db.get(Document, item.get("document_id"))
        if document is None or document.owner_id != user_id:
            raise _conflict(_ATTACHMENT_CHANGED)
        try:
            data, digest = await asyncio.to_thread(compose.read_hashed, document.stored_name)
        except (OSError, ValueError) as exc:
            raise _conflict(_ATTACHMENT_CHANGED) from exc
        if digest != item.get("sha256"):
            raise _conflict(_ATTACHMENT_CHANGED)
        found[document.id] = data
    return found


def _outgoing(row: GmailPendingSend, attachments: Mapping[int, bytes]) -> OutgoingEmail:
    return OutgoingEmail(
        account_email=row.account_email,
        to=compose.stored_addresses(row.to_addrs or []),
        cc=compose.stored_addresses(row.cc_addrs or []),
        bcc=compose.stored_addresses(row.bcc_addrs or []),
        subject=row.subject,
        body=row.body,
        attachments=[
            OutgoingAttachment(item["filename"], item["content_type"], attachments[item["document_id"]])
            for item in row.attachments or []
        ],
        in_reply_to=row.in_reply_to,
        references=row.references_header,
    )


def _build(row: GmailPendingSend, attachments: Mapping[int, bytes], now: datetime) -> tuple[bytes, str]:
    return build_mime(_outgoing(row, attachments), now), row_hash(row)


async def _deliver(
    user_id: int, send_id: int, connection: ConnectionInfo, attachments: Mapping[int, bytes]
) -> tuple[PendingSend, str | None]:
    """Build, send and store the outcome of a claimed email. Runs as a tracked task awaited through a shield, so a
    closed request or a shutdown never interrupts the Gmail call or the status write. Returns (state, a 409 detail
    when the stored content no longer matches its hash)."""
    async with SessionLocal() as db:
        row = await db.get(GmailPendingSend, send_id)
        assert row is not None
        problem: str | None = None
        try:
            raw, digest = await asyncio.to_thread(_build, row, attachments, utcnow())
        except EmailInputError as exc:
            raw, problem = b"", str(exc)
        else:
            if digest != row.content_hash:
                problem = _CHANGED
    outcome: dict[str, Any]
    audited: audit.Outcome
    if problem is not None:
        outcome, audited = {"status": "failed", "error": problem}, "error"
    else:
        try:
            result = await GmailClient(connection).send(raw, row.thread_id)
        except GmailUncertain:
            outcome, audited = {"status": "failed", "error": UNCERTAIN}, "error"
        except PluginError as exc:
            outcome, audited = {"status": "failed", "error": str(exc)}, "error"
        except Exception:
            logger.exception("Sending email %s of user %s failed", send_id, user_id)
            outcome, audited = {"status": "failed", "error": _SEND_FAILED}, "error"
        else:
            message_id, thread_id = result.get("id"), result.get("threadId")
            outcome = {
                "status": "sent",
                "error": None,
                "sent_at": utcnow(),
                "gmail_message_id": message_id if isinstance(message_id, str) else None,
                "gmail_thread_id": thread_id if isinstance(thread_id, str) else None,
            }
            audited = "ok"
    async with SessionLocal() as db:
        await db.execute(update(GmailPendingSend).where(GmailPendingSend.id == send_id).values(**outcome))
        if await db.get(User, user_id) is not None:
            audit.add(db, user_id, PLUGIN_ID, "send", outcome=audited, origin="ui")
        await db.commit()
        row = await db.get(GmailPendingSend, send_id, populate_existing=True)
        assert row is not None
        logger.info("Email %s of user %s: %s", send_id, user_id, row.status)
        return view(row, utcnow(), await existing_documents(db, user_id, [row])), (
            problem if problem == _CHANGED else None
        )


# ---------------- Cancelling and editing ----------------


async def cancel(user: User, send_id: int) -> PendingSend:
    """pending -> cancelled; any other state is returned unchanged (an expired one stored as such)."""
    now = utcnow()
    async with SessionLocal() as db:
        row = await owned(db, user, send_id)
        stored_status = row.status
        if _settle(row, now) == "pending":
            row.status = "cancelled"
            audit.add(db, user.id, PLUGIN_ID, "send_cancelled", origin="ui")
        if row.status != stored_status:
            await db.commit()
        return view(row, now, await existing_documents(db, user.id, [row]))


async def revise(user: User, send_id: int, revision: PendingSendRevision) -> PendingSend:
    """Edit: a new proposal from the user's own input (origin "edit"); the old one becomes replaced if it was pending
    and points to the new one. Only from pending, expired, failed (a stale sending included) or cancelled."""
    plugin = registry.get_plugin(PLUGIN_ID)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown plugin")
    now = utcnow()
    async with SessionLocal() as db:
        row = await owned(db, user, send_id)
        if not row.body:  # scrubbed with its conversation (P27): nothing left to edit, and no conversation to edit in
            raise _conflict(_SCRUBBED)
        current, _ = effective_status(row, now)
        if current not in _REVISABLE:
            raise _conflict(f"{_NOT_PENDING[current]} It can't be edited.")
        access = await registry.access_for(db, user, plugin)
        connection = access.connection
        if connection is None or connection.status != "connected" or "send" not in access.usable:
            raise _conflict("Gmail isn't connected with permission to send. Reconnect it from Connections.")
        await db.commit()
    try:
        to, cc, bcc = normalize_recipients(
            parse_addresses(revision.to), parse_addresses(revision.cc), parse_addresses(revision.bcc)
        )
        body = clean_body(revision.body)
        is_reply = row.reply_to is not None or row.thread_id is not None
        subject = row.subject if is_reply else new_subject(revision.subject)
        attachments = await compose.attachments_by_id(user.id, revision.attachment_document_ids)
    except EmailInputError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    flagged = {
        address_key(item["address"])
        for item in [*(row.to_addrs or []), *(row.cc_addrs or []), *(row.bcc_addrs or [])]
        if item.get("unmentioned")
    }
    flagged_documents = {item.get("document_id") for item in row.attachments or [] if item.get("unrequested")}

    def kept_flags(addresses: list[Address]) -> list[dict[str, Any]]:
        return [
            {"name": item.name, "address": item.address, "unmentioned": address_key(item.address) in flagged}
            for item in addresses
        ]

    email = OutgoingEmail(
        account_email=connection.account_email,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body=body,
        attachments=[],
        in_reply_to=row.in_reply_to,
        references=row.references_header,
    )
    async with SessionLocal() as db:
        new = await propose(
            db,
            user,
            connection,
            email,
            conversation_id=row.conversation_id,
            to=kept_flags(to),
            cc=kept_flags(cc),
            bcc=kept_flags(bcc),
            attachments_meta=[item.meta(item.document_id in flagged_documents) for item in attachments],
            thread_id=row.thread_id,
            reply_to=row.reply_to,
            origin="edit",
        )
        old = await owned(db, user, send_id)
        if _settle(old, now) == "pending":
            old.status = "replaced"
        old.replaced_by_id = new.id
        await db.commit()
        return view(new, utcnow(), await existing_documents(db, user.id, [new]))


# ---------------- Retention (P27) and disconnects ----------------


def _scrub(row: GmailPendingSend) -> None:
    row.subject = ""
    row.body = ""
    row.to_addrs = []
    row.cc_addrs = []
    row.bcc_addrs = []
    row.attachments = []
    row.reply_to = None
    row.in_reply_to = None
    row.references_header = None
    row.conversation_id = None


def _cancel_pending(db: AsyncSession, row: GmailPendingSend, now: datetime) -> None:
    if _settle(row, now) == "pending":
        row.status = "cancelled"
        audit.add(db, row.user_id, PLUGIN_ID, "send_cancelled", origin="system")


async def scrub_conversation(db: AsyncSession, user_id: int, conversation_id: int) -> None:
    """In the transaction deleting the conversation: its pending emails are cancelled and every email of it loses its
    content (id, status, account and times stay, so the daily cap still counts them)."""
    now = utcnow()
    rows = await db.scalars(
        select(GmailPendingSend).where(
            GmailPendingSend.user_id == user_id, GmailPendingSend.conversation_id == conversation_id
        )
    )
    for row in rows:
        _cancel_pending(db, row, now)
        _scrub(row)
    await db.flush()


async def scrub_orphans() -> int:
    """At startup: emails whose conversation went without the hook (SET NULL) are cancelled and scrubbed."""
    now = utcnow()
    async with SessionLocal() as db:
        rows = list(
            await db.scalars(
                select(GmailPendingSend).where(GmailPendingSend.conversation_id.is_(None), GmailPendingSend.body != "")
            )
        )
        for row in rows:
            _cancel_pending(db, row, now)
            _scrub(row)
        await db.commit()
    if rows:
        logger.info("Scrubbed %d proposed email(s) left without a conversation", len(rows))
    return len(rows)


async def cancel_for_account(db: AsyncSession, connection: ConnectionInfo) -> None:
    """In the transaction removing or replacing a connection: the account's pending emails are cancelled."""
    now = utcnow()
    rows = await db.scalars(
        select(GmailPendingSend).where(
            GmailPendingSend.user_id == connection.user_id,
            GmailPendingSend.account_subject == connection.account_subject,
            GmailPendingSend.status == "pending",
        )
    )
    for row in rows:
        _cancel_pending(db, row, now)
    await db.flush()
