"""Usernames (docs/briefs/signup-auth.md 3.3): a self sign-up gets a random handle, never derived from the email, and
a deleted user's username is never issued again."""

import secrets
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UsernameTombstone, utcnow

PREFIX: Final = "user-"
ALPHABET: Final = "abcdefghijklmnopqrstuvwxyz234567"  # base32: 8 characters are 40 bits
LENGTH: Final = 8
DRAWS: Final = 5


async def generate(db: AsyncSession) -> str:
    """"user-" and 8 random base32 characters, neither taken nor retired. The insert's unique constraint stays the
    backstop for a draw racing another sign-up."""
    for _ in range(DRAWS):
        candidate = PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        taken = await db.scalar(select(User.id).where(User.username == candidate).limit(1))
        if taken is None and not await retired(db, candidate):
            return candidate
    raise RuntimeError(f"No free username in {DRAWS} draws")


async def retired(db: AsyncSession, username: str) -> bool:
    """The username belonged to a deleted account."""
    return await db.get(UsernameTombstone, username) is not None


async def tombstone(db: AsyncSession, username: str) -> None:
    """Retires a deleted user's username (in the caller's transaction; never commits)."""
    await db.merge(UsernameTombstone(username=username, deleted_at=utcnow()))
