"""The caller's data export, POST /me/export (docs/briefs/ui-shell-settings.md, section 4.6): a ZIP of their own
account, their plans and payments (billing.json), the metadata of the documents they own and their conversations (a
Markdown transcript and a JSON file each), newest first within EXPORT_MAX_MB of content and EXPORT_TIMEOUT_SECONDS from
the request's start. It is built in a spooled temp file, then streamed with its length. One export per user at a time
(held through the download), at most MAX_BUILDING building in the process. Nothing of the content is logged."""

import asyncio
import contextlib
import json
import re
import tempfile
import time
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import IO, Any, Literal
from weakref import WeakValueDictionary

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from app.config import settings
from app.models import (
    BillingOrder,
    BillingPayment,
    BillingReceipt,
    Conversation,
    Document,
    GeneratedImage,
    Message,
    MessageFile,
    MessagePluginItem,
    Plan,
    PlanPeriod,
    PluginConnection,
    User,
    UserBillingProfile,
    utcnow,
)
from app.plugins import registry
from app.services import account, storage, usage
from app.services.billing import entitlements, periods
from app.services.billing.entitlements import Entitlements

FORMAT = "rag-assistant-export"
VERSION = 1
PAGE_SIZE = 50  # conversations read per query
MAX_BUILDING = 2  # exports building at once in this process
SLOT_WAIT_SECONDS = 5.0
CHUNK_BYTES = 64 * 1024
CLIENT_GONE = 499  # nginx's "client closed request": nobody reads the answer
BUSY_USER = "An export is already being prepared. Wait for it to finish."
BUSY_SERVER = "Exports are busy right now. Try again in a minute."
_SPOOL_BYTES = 16 * 1024 * 1024  # in memory up to this, then a temp file

clock: Callable[[], float] = time.monotonic  # the deadline's clock (tests replace it)

Truncated = Literal["size", "time"]

_user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
_building = asyncio.Semaphore(MAX_BUILDING)


class ClientGone(Exception):
    """The client disconnected while the export was being built."""


def user_lock(user_id: int) -> asyncio.Lock:
    """The user's export lock (it lives while anyone holds a reference to it)."""
    found = _user_locks.get(user_id)
    if found is None:
        found = _user_locks[user_id] = asyncio.Lock()
    return found


# ---------------- The response ----------------


class ExportResponse(StreamingResponse):
    """The built archive, streamed from its temp file. It owns the file and the user's export lock and releases both
    however the response ends: sent, failed, cancelled, or never started (the body generator's own `finally` wouldn't
    run then)."""

    def __init__(self, archive: IO[bytes], size: int, release: Callable[[], None], headers: Mapping[str, str]) -> None:
        self._archive = archive
        self._release = release
        super().__init__(self._chunks(), media_type="application/zip", headers={**headers, "Content-Length": str(size)})

    async def _chunks(self) -> AsyncIterator[bytes]:
        while chunk := await asyncio.to_thread(self._archive.read, CHUNK_BYTES):
            yield chunk

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                self._archive.close()
            finally:
                self._release()


async def export_response(request: Request, db: AsyncSession, user: User, ent: Entitlements) -> Response:
    """Build the user's export and return the response that streams it (409: one of theirs is being built or
    downloaded; 503: MAX_BUILDING are building)."""
    started = clock()
    lock = user_lock(user.id)
    if lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=BUSY_USER)
    await lock.acquire()
    handed_over = False
    try:
        try:
            async with asyncio.timeout(SLOT_WAIT_SECONDS):
                await _building.acquire()
        except TimeoutError:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=BUSY_SERVER) from None
        try:
            built = await _build(request, db, user, ent, started)
        except ClientGone:
            return Response(status_code=CLIENT_GONE)
        finally:
            _building.release()
        response = ExportResponse(built.archive, built.size, lock.release, built.headers(user))
        handed_over = True
        return response
    finally:
        if not handed_over:
            lock.release()


# ---------------- Building ----------------


class _ArchiveWriter:
    """A ZIP in a spooled temp file; entries are compressed in a worker thread. `written` counts uncompressed bytes."""

    def __init__(self, exported_at: datetime) -> None:
        self.file: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)
        self.zip = zipfile.ZipFile(self.file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
        self.written = 0
        self._date_time = exported_at.timetuple()[:6]
        self._pending: asyncio.Future[Any] | None = None

    async def _run(self, func: Callable[..., Any], *args: Any) -> None:
        # Shielded: a cancelled build waits for nothing, but discard() knows the thread may still use the file.
        self._pending = asyncio.get_running_loop().run_in_executor(None, func, *args)
        await asyncio.shield(self._pending)

    async def add(self, name: str, data: bytes) -> None:
        entry = zipfile.ZipInfo(name, date_time=self._date_time)
        entry.external_attr = 0o644 << 16
        await self._run(self.zip.writestr, entry, data, zipfile.ZIP_DEFLATED, 6)
        self.written += len(data)

    async def finish(self) -> int:
        """Write the central directory; the file is then at its start. Returns the archive's size."""
        await self._run(self.zip.close)
        size = self.file.seek(0, 2)
        self.file.seek(0)
        return size

    def discard(self) -> None:
        """Close and drop the archive, once a write still running in its thread is done."""
        if self._pending is not None and not self._pending.done():
            self._pending.add_done_callback(lambda _: self._close())
        else:
            self._close()

    def _close(self) -> None:
        with contextlib.suppress(Exception):  # discarded anyway; closing keeps ZipFile.__del__ quiet
            self.zip.close()
        self.file.close()


@dataclass
class _Built:
    archive: IO[bytes]
    size: int
    exported_at: datetime
    included: int
    omitted: int

    def headers(self, user: User) -> dict[str, str]:
        filename = f"rag-assistant-export-{user.username}-{self.exported_at:%Y-%m-%d}.zip"  # usernames: [a-z0-9_.-]
        return {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Export-Conversations": str(self.included),
            "X-Export-Omitted": str(self.omitted),
        }


async def _build(request: Request, db: AsyncSession, user: User, ent: Entitlements, started: float) -> _Built:
    deadline = started + settings.export_timeout_seconds
    max_bytes = settings.export_max_mb * 1024 * 1024
    exported_at = utcnow()
    writer = _ArchiveWriter(exported_at)
    try:
        profile = await _account(db, user, ent, exported_at)
        await writer.add("billing.json", _json(await _billing(db, user)))
        documents = await _documents(db, user)
        await writer.add("documents.json", _json(documents))
        ids = list(
            await db.scalars(
                select(Conversation.id)
                .where(Conversation.user_id == user.id)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            )
        )
        await db.commit()  # no transaction stays open while writing (or, with nothing more to read, downloading)
        index: list[dict[str, Any]] = []
        truncated: Truncated | None = None
        position = 0
        while position < len(ids) and truncated is None:
            if await request.is_disconnected():
                raise ClientGone
            page = ids[position : position + PAGE_SIZE]
            records = await _conversation_page(db, user, page)
            await db.commit()
            for conversation_id in page:
                record = records.get(conversation_id)
                if record is None:  # deleted meanwhile
                    position += 1
                    continue
                if clock() >= deadline:
                    truncated = "time"
                    break
                markdown, data = await asyncio.to_thread(_render, record)
                if writer.written + len(markdown) + len(data) > max_bytes:
                    truncated = "size"
                    break
                base = f"conversations/{record['id']:06d}-{_slug(record['title'])}"
                await writer.add(f"{base}.md", markdown)
                await writer.add(f"{base}.json", data)
                index.append(
                    {
                        "id": record["id"],
                        "title": record["title"],
                        "created_at": record["created_at"],
                        "updated_at": record["updated_at"],
                        "messages": len(record["messages"]),
                        "markdown": f"{base}.md",
                        "json": f"{base}.json",
                    }
                )
                position += 1
        omitted = len(ids) - position
        profile["conversations"] = {"included": len(index), "omitted": omitted, "truncated": truncated}
        await writer.add("conversations/index.json", _json(index))
        await writer.add("account.json", _json(profile))
        await writer.add("README.txt", _readme(user, exported_at, len(documents), len(index), omitted, truncated))
        if await request.is_disconnected():
            raise ClientGone
        size = await writer.finish()
    except BaseException:
        writer.discard()
        raise
    return _Built(writer.file, size, exported_at, len(index), omitted)


# ---------------- Content ----------------


def _time(value: datetime | None) -> str | None:
    """UTC ISO 8601, as the API's JSON writes it."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value is not None else None


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8", errors="replace")


async def _account(db: AsyncSession, user: User, ent: Entitlements, exported_at: datetime) -> dict[str, Any]:
    connections = await db.scalars(
        select(PluginConnection).where(PluginConnection.user_id == user.id).order_by(PluginConnection.plugin_id)
    )
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": _time(exported_at),
        "username": user.username,
        "role": "admin" if user.is_admin else "member",
        "created_at": _time(user.created_at),
        "last_login_at": _time(user.last_login_at),
        "preferences": (await account.read_preferences(db, user, ent)).model_dump(mode="json"),
        "limits": entitlements.user_limits(ent).model_dump(mode="json"),
        # The allowance window's (the key predates plans)
        "usage_this_month": (await usage.usage_summary(db, user, ent)).model_dump(mode="json"),
        "plugin_connections": [
            {
                "plugin_id": row.plugin_id,
                "plugin_name": registry.plugin_name(row.plugin_id),
                "account_email": row.account_email,
                "status": row.status,
                "chat_enabled": row.chat_enabled,
                "connected_at": _time(row.connected_at),
            }
            for row in connections
        ],
        "recent_logins": [
            event.model_dump(mode="json", exclude={"current"}) for event in await account.recent_logins(db, user, None)
        ],
    }


async def _billing(db: AsyncSession, user: User) -> dict[str, Any]:
    """The user's periods (every mode), open-ended plan, orders and payments with their receipt numbers, and billing
    profile: what they hold, never the admin's notes or Razorpay's fees."""
    comp = await db.get(Plan, user.comp_plan_id) if user.comp_plan_id is not None else None
    rows = await db.scalars(
        select(PlanPeriod).where(PlanPeriod.user_id == user.id).order_by(PlanPeriod.starts_at, PlanPeriod.id)
    )
    orders = await db.scalars(
        select(BillingOrder).where(BillingOrder.user_id == user.id).order_by(BillingOrder.created_at, BillingOrder.id)
    )
    payments = list(
        await db.scalars(
            select(BillingPayment)
            .where(BillingPayment.user_id == user.id)
            .order_by(BillingPayment.created_at, BillingPayment.id)
        )
    )
    receipts = {
        payment_id: number
        for payment_id, number in await db.execute(
            select(BillingReceipt.payment_id, BillingReceipt.number).where(
                BillingReceipt.payment_id.in_([payment.id for payment in payments])
            )
        )
    }
    profile = await db.get(UserBillingProfile, user.id)
    return {
        "periods": [
            {
                "plan": periods.snapshot(row).name,
                "starts_at": _time(row.starts_at),
                "ends_at": _time(row.ends_at),
                "valid_through": periods.valid_through(row.ends_at).isoformat(),
                "source": row.source,
                "mode": row.mode,
                "reason": row.reason,
                "revoked_at": _time(row.revoked_at),
                "revoked_reason": row.revoked_reason,
            }
            for row in rows
        ],
        "complimentary": {
            "plan": comp.name,
            "reason": user.comp_reason,
            "granted_at": _time(user.comp_granted_at),
        }
        if comp is not None
        else None,
        "orders": [
            {
                "id": order.public_id,
                "kind": order.kind,
                "plan": order.plan_snapshot.get("name"),
                "amount_paise": order.amount_paise,
                "credit_paise": order.credit_paise,
                "status": order.status,
                "mode": order.mode,
                "created_at": _time(order.created_at),
                "paid_at": _time(order.paid_at),
            }
            for order in orders
        ],
        "payments": [
            {
                "amount_paise": payment.amount_paise,
                "currency": payment.currency,
                "method": payment.method,
                "status": payment.status,
                "refunded_paise": payment.refunded_paise,
                "mode": payment.mode,
                "paid_at": _time(payment.captured_at or payment.created_at),
                "receipt": receipts.get(payment.id),
            }
            for payment in payments
        ],
        "profile": {"name": profile.name, "email": profile.email} if profile is not None else None,
    }


async def _documents(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """The documents the user owns (the shared ones they uploaded too; never anyone else's), newest first; no file
    bytes."""
    owned = select(Document.id).where(Document.owner_id == user.id)
    generated = await storage.generated_ids(db, owned)
    imported = await storage.imported_sources(db, owned)
    images = {
        row.document_id: row
        for row in await db.scalars(select(GeneratedImage).where(GeneratedImage.document_id.in_(owned)))
    }
    documents = await db.scalars(
        select(Document).where(Document.owner_id == user.id).order_by(Document.created_at.desc(), Document.id.desc())
    )
    found = []
    for doc in documents:
        image = images.get(doc.id)
        found.append(
            {
                "id": doc.id,
                "filename": doc.filename,
                "scope": doc.scope,
                "status": doc.status,
                "error": doc.error,
                "size_bytes": doc.size_bytes,
                "content_type": doc.content_type,
                "num_pages": doc.num_pages,
                "num_chunks": doc.num_chunks,
                "created_at": _time(doc.created_at),
                "updated_at": _time(doc.updated_at),
                "generated": doc.id in generated,
                "imported_from": imported.get(doc.id),
                "image": {
                    "width": image.width,
                    "height": image.height,
                    "alt_text": image.alt_text,
                    "quality": image.quality,
                    "prompt": image.prompt,
                    "revised_prompt": image.revised_prompt,
                    "model_name": image.model_name,
                }
                if image is not None
                else None,
            }
        )
    return found


async def _conversation_page(db: AsyncSession, user: User, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """conversation id -> its export record (plain data), for those of `ids` that are still the user's."""
    conversations = list(
        await db.scalars(select(Conversation).where(Conversation.id.in_(ids), Conversation.user_id == user.id))
    )
    owned = [conversation.id for conversation in conversations]
    messages: defaultdict[int, list[Message]] = defaultdict(list)
    for message in await db.scalars(select(Message).where(Message.conversation_id.in_(owned)).order_by(Message.id)):
        messages[message.conversation_id].append(message)
    answers = [message.id for found in messages.values() for message in found if message.role == "assistant"]
    files: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    cards: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    if answers:
        for row in await db.scalars(
            select(MessageFile).where(MessageFile.message_id.in_(answers)).order_by(MessageFile.id)
        ):
            files[row.message_id].append(
                {
                    "document_id": row.document_id,
                    "filename": row.filename,
                    "format": row.format,
                    "size_bytes": row.size_bytes,
                }
            )
        items = list(
            await db.scalars(
                select(MessagePluginItem)
                .where(MessagePluginItem.message_id.in_(answers))
                .order_by(MessagePluginItem.id)
            )
        )
        # As GET /conversations/{id} shows them; a card without a live state (its plugin isn't registered any more,
        # its hydration failed or left it out) is exported as stored.
        hydrated = await registry.hydrate_cards(db, user, items)
        for item in items:
            card = hydrated.get(item.id)
            cards[item.message_id].append(card.model_dump(mode="json") if card is not None else _stored_card(item))
    return {
        conversation.id: {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": _time(conversation.created_at),
            "updated_at": _time(conversation.updated_at),
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "created_at": _time(message.created_at),
                    "model_name": message.model_name,
                    "prompt_tokens": message.prompt_tokens,
                    "completion_tokens": message.completion_tokens,
                    "error": message.error,
                    "sources": message.sources or [],
                    "files": files.get(message.id, []),
                    "plugin_cards": cards.get(message.id, []),
                }
                for message in messages.get(conversation.id, [])
            ],
        }
        for conversation in conversations
    }


def _stored_card(item: MessagePluginItem) -> dict[str, Any]:
    data = item.data if isinstance(item.data, dict) else {}
    return {"plugin_id": item.plugin_id, "type": item.card_type, **data, "card_id": f"p{item.id}"}


def _slug(title: str) -> str:
    """ASCII letters, digits and dashes, at most 40 characters ("conversation" when nothing is left)."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:40].strip("-") or "conversation"


def _line(value: object) -> str:
    """One line: newlines and runs of whitespace collapsed."""
    return " ".join(str(value).split())


def _size(size: object) -> str:
    amount = size if isinstance(size, int) else 0
    if amount < 1024:
        return f"{amount} bytes"
    if amount < 1024 * 1024:
        return f"{amount / 1024:.1f} KB"
    return f"{amount / 1024 / 1024:.1f} MB"


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _when(value: object) -> str:
    """An ISO time as UTC to the second ("2026-10-18T09:30:00Z"), else the value as it is."""
    parsed = _parse_time(value)
    if parsed is None:
        return _line(value)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _addresses(*groups: object) -> str:
    found = [
        _line(entry.get("address", ""))
        for group in groups
        if isinstance(group, list)
        for entry in group
        if isinstance(entry, dict) and entry.get("address")
    ]
    return ", ".join(found) or "nobody"


def _card_line(card: Mapping[str, Any]) -> str:
    """One transcript line for a plugin card (the union is in app.schemas_plugins), by its type."""
    kind = card.get("type")
    if kind == "gmail_send":
        pending = card.get("pending")
        if isinstance(pending, dict):
            line = (
                f"Email {_line(pending.get('status'))}: to {_addresses(pending.get('to'), pending.get('cc'))}"
                f' · "{_line(pending.get("subject") or "")}"'
            )
            if pending.get("status") == "sent" and pending.get("sent_at"):
                line += f" · sent {_when(pending['sent_at'])}"
            return line
        line = f"Email proposal {_line(card.get('status'))}"
        return f"{line}: {_line(card['error'])}" if card.get("error") else line
    if kind == "gmail_draft":
        subject = _line(card.get("subject") or "")
        return f'Gmail draft {_line(card.get("status"))}: to {_addresses(card.get("to"))} · "{subject}"'
    if kind == "gmail_import":
        items = [entry for entry in card.get("items") or [] if isinstance(entry, dict)]
        counts = {
            status: sum(1 for entry in items if entry.get("status") == status)
            for status in ("imported", "already_existed", "failed")
        }
        return (
            f"Imported from Gmail: {len(items)} items ({counts['imported']} imported, "
            f"{counts['already_existed']} already there, {counts['failed']} failed)"
        )
    if kind == "gmail_activity":
        line = f"Gmail {_line(str(card.get('action') or '').replace('_', ' '))}"
        if card.get("query"):
            line += f' for "{_line(card["query"])}"'
        if card.get("count") is not None:
            line += f" · {_line(card['count'])} found"
        return line
    return f"{_line(card.get('plugin_id') or 'plugin')} card ({_line(kind)})"


def _source_line(position: int, source: object) -> str:
    if not isinstance(source, dict):
        return f"{position}. (unknown source)"
    number = source.get("index") if isinstance(source.get("index"), int) else position
    if source.get("kind") == "email":
        date = _parse_time(source.get("date")) if source.get("date") else None
        when = f" ({date.astimezone(timezone.utc).date().isoformat()})" if date is not None else ""
        return f'{number}. Email: "{_line(source.get("subject") or "")}" from {_line(source.get("sender") or "")}{when}'
    page = f", page {source['page']}" if isinstance(source.get("page"), int) else ""
    return f"{number}. {_line(source.get('filename') or 'document')}{page}"


def _transcript(record: Mapping[str, Any]) -> str:
    lines = [
        f"# {_line(record['title'])}",
        "",
        f"Created {_when(record['created_at'])} · Updated {_when(record['updated_at'])} · Conversation {record['id']}",
        "",
    ]
    for message in record["messages"]:
        when = _when(message["created_at"])
        if message["role"] == "user":
            lines += [f"## You · {when}", ""]
        else:
            model = f" ({_line(message['model_name'])})" if message["model_name"] else ""
            lines += [f"## Assistant{model} · {when}", ""]
        if message["content"]:
            lines += [message["content"], ""]
        if message["sources"]:
            lines += ["Sources:", *(_source_line(n, source) for n, source in enumerate(message["sources"], 1)), ""]
        if message["files"]:
            lines += [
                "Files:",
                *(
                    f"- {_line(file['filename'])} ({_line(file['format'])}, {_size(file['size_bytes'])})"
                    for file in message["files"]
                ),
                "",
            ]
        if message["plugin_cards"]:
            lines += ["Plugins:", *(f"- {_card_line(card)}" for card in message["plugin_cards"]), ""]
        if message["error"]:
            lines += [f"*Error: {_line(message['error'])}*", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _render(record: Mapping[str, Any]) -> tuple[bytes, bytes]:
    """(the Markdown transcript, the JSON file) of one conversation."""
    return _transcript(record).encode("utf-8", errors="replace"), _json(record)


def _readme(
    user: User, exported_at: datetime, documents: int, included: int, omitted: int, truncated: Truncated | None
) -> bytes:
    lines = [
        "RAG Assistant: data export",
        "",
        f"Exported {_time(exported_at)} (UTC) for {user.username}.",
        "",
        "What is inside:",
        "- account.json: your profile, preferences, limits, the usage of your current allowance period, plugin",
        "  connections (without any tokens) and your recent sign-ins.",
        "- billing.json: your plan periods, orders and payments with their receipt numbers.",
        f"- documents.json: the details of the {documents} document(s) you own. The files themselves aren't included:",
        "  download them from the documents panel.",
        "- conversations/index.json: the list of the exported conversations.",
        "- conversations/<id>-<title>.md: each conversation as a readable transcript.",
        "- conversations/<id>-<title>.json: each conversation with its messages, sources, files and plugin cards.",
        "",
        f"Conversations: {included} included, newest first.",
    ]
    if truncated == "size":
        reason = f"the export reached its size limit ({settings.export_max_mb} MB of content)"
        lines.append(f"{omitted} older conversation(s) were left out because {reason}.")
    elif truncated == "time":
        reason = f"building the export reached its time limit ({settings.export_timeout_seconds} seconds)"
        lines.append(f"{omitted} older conversation(s) were left out because {reason}.")
    return ("\n".join(lines) + "\n").encode("utf-8", errors="replace")
