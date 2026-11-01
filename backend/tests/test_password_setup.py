"""Setting a first password (docs/briefs/signup-auth.md 4.9): with this session's Google step-up (spent), no session
ends and a notice goes to the verified email; without a grant 403, with a password already 409."""

from collections.abc import Awaitable, Callable

import pytest
from conftest import ApiClient
from signup_support import event_types, give_email, jobs, set_user, user_row

from app.security import verify_password
from app.services.auth import stepup

CreateUser = Callable[..., Awaitable[ApiClient]]

NEW_PASSWORD = "a-first-and-long-password"


@pytest.fixture
async def google_only(create_user: CreateUser, mailer: None) -> ApiClient:
    """A user without a password, with a verified email; the mailer configured."""
    client = await create_user("alice")
    assert client.user_id is not None
    await set_user(client.user_id, password_hash=None)
    await give_email(client.user_id, "alice@example.com")
    return client


async def grant(client: ApiClient) -> None:
    assert client.user_id is not None
    account = (await client.get("/api/me/account")).json()
    await stepup.grant(client.user_id, next(entry["id"] for entry in account["recent_logins"] if entry["current"]))


async def setup(client: ApiClient, password: str = NEW_PASSWORD):  # type: ignore[no-untyped-def]
    return await client.post("/api/me/password/setup", json={"new_password": password})


async def test_a_step_up_lets_the_account_set_its_first_password(google_only: ApiClient) -> None:
    assert google_only.user_id is not None
    await grant(google_only)

    response = await setup(google_only)

    assert response.status_code == 204
    user = await user_row(google_only.user_id)
    assert verify_password(NEW_PASSWORD, user.password_hash) and user.token_version == 0
    assert (await google_only.get("/api/auth/me")).status_code == 200  # no session ended
    assert "password.set" in await event_types(google_only.user_id)
    [notice] = await jobs()
    assert (notice.kind, notice.to_address) == ("password_added", "alice@example.com")
    assert (await setup(google_only, "another-long-password")).status_code == 409  # it has one now


async def test_without_a_grant_it_is_403_and_a_grant_is_spent_once(google_only: ApiClient) -> None:
    refused = await setup(google_only)
    await grant(google_only)
    await setup(google_only, "short")  # refused by the policy: the grant stays
    accepted = await setup(google_only)

    assert refused.status_code == 403
    assert refused.json() == {"detail": "Confirm it's you with Google first.", "code": "reauth_required"}
    assert accepted.status_code == 204


async def test_an_account_with_a_password_gets_409(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    await grant(alice)

    response = await setup(alice)

    assert response.status_code == 409
    assert response.json() == {"detail": "You already have a password. Use Change password.", "code": "has_password"}


async def test_the_password_policy_applies(google_only: ApiClient) -> None:
    await grant(google_only)

    response = await setup(google_only, "password123")

    assert (response.status_code, response.json()["reason"]) == (422, "common")


async def test_without_an_email_no_notice_is_queued(create_user: CreateUser, mailer: None) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await set_user(alice.user_id, password_hash=None)
    await grant(alice)

    assert (await setup(alice)).status_code == 204
    assert await jobs() == []


async def test_the_env_admin_cant_set_one(admin_client: ApiClient) -> None:
    response = await setup(admin_client)

    assert (response.status_code, response.json()) == (400, {"detail": "The admin account is managed in .env"})
