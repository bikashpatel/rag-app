"""Login, logout, the current user and password changes."""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, BeforeValidator, Field, StringConstraints
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import clear_session_cookie, get_current_user, set_session_cookie
from app.models import User, utcnow
from app.redis_client import get_redis
from app.schemas import Me, UsageSummary, UserLimits
from app.security import hash_password_async, needs_rehash, verify_password_async
from app.services.usage import usage_summary, user_limits

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_LOGIN = "Invalid username or password"
# Generous so a long ADMIN_PASSWORD from .env still works; bounded so hashing stays cheap.
_MAX_LOGIN_PASSWORD = 1024


def _normalize_username(value: object) -> object:
    # Postgres text can't hold NUL characters (the lookup would fail instead of finding no user).
    return value.replace("\x00", "").strip().lower() if isinstance(value, str) else value


class LoginRequest(BaseModel):
    username: Annotated[str, StringConstraints(min_length=1, max_length=64), BeforeValidator(_normalize_username)]
    password: str = Field(min_length=1, max_length=_MAX_LOGIN_PASSWORD)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=_MAX_LOGIN_PASSWORD)
    new_password: str = Field(min_length=8, max_length=256)


def _user_key(username: str) -> str:
    return f"login:fail:user:{username}"


def _ip_key(request: Request) -> str:
    return f"login:fail:ip:{request.client.host if request.client else 'unknown'}"


async def _adjust_counters(redis: Redis, keys: list[str], amount: int) -> list[tuple[int, int]]:
    """Add `amount` to each failure counter. Returns (count, seconds left in its window) per key.

    A counter's window starts with its first change; later changes don't extend it.
    """
    async with redis.pipeline(transaction=True) as pipe:
        for key in keys:
            pipe.incrby(key, amount)
            pipe.expire(key, settings.login_window_seconds, nx=True)
            pipe.ttl(key)
        results = await pipe.execute()
    return list(zip(results[0::3], results[2::3], strict=True))


async def _count_attempt(redis: Redis, limits: dict[str, int]) -> None:
    """Count the attempt as failed (on every key -> its limit) before checking the password, so parallel
    guesses can't all pass the limit before the first one fails. Over a key's limit it takes the attempt
    back and raises 429."""
    keys = list(limits)
    counters = await _adjust_counters(redis, keys, 1)
    waits = [ttl for key, (count, ttl) in zip(keys, counters, strict=True) if count > limits[key]]
    if not waits:
        return
    await _adjust_counters(redis, keys, -1)
    seconds = max(*waits, 1)
    minutes = math.ceil(seconds / 60)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many failed login attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
        headers={"Retry-After": str(seconds)},
    )


async def build_me(db: AsyncSession, user: User) -> Me:
    return Me(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        limits=UserLimits(**user_limits(user)),
        usage=UsageSummary(**await usage_summary(db, user)),
    )


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> Me:
    redis = get_redis()
    user_key, ip_key = _user_key(body.username), _ip_key(request)
    await _count_attempt(redis, {user_key: settings.login_max_attempts, ip_key: settings.login_max_attempts_per_ip})

    user = await db.scalar(select(User).where(User.username == body.username))
    valid = await verify_password_async(body.password, user.password_hash if user is not None else None)
    if user is None or not valid or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_LOGIN)

    # Success clears the username's failures; the IP keeps its earlier ones but not this attempt.
    await redis.delete(user_key)
    await _adjust_counters(redis, [ip_key], -1)
    if needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(body.password)
    user.last_login_at = utcnow()
    await db.commit()
    set_session_cookie(response, user)
    return await build_me(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Me:
    return await build_me(db, user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if user.is_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The admin password is managed in .env")
    # Shares the login counter, so a stolen session can't be used to guess the password.
    redis = get_redis()
    user_key = _user_key(user.username)
    await _count_attempt(redis, {user_key: settings.login_max_attempts})
    # Lock and re-read the row: token_version and password_hash are read-modify-write, and an admin password
    # reset writes them too. A session that was ended meanwhile can't set a password.
    seen_version = user.token_version
    await db.execute(
        select(User).where(User.id == user.id).with_for_update(key_share=True).execution_options(populate_existing=True)
    )
    if not user.is_active or user.token_version != seen_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not await verify_password_async(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    user.password_hash = await hash_password_async(body.new_password)
    user.token_version += 1
    await db.commit()
    await redis.delete(user_key)
    set_session_cookie(response, user)
