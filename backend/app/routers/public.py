"""What the public pages read without a session (SPEC.md section 6, "Billing"; docs/briefs/plans-billing.md 10): the
business details and the active plans. Nothing here depends on a user."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas_billing import PublicPlan, PublicSite
from app.services.billing import business, plans

router = APIRouter(prefix="/public", tags=["public"])
CACHE = "public, max-age=60"


@router.get("/site")
async def site(response: Response, db: AsyncSession = Depends(get_db)) -> PublicSite:
    response.headers["Cache-Control"] = CACHE
    return await business.public_site(db)


@router.get("/plans")
async def public_plans(response: Response, db: AsyncSession = Depends(get_db)) -> list[PublicPlan]:
    response.headers["Cache-Control"] = CACHE
    cat = await plans.catalog(db)
    return [plans.public_plan(plan, cat) for plan in await plans.active_plans(db)]
