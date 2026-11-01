"""The sign-up contract's shapes (docs/briefs/signup-auth.md section 11; SPEC.md section 6): the request bodies of
app.schemas_auth, AppError's `reason`, and the additive fields of stage 0 (the two email switches, Me.email,
LoginEvent.method, UserRef.email) as the existing routes serve them."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import ApiClient
from pydantic import BaseModel, ValidationError

from app.db import SessionLocal
from app.errors import AppError
from app.models import UserPreference
from app.schemas_auth import (
    Accepted,
    AuthConfig,
    GooglePending,
    GoogleStart,
    LoginRequest,
    PasswordReset,
    SignupAvailability,
    SignupRequest,
    SignupSettings,
    TokenRequest,
)
from app.schemas_billing import UserRef

CreateUser = Callable[..., Awaitable[ApiClient]]

SIGNUP = {"email": "maya@example.com", "password": "a long passphrase", "accept_terms": True, "turnstile_token": "t"}


def refused(model: type[BaseModel], body: Any) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(body)
    return [error["type"] for error in caught.value.errors()]


def test_login_takes_an_identifier_or_the_username_alias_trimmed_and_without_nul() -> None:
    assert LoginRequest.model_validate({"identifier": " Maya@Example.com ", "password": "x"}).identifier == (
        "Maya@Example.com"
    )
    alias = LoginRequest.model_validate({"username": "\x00maya.chen\t", "password": "x", "turnstile_token": None})
    assert (alias.identifier, alias.turnstile_token) == ("maya.chen", None)
    assert LoginRequest.model_validate({"identifier": "a", "password": "p" * 1024}).password == "p" * 1024


@pytest.mark.parametrize(
    "body",
    [
        {"identifier": "maya", "username": "maya", "password": "x"},
        {"identifier": "maya", "password": "x", "remember": True},
        {"identifier": "  ", "password": "x"},
        {"identifier": "maya", "password": ""},
        {"identifier": "maya", "password": "p" * 1025},
        {"identifier": "m" * 321, "password": "x"},
        {"identifier": "maya", "password": "x", "turnstile_token": ""},
        {"password": "x"},
    ],
)
def test_login_refuses_malformed_bodies(body: dict[str, Any]) -> None:
    assert refused(LoginRequest, body)


def test_sign_up_needs_the_ticked_terms_and_bounds_every_field() -> None:
    assert SignupRequest.model_validate({**SIGNUP, "next": "/plans"}).next == "/plans"
    with pytest.raises(ValidationError, match="Accept the terms to create an account."):
        SignupRequest.model_validate({**SIGNUP, "accept_terms": False})
    for change in (
        {"accept_terms": "true"},
        {"email": ""},
        {"email": "a" * 321},
        {"turnstile_token": "t" * 2049},
        {"next": "/" * 513},
        {"plan": "starter"},
    ):
        assert refused(SignupRequest, {**SIGNUP, **change}), change
    assert refused(SignupRequest, {key: value for key, value in SIGNUP.items() if key != "turnstile_token"})


def test_tokens_and_resets_are_bounded_and_strict() -> None:
    assert TokenRequest.model_validate({"token": "x" * 43}).token == "x" * 43
    assert refused(TokenRequest, {"token": ""}) and refused(TokenRequest, {"token": "x" * 129})
    reset = PasswordReset.model_validate({"token": "t", "new_password": "p"})
    assert reset.disconnect_google is False
    assert refused(PasswordReset, {"token": "t", "new_password": "p", "disconnect_google": "yes"})


def test_google_start_reads_from_under_its_json_name() -> None:
    start = GoogleStart.model_validate({"intent": "signin", "from": "login", "next": "/plans"})
    assert (start.intent, start.from_, start.next, start.password) == ("signin", "login", "/plans", None)
    assert refused(GoogleStart, {"intent": "signin", "from_": "login"}) == ["extra_forbidden"]
    assert refused(GoogleStart, {"intent": "admin"}) and refused(GoogleStart, {"intent": "signin", "from": "chat"})


def test_google_pending_is_written_with_from() -> None:
    pending = GooglePending(email="maya@gmail.com", email_verified=True, from_="signup", expires_at="2026-11-01T00:00Z")
    assert pending.model_dump(mode="json") == {
        "email": "maya@gmail.com",
        "email_verified": True,
        "from": "signup",
        "expires_at": "2026-11-01T00:00:00Z",
    }


def test_sign_up_settings_default_to_closed_with_both_methods() -> None:
    assert SignupSettings().model_dump() == {"open": False, "email": True, "google": True}
    assert SignupSettings.model_validate({"open": True}).model_dump() == {"open": True, "email": True, "google": True}
    assert refused(SignupSettings, {"open": 1}) and refused(SignupSettings, {"open": True, "domains": []})
    assert Accepted().model_dump() == {"status": "accepted"}


def test_the_config_reports_the_policys_maximum() -> None:
    config = AuthConfig(
        signup=SignupAvailability(open=False, email=False, google=False),
        google_signin=False,
        password_reset=False,
        turnstile_site_key=None,
        password_min_length=8,
        terms_version="2026-11-01",
        contact_email=None,
    )
    assert config.password_max_length == 256


def test_an_app_error_carries_its_reason_only_when_set() -> None:
    error = AppError(422, "Use at least 8 characters.", "password_rejected", reason="too_short")
    assert error.body() == {"detail": "Use at least 8 characters.", "code": "password_rejected", "reason": "too_short"}
    assert "reason" not in AppError(403, "New sign-ups are closed right now.", "signup_closed").body()


def test_a_user_ref_without_an_email_reads_as_none() -> None:
    assert UserRef(id=None, username="gone.user").model_dump() == {"id": None, "username": "gone.user", "email": None}


async def test_the_email_switches_default_on_save_strictly_and_read_leniently(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    me = (await alice.get("/api/auth/me")).json()
    assert (me["email"], me["preferences"]["email_plan_reminders"], me["preferences"]["email_receipts"]) == (
        None,
        True,
        True,
    )

    response = await alice.patch("/api/me/preferences", json={"email_receipts": False})
    assert response.status_code == 200, response.text
    assert (response.json()["email_receipts"], response.json()["email_plan_reminders"]) == (False, True)
    for body in ({"email_receipts": None}, {"email_plan_reminders": "false"}, {"email_plan_reminders": 0}):
        assert (await alice.patch("/api/me/preferences", json=body)).status_code == 422, body
    assert (await alice.get("/api/me/preferences")).json()["email_receipts"] is False

    async with SessionLocal() as session:
        row = await session.get(UserPreference, alice.user_id)
        assert row is not None
        row.data = {**row.data, "email_receipts": "no", "email_plan_reminders": False}
        await session.commit()
    stored = (await alice.get("/api/me/preferences")).json()
    assert (stored["email_receipts"], stored["email_plan_reminders"]) == (True, False)


async def test_sign_ins_say_they_were_with_a_password(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    logins = (await alice.get("/api/me/account")).json()["recent_logins"]
    assert logins and {login["method"] for login in logins} == {"password"}
