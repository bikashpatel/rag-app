"""Settings that are derived rather than read as-is (app.config), the ones that stop the start, the cookie names, and
the admin's sign-up settings with their effective availability (app.services.auth.signup_settings)."""

import logging
from typing import Any

import pytest
from fastapi import FastAPI, Request, Response
from pydantic import ValidationError
from sqlalchemy import make_url, select

from app import db
from app.config import RAZORPAY_API_BASE, REMOVED_SETTINGS, Settings, settings, warn_removed_settings
from app.deps import session_cookie_name
from app.models import AppState, AuthEvent
from app.schemas_auth import SignupAvailability, SignupSettings
from app.services.auth import audit, cookies, signup_settings


@pytest.mark.parametrize(
    "password", ["correct horse battery staple", "a+b", "x y+z", "p@ss:w/d%#?", "x%2By", "naïve pass"]
)
def test_postgres_url_keeps_every_character_of_the_credentials(password: str) -> None:
    configured = Settings(  # type: ignore[call-arg]
        database_url=None, postgres_user="rag user", postgres_password=password, postgres_db="rag db"
    )

    url = make_url(configured.sqlalchemy_url)

    assert (url.username, url.password, url.database) == ("rag user", password, "rag db")
    assert (url.drivername, url.host, url.port) == ("postgresql+asyncpg", "postgres", 5432)


def test_database_url_wins_over_the_postgres_settings() -> None:
    configured = Settings(database_url="sqlite+aiosqlite:///x.db", postgres_password="a b")  # type: ignore[call-arg]

    assert configured.sqlalchemy_url == "sqlite+aiosqlite:///x.db"


# ---------------- Payments (docs/briefs/plans-billing.md 6.9 and 6.10) ----------------


@pytest.mark.parametrize(
    ("key_id", "secret", "mode"),
    [
        ("rzp_test_AbCd", "secret", "test"),
        ("rzp_live_AbCd", "secret", "live"),
        ("rzp_other_AbCd", "secret", None),
        ("rzp_test_AbCd", "", None),
        ("", "secret", None),
    ],
)
def test_the_key_ids_prefix_sets_the_payments_mode(key_id: str, secret: str, mode: str | None) -> None:
    configured = Settings(razorpay_key_id=key_id, razorpay_key_secret=secret)  # type: ignore[call-arg]

    assert configured.razorpay_mode == mode


def test_live_keys_refuse_to_start_with_another_api_base() -> None:
    with pytest.raises(ValidationError, match="RAZORPAY_API_BASE must be https://api.razorpay.com"):
        Settings(razorpay_key_id="rzp_live_AbCd", razorpay_api_base="https://fake-razorpay:8443")  # type: ignore[call-arg]

    live = Settings(razorpay_key_id="rzp_live_AbCd", razorpay_api_base=RAZORPAY_API_BASE)  # type: ignore[call-arg]
    fake = Settings(razorpay_key_id="rzp_test_AbCd", razorpay_api_base="https://fake-razorpay:8443")  # type: ignore[call-arg]
    assert live.razorpay_api_base == RAZORPAY_API_BASE and fake.razorpay_api_base == "https://fake-razorpay:8443"


@pytest.mark.parametrize(
    "values",
    [
        {"razorpay_api_base": "https://api.razorpay.com/v1"},
        {"razorpay_api_base": "ftp://api.razorpay.com"},
        {"billing_checkout_minutes": 15},
        {"billing_checkout_minutes": 121},
        {"billing_late_capture_days": 11},
        {"billing_reminder_days": 0},
        {"billing_reconcile_seconds": 1},
        {"billing_usd_inr": 0},
        {"checkout_unpaid_per_hour": 0},
        {"checkout_unpaid_per_hour": 1001},
    ],
)
def test_payment_settings_out_of_range_stop_the_start(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


# ---------------- Sign-up and email (docs/briefs/signup-auth.md, section 10) ----------------

PUBLIC = "https://asknuvexusdoc.com,http://localhost:8080"


def test_the_defaults_leave_sign_up_mail_turnstile_and_google_unconfigured() -> None:
    configured = Settings()  # type: ignore[call-arg]

    assert (configured.mail_configured, configured.turnstile_configured, configured.google_signin_configured) == (
        False,
        False,
        False,
    )
    assert (configured.password_min_length, configured.password_breach_check, configured.terms_version) == (
        8,
        True,
        "2026-11-01",
    )
    assert (configured.mail_daily_limit, configured.mail_smtp_port, configured.mail_smtp_security) == (80, 465, "ssl")
    assert (configured.login_challenge_after, configured.login_challenge_after_per_ip) == (3, 10)
    assert (configured.login_site_challenge_after, configured.admin_signin_from) == (50, "any")
    assert configured.mail_idempotency_header == "Resend-Idempotency-Key"
    assert configured.checkout_unpaid_per_hour == 30


@pytest.mark.parametrize(
    ("values", "configured"),
    [
        ({"mail_smtp_host": "smtp.resend.com", "mail_from": "AskNuvexusDoc <no-reply@asknuvexusdoc.com>"}, True),
        ({"mail_smtp_host": "smtp.resend.com"}, False),
        ({"mail_from": "no-reply@asknuvexusdoc.com"}, False),
    ],
)
def test_the_mailer_needs_a_host_and_a_sender(values: dict[str, Any], configured: bool) -> None:
    assert Settings(**values).mail_configured is configured  # type: ignore[call-arg]


def test_pairs_of_keys_configure_turnstile_and_google_sign_in() -> None:
    both = Settings(  # type: ignore[call-arg]
        turnstile_site_key="0x4AAA-site",
        turnstile_secret_key="0x4AAA-secret",
        google_signin_client_id="id.apps.googleusercontent.com",
        google_signin_client_secret="secret",
    )

    assert (both.turnstile_configured, both.google_signin_configured, both.turnstile_test_secret) == (True, True, False)
    assert Settings(turnstile_site_key="1x00000000000000000000AA", turnstile_secret_key="2x00").turnstile_test_secret  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"turnstile_site_key": "0x4AAA-site"}, "Set both TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY, or neither"),
        ({"turnstile_secret_key": "0x4AAA-secret"}, "Set both TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY"),
        ({"google_signin_client_id": "id"}, "Set both GOOGLE_SIGNIN_CLIENT_ID and GOOGLE_SIGNIN_CLIENT_SECRET"),
        ({"google_signin_client_secret": "secret"}, "Set both GOOGLE_SIGNIN_CLIENT_ID"),
        ({"login_challenge_after": 11}, "LOGIN_CHALLENGE_AFTER must be at most LOGIN_MAX_ATTEMPTS"),
        ({"login_challenge_after_per_ip": 101}, "LOGIN_CHALLENGE_AFTER_PER_IP must be at most LOGIN_MAX_ATTEMPTS_PER"),
        ({"mail_webhook_secret": "secret"}, "MAIL_WEBHOOK_SECRET must be whsec_ followed by base64"),
        ({"mail_webhook_secret": "whsec_not base64!"}, "MAIL_WEBHOOK_SECRET must be whsec_"),
        ({"mail_webhook_secret": "whsec_"}, "MAIL_WEBHOOK_SECRET must be whsec_"),
    ],
)
def test_inconsistent_sign_up_settings_stop_the_start(values: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "values",
    [
        {"password_min_length": 7},
        {"password_min_length": 65},
        {"mail_daily_limit": 0},
        {"mail_daily_limit": 100_001},
        {"mail_smtp_port": 0},
        {"mail_smtp_port": 65_536},
        {"mail_smtp_security": "tls"},
        {"mail_from": "not an address"},
        {"mail_from": "AskNuvexusDoc <no-reply@asknuvexusdoc.com"},
        {"mail_from": "Evil\r\nBcc: x@y.z <no-reply@asknuvexusdoc.com>"},
        {"mail_reply_to": "AskNuvexusDoc <support@asknuvexusdoc.com>"},
        {"mail_idempotency_header": "Bad Header"},
        {"mail_idempotency_header": "X-Header:"},
        {"login_site_challenge_after": 9},
        {"login_site_challenge_after": 10_001},
        {"login_challenge_after": 0},
        {"admin_signin_from": "remote"},
        {"terms_version": ""},
        {"terms_version": "2026 11 01"},
        {"terms_version": "x" * 33},
        {"turnstile_siteverify_url": "challenges.cloudflare.com/turnstile"},
        {"password_breach_check_url": "ftp://api.pwnedpasswords.com/range/"},
        {"google_signin_jwks_url": "not a url"},
    ],
)
def test_sign_up_and_mail_settings_out_of_range_stop_the_start(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "values",
    [
        {"mail_from": "no-reply@asknuvexusdoc.com", "mail_reply_to": "support@asknuvexusdoc.com"},
        {"mail_from": "AskNuvexusDoc <no-reply@asknuvexusdoc.com>"},
        {"mail_idempotency_header": ""},
        {"mail_webhook_secret": "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"},
        {"password_min_length": 15, "terms_version": "2026-11-20", "admin_signin_from": "local"},
        {"login_challenge_after": 10, "login_challenge_after_per_ip": 100},
    ],
)
def test_valid_sign_up_and_mail_settings_start(values: dict[str, Any]) -> None:
    Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("values", "refused"),
    [
        (
            {"turnstile_site_key": "1x00000000000000000000AA", "turnstile_secret_key": "1x" + "0" * 31 + "AA"},
            "TURNSTILE_SECRET_KEY must be a real secret, not one of Cloudflare's test secrets",
        ),
        ({"turnstile_site_key": "x", "turnstile_secret_key": "2x0000000000000000000000000000000AA"}, "test secrets"),
        ({"turnstile_site_key": "x", "turnstile_secret_key": "3x0000000000000000000000000000000AA"}, "test secrets"),
        (
            {"turnstile_siteverify_url": "http://fake-turnstile:8000/siteverify"},
            "TURNSTILE_SITEVERIFY_URL must be https://challenges.cloudflare.com/turnstile/v0/siteverify",
        ),
        (
            {"google_signin_authorize_url": "http://fake-google/auth"},
            "GOOGLE_SIGNIN_AUTHORIZE_URL must be https://accounts.google.com/o/oauth2/v2/auth",
        ),
        (
            {"google_signin_token_url": "http://fake-google/token"},
            "GOOGLE_SIGNIN_TOKEN_URL must be https://oauth2.googleapis.com/token",
        ),
        (
            {"google_signin_jwks_url": "http://fake-google/certs"},
            "GOOGLE_SIGNIN_JWKS_URL must be https://www.googleapis.com/oauth2/v3/certs",
        ),
        (
            {"password_breach_check_url": "http://fake-hibp/range/"},
            "PASSWORD_BREACH_CHECK_URL must be https://api.pwnedpasswords.com/range/",
        ),
        ({"mail_smtp_security": "none"}, "MAIL_SMTP_SECURITY must be ssl or starttls"),
    ],
)
def test_test_keys_and_test_endpoints_never_run_on_the_public_site(values: dict[str, Any], refused: str) -> None:
    with pytest.raises(ValidationError, match="PUBLIC_BASE_URLS holds a public https origin, so ") as error:
        Settings(public_base_urls=PUBLIC, **values)  # type: ignore[call-arg]
    assert refused in str(error.value)

    Settings(public_base_urls="http://localhost:8080,http://127.0.0.1:18090", **values)  # type: ignore[call-arg]
    Settings(public_base_urls="https://localhost:8443,https://127.0.0.1", **values)  # type: ignore[call-arg]


def test_every_refusal_on_the_public_site_is_named_at_once() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(  # type: ignore[call-arg]
            public_base_urls="https://asknuvexusdoc.com",
            mail_smtp_security="none",
            password_breach_check_url="http://fake-hibp/range/",
        )

    assert "PASSWORD_BREACH_CHECK_URL must be" in str(error.value)
    assert "MAIL_SMTP_SECURITY must be ssl or starttls" in str(error.value)


def test_the_production_setup_starts_on_the_public_site() -> None:
    configured = Settings(  # type: ignore[call-arg]
        public_base_urls=PUBLIC,
        cookie_secure=True,
        mail_smtp_host="smtp.resend.com",
        mail_smtp_username="resend",
        mail_smtp_password="re_secret",
        mail_from="AskNuvexusDoc <no-reply@asknuvexusdoc.com>",
        mail_reply_to="support@asknuvexusdoc.com",
        mail_webhook_secret="whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
        turnstile_site_key="0x4AAAAAAA-site",
        turnstile_secret_key="0x4AAAAAAA-secret",
        google_signin_client_id="id.apps.googleusercontent.com",
        google_signin_client_secret="secret",
    )

    assert configured.mail_configured and configured.turnstile_configured and configured.google_signin_configured


def test_billing_email_from_is_ignored_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in REMOVED_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BILLING_EMAIL_FROM", "billing@example.com")

    with caplog.at_level(logging.WARNING, logger="app.config"):
        found = warn_removed_settings()

    assert found == ["BILLING_EMAIL_FROM"]
    assert [record.getMessage() for record in caplog.records] == [
        "BILLING_EMAIL_FROM is ignored: plan reminders and receipts now use MAIL_FROM and the MAIL_SMTP_ settings"
    ]
    assert not hasattr(Settings(), "billing_email_from")  # type: ignore[call-arg]


# ---------------- Cookie names (S41) ----------------


@pytest.mark.parametrize(
    ("secure", "session", "signup"),
    [(False, "rag_session", "rag_signup"), (True, "__Host-rag_session", "__Secure-rag_signup")],
)
def test_behind_https_the_cookies_carry_their_prefixes(
    monkeypatch: pytest.MonkeyPatch, secure: bool, session: str, signup: str
) -> None:
    monkeypatch.setattr(settings, "cookie_secure", secure)

    assert (session_cookie_name(), cookies.cookie_name(cookies.SIGNUP)) == (session, signup)
    prefix = "__Secure-" if secure else ""
    assert cookies.cookie_name(cookies.GOOGLE_SIGNIN) == f"{prefix}rag_gsignin"
    assert cookies.cookie_name(cookies.GOOGLE_TICKET) == f"{prefix}rag_gticket"


def test_cookie_name_is_the_session_cookies_base_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cookie_name", "app_session")
    monkeypatch.setattr(settings, "cookie_secure", True)

    assert session_cookie_name() == "__Host-app_session"


@pytest.mark.parametrize("secure", [False, True])
def test_auth_cookies_are_http_only_path_scoped_and_secure_behind_https(
    monkeypatch: pytest.MonkeyPatch, secure: bool
) -> None:
    monkeypatch.setattr(settings, "cookie_secure", secure)
    response = Response()

    cookies.set_auth_cookie(
        response, cookies.GOOGLE_SIGNIN, "binding", path="/api/auth/google/callback", max_age=600, samesite="lax"
    )
    cookies.clear_auth_cookie(response, cookies.SIGNUP, path="/api/auth/signup")

    set_cookie, cleared = response.headers.getlist("set-cookie")
    prefix = "__Secure-" if secure else ""
    assert set_cookie.startswith(f"{prefix}rag_gsignin=binding;")
    parts = {part.strip().split("=", 1)[0].lower(): part for part in set_cookie.split(";")}
    assert {"httponly", "max-age", "path", "samesite"} <= set(parts) and ("secure" in parts) is secure
    assert "Max-Age=600" in set_cookie and "Path=/api/auth/google/callback" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert cleared.startswith(f'{prefix}rag_signup="";') and "Max-Age=0" in cleared
    assert "Path=/api/auth/signup" in cleared and ("Secure" in cleared) is secure


# ---------------- The admin's sign-up settings (app.services.auth.signup_settings, S27) ----------------


async def availability() -> tuple[SignupSettings, SignupAvailability]:
    async with db.SessionLocal() as session:
        return await signup_settings.load(session), await signup_settings.availability(session)


@pytest.fixture
def everything_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (
        ("mail_smtp_host", "smtp.example.test"),
        ("mail_from", "no-reply@example.test"),
        ("turnstile_site_key", "0x4AAA-site"),
        ("turnstile_secret_key", "0x4AAA-secret"),
        ("google_signin_client_id", "id"),
        ("google_signin_client_secret", "secret"),
    ):
        monkeypatch.setattr(settings, name, value)


async def test_sign_up_ships_closed_with_both_methods_on(app: FastAPI, everything_configured: None) -> None:
    assert await availability() == (
        SignupSettings(open=False, email=True, google=True),
        SignupAvailability(open=False, email=False, google=False),
    )


async def test_saving_the_settings_records_who_changed_what(app: FastAPI, everything_configured: None) -> None:
    async with db.SessionLocal() as session:
        await signup_settings.save(session, SignupSettings(open=True, google=False), actor="admin:admin")
        await session.commit()

    assert await availability() == (
        SignupSettings(open=True, email=True, google=False),
        SignupAvailability(open=True, email=True, google=False),
    )
    async with db.SessionLocal() as session:
        [event] = list(await session.scalars(select(AuthEvent)))
    assert (event.type, event.actor, event.user_id, event.ip) == ("signup.settings", "admin:admin", None, None)
    assert event.detail == {
        "before": {"open": False, "email": True, "google": True},
        "after": {"open": True, "email": True, "google": False},
    }


@pytest.mark.parametrize(
    ("unset", "effective"),
    [
        ((), SignupAvailability(open=True, email=True, google=True)),
        (("mail_smtp_host",), SignupAvailability(open=True, email=False, google=True)),
        (("turnstile_site_key", "turnstile_secret_key"), SignupAvailability(open=True, email=False, google=True)),
        (("google_signin_client_secret",), SignupAvailability(open=True, email=True, google=False)),
        (("mail_from", "google_signin_client_id"), SignupAvailability(open=False, email=False, google=False)),
    ],
)
async def test_an_open_method_needs_what_it_sends_with(
    app: FastAPI,
    everything_configured: None,
    monkeypatch: pytest.MonkeyPatch,
    unset: tuple[str, ...],
    effective: SignupAvailability,
) -> None:
    async with db.SessionLocal() as session:
        await signup_settings.save(session, SignupSettings(open=True), actor="admin:admin")
        await session.commit()
    for name in unset:
        monkeypatch.setattr(settings, name, "")

    assert (await availability())[1] == effective


@pytest.mark.parametrize(
    "stored",
    ['{"open": true, "email": true, "google": true, "domains": ["example.com"]}', "not json", '{"open": "yes"}', "[]"],
)
async def test_stored_settings_are_read_leniently(app: FastAPI, stored: str) -> None:
    async with db.SessionLocal() as session:
        session.add(AppState(key=signup_settings.KEY, value=stored))
        await session.commit()

    loaded, _ = await availability()

    assert loaded == SignupSettings(open=stored.startswith('{"open": true'))


async def test_an_audit_event_keeps_the_requests_approximate_address(app: FastAPI) -> None:
    request = Request({"type": "http", "headers": [], "client": ("203.0.113.77", 4000)})
    async with db.SessionLocal() as session:
        detail = {"email": "j***@example.com"}
        await audit.record(session, "email.pending", actor="self", request=request, detail=detail)
        await session.commit()
        [event] = list(await session.scalars(select(AuthEvent)))

    assert (event.type, event.actor, event.ip, event.detail) == (
        "email.pending",
        "self",
        "203.0.113.x",
        {"email": "j***@example.com"},
    )
