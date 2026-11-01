"""Signing in by email or username (docs/briefs/signup-auth.md 4.4): the identifier's forms, only verified addresses,
password-less accounts, one generic 401, one counter per account, the adaptive Turnstile challenge (per identifier
and network, per network, the site-wide surge; never without Turnstile or for local requests; an outage falls back
to the hard limits), ADMIN_SIGNIN_FROM=local and the `username` alias."""

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, USER_PASSWORD, ApiClient
from fake_turnstile import FakeTurnstile, turnstile_token
from fastapi import FastAPI
from signup_support import give_email, set_user
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import LoginEvent
from app.redis_client import get_redis
from app.services.auth import turnstile

CreateUser = Callable[..., Awaitable[ApiClient]]
ClientAt = Callable[..., httpx.AsyncClient]

INVALID = {"detail": "Incorrect email, username or password."}
CHALLENGE = {"detail": "Confirm you're not a robot to continue.", "code": "challenge_required"}
WRONG = "not-the-password"


@pytest.fixture
async def client_at(app: FastAPI) -> AsyncIterator[ClientAt]:
    """Anonymous clients from a given address, asking for a given host."""
    clients: list[httpx.AsyncClient] = []

    def factory(ip: str, host: str = "testserver") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(ip, 50000))
        client = httpx.AsyncClient(transport=transport, base_url=f"http://{host}")
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


async def sign_in(client: httpx.AsyncClient, identifier: str, password: str, **extra: str) -> httpx.Response:
    return await client.post("/api/auth/login", json={"identifier": identifier, "password": password, **extra})


async def methods(user_id: int) -> list[str]:
    async with SessionLocal() as session:
        return list(
            await session.scalars(
                select(LoginEvent.method).where(LoginEvent.user_id == user_id).order_by(LoginEvent.id)
            )
        )


async def test_an_account_signs_in_by_its_verified_email_or_its_username(
    create_user: CreateUser, client: ApiClient
) -> None:
    alice = await create_user("alice", login=False)
    assert alice.user_id is not None
    await give_email(alice.user_id, "Alice.Smith@Example.COM")

    by_email = await sign_in(client, "  ALICE.smith@example.com ", USER_PASSWORD)
    by_username = await sign_in(client, "Alice", USER_PASSWORD)
    alias = await client.post("/api/auth/login", json={"username": "alice", "password": USER_PASSWORD})

    for response in (by_email, by_username, alias):
        assert response.status_code == 200, response.text
        assert (response.json()["id"], response.json()["email"]) == (alice.user_id, "Alice.Smith@example.com")
    assert await methods(alice.user_id) == ["password"] * 3


@pytest.mark.parametrize(
    "identifier", ["nobody@example.com", "not an address @", "a@b", "alice@example.com\x00", "ghost"]
)
async def test_unknown_and_invalid_identifiers_get_the_generic_401(
    identifier: str, create_user: CreateUser, client: ApiClient
) -> None:
    alice = await create_user("alice", login=False)
    assert alice.user_id is not None
    await give_email(alice.user_id, "alice@example.org")

    response = await sign_in(client, identifier, USER_PASSWORD)

    assert (response.status_code, response.json()) == (401, INVALID)


async def test_unverified_and_pending_addresses_never_sign_in(
    create_user: CreateUser, client: ApiClient, mailer: None
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    requested = await alice.post("/api/me/email", json={"email": "alice@example.com", "password": USER_PASSWORD})
    assert requested.status_code == 202, requested.text

    response = await sign_in(client, "alice@example.com", USER_PASSWORD)

    assert (response.status_code, response.json()) == (401, INVALID)


async def test_an_account_without_a_password_and_inactive_accounts_get_the_generic_401(
    create_user: CreateUser, client: ApiClient
) -> None:
    alice = await create_user("alice", login=False)
    bob = await create_user("bob", login=False, is_active=False)
    assert alice.user_id is not None and bob.user_id is not None
    await set_user(alice.user_id, password_hash=None)

    for name in ("alice", "bob"):
        response = await sign_in(client, name, USER_PASSWORD)
        assert (response.status_code, response.json()) == (401, INVALID), name


async def test_the_admin_has_no_email_sign_in(admin_client: ApiClient, client: ApiClient) -> None:
    response = await sign_in(client, "admin@example.com", ADMIN_PASSWORD)

    assert (response.status_code, response.json()) == (401, INVALID)


async def test_the_email_and_the_username_share_the_accounts_counter(
    create_user: CreateUser, client: ApiClient, client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 4)
    alice = await create_user("alice", login=False)
    assert alice.user_id is not None
    await give_email(alice.user_id, "alice@example.com")
    for identifier in ("alice", "alice@example.com", "ALICE", "Alice@Example.com"):
        assert (await sign_in(client, identifier, WRONG)).status_code == 401

    throttled = await sign_in(client_at("198.51.100.4"), "alice", USER_PASSWORD)

    assert throttled.status_code == 429
    assert throttled.json()["code"] == "too_many_attempts"
    assert await get_redis().get(f"auth:login:id:acct:{alice.user_id}") == "4"


async def test_unknown_identifiers_have_counters_too(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 2)
    for _ in range(2):
        assert (await sign_in(client, "ghost@example.com", WRONG)).status_code == 401

    response = await sign_in(client, "GHOST@example.com", WRONG)

    assert response.status_code == 429


# ---------------- The challenge ----------------


async def test_no_challenge_without_turnstile(create_user: CreateUser, client: ApiClient) -> None:
    await create_user("alice", login=False)
    for _ in range(settings.login_challenge_after + 2):
        assert (await sign_in(client, "alice", WRONG)).status_code == 401

    assert (await sign_in(client, "alice", USER_PASSWORD)).status_code == 200


async def test_three_failures_from_one_network_challenge_that_identifier_there_only(
    create_user: CreateUser, client: ApiClient, client_at: ClientAt, fake_turnstile: FakeTurnstile
) -> None:
    await create_user("alice", login=False)
    await create_user("bob", login=False)
    for _ in range(settings.login_challenge_after):
        assert (await sign_in(client, "alice", WRONG)).status_code == 401
    checks = len(fake_turnstile.requests)

    challenged = await sign_in(client, "alice", USER_PASSWORD)
    other_account = await sign_in(client, "bob", USER_PASSWORD)
    passed = await sign_in(client, "alice", USER_PASSWORD, turnstile_token=turnstile_token("login"))

    assert (challenged.status_code, challenged.json()) == (401, CHALLENGE)
    assert other_account.status_code == passed.status_code == 200
    assert len(fake_turnstile.requests) == checks + 1  # only the challenged retry asked Cloudflare
    assert fake_turnstile.requests[-1]["remoteip"] == "127.0.0.1"


async def test_another_network_isnt_challenged_for_the_identifier(
    create_user: CreateUser, client: ApiClient, client_at: ClientAt, fake_turnstile: FakeTurnstile
) -> None:
    await create_user("alice", login=False)
    for _ in range(settings.login_challenge_after):
        assert (await sign_in(client, "alice", WRONG)).status_code == 401

    assert (await sign_in(client_at("198.51.100.4"), "alice", USER_PASSWORD)).status_code == 200
    assert fake_turnstile.requests == []


async def test_a_challenged_request_is_neither_checked_nor_counted(
    create_user: CreateUser, client: ApiClient, fake_turnstile: FakeTurnstile
) -> None:
    alice = await create_user("alice", login=False)
    for _ in range(settings.login_challenge_after):
        await sign_in(client, "alice", WRONG)

    for _ in range(5):
        assert (await sign_in(client, "alice", WRONG)).json() == CHALLENGE

    assert await get_redis().get(f"auth:login:id:acct:{alice.user_id}") == str(settings.login_challenge_after)


async def test_a_failed_challenge_is_400_and_a_wrong_password_with_a_good_token_is_401(
    create_user: CreateUser, client: ApiClient, fake_turnstile: FakeTurnstile
) -> None:
    await create_user("alice", login=False)
    for _ in range(settings.login_challenge_after):
        await sign_in(client, "alice", WRONG)

    failed = await sign_in(client, "alice", USER_PASSWORD, turnstile_token="fail:login:x")
    wrong = await sign_in(client, "alice", WRONG, turnstile_token=turnstile_token("login"))

    assert (failed.status_code, failed.json()["code"]) == (400, "challenge_failed")
    assert (wrong.status_code, wrong.json()) == (401, INVALID)


async def test_a_stranger_elsewhere_cant_force_the_challenge_on_the_owner(
    create_user: CreateUser, client: ApiClient, client_at: ClientAt, fake_turnstile: FakeTurnstile
) -> None:
    await create_user("alice", login=False)
    stranger = client_at("203.0.113.9")
    for _ in range(settings.login_challenge_after + 3):
        assert (await sign_in(stranger, "alice", WRONG)).status_code in (401,)

    assert (await sign_in(client, "alice", USER_PASSWORD)).status_code == 200


async def test_a_network_with_many_failures_is_challenged_for_every_identifier(
    create_user: CreateUser, client: ApiClient, fake_turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_challenge_after_per_ip", 4)
    await create_user("alice", login=False)
    for number in range(4):
        assert (await sign_in(client, f"ghost{number}", WRONG)).status_code == 401

    assert (await sign_in(client, "alice", USER_PASSWORD)).json() == CHALLENGE


async def test_a_site_wide_surge_challenges_everyone(
    create_user: CreateUser, client_at: ClientAt, fake_turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_site_challenge_after", 10)
    await create_user("alice", login=False)
    for number in range(11):
        assert (await sign_in(client_at(f"198.51.{number}.1"), f"ghost{number}", WRONG)).status_code == 401

    response = await sign_in(client_at("192.0.2.200"), "alice", USER_PASSWORD)

    assert response.json() == CHALLENGE
    assert await get_redis().exists("auth:login:site_challenge")


async def test_local_requests_are_never_challenged(
    create_user: CreateUser, client_at: ClientAt, fake_turnstile: FakeTurnstile
) -> None:
    await create_user("alice", login=False)
    local = client_at("172.18.0.1", host="localhost:8080")
    for _ in range(settings.login_challenge_after + 2):
        assert (await sign_in(local, "alice", WRONG)).status_code == 401

    assert (await sign_in(local, "alice", USER_PASSWORD)).status_code == 200


async def test_a_turnstile_outage_at_sign_in_carries_on_without_the_challenge(
    create_user: CreateUser, client: ApiClient, fake_turnstile: FakeTurnstile
) -> None:
    await create_user("alice", login=False)
    for _ in range(settings.login_challenge_after):
        await sign_in(client, "alice", WRONG)
    fake_turnstile.outage = "500"

    response = await sign_in(client, "alice", USER_PASSWORD, turnstile_token=turnstile_token("login"))

    assert response.status_code == 200
    assert await turnstile.unavailable_last_hour() == 1  # the monitor hears of it


async def test_during_a_turnstile_outage_the_hard_limits_still_apply(
    create_user: CreateUser, client: ApiClient, fake_turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_max_attempts", 5)
    await create_user("alice", login=False)
    for _ in range(settings.login_challenge_after):
        await sign_in(client, "alice", WRONG)
    fake_turnstile.outage = "timeout"
    for _ in range(5 - settings.login_challenge_after):
        assert (await sign_in(client, "alice", WRONG, turnstile_token=turnstile_token("login"))).status_code == 401

    response = await sign_in(client, "alice", USER_PASSWORD, turnstile_token=turnstile_token("login"))

    assert (response.status_code, response.json()["code"]) == (429, "too_many_attempts")


# ---------------- ADMIN_SIGNIN_FROM ----------------


async def test_admin_signin_from_local_refuses_the_admin_elsewhere(
    client_at: ClientAt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_signin_from", "local")

    outside = await sign_in(client_at("198.51.100.4"), ADMIN_USERNAME, ADMIN_PASSWORD)
    tunnel = await sign_in(client_at("172.18.0.1", host="asknuvexusdoc.example"), ADMIN_USERNAME, ADMIN_PASSWORD)
    local = await sign_in(client_at("172.18.0.1", host="localhost:8080"), ADMIN_USERNAME, ADMIN_PASSWORD)

    assert (outside.status_code, outside.json()) == (401, INVALID)
    assert (tunnel.status_code, tunnel.json()) == (401, INVALID)
    assert local.status_code == 200


async def test_admin_signin_from_any_lets_the_admin_in_from_anywhere(client_at: ClientAt) -> None:
    assert (await sign_in(client_at("198.51.100.4"), ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"identifier": "alice", "username": "alice", "password": USER_PASSWORD},
        {"identifier": "a" * 321, "password": USER_PASSWORD},
        {"identifier": "alice", "password": USER_PASSWORD, "turnstile_token": ""},
    ],
    ids=["both names", "too long", "empty token"],
)
async def test_malformed_sign_ins_get_422(body: dict[str, object], client: ApiClient) -> None:
    assert (await client.post("/api/auth/login", json=body)).status_code == 422
