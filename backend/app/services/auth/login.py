"""Signing in (docs/briefs/signup-auth.md 4.4) and what every flow shares: one field for an email or a username, one
generic 401, one failure counter per account with the adaptive Turnstile challenge, the session each sign-in path
starts (a password, Google, a completed sign-up), re-entering a password for an account change, and the security
notices mailed to the account's verified email."""

from collections.abc import Mapping

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_current_user, set_session_cookie
from app.errors import AppError
from app.models import LoginEvent, User, utcnow
from app.schemas_account import LoginMethod, Me
from app.schemas_auth import LoginRequest
from app.security import hash_password_async, needs_rehash, verify_password_async
from app.services import account
from app.services.auth import emails, throttle, turnstile
from app.services.billing import entitlements
from app.services.mail import outbox
from app.services.usage import usage_summary

INVALID_LOGIN = "Incorrect email, username or password."
ENV_ADMIN = "The admin account is managed in .env"


# ---------------- Errors shared by the flows (SPEC.md section 6, the sign-up error table) ----------------


def link_invalid() -> AppError:
    return AppError(400, "This link has expired or was already used.", "link_invalid")


def signup_closed() -> AppError:
    return AppError(403, "New sign-ups are closed right now.", "signup_closed")


def mail_unavailable() -> AppError:
    return AppError(
        503, "We can't send emails right now. Try again later, or continue with Google.", "mail_unavailable"
    )


def wrong_password(detail: str = "Your password is incorrect.") -> AppError:
    return AppError(400, detail, "wrong_password")


def email_taken(detail: str = "This address already has an account. Sign in instead.") -> AppError:
    return AppError(409, detail, "email_taken")


def env_admin() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ENV_ADMIN)


def parse_email(value: str) -> emails.Email:
    """emails.parse, refused as 422 "Enter a valid email address." (no code)."""
    try:
        return emails.parse(value)
    except emails.EmailError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


def password_context(user: User) -> tuple[str, ...]:
    """The words a new password of this account may not be: its username, and its email and the email's local part."""
    return (user.username, user.email, user.email.partition("@")[0]) if user.email else (user.username,)


# ---------------- Signing in (4.4) ----------------


async def find(db: AsyncSession, identifier: str) -> tuple[User | None, str]:
    """The user an identifier names and its failure-counter key: an identifier with "@" is an email, found by its
    canonical form among verified addresses (an invalid one finds nobody), anything else a username. A found user's
    key is their account's, so the email and the username share one counter."""
    if "@" in identifier:
        try:
            canonical = emails.parse(identifier).canonical
        except emails.EmailError:
            return None, f"email:{emails.digest(identifier.lower())}"
        user = await db.scalar(select(User).where(User.email_canonical == canonical))
        key = f"email:{emails.digest(canonical)}"
    else:
        username = identifier.lower()
        user = await db.scalar(select(User).where(User.username == username))
        key = f"user:{username}"
    return user, throttle.account_key(user.id) if user is not None else key


def _admin_refused_here(user: User, request: Request) -> bool:
    return user.is_admin and settings.admin_signin_from == "local" and not throttle.is_local(request)


async def password_sign_in(db: AsyncSession, request: Request, body: LoginRequest) -> User:
    """The user an identifier and password sign in, under the hard limits (429) and the challenge (401
    challenge_required; a Turnstile outage carries on under the hard limits). Unknown, wrong, password-less, inactive
    and refused-admin attempts all get the same 401 after the same password-hash time."""
    user, key = await find(db, body.identifier)
    wait = await throttle.login_wait(request, key)
    if wait is not None:
        raise throttle.too_many_attempts(wait)
    if await throttle.login_challenge_due(request, key):
        if not body.turnstile_token:
            raise AppError(401, "Confirm you're not a robot to continue.", "challenge_required")
        try:
            await turnstile.verify(body.turnstile_token, "login", throttle.client_ip(request))
        except AppError as exc:
            if exc.code != "challenge_unavailable":
                raise
    await throttle.login_failed(request, key)
    valid = await verify_password_async(body.password, user.password_hash if user is not None else None)
    if user is None or not valid or not user.is_active or _admin_refused_here(user, request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_LOGIN)
    await throttle.login_cleared(request, key)
    if needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(body.password)
    return user


async def confirm_password(request: Request, user: User, password: str | None) -> None:
    """The account's current password, re-entered for a change: counted under the account's sign-in counter (429
    past the limit), 400 wrong_password when it doesn't match (a missing one isn't counted)."""
    if not password:
        raise wrong_password()
    key = throttle.account_key(user.id)
    wait = await throttle.login_wait(request, key)
    if wait is not None:
        raise throttle.too_many_attempts(wait)
    await throttle.login_failed(request, key)
    if not await verify_password_async(password, user.password_hash):
        raise wrong_password()
    await throttle.login_cleared(request, key)


# ---------------- Sessions ----------------


async def me(db: AsyncSession, user: User) -> Me:
    ent = await entitlements.load(db, user)
    return Me(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        limits=entitlements.user_limits(ent),
        usage=await usage_summary(db, user, ent),
        preferences=await account.read_preferences(db, user, ent),
        plan=await entitlements.plan_access(db, user, ent),
        email=user.email,
    )


async def start_session(
    db: AsyncSession, request: Request, response: Response, user: User, method: LoginMethod
) -> LoginEvent:
    """A new session for `user`: last_login_at, a login_events row (its id is the token's sid), the commit and the
    session cookie."""
    user.last_login_at = utcnow()
    event = await account.record_login(
        db, user, request.client.host if request.client else None, request.headers.get("user-agent"), method=method
    )
    await db.commit()
    set_session_cookie(response, user, event.id)
    return event


async def session_user(request: Request, db: AsyncSession) -> User | None:
    """The user of the request's session, or None (no session, or one that ended)."""
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


# ---------------- Security notices (6.4) ----------------


async def notice(
    db: AsyncSession, kind: outbox.MailKind, user: User, params: Mapping[str, str | int | bool | None] | None = None
) -> bool:
    """Queues a security notice to the account's verified email (in the caller's transaction) while its class has room
    today and the account is within its notices (3 an hour, 10 a day); else nothing, silently, as without an email or a
    mailer. True when queued: wake the worker after the commit."""
    if user.email is None or not outbox.configured():
        return False
    if not await outbox.available(db, await outbox.class_of(db, kind, user.id)):
        return False
    if not await throttle.notice_allowed(user.id):
        return False
    await outbox.enqueue(db, kind, to=user.email, canonical=user.email_canonical, user_id=user.id, params=params)
    return True
