"""The Gmail REST client (brief 8, 19.2): retries and backoff, POSTs that may have been sent, token refresh on 401,
Google's errors, deadlines, concurrency limits and logging. Scripted with httpx.MockTransport; sleeps are recorded."""

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from gmail_support import gmail  # noqa: F401

from app.plugins import connections
from app.plugins import http as plugin_http
from app.plugins.base import AccountChanged, ConnectionInfo, PluginError, PluginUnavailable, ReconnectNeeded
from app.plugins.gmail import client as gmail_client
from app.plugins.gmail.client import GmailClient, GmailNotFound, GmailUncertain

CONNECTION = ConnectionInfo(
    id=7,
    user_id=1,
    plugin_id="gmail",
    account_email="alice@example.test",
    account_subject="sub-1",
    scopes=frozenset(),
    status="connected",
    status_reason=None,
    chat_enabled=True,
    connected_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
)


class Tokens:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, bool]] = []
        self.marked: list[tuple[int, str]] = []
        self.touched: list[int] = []
        self.error: BaseException | None = None

    async def access_token(self, connection_id: int, *, expected_subject: str, force_refresh: bool = False) -> str:
        self.calls.append((connection_id, expected_subject, force_refresh))
        if self.error is not None:
            raise self.error
        return f"token-{len(self.calls)}"

    async def mark(self, connection_id: int, reason: str) -> None:
        self.marked.append((connection_id, reason))

    async def touch(self, connection_id: int) -> None:
        self.touched.append(connection_id)


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> Tokens:
    found = Tokens()
    monkeypatch.setattr(connections, "access_token", found.access_token)
    monkeypatch.setattr(connections, "mark_reconnect_needed", found.mark)
    monkeypatch.setattr(connections, "touch", found.touch)
    return found


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(gmail_client, "_sleep", sleep)
    return recorded


Handler = Callable[[httpx.Request], Any]


@pytest.fixture
def transport() -> Callable[[Handler], list[httpx.Request]]:
    def install(handler: Handler) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            result = handler(request)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        plugin_http.set_transport(httpx.MockTransport(handle))
        return seen

    return install


def google_error(status: int, reason: str, message: str = "Error.", headers: dict[str, str] | None = None) -> Any:
    body = {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
    return httpx.Response(status, json=body, headers=headers)


def scripted(*responses: Any) -> Handler:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return handler


OK_LIST = httpx.Response(200, json={"messages": [{"id": "abc1", "threadId": "t1"}], "resultSizeEstimate": 1})


@pytest.mark.parametrize(
    "failure",
    [
        google_error(429, "rateLimitExceeded"),
        google_error(500, "backendError"),
        google_error(503, "backendError"),
        google_error(403, "rateLimitExceeded"),
        google_error(403, "userRateLimitExceeded"),
    ],
)
async def test_reads_retry_with_backoff(tokens, sleeps, transport, failure) -> None:
    seen = transport(scripted(failure, failure, OK_LIST))
    page = await GmailClient(CONNECTION).list_messages("is:unread", 10, None)
    assert page.messages == [("abc1", "t1")] and len(seen) == 3
    assert 1.0 <= sleeps[0] <= 1.5 and 2.0 <= sleeps[1] <= 2.5
    assert tokens.touched == [7]


async def test_reads_give_up_after_three_retries(tokens, sleeps, transport) -> None:
    seen = transport(scripted(*[google_error(500, "backendError")] * 4))
    with pytest.raises(PluginUnavailable, match="Gmail is busy right now"):
        await GmailClient(CONNECTION).list_messages("x", 10, None)
    assert len(seen) == 4 and len(sleeps) == 3


async def test_connect_errors_and_timeouts_are_retried_for_reads(tokens, sleeps, transport) -> None:
    request = httpx.Request("GET", "https://gmail.googleapis.com/")
    failures = [httpx.ConnectError("down", request=request), httpx.ReadTimeout("slow", request=request)]
    transport(scripted(*failures, OK_LIST))
    page = await GmailClient(CONNECTION).list_messages("x", 10, None)
    assert page.result_size_estimate == 1 and len(sleeps) == 2


@pytest.mark.parametrize(("header", "expected"), [("120", 10.0), ("0.2", 1.0), ("3", 3.0)])
async def test_retry_after_is_clamped(tokens, sleeps, transport, header, expected) -> None:
    transport(scripted(google_error(429, "rateLimitExceeded", headers={"Retry-After": header}), OK_LIST))
    await GmailClient(CONNECTION).list_messages("x", 10, None)
    assert sleeps == [expected]


async def test_backoff_never_waits_past_the_deadline(tokens, sleeps, transport, monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "CALL_SECONDS", 5.0)
    transport(scripted(google_error(429, "rateLimitExceeded", headers={"Retry-After": "9"})))
    with pytest.raises(PluginUnavailable, match="busy"):
        await GmailClient(CONNECTION).list_messages("x", 10, None)
    assert sleeps == []


async def test_posts_are_not_retried(tokens, sleeps, transport) -> None:
    seen = transport(scripted(google_error(429, "rateLimitExceeded")))
    with pytest.raises(PluginUnavailable, match="busy"):
        await GmailClient(CONNECTION).send(b"raw", None)
    assert len(seen) == 1 and sleeps == []


async def test_a_post_retries_once_after_a_connect_error(tokens, transport) -> None:
    request = httpx.Request("POST", "https://gmail.googleapis.com/")
    sent = httpx.Response(200, json={"id": "m9", "threadId": "t9"})
    seen = transport(scripted(httpx.ConnectError("down", request=request), sent))
    assert await GmailClient(CONNECTION).send(b"raw", None) == {"id": "m9", "threadId": "t9"}
    assert len(seen) == 2
    transport(scripted(httpx.ConnectError("down", request=request), httpx.ConnectError("down", request=request)))
    with pytest.raises(PluginUnavailable) as caught:
        await GmailClient(CONNECTION).send(b"raw", None)
    assert not isinstance(caught.value, GmailUncertain)


@pytest.mark.parametrize("failure", ["timeout", "dropped", "500"])
async def test_a_send_without_an_answer_is_uncertain(tokens, transport, failure) -> None:
    request = httpx.Request("POST", "https://gmail.googleapis.com/")
    outcome = {
        "timeout": httpx.ReadTimeout("slow", request=request),
        "dropped": httpx.RemoteProtocolError("Server disconnected", request=request),
        "500": google_error(500, "backendError"),
    }[failure]
    seen = transport(scripted(outcome))
    with pytest.raises(GmailUncertain):
        await GmailClient(CONNECTION).send(b"raw", "t1")
    assert len(seen) == 1


async def test_a_post_past_its_deadline_is_uncertain(tokens, transport, monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "LONG_CALL_SECONDS", 0.05)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    transport(slow)
    with pytest.raises(GmailUncertain):
        await GmailClient(CONNECTION).create_draft(b"raw", None)


async def test_a_read_past_its_deadline_is_unavailable(tokens, transport, monkeypatch) -> None:
    monkeypatch.setattr(gmail_client, "CALL_SECONDS", 0.05)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return OK_LIST

    transport(slow)
    with pytest.raises(PluginUnavailable, match="unreachable") as caught:
        await GmailClient(CONNECTION).profile()
    assert not isinstance(caught.value, GmailUncertain)


async def test_a_401_forces_one_refresh(tokens, transport) -> None:
    seen = transport(scripted(httpx.Response(401, json={}), OK_LIST))
    await GmailClient(CONNECTION).list_messages("x", 10, None)
    assert tokens.calls == [(7, "sub-1", False), (7, "sub-1", True)]
    assert [request.headers["authorization"] for request in seen] == ["Bearer token-1", "Bearer token-2"]
    transport(scripted(httpx.Response(401, json={}), httpx.Response(401, json={})))
    with pytest.raises(ReconnectNeeded) as caught:
        await GmailClient(CONNECTION).list_messages("x", 10, None)
    assert caught.value.reason == "revoked" and tokens.marked == [(7, "revoked")]


async def test_errors_are_mapped(tokens, transport) -> None:
    transport(scripted(google_error(404, "notFound")))
    with pytest.raises(GmailNotFound, match="wasn't found"):
        await GmailClient(CONNECTION).get_message("abc", "full")
    transport(scripted(google_error(400, "invalidArgument", "Invalid query " + "x" * 300)))
    with pytest.raises(PluginError) as caught:
        await GmailClient(CONNECTION).list_messages("from:", 10, None)
    assert str(caught.value).startswith("Invalid query") and len(str(caught.value)) == 200
    transport(scripted(google_error(403, "forbidden")))
    with pytest.raises(PluginError, match="Gmail refused this request"):
        await GmailClient(CONNECTION).profile()
    assert tokens.marked == []
    transport(scripted(google_error(403, "insufficientPermissions")))
    with pytest.raises(ReconnectNeeded) as reconnect:
        await GmailClient(CONNECTION).create_draft(b"raw", None)
    assert reconnect.value.reason == "scopes_missing" and tokens.marked == [(7, "scopes_missing")]


async def test_account_changed_propagates(tokens, transport) -> None:
    tokens.error = AccountChanged("Your connected Gmail account changed. Ask again.")
    seen = transport(scripted())
    with pytest.raises(AccountChanged):
        await GmailClient(CONNECTION).profile()
    assert seen == [] and tokens.calls[0][1] == "sub-1"


async def test_ids_never_reach_a_url_unchecked(tokens, transport) -> None:
    seen = transport(scripted())
    for bad in ("../profile", "a/b", "", "x" * 65, "abc?q=1"):
        with pytest.raises(GmailNotFound):
            await GmailClient(CONNECTION).get_message(bad, "full")
    assert seen == []
    with pytest.raises(ValueError):
        await GmailClient(CONNECTION).get_thread("abc", "full")  # type: ignore[arg-type]


async def test_requests_and_metadata_parameters(tokens, transport) -> None:
    seen = transport(scripted(httpx.Response(200, json={"id": "abc"}), OK_LIST))
    await GmailClient(CONNECTION).get_message("abc", "metadata")
    await GmailClient(CONNECTION).list_messages("from:bob", 5, "token2")
    first, second = seen
    assert first.url.path == "/gmail/v1/users/me/messages/abc"
    assert first.url.params.get_list("format") == ["metadata"]
    assert first.url.params.get_list("metadataHeaders") == list(gmail_client.METADATA_HEADERS)
    assert dict(second.url.params) == {"q": "from:bob", "maxResults": "5", "pageToken": "token2"}


async def test_uploads_are_multipart_related(tokens, transport) -> None:
    seen = transport(scripted(httpx.Response(200, json={"id": "r1", "message": {"id": "m1", "threadId": "t1"}})))
    raw = b"From: a@example.com\r\nSubject: x\r\n\r\nbody\r\n"
    await GmailClient(CONNECTION).create_draft(raw, "t1")
    request = seen[0]
    assert request.url.path == "/upload/gmail/v1/users/me/drafts"
    assert request.url.params["uploadType"] == "multipart"
    content_type = request.headers["content-type"]
    assert content_type.startswith('multipart/related; boundary="rag-')
    boundary = content_type.split('boundary="')[1].rstrip('"').encode()
    parts = request.content.split(b"--" + boundary)
    assert json.loads(parts[1].split(b"\r\n\r\n", 1)[1]) == {"message": {"threadId": "t1"}}
    assert parts[2].split(b"\r\n\r\n", 1)[1] == raw + b"\r\n"
    assert parts[3] == b"--\r\n"


async def test_at_most_four_requests_per_connection(tokens, transport) -> None:
    running = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return httpx.Response(200, json={"id": "abc"})

    transport(handler)
    client = GmailClient(CONNECTION)
    await asyncio.gather(*(client.get_message("abc", "metadata") for _ in range(12)))
    assert peak == 4


async def test_at_most_two_attachment_downloads_at_once(tokens, transport) -> None:
    running = peak = 0
    data = "Grüße".encode() * 10

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        unpadded = base64.urlsafe_b64encode(data).rstrip(b"=").decode()
        return httpx.Response(200, json={"size": len(data), "data": unpadded})

    transport(handler)
    clients = [GmailClient(CONNECTION), GmailClient(CONNECTION.__class__(**{**CONNECTION.__dict__, "id": 8}))]
    results = await asyncio.gather(*(clients[n % 2].get_attachment("abc", "ANG_1", 1000) for n in range(6)))
    assert peak == 2 and results == [data] * 6


async def test_attachments_over_the_limit_are_refused_while_reading(tokens, transport) -> None:
    big = base64.urlsafe_b64encode(b"x" * 300_000).decode()
    transport(lambda request: httpx.Response(200, json={"size": 300_000, "data": big}))
    with pytest.raises(PluginError, match="larger than"):
        await GmailClient(CONNECTION).get_attachment("abc", "ANG_1", 100_000)


async def test_logs_hold_no_query_strings_or_tokens(tokens, transport, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="app.plugins.gmail.client")
    transport(scripted(google_error(500, "backendError"), OK_LIST))
    await GmailClient(CONNECTION).list_messages("from:secret-sender subject:salary", 10, "pagetok")
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "GET /gmail/v1/users/me/messages -> 500 (backendError)" in text
    for secret in ("secret-sender", "salary", "pagetok", "token-1", "Bearer"):
        assert secret not in text


async def test_thread_messages_use_minimal_then_the_newest_in_full(gmail, create_user) -> None:  # noqa: F811
    connection = await gmail.connect(await create_user())
    head = gmail.message("thread150_1")
    gmail.mailbox.requests.clear()
    resources, total = await GmailClient(connection).get_thread_messages(head.thread_id, 20)
    assert total == 150 and len(resources) == 20
    numbers = [int(resource["snippet"].split()[-1]) for resource in resources]
    assert numbers == list(range(131, 151))  # the newest 20, oldest first
    thread_calls = [call for call in gmail.mailbox.requests if "/threads/" in call[1]]
    assert len(thread_calls) == 1 and ("format", "minimal") in thread_calls[0][2]
    message_calls = [call for call in gmail.mailbox.requests if "/messages/" in call[1]]
    assert len(message_calls) == 20 and all(("format", "full") in call[2] for call in message_calls)
