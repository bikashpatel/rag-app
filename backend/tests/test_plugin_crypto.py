"""Token encryption at rest (docs/briefs/plugins-gmail.md, section 5.3): row binding, key rotation (lazy and at
startup), unreadable rows, the admin's old-key count and invalid keys."""

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from conftest import TEST_TOKEN_KEY, ApiClient, PluginHarness
from cryptography.fernet import Fernet
from fake_plugin import FakePlugin, insert_connection
from fastapi import FastAPI
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, init_db
from app.main import app as fastapi_app
from app.models import PluginConnection, User
from app.plugins import connections, crypto, registry


@pytest.fixture
async def fake(plugins: PluginHarness) -> AsyncIterator[FakePlugin]:
    plugin = FakePlugin()
    with registry.use_plugins([plugin]):
        yield plugin


async def _row(connection_id: int) -> PluginConnection:
    async with SessionLocal() as db:
        row = await db.get(PluginConnection, connection_id)
        assert row is not None
        return row


def test_round_trip_and_row_binding() -> None:
    cipher = crypto.TokenCipher([Fernet.generate_key()])
    ciphertext = cipher.encrypt(7, "gmail", "1//secret-refresh")
    assert "secret-refresh" not in ciphertext
    assert cipher.decrypt(7, "gmail", ciphertext) == "1//secret-refresh"
    with pytest.raises(crypto.TokenUnreadable):
        cipher.decrypt(8, "gmail", ciphertext)  # copied into another user's row
    with pytest.raises(crypto.TokenUnreadable):
        cipher.decrypt(7, "fake", ciphertext)  # another plugin's
    with pytest.raises(crypto.TokenUnreadable):
        cipher.decrypt(7, "gmail", "not a token")


def test_rotation_keeps_old_tokens_readable() -> None:
    old, new = Fernet.generate_key(), Fernet.generate_key()
    before = crypto.TokenCipher([old])
    ciphertext = before.encrypt(1, "gmail", "token")
    after = crypto.TokenCipher([new, old])
    assert after.key_id == crypto.key_id(new) != before.key_id
    assert after.decrypt(1, "gmail", ciphertext) == "token"
    assert after.needs_rotation(before.key_id) and not after.needs_rotation(after.key_id)
    rotated = after.rotate(ciphertext)
    assert crypto.TokenCipher([new]).decrypt(1, "gmail", rotated) == "token"  # binding kept, first key only
    with pytest.raises(crypto.TokenUnreadable):
        crypto.TokenCipher([Fernet.generate_key()]).decrypt(1, "gmail", ciphertext)  # the key was removed


def test_key_id_is_short_and_not_the_key() -> None:
    key = Fernet.generate_key()
    assert len(crypto.key_id(key)) == 16 and crypto.key_id(key) not in key.decode()


@pytest.mark.parametrize("value", ["", "  ", "not-a-key", f"{TEST_TOKEN_KEY},short"])
def test_invalid_or_empty_key_means_not_configured(
    monkeypatch: pytest.MonkeyPatch, plugins: PluginHarness, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", value)
    assert crypto.token_cipher() is None
    assert "TOKEN_ENCRYPTION_KEY" in registry.configured(FakePlugin())
    assert "short" not in caplog.text and "not-a-key" not in caplog.text  # never the value


def test_valid_key_list_is_configured(monkeypatch: pytest.MonkeyPatch, plugins: PluginHarness) -> None:
    second = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "token_encryption_key", f" {TEST_TOKEN_KEY} , {second} ")
    cipher = crypto.token_cipher()
    assert cipher is not None
    assert cipher.key_ids == (crypto.key_id(TEST_TOKEN_KEY.encode()), crypto.key_id(second.encode()))
    assert registry.configured(FakePlugin()) == []


async def test_rotate_all_re_encrypts_dormant_rows_and_leaves_unreadable_ones(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakePlugin,
    app: FastAPI,
    admin_client: ApiClient,
    create_user: Callable[..., Awaitable[ApiClient]],
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    assert alice.user_id is not None and bob.user_id is not None
    old, lost = TEST_TOKEN_KEY, Fernet.generate_key().decode()
    readable = await insert_connection(alice.user_id, key=old, refresh_token="1//alice")
    unreadable = await insert_connection(bob.user_id, key=lost, refresh_token="1//bob", subject="200")
    new = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "token_encryption_key", f"{new},{old}")

    listed = (await admin_client.get("/api/admin/plugins")).json()[0]
    assert listed["connections"]["old_key"] == 2

    assert await crypto.rotate_all() == (1, 1)
    cipher = crypto.token_cipher()
    assert cipher is not None
    row = await _row(readable)
    assert row.key_id == cipher.key_id
    only_new = crypto.TokenCipher([new.encode()])
    assert only_new.decrypt(alice.user_id, "fake", row.refresh_token_enc) == "1//alice"  # binding kept
    assert row.access_token_enc is not None
    assert only_new.decrypt(alice.user_id, "fake", row.access_token_enc) == "ya29.access"
    untouched = await _row(unreadable)
    assert untouched.key_id == crypto.key_id(lost.encode())

    listed = (await admin_client.get("/api/admin/plugins")).json()[0]
    assert listed["connections"]["old_key"] == 1  # only the row no configured key reads

    # Re-adding the lost key recovers the row at the next start.
    monkeypatch.setattr(settings, "token_encryption_key", f"{new},{lost}")
    assert await crypto.rotate_all() == (1, 0)
    assert only_new.decrypt(bob.user_id, "fake", (await _row(unreadable)).refresh_token_enc) == "1//bob"
    listed = (await admin_client.get("/api/admin/plugins")).json()[0]
    assert listed["connections"]["old_key"] == 0


async def test_startup_runs_the_rotation_and_the_plugins_startup(
    monkeypatch: pytest.MonkeyPatch, fake: FakePlugin
) -> None:
    await init_db()
    async with SessionLocal() as db:
        user = User(username="alice", password_hash="unused")
        db.add(user)
        await db.commit()
    connection_id = await insert_connection(user.id, key=TEST_TOKEN_KEY)
    new = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "token_encryption_key", f"{new},{TEST_TOKEN_KEY}")
    async with fastapi_app.router.lifespan_context(fastapi_app):
        assert (await _row(connection_id)).key_id == crypto.key_id(new.encode())
        assert fake.events == ["startup"]


async def test_access_token_re_encrypts_lazily_and_unreadable_tokens_need_a_reconnect(
    monkeypatch: pytest.MonkeyPatch, fake: FakePlugin, create_user: Callable[..., Awaitable[ApiClient]]
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    assert alice.user_id is not None and bob.user_id is not None
    rolling = await insert_connection(alice.user_id, key=TEST_TOKEN_KEY)
    lost = await insert_connection(bob.user_id, key=Fernet.generate_key().decode(), subject="200", access_token=None)
    new = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "token_encryption_key", f"{new},{TEST_TOKEN_KEY}")

    token = await connections.access_token(rolling, expected_subject="100000000000000000001")
    assert token == "ya29.access"
    assert (await _row(rolling)).key_id == crypto.key_id(new.encode())

    with pytest.raises(connections.ReconnectNeeded) as raised:
        await connections.access_token(lost, expected_subject="200")
    assert raised.value.reason == "key_changed"
    row = await _row(lost)
    assert (row.status, row.status_reason) == ("reconnect_needed", "key_changed")
    async with SessionLocal() as db:
        statuses = (await db.execute(select(PluginConnection.status))).scalars().all()
    assert sorted(statuses) == ["connected", "reconnect_needed"]
