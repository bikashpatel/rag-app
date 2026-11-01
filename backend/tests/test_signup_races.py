"""Races only Postgres can show (docs/briefs/signup-auth.md 15.1): two completions of one sign-up, a completion
against a Google finish for the same address, two accounts confirming one address, and an undo against a new
change. Run with TEST_POSTGRES_URL set (tests/README.md); skipped otherwise."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import USER_PASSWORD, ApiClient, OutsideHosts, open_signup, signup
from fake_google import FakeGoogleSignin
from fake_turnstile import FakeTurnstile
from google_oidc import GoogleOidc
from signup_support import (
    confirm_token,
    give_email,
    google_callback,
    google_fake,
    signup_token,
    undo_token,
    user_row,
)
from sqlalchemy import func, select

from app import db
from app.models import User
from app.services.auth import signup as signup_service

pytestmark = pytest.mark.postgres

CreateUser = Callable[..., Awaitable[ApiClient]]
NewClient = Callable[[], ApiClient]


@pytest.fixture
def slow_address_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every request passes the "is this address free" check before any of them inserts: only the unique index can
    stop the second."""
    held = signup_service.email_held

    async def slow(session: Any, canonical: str) -> bool:
        found = await held(session, canonical)
        await asyncio.sleep(0.3)
        return found

    monkeypatch.setattr(signup_service, "email_held", slow)


async def holders(address: str) -> int:
    async with db.SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(User).where(User.email_canonical == address))
    return int(count or 0)


async def test_two_completions_of_one_sign_up_make_one_account(
    client: ApiClient, mailer: None, fake_turnstile: FakeTurnstile
) -> None:
    await open_signup()
    await signup(client, "racer@example.com")
    token = await signup_token("racer@example.com")

    responses = await asyncio.gather(
        *(client.post("/api/auth/signup/complete", json={"token": token}) for _ in range(2))
    )

    assert sorted(response.status_code for response in responses) == [200, 400]
    assert await holders("racer@example.com") == 1


async def test_a_completion_racing_a_google_finish_for_the_same_address(
    new_client: NewClient,
    mailer: None,
    fake_turnstile: FakeTurnstile,
    google_oidc: GoogleOidc,
    outside_hosts: OutsideHosts,
    slow_address_checks: None,
) -> None:
    google: FakeGoogleSignin = google_fake(google_oidc, outside_hosts)
    await open_signup()
    address = google.account.email
    by_email, by_google = new_client(), new_client()
    await signup(by_email, address)
    token = await signup_token(address)
    await google_callback(by_google, google)

    responses = await asyncio.gather(
        by_email.post("/api/auth/signup/complete", json={"token": token}),
        by_google.post("/api/auth/google/finish", json={"accept_terms": True}),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert {response.json().get("code") for response in responses if response.status_code == 409} == {"email_taken"}
    assert await holders(address) == 1


async def test_two_accounts_confirming_one_address_give_it_to_one(
    create_user: CreateUser, mailer: None, slow_address_checks: None
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    tokens = []
    for client in (alice, bob):
        assert client.user_id is not None
        await client.post("/api/me/email", json={"email": "shared@example.com", "password": USER_PASSWORD})
        tokens.append(await confirm_token(client.user_id))

    responses = await asyncio.gather(
        alice.post("/api/auth/email/verify", json={"token": tokens[0]}),
        bob.post("/api/auth/email/verify", json={"token": tokens[1]}),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert await holders("shared@example.com") == 1


async def test_an_undo_racing_a_new_change_leaves_the_old_address(create_user: CreateUser, mailer: None) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await give_email(alice.user_id, "alice@old.example")
    await alice.post("/api/me/email", json={"email": "alice@new.example", "password": USER_PASSWORD})
    assert (await alice.post("/api/auth/email/verify", json={"token": await confirm_token(alice.user_id)})).is_success
    undo = await undo_token(alice.user_id, "alice@old.example")
    await alice.post("/api/me/email", json={"email": "alice@third.example", "password": USER_PASSWORD})
    third = await confirm_token(alice.user_id)

    await asyncio.gather(
        alice.post("/api/auth/email/verify", json={"token": third}),
        alice.post("/api/auth/email/undo", json={"token": undo}),
    )

    user = await user_row(alice.user_id)  # whichever went first, the undo has the last word
    assert (user.email, user.password_hash) == ("alice@old.example", None)
