"""Google identities linked to users (docs/briefs/signup-auth.md 3.4, 4.6): keyed on (issuer, subject), at most one
per user. Google's address is informational once linked: an account's address is users.email alone. The kind of an
address says how far Google vouches for it: `gmail` and `workspace` addresses are verified now, anything else is
`unverified` and never looked up among users."""

from typing import Final, Literal

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import User, UserIdentity, utcnow
from app.services import account
from app.services.auth import audit, emails, login
from app.services.mail import outbox

PROVIDER: Final = "google"
ISSUER: Final = "https://accounts.google.com"
ISSUERS: Final = frozenset({ISSUER, "accounts.google.com"})

Kind = Literal["gmail", "workspace", "unverified"]


def kind(address: emails.Email, verified: bool, hosted_domain: str | None) -> Kind:
    """`gmail`: an @gmail.com address Google verified; `workspace`: verified with an `hd` claim; else `unverified`."""
    if verified and address.domain == "gmail.com":
        return "gmail"
    if verified and hosted_domain:
        return "workspace"
    return "unverified"


async def find(db: AsyncSession, issuer: str, subject: str) -> UserIdentity | None:
    identity: UserIdentity | None = await db.scalar(
        select(UserIdentity).where(UserIdentity.issuer == issuer, UserIdentity.subject == subject)
    )
    return identity


async def link(
    db: AsyncSession,
    user: User,
    *,
    issuer: str,
    subject: str,
    address: str,
    email_kind: Kind,
    method: Literal["auto", "manual"],
    request: Request | None,
) -> UserIdentity:
    """Links the Google account to the user (the caller commits; the unique keys refuse a second link) with a
    google.linked event and a google_linked notice to the account's verified email."""
    now = utcnow()
    identity = UserIdentity(
        user_id=user.id,
        provider=PROVIDER,
        issuer=issuer,
        subject=subject,
        email_at_link=address,
        email_kind=email_kind,
        created_at=now,
        last_used_at=now,
    )
    user.identities.append(identity)
    await db.flush()
    masked = emails.mask(address)
    detail = {"method": method, "google": masked}
    await audit.record(db, "google.linked", actor="self", user_id=user.id, request=request, detail=detail)
    await login.notice(db, "google_linked", user, {"google_email": masked})
    return identity


async def unlink(
    db: AsyncSession, user: User, identity: UserIdentity, *, reason: Literal["self", "reset"], request: Request | None
) -> None:
    """Removes the identity with a google.unlinked event; a removal by the user also mails a google_unlinked notice
    (a reset's "password changed" notice says it instead). The caller commits."""
    user.identities.remove(identity)
    await db.flush()
    masked = emails.mask(identity.email_at_link)
    detail = {"reason": reason, "google": masked}
    await audit.record(db, "google.unlinked", actor="self", user_id=user.id, request=request, detail=detail)
    if reason == "self":
        await login.notice(db, "google_unlinked", user, {"google_email": masked})


async def disconnect(db: AsyncSession, request: Request, user: User, password: str) -> None:
    """DELETE /me/google: only while a password and a verified email remain to sign in with (409
    last_sign_in_method), and with the password re-entered."""
    if user.is_admin:
        raise login.env_admin()
    identity = account.google_identity(user)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Google sign-in is connected.")
    if user.password_hash is None or user.email is None:
        raise AppError(
            409, "Set a password and confirm an email first, so you can still sign in.", "last_sign_in_method"
        )
    await login.confirm_password(request, user, password)
    await unlink(db, user, identity, reason="self", request=request)
    await db.commit()
    await outbox.wake()
