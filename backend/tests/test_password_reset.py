"""Forgot and reset password (docs/briefs/signup-auth.md 4.7): "forgot" (its availability, limits, Turnstile and the
silent per-address limit), the link's check, and the reset: every session ended, no session issued, the other reset
links revoked, a pending email change cancelled, the counters cleared, the notice queued, and the Google
disconnect."""

from collections.abc import Awaitable, Callable

import httpx
import pytest
from conftest import USER_PASSWORD, ApiClient, drain_mail
from fake_mail import FakeMail
from fake_turnstile import FakeTurnstile, turnstile_token
from signup_support import (
    event_types,
    give_email,
    identity_of,
    jobs,
    live_tokens,
    pending_email,
    reset_token,
    set_cookies,
    set_user,
    user_row,
)

from app import db
from app.config import settings
from app.deps import session_cookie_name
from app.models import UserIdentity
from app.redis_client import get_redis
from app.security import verify_password
from app.services.auth import throttle
from app.services.auth.throttle import Limit, Window

CreateUser = Callable[..., Awaitable[ApiClient]]
NewClient = Callable[[], ApiClient]

ADDRESS = "alice@example.com"
NEW_PASSWORD = "a-new-and-long-password"
INVALID = {"detail": "This link has expired or was already used.", "code": "link_invalid"}


@pytest.fixture
def turnstile(mailer: None, fake_turnstile: FakeTurnstile) -> FakeTurnstile:
    return fake_turnstile


async def alice_with_email(create_user: CreateUser) -> ApiClient:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await give_email(alice.user_id, ADDRESS)
    return alice


async def forgot(client: httpx.AsyncClient, email: str = ADDRESS, token: str | None = None) -> httpx.Response:
    body = {"email": email, "turnstile_token": token or turnstile_token("reset")}
    return await client.post("/api/auth/password/forgot", json=body)


async def reset(client: httpx.AsyncClient, token: str, password: str = NEW_PASSWORD, **extra: object) -> httpx.Response:
    return await client.post("/api/auth/password/reset", json={"token": token, "new_password": password, **extra})


async def link_google(user_id: int, address: str) -> None:
    async with db.SessionLocal() as session:
        session.add(
            UserIdentity(
                user_id=user_id,
                provider="google",
                issuer="https://accounts.google.com",
                subject="600000000000000000001",
                email_at_link=address,
                email_kind="gmail",
            )
        )
        await session.commit()


# ---------------- Forgot ----------------


async def test_forgot_queues_one_reset_job(client: ApiClient, turnstile: FakeTurnstile) -> None:
    response = await forgot(client, "Someone@Example.com")

    assert (response.status_code, response.json()) == (202, {"status": "accepted"})
    [job] = await jobs()
    assert (job.kind, job.mail_class, job.to_address, job.canonical, job.user_id) == (
        "password_reset",
        "reset",
        "Someone@example.com",
        "someone@example.com",
        None,
    )


@pytest.mark.parametrize("missing", ["mailer", "turnstile"])
async def test_forgot_needs_the_mailer_and_turnstile(
    missing: str, client: ApiClient, request: pytest.FixtureRequest
) -> None:
    request.getfixturevalue("fake_turnstile" if missing == "mailer" else "mailer")

    response = await forgot(client)

    assert response.status_code == 503
    assert response.json()["code"] == "mail_unavailable"


async def test_forgot_checks_turnstile_then_the_address(client: ApiClient, turnstile: FakeTurnstile) -> None:
    failed = await forgot(client, "not an address", token="fail:reset:x")
    invalid = await forgot(client, "not an address")

    assert (failed.status_code, failed.json()["code"]) == (400, "challenge_failed")
    assert (invalid.status_code, invalid.json()) == (422, {"detail": "Enter a valid email address."})


async def test_forgot_has_its_network_limit_and_a_silent_per_address_one(
    client: ApiClient, turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(throttle.RULES, "forgot", Limit(True, (Window(3600, 3),)))
    for address in (ADDRESS, "ALICE+again@example.com", "bob@example.com"):
        assert (await forgot(client, address)).status_code == 202

    limited = await forgot(client, "carol@example.com")

    assert (limited.status_code, limited.json()["code"]) == (429, "too_many_attempts")
    assert [job.canonical for job in await jobs()] == [ADDRESS, "bob@example.com"]  # the variant was silent


async def test_forgot_with_the_reset_share_used_up_answers_mail_unavailable(
    client: ApiClient, turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 4)  # reset: 1 a day
    assert (await forgot(client)).status_code == 202

    assert (await forgot(client, "bob@example.com")).json()["code"] == "mail_unavailable"


async def test_a_username_isnt_accepted(client: ApiClient, turnstile: FakeTurnstile) -> None:
    assert (await forgot(client, "alice")).status_code == 422


# ---------------- The link ----------------


async def test_the_check_names_the_account_and_its_google_sign_in(create_user: CreateUser, client: ApiClient) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    token = await reset_token(alice.user_id)

    plain = await client.post("/api/auth/password/check", json={"token": token})
    await link_google(alice.user_id, "a.someone@gmail.com")
    linked = await client.post("/api/auth/password/check", json={"token": token})

    assert plain.status_code == 200
    body = plain.json()
    assert (body["email"], body["username"], body["has_password"], body["google"]) == (ADDRESS, "alice", True, None)
    assert linked.json()["google"] == {"email": "a***@gmail.com", "suggest_disconnect": True}


async def test_no_disconnect_is_suggested_for_the_accounts_own_google_address(
    create_user: CreateUser, client: ApiClient
) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    await link_google(alice.user_id, "Alice@example.com")

    response = await client.post("/api/auth/password/check", json={"token": await reset_token(alice.user_id)})

    assert response.json()["google"] == {"email": "A***@example.com", "suggest_disconnect": False}


async def test_a_link_for_an_address_the_account_no_longer_has_is_invalid(
    create_user: CreateUser, client: ApiClient
) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    token = await reset_token(alice.user_id)
    await give_email(alice.user_id, "alice@new.example")

    for path in ("/api/auth/password/check", "/api/auth/password/reset"):
        response = await client.post(
            path, json={"token": token, **({"new_password": NEW_PASSWORD} if "reset" in path else {})}
        )
        assert (response.status_code, response.json()) == (400, INVALID), path


async def test_an_inactive_accounts_link_is_invalid(create_user: CreateUser, client: ApiClient) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    token = await reset_token(alice.user_id)
    await set_user(alice.user_id, is_active=False)

    assert (await client.post("/api/auth/password/check", json={"token": token})).json() == INVALID


# ---------------- The reset ----------------


async def test_a_reset_sets_the_password_ends_every_session_and_signs_nobody_in(
    create_user: CreateUser, client: ApiClient, new_client: NewClient, mailer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    other = await reset_token(alice.user_id)
    token = await reset_token(alice.user_id)
    requested = await alice.post("/api/me/email", json={"email": "alice@new.example", "password": USER_PASSWORD})
    assert requested.status_code == 202
    token = await reset_token(alice.user_id)  # the change request revoked the earlier links
    monkeypatch.setattr(settings, "login_max_attempts", 3)
    for _ in range(2):
        await new_client().post("/api/auth/login", json={"identifier": "alice", "password": "wrong-password"})

    response = await reset(client, token)

    assert response.status_code == 204
    assert session_cookie_name() not in set_cookies(response)
    assert (await alice.get("/api/auth/me")).status_code == 401
    user = await user_row(alice.user_id)
    assert verify_password(NEW_PASSWORD, user.password_hash) and user.token_version == 1
    assert await live_tokens(alice.user_id, "reset") == [] and other != token
    assert await pending_email(alice.user_id) is None
    assert await get_redis().get(f"auth:login:id:acct:{alice.user_id}") is None
    assert "password.reset" in await event_types(alice.user_id)
    notices = [job for job in await jobs() if job.kind == "password_changed"]
    assert [(job.to_address, job.params) for job in notices] == [(ADDRESS, {"google_disconnected": False})]
    for _ in range(3):  # the earlier failures were forgotten
        signed_in = await new_client().post("/api/auth/login", json={"identifier": "alice", "password": NEW_PASSWORD})
    assert signed_in.status_code == 200
    assert (await reset(client, token)).json() == INVALID  # once


async def test_a_reset_can_disconnect_google(create_user: CreateUser, client: ApiClient, mailer: None) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    await link_google(alice.user_id, "intruder@gmail.com")

    response = await reset(client, await reset_token(alice.user_id), disconnect_google=True)

    assert response.status_code == 204
    assert await identity_of(alice.user_id) is None
    assert await event_types(alice.user_id) == ["google.unlinked", "password.reset"]
    [notice] = await jobs("password_changed")
    assert notice.params == {"google_disconnected": True}


async def test_a_reset_keeps_google_unless_asked(create_user: CreateUser, client: ApiClient) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    await link_google(alice.user_id, "alice@gmail.com")

    assert (await reset(client, await reset_token(alice.user_id))).status_code == 204
    assert await identity_of(alice.user_id) is not None


async def test_a_google_only_account_sets_its_first_password_by_reset(
    create_user: CreateUser, client: ApiClient
) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    await set_user(alice.user_id, password_hash=None)
    token = await reset_token(alice.user_id)

    check = await client.post("/api/auth/password/check", json={"token": token})
    response = await reset(client, token)

    assert check.json()["has_password"] is False
    assert response.status_code == 204
    assert verify_password(NEW_PASSWORD, (await user_row(alice.user_id)).password_hash)


@pytest.mark.parametrize(
    ("password", "reason"), [("short", "too_short"), ("password123", "common"), ("Alice!1", "too_short")]
)
async def test_a_reset_applies_the_password_policy(
    password: str, reason: str, create_user: CreateUser, client: ApiClient
) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    token = await reset_token(alice.user_id)

    response = await reset(client, token, password)

    assert response.status_code == 422
    assert response.json()["code"] == "password_rejected"
    assert (await client.post("/api/auth/password/check", json={"token": token})).status_code == 200  # not spent


async def test_a_reset_with_the_email_as_password_is_refused(create_user: CreateUser, client: ApiClient) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    await give_email(alice.user_id, "wonderland1865@example.com")

    response = await reset(client, await reset_token(alice.user_id), "Wonderland-1865")

    assert (response.json()["code"], response.json()["reason"]) == ("password_rejected", "context")


async def test_the_reset_mail_link_works_end_to_end(
    create_user: CreateUser, client: ApiClient, turnstile: FakeTurnstile, fake_mail: FakeMail
) -> None:
    alice = await alice_with_email(create_user)
    assert alice.user_id is not None
    assert (await forgot(client)).status_code == 202

    await drain_mail()

    assert fake_mail.last_link(ADDRESS).startswith("http://localhost:8080/reset-password#t=")
    assert (await reset(client, fake_mail.last_token(ADDRESS))).status_code == 204
