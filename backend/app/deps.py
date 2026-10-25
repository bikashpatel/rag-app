from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User
from app.security import create_access_token, decode_access_token


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def _extract_token(request: Request) -> str | None:
    token = request.cookies.get(settings.cookie_name)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return None


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = _extract_token(request)
    if not token:
        raise _unauthorized()
    payload = decode_access_token(token)
    if payload is None:
        raise _unauthorized()
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise _unauthorized() from None
    user = await db.get(User, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise _unauthorized()
    sid, exp = payload.get("sid"), payload.get("exp")
    request.state.session_id = sid if isinstance(sid, int) and not isinstance(sid, bool) else None
    request.state.session_expires_at = (
        datetime.fromtimestamp(exp, timezone.utc)
        if isinstance(exp, (int, float)) and not isinstance(exp, bool)
        else None
    )
    return user


def current_session_id(request: Request) -> int | None:
    """The `sid` of the session that authenticated this request (get_current_user ran); None for older tokens."""
    return getattr(request.state, "session_id", None)


def current_session_expiry(request: Request) -> datetime | None:
    """When the token that authenticated this request expires (get_current_user ran)."""
    return getattr(request.state, "session_expires_at", None)


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def set_session_cookie(
    response: Response, user: User, session_id: int | None = None, expires_at: datetime | None = None
) -> None:
    """`expires_at`: keep the re-issued session's expiry (see create_access_token); None = a fresh lifetime."""
    token = create_access_token(user.id, user.token_version, session_id, expires_at)
    lifetime = settings.access_token_expire_minutes * 60
    if expires_at is not None:
        lifetime = max(1, min(lifetime, int((expires_at - datetime.now(timezone.utc)).total_seconds())))
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=lifetime,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
