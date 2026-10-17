"""The Gmail REST client (docs/briefs/plugins-gmail.md, section 8): plain httpx through plugins.http with the
connection's access token, retries with backoff for reads, and Google's errors mapped to PluginErrors.

Logged per request: method, path without the query, status, Google's reason and latency; never queries, bodies or
tokens.
"""

import asyncio
import json
import logging
import random
import re
import secrets
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, NoReturn
from weakref import WeakKeyDictionary

import httpx

from app.config import settings
from app.plugins import connections, http
from app.plugins.base import ConnectionInfo, PluginError, PluginUnavailable, ReconnectNeeded
from app.plugins.gmail.mime import b64url_decode

logger = logging.getLogger(__name__)

CALL_SECONDS = 45.0
LONG_CALL_SECONDS = 120.0  # attachments and uploads
MAX_RESPONSE_BYTES = 40 * 1024 * 1024
RETRIES = 3
MAX_IN_FLIGHT = 4  # per connection: metadata fan-out never trips "too many concurrent requests"
MAX_DOWNLOADS = 2  # attachment downloads at once in this process
METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID", "References", "Reply-To")
BUSY = "Gmail is busy right now. Try again in a minute."
UNREACHABLE = "Gmail is unreachable right now. Try again shortly."
NOT_FOUND = "That email wasn't found in Gmail."
REFUSED = "Gmail refused this request."
UNCERTAIN = "Gmail didn't answer in time, so it isn't known whether the request went through."

_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)
_LONG_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)
_JSON_IN_THREAD = 256 * 1024  # larger responses are parsed off the event loop
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ATTACHMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,4096}")
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_SCOPE_REASONS = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)  # the request never left

_in_flight: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, asyncio.Semaphore]] = WeakKeyDictionary()
_downloads: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()
_sleep = asyncio.sleep  # tests replace it


def conversation_messages(messages: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The messages of a thread (resources with `labelIds`, oldest first) that are its conversation: never the user's
    unsent drafts, and messages in Trash or Spam only when the whole thread is there (opened on purpose)."""
    sent = [message for message in messages if "DRAFT" not in _labels(message)]
    kept = [message for message in sent if not {"TRASH", "SPAM"} & _labels(message)]
    return kept or sent


def _labels(message: Mapping[str, Any]) -> set[str]:
    labels = message.get("labelIds")
    return {label for label in labels if isinstance(label, str)} if isinstance(labels, list) else set()


class GmailNotFound(PluginError):
    http_status = 404

    def __init__(self, message: str = NOT_FOUND) -> None:
        super().__init__(message)


class GmailUncertain(PluginUnavailable):
    """A draft or send whose request may have reached Gmail, with no answer: it may have been created or sent."""


class AttachmentTooLarge(PluginError):
    http_status = 413


@dataclass(frozen=True)
class MessagePage:
    messages: list[tuple[str, str]]  # (message id, thread id), newest first
    next_page_token: str | None
    result_size_estimate: int


@dataclass(frozen=True)
class ThreadPage:
    threads: list[str]  # thread ids, newest first
    next_page_token: str | None
    result_size_estimate: int


@dataclass
class _Budget:
    deadline: float  # loop.time()
    posted: bool = False  # a non-idempotent request may have reached Gmail


@asynccontextmanager
async def _limited(seconds: float) -> AsyncIterator[_Budget]:
    """A public method's deadline: PluginUnavailable when it passes (GmailUncertain once a POST may have been sent)."""
    budget = _Budget(asyncio.get_running_loop().time() + seconds)
    try:
        async with asyncio.timeout_at(budget.deadline):
            yield budget
    except TimeoutError as exc:
        if budget.posted:
            raise GmailUncertain(UNCERTAIN) from exc
        raise PluginUnavailable(UNREACHABLE) from exc


def _connection_slot(connection_id: int) -> asyncio.Semaphore:
    slots = _in_flight.setdefault(asyncio.get_running_loop(), {})
    return slots.setdefault(connection_id, asyncio.Semaphore(MAX_IN_FLIGHT))


def _download_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _downloads.get(loop)
    if slot is None:
        slot = _downloads[loop] = asyncio.Semaphore(MAX_DOWNLOADS)
    return slot


def check_id(value: str) -> str:
    """A Gmail message or thread id; anything else can't be one (GmailNotFound), and never reaches a URL."""
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise GmailNotFound()
    return value


class GmailClient:
    def __init__(self, connection: ConnectionInfo) -> None:
        self.connection = connection
        self._touched = False

    # ---------------- Public methods ----------------

    async def profile(self) -> dict[str, Any]:
        async with _limited(CALL_SECONDS) as budget:
            return await self._json("GET", "/gmail/v1/users/me/profile", budget=budget)

    async def list_messages(self, q: str, max_results: int, page_token: str | None) -> MessagePage:
        params = _list_params(q, max_results, page_token)
        async with _limited(CALL_SECONDS) as budget:
            data = await self._json("GET", "/gmail/v1/users/me/messages", params=params, budget=budget)
        messages = [
            (item["id"], str(item.get("threadId") or ""))
            for item in data.get("messages") or []
            if isinstance(item, dict) and isinstance(item.get("id"), str) and _ID.fullmatch(item["id"])
        ]
        return MessagePage(messages, _page_token(data), _estimate(data))

    async def list_threads(self, q: str, max_results: int, page_token: str | None) -> ThreadPage:
        params = _list_params(q, max_results, page_token)
        async with _limited(CALL_SECONDS) as budget:
            data = await self._json("GET", "/gmail/v1/users/me/threads", params=params, budget=budget)
        threads = [
            item["id"]
            for item in data.get("threads") or []
            if isinstance(item, dict) and isinstance(item.get("id"), str) and _ID.fullmatch(item["id"])
        ]
        return ThreadPage(threads, _page_token(data), _estimate(data))

    async def get_message(self, message_id: str, fmt: Literal["full", "metadata"]) -> dict[str, Any]:
        path = f"/gmail/v1/users/me/messages/{check_id(message_id)}"
        async with _limited(CALL_SECONDS) as budget:
            return await self._json("GET", path, params=_format_params(fmt), budget=budget)

    async def get_thread(self, thread_id: str, fmt: Literal["minimal", "metadata"]) -> dict[str, Any]:
        """Never `full`: a long mailing-list thread can be tens of MB (use get_thread_messages)."""
        if fmt not in ("minimal", "metadata"):
            raise ValueError(f"Threads are read as minimal or metadata, not {fmt}")
        path = f"/gmail/v1/users/me/threads/{check_id(thread_id)}"
        async with _limited(CALL_SECONDS) as budget:
            return await self._json("GET", path, params=_format_params(fmt), budget=budget)

    async def get_thread_messages(self, thread_id: str, newest: int) -> tuple[list[dict[str, Any]], int]:
        """The newest `newest` messages of the thread's conversation (conversation_messages) in full, oldest first,
        and the conversation's message count: the ids come from `format=minimal`, then each chosen message is read on
        its own (a message deleted meanwhile is left out). No messages when the thread holds only drafts."""
        thread = await self.get_thread(thread_id, "minimal")
        listed = [
            item
            for item in thread.get("messages") or []
            if isinstance(item, dict) and isinstance(item.get("id"), str) and _ID.fullmatch(item["id"])
        ]
        ids = [item["id"] for item in conversation_messages(listed)]
        chosen = ids[-newest:] if newest > 0 else []
        results = await asyncio.gather(*(self.get_message(item, "full") for item in chosen), return_exceptions=True)
        resources: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, GmailNotFound):
                continue
            if isinstance(result, BaseException):
                raise result
            resources.append(result)
        return resources, len(ids)

    async def get_attachment(self, message_id: str, attachment_id: str, max_bytes: int) -> bytes:
        """An attachment's bytes (or a large body part's), refused while reading when over `max_bytes`."""
        if not isinstance(attachment_id, str) or not _ATTACHMENT_ID.fullmatch(attachment_id):
            raise GmailNotFound()
        path = f"/gmail/v1/users/me/messages/{check_id(message_id)}/attachments/{attachment_id}"
        too_large = AttachmentTooLarge(f"The attachment is larger than {_megabytes(max_bytes)}.")
        async with _download_slot(), _limited(LONG_CALL_SECONDS) as budget:
            raw = await self._request(
                "GET", path, budget=budget, timeout=_LONG_TIMEOUT, max_bytes=max_bytes * 4 // 3 + 64 * 1024,
                too_large=too_large,
            )
            data = await asyncio.to_thread(_attachment_bytes, raw)
        if len(data) > max_bytes:
            raise too_large
        return data

    async def create_draft(self, raw: bytes, thread_id: str | None) -> dict[str, Any]:
        """{id, message: {id, threadId}}."""
        metadata: dict[str, Any] = {"message": {"threadId": check_id(thread_id)}} if thread_id else {}
        return await self._upload("/upload/gmail/v1/users/me/drafts", metadata, raw)

    async def send(self, raw: bytes, thread_id: str | None) -> dict[str, Any]:
        """{id, threadId}."""
        metadata: dict[str, Any] = {"threadId": check_id(thread_id)} if thread_id else {}
        return await self._upload("/upload/gmail/v1/users/me/messages/send", metadata, raw)

    # ---------------- Requests ----------------

    async def _upload(self, path: str, metadata: dict[str, Any], raw: bytes) -> dict[str, Any]:
        content, content_type = _related(metadata, raw)
        async with _limited(LONG_CALL_SECONDS) as budget:
            return await self._json(
                "POST", path, params=httpx.QueryParams({"uploadType": "multipart"}), content=content,
                headers={"Content-Type": content_type}, budget=budget, timeout=_LONG_TIMEOUT,
            )

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        raw = await self._request(method, path, **kwargs)
        try:
            data = await asyncio.to_thread(json.loads, raw) if len(raw) > _JSON_IN_THREAD else json.loads(raw)
        except ValueError as exc:
            raise PluginUnavailable(UNREACHABLE) from exc
        if not isinstance(data, dict):
            raise PluginUnavailable(UNREACHABLE)
        return data

    async def _request(
        self,
        method: str,
        path: str,
        *,
        budget: _Budget,
        params: httpx.QueryParams | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout = _TIMEOUT,
        max_bytes: int = MAX_RESPONSE_BYTES,
        too_large: PluginError | None = None,
    ) -> bytes:
        """One Gmail call: the access token (refreshed once on a 401), retries of GETs with backoff, and the response
        body (at most `max_bytes`)."""
        idempotent = method == "GET"
        url = settings.gmail_api_base_url.rstrip("/") + path
        force_refresh = False
        retries = 0
        resent = False
        loop = asyncio.get_running_loop()
        while True:
            token = await connections.access_token(
                self.connection.id, expected_subject=self.connection.account_subject, force_refresh=force_refresh
            )
            started = time.monotonic()
            retry_after: float | None = None
            try:
                async with _connection_slot(self.connection.id):
                    if not idempotent:
                        budget.posted = True
                    status, body, retry_after = await _exchange(
                        method, url, params, content, {**(headers or {}), "Authorization": f"Bearer {token}"},
                        timeout, max_bytes, too_large,
                    )
            except _NOT_SENT as exc:
                _log(method, path, None, type(exc).__name__, started)
                if not idempotent:
                    budget.posted = False
                    if resent:
                        raise PluginUnavailable(UNREACHABLE) from exc
                    resent = True
                    continue
                failure: str = UNREACHABLE
            except httpx.TransportError as exc:  # timeouts and dropped connections after the request was sent
                _log(method, path, None, type(exc).__name__, started)
                if not idempotent:
                    raise GmailUncertain(UNCERTAIN) from exc
                failure = UNREACHABLE
            else:
                reason, message = _google_error(body) if status >= 400 else ("", "")
                _log(method, path, status, reason, started)
                if status == 401:
                    if force_refresh:
                        await connections.mark_reconnect_needed(self.connection.id, "revoked")
                        raise ReconnectNeeded("revoked")
                    force_refresh = True
                    continue
                if 200 <= status < 300:
                    await self._touch()
                    return body
                rate_limited = status == 429 or (status == 403 and reason in _RATE_REASONS)
                if idempotent and (rate_limited or status >= 500):
                    failure = BUSY
                elif status >= 500:
                    raise GmailUncertain(UNCERTAIN)  # a POST Gmail failed on: it may have been done
                elif rate_limited:
                    raise PluginUnavailable(BUSY)
                elif status == 403 and reason in _SCOPE_REASONS:
                    await connections.mark_reconnect_needed(self.connection.id, "scopes_missing")
                    raise ReconnectNeeded("scopes_missing")
                else:
                    _raise_for(status, message)
            retries += 1
            wait = min(max(retry_after, 1.0), 10.0) if retry_after else 2.0 ** (retries - 1) + random.uniform(0, 0.5)
            if retries > RETRIES or loop.time() + wait >= budget.deadline:
                raise PluginUnavailable(failure)
            await _sleep(wait)

    async def _touch(self) -> None:
        if self._touched:
            return
        self._touched = True
        try:
            await connections.touch(self.connection.id)
        except Exception:
            logger.exception("Could not record the use of connection %s", self.connection.id)


async def _exchange(
    method: str,
    url: str,
    params: httpx.QueryParams | None,
    content: bytes | None,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    max_bytes: int,
    too_large: PluginError | None,
) -> tuple[int, bytes, float | None]:
    """(status, body, Retry-After seconds) of one HTTP exchange; the body is refused while reading past `max_bytes`."""
    async with http.client().stream(
        method, url, params=params, content=content, headers=headers, timeout=timeout
    ) as response:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                if response.status_code < 300:
                    raise too_large or PluginError("Gmail's answer was too large to read.")
                break  # an error body: its start is enough
            chunks.append(chunk)
        return response.status_code, b"".join(chunks), _retry_after(response.headers.get("retry-after"))


def _raise_for(status: int, message: str) -> NoReturn:
    if status == 404:
        raise GmailNotFound()
    if status == 400:
        raise PluginError(message[:200] or "Gmail rejected this request.")
    if status == 403:
        raise PluginError(REFUSED)
    raise PluginUnavailable(UNREACHABLE)


def _google_error(body: bytes) -> tuple[str, str]:
    """(reason, message) of Google's error JSON: `error.errors[0].reason` (or `error.status`) and `error.message`."""
    try:
        error = json.loads(body[:65536]).get("error")
    except (ValueError, AttributeError):
        return "", ""
    if not isinstance(error, dict):
        return "", ""
    errors = error.get("errors")
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    reason = first.get("reason") or error.get("status") or ""
    details = error.get("details")
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict) and detail.get("reason") in _SCOPE_REASONS:
                reason = detail["reason"]
    message = error.get("message")
    return str(reason), " ".join(message.split()) if isinstance(message, str) else ""


def _retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value) if value else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None


def _log(method: str, path: str, status: int | None, reason: str, started: float) -> None:
    level = logging.DEBUG if status is not None and status < 400 else logging.INFO
    logger.log(
        level, "Gmail %s %s -> %s%s in %.0f ms", method, path, status if status is not None else "no response",
        f" ({reason})" if reason else "", (time.monotonic() - started) * 1000,
    )


def _list_params(q: str, max_results: int, page_token: str | None) -> httpx.QueryParams:
    params: dict[str, str | int] = {"q": q, "maxResults": max_results}
    if page_token:
        params["pageToken"] = page_token
    return httpx.QueryParams(params)


def _format_params(fmt: str) -> httpx.QueryParams:
    params: list[tuple[str, str | int | float | bool | None]] = [("format", fmt)]
    if fmt == "metadata":
        params.extend(("metadataHeaders", name) for name in METADATA_HEADERS)
    return httpx.QueryParams(params)


def _page_token(data: dict[str, Any]) -> str | None:
    token = data.get("nextPageToken")
    return token if isinstance(token, str) and token else None


def _estimate(data: dict[str, Any]) -> int:
    value = data.get("resultSizeEstimate")
    return value if isinstance(value, int) and value >= 0 else 0


def _attachment_bytes(raw: bytes) -> bytes:
    """The decoded `data` of an attachment response (in a thread: JSON and base64 of up to tens of MB)."""
    try:
        data = json.loads(raw).get("data")
    except (ValueError, AttributeError) as exc:
        raise PluginUnavailable(UNREACHABLE) from exc
    if not isinstance(data, str):
        raise PluginUnavailable(UNREACHABLE)
    return b64url_decode(data)


def _related(metadata: dict[str, Any], raw: bytes) -> tuple[bytes, str]:
    """A multipart/related upload body: the JSON metadata, then the message as message/rfc822."""
    boundary = f"rag-{secrets.token_hex(16)}"
    while boundary.encode() in raw:
        boundary = f"rag-{secrets.token_hex(16)}"
    content = b"".join(
        [
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
            json.dumps(metadata).encode(),
            f"\r\n--{boundary}\r\nContent-Type: message/rfc822\r\n\r\n".encode(),
            raw,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return content, f'multipart/related; boundary="{boundary}"'


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB" if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"
