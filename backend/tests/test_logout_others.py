"""POST /auth/logout-others (docs/briefs/ui-shell-settings.md, 4.3): every other session ends, the caller's cookie is
re-issued with the same session id, under the row lock that change-password uses."""

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any

import httpx
import jwt
import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, USER_PASSWORD, ApiClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import PluginConnection, User
from app.routers import auth as auth_router
from app.security import create_access_token

CreateUser = Callable[..., Awaitable[ApiClient]]


def token_of(client: ApiClient) -> str:
    token = client.cookies.get(settings.cookie_name)
    assert token is not None
    return token


def claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def expiring_in(token: str, seconds: int) -> str:
    """The same session (sub, tv, sid) with only `seconds` left, as a stolen nearly-expired token would be."""
    return jwt.encode(claims(token) | {"exp": int(time.time()) + seconds}, settings.secret_key, algorithm="HS256")


def cookie_max_age(response: httpx.Response) -> int:
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    return int(cookies[settings.cookie_name]["max-age"])


async def token_version(user_id: int | None) -> int:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.token_version


async def test_other_sessions_end_and_the_caller_keeps_its_own(
    create_user: CreateUser, new_client: Callable[[], ApiClient], client: ApiClient
) -> None:
    alice = await create_user("alice")
    other = new_client()
    assert (await other.login("alice", USER_PASSWORD)).status_code == 200
    bearer = {"Authorization": f"Bearer {token_of(other)}"}
    before, sid = await token_version(alice.user_id), claims(token_of(alice))["sid"]
    async with SessionLocal() as session:
        session.add(
            PluginConnection(
                user_id=alice.user_id,
                plugin_id="gmail",
                account_subject="1",
                account_email="alice@example.test",
                refresh_token_enc="x",
                key_id="k",
            )
        )
        await session.commit()

    response = await alice.post("/api/auth/logout-others")

    assert (response.status_code, response.content) == (204, b"")
    assert settings.cookie_name in response.cookies
    assert claims(token_of(alice)) | {"iat": 0, "exp": 0} == {
        "sub": str(alice.user_id),
        "tv": before + 1,
        "sid": sid,
        "iat": 0,
        "exp": 0,
    }
    assert await token_version(alice.user_id) == before + 1
    assert (await alice.get("/api/auth/me")).status_code == 200
    assert (await other.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer)).status_code == 401
    current = [entry for entry in (await alice.get("/api/me/account")).json()["recent_logins"] if entry["current"]]
    assert [entry["id"] for entry in current] == [sid]
    async with SessionLocal() as session:  # plugin connections are untouched
        assert len(list(await session.scalars(select(PluginConnection)))) == 1
    assert (await other.login("alice", USER_PASSWORD)).status_code == 200  # the password still works


async def test_a_bearer_caller_gets_a_cookie_and_its_token_ends(
    create_user: CreateUser, new_client: Callable[[], ApiClient], client: ApiClient
) -> None:
    alice = await create_user("alice")
    bearer = {"Authorization": f"Bearer {token_of(alice)}"}

    response = await client.post("/api/auth/logout-others", headers=bearer)

    assert response.status_code == 204
    assert (await new_client().get("/api/auth/me", headers=bearer)).status_code == 401
    assert (await client.get("/api/auth/me")).status_code == 200  # the new cookie
    assert (await alice.get("/api/auth/me")).status_code == 401


async def test_the_env_admin_can_log_out_other_sessions(
    admin_client: ApiClient, new_client: Callable[[], ApiClient]
) -> None:
    other = new_client()
    assert (await other.login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 200

    assert (await admin_client.post("/api/auth/logout-others")).status_code == 204

    assert (await admin_client.get("/api/auth/me")).status_code == 200
    assert (await other.get("/api/auth/me")).status_code == 401


async def test_logout_others_needs_a_session(client: ApiClient) -> None:
    response = await client.post("/api/auth/logout-others")

    assert (response.status_code, response.json()) == (401, {"detail": "Not authenticated"})


async def test_it_is_not_throttled(create_user: CreateUser, monkeypatch: pytest.MonkeyPatch) -> None:
    alice = await create_user("alice")
    monkeypatch.setattr(settings, "login_max_attempts", 1)

    for _ in range(3):
        assert (await alice.post("/api/auth/logout-others")).status_code == 204


@pytest.mark.parametrize("admin_change", [{"password": "reset-by-the-admin"}, {"is_active": False}])
async def test_a_session_ended_meanwhile_gets_401(
    admin_change: dict[str, Any],
    create_user: CreateUser,
    admin_client: ApiClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = await create_user("alice")
    lock_session_user = auth_router._lock_session_user

    async def admin_acts_meanwhile(*args: Any) -> None:
        response = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json=admin_change)
        assert response.status_code == 200
        await lock_session_user(*args)

    monkeypatch.setattr(auth_router, "_lock_session_user", admin_acts_meanwhile)

    response = await alice.post("/api/auth/logout-others")

    assert response.status_code == 401
    assert settings.cookie_name not in response.cookies
    assert await token_version(alice.user_id) == 1  # only the admin's change counted


@pytest.mark.parametrize("sent_as", ["cookie", "bearer"])
async def test_the_reissued_session_keeps_its_expiry(
    sent_as: str, create_user: CreateUser, client: ApiClient
) -> None:
    alice = await create_user("alice")
    short = expiring_in(token_of(alice), 60)
    sent = {"Cookie": f"{settings.cookie_name}={short}"}
    if sent_as == "bearer":
        sent = {"Authorization": f"Bearer {short}"}

    response = await client.post("/api/auth/logout-others", headers=sent)

    assert response.status_code == 204
    renewed = claims(response.cookies[settings.cookie_name])
    assert (renewed["exp"], renewed["sid"], renewed["tv"]) == (claims(short)["exp"], claims(short)["sid"], 1)
    assert 1 <= cookie_max_age(response) <= 60
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_chained_calls_never_extend_the_session(
    create_user: CreateUser, new_client: Callable[[], ApiClient]
) -> None:
    alice = await create_user("alice")
    token = expiring_in(token_of(alice), 60)
    expiry = claims(token)["exp"]

    for _ in range(5):
        response = await new_client().post("/api/auth/logout-others", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 204
        token = response.cookies[settings.cookie_name]
        assert claims(token)["exp"] == expiry
        assert cookie_max_age(response) <= 60


async def test_a_fresh_session_keeps_its_full_expiry(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    expiry = claims(token_of(alice))["exp"]

    response = await alice.post("/api/auth/logout-others")

    assert claims(token_of(alice))["exp"] == expiry
    assert cookie_max_age(response) <= settings.access_token_expire_minutes * 60


def test_a_reissued_token_never_outlives_a_fresh_one() -> None:
    far = datetime.now(timezone.utc) + timedelta(days=30)

    exp = claims(create_access_token(1, 0, 7, far))["exp"]

    assert exp <= time.time() + settings.access_token_expire_minutes * 60 + 1
