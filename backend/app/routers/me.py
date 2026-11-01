"""The caller's own account (SPEC.md section 6, "Account"; docs/briefs/ui-shell-settings.md, section 4): preferences,
the account with its sign-in history, the allowance window's usage, the data export, and the account's email, first
password and Google sign-in (docs/briefs/signup-auth.md 4.6, 4.8, 4.9). Only the caller's own rows; no ids in the
paths. Open to users without a plan (their account and Settings, plans-billing B4)."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_session_id, get_current_user, get_entitlements
from app.models import User
from app.schemas import Preferences
from app.schemas_account import AccountInfo, MyUsage, PreferencesUpdate
from app.schemas_auth import Accepted, EmailChange, NewPassword, PasswordConfirm
from app.services import account, export
from app.services.auth import email_change, identities, reset
from app.services.billing.entitlements import Entitlements

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/preferences")
async def get_preferences(
    user: User = Depends(get_current_user),
    ent: Entitlements = Depends(get_entitlements),
    db: AsyncSession = Depends(get_db),
) -> Preferences:
    return await account.read_preferences(db, user, ent)


@router.patch("/preferences")
async def update_preferences(
    body: PreferencesUpdate,
    user: User = Depends(get_current_user),
    ent: Entitlements = Depends(get_entitlements),
    db: AsyncSession = Depends(get_db),
) -> Preferences:
    return await account.update_preferences(db, user, body, ent)


@router.get("/account")
async def get_account(
    request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> AccountInfo:
    return await account.account_info(db, user, current_session_id(request))


@router.get("/usage")
async def get_usage(
    user: User = Depends(get_current_user),
    ent: Entitlements = Depends(get_entitlements),
    db: AsyncSession = Depends(get_db),
) -> MyUsage:
    return await account.my_usage(db, user, ent)


@router.post(
    "/export",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}, "description": "The caller's data as a ZIP download"}},
)
async def export_data(
    request: Request,
    user: User = Depends(get_current_user),
    ent: Entitlements = Depends(get_entitlements),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """No body. A POST, so nginx's cross-site check applies (a page on another localhost port can't start one)."""
    return await export.export_response(request, db, user, ent)


# ---------------- Email, first password, Google sign-in (the env admin: 400) ----------------


@router.post("/email", status_code=status.HTTP_202_ACCEPTED)
async def change_email(
    body: EmailChange, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Accepted:
    """The password re-entered, or a Google step-up for an account without one; a confirmation to the new address."""
    await email_change.request_change(db, request, user, current_session_id(request), body)
    return Accepted()


@router.post("/email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_email(
    request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> Accepted:
    await email_change.resend(db, request, user)
    return Accepted()


@router.delete("/email/pending", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_email(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    await email_change.cancel(db, user)


@router.post("/password/setup", status_code=status.HTTP_204_NO_CONTENT)
async def setup_password(
    body: NewPassword, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await reset.set_first_password(db, request, user, current_session_id(request), body.new_password)


@router.delete("/google", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_google(
    body: PasswordConfirm, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    await identities.disconnect(db, request, user, body.password)
