"""Adding, changing and confirming the account's email (docs/briefs/signup-auth.md 4.8): the change needs the
password (or, without one, a Google step-up), waits as a pending email until the confirmation link to the new address
is spent by the signed-in owner of the account that asked, and then tells the old address, with an undo link the
first time in 7 days (undo.py)."""

import base64
import hashlib
from datetime import timedelta
from typing import Final

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models import AuthToken, PendingEmail, User, utcnow
from app.schemas_auth import EmailChange, EmailConfirmed, EmailTokenInfo
from app.services.auth import audit, emails, login, signup, stepup, throttle, tokens
from app.services.billing import paid
from app.services.mail import outbox

PENDING_HOURS: Final = 24  # a pending email expires this long after its last mail


def reauth_required() -> AppError:
    return AppError(403, "Confirm it's you with Google first.", "reauth_required")


def wrong_account() -> AppError:
    return AppError(403, "Sign in to the account that asked for this change.", "wrong_account")


# The address an undo restores. auth_tokens keeps only its hash, so the email.changed event carries it encrypted with
# a key derived from SECRET_KEY: never readable in the database, a backup or the user's export (which leaves it out).
def _fernet() -> Fernet:
    digest = hashlib.sha256(b"rag-email-undo:" + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(address: str) -> str:
    return _fernet().encrypt(address.encode()).decode()


def unseal(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:  # sealed under another SECRET_KEY
        return None


async def _class_available(db: AsyncSession, user: User) -> bool:
    """Room today for the confirmation: `public` until the account has paid, then `account`."""
    return await outbox.available(db, await outbox.class_of(db, "email_confirm", user.id))


async def _limit(db: AsyncSession, request: Request, user: User) -> None:
    """5 email changes and resends a day (3 for an account that never paid, which also count in the network's
    `signup` bucket)."""
    unpaid = not await paid.has_paid(db, user.id)
    await throttle.check(request, "email_change", subject=str(user.id), unpaid=unpaid)
    if unpaid:
        await throttle.check(request, "signup")


async def _queue_confirmation(db: AsyncSession, user: User, row: PendingEmail, address: emails.Email) -> bool:
    """One `email_confirm` job within the silent per-address limit (kind `confirm`)."""
    if not await throttle.address_allowed("confirm", address.delivery_key):
        return False
    await outbox.enqueue(
        db, "email_confirm", to=row.address, canonical=row.canonical, user_id=user.id, pending_email_id=row.id
    )
    return True


async def request_change(db: AsyncSession, request: Request, user: User, sid: int | None, body: EmailChange) -> None:
    """POST /me/email: replaces the account's pending email and queues its confirmation. Whether another account
    holds the address is decided at confirmation."""
    if user.is_admin:
        raise login.env_admin()
    address = login.parse_email(body.email)
    if address.canonical == user.email_canonical:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="That's already your email.")
    if user.password_hash is not None:
        await login.confirm_password(request, user, body.password)
    elif not await stepup.spend(user.id, sid):
        raise reauth_required()
    await _limit(db, request, user)
    if not await _class_available(db, user):
        raise login.mail_unavailable()
    await tokens.revoke(db, ["reset", "email"], user_id=user.id)
    await db.execute(delete(PendingEmail).where(PendingEmail.user_id == user.id))
    now = utcnow()
    row = PendingEmail(
        user_id=user.id,
        address=address.address,
        canonical=address.canonical,
        source="user",
        created_at=now,
        expires_at=now + timedelta(hours=PENDING_HOURS),
    )
    db.add(row)
    await db.flush()
    detail = {"source": "user", "email": emails.mask(address.address)}
    await audit.record(db, "email.pending", actor="self", user_id=user.id, request=request, detail=detail)
    queued = await _queue_confirmation(db, user, row, address)
    await db.commit()
    if queued:
        await outbox.wake()


async def _pending(db: AsyncSession, user: User) -> PendingEmail | None:
    row: PendingEmail | None = await db.scalar(select(PendingEmail).where(PendingEmail.user_id == user.id))
    return row


async def resend(db: AsyncSession, request: Request, user: User) -> None:
    """POST /me/email/resend: another confirmation for the pending email (409 without one)."""
    if user.is_admin:
        raise login.env_admin()
    row = await _pending(db, user)
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No email is waiting for confirmation.")
    await _limit(db, request, user)
    if not await _class_available(db, user):
        raise login.mail_unavailable()
    queued = await _queue_confirmation(db, user, row, login.parse_email(row.address))
    await db.commit()
    if queued:
        await outbox.wake()


async def cancel(db: AsyncSession, user: User) -> None:
    """DELETE /me/email/pending: the pending email and its links gone."""
    if user.is_admin:
        raise login.env_admin()
    await tokens.revoke(db, ["email"], user_id=user.id)
    await db.execute(delete(PendingEmail).where(PendingEmail.user_id == user.id))
    await db.commit()


# ---------------- Confirming (/confirm-email) ----------------


async def _token_and_row(db: AsyncSession, token: str, *, lock: bool) -> tuple[AuthToken, PendingEmail, User]:
    """The live `email` token, its pending email (the token mailed to that address) and the active account that
    asked; else 400 link_invalid."""
    row = await tokens.find(db, ["email"], token, lock=lock)
    pending = None
    if row is not None and row.pending_email_id is not None:
        pending = await db.get(PendingEmail, row.pending_email_id, with_for_update=lock or None, populate_existing=lock)
    if row is None or pending is None or not tokens.address_matches(row, pending.canonical):
        raise login.link_invalid()
    user = await db.get(User, pending.user_id, with_for_update=lock or None, populate_existing=lock)
    if user is None or not user.is_active or user.is_admin:
        raise login.link_invalid()
    return row, pending, user


async def check(db: AsyncSession, request: Request, token: str) -> EmailTokenInfo:
    """POST /auth/email/check: the address and the account that asked (masked), and whether this session is it."""
    await throttle.check(request, "token")
    _, pending, user = await _token_and_row(db, token, lock=False)
    current = await login.session_user(request, db)
    return EmailTokenInfo(
        email=pending.address,
        account=emails.mask(user.email) if user.email else user.username,
        signed_in=current is not None and current.id == user.id,
    )


async def confirm(db: AsyncSession, request: Request, session: User, token: str) -> EmailConfirmed:
    """POST /auth/email/verify, with the session of the account that asked: the address becomes its verified email
    (409 email_taken when another account holds it now), and the old address gets the email_changed notice."""
    await throttle.check(request, "token")
    _, pending, user = await _token_and_row(db, token, lock=True)
    if session.id != user.id:
        raise wrong_account()
    address, canonical, source, user_id = pending.address, pending.canonical, pending.source, user.id
    old = user.email
    await db.delete(pending)
    if await signup.email_held(db, canonical):
        await db.commit()
        raise login.email_taken()
    try:
        async with db.begin_nested():
            user.email, user.email_canonical, user.email_verified_at = address, canonical, utcnow()
    except IntegrityError:  # another account confirmed the same address at the same moment
        await db.rollback()
        await db.execute(
            delete(PendingEmail).where(PendingEmail.user_id == user_id, PendingEmail.canonical == canonical)
        )
        await db.commit()
        raise login.email_taken() from None
    await signup.drop_pending(db, canonical)
    await tokens.revoke(db, ["reset", "email"], user_id=user.id)
    await outbox.unsuppress(db, emails.digest(canonical))
    if old is None:
        detail: dict[str, object] = {"source": source, "email": emails.mask(address)}
        await audit.record(db, "email.confirmed", actor="self", user_id=user.id, request=request, detail=detail)
    else:
        detail = {"from": emails.mask(old), "to": emails.mask(address), "restore": seal(old)}
        await audit.record(db, "email.changed", actor="self", user_id=user.id, request=request, detail=detail)
    queued = old is not None and outbox.configured()
    if old is not None and queued:
        await outbox.enqueue(db, "email_changed", to=old, user_id=user.id)
    await db.commit()
    if queued:
        await outbox.wake()
    return EmailConfirmed(email=address)
