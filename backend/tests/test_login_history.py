"""The sign-in history and GET /me/account (docs/briefs/ui-shell-settings.md, 4.2 and 4.4): what a login records (an
approximate IP, the parsed User-Agent, never the raw one), what doesn't record anything, the newest 20, the session id
that marks "this device", and the account view."""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
import pytest
from conftest import ADMIN_USERNAME, USER_PASSWORD, ApiClient, chat
from fastapi import FastAPI
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import LoginEvent
from app.schemas_admin import AdminUser
from app.security import create_access_token
from app.services import account

CreateUser = Callable[..., Awaitable[ApiClient]]
ClientAt = Callable[..., httpx.AsyncClient]

CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
    "Safari/537.36"
)


@pytest.fixture
async def client_at(app: FastAPI) -> AsyncIterator[ClientAt]:
    """Factory for anonymous clients connecting from `ip` (ApiClient always uses 127.0.0.1)."""
    clients: list[httpx.AsyncClient] = []

    def factory(ip: str, user_agent: str | None = None) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(ip, 50000))
        headers = {"User-Agent": user_agent} if user_agent is not None else None
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=headers)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


async def events_of(user_id: int | None) -> list[LoginEvent]:
    async with SessionLocal() as session:
        return list(
            await session.scalars(select(LoginEvent).where(LoginEvent.user_id == user_id).order_by(LoginEvent.id))
        )


async def login(client: httpx.AsyncClient, username: str, password: str = USER_PASSWORD) -> httpx.Response:
    return await client.post("/api/auth/login", json={"username": username, "password": password})


async def account_of(client: httpx.AsyncClient, **kwargs: Any) -> dict[str, Any]:
    response = await client.get("/api/me/account", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def sid_of(client: httpx.AsyncClient) -> Any:
    token = client.cookies.get(settings.cookie_name)
    assert token is not None
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"]).get("sid")


# ---------------- What a login records ----------------


@pytest.mark.parametrize(
    ("host", "ip"),
    [
        ("81.2.69.142", "81.2.69.x"),
        ("192.168.65.1", "192.168.65.x"),
        ("::ffff:81.2.69.142", "81.2.69.x"),  # IPv4-mapped IPv6: its IPv4 form
        ("2a00:1450:4001:82b::200e", "2a00:1450:4001::/48"),
        ("::1", "::/48"),
        ("testclient", None),
        ("unknown", None),
    ],
)
async def test_a_login_records_one_event_with_an_approximate_ip(
    host: str, ip: str | None, create_user: CreateUser, client_at: ClientAt
) -> None:
    alice = await create_user("alice", login=False)
    started = datetime.now(timezone.utc)

    response = await login(client_at(host, CHROME_MAC), "alice")

    assert response.status_code == 200
    [event] = await events_of(alice.user_id)
    assert (event.ip, event.browser, event.os, event.device) == (ip, "Chrome 141", "macOS", "desktop")
    assert started <= event.created_at <= datetime.now(timezone.utc)
    assert sid_of(response) == event.id


async def test_the_raw_user_agent_is_never_stored(create_user: CreateUser, client_at: ClientAt) -> None:
    marker = "Secret-Marker-4242"
    alice = await create_user("alice", login=False)

    await login(client_at("81.2.69.142", f"{CHROME_MAC} {marker}/1.0"), "alice")

    [event] = await events_of(alice.user_id)
    columns = [getattr(event, column.key) for column in LoginEvent.__table__.columns]
    assert not any(marker in str(value) for value in columns)


@pytest.mark.parametrize(
    ("stored", "private"),
    [
        ("192.168.65.x", True),  # Docker Desktop's gateway
        ("10.1.2.x", True),
        ("172.17.0.x", True),
        ("127.0.0.x", True),
        ("100.64.0.x", True),  # shared address space (carrier-grade NAT)
        ("169.254.1.x", True),
        ("203.0.113.x", True),  # documentation ranges aren't global either
        ("fe80::/48", True),
        ("fd12:3456:789a::/48", True),
        ("::/48", True),  # ::1 masked
        ("2001:db8:85a3::/48", True),
        ("81.2.69.x", False),
        ("8.8.8.x", False),
        ("2a00:1450:4001::/48", False),
        (None, False),
        ("", False),
        ("garbage", False),
        ("300.1.2.x", False),
    ],
)
def test_ip_is_private(stored: str | None, private: bool) -> None:
    assert account.ip_is_private(stored) is private


async def test_the_account_says_whether_a_sign_in_came_through_a_private_address(
    create_user: CreateUser, client_at: ClientAt
) -> None:
    await create_user("alice", login=False)
    public, local = client_at("81.2.69.142"), client_at("192.168.65.1")
    await login(public, "alice")
    await login(local, "alice")

    logins = (await account_of(local))["recent_logins"]

    assert [(entry["ip"], entry["ip_private"]) for entry in logins] == [("192.168.65.x", True), ("81.2.69.x", False)]


@pytest.mark.parametrize(
    ("user_agent", "parsed"),
    [
        (CHROME_MAC, ("Chrome 141", "macOS", "desktop")),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/26.0 Mobile/15E148 Safari/604.1",
            ("Safari 26", "iOS", "mobile"),
        ),
        (
            "Mozilla/5.0 (iPad; CPU OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 "
            "Mobile/15E148 Safari/604.1",
            ("Safari 18", "iPadOS", "tablet"),
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 "
            "Safari/605.1.15",
            ("Safari 26", "macOS", "desktop"),
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0",
            ("Firefox 144", "Linux", "desktop"),
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "FxiOS/144.0 Mobile/15E148 Safari/605.1.15",
            ("Firefox 144", "iOS", "mobile"),
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Safari/537.36 Edg/141.0.3537.71",
            ("Edge 141", "Windows", "desktop"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 "
            "Chrome/130.0.0.0 Mobile Safari/537.36",
            ("Samsung Internet 28", "Android", "mobile"),
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 "
            "Safari/537.36 OPR/124.0.0.0",
            ("Opera 124", "Windows", "desktop"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile "
            "Safari/537.36",
            ("Chrome 141", "Android", "mobile"),
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; SM-X910) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Safari/537.36",
            ("Chrome 141", "Android", "tablet"),
        ),
        (
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
            "Safari/537.36",
            ("Chrome 141", "ChromeOS", "desktop"),
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.7390.37 "
            "Safari/537.36",
            ("Headless Chrome 141", "Linux", "desktop"),
        ),
        ("curl/8.7.1", ("curl 8", None, "unknown")),
        ("python-httpx/0.28.1", ("Python", None, "unknown")),
        ("", (None, None, "unknown")),
        (None, (None, None, "unknown")),
        ("\x00\x01 \U0001f4a5 not a browser ☃", (None, None, "unknown")),
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/١٤١", (None, "Windows", "desktop")),  # no ASCII digits
        ("A" * 10_000 + " Chrome/141", (None, None, "unknown")),  # only the first 512 characters are read
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/141.0 " + "x" * 10_000, ("Chrome 141", "Windows", "desktop")),
    ],
)
def test_parse_user_agent(user_agent: str | None, parsed: tuple[str | None, str | None, str]) -> None:
    result = account.parse_user_agent(user_agent)

    assert result == parsed
    for value in result:
        assert value is None or (len(value) <= 64 and value.isascii() and value.isprintable())


# ---------------- What records nothing ----------------


async def test_failed_refused_and_throttled_logins_record_nothing(
    create_user: CreateUser, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice", login=False)
    inactive = await create_user("inactive", login=False, is_active=False)
    monkeypatch.setattr(settings, "login_max_attempts", 2)

    assert (await login(client, "alice", "wrong-password")).status_code == 401
    assert (await login(client, "inactive")).status_code == 401
    assert (await login(client, "nobody")).status_code == 401
    assert (await login(client, "alice", "wrong-password")).status_code == 401
    assert (await login(client, "alice")).status_code == 429  # throttled, even with the right password

    assert await events_of(alice.user_id) == []
    assert await events_of(inactive.user_id) == []


async def test_25_logins_leave_the_newest_20(create_user: CreateUser, client: ApiClient) -> None:
    alice = await create_user("alice", login=False)
    sids = []
    for _ in range(25):
        response = await login(client, "alice")
        assert response.status_code == 200
        sids.append(sid_of(response))

    kept = await events_of(alice.user_id)

    assert [event.id for event in kept] == sids[-account.LOGIN_HISTORY_LIMIT :]
    listed = (await account_of(client))["recent_logins"]
    assert [entry["id"] for entry in listed] == list(reversed(sids[-account.LOGIN_HISTORY_LIMIT :]))  # newest first


# ---------------- This device ----------------


async def test_current_marks_the_sign_in_of_the_calling_session(
    create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    alice = await create_user("alice")
    other = new_client()
    assert (await other.login("alice", USER_PASSWORD)).status_code == 200
    first, second = [event.id for event in await events_of(alice.user_id)]

    mine = (await account_of(alice))["recent_logins"]
    theirs = (await account_of(other))["recent_logins"]

    assert [(entry["id"], entry["current"]) for entry in mine] == [(second, False), (first, True)]
    assert [(entry["id"], entry["current"]) for entry in theirs] == [(second, True), (first, False)]


async def test_a_token_without_a_session_id_marks_nothing(create_user: CreateUser, client: ApiClient) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    old_style = create_access_token(alice.user_id, 0)  # from before session ids

    logins = (await account_of(client, headers={"Authorization": f"Bearer {old_style}"}))["recent_logins"]

    assert len(logins) == 1 and logins[0]["current"] is False


async def test_change_password_keeps_the_session_id(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    sid = sid_of(alice)

    response = await alice.post(
        "/api/auth/change-password", json={"current_password": USER_PASSWORD, "new_password": "a-new-password-1"}
    )

    assert response.status_code == 204
    assert sid_of(alice) == sid
    [entry] = (await account_of(alice))["recent_logins"]
    assert (entry["id"], entry["current"]) == (sid, True)


# ---------------- The account ----------------


async def test_the_account_view(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    await chat(alice, "First question?", document_ids=[])
    await chat(alice, "Second question?", document_ids=[])

    info = await account_of(alice)
    admin = await account_of(admin_client)

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
    assert (info["id"], info["username"], info["is_admin"]) == (alice.user_id, "alice", False)
    assert (info["password_managed_by_env"], info["conversation_count"]) == (False, 2)
    created = datetime.fromisoformat(info["created_at"])
    last_login = datetime.fromisoformat(info["last_login_at"])
    assert created <= last_login <= datetime.now(timezone.utc) and last_login - created < timedelta(minutes=1)
    [entry] = info["recent_logins"]
    assert set(entry) == {"id", "created_at", "ip", "ip_private", "browser", "os", "device", "current"}
    assert (entry["ip"], entry["ip_private"], entry["browser"], entry["device"]) == (
        "127.0.0.x",
        True,
        "Python",
        "unknown",
    )
    assert (admin["username"], admin["is_admin"], admin["password_managed_by_env"]) == (ADMIN_USERNAME, True, True)
    assert admin["conversation_count"] == 0


async def test_nobody_sees_another_users_sign_ins(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    alice_events = {event.id for event in await events_of(alice.user_id)}

    bob_logins = (await account_of(bob))["recent_logins"]
    admin_logins = (await account_of(admin_client))["recent_logins"]

    assert not alice_events & {entry["id"] for entry in bob_logins + admin_logins}
    users = (await admin_client.get("/api/admin/users")).json()
    assert all(set(user) == set(AdminUser.model_fields) for user in users)  # the admin API doesn't show them


async def test_the_account_needs_a_session(client: ApiClient) -> None:
    response = await client.get("/api/me/account")

    assert (response.status_code, response.json()) == (401, {"detail": "Not authenticated"})


async def test_deleting_the_user_deletes_their_sign_ins(create_user: CreateUser, admin_client: ApiClient) -> None:
    alice = await create_user("alice")
    assert len(await events_of(alice.user_id)) == 1

    assert (await admin_client.delete(f"/api/admin/users/{alice.user_id}")).status_code == 204

    assert await events_of(alice.user_id) == []
