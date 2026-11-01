"""Link tokens (docs/briefs/signup-auth.md S7, 8.3): 32 random bytes minted by the worker when it sends a mail, stored
only as SHA-256 beside the SHA-256 of the canonical address they were mailed to, carried in the link's fragment and
spent once by a POST from our page. A token is good only while it is unused, unexpired and (for `signup`, `email` and
`reset`) its address still matches."""

import hashlib
import hmac
import secrets
from collections.abc import Collection, Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import Final, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthToken, utcnow
from app.services.auth import emails

TokenPurpose = Literal["signup", "email", "reset", "email_undo"]

TTL: Final[Mapping[TokenPurpose, timedelta]] = MappingProxyType(
    {
        "signup": timedelta(hours=24),
        "email": timedelta(hours=24),
        "reset": timedelta(minutes=30),
        "email_undo": timedelta(days=7),
    }
)
PAGES: Final[Mapping[TokenPurpose, str]] = MappingProxyType(
    {"signup": "/verify-email", "email": "/confirm-email", "reset": "/reset-password", "email_undo": "/undo-email"}
)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def mint(
    db: AsyncSession,
    purpose: TokenPurpose,
    *,
    address_canonical: str,
    user_id: int | None = None,
    pending_signup_id: int | None = None,
    pending_email_id: int | None = None,
) -> str:
    """A new token bound to the address it is mailed to (flushed, not committed); only its hash is kept."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        AuthToken(
            purpose=purpose,
            token_sha256=_sha256(token),
            address_sha256=emails.digest(address_canonical),
            user_id=user_id,
            pending_signup_id=pending_signup_id,
            pending_email_id=pending_email_id,
            created_at=now,
            expires_at=now + TTL[purpose],
        )
    )
    await db.flush()
    return token


async def find(
    db: AsyncSession, purposes: Collection[TokenPurpose], token: str, *, lock: bool = False
) -> AuthToken | None:
    """The unused, unexpired token of one of `purposes` (locked FOR UPDATE with `lock`), else None."""
    query = select(AuthToken).where(
        AuthToken.token_sha256 == _sha256(token),
        AuthToken.purpose.in_(list(purposes)),
        AuthToken.used_at.is_(None),
        AuthToken.expires_at > utcnow(),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row: AuthToken | None = await db.scalar(query)
    return row


def address_matches(row: AuthToken, canonical: str) -> bool:
    """The token was mailed to this canonical address (the pending row's or the account's, now)."""
    return hmac.compare_digest(row.address_sha256, emails.digest(canonical))


async def spend(db: AsyncSession, row: AuthToken) -> None:
    row.used_at = utcnow()
    await db.flush()


async def revoke(
    db: AsyncSession,
    purposes: Collection[TokenPurpose],
    *,
    user_id: int | None = None,
    pending_signup_id: int | None = None,
    pending_email_id: int | None = None,
) -> None:
    """Ends the unused tokens of `purposes` that belong to every owner given (at least one). They are kept, as used,
    until the sweep, so a later click reads "already used"."""
    owners = [
        column == value
        for column, value in (
            (AuthToken.user_id, user_id),
            (AuthToken.pending_signup_id, pending_signup_id),
            (AuthToken.pending_email_id, pending_email_id),
        )
        if value is not None
    ]
    if not owners:
        raise ValueError("revoke needs a user, a pending sign-up or a pending email")
    await db.execute(
        update(AuthToken)
        .where(AuthToken.purpose.in_(list(purposes)), AuthToken.used_at.is_(None), *owners)
        .values(used_at=utcnow())
        .execution_options(synchronize_session=False)
    )


async def has_live(db: AsyncSession, purpose: TokenPurpose, *, user_id: int) -> bool:
    """The user holds an unused, unexpired token of `purpose` (e.g. the one email_undo of a chain of changes)."""
    found = await db.scalar(
        select(AuthToken.id)
        .where(
            AuthToken.purpose == purpose,
            AuthToken.user_id == user_id,
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > utcnow(),
        )
        .limit(1)
    )
    return found is not None


def base_url() -> str:
    """Where mailed links point: the first https entry of PUBLIC_BASE_URLS, else the first entry (development)."""
    from app.plugins import oauth  # it imports the plugin registry, which imports app.deps

    origins = oauth.public_base_urls() or ["http://localhost:8080"]
    return next((origin for origin in origins if origin.startswith("https://")), origins[0])


def link(purpose: TokenPurpose, token: str) -> str:
    """<first https origin>/<page>#t=<token>: a fragment reaches neither nginx's log nor a Referer."""
    return f"{base_url()}{PAGES[purpose]}#t={token}"
