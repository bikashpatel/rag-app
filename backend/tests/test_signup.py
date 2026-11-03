"""Email sign-up (docs/briefs/signup-auth.md 4.1 to 4.3): the request's order, its 202 and cookie, the pending rows
(three per address), finishing in the same browser or with the chosen password elsewhere (which lifts the address's
suppression), the links' rules, the session it starts, resend, a stranger's sign-up for the same address (SEC-07), and a
database error that logs neither the address nor the password's hash."""

import logging
import sqlite3
import traceback
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from conftest import USER_PASSWORD, ApiClient, drain_mail, open_signup, signup
from fake_mail import FakeMail
from fake_turnstile import FakeTurnstile, turnstile_token
from signup_support import (
    give_email,
    jobs,
    pending_signups,
    set_cookies,
    signup_token,
    user_row,
)
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import SessionLocal
from app.deps import session_cookie_name
from app.models import AuthToken, LoginEvent, MailSuppression, PendingSignup, User, utcnow
from app.services.auth import emails, throttle
from app.services.auth.throttle import Limit, Window
from app.services.mail import outbox

NewClient = Callable[[], ApiClient]
CreateUser = Callable[..., Awaitable[ApiClient]]

ADDRESS = "new.person@example.com"
ACCEPTED = {"status": "accepted"}


@pytest.fixture
async def turnstile(mailer: None, fake_turnstile: FakeTurnstile) -> FakeTurnstile:
    """Email sign-up open and effective: the mailer and Turnstile configured, the admin's switches on."""
    await open_signup()
    return fake_turnstile


@pytest.fixture
def no_address_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lifts the silent per-address limit (1 a minute), so one address can sign up several times in a test."""
    monkeypatch.setattr(throttle, "ADDRESS_WINDOWS", (Window(60, 100),))


async def complete(client: ApiClient, token: str, password: str | None = None) -> httpx.Response:
    body = {"token": token} if password is None else {"token": token, "password": password}
    return await client.post("/api/auth/signup/complete", json=body)


# ---------------- The request ----------------


async def test_a_sign_up_answers_202_sets_the_browser_cookie_and_queues_one_job(
    client: ApiClient, turnstile: FakeTurnstile
) -> None:
    response = await signup(client, ADDRESS, next="/plans/checkout/standard")

    assert (response.status_code, response.json()) == (202, ACCEPTED)
    cookie = set_cookies(response)["rag_signup"]
    assert len(cookie.value) == 43
    assert (cookie["path"], cookie["httponly"], cookie["samesite"].lower()) == ("/api/auth/signup", True, "strict")
    assert cookie["max-age"] == str(7 * 86_400)
    [row] = await pending_signups()
    assert (row.address, row.canonical, row.next_path, row.password_failures) == (
        ADDRESS,
        ADDRESS,
        "/plans/checkout/standard",
        0,
    )
    assert row.password_hash != USER_PASSWORD and row.browser_sha256 and row.terms_version == "2026-11-01"
    [job] = await jobs()
    assert (job.kind, job.mail_class, job.to_address, job.pending_signup_id) == ("signup", "public", ADDRESS, row.id)


async def test_a_second_sign_up_from_the_same_browser_keeps_its_cookie(
    client: ApiClient, turnstile: FakeTurnstile, no_address_limit: None
) -> None:
    first = set_cookies(await signup(client, ADDRESS))["rag_signup"].value
    second = set_cookies(await signup(client, ADDRESS))["rag_signup"].value

    assert first == second
    assert len(await pending_signups()) == 2


async def test_sign_up_closed_answers_403_without_asking_turnstile(
    client: ApiClient, mailer: None, fake_turnstile: FakeTurnstile
) -> None:
    response = await signup(client, ADDRESS)

    assert response.status_code == 403
    assert response.json() == {"detail": "New sign-ups are closed right now.", "code": "signup_closed"}
    assert fake_turnstile.requests == [] and await pending_signups() == []


@pytest.mark.parametrize("missing", ["mailer", "turnstile", "email switch"])
async def test_email_sign_up_needs_the_mailer_turnstile_and_its_switch(
    missing: str, client: ApiClient, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if missing != "mailer":
        request.getfixturevalue("mailer")
    if missing != "turnstile":
        request.getfixturevalue("fake_turnstile")
    await open_signup(email=missing != "email switch")

    response = await signup(client, ADDRESS)

    assert (response.status_code, response.json()["code"]) == (403, "signup_closed")


async def test_the_network_limit_comes_before_turnstile(
    client: ApiClient, turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(throttle.RULES, "signup", Limit(True, (Window(3600, 2),)))
    for number in range(2):
        assert (await signup(client, f"person{number}@example.com")).status_code == 202
    asked = len(turnstile.requests)

    response = await signup(client, ADDRESS)

    assert response.status_code == 429
    assert response.json()["code"] == "too_many_attempts" and int(response.headers["retry-after"]) > 0
    assert len(turnstile.requests) == asked


async def test_turnstile_comes_before_the_address_is_parsed(client: ApiClient, turnstile: FakeTurnstile) -> None:
    response = await signup(client, "not an address", turnstile_token="fail:signup:x")

    assert (response.status_code, response.json()["code"]) == (400, "challenge_failed")


async def test_a_turnstile_outage_refuses_sign_up(client: ApiClient, turnstile: FakeTurnstile) -> None:
    turnstile.outage = "timeout"

    response = await signup(client, ADDRESS)

    assert (response.status_code, response.json()["code"]) == (503, "challenge_unavailable")
    assert await pending_signups() == []


@pytest.mark.parametrize(
    ("address", "fields", "detail"),
    [
        ("two@@example.com", {}, "Enter a valid email address."),
        ("a" * 321, {}, None),
        (ADDRESS, {"accept_terms": False}, None),
        (ADDRESS, {"password": ""}, None),
        (ADDRESS, {"extra": 1}, None),
        (ADDRESS, {"name": "  "}, None),
        (ADDRESS, {"name": "<script>"}, None),
    ],
    ids=["invalid address", "too long", "terms", "no password", "unknown field", "blank name", "markup in the name"],
)
async def test_invalid_sign_ups_get_422(
    address: str, fields: dict[str, object], detail: str | None, client: ApiClient, turnstile: FakeTurnstile
) -> None:
    response = await signup(client, address, **fields)

    assert response.status_code == 422
    if detail is not None:
        assert response.json() == {"detail": detail}
    assert await pending_signups() == []


async def test_the_terms_must_be_accepted(client: ApiClient, turnstile: FakeTurnstile) -> None:
    response = await signup(client, ADDRESS, accept_terms=False)

    assert "Accept the terms to create an account." in response.text


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("short", "too_short"),
        ("password123", "common"),
        ("New-Person-11", "context"),
        ("NewPerson11@Example.com", "context"),
    ],
)
async def test_the_password_policy_applies_with_the_address_and_its_local_part_as_context(
    password: str, reason: str, client: ApiClient, turnstile: FakeTurnstile
) -> None:
    response = await signup(client, "newperson11@example.com", password)

    assert response.status_code == 422
    assert (response.json()["code"], response.json()["reason"]) == ("password_rejected", reason)


async def test_a_full_public_mail_day_answers_mail_unavailable(
    client: ApiClient, turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch, no_address_limit: None
) -> None:
    monkeypatch.setattr(settings, "mail_daily_limit", 5)  # public: 2 a day
    for number in range(2):
        assert (await signup(client, f"person{number}@example.com")).status_code == 202

    response = await signup(client, ADDRESS)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "We can't send emails right now. Try again later, or continue with Google.",
        "code": "mail_unavailable",
    }


async def test_the_per_address_limit_is_silent(client: ApiClient, turnstile: FakeTurnstile) -> None:
    first = await signup(client, ADDRESS)
    again = await signup(client, "New.Person+tag@Example.com")

    assert first.status_code == again.status_code == 202 and first.json() == again.json()
    assert len(await pending_signups()) == 1 and len(await jobs()) == 1


async def test_at_most_three_live_pending_rows_per_address(
    new_client: NewClient, turnstile: FakeTurnstile, no_address_limit: None
) -> None:
    browsers = [new_client() for _ in range(4)]
    for browser in browsers[:3]:
        assert (await signup(browser, ADDRESS)).status_code == 202
    oldest = (await pending_signups())[0]
    first_link = await signup_token(ADDRESS, oldest.id)

    assert (await signup(browsers[3], ADDRESS.upper())).status_code == 202

    rows = await pending_signups()
    assert len(rows) == 3 and oldest.id not in {row.id for row in rows}
    refused = await complete(browsers[0], first_link)
    assert (refused.status_code, refused.json()["code"]) == (400, "link_invalid")


# ---------------- Finishing ----------------


async def test_the_same_browser_finishes_with_one_click_and_is_signed_in(
    client: ApiClient, turnstile: FakeTurnstile
) -> None:
    await signup(client, ADDRESS, next="/plans/checkout/standard", name="  Meera\tKrishnan ")
    assert [row.display_name for row in await pending_signups()] == ["Meera Krishnan"]
    token = await signup_token(ADDRESS)

    check = await client.post("/api/auth/signup/check", json={"token": token})
    response = await complete(client, token)

    assert check.status_code == 200
    assert {key: check.json()[key] for key in ("email", "needs_password", "next")} == {
        "email": ADDRESS,
        "needs_password": False,
        "next": "/plans/checkout/standard",
    }
    assert response.status_code == 200, response.text
    me = response.json()
    assert (me["email"], me["is_admin"], me["plan"]["state"], me["display_name"]) == (
        ADDRESS,
        False,
        "none",
        "Meera Krishnan",
    )
    assert me["username"].startswith("user-") and len(me["username"]) == 13
    cookies = set_cookies(response)
    assert session_cookie_name() in cookies and cookies["rag_signup"]["max-age"] == "0"
    assert (await client.get("/api/auth/me")).json()["id"] == me["id"]
    user = await user_row(me["id"])
    assert (user.signup_source, user.email_canonical, user.terms_version) == ("email", ADDRESS, "2026-11-01")
    assert user.display_name == "Meera Krishnan"
    assert user.email_verified_at is not None and user.terms_accepted_at is not None
    assert await pending_signups() == []
    async with SessionLocal() as session:
        [method] = await session.scalars(select(LoginEvent.method).where(LoginEvent.user_id == me["id"]))
    assert method == "password"
    assert [job.kind for job in await jobs()] == ["signup"]  # no welcome mail
    signed_in = await client.post("/api/auth/login", json={"identifier": ADDRESS, "password": USER_PASSWORD})
    assert signed_in.status_code == 200


async def test_finishing_lifts_a_suppression_of_the_address(client: ApiClient, turnstile: FakeTurnstile) -> None:
    await signup(client, ADDRESS)
    async with SessionLocal() as session:  # a complaint long ago, from a deleted account
        await outbox.suppress(session, emails.digest(ADDRESS), reason="complaint", source="webhook")
        await session.commit()

    assert (await complete(client, await signup_token(ADDRESS))).status_code == 200

    async with SessionLocal() as session:
        assert await session.get(MailSuppression, emails.digest(ADDRESS)) is None


async def test_a_database_error_logs_neither_the_address_nor_the_password_hash(
    client: ApiClient, turnstile: FakeTurnstile, caplog: pytest.LogCaptureFixture
) -> None:
    def failing(cursor: Any, statement: str, parameters: Any, context: Any) -> None:
        if statement.startswith("INSERT INTO pending_signups"):
            raise sqlite3.OperationalError("disk I/O error")

    engine = SessionLocal.kw["bind"].sync_engine
    event.listen(engine, "do_execute", failing)
    try:
        with caplog.at_level(logging.INFO), pytest.raises(OperationalError) as raised:
            await signup(client, "Victim.Person@example.com", name="Vimala Pillai")
    finally:
        event.remove(engine, "do_execute", failing)

    logged = "".join(traceback.format_exception_only(raised.value)) + caplog.text  # the error log's last lines
    assert "INSERT INTO pending_signups" in logged
    assert "victim.person" not in logged.lower() and "$argon2" not in logged and "vimala" not in logged.lower()


async def test_another_device_finishes_with_the_chosen_password(
    client: ApiClient, new_client: NewClient, turnstile: FakeTurnstile
) -> None:
    await signup(client, ADDRESS)
    token = await signup_token(ADDRESS)
    phone = new_client()

    check = await phone.post("/api/auth/signup/check", json={"token": token})
    missing = await complete(phone, token)
    wrong = await complete(phone, token, "not-the-password")
    right = await complete(phone, token, USER_PASSWORD)

    assert check.json()["needs_password"] is True
    detail = "That isn't the password you chose when you signed up."
    assert missing.json() == wrong.json() == {"detail": detail, "code": "wrong_password"}
    assert right.status_code == 200 and right.json()["display_name"] == "Maya Rao"  # the name given at sign-up
    assert (await phone.get("/api/auth/me")).json()["email"] == ADDRESS


async def test_five_wrong_passwords_delete_the_pending_sign_up(
    client: ApiClient, new_client: NewClient, turnstile: FakeTurnstile
) -> None:
    await signup(client, ADDRESS)
    token = await signup_token(ADDRESS)
    phone = new_client()
    assert (await complete(phone, token)).json()["code"] == "wrong_password"  # a missing one isn't counted
    for _ in range(4):
        assert (await complete(phone, token, "not-the-password")).json()["code"] == "wrong_password"

    fifth = await complete(phone, token, "not-the-password")

    assert fifth.status_code == 400
    assert fifth.json() == {
        "detail": "Too many wrong passwords. Sign up again to choose a new one.",
        "code": "signup_reset",
    }
    assert await pending_signups() == []
    assert (await complete(client, token)).json()["code"] == "link_invalid"


async def test_a_used_an_expired_and_an_unknown_link_are_invalid(
    client: ApiClient, new_client: NewClient, turnstile: FakeTurnstile, no_address_limit: None
) -> None:
    await signup(client, ADDRESS)
    used = await signup_token(ADDRESS)
    assert (await complete(client, used)).status_code == 200
    other = new_client()
    await signup(other, "second@example.com")
    expired = await signup_token("second@example.com")
    async with SessionLocal() as session:
        await session.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()

    for token in (used, expired, "x" * 43, "short"):
        for path in ("/api/auth/signup/check", "/api/auth/signup/complete"):
            response = await other.post(path, json={"token": token})
            assert response.status_code == 400, (token, path)
            assert response.json() == {"detail": "This link has expired or was already used.", "code": "link_invalid"}


async def test_a_pending_sign_up_past_its_seven_days_is_gone(client: ApiClient, turnstile: FakeTurnstile) -> None:
    await signup(client, ADDRESS)
    token = await signup_token(ADDRESS)
    async with SessionLocal() as session:
        await session.execute(update(PendingSignup).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()

    assert (await complete(client, token)).json()["code"] == "link_invalid"


async def test_an_address_taken_meanwhile_answers_email_taken(
    client: ApiClient, create_user: CreateUser, turnstile: FakeTurnstile
) -> None:
    await signup(client, ADDRESS)
    token = await signup_token(ADDRESS)
    bob = await create_user("bob")
    assert bob.user_id is not None
    await give_email(bob.user_id, ADDRESS)

    response = await complete(client, token)

    assert response.status_code == 409
    assert response.json() == {"detail": "This address already has an account. Sign in instead.", "code": "email_taken"}
    assert await pending_signups() == []


async def test_sign_up_closed_meanwhile_refuses_the_completion(client: ApiClient, turnstile: FakeTurnstile) -> None:
    await signup(client, ADDRESS)
    token = await signup_token(ADDRESS)
    await open_signup(email=False)

    response = await complete(client, token)

    assert (response.status_code, response.json()["code"]) == (403, "signup_closed")
    async with SessionLocal() as session:
        assert await session.scalar(select(User).where(User.email_canonical == ADDRESS)) is None


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("/plans", "/plans"),
        ("//evil.example", None),
        ("/api/auth/me", None),
        ("https://evil.example", None),
        ("/a\\b", None),
    ],
)
async def test_next_is_kept_only_as_a_path_on_this_site(
    given: str, kept: str | None, client: ApiClient, turnstile: FakeTurnstile
) -> None:
    await signup(client, ADDRESS, next=given)

    check = await client.post("/api/auth/signup/check", json={"token": await signup_token(ADDRESS)})

    assert check.json()["next"] == kept


# ---------------- Resend ----------------


async def resend(client: ApiClient, email: str = ADDRESS, token: str | None = None) -> httpx.Response:
    body = {"email": email, "turnstile_token": token or turnstile_token("resend")}
    return await client.post("/api/auth/signup/resend", json=body)


async def test_resend_queues_a_job_for_this_browsers_pending_row(
    client: ApiClient, new_client: NewClient, turnstile: FakeTurnstile, no_address_limit: None
) -> None:
    await signup(client, ADDRESS)
    other = new_client()
    await signup(other, ADDRESS)
    mine, theirs = await pending_signups()

    response = await resend(client)
    elsewhere = await resend(new_client())

    assert (response.status_code, response.json()) == (202, ACCEPTED)
    assert elsewhere.status_code == 202
    resent = (await jobs())[2:]
    assert [job.pending_signup_id for job in resent] == [mine.id, None]
    assert theirs.id != mine.id


async def test_resend_shares_the_silent_per_address_limit_with_sign_up(
    client: ApiClient, turnstile: FakeTurnstile
) -> None:
    await signup(client, ADDRESS)

    response = await resend(client)

    assert response.status_code == 202
    assert len(await jobs()) == 1


async def test_resend_has_its_own_network_bucket(
    client: ApiClient, turnstile: FakeTurnstile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(throttle.RULES, "resend", Limit(True, (Window(3600, 1),)))
    assert (await resend(client)).status_code == 202

    assert (await resend(client, "other@example.com")).status_code == 429
    assert (await signup(client, "other@example.com")).status_code == 202


async def test_resend_checks_turnstile_with_its_own_action(client: ApiClient, turnstile: FakeTurnstile) -> None:
    response = await resend(client, token="fail:resend:x")

    assert (response.status_code, response.json()["code"]) == (400, "challenge_failed")


# ---------------- A stranger's sign-up (SEC-07) ----------------


async def test_a_strangers_sign_up_keeps_the_first_link_and_the_reset_quota(
    client: ApiClient, new_client: NewClient, turnstile: FakeTurnstile, no_address_limit: None
) -> None:
    await signup(client, ADDRESS)
    first = await signup_token(ADDRESS)
    stranger = new_client()
    await signup(stranger, ADDRESS, "a-strangers-password")
    forgot = await client.post(
        "/api/auth/password/forgot", json={"email": ADDRESS, "turnstile_token": turnstile_token("reset")}
    )

    response = await complete(client, first)

    assert response.status_code == 200
    assert forgot.status_code == 202 and [job.kind for job in await jobs("password_reset")] == ["password_reset"]
    assert await pending_signups() == []  # the stranger's row went with the account


# ---------------- Through the worker ----------------


async def test_the_verification_mail_finishes_the_sign_up(
    client: ApiClient, turnstile: FakeTurnstile, fake_mail: FakeMail
) -> None:
    await signup(client, ADDRESS)

    assert await drain_mail() == 1
    link = fake_mail.last_link(ADDRESS)
    assert link.startswith("http://localhost:8080/verify-email#t=")

    response = await complete(client, fake_mail.last_token(ADDRESS))
    assert response.status_code == 200
    assert len(fake_mail.sent) == 1
