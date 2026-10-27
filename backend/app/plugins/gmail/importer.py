"""Importing Gmail threads and attachments into the user's private documents (docs/briefs/plugins-gmail.md, section
11), from the Gmail browser and from chat. One import per user at a time; items run one by one, and none starts after
the deadline or a Stop (no item is cut off midway: store_import is shielded)."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, User
from app.plugins import audit, importing, registry
from app.plugins.base import (
    AccountChanged,
    ConnectionInfo,
    NotConnected,
    Origin,
    PluginDisabled,
    PluginError,
    ReconnectNeeded,
)
from app.plugins.gmail.client import GmailClient, GmailNotFound, check_id
from app.plugins.gmail.mime import ParsedThread, find_part, large_body_parts, list_attachments, parse_message
from app.plugins.gmail.render import render_thread
from app.plugins.gmail.schemas import GmailImportItem
from app.schemas import DocumentItem
from app.services import storage
from app.services.billing.entitlements import Entitlements
from app.services.model_registry import get_active_embedding_model

logger = logging.getLogger(__name__)

PLUGIN_ID = "gmail"
IMPORT_SECONDS = 90
THREAD_MESSAGES = 100  # the newest messages of a thread an import keeps
LARGE_BODY_BYTES = 25 * 1024 * 1024  # a body part held as an attachment (Gmail's message limit)
LATE = "Not imported in time. Import it again."
STOPPED = "Stopped before this item"
_SERVER_ERROR = "It couldn't be imported because of a server error."
_FATAL = (NotConnected, ReconnectNeeded, AccountChanged, PluginDisabled)  # every later item would fail the same way
_MIB = 1024 * 1024

_locks: dict[int, asyncio.Lock] = {}


def user_lock(user_id: int) -> asyncio.Lock:
    """One import per user at a time, from the Gmail browser and chat alike."""
    return _locks.setdefault(user_id, asyncio.Lock())


@dataclass(frozen=True)
class ImportSelection:
    thread_id: str
    include_thread: bool
    attachments: list[tuple[str, str]] = field(default_factory=list)  # (message id, part id)


@dataclass(frozen=True)
class _Unit:
    kind: Literal["thread", "attachment"]
    thread_id: str
    message_id: str | None = None
    part_id: str | None = None
    label: str | None = None  # known before the run (chat: from the thread already read)


class _Run:
    def __init__(
        self,
        connection: ConnectionInfo,
        user: User,
        ent: Entitlements,
        *,
        deadline: float,
        stopping: asyncio.Event | None,
    ) -> None:
        plugin = registry.get_plugin(PLUGIN_ID)
        if plugin is None:
            raise PluginDisabled("Gmail is turned off.")
        self.plugin = plugin
        self.connection = connection
        self.user = user
        self.ent = ent
        self.client = GmailClient(connection)
        self.deadline = deadline
        self.stopping = stopping
        self.limit = ent.limits.max_upload_mb * _MIB
        self.threads: dict[str, tuple[list[dict[str, Any]], int]] = {}
        self.messages: dict[str, dict[str, Any]] = {}
        self.subjects: dict[str, str] = {}
        self.fatal: str | None = None

    def halted(self) -> str | None:
        if self.stopping is not None and self.stopping.is_set():
            return STOPPED
        if asyncio.get_running_loop().time() >= self.deadline:
            return LATE
        return None

    async def thread(self, thread_id: str) -> tuple[list[dict[str, Any]], int]:
        if thread_id not in self.threads:
            resources, total = await self.client.get_thread_messages(check_id(thread_id), THREAD_MESSAGES)
            if not resources:
                raise GmailNotFound()
            self.threads[thread_id] = resources, total
            for resource in resources:
                self.messages[str(resource.get("id"))] = resource
        return self.threads[thread_id]

    async def run(self, unit: _Unit) -> GmailImportItem:
        if unit.kind == "thread":
            label = unit.label or self.subjects.get(unit.thread_id) or unit.thread_id
        else:
            label = unit.label or str(unit.part_id)
        if self.fatal is not None:
            return _item(unit, label, "failed", self.fatal)
        if (halted := self.halted()) is not None:
            return _item(unit, label, "skipped", halted)
        try:
            if unit.kind == "thread":
                label, outcome = await self._thread(unit.thread_id)
            else:
                label, outcome = await self._attachment(unit, label)
        except _FATAL as exc:
            self.fatal = str(exc)
            return _item(unit, label, "failed", self.fatal)
        except PluginError as exc:
            return _item(unit, label, "failed", str(exc))
        except Exception:
            logger.exception("Importing a Gmail %s for user %s failed", unit.kind, self.user.id)
            return _item(unit, label, "failed", _SERVER_ERROR)
        return _item(unit, label, outcome.status, outcome.reason, outcome.document)

    async def _thread(self, thread_id: str) -> tuple[str, importing.ImportOutcome]:
        resources, total = await self.thread(thread_id)
        messages = []
        for resource in resources:
            large: dict[str, bytes] = {}
            for part_id, attachment_id in large_body_parts(resource):
                large[part_id] = await self.client.get_attachment(
                    str(resource.get("id")), attachment_id, LARGE_BODY_BYTES
                )
            messages.append(await asyncio.to_thread(parse_message, resource, large))
        parsed = ParsedThread(thread_id, messages, total)
        filename, data = await asyncio.to_thread(render_thread, parsed, self.connection.account_email)
        subject = next((message.subject for message in messages if message.subject), "") or "(no subject)"
        self.subjects[thread_id] = subject
        outcome = await importing.store_import(
            self.user.id,
            self.plugin,
            self.connection.account_email,
            importing.ImportFile(filename, data, "email", thread_id),
            self.ent,
        )
        return subject, outcome

    async def _attachment(self, unit: _Unit, label: str) -> tuple[str, importing.ImportOutcome]:
        assert unit.message_id is not None and unit.part_id is not None
        resource = self.messages.get(unit.message_id)
        if resource is None:
            resource = await self.client.get_message(check_id(unit.message_id), "full")
        if resource.get("threadId") != unit.thread_id:
            return label, importing.ImportOutcome("failed", None, "This attachment isn't in that conversation.")
        info = find_part(resource, unit.part_id)
        if info is None:
            return label, importing.ImportOutcome("failed", None, "That attachment wasn't found in the email.")
        label = info.filename or label
        if not info.importable:
            return label, importing.ImportOutcome("skipped", None, info.reason)
        if not self.ent.is_admin and info.size_bytes > self.limit:
            return label, importing.ImportOutcome(
                "failed", None, f"It's over your limit of {self.limit // _MIB} MB per file."
            )
        if info.data is not None:
            data = info.data
        elif info.attachment_id is not None:
            data = await self.client.get_attachment(unit.message_id, info.attachment_id, self.limit)
        else:
            return label, importing.ImportOutcome("skipped", None, "The file is empty.")
        outcome = await importing.store_import(
            self.user.id,
            self.plugin,
            self.connection.account_email,
            importing.ImportFile(info.filename, data, "attachment", f"{unit.message_id}/{unit.part_id}"),
            self.ent,
        )
        return label, outcome


def _item(
    unit: _Unit, label: str, status: str, reason: str | None, document: DocumentItem | None = None
) -> GmailImportItem:
    return GmailImportItem(
        kind=unit.kind,
        thread_id=unit.thread_id,
        message_id=unit.message_id,
        part_id=unit.part_id,
        label=label,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        document_id=document.id if document is not None else None,
        document=document,
    )


async def _record(user: User, items: Sequence[GmailImportItem], origin: Origin) -> None:
    stored = sum(item.status in ("imported", "already_existed") for item in items)
    failed = any(item.status == "failed" for item in items)
    await audit.record(
        user.id, PLUGIN_ID, "import", outcome="error" if failed and not stored else "ok", origin=origin, items=stored
    )


async def import_selection(
    connection: ConnectionInfo,
    user: User,
    items: Sequence[ImportSelection],
    ent: Entitlements,
    *,
    origin: Origin,
    deadline: float,
    stopping: asyncio.Event | None = None,
) -> list[GmailImportItem]:
    """The Gmail browser's import, in request order (each thread, then its attachments). The caller holds
    user_lock(user.id); `deadline` is a loop.time() value; `ent` sets the limits."""
    run = _Run(connection, user, ent, deadline=deadline, stopping=stopping)
    units = [
        unit
        for selection in items
        for unit in (
            *([_Unit("thread", selection.thread_id)] if selection.include_thread else []),
            *(_Unit("attachment", selection.thread_id, mid, pid) for mid, pid in selection.attachments),
        )
    ]
    results = [await run.run(unit) for unit in units]
    await _record(user, results, origin)
    return results


async def chat_import(
    connection: ConnectionInfo,
    user: User,
    thread_ids: Sequence[str],
    include_attachments: bool,
    ent: Entitlements,
    *,
    max_items: int,
    deadline: float,
    stopping: asyncio.Event | None = None,
) -> list[GmailImportItem]:
    """A chat import: the threads first, then (include_attachments) every importable attachment, in thread and message
    order; past `max_items` the rest is skipped. The caller holds user_lock(user.id)."""
    run = _Run(connection, user, ent, deadline=deadline, stopping=stopping)
    over = f"Over the import limit of {max_items} items; import the rest from the Gmail browser"
    results = []
    for number, thread_id in enumerate(thread_ids):
        unit = _Unit("thread", thread_id)
        if number >= max_items:
            results.append(_item(unit, thread_id, "skipped", over))
        else:
            results.append(await run.run(unit))
    room = max_items - len(results)
    if include_attachments:
        for thread_id in thread_ids:
            resources, _ = run.threads.get(thread_id, ([], 0))  # read by the thread's import (none when it failed)
            for resource in resources:
                message_id = str(resource.get("id"))
                for info in list_attachments(resource):
                    if not info.importable:
                        continue
                    unit = _Unit("attachment", thread_id, message_id, info.part_id, info.filename)
                    if room <= 0:
                        results.append(_item(unit, info.filename, "skipped", over))
                    else:
                        room -= 1
                        results.append(await run.run(unit))
    await _record(user, results, "chat")
    return results


async def document_items(db: AsyncSession, user: User, document_ids: Sequence[int]) -> dict[int, DocumentItem]:
    """The current state of those documents that still exist and are the user's (import cards)."""
    ids = {document_id for document_id in document_ids if document_id}
    if not ids:
        return {}
    these = select(Document.id).where(Document.id.in_(ids), Document.owner_id == user.id)
    documents = list(await db.scalars(select(Document).where(Document.id.in_(these))))
    if not documents:
        return {}
    active = await get_active_embedding_model(db)
    vision_pages = await storage.vision_page_counts(db, these)
    generated = await storage.generated_ids(db, these)
    images = await storage.generated_images(db, these)
    imported = await storage.imported_sources(db, these)
    return {
        document.id: storage.document_item(
            document,
            viewer=user,
            active_embedding_model_id=active.id if active is not None else None,
            vision_pages=vision_pages.get(document.id, 0),
            generated=document.id in generated,
            image=images.get(document.id),
            imported_from=imported.get(document.id),
        )
        for document in documents
    }
