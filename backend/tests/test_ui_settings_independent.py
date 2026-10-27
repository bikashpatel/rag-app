"""Independent tests of the account features of docs/briefs/ui-shell-settings.md, written from SPEC.md and the brief
rather than from the implementation: preferences, the sign-in history, logging out other sessions, the usage page,
the data export, deleting every chat, and the plugin catalog users see (with Gmail's chat tools still gated)."""

import asyncio
import io
import json
import logging
import os
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    CLIENT_SECRET,
    TEST_TOKEN_KEY,
    USER_PASSWORD,
    ApiClient,
    FakeLLM,
    PluginHarness,
    ScriptedCall,
    chat,
    ingest,
    tool_call,
    txt_bytes,
    upload,
)
from fake_google import CLIENT_ID
from fake_plugin import FakePlugin, insert_connection
from fastapi import Depends, FastAPI
from gmail_support import Gmail, gmail, pending_sends, update_connection  # noqa: F401
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.config import Settings, settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.models import (
    Conversation,
    GeneratedDocument,
    GeneratedImage,
    GmailPendingSend,
    LLMModel,
    LoginEvent,
    Message,
    MessageFile,
    MessagePluginItem,
    PluginConnection,
    PluginSetting,
    UsageEvent,
    User,
    UserPreference,
    utcnow,
)
from app.plugins import registry
from app.plugins.gmail import compose, sends
from app.plugins.gmail import tools as gmail_tools
from app.plugins.gmail.mime import OutgoingEmail
from app.plugins.gmail.plugin import GmailPlugin
from app.plugins.gmail.schemas import GmailSendCard
from app.security import create_access_token
from app.services import account, export, llm

CreateUser = Callable[..., Awaitable[ApiClient]]

UTC = timezone.utc
DEFAULTS = {
    "theme": "system",
    "accent": "clay",
    "chat_font_size": "default",
    "enter_to_send": True,
    "default_model_id": None,
}
ACCENTS = ("clay", "ocean", "sage", "plum", "slate")
GMAIL_TOOLS = {
    "gmail_search",
    "gmail_read_message",
    "gmail_read_thread",
    "gmail_import",
    "gmail_create_draft",
    "gmail_propose_send",
}


# ---------------- Helpers ----------------


class ClientAt(ApiClient):
    """An ApiClient whose requests arrive from `host` (what the server sees as the client address)."""

    def __init__(self, app: FastAPI, host: str) -> None:
        httpx.AsyncClient.__init__(
            self, transport=httpx.ASGITransport(app=app, client=(host, 50123)), base_url="http://testserver"
        )
        self.user_id = None
        self.username = None
        self.password = None


@pytest.fixture
async def client_at(app: FastAPI) -> AsyncIterator[Callable[[str], ApiClient]]:
    opened: list[ApiClient] = []

    def factory(host: str) -> ApiClient:
        found = ClientAt(app, host)
        opened.append(found)
        return found

    yield factory
    for found in opened:
        await found.aclose()


async def request_with_bearer(app: FastAPI, token: str, method: str, path: str) -> httpx.Response:
    """One request in a fresh client that has no cookies, authenticated by the Bearer header only."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as fresh:
        return await fresh.request(method, path, headers={"Authorization": f"Bearer {token}"}, json=None)


def cookie_token(client: httpx.AsyncClient) -> str:
    token = client.cookies.get(settings.cookie_name)
    assert token, "no session cookie"
    return token


async def models_by_name(admin: ApiClient) -> dict[str, dict[str, Any]]:
    response = await admin.get("/api/admin/models")
    assert response.status_code == 200, response.text
    return {model["display_name"]: model for model in response.json()}


async def preferences_of(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/me/preferences")
    assert response.status_code == 200, response.text
    return response.json()


async def stored_preferences(user_id: int | None) -> Any:
    async with SessionLocal() as session:
        row = await session.get(UserPreference, user_id)
        return None if row is None else row.data


async def account_of(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/me/account")
    assert response.status_code == 200, response.text
    return response.json()


async def login_rows(user_id: int | None = None) -> list[LoginEvent]:
    async with SessionLocal() as session:
        query = select(LoginEvent).order_by(LoginEvent.id)
        if user_id is not None:
            query = query.where(LoginEvent.user_id == user_id)
        return list(await session.scalars(query))


async def token_version(user_id: int | None) -> int:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.token_version


async def count(model: Any, *where: Any) -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def add_conversation(
    user_id: int | None,
    title: str = "A chat",
    *,
    updated_at: datetime | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> int:
    """A conversation inserted directly, with a question and an answer unless `messages` (Message fields) are given."""
    async with SessionLocal() as session:
        conversation = Conversation(user_id=user_id, title=title)
        if updated_at is not None:
            conversation.created_at = conversation.updated_at = updated_at
        session.add(conversation)
        await session.flush()
        for fields in messages or [
            {"role": "user", "content": f"Question in {title}"},
            {"role": "assistant", "content": f"Answer in {title}", "model_name": "Claude Haiku 5.5"},
        ]:
            session.add(Message(conversation_id=conversation.id, **fields))
        await session.commit()
        return conversation.id


async def answer_id(conversation_id: int) -> int:
    async with SessionLocal() as session:
        found = await session.scalar(
            select(Message.id).where(Message.conversation_id == conversation_id, Message.role == "assistant")
        )
        assert found is not None
        return found


async def add_card(message_id: int, plugin_id: str, card_type: str, data: dict[str, Any]) -> int:
    async with SessionLocal() as session:
        item = MessagePluginItem(message_id=message_id, plugin_id=plugin_id, card_type=card_type, data=data)
        session.add(item)
        await session.commit()
        return item.id


async def add_usage(*events: UsageEvent) -> None:
    async with SessionLocal() as session:
        session.add_all(events)
        await session.commit()


def usage_event(
    user_id: int | None,
    kind: str,
    when: datetime,
    *,
    model: str = "anthropic/claude-haiku-5-5",
    prompt: int = 0,
    completion: int = 0,
    total: int | None = None,
    cost: float = 0.0,
) -> UsageEvent:
    return UsageEvent(
        user_id=user_id,
        model_name=model,
        kind=kind,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion if total is None else total,
        cost_usd=cost,
        created_at=when,
    )


def system_prompt(call: dict[str, Any]) -> str:
    return str(call["messages"][0]["content"])


def offered_tools(call: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in call.get("tools") or []}


@pytest.fixture(autouse=True)
def fresh_export_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The export keeps its per-user locks and build slots for the process's life: each test starts with free ones."""
    monkeypatch.setattr(export, "_user_locks", export.WeakValueDictionary())
    monkeypatch.setattr(export, "_building", asyncio.Semaphore(export.MAX_BUILDING))


# ================ Preferences ================


async def test_a_new_user_gets_the_defaults_in_the_login_response_me_and_preferences(
    create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    await create_user("alice", login=False)
    alice = new_client()

    login = await alice.login("alice", USER_PASSWORD)

    assert login.status_code == 200, login.text
    assert login.json()["preferences"] == DEFAULTS
    assert (await alice.get("/api/auth/me")).json()["preferences"] == DEFAULTS
    assert await preferences_of(alice) == DEFAULTS
    assert await stored_preferences(alice.user_id) is None  # reading never writes a row


async def test_saved_preferences_follow_the_account_to_a_new_session(
    create_user: CreateUser, new_client: Callable[[], ApiClient], admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    haiku = (await models_by_name(admin_client))["Claude Haiku 5.5"]["id"]
    wanted = {"theme": "dark", "accent": "ocean", "chat_font_size": "large", "enter_to_send": False}

    response = await alice.patch("/api/me/preferences", json={**wanted, "default_model_id": haiku})

    assert response.status_code == 200, response.text
    assert response.json() == {**wanted, "default_model_id": haiku}
    laptop = new_client()
    login = await laptop.login("alice", USER_PASSWORD)
    assert login.json()["preferences"] == {**wanted, "default_model_id": haiku}
    assert (await laptop.get("/api/auth/me")).json()["preferences"] == {**wanted, "default_model_id": haiku}


@pytest.mark.parametrize("accent", ACCENTS)
async def test_every_curated_accent_is_accepted(create_user: CreateUser, accent: str) -> None:
    alice = await create_user("alice")

    response = await alice.patch("/api/me/preferences", json={"accent": accent})

    assert (response.status_code, response.json()["accent"]) == (200, accent)


@pytest.mark.parametrize(
    "accent",
    [
        "#c96442",
        "#fff",
        "red",
        "Clay",
        "CLAY",
        " clay",
        "clay ",
        "clay;",
        "clay; background: url(https://evil.test/x.png)",
        "var(--accent)",
        "rgb(201, 100, 66)",
        "hsl(15 55% 52%)",
        "expression(alert(1))",
        "</style><script>alert(1)</script>",
        "",
        0,
        ["clay"],
        {"preset": "clay"},
    ],
)
async def test_raw_css_and_anything_but_a_preset_accent_is_refused(create_user: CreateUser, accent: Any) -> None:
    alice = await create_user("alice")

    response = await alice.patch("/api/me/preferences", json={"accent": accent})

    assert response.status_code == 422, response.text
    assert await preferences_of(alice) == DEFAULTS
    assert await stored_preferences(alice.user_id) is None


@pytest.mark.parametrize(
    "body",
    [
        {"theme": "Dark"},
        {"theme": "auto"},
        {"theme": None},
        {"chat_font_size": "x-large"},
        {"chat_font_size": 16},
        {"chat_font_size": "16px"},
        {"enter_to_send": None},
        {"enter_to_send": "true"},
        {"enter_to_send": "off"},
        {"enter_to_send": 1},
        {"enter_to_send": 0},
        {"default_model_id": True},
        {"default_model_id": "3"},
        {"default_model_id": 3.0},
        {"default_model_id": -1},
        {"default_model_id": 2**31},
        {"user_id": 1, "theme": "dark"},
        {"css": "body { display: none }"},
    ],
)
async def test_invalid_values_wrong_types_and_unknown_fields_are_refused_and_change_nothing(
    create_user: CreateUser, body: dict[str, Any]
) -> None:
    alice = await create_user("alice")
    assert (await alice.patch("/api/me/preferences", json={"theme": "light"})).status_code == 200

    response = await alice.patch("/api/me/preferences", json=body)

    assert response.status_code == 422, response.text
    assert await preferences_of(alice) == {**DEFAULTS, "theme": "light"}


@pytest.mark.parametrize("body", [None, [], "dark", 5])
async def test_a_body_that_isnt_an_object_is_refused(create_user: CreateUser, body: Any) -> None:
    alice = await create_user("alice")

    response = await alice.request(
        "PATCH", "/api/me/preferences", content=json.dumps(body), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422, response.text
    assert await stored_preferences(alice.user_id) is None


async def test_an_empty_patch_changes_nothing_and_returns_the_current_values(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    await alice.patch("/api/me/preferences", json={"accent": "plum"})

    response = await alice.patch("/api/me/preferences", json={})

    assert (response.status_code, response.json()) == (200, {**DEFAULTS, "accent": "plum"})


async def test_patches_merge_and_each_user_keeps_one_row(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    for body in ({"theme": "dark"}, {"accent": "sage"}, {"chat_font_size": "small"}, {"theme": "light"}):
        assert (await alice.patch("/api/me/preferences", json=body)).status_code == 200

    assert await preferences_of(alice) == {**DEFAULTS, "theme": "light", "accent": "sage", "chat_font_size": "small"}
    assert await count(UserPreference, UserPreference.user_id == alice.user_id) == 1


async def test_a_restricted_user_may_default_only_to_an_allowed_model_and_it_follows_the_admins_changes(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    models = await models_by_name(admin_client)
    haiku, sonnet, opus = (models[name]["id"] for name in ("Claude Haiku 5.5", "Claude Sonnet 5.5", "Claude Opus 5.5"))
    alice = await create_user("alice", restrict_models=True, allowed_model_ids=[haiku])

    refused = await alice.patch("/api/me/preferences", json={"default_model_id": opus})
    saved = await alice.patch("/api/me/preferences", json={"default_model_id": haiku})

    assert (refused.status_code, refused.json()) == (422, {"detail": "That model isn't available to you."})
    assert saved.json()["default_model_id"] == haiku
    changed = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"allowed_model_ids": [sonnet]})
    assert changed.status_code == 200, changed.text
    assert (await preferences_of(alice))["default_model_id"] is None
    assert (await alice.get("/api/auth/me")).json()["preferences"]["default_model_id"] is None
    assert (await stored_preferences(alice.user_id))["default_model_id"] == haiku  # kept for when it comes back
    await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"allowed_model_ids": [sonnet, haiku]})
    assert (await preferences_of(alice))["default_model_id"] == haiku
    await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"restrict_models": False})
    assert (await alice.patch("/api/me/preferences", json={"default_model_id": opus})).json()[
        "default_model_id"
    ] == opus


@pytest.mark.parametrize("name", ["OpenAI text-embedding-3-small", "Claude Sonnet 5.5 (vision)", "GPT Image 2"])
async def test_a_model_that_isnt_a_chat_model_cant_be_the_default(
    create_user: CreateUser, admin_client: ApiClient, name: str
) -> None:
    alice = await create_user("alice")
    model_id = (await models_by_name(admin_client))[name]["id"]

    response = await alice.patch("/api/me/preferences", json={"default_model_id": model_id})

    assert (response.status_code, response.json()) == (422, {"detail": "That model isn't available to you."})


async def test_a_default_model_that_is_disabled_or_deleted_reads_as_null(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    models = await models_by_name(admin_client)
    mini = models["GPT-5.4 mini"]["id"]
    alice = await create_user("alice")
    await alice.patch("/api/me/preferences", json={"default_model_id": mini, "theme": "dark"})

    assert (await admin_client.patch(f"/api/admin/models/{mini}", json={"enabled": False})).status_code == 200
    assert await preferences_of(alice) == {**DEFAULTS, "theme": "dark"}
    assert (await admin_client.patch(f"/api/admin/models/{mini}", json={"enabled": True})).status_code == 200
    assert (await preferences_of(alice))["default_model_id"] == mini
    assert (await admin_client.delete(f"/api/admin/models/{mini}")).status_code == 204
    assert await preferences_of(alice) == {**DEFAULTS, "theme": "dark"}
    # A theme change still works while the stored default points at a model that is gone.
    assert (await alice.patch("/api/me/preferences", json={"theme": "light"})).status_code == 200


async def test_users_never_see_or_change_each_others_preferences(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")

    await alice.patch("/api/me/preferences", json={"theme": "dark", "accent": "plum"})
    await bob.patch("/api/me/preferences", json={"chat_font_size": "large"})

    assert await preferences_of(alice) == {**DEFAULTS, "theme": "dark", "accent": "plum"}
    assert await preferences_of(bob) == {**DEFAULTS, "chat_font_size": "large"}
    assert (await bob.get("/api/auth/me")).json()["preferences"] == {**DEFAULTS, "chat_font_size": "large"}


async def test_the_env_admin_saves_preferences_like_anyone(admin_client: ApiClient) -> None:
    opus = (await models_by_name(admin_client))["Claude Opus 5.5"]["id"]

    response = await admin_client.patch("/api/me/preferences", json={"theme": "dark", "default_model_id": opus})

    assert response.json() == {**DEFAULTS, "theme": "dark", "default_model_id": opus}
    assert (await admin_client.get("/api/auth/me")).json()["preferences"]["theme"] == "dark"


async def test_preferences_need_a_valid_session(client: ApiClient, create_user: CreateUser, app: FastAPI) -> None:
    alice = await create_user("alice")
    old = cookie_token(alice)
    assert (await alice.post("/api/auth/logout-others")).status_code == 204

    assert (await client.get("/api/me/preferences")).status_code == 401
    assert (await client.patch("/api/me/preferences", json={"theme": "dark"})).status_code == 401
    assert (await request_with_bearer(app, old, "GET", "/api/me/preferences")).status_code == 401
    assert (await request_with_bearer(app, "not-a-token", "GET", "/api/me/preferences")).status_code == 401
    assert await stored_preferences(alice.user_id) is None


async def test_a_corrupted_row_reads_as_defaults_and_the_next_patch_repairs_it(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    async with SessionLocal() as session:
        session.add(UserPreference(user_id=alice.user_id, data=["dark", "plum"]))
        await session.commit()

    assert await preferences_of(alice) == DEFAULTS
    assert (await alice.get("/api/auth/me")).status_code == 200
    response = await alice.patch("/api/me/preferences", json={"accent": "slate"})
    assert (response.status_code, response.json()) == (200, {**DEFAULTS, "accent": "slate"})
    assert await stored_preferences(alice.user_id) == {"accent": "slate"}


async def test_stored_values_of_the_wrong_type_read_as_their_defaults(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    haiku = (await models_by_name(admin_client))["Claude Haiku 5.5"]["id"]
    async with SessionLocal() as session:
        session.add(
            UserPreference(
                user_id=alice.user_id,
                data={
                    "theme": "DARK",
                    "accent": "#ff0000",
                    "chat_font_size": "large",
                    "enter_to_send": "false",
                    "default_model_id": str(haiku),
                    "future_setting": {"kept": True},
                },
            )
        )
        await session.commit()

    assert await preferences_of(alice) == {**DEFAULTS, "chat_font_size": "large"}
    await alice.patch("/api/me/preferences", json={"theme": "dark"})
    stored = await stored_preferences(alice.user_id)
    assert stored["future_setting"] == {"kept": True} and stored["theme"] == "dark"


# ================ Sign-in history ================


@pytest.mark.parametrize(
    ("host", "ip", "private"),
    [
        ("81.2.69.160", "81.2.69.x", False),
        ("192.168.65.1", "192.168.65.x", True),
        ("10.20.30.40", "10.20.30.x", True),
        ("172.17.0.1", "172.17.0.x", True),
        ("127.0.0.1", "127.0.0.x", True),
        ("100.64.12.7", "100.64.12.x", True),
        ("169.254.10.1", "169.254.10.x", True),
        ("203.0.113.9", "203.0.113.x", True),
        ("2a00:1450:4001:82a::200e", "2a00:1450:4001::/48", False),
        ("2001:db8:85a3::8a2e:370:7334", "2001:db8:85a3::/48", True),
        ("fe80::1", "fe80::/48", True),
        ("fd12:3456:789a:1::1", "fd12:3456:789a::/48", True),
        ("::1", "::/48", True),
        ("::ffff:81.2.69.160", "81.2.69.x", False),
        ("::ffff:192.168.65.1", "192.168.65.x", True),
        ("testclient", None, False),
        ("unknown", None, False),
    ],
)
async def test_a_login_records_an_approximate_ip_and_whether_it_is_public(
    create_user: CreateUser, client_at: Callable[[str], ApiClient], host: str, ip: str | None, private: bool
) -> None:
    await create_user("alice", login=False)
    alice = client_at(host)

    assert (await alice.login("alice", USER_PASSWORD)).status_code == 200

    [event] = (await account_of(alice))["recent_logins"]
    assert (event["ip"], event["ip_private"], event["current"]) == (ip, private, True)
    [row] = await login_rows(alice.user_id)
    assert row.ip == ip
    if ip is not None:  # the full address is stored nowhere
        assert not any(host in str(getattr(row, column.key)) for column in LoginEvent.__table__.columns)


async def test_a_forwarded_for_header_doesnt_change_the_recorded_address(
    create_user: CreateUser, client_at: Callable[[str], ApiClient]
) -> None:
    await create_user("alice", login=False)
    alice = client_at("192.168.65.1")

    response = await alice.post(
        "/api/auth/login",
        json={"username": "alice", "password": USER_PASSWORD},
        headers={"X-Forwarded-For": "81.2.69.160", "X-Real-IP": "81.2.69.161", "Forwarded": "for=81.2.69.162"},
    )

    assert response.status_code == 200
    [row] = await login_rows(response.json()["id"])
    assert row.ip == "192.168.65.x"


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36",
            ("Chrome 141", "macOS", "desktop"),
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 "
            "Safari/605.1.15",
            ("Safari 26", "macOS", "desktop"),
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/26.0 Mobile/15E148 Safari/604.1",
            ("Safari 26", "iOS", "mobile"),
        ),
        (
            "Mozilla/5.0 (iPad; CPU OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 "
            "Mobile/15E148 Safari/604.1",
            ("Safari 26", "iPadOS", "tablet"),
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "CriOS/141.0.7390.41 Mobile/15E148 Safari/604.1",
            ("Chrome 141", "iOS", "mobile"),
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "FxiOS/143.0 Mobile/15E148 Safari/605.1.15",
            ("Firefox 143", "iOS", "mobile"),
        ),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0", ("Firefox 143", "Linux", "desktop")),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
            ("Firefox 143", "Windows", "desktop"),
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Safari/537.36 Edg/141.0.3537.57",
            ("Edge 141", "Windows", "desktop"),
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 "
            "Safari/537.36 OPR/124.0.0.0",
            ("Opera 124", "Windows", "desktop"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 "
            "Chrome/130.0.0.0 Mobile Safari/537.36",
            ("Samsung Internet 28", "Android", "mobile"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Mobile Safari/537.36",
            ("Chrome 141", "Android", "mobile"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 15; SM-X910) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Safari/537.36",
            ("Chrome 141", "Android", "tablet"),
        ),
        (
            "Mozilla/5.0 (X11; CrOS x86_64 16181.61.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Safari/537.36",
            ("Chrome 141", "ChromeOS", "desktop"),
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 "
            "Safari/537.36",
            ("Headless Chrome 141", "Linux", "desktop"),
        ),
        ("Mozilla/5.0 (Android 14; Mobile; rv:143.0) Gecko/143.0 Firefox/143.0", ("Firefox 143", "Android", "mobile")),
        ("Mozilla/5.0 (Android 14; Tablet; rv:143.0) Gecko/143.0 Firefox/143.0", ("Firefox 143", "Android", "tablet")),
        ("curl/8.7.1", ("curl 8", None, "unknown")),
        ("python-httpx/0.28.1", ("Python", None, "unknown")),
        ("python-requests/2.32.3", ("Python", None, "unknown")),
        ("", (None, None, "unknown")),
        (None, (None, None, "unknown")),
        ("\x00\x01<script>alert(1)</script>\x7f", (None, None, "unknown")),
        ("Chrome/abc Firefox/ Version/ Safari", (None, None, "unknown")),
        ("A" * 10_000, (None, None, "unknown")),
        ("x" * 600 + " (Windows NT 10.0) Chrome/141.0", (None, None, "unknown")),  # only the first 512 characters
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/141.0 " + "x" * 10_000, ("Chrome 141", "Windows", "desktop")),
    ],
)
def test_user_agents_are_parsed_into_browser_os_and_device(user_agent: str | None, expected: tuple[Any, ...]) -> None:
    parsed = account.parse_user_agent(user_agent)

    assert parsed == expected
    for value in parsed:
        assert value is None or (len(value) <= 64 and value.isascii() and value.isprintable())


@pytest.mark.parametrize(
    "user_agent",
    ["Chrome/" + "9" * 200, "Edg/" + "1" * 80 + " OPR/2", "Version/" + "7" * 99 + " Safari/1", "Firefox/0"],
)
def test_parsed_values_stay_short_printable_ascii(user_agent: str) -> None:
    for value in account.parse_user_agent(user_agent):
        assert value is None or (len(value) <= 64 and value.isascii() and value.isprintable())


async def test_the_raw_user_agent_is_never_stored_nor_logged(
    create_user: CreateUser, new_client: Callable[[], ApiClient], caplog: pytest.LogCaptureFixture
) -> None:
    await create_user("alice", login=False)
    alice = new_client()
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
        "Safari/537.36 Edg/141.0.3537.57 TrackingTag/zq81xv"
    )
    caplog.set_level(logging.DEBUG)

    response = await alice.post(
        "/api/auth/login", json={"username": "alice", "password": USER_PASSWORD}, headers={"User-Agent": user_agent}
    )
    alice.user_id = response.json()["id"]
    await account_of(alice)

    [row] = await login_rows(alice.user_id)
    assert (row.browser, row.os, row.device) == ("Edge 141", "Windows", "desktop")
    columns = [getattr(row, column.key) for column in LoginEvent.__table__.columns]
    assert not any(isinstance(value, str) and ("TrackingTag" in value or "Mozilla" in value) for value in columns)
    assert "TrackingTag" not in caplog.text and "zq81xv" not in caplog.text


async def test_a_user_agent_with_latin_1_bytes_still_signs_in(
    create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    await create_user("alice", login=False)
    alice = new_client()

    response = await alice.post(
        "/api/auth/login",
        json={"username": "alice", "password": USER_PASSWORD},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Firefox/143.0 caf\xe9".encode("latin-1")},
    )

    assert response.status_code == 200, response.text
    [row] = await login_rows(response.json()["id"])
    assert (row.browser, row.os) == ("Firefox 143", "Windows")


async def test_only_successful_logins_are_recorded(
    create_user: CreateUser, new_client: Callable[[], ApiClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    await create_user("alice", login=False)
    await create_user("carol", login=False, is_active=False)
    attacker = new_client()
    before = len(await login_rows())

    wrong = await attacker.login("alice", "not-the-password")
    unknown = await attacker.login("nobody", USER_PASSWORD)
    inactive = await attacker.login("carol", USER_PASSWORD)
    malformed = await attacker.post("/api/auth/login", json={"username": "alice"})
    monkeypatch.setattr(settings, "login_max_attempts", 2)
    for _ in range(2):
        await attacker.login("alice", "still-wrong")
    throttled = await attacker.login("alice", USER_PASSWORD)  # the right password, but throttled

    assert [wrong.status_code, unknown.status_code, inactive.status_code, malformed.status_code] == [401, 401, 401, 422]
    assert throttled.status_code == 429
    assert len(await login_rows()) == before  # the admin's own sign-in only


async def test_the_newest_20_sign_ins_are_kept_without_touching_other_users(
    create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    bob = await create_user("bob")
    await bob.login("bob", USER_PASSWORD)
    await create_user("alice", login=False)
    alice = new_client()
    for _ in range(25):
        assert (await alice.login("alice", USER_PASSWORD)).status_code == 200

    rows = await login_rows(alice.user_id)
    listed = (await account_of(alice))["recent_logins"]

    assert len(rows) == 20
    all_ids = [row.id for row in rows]
    assert [event["id"] for event in listed] == sorted(all_ids, reverse=True)
    assert listed[0]["current"] and not any(event["current"] for event in listed[1:])
    assert len(await login_rows(bob.user_id)) == 2


async def test_each_session_marks_its_own_sign_in_as_current(
    create_user: CreateUser, client_at: Callable[[str], ApiClient], app: FastAPI
) -> None:
    await create_user("alice", login=False)
    phone, laptop = client_at("81.2.69.10"), client_at("81.2.70.20")
    await phone.login("alice", USER_PASSWORD)
    await laptop.login("alice", USER_PASSWORD)

    on_phone, on_laptop = (await account_of(phone))["recent_logins"], (await account_of(laptop))["recent_logins"]

    assert [(event["ip"], event["current"]) for event in on_phone] == [("81.2.70.x", False), ("81.2.69.x", True)]
    assert [(event["ip"], event["current"]) for event in on_laptop] == [("81.2.70.x", True), ("81.2.69.x", False)]
    no_sid = create_access_token(phone.user_id or 0, await token_version(phone.user_id))
    response = await request_with_bearer(app, no_sid, "GET", "/api/me/account")
    assert response.status_code == 200
    assert not any(event["current"] for event in response.json()["recent_logins"])
    # A Bearer copy of a session's own token marks that sign-in.
    copied = await request_with_bearer(app, cookie_token(laptop), "GET", "/api/me/account")
    assert [event["current"] for event in copied.json()["recent_logins"]] == [True, False]


async def test_a_session_id_of_someone_elses_sign_in_marks_nothing(create_user: CreateUser, app: FastAPI) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    [bobs] = await login_rows(bob.user_id)
    forged = create_access_token(alice.user_id or 0, await token_version(alice.user_id), bobs.id)

    response = await request_with_bearer(app, forged, "GET", "/api/me/account")

    assert response.status_code == 200
    assert [event["id"] for event in response.json()["recent_logins"]] != [bobs.id]
    assert not any(event["current"] for event in response.json()["recent_logins"])


async def test_nobody_sees_another_users_sign_ins(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    for _ in range(3):
        await bob.login("bob", USER_PASSWORD)
        await alice.login("alice", USER_PASSWORD)

    alices = {event["id"] for event in (await account_of(alice))["recent_logins"]}
    bobs = {event["id"] for event in (await account_of(bob))["recent_logins"]}
    admins = {event["id"] for event in (await account_of(admin_client))["recent_logins"]}

    assert alices == {row.id for row in await login_rows(alice.user_id)} and len(alices) == 4
    assert bobs == {row.id for row in await login_rows(bob.user_id)} and len(bobs) == 4
    assert not alices & bobs and not admins & (alices | bobs)
    users = await admin_client.get("/api/admin/users")
    assert users.status_code == 200
    assert "recent_logins" not in users.text and "ip_private" not in users.text and "127.0.0.x" not in users.text


async def test_the_env_admins_sign_ins_are_recorded_and_its_password_is_managed_by_env(
    new_client: Callable[[], ApiClient], app: FastAPI
) -> None:
    first, second = new_client(), new_client()
    await first.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    await second.login(ADMIN_USERNAME, ADMIN_PASSWORD)

    info = await account_of(second)

    assert (info["username"], info["is_admin"], info["password_managed_by_env"]) == (ADMIN_USERNAME, True, True)
    assert [event["current"] for event in info["recent_logins"]] == [True, False]
    assert (await account_of(first))["recent_logins"][1]["current"] is True


async def test_the_account_view(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    await add_conversation(alice.user_id, "One")
    await add_conversation(alice.user_id, "Two")
    await add_conversation(bob.user_id, "Bob's")
    admin_view = next(
        user for user in (await admin_client.get("/api/admin/users")).json() if user["username"] == "alice"
    )

    info = await account_of(alice)

    assert set(info) == {
        "id",
        "username",
        "is_admin",
        "password_managed_by_env",
        "created_at",
        "last_login_at",
        "conversation_count",
        "recent_logins",
    }
    assert (info["id"], info["username"], info["is_admin"], info["password_managed_by_env"]) == (
        alice.user_id,
        "alice",
        False,
        False,
    )
    assert info["conversation_count"] == 2
    assert (info["created_at"], info["last_login_at"]) == (admin_view["created_at"], admin_view["last_login_at"])
    [event] = info["recent_logins"]
    assert set(event) == {"id", "created_at", "ip", "ip_private", "browser", "os", "device", "current"}
    last_login = datetime.fromisoformat(info["last_login_at"])
    assert abs(datetime.fromisoformat(event["created_at"]) - last_login) < timedelta(seconds=5)


async def test_change_password_and_logout_others_keep_this_sessions_sign_in_current(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    [own] = await login_rows(alice.user_id)

    changed = await alice.post(
        "/api/auth/change-password", json={"current_password": USER_PASSWORD, "new_password": "another-password-9"}
    )
    after_change = (await account_of(alice))["recent_logins"]
    ended = await alice.post("/api/auth/logout-others")
    after_logout = (await account_of(alice))["recent_logins"]

    assert (changed.status_code, ended.status_code) == (204, 204)
    assert [(event["id"], event["current"]) for event in after_change] == [(own.id, True)]
    assert [(event["id"], event["current"]) for event in after_logout] == [(own.id, True)]
    assert len(await login_rows(alice.user_id)) == 1  # re-issuing a cookie isn't a sign-in


async def test_the_account_view_needs_a_session(client: ApiClient) -> None:
    assert (await client.get("/api/me/account")).status_code == 401


async def test_deleting_a_user_deletes_their_sign_ins_and_preferences(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    await alice.patch("/api/me/preferences", json={"theme": "dark"})
    await bob.patch("/api/me/preferences", json={"theme": "light"})

    response = await admin_client.delete(f"/api/admin/users/{alice.user_id}")

    assert response.status_code == 204
    assert await login_rows(alice.user_id) == []
    assert await stored_preferences(alice.user_id) is None
    assert len(await login_rows(bob.user_id)) == 1 and await stored_preferences(bob.user_id) == {"theme": "light"}


# ================ Log out of other sessions ================


async def test_logout_others_ends_every_other_session_and_keeps_the_callers(
    create_user: CreateUser, new_client: Callable[[], ApiClient], app: FastAPI
) -> None:
    alice = await create_user("alice")
    [own] = await login_rows(alice.user_id)
    other_browser = new_client()
    await other_browser.login("alice", USER_PASSWORD)
    script = new_client()
    await script.login("alice", USER_PASSWORD)
    bearer = cookie_token(script)
    old_cookie = cookie_token(alice)
    version = await token_version(alice.user_id)

    response = await alice.post("/api/auth/logout-others")

    assert response.status_code == 204
    assert settings.cookie_name in response.cookies
    assert cookie_token(alice) != old_cookie
    assert await token_version(alice.user_id) == version + 1
    assert (await alice.get("/api/auth/me")).status_code == 200
    current = [event["id"] for event in (await account_of(alice))["recent_logins"] if event["current"]]
    assert current == [own.id]
    assert (await other_browser.get("/api/auth/me")).status_code == 401
    assert (await request_with_bearer(app, bearer, "GET", "/api/auth/me")).status_code == 401
    assert (await request_with_bearer(app, old_cookie, "GET", "/api/auth/me")).status_code == 401
    # The signed-out browser can sign in again.
    assert (await other_browser.login("alice", USER_PASSWORD)).status_code == 200


async def test_a_bearer_caller_gets_a_new_cookie_and_its_own_token_ends(create_user: CreateUser, app: FastAPI) -> None:
    alice = await create_user("alice")
    token = cookie_token(alice)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as script:
        response = await script.post("/api/auth/logout-others", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 204
        issued = response.cookies.get(settings.cookie_name)
        assert issued
        assert (await script.get("/api/auth/me")).status_code == 200  # by the new cookie

    assert (await request_with_bearer(app, token, "GET", "/api/auth/me")).status_code == 401
    assert (await request_with_bearer(app, issued, "GET", "/api/auth/me")).status_code == 200


async def test_logout_others_works_again_and_for_the_env_admin(
    admin_client: ApiClient, new_client: Callable[[], ApiClient], create_user: CreateUser
) -> None:
    other = new_client()
    await other.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    alice = await create_user("alice")
    admin_id = admin_client.user_id
    version = await token_version(admin_id)

    assert (await admin_client.post("/api/auth/logout-others")).status_code == 204
    assert (await admin_client.post("/api/auth/logout-others")).status_code == 204

    assert await token_version(admin_id) == version + 2
    assert (await admin_client.get("/api/admin/users")).status_code == 200
    assert (await other.get("/api/auth/me")).status_code == 401
    assert (await alice.get("/api/auth/me")).status_code == 200  # other users' sessions are theirs


async def test_logout_others_needs_a_session(client: ApiClient) -> None:
    assert (await client.post("/api/auth/logout-others")).status_code == 401
    client.cookies.set(settings.cookie_name, "garbage")
    assert (await client.post("/api/auth/logout-others")).status_code == 401


async def test_logout_others_isnt_throttled_and_leaves_the_login_counter_alone(
    create_user: CreateUser, new_client: Callable[[], ApiClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 3)
    alice = await create_user("alice")

    statuses = [(await alice.post("/api/auth/logout-others")).status_code for _ in range(8)]

    assert statuses == [204] * 8
    assert (await new_client().login("alice", USER_PASSWORD)).status_code == 200


async def test_a_session_ended_while_the_request_ran_gets_401_and_nothing_changes(
    create_user: CreateUser, app: FastAPI
) -> None:
    alice = await create_user("alice")
    version = await token_version(alice.user_id)

    async def reset_in_between(request: Request, db: AsyncSession = Depends(get_db)) -> User:
        """The real session check (sharing the route's database session), then an admin password reset."""
        user = await get_current_user(request, db)
        async with SessionLocal() as other:
            row = await other.get(User, user.id)
            assert row is not None
            row.token_version += 1
            await other.commit()
        return user

    app.dependency_overrides[get_current_user] = reset_in_between
    try:
        response = await alice.post("/api/auth/logout-others")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 401
    assert settings.cookie_name not in response.cookies
    assert await token_version(alice.user_id) == version + 1  # only the admin's reset


async def test_logout_others_leaves_plugin_connections_alone(
    plugins: PluginHarness, create_user: CreateUser, admin_client: ApiClient
) -> None:
    plugin = FakePlugin()
    with registry.use_plugins([plugin]):
        assert (await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})).status_code == 200
        alice = await create_user("alice")
        connection_id = await insert_connection(alice.user_id or 0)
        before = (await alice.get("/api/plugins")).json()[0]["connection"]

        assert (await alice.post("/api/auth/logout-others")).status_code == 204

        assert (await alice.get("/api/plugins")).json()[0]["connection"] == before
        async with SessionLocal() as session:
            row = await session.get(PluginConnection, connection_id)
            assert row is not None and row.status == "connected"
        assert plugin.events == []  # no disconnect hook ran


# ================ My usage ================

NOW = datetime(2026, 10, 18, 15, 30, tzinfo=UTC)
KATHMANDU = timezone(timedelta(hours=5, minutes=45))


@pytest.fixture
def pin_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    """Pins the usage page's clock (account.current_time) to NOW; call the result to pin another time."""

    def pin(now: datetime) -> None:
        monkeypatch.setattr(account, "current_time", lambda: now)

    pin(NOW)
    return pin


async def usage_of(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/me/usage")
    assert response.status_code == 200, response.text
    return response.json()


async def test_usage_counts_only_this_utc_months_events_of_the_caller(
    create_user: CreateUser, admin_client: ApiClient, pin_clock: Callable[[datetime], None]
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    sonnet = "anthropic/claude-sonnet-5-5"  # also a vision model: its chat model's name is shown
    await add_usage(
        usage_event(alice.user_id, "chat", datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=UTC), prompt=900, cost=9),
        usage_event(alice.user_id, "chat", datetime(2026, 10, 1, 3, 0, tzinfo=KATHMANDU), prompt=800, cost=8),
        usage_event(
            alice.user_id, "chat", datetime(2026, 10, 1, 0, 0, tzinfo=UTC), prompt=100, completion=20, cost=0.001
        ),
        # 05:30 in Kathmandu on the 2nd is 23:45 UTC on the 1st; 05:50 is 00:05 UTC on the 2nd.
        usage_event(
            alice.user_id, "chat", datetime(2026, 10, 2, 5, 30, tzinfo=KATHMANDU), prompt=50, completion=10, cost=0.0005
        ),
        usage_event(alice.user_id, "chat", datetime(2026, 10, 2, 5, 50, tzinfo=KATHMANDU), model=sonnet, prompt=7),
        usage_event(
            alice.user_id,
            "vision",
            datetime(2026, 10, 5, 9, tzinfo=UTC),
            model=sonnet,
            prompt=1500,
            completion=200,
            cost=0.02,
        ),
        usage_event(
            alice.user_id,
            "embedding",
            datetime(2026, 10, 5, 9, tzinfo=UTC),
            model="openai/text-embedding-3-small",
            prompt=30,
            cost=0.0000006,
        ),
        usage_event(
            alice.user_id,
            "image",
            datetime(2026, 10, 18, 15, tzinfo=UTC),
            model="openai/gpt-image-2",
            prompt=50,
            completion=272,
            cost=0.006,
        ),
        usage_event(alice.user_id, "chat", datetime(2026, 11, 1, 0, 0, tzinfo=UTC), prompt=700, cost=7),
        usage_event(bob.user_id, "chat", datetime(2026, 10, 3, tzinfo=UTC), prompt=5000, cost=5),
        usage_event(bob.user_id, "image", datetime(2026, 10, 3, tzinfo=UTC), model="openai/gpt-image-2", cost=5),
        usage_event(admin_client.user_id, "chat", datetime(2026, 10, 3, tzinfo=UTC), prompt=6000, cost=6),
    )

    usage = await usage_of(alice)

    assert (usage["month"], usage["period_start"], usage["resets_at"]) == (
        "2026-10",
        "2026-10-01T00:00:00Z",
        "2026-11-01T00:00:00Z",
    )
    assert usage["chat_tokens"] == 120 + 60 + 7
    assert usage["requests"] == 3
    assert usage["images"]["used"] == 1
    assert usage["vision_tokens"]["used"] == 1700
    assert usage["embedding_tokens"] == 30
    assert usage["cost_usd"] == {
        "total": round(0.001 + 0.0005 + 0.02 + 0.0000006 + 0.006, 6),
        "chat": 0.0015,
        "embedding": round(0.0000006, 6),
        "vision": 0.02,
        "image": 0.006,
    }
    days = {entry["date"]: entry for entry in usage["daily"]}
    assert list(days) == [f"2026-10-{n:02d}" for n in range(1, 19)]
    assert (days["2026-10-01"]["chat_tokens"], days["2026-10-01"]["requests"]) == (180, 2)
    assert (days["2026-10-02"]["chat_tokens"], days["2026-10-02"]["requests"]) == (7, 1)
    assert (days["2026-10-05"]["vision_tokens"], days["2026-10-05"]["embedding_tokens"]) == (1700, 30)
    assert days["2026-10-18"]["images"] == 1 and days["2026-10-18"]["cost_usd"] == 0.006
    assert days["2026-10-03"] == {
        "date": "2026-10-03",
        "chat_allowance": 0,
        "chat_tokens": 0,
        "requests": 0,
        "images": 0,
        "vision_tokens": 0,
        "embedding_tokens": 0,
        "cost_usd": 0.0,
    }
    assert sum(entry["chat_tokens"] for entry in usage["daily"]) == usage["chat_tokens"]
    assert round(sum(entry["cost_usd"] for entry in usage["daily"]), 6) == usage["cost_usd"]["total"]
    # The rows were written without allowance tokens (an answer's are in test_usage_allowance.py)
    rows = [{key: value for key, value in row.items() if key != "allowance_tokens"} for row in usage["by_model"]]
    assert rows == [
        {"model_name": "Claude Sonnet 5.5", "kind": "vision", "tokens": 1700, "requests": 1, "cost_usd": 0.02},
        {"model_name": "GPT Image 2", "kind": "image", "tokens": 322, "requests": 1, "cost_usd": 0.006},
        {"model_name": "Claude Haiku 5.5", "kind": "chat", "tokens": 180, "requests": 2, "cost_usd": 0.0015},
        {
            "model_name": "OpenAI text-embedding-3-small",
            "kind": "embedding",
            "tokens": 30,
            "requests": 1,
            "cost_usd": 0.000001,
        },
        {"model_name": "Claude Sonnet 5.5", "kind": "chat", "tokens": 7, "requests": 1, "cost_usd": 0.0},
    ]
    assert {entry["allowance_tokens"] for entry in usage["by_model"]} == {0}


async def test_a_deleted_models_usage_keeps_its_stored_name(
    create_user: CreateUser, admin_client: ApiClient, pin_clock: Callable[[datetime], None]
) -> None:
    alice = await create_user("alice")
    mini = (await models_by_name(admin_client))["GPT-5.4 mini"]
    await add_usage(usage_event(alice.user_id, "chat", NOW - timedelta(hours=1), model=mini["litellm_model"], prompt=9))
    assert (await usage_of(alice))["by_model"][0]["model_name"] == "GPT-5.4 mini"

    assert (await admin_client.delete(f"/api/admin/models/{mini['id']}")).status_code == 204

    [entry] = (await usage_of(alice))["by_model"]
    assert (entry["model_name"], entry["tokens"]) == ("openai/gpt-5.4-mini", 9)
    assert (await usage_of(alice))["chat_tokens"] == 9  # history stays


async def test_limits_come_from_the_users_settings_and_the_admin_has_none(
    create_user: CreateUser,
    admin_client: ApiClient,
    pin_clock: Callable[[datetime], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = await create_user(
        "alice",
        monthly_token_limit=5000,
        rate_limit_per_min=7,
        monthly_image_limit=6,
        vision_monthly_token_limit=12345,
        max_documents=9,
        max_storage_mb=40,
        max_upload_mb=3,
    )

    usage = await usage_of(alice)
    admin = await usage_of(admin_client)

    assert usage["chat_allowance"]["limit"] == 5000 and usage["images"]["limit"] == 6
    assert usage["vision_tokens"]["limit"] == 12345 and usage["rate_limit_per_min"] == 7
    assert {key: usage["documents"][key] for key in ("max_documents", "max_storage_mb", "max_upload_mb")} == {
        "max_documents": 9,
        "max_storage_mb": 40,
        "max_upload_mb": 3,
    }
    assert (admin["chat_allowance"]["limit"], admin["images"]["limit"], admin["vision_tokens"]["limit"]) == (0, 0, 0)
    assert admin["rate_limit_per_min"] == 0
    assert (admin["documents"]["max_documents"], admin["documents"]["max_storage_mb"]) == (0, 0)
    assert admin["documents"]["max_upload_mb"] == settings.max_upload_mb_hard
    # Without a value of her own her plan's limit applies.
    async with SessionLocal() as session:
        row = await session.get(User, alice.user_id)
        assert row is not None and row.monthly_image_limit is not None
        row.monthly_image_limit = None
        await session.commit()
    assert (await usage_of(alice))["images"]["limit"] == 60  # Professional's
    # The page and /auth/me agree on every limit.
    limits = (await alice.get("/api/auth/me")).json()["limits"]
    again = await usage_of(alice)
    assert (limits["monthly_token_limit"], limits["monthly_image_limit"], limits["rate_limit_per_min"]) == (
        again["chat_allowance"]["limit"],
        again["images"]["limit"],
        again["rate_limit_per_min"],
    )


async def test_documents_and_storage_count_only_what_the_user_owns(
    create_user: CreateUser, admin_client: ApiClient, pin_clock: Callable[[datetime], None]
) -> None:
    alice, bob = await create_user("alice", max_documents=5), await create_user("bob")
    first, second = txt_bytes("Alice's first file."), txt_bytes("Alice's second, longer file with more words.")
    assert (await upload(alice, "one.txt", first)).status_code == 201
    assert (await upload(alice, "two.txt", second)).status_code == 201
    assert (await upload(bob, "bob.txt", txt_bytes("Bob's file, which is not Alice's."))).status_code == 201
    shared = await upload(admin_client, "handbook.txt", txt_bytes("A shared handbook everyone can read."), "shared")
    assert shared.status_code == 201

    documents = (await usage_of(alice))["documents"]
    config = (await alice.get("/api/documents/config")).json()

    assert (documents["count"], documents["storage_bytes"]) == (2, len(first) + len(second))
    assert {
        key: documents[key] for key in ("count", "storage_bytes", "max_documents", "max_storage_mb", "max_upload_mb")
    } == {
        "count": config["document_count"],
        "storage_bytes": config["storage_bytes"],
        "max_documents": config["max_documents"],
        "max_storage_mb": config["max_storage_mb"],
        "max_upload_mb": config["max_upload_mb"],
    }
    assert (await usage_of(admin_client))["documents"]["count"] == 1  # the shared document is the admin's


@pytest.mark.parametrize(
    ("now", "month", "start", "resets", "days", "used"),
    [
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), "2026-12", "2026-12-01", "2027-01-01", 31, 11),
        (datetime(2027, 1, 1, 0, 0, tzinfo=UTC), "2027-01", "2027-01-01", "2027-02-01", 1, 22),
        (datetime(2028, 2, 29, 12, 0, tzinfo=UTC), "2028-02", "2028-02-01", "2028-03-01", 29, 0),
        # 05:00 on 1 January in Kathmandu is still 31 December in UTC.
        (datetime(2027, 1, 1, 5, 0, tzinfo=KATHMANDU), "2026-12", "2026-12-01", "2027-01-01", 31, 11),
    ],
)
async def test_the_month_and_its_reset_follow_utc(
    create_user: CreateUser,
    pin_clock: Callable[[datetime], None],
    now: datetime,
    month: str,
    start: str,
    resets: str,
    days: int,
    used: int,
) -> None:
    alice = await create_user("alice")
    pin_clock(now)
    await add_usage(
        usage_event(alice.user_id, "chat", datetime(2026, 12, 31, 23, 0, tzinfo=UTC), prompt=11),
        usage_event(alice.user_id, "chat", datetime(2027, 1, 1, 0, 0, tzinfo=UTC), prompt=22),
    )

    usage = await usage_of(alice)

    assert (usage["month"], usage["period_start"], usage["resets_at"]) == (
        month,
        f"{start}T00:00:00Z",
        f"{resets}T00:00:00Z",
    )
    assert [entry["date"] for entry in usage["daily"]] == [
        (date.fromisoformat(start) + timedelta(days=n)).isoformat() for n in range(days)
    ]
    assert usage["chat_tokens"] == used


async def test_usage_after_a_real_answer_matches_the_events_and_auth_me(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    response, _ = await chat(alice, "Hello there?", document_ids=[])
    assert response.status_code == 200

    usage = await usage_of(alice)
    me = (await alice.get("/api/auth/me")).json()["usage"]

    async with SessionLocal() as session:
        events = list(await session.scalars(select(UsageEvent).where(UsageEvent.user_id == alice.user_id)))
    chats = [event for event in events if event.kind == "chat"]
    assert usage["chat_tokens"] == sum(event.total_tokens for event in chats) == me["chat_tokens"] == 47
    allowance = sum(event.allowance_tokens for event in chats)
    assert usage["chat_allowance"]["used"] == me["chat_allowance_used"] == allowance == 84
    assert usage["requests"] == len(chats) == 1
    assert usage["images"]["used"] == me["images"] == 0
    assert usage["cost_usd"]["total"] == round(me["cost_usd"], 6)
    # Claude Opus 5.5 is seeded at $4 / $20 per million prompt / completion tokens: 42 and 5 tokens.
    assert usage["cost_usd"]["chat"] == round(42 * 4 / 1e6 + 5 * 20 / 1e6, 6)
    [model] = usage["by_model"]
    assert (model["model_name"], model["kind"], model["requests"]) == ("Claude Opus 5.5", "chat", 1)
    today = datetime.now(UTC).date().isoformat()
    assert usage["daily"][-1]["date"] == today and usage["daily"][-1]["chat_tokens"] == 47


async def test_usage_needs_a_session(client: ApiClient) -> None:
    assert (await client.get("/api/me/usage")).status_code == 401


# ================ Export ================


def unzip(response: httpx.Response) -> dict[str, bytes]:
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == len(set(names)), "duplicate entries"
        return {name: archive.read(name) for name in names}


async def export_of(client: ApiClient) -> httpx.Response:
    return await client.post("/api/me/export", json={})


def conversation_entries(entries: dict[str, bytes], conversation_id: int) -> tuple[str, dict[str, Any]]:
    """The Markdown transcript and the JSON file of one conversation."""
    names = sorted(name for name in entries if name.startswith(f"conversations/{conversation_id:06d}-"))
    assert len(names) == 2 and names[0].endswith(".json") and names[1].endswith(".md"), names
    return entries[names[1]].decode("utf-8"), json.loads(entries[names[0]])


def entry_text(entries: dict[str, bytes]) -> str:
    return "\n".join(data.decode("utf-8", errors="replace") for data in entries.values())


async def assert_export_free(user_id: int | None) -> None:
    """The user's export lock and both build slots are free."""
    assert user_id is not None and not export.user_lock(user_id).locked()
    for _ in range(export.MAX_BUILDING):
        await asyncio.wait_for(export._building.acquire(), 0.5)
    for _ in range(export.MAX_BUILDING):
        export._building.release()


async def test_the_export_holds_only_the_callers_data_even_with_shared_documents(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob-other")
    handbook = await ingest(
        admin_client, "handbook.txt", txt_bytes("The shared handbook says vacations last ten days."), "shared"
    )
    bobs = await ingest(bob, "bobfile.txt", txt_bytes("BOBPRIVATE notes about vacations."))
    alices = await ingest(alice, "alicefile.txt", txt_bytes("Alice's own notes about vacations."))
    await chat(bob, "BOBQUESTION: how long are vacations?")
    _, events = await chat(alice, "ALICEQUESTION: how long are vacations?")
    conversation_id = events[0][1]["conversation_id"]
    await alice.patch("/api/me/preferences", json={"theme": "dark", "accent": "sage"})

    response = await export_of(alice)
    entries = unzip(response)

    assert set(entries) == {
        "README.txt",
        "account.json",
        "billing.json",
        "documents.json",
        "conversations/index.json",
        *(name for name in entries if name.startswith(f"conversations/{conversation_id:06d}-")),
    }
    documents = json.loads(entries["documents.json"])
    assert [document["id"] for document in documents] == [alices["id"]]
    assert {handbook["id"], bobs["id"]}.isdisjoint(document["id"] for document in documents)
    text = entry_text(entries)
    for foreign in ("BOBQUESTION", "BOBPRIVATE", "bobfile.txt", "bob-other"):
        assert foreign not in text
    index = json.loads(entries["conversations/index.json"])
    assert [(item["id"], item["messages"]) for item in index] == [(conversation_id, 2)]
    assert index[0]["markdown"] in entries and index[0]["json"] in entries
    profile = json.loads(entries["account.json"])
    assert (profile["format"], profile["version"], profile["username"], profile["role"]) == (
        "rag-assistant-export",
        1,
        "alice",
        "member",
    )
    assert profile["preferences"] == {**DEFAULTS, "theme": "dark", "accent": "sage"}
    assert profile["usage_this_month"] == (await alice.get("/api/auth/me")).json()["usage"]
    assert profile["limits"] == (await alice.get("/api/auth/me")).json()["limits"]
    assert profile["plugin_connections"] == []
    assert {event["id"] for event in profile["recent_logins"]} == {row.id for row in await login_rows(alice.user_id)}
    assert all("current" not in event for event in profile["recent_logins"])
    assert profile["conversations"] == {"included": 1, "omitted": 0, "truncated": None}
    readme = entries["README.txt"].decode()
    assert "alice" in readme and "documents panel" in readme


async def test_the_export_mirrors_what_the_conversation_api_shows(create_user: CreateUser, fake_llm: FakeLLM) -> None:
    alice = await create_user("alice")
    await ingest(alice, "alpha.txt", txt_bytes("Alpha says the launch is on Tuesday."))
    _, events = await chat(alice, "What does alpha say?")
    conversation_id = events[0][1]["conversation_id"]
    fake_llm.fail_after = 1
    await chat(alice, "And then?", conversation_id=conversation_id, document_ids=[])

    entries = unzip(await export_of(alice))

    markdown, data = conversation_entries(entries, conversation_id)
    shown = (await alice.get(f"/api/conversations/{conversation_id}")).json()
    assert (data["id"], data["title"], data["created_at"], data["updated_at"]) == (
        shown["id"],
        shown["title"],
        shown["created_at"],
        shown["updated_at"],
    )
    fields = ("id", "role", "content", "created_at", "model_name", "prompt_tokens", "completion_tokens", "error")
    assert [{key: message[key] for key in fields} for message in data["messages"]] == [
        {key: message[key] for key in fields} for message in shown["messages"]
    ]
    assert [message["sources"] for message in data["messages"]] == [message["sources"] for message in shown["messages"]]
    first_answer, failed = shown["messages"][1], shown["messages"][3]
    assert first_answer["sources"] and first_answer["sources"][0]["filename"] == "alpha.txt"
    assert failed["error"]
    assert markdown.startswith(f"# {shown['title']}\n")
    assert "## You · " in markdown and "## Assistant (Claude Opus 5.5) · " in markdown
    assert "What does alpha say?" in markdown and first_answer["content"] in markdown
    assert "Sources:\n1. alpha.txt" in markdown
    assert f"*Error: {' '.join(failed['error'].split())}*" in markdown


async def test_the_response_is_a_bounded_attachment_download(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    for number in range(3):
        await add_conversation(alice.user_id, f"Chat {number}")
    before = datetime.now(UTC).date()

    response = await export_of(alice)

    after = datetime.now(UTC).date()
    unzip(response)
    headers = response.headers
    assert headers["content-type"] == "application/zip"
    assert headers["content-disposition"] in {
        f'attachment; filename="rag-assistant-export-alice-{day.isoformat()}.zip"' for day in (before, after)
    }
    assert int(headers["content-length"]) == len(response.content)
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert (headers["x-export-conversations"], headers["x-export-omitted"]) == ("3", "0")


async def test_many_conversations_export_newest_first_with_unique_names(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    base = datetime(2026, 9, 1, tzinfo=UTC)
    ids = [
        await add_conversation(alice.user_id, "Same title", updated_at=base + timedelta(minutes=n)) for n in range(128)
    ]
    tied = [await add_conversation(alice.user_id, f"Tie {n}", updated_at=base + timedelta(days=10)) for n in range(2)]

    response = await export_of(alice)
    entries = unzip(response)

    index = json.loads(entries["conversations/index.json"])
    assert [item["id"] for item in index] == [tied[1], tied[0], *reversed(ids)]
    assert response.headers["x-export-conversations"] == "130" and response.headers["x-export-omitted"] == "0"
    conversation_names = [
        name for name in entries if name.startswith("conversations/") and name != "conversations/index.json"
    ]
    assert len(conversation_names) == 260
    for item in index:
        assert item["markdown"] in entries and item["json"] in entries
        markdown, data = conversation_entries(entries, item["id"])
        assert data["id"] == item["id"] and len(data["messages"]) == 2
        assert f"Question in {item['title']}" in markdown


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("../../etc/passwd", "etc-passwd"),
        ("C:\\Windows\\System32", "c-windows-system32"),
        ("/absolute/path/", "absolute-path"),
        ("名前のない会話", "conversation"),
        ("Ünïcödé café", "unicode-cafe"),
        ("", "conversation"),
        ("   ", "conversation"),
        ("a" * 200, "a" * 40),
        ("Quarterly report -- 2026 / Q3 ...", "quarterly-report-2026-q3"),
    ],
)
async def test_titles_become_safe_ascii_entry_names(create_user: CreateUser, title: str, slug: str) -> None:
    alice = await create_user("alice")
    conversation_id = await add_conversation(alice.user_id, title)

    entries = unzip(await export_of(alice))

    for name in entries:
        assert name.isascii() and ".." not in name and "\\" not in name and not name.startswith("/")
    assert {
        f"conversations/{conversation_id:06d}-{slug}.md",
        f"conversations/{conversation_id:06d}-{slug}.json",
    } <= set(entries)


async def test_a_title_with_line_breaks_is_one_heading(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    conversation_id = await add_conversation(alice.user_id, "line\nbreak\r\n  title")

    markdown, data = conversation_entries(unzip(await export_of(alice)), conversation_id)

    assert markdown.splitlines()[0] == "# line break title"
    assert data["title"] == "line\nbreak\r\n  title"


async def test_the_size_bound_keeps_the_newest_and_says_what_was_left_out(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "export_max_mb", 1)
    alice = await create_user("alice")
    base = datetime(2026, 10, 1, tzinfo=UTC)
    ids = [
        await add_conversation(
            alice.user_id,
            f"Long {n}",
            updated_at=base + timedelta(hours=n),
            messages=[{"role": "user", "content": "Write a lot"}, {"role": "assistant", "content": "x" * 200_000}],
        )
        for n in range(5)
    ]

    response = await export_of(alice)
    entries = unzip(response)

    index = json.loads(entries["conversations/index.json"])
    assert [item["id"] for item in index] == [ids[4], ids[3]]
    assert (response.headers["x-export-conversations"], response.headers["x-export-omitted"]) == ("2", "3")
    assert json.loads(entries["account.json"])["conversations"] == {"included": 2, "omitted": 3, "truncated": "size"}
    readme = entries["README.txt"].decode()
    assert "3 older conversation(s)" in readme and "size limit" in readme
    content = sum(len(data) for name, data in entries.items() if name.startswith("conversations/0"))
    assert content <= 1024 * 1024


async def test_the_time_bound_still_writes_the_account_and_documents(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    document = await upload(alice, "kept.txt", txt_bytes("Kept."))
    for number in range(3):
        await add_conversation(alice.user_id, f"Chat {number}")
    calls = iter([1000.0])
    monkeypatch.setattr(export, "clock", lambda: next(calls, 1000.0 + 10_000))  # the deadline passes at once

    response = await export_of(alice)
    entries = unzip(response)

    assert json.loads(entries["conversations/index.json"]) == []
    assert (response.headers["x-export-conversations"], response.headers["x-export-omitted"]) == ("0", "3")
    assert json.loads(entries["account.json"])["conversations"] == {"included": 0, "omitted": 3, "truncated": "time"}
    assert [item["id"] for item in json.loads(entries["documents.json"])] == [document.json()["id"]]
    assert "time limit" in entries["README.txt"].decode()


async def test_one_export_per_user_at_a_time_without_blocking_other_users(create_user: CreateUser) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    lock = export.user_lock(alice.user_id or 0)
    await lock.acquire()  # an export of alice's is being built or downloaded
    try:
        busy = await export_of(alice)
        bobs = await export_of(bob)
    finally:
        lock.release()

    assert (busy.status_code, busy.json()) == (
        409,
        {"detail": "An export is already being prepared. Wait for it to finish."},
    )
    assert bobs.status_code == 200
    assert (await export_of(alice)).status_code == 200
    assert (await export_of(alice)).status_code == 200  # the finished download freed the lock
    await assert_export_free(alice.user_id)


async def test_a_third_build_waits_briefly_then_gets_503_and_frees_its_lock(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "SLOT_WAIT_SECONDS", 0.2)
    alice = await create_user("alice")
    for _ in range(export.MAX_BUILDING):
        await export._building.acquire()
    try:
        busy = await export_of(alice)
    finally:
        for _ in range(export.MAX_BUILDING):
            export._building.release()

    assert (busy.status_code, busy.json()) == (503, {"detail": "Exports are busy right now. Try again in a minute."})
    assert (await export_of(alice)).status_code == 200
    await assert_export_free(alice.user_id)


async def test_concurrent_exports_of_different_users_each_hold_only_their_own_data(create_user: CreateUser) -> None:
    clients = [await create_user(name) for name in ("alice", "bob", "carol")]
    for found in clients:
        for number in range(60):
            await add_conversation(found.user_id, f"{found.username} chat {number}")

    responses = await asyncio.gather(*(export_of(found) for found in clients))

    for found, response in zip(clients, responses, strict=True):
        entries = unzip(response)
        titles = {item["title"] for item in json.loads(entries["conversations/index.json"])}
        assert titles == {f"{found.username} chat {number}" for number in range(60)}
        assert json.loads(entries["account.json"])["username"] == found.username
        await assert_export_free(found.user_id)


async def test_a_client_that_disconnects_ends_the_build_and_frees_everything(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    for number in range(3):
        await add_conversation(alice.user_id, f"Chat {number}")

    async def gone(self: Request) -> bool:
        return True

    with monkeypatch.context() as patched:
        patched.setattr(Request, "is_disconnected", gone)
        response = await export_of(alice)

    assert response.status_code != 200 and not response.content.startswith(b"PK")
    await assert_export_free(alice.user_id)
    assert (await export_of(alice)).status_code == 200


async def test_a_build_that_fails_frees_the_lock_and_the_slot(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    await add_conversation(alice.user_id, "Chat")

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the database went away")

    with monkeypatch.context() as patched:
        patched.setattr(registry, "hydrate_cards", broken)
        with pytest.raises(RuntimeError, match="the database went away"):  # no catch-all handler: a plain 500
            await export_of(alice)

    await assert_export_free(alice.user_id)
    assert (await export_of(alice)).status_code == 200


async def test_the_export_needs_a_session_and_a_post(client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")

    assert (await client.post("/api/me/export", json={})).status_code == 401
    assert (await alice.get("/api/me/export")).status_code == 405
    assert (await alice.put("/api/me/export", json={})).status_code == 405


async def test_an_account_with_nothing_exports_a_valid_zip(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    response = await export_of(alice)
    entries = unzip(response)

    assert set(entries) == {"README.txt", "account.json", "billing.json", "documents.json", "conversations/index.json"}
    assert json.loads(entries["documents.json"]) == [] and json.loads(entries["conversations/index.json"]) == []
    assert (response.headers["x-export-conversations"], response.headers["x-export-omitted"]) == ("0", "0")


async def test_the_admins_export_holds_only_the_admins_own_data(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice = await create_user("alice")
    shared = await upload(admin_client, "policy.txt", txt_bytes("The admin's shared policy."), "shared")
    alices = await upload(alice, "alice-private.txt", txt_bytes("Alice's private notes."))
    await add_conversation(alice.user_id, "Alice's private chat")
    own = await add_conversation(admin_client.user_id, "Admin chat")

    entries = unzip(await export_of(admin_client))

    assert [item["id"] for item in json.loads(entries["conversations/index.json"])] == [own]
    assert [item["id"] for item in json.loads(entries["documents.json"])] == [shared.json()["id"]]
    text = entry_text(entries)
    assert "Alice's private" not in text and alices.json()["filename"] not in text
    profile = json.loads(entries["account.json"])
    assert (profile["username"], profile["role"]) == (ADMIN_USERNAME, "admin")
    assert profile["limits"]["monthly_token_limit"] == 0


@pytest.mark.parametrize(
    "values",
    [
        {"export_timeout_seconds": 91},
        {"export_timeout_seconds": 9},
        {"export_max_mb": 0},
        {"export_max_mb": 1001},
    ],
)
def test_export_bounds_outside_their_ranges_stop_the_settings(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


def test_the_export_bounds_default_to_the_brief() -> None:
    defaults = Settings()  # type: ignore[call-arg]
    assert (defaults.export_max_mb, defaults.export_timeout_seconds) == (100, 80)


# ---------------- Export: plugin cards and secrets ----------------


async def propose(
    user_id: int | None, connection: Any, conversation_id: int, subject: str, to: str = "bob@company.com"
) -> GmailPendingSend:
    """A proposed email of the user's Gmail connection in `conversation_id`, with its send card on the answer."""
    recipients = [{"name": None, "address": to, "unmentioned": False}]
    email = OutgoingEmail(
        account_email=connection.account_email,
        to=compose.stored_addresses(recipients),
        cc=[],
        bcc=[],
        subject=subject,
        body=f"Hello,\nthis is {subject}.\n",
        attachments=[],
        in_reply_to=None,
        references=None,
    )
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None
        row = await sends.propose(
            session,
            user,
            connection,
            email,
            conversation_id=conversation_id,
            to=recipients,
            cc=[],
            bcc=[],
            attachments_meta=[],
            thread_id=None,
            reply_to=None,
            origin="chat",
        )
    card = GmailSendCard(card_id="g1", status="proposed", pending=sends.view(row, utcnow()))
    await add_card(await answer_id(conversation_id), "gmail", "gmail_send", card.model_dump(mode="json"))
    return row


async def test_the_export_hydrates_cards_and_never_holds_a_secret(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    admin_client: ApiClient,
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    connection = await gmail.connect(alice)
    await gmail.connect(bob, account="bob@example.test")
    conversation_id = await add_conversation(alice.user_id, "Lunch email")
    row = await propose(alice.user_id, connection, conversation_id, "Lunch on Friday")
    retired = await add_card(await answer_id(conversation_id), "retired", "retired_card", {"note": "kept as stored"})
    cancelled = await alice.post(f"/api/plugins/gmail/sends/{row.id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"

    response = await export_of(alice)
    entries = unzip(response)

    markdown, data = conversation_entries(entries, conversation_id)
    send_card, stored_card = data["messages"][1]["plugin_cards"]
    assert (send_card["type"], send_card["pending"]["status"]) == ("gmail_send", "cancelled")
    shown = (await alice.get(f"/api/conversations/{conversation_id}")).json()["messages"][1]["plugin_cards"]
    assert [send_card] == shown
    assert stored_card == {
        "plugin_id": "retired",
        "type": "retired_card",
        "note": "kept as stored",
        "card_id": f"p{retired}",
    }
    assert '- Email cancelled: to bob@company.com · "Lunch on Friday"' in markdown
    assert "- retired card (retired_card)" in markdown
    [linked] = json.loads(entries["account.json"])["plugin_connections"]
    assert (linked["plugin_id"], linked["plugin_name"], linked["account_email"], linked["status"]) == (
        "gmail",
        "Gmail",
        "alice@example.test",
        "connected",
    )
    secrets: list[str] = [settings.secret_key, TEST_TOKEN_KEY, CLIENT_SECRET, settings.upload_dir]
    async with SessionLocal() as session:
        secrets += [user.password_hash for user in await session.scalars(select(User))]
        for found in await session.scalars(select(PluginConnection)):
            secrets += [found.refresh_token_enc, found.account_subject]
            secrets += [found.access_token_enc] if found.access_token_enc else []
    for grant in gmail.google.grants.values():
        secrets += [*grant.refresh_tokens, *grant.access_tokens]
    secrets = [secret for secret in secrets if secret]
    assert len(secrets) >= 12
    archive_text = response.content.decode("latin-1")
    text = entry_text(entries)
    for secret in secrets:
        assert secret not in text and secret not in archive_text
    for key in ("password_hash", "token_version", "refresh_token", "access_token", "bob@example.test"):
        assert key not in text


# ================ Delete all chats ================


async def test_delete_all_removes_only_the_callers_conversations(
    create_user: CreateUser, admin_client: ApiClient
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    mine = [await add_conversation(alice.user_id, f"Mine {n}") for n in range(3)]
    _, events = await chat(alice, "A real chat too", document_ids=[])
    mine.append(events[0][1]["conversation_id"])
    bobs = [await add_conversation(bob.user_id, f"Bob {n}") for n in range(2)]
    admins = await add_conversation(admin_client.user_id, "Admin's")

    response = await alice.delete("/api/conversations")

    assert (response.status_code, response.json()) == (200, {"deleted": 4})
    assert (await alice.get("/api/conversations")).json() == []
    assert await count(Message, Message.conversation_id.in_(mine)) == 0
    assert (await account_of(alice))["conversation_count"] == 0
    for conversation_id in mine:
        assert (await alice.get(f"/api/conversations/{conversation_id}")).status_code == 404
    assert [item["id"] for item in (await bob.get("/api/conversations")).json()] == list(reversed(bobs))
    for conversation_id in bobs:
        assert len((await bob.get(f"/api/conversations/{conversation_id}")).json()["messages"]) == 2
    assert (await admin_client.get(f"/api/conversations/{admins}")).status_code == 200
    again = await alice.delete("/api/conversations")
    assert (again.status_code, again.json()) == (200, {"deleted": 0})


async def test_delete_all_needs_a_session(client: ApiClient, create_user: CreateUser) -> None:
    alice = await create_user("alice")
    await add_conversation(alice.user_id, "Kept")

    assert (await client.delete("/api/conversations")).status_code == 401
    assert await count(Conversation, Conversation.user_id == alice.user_id) == 1


async def test_delete_all_keeps_documents_generated_files_images_and_usage(
    create_user: CreateUser, image_tools: FakeLLM
) -> None:
    alice = await create_user("alice")
    image_tools.script = [
        ScriptedCall(
            [
                tool_call(
                    "create_image", prompt="A red lighthouse at dusk", title="Lighthouse", alt_text="A red lighthouse"
                )
            ]
        ),
        ScriptedCall([("text", "Here is your lighthouse.")]),
    ]
    response, _ = await chat(alice, "Generate an image of a red lighthouse", document_ids=[])
    assert response.status_code == 200
    image_tools.script = [
        ScriptedCall([tool_call("create_document", title="Trip notes", format="md", content="# Trip\n\nPack a coat.")]),
        ScriptedCall([("text", "Your notes are ready.")]),
    ]
    response, _ = await chat(alice, "Create a Markdown file with my trip notes", document_ids=[])
    assert response.status_code == 200
    before = {item["id"]: item for item in (await alice.get("/api/documents")).json()}
    image = next(item for item in before.values() if item["image"] is not None)
    notes = next(item for item in before.values() if item["filename"].endswith(".md"))
    assert image["generated"] and notes["generated"]
    usage_before = (await alice.get("/api/me/usage")).json()
    events_before = await count(UsageEvent, UsageEvent.user_id == alice.user_id)
    generated_before = await count(GeneratedDocument)

    response = await alice.delete("/api/conversations")

    assert response.json() == {"deleted": 2}
    after = {item["id"]: item for item in (await alice.get("/api/documents")).json()}
    assert after == before
    assert (await alice.get(f"/api/documents/{notes['id']}/download")).status_code == 200
    assert (await alice.get(f"/api/documents/{image['id']}/image", params={"size": "thumb"})).status_code == 200
    async with SessionLocal() as session:
        kept = await session.get(GeneratedImage, image["id"])
        assert kept is not None and kept.conversation_id is None and kept.prompt == "A red lighthouse at dusk"
    assert await count(GeneratedDocument) == generated_before
    assert await count(MessageFile) == 0
    assert await count(UsageEvent, UsageEvent.user_id == alice.user_id) == events_before
    usage_after = (await alice.get("/api/me/usage")).json()
    assert (usage_after["chat_tokens"], usage_after["images"]) == (usage_before["chat_tokens"], usage_before["images"])
    # The export still describes them as created in chat.
    documents = {item["id"]: item for item in json.loads(unzip(await export_of(alice))["documents.json"])}
    assert documents[image["id"]]["generated"] and documents[notes["id"]]["generated"]
    assert documents[image["id"]]["image"]["prompt"] == "A red lighthouse at dusk"


@pytest.mark.parametrize("gmail_state", ["on", "turned off", "not configured"])
async def test_delete_all_cancels_and_scrubs_the_callers_proposed_emails(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    admin_client: ApiClient,
    monkeypatch: pytest.MonkeyPatch,
    gmail_state: str,
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    alices = await gmail.connect(alice)
    bobs = await gmail.connect(bob, account="bob@example.test")
    first, second = await add_conversation(alice.user_id, "Email 1"), await add_conversation(alice.user_id, "Email 2")
    bob_conversation = await add_conversation(bob.user_id, "Bob's email")
    pending = await propose(alice.user_id, alices, first, "Pending proposal")
    sent = await propose(alice.user_id, alices, second, "Already sent")
    confirmed = await alice.post(f"/api/plugins/gmail/sends/{sent.id}/send", json={"content_hash": sent.content_hash})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "sent"
    bobs_send = await propose(bob.user_id, bobs, bob_conversation, "Bob's proposal")
    if gmail_state == "turned off":
        assert (await admin_client.patch("/api/admin/plugins/gmail", json={"enabled": False})).status_code == 200
    elif gmail_state == "not configured":
        monkeypatch.setattr(settings, "google_oauth_client_secret", "")
    delivered = len(gmail.mailbox.sent)

    response = await alice.delete("/api/conversations")

    assert response.json() == {"deleted": 2}
    rows = {row.id: row for row in await pending_sends()}
    for row_id, status in ((pending.id, "cancelled"), (sent.id, "sent")):
        row = rows[row_id]
        assert row.status == status
        assert (row.subject, row.body, row.to_addrs, row.cc_addrs, row.bcc_addrs, row.conversation_id) == (
            "",
            "",
            [],
            [],
            [],
            None,
        )
    assert rows[sent.id].sent_at is not None
    untouched = rows[bobs_send.id]
    assert (untouched.status, untouched.subject, untouched.conversation_id) == (
        "pending",
        "Bob's proposal",
        bob_conversation,
    )
    retry = await alice.post(f"/api/plugins/gmail/sends/{pending.id}/send", json={"content_hash": pending.content_hash})
    assert retry.status_code != 200
    assert len(gmail.mailbox.sent) == delivered


async def test_a_failing_plugin_clean_up_never_keeps_a_conversation(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    ids = [await add_conversation(alice.user_id, f"Chat {n}") for n in range(5)]
    plugin = FakePlugin()
    plugin.fail_conversation_hook = True

    with registry.use_plugins([plugin]):
        response = await alice.delete("/api/conversations")

    assert (response.status_code, response.json()) == (200, {"deleted": 5})
    assert sorted(plugin.events) == sorted(f"conversation_deleted:{conversation_id}" for conversation_id in ids)
    assert await count(Conversation) == 0 and await count(Message) == 0


# ================ The plugin catalog users see ================

INFO_KEYS = {
    "id",
    "name",
    "description",
    "auth_type",
    "configured",
    "enabled",
    "testing_mode",
    "supports_import",
    "import_max_items",
    "capabilities",
    "connection",
    "in_plan",
}
CAPABILITY_KEYS = {"id", "name", "description", "enabled", "granted"}
CONNECTION_KEYS = {
    "account_email",
    "status",
    "status_reason",
    "status_message",
    "granted_scopes",
    "chat_enabled",
    "connected_at",
    "last_used_at",
    "refresh_expires_at",
}
ADMIN_ONLY = (
    "missing_settings",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "PUBLIC_BASE_URLS",
    "requested_scopes",
    "redirect_uris",
    "test_endpoints",
    "admin_note",
    "activity",
    CLIENT_ID,
    CLIENT_SECRET,
)


async def catalog(client: ApiClient) -> dict[str, Any]:
    response = await client.get("/api/plugins")
    assert response.status_code == 200, response.text
    for word in ADMIN_ONLY:
        assert word not in response.text, word
    [info] = response.json()
    assert set(info) == INFO_KEYS
    for capability in info["capabilities"]:
        assert set(capability) == CAPABILITY_KEYS
    if info["connection"] is not None:
        assert set(info["connection"]) == CONNECTION_KEYS
    return info


async def chat_offer(client: ApiClient, fake_llm: FakeLLM) -> tuple[set[str], str]:
    """The Gmail tools the next answer's model call was offered, and its system prompt."""
    response, _ = await chat(client, "Did Bob email me about the invoice?", document_ids=[])
    assert response.status_code == 200, response.text
    call = fake_llm.chat_calls[-1]
    return offered_tools(call) & GMAIL_TOOLS, system_prompt(call)


@pytest.fixture
def gmail_not_configured() -> Iterator[GmailPlugin]:
    """The Gmail plugin registered in an app without its settings (no OAuth client, no token key)."""
    plugin = GmailPlugin()
    with registry.use_plugins([plugin]):
        yield plugin


async def test_a_plugin_that_isnt_set_up_is_listed_for_users_but_offers_nothing(
    gmail_not_configured: GmailPlugin, create_user: CreateUser, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice")
    async with SessionLocal() as session:  # even a stale "on" switch doesn't turn it on
        session.add(PluginSetting(plugin_id="gmail", enabled=True, capabilities={}))
        await session.commit()

    info = await catalog(alice)
    tools, prompt = await chat_offer(alice, fake_llm)

    assert (info["id"], info["name"], info["configured"], info["enabled"], info["connection"]) == (
        "gmail",
        "Gmail",
        False,
        False,
        None,
    )
    assert info["capabilities"] and not any(capability["granted"] for capability in info["capabilities"])
    assert tools == set() and "Gmail" not in prompt
    connect = await alice.post("/api/plugins/gmail/connect", json={}, headers={"Origin": "http://testserver"})
    assert connect.status_code == 409
    assert (await catalog(admin_client))["configured"] is False


async def test_a_plugin_that_is_set_up_but_off_is_listed_as_off(
    plugins: PluginHarness, create_user: CreateUser, fake_llm: FakeLLM
) -> None:
    alice = await create_user("alice")
    with registry.use_plugins([GmailPlugin()]):
        info = await catalog(alice)
        tools, prompt = await chat_offer(alice, fake_llm)
        connect = await plugins.start(alice, "gmail")

    assert (info["configured"], info["enabled"], info["connection"]) == (True, False, None)
    assert tools == set() and gmail_tools.NOTE_CONNECT not in prompt
    assert connect.status_code == 403


async def test_an_enabled_plugin_without_a_connection_offers_only_the_connect_note(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    fake_llm: FakeLLM,
) -> None:
    alice = await create_user("alice")

    info = await catalog(alice)
    tools, prompt = await chat_offer(alice, fake_llm)

    assert (info["configured"], info["enabled"], info["connection"]) == (True, True, None)
    assert tools == set() and gmail_tools.NOTE_CONNECT in prompt
    assert "Settings" in gmail_tools.NOTE_CONNECT and "Plugins menu" in gmail_tools.NOTE_CONNECT


async def test_only_a_connected_user_with_use_in_chat_on_gets_gmail_tools(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    admin_client: ApiClient,
    fake_llm: FakeLLM,
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    await gmail.connect(alice)

    info = await catalog(alice)
    text = (await alice.get("/api/plugins")).text
    tools, _ = await chat_offer(alice, fake_llm)
    bobs_info = await catalog(bob)
    bobs_tools, bobs_prompt = await chat_offer(bob, fake_llm)

    assert info["enabled"] and info["connection"]["account_email"] == "alice@example.test"
    assert info["connection"]["status"] == "connected" and info["connection"]["chat_enabled"] is True
    assert any(capability["granted"] for capability in info["capabilities"])
    async with SessionLocal() as session:
        row = await session.scalar(select(PluginConnection).where(PluginConnection.user_id == alice.user_id))
        assert row is not None
        for secret in (row.account_subject, row.refresh_token_enc, row.access_token_enc):
            assert not secret or secret not in text
    for grant in gmail.google.grants.values():
        for token in (*grant.refresh_tokens, *grant.access_tokens):
            assert token not in text
    assert "gmail_search" in tools
    assert bobs_info["connection"] is None and "alice@example.test" not in (await bob.get("/api/plugins")).text
    assert bobs_tools == set() and gmail_tools.NOTE_CONNECT in bobs_prompt

    off = await alice.patch("/api/plugins/gmail/connection", json={"chat_enabled": False})
    assert off.status_code == 200
    tools, prompt = await chat_offer(alice, fake_llm)
    assert tools == set() and gmail_tools.NOTE_CONNECT not in prompt and (await catalog(alice))["connection"]

    await alice.patch("/api/plugins/gmail/connection", json={"chat_enabled": True})
    assert (await admin_client.patch("/api/admin/plugins/gmail", json={"enabled": False})).status_code == 200
    info = await catalog(alice)
    tools, prompt = await chat_offer(alice, fake_llm)
    assert (info["configured"], info["enabled"]) == (True, False)
    assert info["connection"]["account_email"] == "alice@example.test"
    assert tools == set() and "Gmail" not in prompt
    assert (await alice.delete("/api/plugins/gmail/connection")).status_code == 204
    assert (await catalog(alice))["connection"] is None


async def test_a_connection_that_needs_reconnecting_is_listed_and_offers_no_tools(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    fake_llm: FakeLLM,
) -> None:
    alice = await create_user("alice")
    await gmail.connect(alice)
    await update_connection(alice.user_id or 0, status="reconnect_needed", status_reason="revoked")

    info = await catalog(alice)
    tools, prompt = await chat_offer(alice, fake_llm)

    assert info["enabled"] and (info["connection"]["status"], info["connection"]["status_reason"]) == (
        "reconnect_needed",
        "revoked",
    )
    assert tools == set() and "Settings → Plugins & data" in prompt


async def test_a_connection_outlives_the_plugins_settings_and_can_still_be_removed(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = await create_user("alice")
    await gmail.connect(alice)
    monkeypatch.setattr(settings, "google_oauth_client_id", "")

    info = await catalog(alice)
    tools, prompt = await chat_offer(alice, fake_llm)

    assert (info["configured"], info["enabled"]) == (False, False)
    assert info["connection"]["account_email"] == "alice@example.test"
    assert tools == set() and "Gmail" not in prompt
    assert (await alice.delete("/api/plugins/gmail/connection")).status_code == 204
    after = await catalog(alice)
    assert (after["configured"], after["connection"]) == (False, None)


async def test_a_model_that_cant_use_tools_says_so_and_gets_no_gmail_tools(
    gmail: Gmail,  # noqa: F811
    create_user: CreateUser,
    fake_llm: FakeLLM,
) -> None:
    alice = await create_user("alice")
    await gmail.connect(alice)
    before = {model["display_name"]: model for model in (await alice.get("/api/models")).json()}
    assert before["Claude Opus 5.5"]["supports_tools"] is True
    async with SessionLocal() as session:
        opus = await session.get(LLMModel, before["Claude Opus 5.5"]["id"])
        assert opus is not None
        llm.mark_tools_rejected(opus, "tools are not supported")

    after = {model["display_name"]: model for model in (await alice.get("/api/models")).json()}
    tools, prompt = await chat_offer(alice, fake_llm)

    assert after["Claude Opus 5.5"]["supports_tools"] is False and after["Claude Haiku 5.5"]["supports_tools"] is True
    assert tools == set() and gmail_tools.NOTE_NO_TOOLS in prompt


def test_the_tests_never_load_the_repositorys_env_file() -> None:
    """LiteLLM loads the nearest .env at import unless LITELLM_MODE is set; the suite must not depend on (or read) the
    owner's real settings, so Gmail starts out not set up here."""
    assert os.environ.get("LITELLM_MODE", "DEV") != "DEV"
    assert (settings.google_oauth_client_id, settings.google_oauth_client_secret, settings.token_encryption_key) == (
        "",
        "",
        "",
    )
