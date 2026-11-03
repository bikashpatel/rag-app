"""Google sign-in (SPEC.md section 6, "Google sign-in"; docs/briefs/signup-auth.md 4.5, 4.6): start, the callback
Google redirects the browser to, and the pending Google sign-up's finish and cancel. Separate from the Gmail plugin's
connect and callback."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_session_id, get_current_user
from app.errors import AppError
from app.schemas_account import Me
from app.schemas_auth import GoogleFinish, GooglePending, GoogleStart, GoogleStarted
from app.services import account
from app.services.auth import cookies, google, login, signup, throttle, tokens
from app.services.mail import outbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["auth"])


@router.post("/start")
async def start(
    body: GoogleStart, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> GoogleStarted:
    """`signin` needs no session; `link` (the password re-entered) and `reauth` (a step-up) need the caller's."""
    if not google.configured():
        raise AppError(503, "Google sign-in isn't set up.", "google_unavailable")
    await throttle.check(request, "google_start")
    user, sid, hint = None, None, None
    next_path = signup.safe_next(body.next) or "/"
    if body.intent != "signin":
        user = await get_current_user(request, db)
        if user.is_admin:
            raise login.env_admin()
        sid = current_session_id(request)
        if body.intent == "link":
            if user.password_hash is None:
                raise AppError(400, "You don't have a password yet. Use Set a password.", "no_password")
            await login.confirm_password(request, user, body.password)
            next_path = google.ACCOUNT_PAGE
        else:
            identity = account.google_identity(user)
            if identity is None or sid is None:
                detail = "Connect Google first." if identity is None else "Sign in again first."
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
            hint = identity.subject
            next_path = signup.safe_next(body.next) or google.ACCOUNT_PAGE
    base = google.redirect_base(request.headers.get("origin"))
    url, binding = await google.start(
        intent=body.intent,
        base=base,
        next_path=next_path,
        from_=body.from_ if body.intent == "signin" else None,
        user=user,
        sid=sid,
        login_hint=hint,
    )
    cookies.set_auth_cookie(
        response,
        cookies.GOOGLE_SIGNIN,
        binding,
        path=google.CALLBACK_PATH,
        max_age=google.STATE_SECONDS,
        samesite="lax",
    )
    return GoogleStarted(authorize_url=url)


@router.get("/callback", response_class=RedirectResponse)
async def callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    """Google sends the browser here (a cross-site GET: the session cookie doesn't arrive; the state and the
    rag_gsignin cookie identify the flow). Always a 303 to a page of the app, never an error page."""
    try:
        outcome = await google.callback(
            db, request, dict(request.query_params), request.cookies.get(cookies.cookie_name(cookies.GOOGLE_SIGNIN))
        )
        response = RedirectResponse(outcome.url, status_code=status.HTTP_303_SEE_OTHER)
        if outcome.sign_in is not None:
            await login.start_session(db, request, response, outcome.sign_in, "google")
        if outcome.ticket is not None:
            cookies.set_auth_cookie(
                response,
                cookies.GOOGLE_TICKET,
                outcome.ticket,
                path=google.TICKET_PATH,
                max_age=google.TICKET_SECONDS,
                samesite="strict",
            )
        if outcome.wake:
            await outbox.wake()
    except Exception:
        logger.exception("The Google sign-in callback failed")
        await db.rollback()
        response = RedirectResponse(f"{tokens.base_url()}/login?google=error", status_code=status.HTTP_303_SEE_OTHER)
    cookies.clear_auth_cookie(response, cookies.GOOGLE_SIGNIN, path=google.CALLBACK_PATH)
    return response


@router.get("/pending")
async def pending(request: Request) -> GooglePending:
    return await google.pending(request)


@router.post("/finish")
async def finish(body: GoogleFinish, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> Me:
    user = await google.finish(db, request, response, body.name)
    return await login.me(db, user)


@router.post("/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(request: Request, response: Response) -> None:
    await google.cancel(request, response)
