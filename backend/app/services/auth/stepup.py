"""Google step-up grants (docs/briefs/signup-auth.md 4.5, S13, S14): a fresh Google sign-in as the linked identity
lets one session of an account without a password change its email or set a first password, once, within 5 minutes.
A session token without a `sid` (from before sign-in history) can't hold one."""

from datetime import datetime, timedelta
from typing import Final

from app.models import utcnow
from app.redis_client import get_redis

GRANT_SECONDS: Final = 300


def _key(user_id: int, sid: int) -> str:
    return f"auth:stepup:{user_id}:{sid}"


async def grant(user_id: int, sid: int) -> datetime:
    """Grants the session a step-up; returns when it expires."""
    await get_redis().set(_key(user_id, sid), "1", ex=GRANT_SECONDS)
    return utcnow() + timedelta(seconds=GRANT_SECONDS)


async def until(user_id: int, sid: int | None) -> datetime | None:
    """When the session's grant expires; None without one."""
    if sid is None:
        return None
    left = await get_redis().pttl(_key(user_id, sid))
    return utcnow() + timedelta(milliseconds=left) if left > 0 else None


async def spend(user_id: int, sid: int | None) -> bool:
    """Takes the session's grant (once); False without one."""
    if sid is None:
        return False
    return await get_redis().getdel(_key(user_id, sid)) is not None
