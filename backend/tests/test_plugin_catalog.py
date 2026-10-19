"""GET /plugins as a catalog (docs/briefs/ui-shell-settings.md, 4.8 and U6): every registered plugin with `configured`,
`enabled` and the caller's own connection, whatever its state, while chat still offers tools only to a connected user
of an enabled plugin with "Use in chat" on."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from conftest import ApiClient, FakeLLM, PluginHarness, chat, oauth_result
from fake_plugin import NOTE_CONNECT, FakePlugin, fake_api_router

from app.config import settings
from app.plugins import connections, registry
from app.plugins.gmail import tools as gmail_tools

USER = Callable[..., Awaitable[ApiClient]]
ALICE = "alice@example.test"


@pytest.fixture
async def fake(
    plugins: PluginHarness, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FakePlugin]:
    """The FakePlugin registered and configured but turned off; only plugin tools are offered in chat."""
    plugin = FakePlugin()
    plugins.app.include_router(fake_api_router(plugins.google))
    monkeypatch.setattr(settings, "chat_file_tools", False)
    with registry.use_plugins([plugin]):
        yield plugin


async def listed(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/plugins")
    assert response.status_code == 200, response.text
    [info] = response.json()
    return info


def state(info: dict[str, Any]) -> tuple[bool, bool, str | None]:
    connection = info["connection"]
    return info["configured"], info["enabled"], connection["account_email"] if connection else None


async def offers_tools(client: ApiClient, fake_llm: FakeLLM) -> tuple[bool, str]:
    """Whether a chat turn offered the plugin's tools, and its system prompt."""
    response, _ = await chat(client, "Any mail?", document_ids=[])
    assert response.status_code == 200, response.text
    call = fake_llm.chat_calls[-1]
    names = [tool["function"]["name"] for tool in call.get("tools") or []]
    return "fake_lookup" in names, call["messages"][0]["content"]


async def set_enabled(admin: ApiClient, enabled: bool) -> None:
    response = await admin.patch("/api/admin/plugins/fake", json={"enabled": enabled})
    assert response.status_code == 200, response.text


async def test_an_empty_registry_lists_nothing(create_user: USER) -> None:
    alice = await create_user("alice")

    response = await alice.get("/api/plugins")

    assert (response.status_code, response.json()) == (200, [])


async def test_every_state_is_listed_and_only_the_last_offers_tools(
    fake: FakePlugin,
    plugins: PluginHarness,
    admin_client: ApiClient,
    create_user: USER,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")

    # Configured, turned off (no plugin_settings row yet): connect 403
    info = await listed(alice)
    assert state(info) == (True, False, None)
    assert {"id", "name", "description", "capabilities", "testing_mode"} <= set(info)
    offered, prompt = await offers_tools(alice, fake_llm)
    assert not offered and "Fake Mail" not in prompt
    assert (await plugins.start(alice, "fake")).status_code == 403

    # Not configured: listed as such, connect 409
    with monkeypatch.context() as patched:
        patched.setattr(settings, "google_oauth_client_id", "")
        assert state(await listed(alice)) == (False, False, None)
        assert (await plugins.start(alice, "fake")).status_code == 409

    # Enabled, not connected: the connect note only
    await set_enabled(admin_client, True)
    assert state(await listed(alice)) == (True, True, None)
    offered, prompt = await offers_tools(alice, fake_llm)
    assert not offered and NOTE_CONNECT in prompt

    # Enabled and connected: tools; "Use in chat" off: none
    assert oauth_result(await plugins.connect(alice, "fake", account=ALICE)) == "connected"
    assert state(await listed(alice)) == (True, True, ALICE)
    assert state(await listed(bob)) == (True, True, None)  # never another user's connection
    assert (await offers_tools(alice, fake_llm))[0] is True
    await alice.patch("/api/plugins/fake/connection", json={"chat_enabled": False})
    offered, prompt = await offers_tools(alice, fake_llm)
    assert not offered and NOTE_CONNECT not in prompt
    await alice.patch("/api/plugins/fake/connection", json={"chat_enabled": True})

    # Turned on and connected, but no longer configured: no tools, no note
    with monkeypatch.context() as patched:
        patched.setattr(settings, "google_oauth_client_id", "")
        assert state(await listed(alice)) == (False, False, ALICE)
        offered, prompt = await offers_tools(alice, fake_llm)
        assert not offered and "Fake Mail" not in prompt

    # Connected, turned off: listed with the connection, no tools, no note
    await set_enabled(admin_client, False)
    assert state(await listed(alice)) == (True, False, ALICE)
    offered, prompt = await offers_tools(alice, fake_llm)
    assert not offered and "Fake Mail" not in prompt


async def test_a_connected_user_of_a_plugin_that_is_no_longer_configured_can_disconnect(
    fake: FakePlugin,
    plugins: PluginHarness,
    admin_client: ApiClient,
    create_user: USER,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = await create_user("alice")
    await set_enabled(admin_client, True)
    assert oauth_result(await plugins.connect(alice, "fake", account=ALICE)) == "connected"
    monkeypatch.setattr(settings, "google_oauth_client_secret", "")

    assert state(await listed(alice)) == (False, False, ALICE)
    offered, prompt = await offers_tools(alice, fake_llm)
    assert not offered and "Fake Mail" not in prompt
    assert (await alice.delete("/api/plugins/fake/connection")).status_code == 204

    assert state(await listed(alice)) == (False, False, None)  # still in the catalog


def test_messages_point_at_settings() -> None:
    for message in (
        gmail_tools.NOTE_CONNECT,
        gmail_tools.note_reconnect("revoked"),
        connections._GONE,
    ):
        assert "Settings → Plugins & data" in message
    assert "Plugins menu" in gmail_tools.NOTE_CONNECT
