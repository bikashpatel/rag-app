"""Connections after the consent (docs/briefs/plugins-gmail.md, sections 5.4 to 5.8): access tokens and refresh,
status changes, Google's testing-mode expiry, disconnecting, shared Google accounts, "Use in chat", the user's list
and deleting a user."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta

import pytest
from conftest import ApiClient, PluginHarness, oauth_result
from fake_plugin import READ, FakePlugin, _fetch, fake_api_router, insert_connection
from fastapi import HTTPException
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.main import app as fastapi_app
from app.models import PluginAuditEvent, PluginConnection, User, utcnow
from app.plugins import connections, crypto, registry
from app.plugins.base import AccountChanged, PluginDisabled, PluginError, PluginUnavailable, ReconnectNeeded

USER = Callable[..., Awaitable[ApiClient]]
ALICE, BOB = "alice@example.test", "bob@example.test"
ALICE_SUB, BOB_SUB = "100000000000000000001", "100000000000000000002"


@pytest.fixture
async def fake(plugins: PluginHarness, admin_client: ApiClient) -> AsyncIterator[FakePlugin]:
    plugin = FakePlugin()
    plugins.app.include_router(fake_api_router(plugins.google))
    with registry.use_plugins([plugin]):
        response = await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
        assert response.status_code == 200, response.text
        yield plugin


async def _connected(plugins: PluginHarness, client: ApiClient, account: str = ALICE) -> PluginConnection:
    assert oauth_result(await plugins.connect(client, "fake", account=account)) == "connected"
    row = await _row_of(client.user_id)
    assert row is not None
    return row


async def _row_of(user_id: int | None) -> PluginConnection | None:
    async with SessionLocal() as db:
        return await db.scalar(select(PluginConnection).where(PluginConnection.user_id == user_id))


async def _set(connection_id: int, **values: object) -> None:
    async with SessionLocal() as db:
        await db.execute(update(PluginConnection).where(PluginConnection.id == connection_id).values(**values))
        await db.commit()


def _token_calls(plugins: PluginHarness) -> int:
    return sum(1 for call in plugins.google.calls if call.path == "/token")


def _revoke_calls(plugins: PluginHarness) -> int:
    return sum(1 for call in plugins.google.calls if call.path == "/revoke")


# ---------------- Access tokens ----------------


async def test_valid_token_is_reused_and_an_expiring_one_refreshed(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    exchanged = _token_calls(plugins)
    first = await connections.access_token(row.id, expected_subject=ALICE_SUB)
    assert _token_calls(plugins) == exchanged
    await _set(row.id, access_expires_at=utcnow() + timedelta(seconds=30))  # within the 60 s margin
    second = await connections.access_token(row.id, expected_subject=ALICE_SUB)
    assert second != first and _token_calls(plugins) == exchanged + 1
    refreshed = await _row_of(alice.user_id)
    assert refreshed is not None and refreshed.refreshed_at is not None
    assert refreshed.access_expires_at is not None and refreshed.access_expires_at > utcnow() + timedelta(minutes=50)
    assert await connections.access_token(row.id, expected_subject=ALICE_SUB) == second


async def test_concurrent_refreshes_make_one_token_call(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _set(row.id, access_expires_at=utcnow() - timedelta(minutes=1))
    before = _token_calls(plugins)
    tokens = await asyncio.gather(*(connections.access_token(row.id, expected_subject=ALICE_SUB) for _ in range(5)))
    assert len(set(tokens)) == 1
    assert _token_calls(plugins) == before + 1


async def test_forced_refresh_after_a_401(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    cipher = crypto.token_cipher()
    assert cipher is not None
    await _set(row.id, access_token_enc=cipher.encrypt(row.user_id, "fake", "ya29.revoked-at-google"))
    access = await registry.live_access(row.user_id, fake)
    assert access.connection is not None
    messages = await _fetch(access.connection)  # 401, one forced refresh, then 200
    assert [message["id"] for message in messages] == ["m1", "m2"]


async def test_refresh_racing_an_account_switch_is_discarded(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _set(row.id, access_expires_at=utcnow() - timedelta(minutes=1))
    gate = plugins.google.token_gate = asyncio.Event()
    refresh = asyncio.create_task(connections.access_token(row.id, expected_subject=ALICE_SUB))
    while not any(call.path == "/token" and "refresh_token" in call.form_keys for call in plugins.google.calls):
        await asyncio.sleep(0.01)
    # Another process switches the connection to Bob's account while Alice's refresh is in flight.
    cipher = crypto.token_cipher()
    assert cipher is not None
    bob_refresh = cipher.encrypt(row.user_id, "fake", "1//bob-refresh")
    bob_access = cipher.encrypt(row.user_id, "fake", "ya29.bob-access")
    await _set(
        row.id,
        account_subject=BOB_SUB,
        account_email=BOB,
        refresh_token_enc=bob_refresh,
        access_token_enc=bob_access,
        access_expires_at=utcnow() + timedelta(hours=1),
    )
    gate.set()
    with pytest.raises(AccountChanged):
        await refresh
    after = await _row_of(alice.user_id)
    assert after is not None
    assert (after.account_subject, after.refresh_token_enc, after.access_token_enc) == (
        BOB_SUB,
        bob_refresh,
        bob_access,
    )
    assert await connections.access_token(row.id, expected_subject=BOB_SUB) == "ya29.bob-access"


async def test_expected_subject_mismatch_and_missing_connection(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    with pytest.raises(AccountChanged, match="account changed"):
        await connections.access_token(row.id, expected_subject=BOB_SUB)
    with pytest.raises(connections.NotConnected):
        await connections.access_token(row.id + 100, expected_subject=ALICE_SUB)


@pytest.mark.parametrize(
    ("testing", "age_days", "reason"), [(True, 6.5, "expired_testing"), (True, 2, "revoked"), (False, 6.5, "revoked")]
)
async def test_invalid_grant_needs_a_reconnect(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakePlugin,
    plugins: PluginHarness,
    create_user: USER,
    testing: bool,
    age_days: float,
    reason: str,
) -> None:
    monkeypatch.setattr(settings, "google_oauth_testing_mode", testing)
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _set(row.id, connected_at=utcnow() - timedelta(days=age_days), access_expires_at=utcnow())
    plugins.google.revoke_account(ALICE)
    with pytest.raises(ReconnectNeeded) as raised:
        await connections.access_token(row.id, expected_subject=ALICE_SUB)
    assert raised.value.reason == reason
    after = await _row_of(alice.user_id)
    assert after is not None and (after.status, after.status_reason) == ("reconnect_needed", reason)
    async with SessionLocal() as db:
        actions = (await db.scalars(select(PluginAuditEvent.action).where(PluginAuditEvent.origin == "system"))).all()
    assert actions == ["reconnect_needed"]
    with pytest.raises(ReconnectNeeded):  # no further token call
        before = _token_calls(plugins)
        await connections.access_token(row.id, expected_subject=ALICE_SUB)
    assert _token_calls(plugins) == before


async def test_testing_mode_expiry_without_a_token_call(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    status = (await alice.get("/api/plugins")).json()[0]["connection"]
    assert status["refresh_expires_at"] is not None
    await _set(row.id, connected_at=utcnow() - timedelta(days=7, minutes=1))
    before = _token_calls(plugins)
    listed = (await alice.get("/api/plugins")).json()[0]["connection"]
    assert (listed["status"], listed["status_reason"]) == ("reconnect_needed", "expired_testing")
    assert listed["status_message"].startswith("Google ended this connection")
    with pytest.raises(ReconnectNeeded):
        await connections.access_token(row.id, expected_subject=ALICE_SUB)
    assert _token_calls(plugins) == before


async def test_temporary_failures_leave_the_status(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _set(row.id, access_expires_at=utcnow())
    before = _token_calls(plugins)
    plugins.google.token_failures = [503, 500]
    with pytest.raises(PluginUnavailable, match="unreachable"):
        await connections.access_token(row.id, expected_subject=ALICE_SUB)
    assert _token_calls(plugins) == before + 2  # retried once
    plugins.google.token_failures = [500]
    assert await connections.access_token(row.id, expected_subject=ALICE_SUB)  # the retry worked
    after = await _row_of(alice.user_id)
    assert after is not None and after.status == "connected"


async def test_rejected_client_is_unavailable_and_logged_once(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, caplog: pytest.LogCaptureFixture
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    await _set(row.id, access_expires_at=utcnow())
    plugins.google.token_failures = [401, 401]
    for _ in range(2):
        with pytest.raises(PluginUnavailable, match="tell the admin"):
            await connections.access_token(row.id, expected_subject=ALICE_SUB)
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1 and "rejected the fake OAuth client" in errors[0].getMessage()
    after = await _row_of(alice.user_id)
    assert after is not None and after.status == "connected"


# ---------------- Disconnecting ----------------


async def test_disconnect_revokes_and_deletes(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> None:
    alice = await create_user("alice")
    await _connected(plugins, alice)
    response = await alice.delete("/api/plugins/fake/connection")
    assert response.status_code == 204
    assert await _row_of(alice.user_id) is None
    grant = plugins.google.grant(ALICE)
    assert grant is not None and grant.revoked
    assert fake.events == [f"disconnect:{ALICE_SUB}"]
    assert (await alice.delete("/api/plugins/fake/connection")).status_code == 204  # nothing to do
    assert _revoke_calls(plugins) == 1


async def test_disconnect_works_while_off_and_when_revoking_fails(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakePlugin,
    plugins: PluginHarness,
    admin_client: ApiClient,
    create_user: USER,
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    await _connected(plugins, alice, ALICE)
    await _connected(plugins, bob, BOB)
    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": False})
    plugins.google.revoke_account(ALICE)  # the revoke will fail (invalid_token)
    assert (await alice.delete("/api/plugins/fake/connection")).status_code == 204
    assert await _row_of(alice.user_id) is None
    monkeypatch.setattr(settings, "token_encryption_key", crypto.Fernet.generate_key().decode())
    assert (await bob.delete("/api/plugins/fake/connection")).status_code == 204  # tokens unreadable: no revoke
    assert await _row_of(bob.user_id) is None
    assert plugins.google.active_grant(BOB) is not None


async def test_shared_account_is_revoked_only_when_the_last_connection_goes(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice, carol = await create_user("alice"), await create_user("carol")
    await _connected(plugins, alice, ALICE)
    carol_row = await _connected(plugins, carol, ALICE)
    assert (await alice.delete("/api/plugins/fake/connection")).status_code == 204
    assert _revoke_calls(plugins) == 0 and plugins.google.active_grant(ALICE) is not None
    await _set(carol_row.id, access_expires_at=utcnow())
    assert await connections.access_token(carol_row.id, expected_subject=ALICE_SUB)  # carol's Gmail still works
    assert (await carol.delete("/api/plugins/fake/connection")).status_code == 204
    assert _revoke_calls(plugins) == 1 and plugins.google.active_grant(ALICE) is None


async def test_deleting_a_user_removes_their_rows_and_revokes_unless_shared(
    fake: FakePlugin, plugins: PluginHarness, admin_client: ApiClient, create_user: USER
) -> None:
    alice, carol, dave = await create_user("alice"), await create_user("carol"), await create_user("dave")
    await _connected(plugins, alice, ALICE)
    await _connected(plugins, carol, ALICE)
    await _connected(plugins, dave, BOB)
    assert (await admin_client.delete(f"/api/admin/users/{alice.user_id}")).status_code == 204
    assert await _row_of(alice.user_id) is None
    assert plugins.google.active_grant(ALICE) is not None  # carol shares it
    assert (await admin_client.delete(f"/api/admin/users/{dave.user_id}")).status_code == 204
    assert plugins.google.active_grant(BOB) is None
    async with SessionLocal() as db:
        users = set((await db.scalars(select(PluginAuditEvent.user_id))).all())
    assert users == {carol.user_id}


async def test_disconnect_all_revokes_each_account_once(
    fake: FakePlugin, plugins: PluginHarness, admin_client: ApiClient, create_user: USER
) -> None:
    alice, carol, dave = await create_user("alice"), await create_user("carol"), await create_user("dave")
    await _connected(plugins, alice, ALICE)
    await _connected(plugins, carol, ALICE)
    await _connected(plugins, dave, BOB)
    response = await admin_client.post("/api/admin/plugins/fake/disconnect-all")
    assert response.json() == {"disconnected": 3}
    assert _revoke_calls(plugins) == 2
    assert plugins.google.active_grant(ALICE) is None and plugins.google.active_grant(BOB) is None
    for user in (alice, carol, dave):
        assert await _row_of(user.user_id) is None
    assert sorted(fake.events) == sorted([f"disconnect:{ALICE_SUB}"] * 2 + [f"disconnect:{BOB_SUB}"])


# ---------------- Use in chat and the list ----------------


async def test_use_in_chat_switch(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> None:
    alice = await create_user("alice")
    assert (await alice.patch("/api/plugins/fake/connection", json={"chat_enabled": False})).status_code == 404
    await _connected(plugins, alice)
    response = await alice.patch("/api/plugins/fake/connection", json={"chat_enabled": False})
    assert response.status_code == 200 and response.json()["chat_enabled"] is False
    assert (await alice.get("/api/plugins")).json()[0]["connection"]["chat_enabled"] is False


async def test_plugin_list_rules(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakePlugin,
    plugins: PluginHarness,
    admin_client: ApiClient,
    create_user: USER,
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    await _connected(plugins, alice)
    listed = (await bob.get("/api/plugins")).json()
    assert len(listed) == 1 and listed[0]["connection"] is None  # never another user's connection
    assert listed[0]["testing_mode"] is True and listed[0]["import_max_items"] == settings.gmail_import_max_items
    assert listed[0]["enabled"] is True and listed[0]["auth_type"] == "oauth2"

    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": False})
    [off] = (await bob.get("/api/plugins")).json()  # off: still listed (the catalog)
    assert (off["configured"], off["enabled"], off["connection"]) == (True, False, None)
    mine = (await alice.get("/api/plugins")).json()
    assert len(mine) == 1 and mine[0]["enabled"] is False  # still listed, so they can disconnect
    assert mine[0]["connection"]["account_email"] == ALICE

    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
    monkeypatch.setattr(settings, "google_oauth_testing_mode", False)
    status = (await alice.get("/api/plugins")).json()[0]
    assert status["testing_mode"] is False and status["connection"]["refresh_expires_at"] is None

    monkeypatch.setattr(settings, "google_oauth_client_id", "")  # not configured: listed as such
    [unconfigured] = (await bob.get("/api/plugins")).json()
    assert (unconfigured["configured"], unconfigured["enabled"], unconfigured["connection"]) == (False, False, None)


async def test_touch_writes_at_most_once_a_minute(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    connection_id = await insert_connection(alice.user_id)
    await connections.touch(connection_id)
    first = (await _row_of(alice.user_id)).last_used_at  # type: ignore[union-attr]
    assert first is not None
    await connections.touch(connection_id)
    assert (await _row_of(alice.user_id)).last_used_at == first  # type: ignore[union-attr]


async def test_concurrent_forced_refreshes_make_one_token_call(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    row = await _connected(plugins, alice)
    before = _token_calls(plugins)
    tokens = await asyncio.gather(
        *(connections.access_token(row.id, expected_subject=ALICE_SUB, force_refresh=True) for _ in range(4))
    )
    assert len(set(tokens)) == 1 and _token_calls(plugins) == before + 1


# ---------------- Plugin routes ----------------


async def test_require_maps_access_problems_to_403_and_409(
    fake: FakePlugin, plugins: PluginHarness, admin_client: ApiClient, create_user: USER
) -> None:
    alice = await create_user("alice")
    dependency = registry.require("fake", "draft")

    async def status_of() -> int | tuple[str, ...]:
        async with SessionLocal() as db:
            user = await db.get(User, alice.user_id)
            assert user is not None
            try:
                found_user, access = await dependency(user=user, db=db)
            except HTTPException as exc:
                return exc.status_code
            assert found_user.id == alice.user_id
            return tuple(sorted(access.usable))

    assert await status_of() == 409  # not connected
    plugins.google.grant_scopes = {READ}
    await _connected(plugins, alice)
    assert await status_of() == 409  # draft not granted
    plugins.google.grant_scopes = None
    row = await _connected(plugins, alice)
    assert await status_of() == ("draft", "read", "send")
    await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"draft": False}})
    assert await status_of() == 403  # the capability is off
    await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"draft": True}})
    await connections.mark_reconnect_needed(row.id, "scopes_missing")
    assert await status_of() == 409
    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": False})
    assert await status_of() == 403
    with registry.use_plugins([]):
        assert await status_of() == 404


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AccountChanged("Your connected Fake Mail account changed. Ask again."), 409),
        (ReconnectNeeded("revoked"), 409),
        (connections.NotConnected("gone"), 409),
        (PluginUnavailable("busy"), 503),
        (PluginDisabled("off"), 403),
        (PluginError("bad request"), 400),
    ],
)
async def test_plugin_errors_escaping_a_route_never_answer_401(error: PluginError, status: int) -> None:
    handler = fastapi_app.exception_handlers[PluginError]
    response = await handler(None, error)  # type: ignore[arg-type]
    assert response.status_code == status
    assert json.loads(bytes(response.body)) == {"detail": str(error)}
