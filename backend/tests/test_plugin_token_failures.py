"""The provider's token and revoke endpoints failing (docs/briefs/plugins-gmail.md, sections 5.2 to 5.6): network errors
and unusable answers at the callback's code exchange and at a refresh, a provider that rotates the refresh token, a
refresh under a rotated TOKEN_ENCRYPTION_KEY, and a revoke that can't reach the provider. Nothing is stored from an
unusable answer, a connection is never marked for a reconnect over a temporary failure, and no token is logged."""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from conftest import TEST_TOKEN_KEY, ApiClient, PluginHarness, oauth_result
from cryptography.fernet import Fernet
from fake_plugin import FakePlugin
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import PluginConnection, utcnow
from app.plugins import connections, crypto, registry
from app.plugins import http as plugin_http
from app.plugins.base import PluginUnavailable

USER = Callable[..., Awaitable[ApiClient]]
ALICE_SUB = "100000000000000000001"
# What the provider's next answer becomes: an exception to raise, raw (status, body) to answer, or an edit of the
# fake Google's real JSON answer.
Answer = BaseException | tuple[int, bytes] | Callable[[dict[str, Any]], dict[str, Any]]


class Provider(httpx.AsyncBaseTransport):
    """The fake Google, except for the next answers of its token and revoke endpoints."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.answers: dict[str, list[Answer]] = {"/token": [], "/revoke": []}
        self.calls: dict[str, int] = {"/token": 0, "/revoke": 0}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in self.calls:
            self.calls[path] += 1
        pending = self.answers.get(path)
        if not pending:
            return await self.inner.handle_async_request(request)
        answer = pending.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, tuple):
            status, body = answer
            return httpx.Response(status, content=body, headers={"content-type": "application/json"}, request=request)
        real = await self.inner.handle_async_request(request)
        await real.aread()
        edited = json.dumps(answer(json.loads(real.content))).encode()
        return httpx.Response(real.status_code, content=edited, headers={"content-type": "application/json"})


@pytest.fixture
async def fake(plugins: PluginHarness, admin_client: ApiClient) -> AsyncIterator[FakePlugin]:
    plugin = FakePlugin()
    with registry.use_plugins([plugin]):
        response = await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
        assert response.status_code == 200, response.text
        yield plugin


@pytest.fixture
def provider(plugins: PluginHarness) -> Provider:
    found = Provider(httpx.ASGITransport(app=plugins.app))
    plugin_http.set_transport(found)  # the autouse plugin_registry fixture resets it
    return found


async def _row(user_id: int | None) -> PluginConnection | None:
    async with SessionLocal() as db:
        return await db.scalar(select(PluginConnection).where(PluginConnection.user_id == user_id))


async def _connected(plugins: PluginHarness, client: ApiClient) -> PluginConnection:
    assert oauth_result(await plugins.connect(client, "fake")) == "connected"
    row = await _row(client.user_id)
    assert row is not None
    return row


async def _expire_access(connection_id: int) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(PluginConnection).where(PluginConnection.id == connection_id).values(access_expires_at=utcnow())
        )
        await db.commit()


def without(*keys: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda body: {key: value for key, value in body.items() if key not in keys}


def replacing(**values: Any) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda body: {**body, **values}


# ---------------- The callback's code exchange ----------------


async def test_a_code_exchange_that_cant_reach_the_provider_is_an_error(
    fake: FakePlugin,
    plugins: PluginHarness,
    provider: Provider,
    create_user: USER,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alice = await create_user("alice")
    provider.answers["/token"] = [httpx.ConnectError("connection refused")]

    with caplog.at_level(logging.WARNING, logger="app.plugins.oauth"):
        assert oauth_result(await plugins.connect(alice, "fake")) == "error"

    assert await _row(alice.user_id) is None
    assert "The fake token exchange failed: ConnectError" in caplog.text
    assert "code=" not in caplog.text and "4/fake-" not in caplog.text


@pytest.mark.parametrize(
    "answer",
    [
        (200, b"<html>Service Unavailable</html>"),
        (200, b"[]"),
        without("access_token"),
        replacing(access_token=""),
        replacing(expires_in=0),
        replacing(expires_in="soon"),
        replacing(expires_in=True),
        without("id_token"),
        replacing(id_token="not-a-jwt"),
    ],
    ids=[
        "not-json",
        "not-an-object",
        "no-access-token",
        "empty-access-token",
        "zero-expiry",
        "text-expiry",
        "boolean-expiry",
        "no-id-token",
        "unreadable-id-token",
    ],
)
async def test_an_unusable_code_exchange_answer_stores_nothing(
    answer: Answer, fake: FakePlugin, plugins: PluginHarness, provider: Provider, create_user: USER
) -> None:
    alice = await create_user("alice")
    provider.answers["/token"] = [answer]

    assert oauth_result(await plugins.connect(alice, "fake")) == "error"

    assert await _row(alice.user_id) is None
    listed = (await alice.get("/api/plugins")).json()[0]
    assert listed["connection"] is None


async def test_an_expiry_sent_as_digits_is_accepted(
    fake: FakePlugin, plugins: PluginHarness, provider: Provider, create_user: USER
) -> None:
    alice = await create_user("alice")
    provider.answers["/token"] = [replacing(expires_in="1800")]

    row = await _connected(plugins, alice)

    assert row.access_expires_at is not None
    assert utcnow() + timedelta(minutes=29) < row.access_expires_at <= utcnow() + timedelta(minutes=30)


# ---------------- Refreshing ----------------


async def test_a_refresh_that_cant_reach_the_provider_twice_is_temporary(
    fake: FakePlugin,
    plugins: PluginHarness,
    provider: Provider,
    create_user: USER,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _expire_access(row.id)
    before = provider.calls["/token"]
    provider.answers["/token"] = [httpx.ConnectTimeout("timed out"), httpx.ReadError("reset by peer")]

    with caplog.at_level(logging.WARNING, logger="app.plugins.connections"):
        with pytest.raises(PluginUnavailable, match="unreachable right now"):
            await connections.access_token(row.id, expected_subject=ALICE_SUB)

    assert provider.calls["/token"] == before + 2  # retried once
    assert f"Refreshing connection {row.id} failed: ConnectTimeout" in caplog.text
    after = await _row(alice.user_id)
    assert after is not None and (after.status, after.refresh_token_enc) == ("connected", row.refresh_token_enc)
    provider.answers["/token"] = [httpx.ConnectError("connection refused")]
    assert await connections.access_token(row.id, expected_subject=ALICE_SUB)  # the retry reached it
    assert provider.calls["/token"] == before + 4


@pytest.mark.parametrize(
    "answer",
    [(200, b"upstream says hi"), without("access_token"), replacing(expires_in=-5)],
    ids=["not-json", "no-access-token", "negative-expiry"],
)
async def test_an_unusable_refresh_answer_is_temporary_and_keeps_the_connection(
    answer: Answer,
    fake: FakePlugin,
    plugins: PluginHarness,
    provider: Provider,
    create_user: USER,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _expire_access(row.id)
    provider.answers["/token"] = [answer]

    with caplog.at_level(logging.WARNING, logger="app.plugins.connections"):
        with pytest.raises(PluginUnavailable, match="unreachable right now"):
            await connections.access_token(row.id, expected_subject=ALICE_SUB)

    assert "The fake token endpoint returned an unexpected refresh response" in caplog.text
    after = await _row(alice.user_id)
    assert after is not None and after.status == "connected" and after.access_token_enc == row.access_token_enc


async def test_a_refresh_token_rotated_by_the_provider_replaces_the_stored_one(
    fake: FakePlugin, plugins: PluginHarness, provider: Provider, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _expire_access(row.id)
    provider.answers["/token"] = [replacing(refresh_token="1//rotated-by-google")]

    access = await connections.access_token(row.id, expected_subject=ALICE_SUB)

    after = await _row(alice.user_id)
    cipher = crypto.token_cipher()
    assert after is not None and cipher is not None
    assert cipher.decrypt(after.user_id, "fake", after.refresh_token_enc) == "1//rotated-by-google"
    assert after.access_token_enc is not None
    assert cipher.decrypt(after.user_id, "fake", after.access_token_enc) == access
    assert "rotated-by-google" not in after.refresh_token_enc


async def test_a_refresh_after_a_key_rotation_re_encrypts_the_kept_refresh_token(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    old_cipher = crypto.token_cipher()
    assert old_cipher is not None
    refresh_token = old_cipher.decrypt(row.user_id, "fake", row.refresh_token_enc)
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "token_encryption_key", f"{new_key},{TEST_TOKEN_KEY}")
    await _expire_access(row.id)

    await connections.access_token(row.id, expected_subject=ALICE_SUB)

    after = await _row(alice.user_id)
    only_new = crypto.TokenCipher([new_key.encode()])
    assert after is not None and after.key_id == only_new.key_id
    assert only_new.decrypt(after.user_id, "fake", after.refresh_token_enc) == refresh_token  # binding kept
    with pytest.raises(crypto.TokenUnreadable):
        old_cipher.decrypt(after.user_id, "fake", after.refresh_token_enc)


async def test_a_refresh_without_a_token_key_is_unavailable_and_keeps_the_connection(
    fake: FakePlugin, plugins: PluginHarness, provider: Provider, create_user: USER, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _expire_access(row.id)
    before = provider.calls["/token"]
    monkeypatch.setattr(settings, "token_encryption_key", "")

    with pytest.raises(PluginUnavailable, match="isn't set up on this server"):
        await connections.access_token(row.id, expected_subject=ALICE_SUB)

    assert provider.calls["/token"] == before
    after = await _row(alice.user_id)
    assert after is not None and after.status == "connected"


# ---------------- Revoking ----------------


async def test_a_disconnect_whose_revoke_cant_reach_the_provider_still_deletes_the_connection(
    fake: FakePlugin,
    plugins: PluginHarness,
    provider: Provider,
    create_user: USER,
    caplog: pytest.LogCaptureFixture,
) -> None:
    alice = await create_user("alice")
    await _connected(plugins, alice)
    provider.answers["/revoke"] = [httpx.ConnectError("connection refused")]

    with caplog.at_level(logging.WARNING, logger="app.plugins.connections"):
        response = await alice.delete("/api/plugins/fake/connection")

    assert response.status_code == 204
    assert await _row(alice.user_id) is None
    assert provider.calls["/revoke"] == 1
    assert "Revoking a fake grant failed: ConnectError" in caplog.text
    assert "1//fake-" not in caplog.text and "ya29." not in caplog.text
