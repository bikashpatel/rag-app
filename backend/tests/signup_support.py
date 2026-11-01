"""Helpers of the sign-up and sign-in tests (test_signup*.py, test_login_identifier.py, test_google_*.py,
test_password_*.py, test_email_change.py, test_admin_signup.py): sign-up opened with the mailer and the fake
Turnstile, the links the worker would mint (tokens.mint, as app.services.mail.jobs does when it sends), the outbox and
the rows a flow wrote, and Google sign-in through the fake Google (fake_google.FakeGoogleSignin) with the conftest
`google_oidc` keys."""

from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from conftest import OutsideHosts
from fake_google import FakeGoogleSignin
from google_oidc import CLIENT_SECRET, GoogleOidc
from sqlalchemy import select

from app import db
from app.config import settings
from app.models import AuthEvent, AuthToken, EmailOutbox, PendingEmail, PendingSignup, User, UserIdentity, utcnow
from app.services.auth import emails, tokens

ORIGIN = "http://localhost:8080"  # PUBLIC_BASE_URLS in the tests


def google_fake(google_oidc: GoogleOidc, outside_hosts: OutsideHosts) -> FakeGoogleSignin:
    """Google sign-in configured (with conftest's `google_oidc`): the fake's token endpoint at
    GOOGLE_SIGNIN_TOKEN_URL, its ID tokens signed with the `google_oidc` keys served at GOOGLE_SIGNIN_JWKS_URL."""
    fake = FakeGoogleSignin(
        client_id=google_oidc.client_id, client_secret=CLIENT_SECRET, mint=lambda claims: google_oidc.id_token(**claims)
    )
    outside_hosts.route(settings.google_signin_token_url, fake.handle)
    return fake


def set_cookies(response: httpx.Response) -> SimpleCookie:
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    return cookies


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


# ---------------- Rows ----------------


async def jobs(kind: str | None = None) -> list[EmailOutbox]:
    """The outbox, oldest first (of one kind)."""
    async with db.SessionLocal() as session:
        statement = select(EmailOutbox).order_by(EmailOutbox.id)
        if kind is not None:
            statement = statement.where(EmailOutbox.kind == kind)
        return list(await session.scalars(statement))


async def pending_signups(canonical: str | None = None) -> list[PendingSignup]:
    async with db.SessionLocal() as session:
        statement = select(PendingSignup).order_by(PendingSignup.id)
        if canonical is not None:
            statement = statement.where(PendingSignup.canonical == canonical)
        return list(await session.scalars(statement))


async def pending_email(user_id: int) -> PendingEmail | None:
    async with db.SessionLocal() as session:
        row: PendingEmail | None = await session.scalar(select(PendingEmail).where(PendingEmail.user_id == user_id))
    return row


async def user_row(user_id: int | None = None, *, email: str | None = None) -> User:
    async with db.SessionLocal() as session:
        if user_id is not None:
            found = await session.get(User, user_id)
        else:
            found = await session.scalar(select(User).where(User.email_canonical == (email or "").lower()))
    assert found is not None, (user_id, email)
    return found


async def identity_of(user_id: int) -> UserIdentity | None:
    async with db.SessionLocal() as session:
        row: UserIdentity | None = await session.scalar(select(UserIdentity).where(UserIdentity.user_id == user_id))
    return row


async def event_types(user_id: int | None) -> list[str]:
    async with db.SessionLocal() as session:
        statement = select(AuthEvent.type).order_by(AuthEvent.id)
        statement = statement.where(
            AuthEvent.user_id == user_id if user_id is not None else AuthEvent.user_id.is_(None)
        )
        return list(await session.scalars(statement))


async def live_tokens(user_id: int, purpose: str) -> list[AuthToken]:
    async with db.SessionLocal() as session:
        return list(
            await session.scalars(
                select(AuthToken).where(
                    AuthToken.user_id == user_id,
                    AuthToken.purpose == purpose,
                    AuthToken.used_at.is_(None),
                    AuthToken.expires_at > utcnow(),
                )
            )
        )


async def give_email(user_id: int, address: str) -> None:
    """The account's verified email, as a confirmed change would set it."""
    parsed = emails.parse(address)
    async with db.SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None, user_id
        user.email, user.email_canonical, user.email_verified_at = parsed.address, parsed.canonical, utcnow()
        await session.commit()


async def set_user(user_id: int, **values: Any) -> None:
    async with db.SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None, user_id
        for name, value in values.items():
            setattr(user, name, value)
        await session.commit()


# ---------------- Links, as the worker mints them ----------------


async def signup_token(address: str, row_id: int | None = None) -> str:
    """A verification link's token for the pending sign-up (the newest live one for the address by default)."""
    canonical = address.lower()
    async with db.SessionLocal() as session:
        statement = select(PendingSignup).where(PendingSignup.canonical == canonical)
        if row_id is not None:
            statement = statement.where(PendingSignup.id == row_id)
        row = await session.scalar(statement.order_by(PendingSignup.id.desc()).limit(1))
        assert row is not None, address
        token = await tokens.mint(session, "signup", address_canonical=row.canonical, pending_signup_id=row.id)
        await session.commit()
    return token


async def reset_token(user_id: int) -> str:
    async with db.SessionLocal() as session:
        user = await session.get(User, user_id)
        assert user is not None and user.email_canonical is not None, user_id
        token = await tokens.mint(session, "reset", address_canonical=user.email_canonical, user_id=user.id)
        await session.commit()
    return token


async def confirm_token(user_id: int) -> str:
    """The confirmation link's token for the user's pending email."""
    async with db.SessionLocal() as session:
        row = await session.scalar(select(PendingEmail).where(PendingEmail.user_id == user_id))
        assert row is not None, user_id
        token = await tokens.mint(
            session, "email", address_canonical=row.canonical, user_id=user_id, pending_email_id=row.id
        )
        await session.commit()
    return token


async def undo_token(user_id: int, old_address: str) -> str:
    """The undo link of the email_changed notice to the old address."""
    async with db.SessionLocal() as session:
        token = await tokens.mint(session, "email_undo", address_canonical=old_address.lower(), user_id=user_id)
        await session.commit()
    return token


# ---------------- Google sign-in ----------------


async def google_start(client: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await client.post("/api/auth/google/start", json={"intent": "signin", **body}, headers={"Origin": ORIGIN})


async def google_callback(client: httpx.AsyncClient, fake: FakeGoogleSignin, **start: Any) -> httpx.Response:
    """Start (intent signin unless given), Google's consent for `fake.account`, and the callback's 303."""
    started = await google_start(client, **start)
    assert started.status_code == 200, started.text
    back = urlsplit(fake.consent(started.json()["authorize_url"]))
    return await client.get(f"{back.path}?{back.query}")


def landed(response: httpx.Response) -> str:
    """Where the callback's 303 sends the browser, without the origin."""
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(ORIGIN), location
    return location.removeprefix(ORIGIN)
