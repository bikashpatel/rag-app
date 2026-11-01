"""Undoing an email change (docs/briefs/signup-auth.md 4.8): the link in the old address's email_changed notice
(the first of a chain of changes in 7 days) puts that address back, removes the password, ends every session, mails
a reset link to the restored address and tells the address it replaced. No session is needed: the old mailbox was
the account's verified address until the change."""

from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import AuthEvent, AuthToken, PendingEmail, User, utcnow
from app.schemas_auth import UndoInfo
from app.services.auth import audit, email_change, emails, login, signup, throttle, tokens
from app.services.mail import outbox

# The notice carrying the link may go out up to its not_after after the change (the cap, a provider outage); a day more
# for the clocks.
_NOTICE_DELAY = outbox.KINDS["email_changed"].not_after + timedelta(days=1)


def _taken() -> AppError:
    return login.email_taken("That address is now used by another account. Contact us.")


async def _restored_address(db: AsyncSession, row: AuthToken) -> str | None:
    """The address the token puts back: the newest email.changed event of its user, no older than the link's notice can
    be, whose old address the token was mailed to."""
    events = await db.scalars(
        select(AuthEvent)
        .where(
            AuthEvent.user_id == row.user_id,
            AuthEvent.type == "email.changed",
            AuthEvent.created_at >= row.created_at - _NOTICE_DELAY,
        )
        .order_by(AuthEvent.created_at.desc(), AuthEvent.id.desc())
    )
    for event in events:
        address = email_change.unseal((event.detail or {}).get("restore"))
        if address is not None and tokens.address_matches(row, address.lower()):
            return address
    return None


async def _token_and_user(db: AsyncSession, token: str, *, lock: bool) -> tuple[AuthToken, User, str]:
    row = await tokens.find(db, ["email_undo"], token, lock=lock)
    user = None
    if row is not None and row.user_id is not None:
        user = await db.get(User, row.user_id, with_for_update=lock or None, populate_existing=lock)
    if row is None or user is None or not user.is_active or user.is_admin:
        raise login.link_invalid()
    address = await _restored_address(db, row)
    if address is None:
        raise login.link_invalid()
    return row, user, address


async def check(db: AsyncSession, request: Request, token: str) -> UndoInfo:
    await throttle.check(request, "token")
    _, user, address = await _token_and_user(db, token, lock=False)
    return UndoInfo(old_email=address, new_email=emails.mask(user.email) if user.email else "")


async def undo(db: AsyncSession, request: Request, token: str) -> None:
    """POST /auth/email/undo: in one transaction the old address back (verified now), the pending email gone, the
    password removed, every session ended and every reset, email and undo link of the account revoked."""
    await throttle.check(request, "token")
    row, user, address = await _token_and_user(db, token, lock=True)
    canonical = address.lower()
    replaced = user.email
    if user.email_canonical != canonical and await signup.email_held(db, canonical):
        raise _taken()
    try:
        async with db.begin_nested():
            user.email, user.email_canonical, user.email_verified_at = address, canonical, utcnow()
    except IntegrityError:
        await db.rollback()
        raise _taken() from None
    user.password_hash = None
    user.token_version += 1
    await db.execute(delete(PendingEmail).where(PendingEmail.user_id == user.id))
    await tokens.spend(db, row)
    await tokens.revoke(db, ["reset", "email", "email_undo"], user_id=user.id)
    await signup.drop_pending(db, canonical)
    detail = {"to": emails.mask(address), "from": emails.mask(replaced) if replaced else None}
    await audit.record(db, "email.change_undone", actor="self", user_id=user.id, request=request, detail=detail)
    queued = outbox.configured()
    if queued:
        await outbox.enqueue(db, "password_reset", to=address, canonical=canonical, user_id=user.id)
        if replaced is not None and replaced.lower() != canonical:
            await outbox.enqueue(db, "email_undone", to=replaced, user_id=user.id)
    await db.commit()
    await throttle.forget_login_failures(throttle.account_key(user.id))
    if queued:
        await outbox.wake()
