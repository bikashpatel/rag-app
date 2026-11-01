"""Sign-in by email or username (with the sign-in history), logout, the current user, the sign-up configuration,
password changes and logging out other sessions (SPEC.md section 6, "Auth")."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import (
    clear_session_cookie,
    current_session_expiry,
    current_session_id,
    get_current_user,
    get_entitlements,
    set_session_cookie,
)
from app.errors import AppError
from app.models import User
from app.schemas_account import Me
from app.schemas_auth import AuthConfig, LoginRequest, Password
from app.security import hash_password_async, verify_password_async
from app.services.auth import audit, login, passwords, signup_settings, throttle, tokens
from app.services.billing import business
from app.services.billing.entitlements import Entitlements
from app.services.mail import outbox

router = APIRouter(prefix="/auth", tags=["auth"])


class PasswordChange(BaseModel):
    current_password: Password
    new_password: Password  # the password policy (422 password_rejected)


@router.post("/login")
async def sign_in(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> Me:
    user = await login.password_sign_in(db, request, body)
    await login.start_session(db, request, response, user, "password")
    return await login.me(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Me:
    return await login.me(db, user)


@router.get("/config")
async def config(response: Response, db: AsyncSession = Depends(get_db)) -> AuthConfig:
    """What the sign-in, sign-up and reset pages may offer (public)."""
    response.headers["Cache-Control"] = "no-store"
    return AuthConfig(
        signup=await signup_settings.availability(db),
        google_signin=settings.google_signin_configured,
        password_reset=settings.mail_configured and settings.turnstile_configured,
        turnstile_site_key=settings.turnstile_site_key or None,
        password_min_length=settings.password_min_length,
        terms_version=settings.terms_version,
        contact_email=(await business.get_settings(db)).contact_email,
    )


@router.get("/upload-allowed", status_code=status.HTTP_204_NO_CONTENT)
async def upload_allowed(ent: Entitlements = Depends(get_entitlements)) -> None:
    """nginx asks this before reading an upload's body (FastAPI parses it before route dependencies run): 204 when
    the session may upload now (an active plan, or the admin), else 403, which nginx answers as 402 plan_required."""
    if not ent.access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Choose a plan to upload documents.")


async def _lock_session_user(db: AsyncSession, user: User) -> None:
    """Lock and re-read the user's row: token_version (and password_hash) are read-modify-write, and an admin password
    reset or deactivation writes them too. 401 when the session ended meanwhile."""
    seen_version = user.token_version
    await db.execute(
        select(User).where(User.id == user.id).with_for_update(key_share=True).execution_options(populate_existing=True)
    )
    if not user.is_active or user.token_version != seen_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Ends every other session (the caller's cookie is re-issued with the same sid), revokes the account's reset
    links and mails a "password changed" notice."""
    if user.is_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The admin password is managed in .env")
    if user.password_hash is None:
        raise AppError(400, "You don't have a password yet. Use Set a password.", "no_password")
    await passwords.check(body.new_password, context=login.password_context(user))
    # Shares the sign-in counter, so a stolen session can't be used to guess the password.
    key = throttle.account_key(user.id)
    wait = await throttle.login_wait(request, key)
    if wait is not None:
        raise throttle.too_many_attempts(wait)
    await throttle.login_failed(request, key)
    await _lock_session_user(db, user)  # a session that was ended meanwhile can't set a password
    if not await verify_password_async(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    await throttle.login_cleared(request, key)
    user.password_hash = await hash_password_async(body.new_password)
    user.token_version += 1
    await tokens.revoke(db, ["reset"], user_id=user.id)
    await audit.record(db, "password.changed", actor="self", user_id=user.id, request=request)
    queued = await login.notice(db, "password_changed", user)
    await db.commit()
    if queued:
        await outbox.wake()
    set_session_cookie(response, user, current_session_id(request))


@router.post("/logout-others", status_code=status.HTTP_204_NO_CONTENT)
async def logout_others(
    request: Request, response: Response, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    """End every other session (other browsers and devices, every Bearer token) and re-issue the caller's cookie with
    the same session id and expiry: no password, so it must not extend a (possibly stolen) session. Also for the env
    admin; not throttled; plugin connections are untouched."""
    await _lock_session_user(db, user)
    user.token_version += 1
    await db.commit()
    set_session_cookie(response, user, current_session_id(request), current_session_expiry(request))
