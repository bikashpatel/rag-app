"""Email sign-up and password reset (SPEC.md section 6, "Sign-up, Google sign-in, password reset and email";
docs/briefs/signup-auth.md 4.1 to 4.3 and 4.7). No session is needed or used."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas_account import Me
from app.schemas_auth import (
    Accepted,
    EmailWithChallenge,
    PasswordReset,
    PasswordTokenInfo,
    SignupCheck,
    SignupComplete,
    SignupRequest,
    TokenRequest,
)
from app.services.auth import login, reset, signup

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
async def start_signup(
    body: SignupRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> Accepted:
    await signup.start(db, request, response, body)
    return Accepted()


@router.post("/signup/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_signup(body: EmailWithChallenge, request: Request, db: AsyncSession = Depends(get_db)) -> Accepted:
    await signup.resend(db, request, body)
    return Accepted()


@router.post("/signup/check")
async def check_signup(body: TokenRequest, request: Request, db: AsyncSession = Depends(get_db)) -> SignupCheck:
    return await signup.check(db, request, body.token)


@router.post("/signup/complete")
async def complete_signup(
    body: SignupComplete, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> Me:
    user = await signup.complete(db, request, response, body)
    return await login.me(db, user)


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(body: EmailWithChallenge, request: Request, db: AsyncSession = Depends(get_db)) -> Accepted:
    await reset.forgot(db, request, body)
    return Accepted()


@router.post("/password/check")
async def check_reset(body: TokenRequest, request: Request, db: AsyncSession = Depends(get_db)) -> PasswordTokenInfo:
    return await reset.check(db, request, body.token)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: PasswordReset, request: Request, db: AsyncSession = Depends(get_db)) -> None:
    await reset.reset(db, request, body)
