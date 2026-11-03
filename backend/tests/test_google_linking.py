"""Linking Google to an account (docs/briefs/signup-auth.md 4.5, 4.6): automatic only for an @gmail.com address
(SEC-03), by hand with the password, the results of a link, disconnecting, the Google step-up, and the account
pre-hijacking scenarios of research 4.1 with SEC-01's chain A, each blocked."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import GOOGLE_FINISH, USER_PASSWORD, ApiClient, OutsideHosts, open_signup, signup
from fake_google import FakeGoogleSignin
from fake_turnstile import FakeTurnstile
from google_oidc import GoogleOidc
from signup_support import (
    confirm_token,
    event_types,
    give_email,
    google_callback,
    google_fake,
    google_start,
    identity_of,
    jobs,
    landed,
    pending_email,
    pending_signups,
    query,
    set_user,
    user_row,
)
from sqlalchemy import select

from app import db
from app.models import User, UserIdentity
from app.redis_client import get_redis
from app.services.auth import stepup

CreateUser = Callable[..., Awaitable[ApiClient]]
NewClient = Callable[[], ApiClient]

GMAIL = "it-person@gmail.com"
ACCOUNT = "/?settings=account&google="


@pytest.fixture
def google(google_oidc: GoogleOidc, outside_hosts: OutsideHosts) -> FakeGoogleSignin:
    return google_fake(google_oidc, outside_hosts)


async def link(client: ApiClient, google: FakeGoogleSignin, password: str = USER_PASSWORD, **start: Any) -> str:
    return landed(await google_callback(client, google, intent="link", password=password, **start))


async def google_only(client: ApiClient, google: FakeGoogleSignin) -> ApiClient:
    """`client` signed up with Google (a gmail address, no password)."""
    await open_signup(email=False)
    assert landed(await google_callback(client, google)) == "/signup/google"
    finished = await client.post("/api/auth/google/finish", json=GOOGLE_FINISH)
    assert finished.status_code == 200, finished.text
    client.user_id = finished.json()["id"]
    return client


# ---------------- Automatic linking (S10, SEC-03) ----------------


async def test_a_workspace_address_with_a_new_subject_never_links_an_existing_account(
    create_user: CreateUser, client: ApiClient, google: FakeGoogleSignin
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await give_email(alice.user_id, "alice@company.example")
    google.choose(email="alice@company.example", hd="company.example", sub="200000000000000000001")

    response = await google_callback(client, google)

    assert landed(response) == "/login?google=link_required"
    assert await identity_of(alice.user_id) is None
    assert (await client.get("/api/auth/me")).status_code == 401


# ---------------- By hand (4.6) ----------------


async def test_connecting_google_with_the_password_links_it(
    create_user: CreateUser, google: FakeGoogleSignin, mailer: None
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await give_email(alice.user_id, "alice@example.com")

    assert await link(alice, google) == ACCOUNT + "linked"

    identity = await identity_of(alice.user_id)
    assert identity is not None and (identity.subject, identity.email_at_link) == (google.account.sub, GMAIL)
    assert (await user_row(alice.user_id)).email == "alice@example.com"  # linking never replaces it
    assert "google.linked" in await event_types(alice.user_id)
    assert [(job.kind, job.to_address) for job in await jobs()] == [("google_linked", "alice@example.com")]
    assert (await alice.get("/api/auth/me")).status_code == 200  # the session stays


async def test_a_wrong_password_refuses_the_link_and_counts(
    create_user: CreateUser, google: FakeGoogleSignin, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "login_max_attempts", 2)
    alice = await create_user("alice")
    for _ in range(2):
        response = await google_start(alice, intent="link", password="not-the-password")
        assert response.json() == {"detail": "Your password is incorrect.", "code": "wrong_password"}

    response = await google_start(alice, intent="link", password=USER_PASSWORD)

    assert (response.status_code, response.json()["code"]) == (429, "too_many_attempts")


async def test_a_session_ended_meanwhile_expires_the_link(
    create_user: CreateUser, google: FakeGoogleSignin, new_client: NewClient
) -> None:
    alice = await create_user("alice")
    started = await google_start(alice, intent="link", password=USER_PASSWORD)
    back = google.consent(started.json()["authorize_url"])
    other = new_client()
    await other.login("alice", USER_PASSWORD)
    assert (await other.post("/api/auth/logout-others")).status_code == 204

    response = await alice.get(back.removeprefix("http://localhost:8080"))

    assert landed(response) == ACCOUNT + "expired"
    assert await identity_of(alice.user_id) is None  # type: ignore[arg-type]


async def test_in_use_and_already_linked(
    create_user: CreateUser, new_client: NewClient, google: FakeGoogleSignin
) -> None:
    alice = await create_user("alice")
    bob = await create_user("bob")
    assert await link(alice, google) == ACCOUNT + "linked"

    assert await link(bob, google) == ACCOUNT + "in_use"
    assert await link(alice, google) == ACCOUNT + "already_linked"
    google.choose(sub="300000000000000000001", email="other@gmail.com")
    assert await link(alice, google) == ACCOUNT + "already_linked"  # one Google identity per user


async def test_denied_and_errors_come_back_to_settings(create_user: CreateUser, google: FakeGoogleSignin) -> None:
    alice = await create_user("alice")
    google.deny_next = True
    assert await link(alice, google) == ACCOUNT + "denied"
    google.token_failures.append(500)
    assert await link(alice, google) == ACCOUNT + "error"


async def test_linking_gives_an_account_without_an_email_the_verified_gmail_address(
    create_user: CreateUser, google: FakeGoogleSignin, mailer: None
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    requested = await alice.post("/api/me/email", json={"email": GMAIL, "password": USER_PASSWORD})
    assert requested.status_code == 202

    assert await link(alice, google) == ACCOUNT + "linked"

    user = await user_row(alice.user_id)
    assert (user.email, user.email_canonical) == (GMAIL, GMAIL) and user.email_verified_at is not None
    assert await pending_email(alice.user_id) is None


async def test_linking_never_takes_an_address_another_account_holds(
    create_user: CreateUser, google: FakeGoogleSignin
) -> None:
    alice, bob = await create_user("alice"), await create_user("bob")
    assert alice.user_id is not None and bob.user_id is not None
    await give_email(bob.user_id, GMAIL)

    assert await link(alice, google) == ACCOUNT + "linked"
    assert (await user_row(alice.user_id)).email is None


async def test_an_account_without_a_password_cant_start_a_link(client: ApiClient, google: FakeGoogleSignin) -> None:
    user = await google_only(client, google)

    response = await google_start(user, intent="link", password="anything")

    assert (response.status_code, response.json()["code"]) == (400, "no_password")


# ---------------- Disconnecting ----------------


async def test_disconnecting_needs_the_password_and_leaves_a_way_in(
    create_user: CreateUser, client: ApiClient, google: FakeGoogleSignin, mailer: None
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    google.choose(email="alice@company.example")  # no hd: not taken as the account's email
    assert await link(alice, google) == ACCOUNT + "linked"

    no_email = await alice.request("DELETE", "/api/me/google", json={"password": USER_PASSWORD})
    await give_email(alice.user_id, "alice@example.com")
    wrong = await alice.request("DELETE", "/api/me/google", json={"password": "not-the-password"})
    done = await alice.request("DELETE", "/api/me/google", json={"password": USER_PASSWORD})
    again = await alice.request("DELETE", "/api/me/google", json={"password": USER_PASSWORD})

    assert no_email.status_code == 409
    assert no_email.json() == {
        "detail": "Set a password and confirm an email first, so you can still sign in.",
        "code": "last_sign_in_method",
    }
    assert (wrong.status_code, wrong.json()["code"]) == (400, "wrong_password")
    assert done.status_code == 204 and again.status_code == 404
    assert await identity_of(alice.user_id) is None
    assert [job.kind for job in await jobs()][-1] == "google_unlinked"
    assert (await jobs())[-1].params == {"google_email": "a***@company.example"}
    assert "google.unlinked" in await event_types(alice.user_id)
    assert (await alice.get("/api/auth/me")).status_code == 200


async def test_a_google_only_account_cant_disconnect_its_only_way_in(
    client: ApiClient, google: FakeGoogleSignin
) -> None:
    user = await google_only(client, google)

    response = await user.request("DELETE", "/api/me/google", json={"password": "anything"})

    assert (response.status_code, response.json()["code"]) == (409, "last_sign_in_method")


async def test_after_disconnecting_that_google_account_needs_the_usual_rules(
    create_user: CreateUser, new_client: NewClient, google: FakeGoogleSignin
) -> None:
    alice = await create_user("alice")
    assert alice.user_id is not None
    await give_email(alice.user_id, "alice@company.example")
    google.choose(email="alice@company.example", hd="company.example")
    assert await link(alice, google) == ACCOUNT + "linked"
    assert (await alice.request("DELETE", "/api/me/google", json={"password": USER_PASSWORD})).status_code == 204

    assert landed(await google_callback(new_client(), google)) == "/login?google=link_required"


# ---------------- The step-up (4.5, S13, S14) ----------------


async def test_a_step_up_grants_this_session_five_minutes(
    client: ApiClient, new_client: NewClient, google: FakeGoogleSignin
) -> None:
    user = await google_only(client, google)
    started = await google_start(user, intent="reauth", next="/?settings=account&setup=password")
    params = query(started.json()["authorize_url"])
    assert (params["login_hint"], params["max_age"]) == (google.account.sub, "0")

    back = google.consent(started.json()["authorize_url"])
    response = await user.get(back.removeprefix("http://localhost:8080"))

    assert landed(response) == "/?settings=account&setup=password&google=reauth_ok"
    account = (await user.get("/api/me/account")).json()
    assert account["stepup_until"] is not None and account["email_change_needs"] == "google"
    [key] = await get_redis().keys("auth:stepup:*")
    assert 0 < await get_redis().ttl(key) <= 300
    assert "google.reauth" in await event_types(user.user_id)
    other = new_client()
    assert landed(await google_callback(other, google)) == "/"  # the same person, another session
    assert (await other.get("/api/me/account")).json()["stepup_until"] is None


async def test_a_step_up_as_another_google_account_is_a_mismatch(client: ApiClient, google: FakeGoogleSignin) -> None:
    user = await google_only(client, google)
    started = await google_start(user, intent="reauth")
    google.choose(sub="400000000000000000001", email="someone-else@gmail.com")

    response = await user.get(google.consent(started.json()["authorize_url"]).removeprefix("http://localhost:8080"))

    assert landed(response) == "/?settings=account&google=reauth_mismatch"
    assert await get_redis().keys("auth:stepup:*") == []


async def test_a_stale_google_sign_in_doesnt_step_up(client: ApiClient, google: FakeGoogleSignin) -> None:
    user = await google_only(client, google)
    google.choose(auth_time=int(time.time()) - 600)

    response = await google_callback(user, google, intent="reauth")

    assert landed(response) == "/?settings=account&google=expired"
    assert await get_redis().keys("auth:stepup:*") == []


async def test_a_step_up_needs_a_linked_identity_and_a_session_with_a_sid(
    create_user: CreateUser, client: ApiClient, google: FakeGoogleSignin
) -> None:
    alice = await create_user("alice")
    no_identity = await google_start(alice, intent="reauth")
    user = await google_only(client, google)
    from app.security import create_access_token

    async with db.SessionLocal() as session:
        row = await session.get(User, user.user_id)
        assert row is not None
        old_token = create_access_token(row.id, row.token_version)  # from before sign-in history: no sid
    no_sid = await google_start(client, intent="reauth", **{})
    without = await client.post(
        "/api/auth/google/start",
        json={"intent": "reauth"},
        headers={"Origin": "http://localhost:8080", "Authorization": f"Bearer {old_token}", "Cookie": ""},
    )

    assert (no_identity.status_code, no_identity.json()) == (400, {"detail": "Connect Google first."})
    assert no_sid.status_code == 200
    assert (without.status_code, without.json()) == (400, {"detail": "Sign in again first."})


async def test_a_grant_is_spent_once(client: ApiClient, google: FakeGoogleSignin) -> None:
    user = await google_only(client, google)
    await google_callback(user, google, intent="reauth")
    assert user.user_id is not None
    account = (await user.get("/api/me/account")).json()
    sid = next(entry["id"] for entry in account["recent_logins"] if entry["current"])

    assert await stepup.spend(user.user_id, sid) is True
    assert await stepup.spend(user.user_id, sid) is False


# ---------------- Pre-hijacking (research 4.1) and SEC-01's chain A ----------------


async def test_classic_federated_merge_an_attackers_email_sign_up_never_becomes_the_victims(
    new_client: NewClient, google: FakeGoogleSignin, mailer: None, fake_turnstile: FakeTurnstile
) -> None:
    await open_signup()
    attacker = new_client()
    assert (await signup(attacker, GMAIL, "the-attackers-password")).status_code == 202
    victim = new_client()

    assert landed(await google_callback(victim, google)) == "/signup/google"
    finished = await victim.post("/api/auth/google/finish", json=GOOGLE_FINISH)

    assert finished.status_code == 200
    assert await pending_signups(GMAIL) == []  # the attacker's link is dead
    user = await user_row(finished.json()["id"])
    assert user.password_hash is None  # not the attacker's password
    signed_in = await new_client().post(
        "/api/auth/login", json={"identifier": GMAIL, "password": "the-attackers-password"}
    )
    assert signed_in.status_code == 401


async def test_trojan_identifier_and_unexpired_email_change_a_pending_address_identifies_nobody(
    create_user: CreateUser, new_client: NewClient, google: FakeGoogleSignin, mailer: None
) -> None:
    attacker = await create_user("attacker")
    assert attacker.user_id is not None
    requested = await attacker.post("/api/me/email", json={"email": GMAIL, "password": USER_PASSWORD})
    assert requested.status_code == 202
    token = await confirm_token(attacker.user_id)
    await open_signup(email=False)
    victim = new_client()

    assert landed(await google_callback(victim, google)) == "/signup/google"  # not the attacker's account
    victim_id = (await victim.post("/api/auth/google/finish", json=GOOGLE_FINISH)).json()["id"]
    confirmed = await attacker.post("/api/auth/email/verify", json={"token": token})

    assert (confirmed.status_code, confirmed.json()["code"]) == (409, "email_taken")
    assert (await user_row(attacker.user_id)).email is None
    assert (await user_row(victim_id)).email == GMAIL


async def test_a_non_verifying_claim_never_reaches_the_account_holding_the_address(
    create_user: CreateUser, new_client: NewClient, google: FakeGoogleSignin, mailer: None
) -> None:
    victim = await create_user("victim")
    assert victim.user_id is not None
    await give_email(victim.user_id, "victim@company.example")
    await open_signup(email=False)
    google.choose(email="victim@company.example", email_verified=True, sub="500000000000000000001")  # no hd
    attacker = new_client()

    assert landed(await google_callback(attacker, google)) == "/signup/google"
    created = (await attacker.post("/api/auth/google/finish", json=GOOGLE_FINISH)).json()

    assert created["id"] != victim.user_id and created["email"] is None
    assert await identity_of(victim.user_id) is None
    token = await confirm_token(created["id"])
    confirmed = await attacker.post("/api/auth/email/verify", json={"token": token})
    assert (confirmed.status_code, confirmed.json()["code"]) == (409, "email_taken")


async def test_sec01_chain_a_the_victim_clicking_the_link_confirms_nothing(
    create_user: CreateUser, client: ApiClient, google: FakeGoogleSignin, mailer: None
) -> None:
    attacker = await create_user("attacker")
    victim = await create_user("victim")
    assert attacker.user_id is not None
    requested = await attacker.post("/api/me/email", json={"email": "victim@example.com", "password": USER_PASSWORD})
    assert requested.status_code == 202
    token = await confirm_token(attacker.user_id)

    signed_out = await client.post("/api/auth/email/verify", json={"token": token})
    as_victim = await victim.post("/api/auth/email/verify", json={"token": token})
    check = await victim.post("/api/auth/email/check", json={"token": token})

    assert (signed_out.status_code, signed_out.json()) == (401, {"detail": "Not authenticated"})
    assert (as_victim.status_code, as_victim.json()["code"]) == (403, "wrong_account")
    assert check.json() == {"email": "victim@example.com", "account": "attacker", "signed_in": False}
    assert (await user_row(attacker.user_id)).email is None
    async with db.SessionLocal() as session:
        assert await session.scalar(select(UserIdentity)) is None


async def test_an_inactive_accounts_step_up_or_link_ends_expired(
    create_user: CreateUser, google: FakeGoogleSignin
) -> None:
    alice = await create_user("alice")
    started = await google_start(alice, intent="link", password=USER_PASSWORD)
    await set_user(alice.user_id, is_active=False)  # type: ignore[arg-type]

    response = await alice.get(google.consent(started.json()["authorize_url"]).removeprefix("http://localhost:8080"))

    assert landed(response) == ACCOUNT + "expired"
