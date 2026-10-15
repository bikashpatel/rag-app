"""Auth API: login and its throttle, sessions (cookie and Bearer), /auth/me, logout and password changes."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone
from http.cookies import Morsel, SimpleCookie
from typing import Any

import httpx
import jwt
import pytest
from argon2 import PasswordHasher
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, USER_PASSWORD, ApiClient
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import UsageEvent, User
from app.redis_client import get_redis, set_redis
from app.routers import auth as auth_router
from app.security import needs_rehash, verify_password
from app.services.usage import month_start

CreateUser = Callable[..., Awaitable[ApiClient]]
ClientAt = Callable[[str], httpx.AsyncClient]

NEW_PASSWORD = "a-brand-new-password"
WRONG_PASSWORD = "not-the-password"
OTHER_IP = "203.0.113.7"
INVALID_LOGIN = {"detail": "Invalid username or password"}
NOT_AUTHENTICATED = {"detail": "Not authenticated"}


@pytest.fixture
async def client_at(app: FastAPI) -> AsyncIterator[ClientAt]:
    """Factory for anonymous clients connecting from the given IP (ApiClient always uses 127.0.0.1)."""
    clients: list[httpx.AsyncClient] = []

    def factory(ip: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(ip, 50000))
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


async def login(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post("/api/auth/login", json={"username": username, "password": password})


async def get_user(username: str) -> User:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == username))
    assert user is not None
    return user


async def set_user_columns(username: str, **values: Any) -> None:
    async with SessionLocal() as session:
        await session.execute(update(User).where(User.username == username).values(**values))
        await session.commit()


def set_cookies(response: httpx.Response) -> SimpleCookie:
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    return cookies


def session_cookie(response: httpx.Response) -> Morsel[str]:
    cookies = set_cookies(response)
    assert settings.cookie_name in cookies, response.headers.get_list("set-cookie")
    return cookies[settings.cookie_name]


def session_token(client: httpx.AsyncClient) -> str:
    token = client.cookies.get(settings.cookie_name)
    assert token
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def claims(user_id: int, token_version: int = 0, **overrides: Any) -> dict[str, Any]:
    now = int(datetime.now(timezone.utc).timestamp())
    return {"sub": str(user_id), "tv": token_version, "iat": now, "exp": now + 300, **overrides}


def without(values: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in values.items() if name != key}


def signed(values: dict[str, Any], key: str = settings.secret_key) -> str:
    return jwt.encode(values, key, algorithm="HS256")


def b64url_json(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def zero_usage(monthly_token_limit: int) -> dict[str, Any]:
    return {
        "month": this_month(),
        "chat_tokens": 0,
        "embedding_tokens": 0,
        "vision_tokens": 0,
        "cost_usd": 0.0,
        "monthly_token_limit": monthly_token_limit,
    }


# ---------------- Login ----------------


async def test_login_returns_me_sets_the_session_cookie_and_records_the_login(
    create_user: CreateUser, client: ApiClient
) -> None:
    limits = {
        "monthly_token_limit": 5000,
        "rate_limit_per_min": 7,
        "max_upload_mb": 3,
        "max_documents": 4,
        "max_storage_mb": 50,
    }
    alice = await create_user("alice", login=False, **limits)
    assert (await get_user("alice")).last_login_at is None
    started = datetime.now(timezone.utc)

    response = await client.login("alice", USER_PASSWORD)

    assert response.status_code == 200
    assert response.json() == {
        "id": alice.user_id,
        "username": "alice",
        "is_admin": False,
        "limits": limits,
        "usage": zero_usage(5000),
    }
    cookie = session_cookie(response)
    assert cookie["httponly"] is True
    assert cookie["samesite"].lower() == "strict"
    assert cookie["path"] == "/"
    assert cookie["max-age"] == str(settings.access_token_expire_minutes * 60)
    assert not cookie["secure"]
    token = jwt.decode(cookie.value, settings.secret_key, algorithms=["HS256"])
    assert (token["sub"], token["tv"]) == (str(alice.user_id), 0)
    assert token["exp"] - token["iat"] == settings.access_token_expire_minutes * 60
    assert client.cookies.get(settings.cookie_name) == cookie.value
    last_login_at = (await get_user("alice")).last_login_at
    assert last_login_at is not None
    assert started <= last_login_at <= datetime.now(timezone.utc)
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == response.json()


async def test_session_cookie_is_secure_when_configured(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cookie_secure", True)

    response = await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)

    assert response.status_code == 200
    assert session_cookie(response)["secure"] is True


async def test_login_username_is_trimmed_and_case_insensitive(create_user: CreateUser, client: ApiClient) -> None:
    alice = await create_user("alice", login=False)

    response = await client.login("  ALICE ", USER_PASSWORD)

    assert response.status_code == 200
    assert (response.json()["id"], response.json()["username"]) == (alice.user_id, "alice")


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", WRONG_PASSWORD), ("alice", USER_PASSWORD.upper()), ("nobody", USER_PASSWORD)],
    ids=["wrong password", "password is case sensitive", "unknown user"],
)
async def test_bad_credentials_get_401_and_no_session(
    username: str, password: str, create_user: CreateUser, client: ApiClient
) -> None:
    await create_user("alice", login=False)

    response = await client.login(username, password)

    assert response.status_code == 401
    assert response.json() == INVALID_LOGIN
    assert settings.cookie_name not in set_cookies(response)
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await get_user("alice")).last_login_at is None


async def test_inactive_user_gets_the_bad_credentials_401(create_user: CreateUser, client: ApiClient) -> None:
    await create_user("carol", login=False, is_active=False)

    response = await client.login("carol", USER_PASSWORD)

    assert response.status_code == 401
    assert response.json() == INVALID_LOGIN
    assert settings.cookie_name not in set_cookies(response)
    assert (await get_user("carol")).last_login_at is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": "alice"},
        {"password": USER_PASSWORD},
        {"username": "", "password": USER_PASSWORD},
        {"username": "   ", "password": USER_PASSWORD},
        {"username": "a" * 65, "password": USER_PASSWORD},
        {"username": "alice", "password": ""},
        {"username": "alice", "password": "x" * 1025},
        {"username": ["alice"], "password": USER_PASSWORD},
    ],
)
async def test_malformed_login_requests_get_422(body: dict[str, Any], client: ApiClient) -> None:
    response = await client.post("/api/auth/login", json=body)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert settings.cookie_name not in set_cookies(response)


@pytest.mark.parametrize(
    "raw_body",
    [b'{"username": "ali\\ud800", "password": "x"}', b'{"username": "alice", "password": "pass\\udfffword"}'],
    ids=["username", "password"],
)
async def test_lone_surrogates_get_422_not_500(raw_body: bytes, client: ApiClient) -> None:
    response = await client.post("/api/auth/login", content=raw_body, headers={"Content-Type": "application/json"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_unicode"


async def test_nul_characters_in_the_username_are_ignored(create_user: CreateUser, client: ApiClient) -> None:
    await create_user("alice", login=False)

    # Postgres can't compare text with NUL in it: this used to be a 500 there.
    assert (await client.login("ali\x00ce", WRONG_PASSWORD)).status_code == 401
    assert (await client.login("ali\x00ce", USER_PASSWORD)).status_code == 200


async def test_login_upgrades_an_outdated_password_hash(create_user: CreateUser, client: ApiClient) -> None:
    await create_user("alice", login=False)
    outdated = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(USER_PASSWORD)
    await set_user_columns("alice", password_hash=outdated)
    assert needs_rehash(outdated)

    response = await client.login("alice", USER_PASSWORD)

    assert response.status_code == 200
    stored = (await get_user("alice")).password_hash
    assert stored != outdated
    assert not needs_rehash(stored)
    assert verify_password(USER_PASSWORD, stored)


# ---------------- Login throttle ----------------


async def test_failed_logins_throttle_the_username_in_every_spelling(
    create_user: CreateUser, client: ApiClient, client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_window_seconds", 600)
    await create_user("alice", login=False)
    await create_user("bob", login=False)
    spellings = ["alice", "ALICE", "  Alice  ", "aLiCe"]
    for attempt in range(settings.login_max_attempts):
        response = await client.login(spellings[attempt % len(spellings)], WRONG_PASSWORD)
        assert response.status_code == 401, attempt

    elsewhere = client_at(OTHER_IP)
    throttled = await login(elsewhere, "Alice", USER_PASSWORD)

    assert throttled.status_code == 429
    assert throttled.json() == {"detail": "Too many failed login attempts. Try again in 10 minutes."}
    assert 0 < int(throttled.headers["retry-after"]) <= 600
    assert settings.cookie_name not in set_cookies(throttled)
    assert (await login(elsewhere, "bob", USER_PASSWORD)).status_code == 200


async def test_failed_logins_throttle_the_client_ip_for_every_username(
    create_user: CreateUser, client: ApiClient, client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 3)
    monkeypatch.setattr(settings, "login_max_attempts_per_ip", 3)
    await create_user("alice", login=False)
    for username in ("ghost1", "ghost2", "ghost3"):
        assert (await client.login(username, WRONG_PASSWORD)).status_code == 401

    throttled = await client.login("alice", USER_PASSWORD)

    assert throttled.status_code == 429
    assert throttled.json()["detail"].startswith("Too many failed login attempts.")
    assert int(throttled.headers["retry-after"]) > 0
    assert settings.cookie_name not in set_cookies(throttled)
    assert (await login(client_at(OTHER_IP), "alice", USER_PASSWORD)).status_code == 200


async def test_successful_login_resets_the_username_counter_but_not_the_ip_counter(
    create_user: CreateUser, client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 3)
    monkeypatch.setattr(settings, "login_max_attempts_per_ip", 3)
    await create_user("alice", login=False)
    first, second, third = client_at("203.0.113.1"), client_at("203.0.113.2"), client_at("203.0.113.3")
    for _ in range(2):
        assert (await login(first, "alice", WRONG_PASSWORD)).status_code == 401
    assert (await login(second, "alice", USER_PASSWORD)).status_code == 200
    for _ in range(2):
        assert (await login(third, "alice", WRONG_PASSWORD)).status_code == 401

    assert (await login(third, "alice", USER_PASSWORD)).status_code == 200
    assert (await login(first, "ghost", WRONG_PASSWORD)).status_code == 401
    assert (await login(first, "alice", USER_PASSWORD)).status_code == 429


@pytest.mark.parametrize("same_username", [True, False], ids=["one username", "one ip"])
async def test_parallel_guesses_cannot_outrun_the_throttle(
    same_username: bool, create_user: CreateUser, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 3)
    monkeypatch.setattr(settings, "login_max_attempts_per_ip", 3)
    await create_user("alice", login=False)
    usernames = ["alice" if same_username else f"guess{n}" for n in range(12)]

    responses = await asyncio.gather(*(client.login(name, WRONG_PASSWORD) for name in usernames))

    assert sorted(response.status_code for response in responses) == [401] * 3 + [429] * 9
    assert (await client.login("alice", USER_PASSWORD)).status_code == 429


async def test_failure_counters_use_a_fixed_window(
    create_user: CreateUser, client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_window_seconds", 600)
    await create_user("alice", login=False)
    client = client_at(OTHER_IP)
    redis = get_redis()
    keys = ("login:fail:user:alice", f"login:fail:ip:{OTHER_IP}")

    await login(client, "Alice", WRONG_PASSWORD)

    for key in keys:
        assert await redis.get(key) == "1"
        assert 0 < await redis.ttl(key) <= 600
        await redis.expire(key, 100)

    await login(client, "alice", WRONG_PASSWORD)

    for key in keys:
        assert await redis.get(key) == "2"
        assert 0 < await redis.ttl(key) <= 100


@pytest.mark.parametrize(
    ("user_ttl", "ip_ttl", "wait"),
    [(90, 30, "2 minutes"), (45, 45, "1 minute"), (30, 100, "2 minutes")],
)
async def test_throttle_reports_the_longest_wait_and_does_not_count_rejected_attempts(
    user_ttl: int, ip_ttl: int, wait: str, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 1)
    monkeypatch.setattr(settings, "login_max_attempts_per_ip", 1)
    assert (await client.login("ghost", WRONG_PASSWORD)).status_code == 401
    redis = get_redis()
    await redis.expire("login:fail:user:ghost", user_ttl)
    await redis.expire("login:fail:ip:127.0.0.1", ip_ttl)

    response = await client.login("Ghost", WRONG_PASSWORD)

    assert response.status_code == 429
    assert response.json() == {"detail": f"Too many failed login attempts. Try again in {wait}."}
    longest = max(user_ttl, ip_ttl)
    assert longest - 1 <= int(response.headers["retry-after"]) <= longest
    assert await redis.mget(["login:fail:user:ghost", "login:fail:ip:127.0.0.1"]) == ["1", "1"]


async def test_successful_logins_do_not_count_against_the_ip(
    create_user: CreateUser, client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 2)
    monkeypatch.setattr(settings, "login_max_attempts_per_ip", 2)
    await create_user("alice", login=False)

    for _ in range(4):
        assert (await client.login("alice", USER_PASSWORD)).status_code == 200

    assert (await client.login("ghost", WRONG_PASSWORD)).status_code == 401
    assert (await client.login("alice", USER_PASSWORD)).status_code == 200


async def test_typos_of_several_users_behind_one_ip_do_not_lock_out_everyone(
    create_user: CreateUser, client: ApiClient
) -> None:
    # Behind a proxy or Docker's port publishing every client has the same IP.
    for name in ("alice", "bob", "carol", "dave"):
        await create_user(name, login=False)
    for name, typos in (("alice", 4), ("bob", 3), ("carol", 3)):
        for _ in range(typos):
            assert (await client.login(name, WRONG_PASSWORD)).status_code == 401

    assert (await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 200
    assert (await client.login("dave", USER_PASSWORD)).status_code == 200
    assert (await client.login("alice", USER_PASSWORD)).status_code == 200


async def test_the_ip_is_throttled_after_its_own_larger_limit(
    create_user: CreateUser, client: ApiClient, client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts_per_ip", 15)
    await create_user("alice", login=False)
    for attempt in range(15):
        assert (await client.login(f"ghost{attempt}", WRONG_PASSWORD)).status_code == 401, attempt

    assert (await client.login("alice", USER_PASSWORD)).status_code == 429
    assert (await login(client_at(OTHER_IP), "alice", USER_PASSWORD)).status_code == 200


async def test_login_while_redis_is_down_is_a_503_with_a_message(client: ApiClient) -> None:
    set_redis(Redis.from_url("redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=1))

    response = await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)

    assert response.status_code == 503
    assert response.json() == {"detail": "A required service is temporarily unavailable. Try again shortly."}


# ---------------- Sessions ----------------


async def test_me_requires_a_session(client: ApiClient) -> None:
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


async def test_me_reports_the_users_limits_and_this_months_usage(create_user: CreateUser) -> None:
    alice = await create_user("alice", monthly_token_limit=5000, max_upload_mb=3)
    bob = await create_user("bob")
    async with SessionLocal() as session:
        session.add_all(
            [
                UsageEvent(user_id=alice.user_id, model_name="m", kind="chat", total_tokens=120, cost_usd=0.25),
                UsageEvent(user_id=alice.user_id, model_name="m", kind="chat", total_tokens=80, cost_usd=0.5),
                UsageEvent(user_id=alice.user_id, model_name="e", kind="embedding", total_tokens=40, cost_usd=0.01),
                UsageEvent(user_id=alice.user_id, model_name="v", kind="vision", total_tokens=3000, cost_usd=0.02),
                UsageEvent(
                    user_id=alice.user_id,
                    model_name="m",
                    kind="chat",
                    total_tokens=9000,
                    cost_usd=9.0,
                    created_at=month_start() - timedelta(seconds=1),
                ),
                UsageEvent(user_id=bob.user_id, model_name="m", kind="chat", total_tokens=500, cost_usd=1.0),
            ]
        )
        await session.commit()

    response = await alice.get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "id": alice.user_id,
        "username": "alice",
        "is_admin": False,
        "limits": {
            "monthly_token_limit": 5000,
            "rate_limit_per_min": settings.default_rate_limit_per_min,
            "max_upload_mb": 3,
            "max_documents": settings.default_max_documents,
            "max_storage_mb": settings.default_max_storage_mb,
        },
        "usage": {
            **body["usage"],
            "month": this_month(),
            "chat_tokens": 200,
            "embedding_tokens": 40,
            "vision_tokens": 3000,
        },
    }
    assert body["usage"]["cost_usd"] == pytest.approx(0.78)  # every kind
    assert body["usage"]["monthly_token_limit"] == 5000


async def test_me_reports_the_admin_as_unlimited(admin_client: ApiClient) -> None:
    response = await admin_client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": admin_client.user_id,
        "username": ADMIN_USERNAME,
        "is_admin": True,
        "limits": {
            "monthly_token_limit": 0,
            "rate_limit_per_min": 0,
            "max_upload_mb": settings.max_upload_mb_hard,
            "max_documents": 0,
            "max_storage_mb": 0,
        },
        "usage": zero_usage(0),
    }


async def test_bearer_header_authenticates_like_the_cookie(create_user: CreateUser, client: ApiClient) -> None:
    alice = await create_user("alice")
    token = session_token(alice)

    for scheme in ("Bearer", "bearer"):
        response = await client.get("/api/auth/me", headers={"Authorization": f"{scheme} {token}"})
        assert response.status_code == 200, scheme
        assert response.json()["id"] == alice.user_id

    conversations = await client.get("/api/conversations", headers=bearer(token))
    assert conversations.status_code == 200
    assert conversations.json() == []
    assert not client.cookies


@pytest.mark.parametrize("template", ["Basic {token}", "Token {token}", "{token}", "Bearer"])
async def test_other_authorization_headers_are_rejected(
    template: str, create_user: CreateUser, client: ApiClient
) -> None:
    alice = await create_user("alice")

    response = await client.get("/api/auth/me", headers={"Authorization": template.format(token=session_token(alice))})

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


async def test_tampered_expired_and_stale_tokens_are_rejected(
    create_user: CreateUser, admin_client: ApiClient, client: ApiClient
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None and admin_client.user_id is not None
    real = session_token(alice)
    header, _, signature = real.split(".")
    real_claims = jwt.decode(real, options={"verify_signature": False})
    now = int(datetime.now(timezone.utc).timestamp())
    valid = signed(claims(alice.user_id))
    bad_tokens = {
        "signed with another key": signed(claims(alice.user_id), "another-secret-key-with-at-least-32-characters"),
        "subject swapped under the original signature": (
            f"{header}.{b64url_json({**real_claims, 'sub': str(admin_client.user_id)})}.{signature}"
        ),
        "altered signature": real[:-4] + ("AAAA" if not real.endswith("AAAA") else "BBBB"),
        "unsigned (alg none)": f"{b64url_json({'alg': 'none', 'typ': 'JWT'})}.{b64url_json(claims(alice.user_id))}.",
        "expired": signed(claims(alice.user_id, iat=now - 7200, exp=now - 60)),
        "no expiry": signed(without(claims(alice.user_id), "exp")),
        "no subject": signed(without(claims(alice.user_id), "sub")),
        "no token version": signed(without(claims(alice.user_id), "tv")),
        "wrong token version": signed(claims(alice.user_id, token_version=1)),
        "non-numeric subject": signed(claims(alice.user_id, sub="alice")),
        "unknown user": signed(claims(999_999)),
        "not a JWT": "not-a-jwt",
    }

    for name, token in {"forged with the app key": valid, **bad_tokens}.items():
        by_cookie = await client.get("/api/auth/me", headers={"Cookie": f"{settings.cookie_name}={token}"})
        by_header = await client.get("/api/auth/me", headers=bearer(token))
        expected = 200 if token is valid else 401
        assert (by_cookie.status_code, by_header.status_code) == (expected, expected), name
        if expected == 401:
            assert by_cookie.json() == by_header.json() == NOT_AUTHENTICATED, name


async def test_admin_routes_need_an_admin_session(
    create_user: CreateUser, admin_client: ApiClient, client: ApiClient
) -> None:
    alice = await create_user("alice")

    anonymous = await client.get("/api/admin/config")
    forbidden = await alice.get("/api/admin/config")
    allowed = await admin_client.get("/api/admin/config")

    assert (anonymous.status_code, anonymous.json()) == (401, NOT_AUTHENTICATED)
    assert (forbidden.status_code, forbidden.json()) == (403, {"detail": "Admin access required"})
    assert allowed.status_code == 200


async def test_deactivating_a_user_ends_their_sessions(
    create_user: CreateUser, admin_client: ApiClient, client: ApiClient
) -> None:
    alice = await create_user("alice")
    token = session_token(alice)

    deactivated = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"is_active": False})

    assert deactivated.status_code == 200
    assert (await alice.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401
    refused = await client.login("alice", USER_PASSWORD)
    assert (refused.status_code, refused.json()) == (401, INVALID_LOGIN)

    reactivated = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json={"is_active": True})

    assert reactivated.status_code == 200
    assert (await alice.get("/api/auth/me")).status_code == 401
    assert (await client.login("alice", USER_PASSWORD)).status_code == 200


async def test_inactive_flag_alone_rejects_an_otherwise_valid_session(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    assert (await alice.get("/api/auth/me")).status_code == 200

    await set_user_columns("alice", is_active=False)

    response = await alice.get("/api/auth/me")
    assert (response.status_code, response.json()) == (401, NOT_AUTHENTICATED)


# ---------------- Logout ----------------


async def test_logout_clears_the_session_cookie(create_user: CreateUser) -> None:
    alice = await create_user("alice")

    response = await alice.post("/api/auth/logout")

    assert response.status_code == 204
    assert response.content == b""
    cookie = session_cookie(response)
    assert cookie.value == ""
    assert cookie["max-age"] == "0"
    assert cookie["httponly"] is True
    assert cookie["samesite"].lower() == "strict"
    assert cookie["path"] == "/"
    assert alice.cookies.get(settings.cookie_name) is None
    assert (await alice.get("/api/auth/me")).status_code == 401


async def test_logout_without_a_session_still_succeeds(client: ApiClient) -> None:
    response = await client.post("/api/auth/logout")

    assert response.status_code == 204
    assert session_cookie(response)["max-age"] == "0"


# ---------------- Change password ----------------


async def test_change_password_ends_old_sessions_and_reissues_the_cookie(
    create_user: CreateUser, new_client: Callable[[], ApiClient], client: ApiClient
) -> None:
    alice = await create_user("alice")
    other_session = new_client()
    assert (await other_session.login("alice", USER_PASSWORD)).status_code == 200
    old_token = session_token(alice)
    old_version = (await get_user("alice")).token_version

    response = await alice.post(
        "/api/auth/change-password", json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 204
    assert response.content == b""
    cookie = session_cookie(response)
    assert cookie["httponly"] is True
    assert cookie["samesite"].lower() == "strict"
    assert cookie.value != old_token
    assert jwt.decode(cookie.value, settings.secret_key, algorithms=["HS256"])["tv"] == old_version + 1
    assert session_token(alice) == cookie.value
    stored = await get_user("alice")
    assert stored.token_version == old_version + 1
    assert verify_password(NEW_PASSWORD, stored.password_hash)

    assert (await alice.get("/api/auth/me")).status_code == 200
    assert (await other_session.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(old_token))).status_code == 401
    assert (await client.login("alice", USER_PASSWORD)).status_code == 401
    assert (await client.login("alice", NEW_PASSWORD)).status_code == 200


@pytest.mark.parametrize("admin_change", [{"password": "reset-by-the-admin"}, {"is_active": False}])
async def test_change_password_racing_the_admin_cannot_undo_a_reset_or_deactivation(
    admin_change: dict[str, Any],
    create_user: CreateUser,
    admin_client: ApiClient,
    client: ApiClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = await create_user("alice")
    count_attempt = auth_router._count_attempt

    async def admin_acts_meanwhile(*args: Any) -> None:
        await count_attempt(*args)
        response = await admin_client.patch(f"/api/admin/users/{alice.user_id}", json=admin_change)
        assert response.status_code == 200

    monkeypatch.setattr(auth_router, "_count_attempt", admin_acts_meanwhile)
    response = await alice.post(
        "/api/auth/change-password", json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 401
    assert settings.cookie_name not in set_cookies(response)
    stored = await get_user("alice")
    assert stored.token_version == 1  # only the admin's change counted
    assert not verify_password(NEW_PASSWORD, stored.password_hash)
    if "password" in admin_change:
        assert (await client.login("alice", "reset-by-the-admin")).status_code == 200


async def test_change_password_with_the_wrong_current_password_changes_nothing(
    create_user: CreateUser, client: ApiClient
) -> None:
    alice = await create_user("alice")
    before = await get_user("alice")

    response = await alice.post(
        "/api/auth/change-password", json={"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Current password is incorrect"}
    assert settings.cookie_name not in set_cookies(response)
    after = await get_user("alice")
    assert (after.token_version, after.password_hash) == (before.token_version, before.password_hash)
    assert (await alice.get("/api/auth/me")).status_code == 200
    assert (await client.login("alice", USER_PASSWORD)).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"current_password": USER_PASSWORD, "new_password": "short12"},
        {"current_password": USER_PASSWORD, "new_password": "x" * 257},
        {"current_password": USER_PASSWORD},
        {"new_password": NEW_PASSWORD},
        {"current_password": "", "new_password": NEW_PASSWORD},
    ],
    ids=["new too short", "new too long", "new missing", "current missing", "current empty"],
)
async def test_invalid_change_password_requests_get_422(body: dict[str, Any], create_user: CreateUser) -> None:
    alice = await create_user("alice")
    before = await get_user("alice")

    response = await alice.post("/api/auth/change-password", json=body)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    after = await get_user("alice")
    assert (after.token_version, after.password_hash) == (before.token_version, before.password_hash)
    assert (await alice.get("/api/auth/me")).status_code == 200


async def test_change_password_requires_a_session(client: ApiClient) -> None:
    response = await client.post(
        "/api/auth/change-password", json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert (response.status_code, response.json()) == (401, NOT_AUTHENTICATED)


async def test_env_admin_cannot_change_password_through_the_api(admin_client: ApiClient, client: ApiClient) -> None:
    before = await get_user(ADMIN_USERNAME)

    response = await admin_client.post(
        "/api/auth/change-password", json={"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "The admin password is managed in .env"}
    after = await get_user(ADMIN_USERNAME)
    assert (after.token_version, after.password_hash) == (before.token_version, before.password_hash)
    assert (await admin_client.get("/api/auth/me")).status_code == 200
    assert (await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 200


async def test_guessing_the_current_password_is_throttled(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 2)
    alice = await create_user("alice")
    wrong = {"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD}
    for _ in range(2):
        assert (await alice.post("/api/auth/change-password", json=wrong)).status_code == 400

    response = await alice.post(
        "/api/auth/change-password", json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert verify_password(USER_PASSWORD, (await get_user("alice")).password_hash)
    assert (await alice.get("/api/auth/me")).status_code == 200


async def test_successful_password_change_clears_earlier_failures(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 2)
    alice = await create_user("alice")
    wrong = {"current_password": WRONG_PASSWORD, "new_password": USER_PASSWORD}
    assert (await alice.post("/api/auth/change-password", json=wrong)).status_code == 400

    changed = await alice.post(
        "/api/auth/change-password", json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert changed.status_code == 204
    for _ in range(2):
        assert (await alice.post("/api/auth/change-password", json=wrong)).status_code == 400
