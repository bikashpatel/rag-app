"""Connecting an account (docs/briefs/plugins-gmail.md, sections 5.1 and 5.2): connect's checks and the authorize
URL, the callback's checks and results, storing (encrypted), reconnects, account switches, scopes and shared Google
accounts. Google is the fake of tests/fake_google.py."""

import hashlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from conftest import TEST_ORIGIN, ApiClient, PluginHarness, oauth_result
from fake_google import CLIENT_ID, IDENTITY_SCOPES
from fake_plugin import COMPOSE, READ, SEND, FakePlugin
from sqlalchemy import select

from app.db import SessionLocal
from app.main import _RedactPluginQuery
from app.models import PluginAuditEvent, PluginConnection
from app.plugins import connections, registry
from app.redis_client import get_redis

USER = Callable[..., Awaitable[ApiClient]]
ALICE, BOB = "alice@example.test", "bob@example.test"
ALICE_SUB, BOB_SUB = "100000000000000000001", "100000000000000000002"


@pytest.fixture
async def fake(plugins: PluginHarness, admin_client: ApiClient) -> AsyncIterator[FakePlugin]:
    plugin = FakePlugin()
    with registry.use_plugins([plugin]):
        response = await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
        assert response.status_code == 200, response.text
        yield plugin


async def _connection(user_id: int | None) -> PluginConnection | None:
    async with SessionLocal() as db:
        return await db.scalar(select(PluginConnection).where(PluginConnection.user_id == user_id))


async def _audit(user_id: int | None) -> list[tuple[str, str, str]]:
    async with SessionLocal() as db:
        rows = await db.execute(
            select(PluginAuditEvent.action, PluginAuditEvent.outcome, PluginAuditEvent.origin)
            .where(PluginAuditEvent.user_id == user_id)
            .order_by(PluginAuditEvent.id)
        )
        return [tuple(row) for row in rows.tuples()]  # type: ignore[misc]


def _params(url: str) -> dict[str, str]:
    return dict(httpx.URL(url).params)


# ---------------- Connect ----------------


async def test_connect_builds_the_authorize_url_and_sets_the_nonce_cookie(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    response = await plugins.start(alice, "fake")
    assert response.status_code == 200, response.text
    url = response.json()["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    params = _params(url)
    assert set(params) == {
        "response_type",
        "client_id",
        "redirect_uri",
        "scope",
        "state",
        "code_challenge",
        "code_challenge_method",
        "access_type",
        "prompt",
        "include_granted_scopes",
    }
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == f"{TEST_ORIGIN}/api/plugins/fake/oauth/callback"
    assert params["scope"] == f"openid email {READ} {COMPOSE}"  # draft's compose scope covers send
    assert (params["code_challenge_method"], params["access_type"], params["prompt"]) == ("S256", "offline", "consent")
    assert params["include_granted_scopes"] == "true"
    assert len(params["state"]) >= 43 and len(params["code_challenge"]) == 43

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("rag_oauth_fake=")
    lowered = cookie.lower()
    for attribute in ("httponly", "max-age=600", "path=/api/plugins/fake/oauth/", "samesite=lax"):
        assert attribute in lowered
    assert "secure" not in lowered  # COOKIE_SECURE is off in tests

    key = "plugin:oauth:" + hashlib.sha256(params["state"].encode()).hexdigest()
    assert 590 <= await get_redis().ttl(key) <= 600
    assert params["state"] not in (await get_redis().get(key) or "")  # stored under its hash only


@pytest.mark.parametrize(
    ("switches", "scope"),
    [
        ({"read": True, "draft": True, "send": True}, f"openid email {READ} {COMPOSE}"),
        ({"read": True, "draft": False, "send": True}, f"openid email {READ} {SEND}"),
        ({"read": False, "draft": True, "send": False}, f"openid email {COMPOSE}"),
        ({"read": False, "draft": True, "send": True}, f"openid email {COMPOSE}"),
        ({"read": True, "draft": False, "send": False}, f"openid email {READ}"),
    ],
)
async def test_requested_scopes_follow_the_admin_switches(
    fake: FakePlugin,
    plugins: PluginHarness,
    admin_client: ApiClient,
    create_user: USER,
    switches: dict[str, bool],
    scope: str,
) -> None:
    response = await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": switches})
    assert response.status_code == 200, response.text
    assert " ".join(response.json()["requested_scopes"]) == scope
    alice = await create_user("alice")
    started = await plugins.start(alice, "fake")
    assert _params(started.json()["authorize_url"])["scope"] == scope


async def test_connect_with_some_capabilities(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> None:
    alice = await create_user("alice")
    response = await plugins.start(alice, "fake", capabilities=["send"])
    assert _params(response.json()["authorize_url"])["scope"] == f"openid email {SEND}"


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        (None, 400),
        ("null", 400),
        ("http://evil.test", 400),
        ("http://testserver:8080", 400),
        ("https://testserver", 400),
        ("http://TestServer/", 200),
        ("http://testserver:80", 200),
    ],
)
async def test_connect_needs_an_allowed_origin(
    fake: FakePlugin, create_user: USER, origin: str | None, status: int
) -> None:
    alice = await create_user("alice")
    headers = {"Origin": origin} if origin is not None else {}
    response = await alice.post("/api/plugins/fake/connect", headers=headers)
    assert response.status_code == status, response.text
    if status == 400:
        assert response.json()["detail"] == (
            "Connect from http://testserver: this address isn't in PUBLIC_BASE_URLS."
        )


async def test_connect_refusals(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakePlugin,
    plugins: PluginHarness,
    admin_client: ApiClient,
    create_user: USER,
) -> None:
    alice = await create_user("alice")
    origin = {"Origin": TEST_ORIGIN}
    assert (await alice.post("/api/plugins/nope/connect", headers=origin)).status_code == 404
    for capabilities in (["write"], []):
        response = await alice.post("/api/plugins/fake/connect", json={"capabilities": capabilities}, headers=origin)
        assert response.status_code == 422
    await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"send": False}})
    refused = await alice.post("/api/plugins/fake/connect", json={"capabilities": ["send"]}, headers=origin)
    assert refused.status_code == 422  # turned off by the admin
    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": False})
    off = await alice.post("/api/plugins/fake/connect", headers=origin)
    assert (off.status_code, off.json()["detail"]) == (403, "Fake Mail is turned off.")
    monkeypatch.setattr("app.config.settings.google_oauth_client_secret", "")
    missing = await alice.post("/api/plugins/fake/connect", headers=origin)
    assert (missing.status_code, missing.json()["detail"]) == (409, "Fake Mail isn't set up yet. Ask the admin.")


async def test_connect_is_rate_limited(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> None:
    alice = await create_user("alice")
    statuses = [(await plugins.start(alice, "fake")).status_code for _ in range(11)]
    assert statuses == [200] * 10 + [429]


async def test_connect_needs_a_session(fake: FakePlugin, client: ApiClient) -> None:
    response = await client.post("/api/plugins/fake/connect", headers={"Origin": TEST_ORIGIN})
    assert response.status_code == 401


# ---------------- Callback ----------------


async def test_happy_path_stores_encrypted_tokens(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    alice = await create_user("alice")
    started = await plugins.start(alice, "fake")
    state = _params(started.json()["authorize_url"])["state"]
    location = await plugins.consent(started.json()["authorize_url"])
    code = _params(location)["code"]
    response = await alice.get(location)
    assert response.headers["location"] == f"{TEST_ORIGIN}/?plugin=fake&oauth=connected"
    cleared = response.headers["set-cookie"].lower()
    assert cleared.startswith("rag_oauth_fake=") and "max-age=0" in cleared
    assert "path=/api/plugins/fake/oauth/" in cleared

    row = await _connection(alice.user_id)
    assert row is not None
    assert (row.account_email, row.account_subject, row.status) == (ALICE, ALICE_SUB, "connected")
    assert row.scopes == sorted([*IDENTITY_SCOPES, READ, COMPOSE])
    grant = plugins.google.grant(ALICE)
    assert grant is not None
    refresh = next(iter(grant.refresh_tokens))
    access = next(iter(grant.access_tokens))
    assert refresh not in row.refresh_token_enc and access not in (row.access_token_enc or "")
    assert await connections.access_token(row.id, expected_subject=ALICE_SUB) == access
    assert await _audit(alice.user_id) == [("connect", "ok", "ui")]
    for secret in (code, state, refresh, access):
        assert secret not in caplog.text

    listed = (await alice.get("/api/plugins")).json()
    assert listed[0]["connection"]["account_email"] == ALICE
    assert {item["id"]: item["granted"] for item in listed[0]["capabilities"]} == {
        "read": True,
        "draft": True,
        "send": True,
    }


async def test_callback_works_without_the_session_cookie(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, new_client: Callable[[], ApiClient]
) -> None:
    alice = await create_user("alice")
    started = await plugins.start(alice, "fake")
    location = await plugins.consent(started.json()["authorize_url"])
    browser = new_client()  # only the nonce cookie, as on the navigation back from Google
    for cookie in alice.cookies.jar:
        if cookie.name == "rag_oauth_fake":
            browser.cookies.jar.set_cookie(cookie)
    assert oauth_result(await browser.get(location)) == "connected"
    assert await _connection(alice.user_id) is not None


async def test_state_is_one_time(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> None:
    alice = await create_user("alice")
    started = await plugins.start(alice, "fake")
    location = await plugins.consent(started.json()["authorize_url"])
    assert oauth_result(await alice.get(location)) == "connected"
    assert oauth_result(await alice.get(location)) == "expired"
    assert oauth_result(await alice.get("/api/plugins/fake/oauth/callback?code=x")) == "expired"
    assert oauth_result(await alice.get("/api/plugins/fake/oauth/callback?state=unknown&code=x")) == "expired"


async def test_nonce_cookie_must_match(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, new_client: Callable[[], ApiClient]
) -> None:
    alice = await create_user("alice")
    started = await plugins.start(alice, "fake")
    location = await plugins.consent(started.json()["authorize_url"])
    victim = new_client()  # another browser: no nonce cookie
    assert oauth_result(await victim.get(location)) == "invalid"

    started = await plugins.start(alice, "fake")
    location = await plugins.consent(started.json()["authorize_url"])
    forged = new_client()
    forged.cookies.set("rag_oauth_fake", "forged", domain="testserver.local", path="/api/plugins/fake/oauth/")
    assert oauth_result(await forged.get(location)) == "invalid"
    assert await _connection(alice.user_id) is None


async def test_state_of_another_plugin_is_invalid(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    started = await plugins.start(alice, "fake")
    location = await plugins.consent(started.json()["authorize_url"])
    query = httpx.URL(location).query.decode()
    assert oauth_result(await alice.get(f"/api/plugins/gmail/oauth/callback?{query}")) == "invalid"


async def test_denied_and_provider_errors(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, caplog: pytest.LogCaptureFixture
) -> None:
    alice = await create_user("alice")
    plugins.google.deny_next = True
    assert oauth_result(await plugins.connect(alice, "fake")) == "denied"
    started = await plugins.start(alice, "fake")
    state = _params(started.json()["authorize_url"])["state"]
    response = await alice.get(f"/api/plugins/fake/oauth/callback?state={state}&error=server_error%0Aforged")
    assert oauth_result(response) == "error"
    assert "\nforged" not in caplog.text and "with error server_error" in caplog.text  # one sanitized line
    started = await plugins.start(alice, "fake")
    state = _params(started.json()["authorize_url"])["state"]
    assert oauth_result(await alice.get(f"/api/plugins/fake/oauth/callback?state={state}")) == "error"  # no code
    assert await _connection(alice.user_id) is None


async def test_token_exchange_failure_is_an_error(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> None:
    alice = await create_user("alice")
    plugins.google.token_failures = [400]
    assert oauth_result(await plugins.connect(alice, "fake")) == "error"
    plugins.google.token_failures = [500]
    assert oauth_result(await plugins.connect(alice, "fake")) == "error"
    assert await _connection(alice.user_id) is None


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-client.apps.googleusercontent.com"},
        {"iss": "https://evil.test"},
        {"exp": 1_000_000_000},
        {"email": None},
        {"email_verified": False},
        {"sub": None},
    ],
)
async def test_bad_id_token_is_an_error(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, claims: dict[str, object]
) -> None:
    alice = await create_user("alice")
    plugins.google.id_token_claims = claims
    assert oauth_result(await plugins.connect(alice, "fake")) == "error"
    assert await _connection(alice.user_id) is None


async def test_expiry_within_the_clock_skew_is_accepted(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    plugins.google.id_token_claims = {"exp": int(time.time()) - 60}
    assert oauth_result(await plugins.connect(alice, "fake")) == "connected"


async def test_no_capability_granted_is_no_access_and_revoked(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    plugins.google.grant_scopes = set()
    assert oauth_result(await plugins.connect(alice, "fake")) == "no_access"
    assert await _connection(alice.user_id) is None
    assert plugins.google.revocations == 1
    grant = plugins.google.grant(ALICE)
    assert grant is not None and grant.revoked


async def test_partial_grant_connects_without_the_missing_capability(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    plugins.google.grant_scopes = {READ}
    assert oauth_result(await plugins.connect(alice, "fake")) == "connected"
    listed = (await alice.get("/api/plugins")).json()[0]
    assert {item["id"]: item["granted"] for item in listed["capabilities"]} == {
        "read": True,
        "draft": False,
        "send": False,
    }


async def test_compose_only_grant_with_identity_urls_gives_draft_and_send(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    plugins.google.grant_scopes = {COMPOSE}
    assert oauth_result(await plugins.connect(alice, "fake")) == "connected"
    row = await _connection(alice.user_id)
    assert row is not None
    assert row.scopes == sorted([*IDENTITY_SCOPES, COMPOSE])
    assert "email" not in row.scopes  # Google's token lists identity scopes as URLs
    async with SessionLocal() as db:
        from app.models import User

        user = await db.get(User, alice.user_id)
        assert user is not None
        access = await registry.access_for(db, user, fake)
    assert access.usable == {"draft", "send"}


async def test_include_granted_scopes_keeps_earlier_capabilities(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    assert oauth_result(await plugins.connect(alice, "fake", capabilities=["read"])) == "connected"
    started = await plugins.start(alice, "fake", capabilities=["draft"])
    params = _params(started.json()["authorize_url"])
    assert params["scope"] == f"openid email {COMPOSE}"
    assert params["login_hint"] == ALICE  # a reconnect
    location = await plugins.consent(started.json()["authorize_url"])
    assert oauth_result(await alice.get(location)) == "connected"
    row = await _connection(alice.user_id)
    assert row is not None and {READ, COMPOSE} <= set(row.scopes)


async def test_user_deactivated_or_password_changed_in_between_is_invalid(
    fake: FakePlugin, plugins: PluginHarness, admin_client: ApiClient, create_user: USER
) -> None:
    for change in ({"is_active": False}, {"password": "a-new-password-123"}):
        user = await create_user()
        started = await plugins.start(user, "fake")
        location = await plugins.consent(started.json()["authorize_url"])
        assert (await admin_client.patch(f"/api/admin/users/{user.user_id}", json=change)).status_code == 200
        assert oauth_result(await user.get(location)) == "invalid"
        assert await _connection(user.user_id) is None


async def test_plugin_disabled_in_between(
    fake: FakePlugin, plugins: PluginHarness, admin_client: ApiClient, create_user: USER
) -> None:
    alice = await create_user("alice")
    started = await plugins.start(alice, "fake")
    location = await plugins.consent(started.json()["authorize_url"])
    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": False})
    assert oauth_result(await alice.get(location)) == "disabled"
    assert await _connection(alice.user_id) is None


async def test_reconnect_to_the_same_account_keeps_the_row_and_revokes_nothing(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    assert oauth_result(await plugins.connect(alice, "fake")) == "connected"
    first = await _connection(alice.user_id)
    assert first is not None
    await connections.mark_reconnect_needed(first.id, "revoked")
    assert oauth_result(await plugins.connect(alice, "fake")) == "connected"
    second = await _connection(alice.user_id)
    assert second is not None
    assert second.id == first.id and (second.status, second.status_reason) == ("connected", None)
    assert second.refresh_token_enc != first.refresh_token_enc
    assert second.connected_at > first.connected_at
    assert plugins.google.revocations == 0 and fake.events == []
    grant = plugins.google.grant(ALICE)
    assert grant is not None and len(grant.refresh_tokens) == 2 and not grant.revoked


async def test_another_account_replaces_the_connection_and_revokes_the_old_grant(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice = await create_user("alice")
    assert oauth_result(await plugins.connect(alice, "fake", account=ALICE)) == "connected"
    first = await _connection(alice.user_id)
    assert oauth_result(await plugins.connect(alice, "fake", account=BOB)) == "connected"
    second = await _connection(alice.user_id)
    assert first is not None and second is not None
    assert (second.id, second.account_subject, second.account_email) == (first.id, BOB_SUB, BOB)
    assert fake.events == [f"disconnect:{ALICE_SUB}"]
    old = plugins.google.grant(ALICE)
    assert old is not None and old.revoked
    assert plugins.google.active_grant(BOB) is not None
    assert await _audit(alice.user_id) == [("connect", "ok", "ui"), ("connect", "ok", "ui")]


async def test_switching_away_from_a_shared_account_doesnt_revoke_it(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice, carol = await create_user("alice"), await create_user("carol")
    assert oauth_result(await plugins.connect(alice, "fake", account=ALICE)) == "connected"
    assert oauth_result(await plugins.connect(carol, "fake", account=ALICE)) == "connected"
    assert oauth_result(await plugins.connect(alice, "fake", account=BOB)) == "connected"
    assert plugins.google.active_grant(ALICE) is not None  # carol still uses it
    carol_row = await _connection(carol.user_id)
    assert carol_row is not None
    plugins.google.expire_access_tokens()
    assert await connections.access_token(carol_row.id, expected_subject=ALICE_SUB, force_refresh=True)


async def test_no_access_for_a_shared_account_doesnt_revoke_the_others_grant(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER
) -> None:
    alice, carol = await create_user("alice"), await create_user("carol")
    assert oauth_result(await plugins.connect(alice, "fake", account=ALICE)) == "connected"
    plugins.google.grant_scopes = set()
    plugins.google.union_scopes = False
    assert oauth_result(await plugins.connect(carol, "fake", account=ALICE)) == "no_access"
    assert plugins.google.revocations == 0
    row = await _connection(alice.user_id)
    assert row is not None
    assert await connections.access_token(row.id, expected_subject=ALICE_SUB, force_refresh=True)


async def test_callback_of_an_unknown_plugin(plugins: PluginHarness, client: ApiClient) -> None:
    response = await client.get("/api/plugins/nope/oauth/callback?state=x&code=y")
    assert oauth_result(response) == "expired"
    assert response.headers["location"].startswith(f"{TEST_ORIGIN}/?plugin=nope&")


def test_access_log_redacts_the_callback_query() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/plugins/gmail/oauth/callback?state=s3cret&code=4/c0de", "1.1", 303),
        None,
    )
    assert _RedactPluginQuery().filter(record)
    message = record.getMessage()
    assert "s3cret" not in message and "c0de" not in message
    assert "/api/plugins/gmail/oauth/callback?[redacted]" in message
    other = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s %s %s", ("a", "GET", "/api/documents?x=1"), None
    )
    _RedactPluginQuery().filter(other)
    assert other.getMessage() == "a GET /api/documents?x=1"


def test_access_log_redacts_every_plugin_query() -> None:
    """A Gmail search (or any later plugin route) with a query string never puts it in uvicorn's access log."""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/plugins/gmail/threads?q=from%3Aalice%40corp.com+salary&max_results=20", "1.1",
         405),
        None,
    )
    assert _RedactPluginQuery().filter(record)
    message = record.getMessage()
    assert "alice" not in message and "%40" not in message and "salary" not in message
    assert '"GET /api/plugins/gmail/threads?[redacted] HTTP/1.1" 405' in message
    listing = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s %s %s", ("a", "GET", "/api/plugins?q=salary"), None
    )
    _RedactPluginQuery().filter(listing)
    assert listing.getMessage() == "a GET /api/plugins?[redacted]"
    lookalike = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s %s %s", ("a", "GET", "/api/pluginsx?y=1"), None
    )
    _RedactPluginQuery().filter(lookalike)
    assert lookalike.getMessage() == "a GET /api/pluginsx?y=1"
