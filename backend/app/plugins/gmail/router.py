"""Gmail's own API, mounted at /api/plugins/gmail (docs/briefs/plugins-gmail.md, section 6): the Gmail browser,
imports, and the pending sends' Send, Cancel and Edit. Plugin errors map to their status (PluginError.http_status):
never 401."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.config import settings
from app.deps import get_current_user
from app.models import User
from app.plugins import audit, registry
from app.plugins.base import PluginAccess
from app.plugins.gmail import importer, sends
from app.plugins.gmail.client import GmailClient, GmailNotFound, conversation_messages
from app.plugins.gmail.links import thread_url
from app.plugins.gmail.mime import ParsedMessage, capped, clean_line, format_address, parse_message
from app.plugins.gmail.schemas import (
    GmailAttachmentInfo,
    GmailImportRequest,
    GmailImportResult,
    GmailMessageView,
    GmailThreadDetail,
    GmailThreadPage,
    GmailThreadSearch,
    GmailThreadSummary,
    PendingSend,
    PendingSendConfirm,
    PendingSendRevision,
)
from app.schemas import MAX_INT

PLUGIN_ID = "gmail"
BROWSER_MESSAGES = 50  # the newest messages of a thread the browser shows
BODY_CHARS = 20_000

router = APIRouter(tags=["gmail"])

GmailIdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
SendIdPath = Annotated[int, Path(ge=1, le=MAX_INT)]
Reader = Annotated[tuple[User, PluginAccess], Depends(registry.require(PLUGIN_ID, "read"))]
Sender = Annotated[tuple[User, PluginAccess], Depends(registry.require(PLUGIN_ID, "send"))]


def _client(access: PluginAccess) -> GmailClient:
    assert access.connection is not None  # registry.require checked it
    return GmailClient(access.connection)


@router.post("/threads/search")
async def search_threads(search: GmailThreadSearch, auth: Reader) -> GmailThreadPage:
    """A POST with the query in the body (GmailThreadSearch), so no access log records it."""
    user, access = auth
    client = _client(access)
    query = clean_line(search.q or "") or "in:inbox"
    page = await client.list_threads(query, search.max_results, search.page_token)
    results = await asyncio.gather(
        *(client.get_thread(thread_id, "metadata") for thread_id in page.threads), return_exceptions=True
    )
    threads = []
    for result in results:
        if isinstance(result, GmailNotFound):
            continue
        if isinstance(result, BaseException):
            raise result
        if (summary := await asyncio.to_thread(_summary, result)) is not None:
            threads.append(summary)
    await audit.record(user.id, PLUGIN_ID, "search", origin="ui", items=len(threads))
    return GmailThreadPage(
        threads=threads, next_page_token=page.next_page_token, result_size_estimate=page.result_size_estimate
    )


def _summary(thread: dict[str, Any]) -> GmailThreadSummary | None:
    resources = conversation_messages([item for item in thread.get("messages") or [] if isinstance(item, dict)])
    if not resources:
        return None
    messages = [parse_message(resource) for resource in resources]
    newest = messages[-1]
    thread_id = str(thread.get("id") or newest.thread_id)
    return GmailThreadSummary(
        thread_id=thread_id,
        subject=next((message.subject for message in messages if message.subject), ""),
        sender=newest.sender_text,
        date=newest.date,
        snippet=newest.snippet,
        message_count=len(messages),
        unread=any("UNREAD" in message.labels for message in messages),
        gmail_url=thread_url(thread_id),
    )


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: GmailIdPath, auth: Reader) -> GmailThreadDetail:
    user, access = auth
    resources, total = await _client(access).get_thread_messages(thread_id, BROWSER_MESSAGES)
    if not resources:
        raise GmailNotFound()
    messages = [await asyncio.to_thread(parse_message, resource) for resource in resources]
    await audit.record(user.id, PLUGIN_ID, "read", origin="ui", items=len(messages))
    return GmailThreadDetail(
        thread_id=thread_id,
        subject=next((message.subject for message in messages if message.subject), ""),
        gmail_url=thread_url(thread_id),
        messages=[_message_view(message) for message in messages],
        messages_truncated=total > len(messages),
    )


def _message_view(message: ParsedMessage) -> GmailMessageView:
    body, cut = capped(message.body, BODY_CHARS)
    return GmailMessageView(
        message_id=message.message_id,
        thread_id=message.thread_id,
        sender=message.sender_text,
        to=[format_address(address) for address in message.to],
        cc=[format_address(address) for address in message.cc],
        date=message.date,
        subject=message.subject,
        snippet=message.snippet,
        body_text=body,
        body_truncated=cut or message.truncated,
        attachments=[
            GmailAttachmentInfo(
                part_id=item.part_id,
                filename=item.filename,
                mime_type=item.mime_type,
                size_bytes=item.size_bytes,
                importable=item.importable,
                reason=item.reason,
            )
            for item in message.attachments
        ],
        unread="UNREAD" in message.labels,
    )


@router.post("/import")
async def import_items(request: GmailImportRequest, auth: Reader) -> GmailImportResult:
    user, access = auth
    assert access.connection is not None
    count = sum(int(thread.include_thread) + len(thread.attachments) for thread in request.threads)
    limit = settings.gmail_import_max_items
    if not 1 <= count <= limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Choose 1 to {limit} items to import (the conversation and each attachment count as one).",
        )
    lock = importer.user_lock(user.id)
    if lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An import is already running.")
    selections = [
        importer.ImportSelection(
            thread.thread_id,
            thread.include_thread,
            [(attachment.message_id, attachment.part_id) for attachment in thread.attachments],
        )
        for thread in request.threads
    ]
    async with lock:
        items = await importer.import_selection(
            access.connection,
            user,
            selections,
            origin="ui",
            deadline=asyncio.get_running_loop().time() + importer.IMPORT_SECONDS,
        )
    return GmailImportResult(items=items)


@router.get("/sends/{send_id}")
async def get_send(send_id: SendIdPath, user: User = Depends(get_current_user)) -> PendingSend:
    return await sends.get(user, send_id)


@router.post("/sends/{send_id}/send")
async def send_email(send_id: SendIdPath, body: PendingSendConfirm, auth: Sender) -> PendingSend:
    return await sends.confirm(auth[0], send_id, body.content_hash)


@router.post("/sends/{send_id}/cancel")
async def cancel_send(send_id: SendIdPath, user: User = Depends(get_current_user)) -> PendingSend:
    return await sends.cancel(user, send_id)


@router.post("/sends/{send_id}/revise")
async def revise_send(send_id: SendIdPath, body: PendingSendRevision, auth: Sender) -> PendingSend:
    return await sends.revise(auth[0], send_id, body)
