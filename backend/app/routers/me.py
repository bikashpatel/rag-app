"""The caller's own account (SPEC.md section 6, "Account"; docs/briefs/ui-shell-settings.md, section 4): preferences,
the account with its sign-in history, the allowance window's usage and the data export. Only the caller's own rows; no
ids in the paths. Open to users without a plan (their account and Settings, plans-billing B4)."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_session_id, get_current_user, get_entitlements
from app.models import User
from app.schemas import Preferences
from app.schemas_account import AccountInfo, MyUsage, PreferencesUpdate
from app.services import account, export
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
