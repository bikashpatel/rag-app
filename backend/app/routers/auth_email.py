"""Confirming and undoing an email (SPEC.md section 6, "Confirming and undoing an email"; docs/briefs/signup-auth.md
4.8): the pages opened from the emailed links. Only the confirmation itself needs a session, the asking account's."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas_auth import EmailConfirmed, EmailTokenInfo, TokenRequest, UndoInfo
from app.services.auth import email_change, undo

router = APIRouter(prefix="/auth/email", tags=["auth"])


@router.post("/check")
async def check(body: TokenRequest, request: Request, db: AsyncSession = Depends(get_db)) -> EmailTokenInfo:
    return await email_change.check(db, request, body.token)


@router.post("/verify")
async def verify(
    body: TokenRequest, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> EmailConfirmed:
    return await email_change.confirm(db, request, user, body.token)


@router.post("/undo-check")
async def undo_check(body: TokenRequest, request: Request, db: AsyncSession = Depends(get_db)) -> UndoInfo:
    return await undo.check(db, request, body.token)


@router.post("/undo", status_code=status.HTTP_204_NO_CONTENT)
async def undo_change(body: TokenRequest, request: Request, db: AsyncSession = Depends(get_db)) -> None:
    await undo.undo(db, request, body.token)
