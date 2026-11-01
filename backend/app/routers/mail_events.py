"""Resend's signed webhook for bounces and complaints (SPEC.md section 6, Sign-up; docs/briefs/signup-auth.md 6.6):
`POST /api/mail/resend-events`, no session; 404 without MAIL_WEBHOOK_SECRET."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.services.mail import webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mail", tags=["mail"])

_OK = {"ok": True}


async def _body(request: Request) -> bytes | None:
    """The raw body, or None when it is over 64 KB."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > webhook.MAX_BODY:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > webhook.MAX_BODY:
            return None
    return bytes(body)


@router.post(
    "/resend-events",
    responses={
        200: {"description": "Handled, or ignored (another type, a repeated delivery)"},
        401: {"description": "Bad or old signature"},
        404: {"description": "MAIL_WEBHOOK_SECRET isn't set"},
        413: {"description": "Over 64 KB"},
    },
)
async def resend_events(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    if not webhook.configured():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    body = await _body(request)
    if body is None:
        return JSONResponse({"detail": "Too large"}, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    try:
        msg_id = webhook.verify(request.headers, body)
    except webhook.SignatureError:
        return JSONResponse({"detail": "Bad signature"}, status_code=status.HTTP_401_UNAUTHORIZED)
    if not await webhook.first_delivery(msg_id):
        return JSONResponse(_OK)
    try:
        event = json.loads(body)
    except ValueError:
        logger.warning("Mail webhook: a signed delivery wasn't JSON; ignored")
        return JSONResponse(_OK)
    try:
        if isinstance(event, dict):
            await webhook.handle(db, event)
            await db.commit()
    except Exception:
        await webhook.forget(msg_id)
        raise
    return JSONResponse(_OK)
