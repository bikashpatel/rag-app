"""Admin -> Plugins (docs/briefs/plugins-gmail.md, sections 4.5, 6 and 15.5): the plugin list and its switches, the
activity report, and the request-check Test."""

from collections.abc import AsyncIterator, Awaitable, Callable

import litellm
import pytest
from conftest import TEST_ORIGIN, ApiClient, FakeLLM, PluginHarness, intent_answer, oauth_result
from fake_plugin import COMPOSE, READ, SEND, FakePlugin, insert_connection
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import PluginAuditEvent, UsageEvent
from app.plugins import audit, connections, registry

USER = Callable[..., Awaitable[ApiClient]]


@pytest.fixture
async def fake(plugins: PluginHarness) -> AsyncIterator[FakePlugin]:
    plugin = FakePlugin()
    with registry.use_plugins([plugin]):
        yield plugin


async def _model_id(admin: ApiClient, kind: str) -> int:
    models = (await admin.get("/api/admin/models")).json()
    return next(model["id"] for model in models if model["kind"] == kind and model["enabled"])


async def test_plugin_shape(fake: FakePlugin, admin_client: ApiClient) -> None:
    response = await admin_client.get("/api/admin/plugins")
    assert response.status_code == 200
    [plugin] = response.json()
    assert plugin == {
        "id": "fake",
        "name": "Fake Mail",
        "description": "Fake mail for the framework's tests.",
        "auth_type": "oauth2",
        "supports_import": True,
        "configured": True,
        "missing_settings": [],
        "test_endpoints": False,
        "enabled": False,
        "capabilities": [
            {
                "id": "read",
                "name": "Read",
                "description": "Read fake mail.",
                "scopes": [READ],
                "enabled": True,
                "note": None,
            },
            {
                "id": "draft",
                "name": "Drafts",
                "description": "Make fake drafts.",
                "scopes": [COMPOSE],
                "enabled": True,
                "note": "Drafts can send, too.",
            },
            {
                "id": "send",
                "name": "Send",
                "description": "Send fake mail.",
                "scopes": [SEND],
                "enabled": True,
                "note": None,
            },
        ],
        "requested_scopes": ["openid", "email", READ, COMPOSE],
        "redirect_uris": [f"{TEST_ORIGIN}/api/plugins/fake/oauth/callback"],
        "testing_mode": True,
        "connections": {"connected": 0, "reconnect_needed": 0, "old_key": 0},
        "activity": {"month": plugin["activity"]["month"], "actions": []},
    }


async def test_missing_settings_and_test_endpoints(
    monkeypatch: pytest.MonkeyPatch, fake: FakePlugin, admin_client: ApiClient
) -> None:
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "public_base_urls", "ftp://nope, http://x/path")
    monkeypatch.setattr(settings, "google_oauth_token_url", "http://fake-google.test:8099/token")
    [plugin] = (await admin_client.get("/api/admin/plugins")).json()
    assert plugin["configured"] is False
    assert plugin["missing_settings"] == ["GOOGLE_OAUTH_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY", "PUBLIC_BASE_URLS"]
    assert plugin["test_endpoints"] is True and plugin["redirect_uris"] == []
    response = await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Set GOOGLE_OAUTH_CLIENT_SECRET, TOKEN_ENCRYPTION_KEY, PUBLIC_BASE_URLS in .env first."
    )


async def test_redirect_uris_follow_public_base_urls(
    monkeypatch: pytest.MonkeyPatch, fake: FakePlugin, admin_client: ApiClient
) -> None:
    monkeypatch.setattr(
        settings, "public_base_urls", "http://localhost:8080/, https://Abc-Def.trycloudflare.com:443,bad url,"
    )
    [plugin] = (await admin_client.get("/api/admin/plugins")).json()
    assert plugin["redirect_uris"] == [
        "http://localhost:8080/api/plugins/fake/oauth/callback",
        "https://abc-def.trycloudflare.com/api/plugins/fake/oauth/callback",
    ]


async def test_switches(fake: FakePlugin, admin_client: ApiClient, create_user: USER) -> None:
    on = await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
    assert on.status_code == 200 and on.json()["enabled"] is True
    unknown = await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"write": True}})
    assert unknown.status_code == 422
    bad_id = await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"Bad-Id": True}})
    assert bad_id.status_code == 422
    all_off = {"read": False, "draft": False, "send": False}
    refused = await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": all_off})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Turn on at least one capability, or turn the plugin off."
    partial = await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"draft": False}})
    body = partial.json()
    assert [item["enabled"] for item in body["capabilities"]] == [True, False, True]
    assert body["requested_scopes"] == ["openid", "email", READ, SEND]
    off = await admin_client.patch("/api/admin/plugins/fake", json={"enabled": False, "capabilities": all_off})
    assert off.status_code == 200 and off.json()["enabled"] is False  # all off is fine while the plugin is off
    assert (await admin_client.patch("/api/admin/plugins/nope", json={"enabled": True})).status_code == 404
    user = await create_user("mallory")
    assert (await user.patch("/api/admin/plugins/fake", json={"enabled": True})).status_code == 403


async def test_connection_counts_never_show_accounts(
    fake: FakePlugin, plugins: PluginHarness, admin_client: ApiClient, create_user: USER
) -> None:
    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
    alice, bob = await create_user("alice"), await create_user("bob")
    assert oauth_result(await plugins.connect(alice, "fake")) == "connected"
    assert bob.user_id is not None
    stale = await insert_connection(bob.user_id, subject="300", email="bob@example.test")
    await connections.mark_reconnect_needed(stale, "revoked")
    response = await admin_client.get("/api/admin/plugins")
    assert response.json()[0]["connections"] == {"connected": 1, "reconnect_needed": 1, "old_key": 0}
    assert "alice@example.test" not in response.text and "bob@example.test" not in response.text


async def test_activity_by_month_and_user(fake: FakePlugin, admin_client: ApiClient, create_user: USER) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    assert alice.user_id is not None and bob.user_id is not None
    await audit.record(alice.user_id, "fake", "search", origin="chat", items=3)
    await audit.record(alice.user_id, "fake", "search", outcome="error", origin="chat")
    await audit.record(alice.user_id, "fake", "draft", outcome="refused", origin="chat")
    await audit.record(bob.user_id, "fake", "draft", origin="chat")
    await audit.record(bob.user_id, "other", "draft", origin="chat")
    report = (await admin_client.get("/api/admin/plugins/fake/activity")).json()
    assert report["totals"] == [
        {"action": "search", "ok": 1, "failed": 1, "refused": 0},
        {"action": "draft", "ok": 1, "failed": 0, "refused": 1},
    ]
    assert [(row["username"], row["actions"]) for row in report["by_user"]] == [
        (
            "alice",
            [
                {"action": "search", "ok": 1, "failed": 1, "refused": 0},
                {"action": "draft", "ok": 0, "failed": 0, "refused": 1},
            ],
        ),
        ("bob", [{"action": "draft", "ok": 1, "failed": 0, "refused": 0}]),
    ]
    listed = (await admin_client.get("/api/admin/plugins")).json()[0]
    assert listed["activity"]["actions"] == report["totals"]
    empty = (await admin_client.get("/api/admin/plugins/fake/activity?month=2020-01")).json()
    assert empty == {"month": "2020-01", "totals": [], "by_user": []}
    assert (await admin_client.get("/api/admin/plugins/fake/activity?month=2020-13")).status_code == 422
    assert (await admin_client.get("/api/admin/plugins/fake/activity?month=jan")).status_code == 422
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(PluginAuditEvent)) == 5


# ---------------- Request-check Test ----------------


async def _usage_events() -> int:
    async with SessionLocal() as db:
        return int(await db.scalar(select(func.count()).select_from(UsageEvent)) or 0)


async def test_request_check_ok_for_a_draft_only(admin_client: ApiClient, fake_llm: FakeLLM) -> None:
    model_id = await _model_id(admin_client, "chat")
    fake_llm.intent_reply = intent_answer("draft")
    before = await _usage_events()
    response = await admin_client.post("/api/admin/plugins/request-check/test", json={"model_id": model_id})
    body = response.json()
    assert response.status_code == 200 and body["ok"] is True
    assert body["message"].startswith("OK: the model answered the request check correctly (")
    assert body["answer"] == {"file": False, "image": False, "draft": True, "send": False, "import": False}
    assert await _usage_events() == before
    [call] = fake_llm.intent_calls
    assert "Please draft a short reply to Alice's last email" in call["messages"][1]["content"]
    assert "<earlier_user_message>" not in call["messages"][1]["content"]
    assert "<previous_reply>" not in call["messages"][1]["content"]
    assert fake_llm.chat_calls == []


@pytest.mark.parametrize(
    ("reply", "finish", "error", "message"),
    [
        (
            intent_answer("draft", "send"),
            "stop",
            None,
            "The model answered, but not as expected: file false, image false, draft true, send true, import false. "
            "Side effects may be refused or allowed wrongly with this model.",
        ),
        ("Sure! I would draft it.", "stop", None, "The model didn't answer with JSON: Sure! I would draft it."),
        (
            "",
            "length",
            None,
            "The model used up its output limit before answering (thinking counts). Raise its max output tokens or "
            "choose another model.",
        ),
        (
            "",
            "stop",
            litellm.AuthenticationError(message="bad key", llm_provider="anthropic", model="x"),
            "The model provider rejected the API key. Check the provider key in .env.",
        ),
    ],
)
async def test_request_check_failures(
    admin_client: ApiClient, fake_llm: FakeLLM, reply: str, finish: str, error: Exception | None, message: str
) -> None:
    model_id = await _model_id(admin_client, "chat")
    fake_llm.intent_reply, fake_llm.intent_finish_reason, fake_llm.intent_error = reply, finish, error
    body = (await admin_client.post("/api/admin/plugins/request-check/test", json={"model_id": model_id})).json()
    assert body["ok"] is False and body["message"] == message


async def test_request_check_model_errors(admin_client: ApiClient, fake_llm: FakeLLM) -> None:
    missing = await admin_client.post("/api/admin/plugins/request-check/test", json={"model_id": 999_999})
    assert missing.status_code == 404
    embedding = await _model_id(admin_client, "embedding")
    wrong = await admin_client.post("/api/admin/plugins/request-check/test", json={"model_id": embedding})
    assert (wrong.status_code, wrong.json()["detail"]) == (422, "Choose a chat model.")
    assert fake_llm.intent_calls == []
