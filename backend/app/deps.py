from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User
from app.security import create_access_token, decode_access_token

_UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


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
        raise _UNAUTHORIZED
    payload = decode_access_token(token)
    if payload is None:
        raise _UNAUTHORIZED
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise _UNAUTHORIZED
    user = await db.get(User, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise _UNAUTHORIZED
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def set_session_cookie(response: Response, user: User) -> None:
    token = create_access_token(user.id, user.token_version)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
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
