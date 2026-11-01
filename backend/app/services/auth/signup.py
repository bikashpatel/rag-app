"""Email sign-up (docs/briefs/signup-auth.md 4.1 to 4.3): a pending row bound to the chosen password and to the
browser's rag_signup cookie, which becomes an account only when its emailed link is presented with that browser or
that password. The request does the same work for every address and never reads `users`; the worker decides what to
send."""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Final, Literal

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models import AuthToken, PendingSignup, User, utcnow
from app.schemas_auth import EmailWithChallenge, SignupCheck, SignupComplete, SignupRequest
from app.security import hash_password_async, verify_password_async
from app.services import account
from app.services.auth import (
    audit,
    cookies,
    emails,
    login,
    passwords,
    signup_settings,
    throttle,
    tokens,
    turnstile,
    usernames,
)
from app.services.mail import outbox

COOKIE_PATH: Final = "/api/auth/signup"
PENDING_DAYS: Final = 7  # a pending sign-up and the browser cookie live this long
LIVE_PER_ADDRESS: Final = 3
PASSWORD_ATTEMPTS: Final = 5  # wrong passwords before the pending row is deleted
_BROWSER = re.compile(r"[A-Za-z0-9_-]{43}")
_UNSAFE_PATH = re.compile(r"[\x00-\x1f\x7f\\]")


def safe_next(value: str | None) -> str | None:
    """A path on this site (it starts with "/", not "//" or "/api/", without a backslash or control character), else
    None."""
    if not value or not value.startswith("/") or value.startswith(("//", "/api/")) or _UNSAFE_PATH.search(value):
        return None
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def browser_value(request: Request) -> str | None:
    """The request's rag_signup cookie when it has our form (43 base64url characters)."""
    value = request.cookies.get(cookies.cookie_name(cookies.SIGNUP), "")
    return value if _BROWSER.fullmatch(value) else None


def _same_browser(request: Request, row: PendingSignup) -> bool:
    value = browser_value(request)
    return (
        value is not None and row.browser_sha256 is not None and hmac.compare_digest(_sha256(value), row.browser_sha256)
    )


async def _require_email_signup(db: AsyncSession) -> None:
    if not (await signup_settings.availability(db)).email:
        raise login.signup_closed()


async def _public_mail(db: AsyncSession) -> None:
    if not await outbox.available(db, "public"):
        raise login.mail_unavailable()


# ---------------- Sign-up and resend (4.1, 4.3) ----------------


async def start(db: AsyncSession, request: Request, response: Response, body: SignupRequest) -> None:
    """POST /auth/signup, in the brief's order. Every address gets the same answer: within the silent per-address
    limit a pending row and one `signup` job, else nothing."""
    await _require_email_signup(db)
    await throttle.check(request, "signup")
    await turnstile.verify(body.turnstile_token, "signup", throttle.client_ip(request))
    address = login.parse_email(body.email)
    await passwords.check(body.password, context=(address.address, address.local))
    await _public_mail(db)
    password_hash = await hash_password_async(body.password)
    browser = browser_value(request) or secrets.token_urlsafe(32)
    cookies.set_auth_cookie(
        response, cookies.SIGNUP, browser, path=COOKIE_PATH, max_age=PENDING_DAYS * 86_400, samesite="strict"
    )
    if not await throttle.address_allowed("signup", address.delivery_key):
        return
    now = utcnow()
    row = PendingSignup(
        address=address.address,
        canonical=address.canonical,
        password_hash=password_hash,
        browser_sha256=_sha256(browser),
        next_path=safe_next(body.next),
        terms_version=settings.terms_version,
        terms_accepted_at=now,
        password_failures=0,
        ip=account.approximate_ip(request.client.host if request.client else None),
        created_at=now,
        expires_at=now + timedelta(days=PENDING_DAYS),
    )
    db.add(row)
    await db.flush()
    live = select(PendingSignup.id).where(PendingSignup.canonical == address.canonical, PendingSignup.expires_at > now)
    newest = live.order_by(PendingSignup.created_at.desc(), PendingSignup.id.desc()).limit(LIVE_PER_ADDRESS)
    await db.execute(
        delete(PendingSignup)
        .where(PendingSignup.id.in_(live), PendingSignup.id.not_in(newest))
        .execution_options(synchronize_session=False)
    )
    await outbox.enqueue(db, "signup", to=address.address, canonical=address.canonical, pending_signup_id=row.id)
    await db.commit()
    await outbox.wake()


async def resend(db: AsyncSession, request: Request, body: EmailWithChallenge) -> None:
    """POST /auth/signup/resend: one `signup` job for the newest live pending row this browser started for the
    address (or none, and the worker picks the newest live one); the same query and answer for every address."""
    await _require_email_signup(db)
    await throttle.check(request, "resend")
    await turnstile.verify(body.turnstile_token, "resend", throttle.client_ip(request))
    address = login.parse_email(body.email)
    await _public_mail(db)
    if not await throttle.address_allowed("signup", address.delivery_key):
        return
    row_id = await db.scalar(
        select(PendingSignup.id)
        .where(
            PendingSignup.canonical == address.canonical,
            PendingSignup.expires_at > utcnow(),
            PendingSignup.browser_sha256 == _sha256(browser_value(request) or ""),
        )
        .order_by(PendingSignup.created_at.desc(), PendingSignup.id.desc())
        .limit(1)
    )
    await outbox.enqueue(db, "signup", to=address.address, canonical=address.canonical, pending_signup_id=row_id)
    await db.commit()
    await outbox.wake()


# ---------------- Finishing (4.2) ----------------


async def _token_and_row(db: AsyncSession, token: str, *, lock: bool) -> tuple[AuthToken, PendingSignup]:
    """The live `signup` token and its live pending row, the token still matching the row's address; else 400
    link_invalid."""
    row = await tokens.find(db, ["signup"], token, lock=lock)
    pending = None
    if row is not None and row.pending_signup_id is not None:
        pending = await db.get(PendingSignup, row.pending_signup_id, with_for_update=lock, populate_existing=lock)
    if row is None or pending is None or pending.expires_at <= utcnow():
        raise login.link_invalid()
    if not tokens.address_matches(row, pending.canonical):
        raise login.link_invalid()
    return row, pending


async def check(db: AsyncSession, request: Request, token: str) -> SignupCheck:
    await throttle.check(request, "token")
    row, pending = await _token_and_row(db, token, lock=False)
    return SignupCheck(
        email=pending.address,
        expires_at=min(row.expires_at, pending.expires_at),
        needs_password=not _same_browser(request, pending),
        next=pending.next_path,
    )


async def drop_pending(db: AsyncSession, canonical: str) -> None:
    """Every pending sign-up for the address (their tokens cascade): it has an account now."""
    await db.execute(delete(PendingSignup).where(PendingSignup.canonical == canonical))


async def email_held(db: AsyncSession, canonical: str) -> bool:
    return await db.scalar(select(User.id).where(User.email_canonical == canonical).limit(1)) is not None


async def new_account(
    db: AsyncSession,
    source: Literal["email", "google"],
    *,
    password_hash: str | None,
    address: str | None,
    canonical: str | None,
    terms_version: str,
    terms_accepted_at: datetime,
) -> User | None:
    """A self-signed-up user with a generated username (flushed in a savepoint, so the caller's locks stay), the
    verified address when given; None when a user holds that address (checked again by the unique index)."""
    if canonical is not None and await email_held(db, canonical):
        return None
    for attempt in range(2):
        user = User(
            username=await usernames.generate(db),
            password_hash=password_hash,
            is_admin=False,
            is_active=True,
            email=address,
            email_canonical=canonical,
            email_verified_at=utcnow() if address is not None else None,
            signup_source=source,
            terms_version=terms_version,
            terms_accepted_at=terms_accepted_at,
            identities=[],
        )
        try:
            async with db.begin_nested():
                db.add(user)
            return user
        except IntegrityError:  # the address taken meanwhile, or (rarely) the username drawn by another sign-up
            if canonical is not None and await email_held(db, canonical):
                return None
            if attempt:
                raise
    raise AssertionError("unreachable")


async def _wrong_password(db: AsyncSession, pending: PendingSignup) -> AppError:
    pending.password_failures += 1
    if pending.password_failures >= PASSWORD_ATTEMPTS:
        await db.delete(pending)
        await db.commit()
        return AppError(400, "Too many wrong passwords. Sign up again to choose a new one.", "signup_reset")
    await db.commit()
    return login.wrong_password("That isn't the password you chose when you signed up.")


async def complete(db: AsyncSession, request: Request, response: Response, body: SignupComplete) -> User:
    """POST /auth/signup/complete: the account, signed in with a new session. The binding is this browser's cookie, or
    else the chosen password."""
    await throttle.check(request, "token")
    _, pending = await _token_and_row(db, body.token, lock=True)
    if not _same_browser(request, pending):
        if not body.password:
            raise login.wrong_password("That isn't the password you chose when you signed up.")
        if not await verify_password_async(body.password, pending.password_hash):
            raise await _wrong_password(db, pending)
    current = await signup_settings.load(db)
    if not (current.open and current.email):
        raise login.signup_closed()
    canonical = pending.canonical
    user = await new_account(
        db,
        "email",
        password_hash=pending.password_hash,
        address=pending.address,
        canonical=canonical,
        terms_version=pending.terms_version,
        terms_accepted_at=pending.terms_accepted_at,
    )
    if user is None:
        await drop_pending(db, canonical)
        await db.commit()
        raise login.email_taken()
    await drop_pending(db, canonical)
    await outbox.unsuppress(db, emails.digest(canonical))  # the link reached the address: mail may go there again
    await audit.record(
        db,
        "signup.completed",
        actor="self",
        user_id=user.id,
        request=request,
        detail={"method": "email", "email": emails.mask(user.email or "")},
    )
    await login.start_session(db, request, response, user, "password")
    cookies.clear_auth_cookie(response, cookies.SIGNUP, path=COOKIE_PATH)
    return user
