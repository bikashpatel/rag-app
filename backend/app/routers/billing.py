"""The caller's plans, checkout, orders, payments, receipts and billing profile, and Razorpay's two inbound endpoints
(SPEC.md section 6, "Billing"; docs/briefs/plans-billing.md 6 and 7.2). Open to users without a plan; the return and
the webhook take no session (the return's signature and the webhook's HMAC are checked instead)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import BillingOrder, BillingReceipt, User, UserBillingProfile, utcnow
from app.schemas import MAX_INT
from app.schemas_billing import (
    BillingProfile,
    BillingStatus,
    CheckoutRequest,
    CheckoutStarted,
    OrderView,
    PaymentView,
    PlanCatalog,
    WebhookAck,
)
from app.services.billing import business, checkout, payments, receipts, reconcile, summary, webhooks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

PublicId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,24}$")]


async def _own_order(db: AsyncSession, user: User, public_id: str) -> BillingOrder:
    order = await db.scalar(
        select(BillingOrder).where(BillingOrder.public_id == public_id, BillingOrder.user_id == user.id)
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


@router.get("/plans")
async def plans(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PlanCatalog:
    return await checkout.plan_catalog(db, user)


@router.get("/status")
async def billing_status(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> BillingStatus:
    return await summary.billing_status(db, user)


@router.post("/checkout", status_code=status.HTTP_201_CREATED)
async def start_checkout(
    body: CheckoutRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckoutStarted:
    return await checkout.start(db, user, body, request.headers.get("origin"))


@router.get("/orders/{public_id}")
async def get_order(
    public_id: PublicId,
    refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderView:
    """With ?refresh=true the order is checked with Razorpay first, when no other check of it runs and the caller's
    refreshes allow it (reconcile.may_refresh)."""
    order = await _own_order(db, user, public_id)
    if (
        refresh
        and order.status in reconcile.WATCHED
        and order.mode == settings.razorpay_mode
        and await reconcile.may_refresh(user.id, order.id)
    ):
        order_id = order.id
        await db.commit()
        await reconcile.check_order(db, order_id, source="user")
        order = await _own_order(db, user, public_id)
        await db.refresh(order)
    return await checkout.order_view(db, order)


@router.post("/orders/{public_id}/cancel")
async def cancel_order(
    public_id: PublicId, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> OrderView:
    return await checkout.cancel(db, user, await _own_order(db, user, public_id))


@router.get("/payments")
async def list_payments(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[PaymentView]:
    return await payments.user_payments(db, user.id)


@router.get(
    "/receipts/{receipt_id}",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The receipt as a PDF"}},
)
async def download_receipt(
    receipt_id: Annotated[int, Path(ge=1, le=MAX_INT)],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    receipt = await db.get(BillingReceipt, receipt_id)
    if receipt is None or receipt.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return await receipts.pdf_response(db, receipt)


@router.get("/profile")
async def get_profile(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> BillingProfile:
    return await summary.profile(db, user.id)


@router.put("/profile")
async def save_profile(
    body: BillingProfile, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> BillingProfile:
    row = await db.get(UserBillingProfile, user.id)
    if row is None:
        row = UserBillingProfile(user_id=user.id)
        db.add(row)
    row.name, row.email, row.updated_at = body.name, body.email, utcnow()
    await db.commit()
    return await summary.profile(db, user.id)


@router.get("/razorpay/return", include_in_schema=False)
async def razorpay_return(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    """Razorpay sends the browser back here after a payment attempt: always a 303, never an error page."""
    try:
        target = await webhooks.returned(db, request.query_params)
    except Exception as exc:  # the reconciler confirms the payment anyway
        logger.warning("A return from Razorpay couldn't be handled (%s)", type(exc).__name__)
        target = f"{business.origins()[0]}/plans?payment=unknown"
    return RedirectResponse(
        target,
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post(
    "/razorpay/webhook",
    responses={
        200: {"model": WebhookAck},
        400: {"description": "Bad signature"},
        413: {"description": "Over 256 KB"},
        503: {"description": "Webhooks aren't set up"},
    },
)
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > webhooks.MAX_BODY:
        return JSONResponse({"detail": "Too large"}, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    return await webhooks.receive(
        db,
        await request.body(),
        request.headers.get("x-razorpay-signature", ""),
        request.headers.get("x-razorpay-event-id"),
    )
