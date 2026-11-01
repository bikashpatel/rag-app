"""Forgot and reset password (docs/briefs/signup-auth.md 4.7): "forgot" takes an email and answers the same for
every address (the worker decides what to mail); a reset sets the password, ends every session, never signs in, and
can disconnect a Google sign-in someone else may have added."""

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models import AuthToken, PendingEmail, User
from app.schemas_auth import EmailWithChallenge, PasswordReset, PasswordTokenInfo, ResetGoogle
from app.security import hash_password_async
from app.services import account
from app.services.auth import (
    audit,
    email_change,
    emails,
    identities,
    login,
    passwords,
    stepup,
    throttle,
    tokens,
    turnstile,
)
from app.services.mail import outbox


def available() -> bool:
    """Password reset needs the mailer and Turnstile."""
    return settings.mail_configured and settings.turnstile_configured


async def forgot(db: AsyncSession, request: Request, body: EmailWithChallenge) -> None:
    """POST /auth/password/forgot: within the silent per-address limit, one `password_reset` job for the address."""
    if not available():
        raise login.mail_unavailable()
    await throttle.check(request, "forgot")
    await turnstile.verify(body.turnstile_token, "reset", throttle.client_ip(request))
    address = login.parse_email(body.email)
    if not await outbox.available(db, "reset"):
        raise login.mail_unavailable()
    if not await throttle.address_allowed("reset", address.delivery_key):
        return
    await outbox.enqueue(db, "password_reset", to=address.address, canonical=address.canonical)
    await db.commit()
    await outbox.wake()


async def _token_and_user(db: AsyncSession, token: str, *, lock: bool) -> tuple[AuthToken, User]:
    """The live `reset` token and its active, non-admin user, the token mailed to the user's current email; else 400
    link_invalid."""
    row = await tokens.find(db, ["reset"], token, lock=lock)
    user = None
    if row is not None and row.user_id is not None:
        user = await db.get(User, row.user_id, with_for_update=lock or None, populate_existing=lock)
    if row is None or user is None or not user.is_active or user.is_admin or user.email_canonical is None:
        raise login.link_invalid()
    if not tokens.address_matches(row, user.email_canonical):
        raise login.link_invalid()
    return row, user


async def check(db: AsyncSession, request: Request, token: str) -> PasswordTokenInfo:
    await throttle.check(request, "token")
    row, user = await _token_and_user(db, token, lock=False)
    identity = account.google_identity(user)
    google = None
    if identity is not None:
        google = ResetGoogle(
            email=emails.mask(identity.email_at_link),
            suggest_disconnect=identity.email_at_link.lower() != user.email_canonical,
        )
    return PasswordTokenInfo(
        email=user.email or "",
        username=user.username,
        has_password=user.password_hash is not None,
        expires_at=row.expires_at,
        google=google,
    )


async def reset(db: AsyncSession, request: Request, body: PasswordReset) -> None:
    """POST /auth/password/reset: the policy is checked and the password hashed before the token and the user are
    locked (and checked again)."""
    await throttle.check(request, "token")
    _, user = await _token_and_user(db, body.token, lock=False)
    await db.commit()  # no transaction open while the breach check and the hash run
    await passwords.check(body.new_password, context=login.password_context(user))
    password_hash = await hash_password_async(body.new_password)
    row, user = await _token_and_user(db, body.token, lock=True)
    user.password_hash = password_hash
    user.token_version += 1
    await tokens.spend(db, row)
    await tokens.revoke(db, ["reset"], user_id=user.id)
    await db.execute(delete(PendingEmail).where(PendingEmail.user_id == user.id))
    identity = account.google_identity(user) if body.disconnect_google else None
    if identity is not None:
        await identities.unlink(db, user, identity, reason="reset", request=request)
    await audit.record(db, "password.reset", actor="self", user_id=user.id, request=request)
    queued = await login.notice(db, "password_changed", user, {"google_disconnected": identity is not None})
    await db.commit()
    await throttle.forget_login_failures(throttle.account_key(user.id))
    if queued:
        await outbox.wake()


async def set_first_password(
    db: AsyncSession, request: Request, user: User, sid: int | None, new_password: str
) -> None:
    """POST /me/password/setup: a first password for an account without one, after a Google step-up in this session
    (spent here). No session ends."""
    if user.is_admin:
        raise login.env_admin()
    if user.password_hash is not None:
        raise AppError(409, "You already have a password. Use Change password.", "has_password")
    if await stepup.until(user.id, sid) is None:
        raise email_change.reauth_required()
    await passwords.check(new_password, context=login.password_context(user))
    password_hash = await hash_password_async(new_password)
    if not await stepup.spend(user.id, sid):
        raise email_change.reauth_required()
    await db.execute(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if user.password_hash is not None:
        raise AppError(409, "You already have a password. Use Change password.", "has_password")
    user.password_hash = password_hash
    await audit.record(db, "password.set", actor="self", user_id=user.id, request=request)
    queued = await login.notice(db, "password_added", user)
    await db.commit()
    await throttle.forget_login_failures(throttle.account_key(user.id))
    if queued:
        await outbox.wake()
