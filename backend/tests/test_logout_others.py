"""POST /auth/logout-others (docs/briefs/ui-shell-settings.md, 4.3): every other session ends, the caller's cookie is
re-issued with the same session id, under the row lock that change-password uses."""

from collections.abc import Awaitable, Callable
from typing import Any

import jwt
import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, USER_PASSWORD, ApiClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import PluginConnection, User
from app.routers import auth as auth_router

CreateUser = Callable[..., Awaitable[ApiClient]]


def token_of(client: ApiClient) -> str:
    token = client.cookies.get(settings.cookie_name)
    assert token is not None
    return token


def claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


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
