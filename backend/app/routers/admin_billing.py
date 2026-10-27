"""The admin's plans and billing (SPEC.md section 6, "Admin": plans and billing; docs/briefs/plans-billing.md 7.2):
the plan editor, the billing settings and setup, the month's summary and CSV, orders, payments, refunds, bank
transfers, a user's plan (grants, complimentary plans, changing the plan, revoking a period) and receipts. Every write
takes the user's row lock where a user is involved and writes a billing event with the admin as actor."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_admin
from app.models import BillingEvent, BillingOrder, BillingPayment, BillingReceipt, Plan, PlanPeriod, User, utcnow
from app.schemas import MAX_INT
from app.schemas_billing import (
    AdminOrder,
    AdminOrderDetail,
    AdminOrderPage,
    AdminPayment,
    AdminPaymentPage,
    AdminPlan,
    BillingAdminConfig,
    BillingSettings,
    BillingSummary,
    ChangePlanRequest,
    EndComplimentaryRequest,
    GrantRequest,
    OfflinePaymentRequest,
    OrderEvent,
    OrderStatus,
    PaymentStatus,
    PlanCreate,
    PlanSaved,
    PlanUpdate,
    ReconcileResult,
    RefundRequest,
    ResolveRequest,
    RevokeRequest,
    UserBilling,
)
from app.services.billing import (
    audit,
    business,
    checkout,
    csv_export,
    money,
    payments,
    periods,
    plans,
    receipts,
    reconcile,
    refunds,
    summary,
)
from app.services.billing.locks import lock_user

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])

Id = Annotated[int, Path(ge=1, le=MAX_INT)]
PublicId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,24}$")]
Month = Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")]
Page = Annotated[int, Query(ge=1, le=100_000)]
PAGE_SIZE = 50


# ---------------- Plans ----------------


async def _plan(db: AsyncSession, plan_id: int) -> Plan:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return plan


@router.get("/plans")
async def list_plans(db: AsyncSession = Depends(get_db)) -> list[AdminPlan]:
    return await plans.admin_plans(db)


@router.post("/plans", status_code=status.HTTP_201_CREATED)
async def create_plan(
    body: PlanCreate, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> PlanSaved:
    return await plans.create_plan(db, body, actor=admin.username)


@router.patch("/plans/{plan_id}")
async def update_plan(
    plan_id: Id, body: PlanUpdate, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> PlanSaved:
    return await plans.update_plan(db, await _plan(db, plan_id), body, actor=admin.username)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: Id, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> None:
    await plans.delete_plan(db, await _plan(db, plan_id), actor=admin.username)


# ---------------- Settings, summary and export ----------------


async def _config(db: AsyncSession) -> BillingAdminConfig:
    return BillingAdminConfig(
        settings=await business.get_settings(db),
        payments=await business.payments_setup(db),
        estimate=money.cost_inputs(),
    )


@router.get("/billing/config")
async def get_config(db: AsyncSession = Depends(get_db)) -> BillingAdminConfig:
    return await _config(db)


@router.put("/billing/settings")
async def save_settings(
    body: BillingSettings, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> BillingAdminConfig:
    await business.save_settings(db, body, actor=admin.username)
    await db.commit()
    return await _config(db)


@router.get("/billing/summary")
async def get_summary(month: Month = None, db: AsyncSession = Depends(get_db)) -> BillingSummary:
    return await summary.summary(db, month)


@router.get(
    "/billing/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "The month's payments, refunds and lost disputes"}},
)
async def export_csv(month: Month = None, db: AsyncSession = Depends(get_db)) -> Response:
    filename, text = await csv_export.export(db, month)
    return Response(
        content=text.encode(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"},
    )


# ---------------- Orders ----------------


async def _order(db: AsyncSession, public_id: str) -> BillingOrder:
    order = await db.scalar(select(BillingOrder).where(BillingOrder.public_id == public_id))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


@router.get("/billing/orders")
async def list_orders(
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    user_id: Annotated[int | None, Query(ge=1, le=MAX_INT)] = None,
    page: Page = 1,
    db: AsyncSession = Depends(get_db),
) -> AdminOrderPage:
    conditions = [
        condition
        for condition in (
            BillingOrder.status == status_filter if status_filter else None,
            BillingOrder.reviewed_at.is_(None) if status_filter == "review" else None,  # still to review
            BillingOrder.user_id == user_id if user_id else None,
        )
        if condition is not None
    ]
    total = await db.scalar(select(func.count()).select_from(BillingOrder).where(*conditions))
    rows = await db.scalars(
        select(BillingOrder)
        .where(*conditions)
        .order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return AdminOrderPage(items=[await checkout.admin_order(db, order) for order in rows], total=total or 0)


@router.get("/billing/orders/{public_id}")
async def get_order(public_id: PublicId, db: AsyncSession = Depends(get_db)) -> AdminOrderDetail:
    order = await _order(db, public_id)
    events = await db.scalars(select(BillingEvent).where(BillingEvent.order_id == order.id).order_by(BillingEvent.id))
    return AdminOrderDetail(
        **(await checkout.admin_order(db, order)).model_dump(),
        events=[
            OrderEvent(
                at=event.created_at, source=event.source, type=event.type, detail=event.detail, error=event.error
            )
            for event in events
        ],
    )


@router.post("/billing/orders/{public_id}/check")
async def check_order(public_id: PublicId, db: AsyncSession = Depends(get_db)) -> AdminOrder:
    """Checks the order with Razorpay now (unless another check of it runs or it is paused)."""
    order = await _order(db, public_id)
    order_id = order.id
    await db.commit()
    await reconcile.check_order(db, order_id, source="admin")
    order = await _order(db, public_id)
    await db.refresh(order)
    return await checkout.admin_order(db, order)


@router.post("/billing/orders/{public_id}/resolve")
async def resolve_order(
    public_id: PublicId,
    body: ResolveRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOrder:
    order = await _order(db, public_id)
    if order.status != "review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an order under review can be resolved.")
    if order.user_id is not None:
        await lock_user(db, order.user_id)
    order.reviewed_at, order.reviewed_by, order.review_note = utcnow(), admin.username, body.note
    await audit.record_event(
        db,
        source="admin",
        type="order.resolved",
        actor=admin.username,
        user_id=order.user_id,
        order_id=order.id,
        detail={"note": body.note, "reason": order.review_reason},
    )
    await db.commit()
    return await checkout.admin_order(db, order)


@router.post("/billing/reconcile")
async def reconcile_now(admin: User = Depends(get_current_admin)) -> ReconcileResult:
    """Checks every due order of the current mode now; also tries again after Razorpay rejected the keys."""
    await business.clear_keys_rejected()
    return ReconcileResult(checked=await reconcile.run_due(limit=None, source="admin"))


# ---------------- Payments ----------------


async def _payment(db: AsyncSession, payment_id: int) -> BillingPayment:
    payment = await db.get(BillingPayment, payment_id, populate_existing=True)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return payment


@router.get("/billing/payments")
async def list_payments(
    month: Month = None,
    user_id: Annotated[int | None, Query(ge=1, le=MAX_INT)] = None,
    status_filter: Annotated[PaymentStatus | None, Query(alias="status")] = None,
    page: Page = 1,
    db: AsyncSession = Depends(get_db),
) -> AdminPaymentPage:
    conditions: list[ColumnElement[bool]] = [
        BillingPayment.status.in_((status_filter,) if status_filter else payments.LISTED_STATUSES)
    ]
    if user_id:
        conditions.append(BillingPayment.user_id == user_id)
    if month:
        _, start, end = summary.month_range(month)
        conditions += [BillingPayment.captured_at >= start, BillingPayment.captured_at < end]
    total = await db.scalar(select(func.count()).select_from(BillingPayment).where(*conditions))
    rows = await db.scalars(
        select(BillingPayment)
        .where(*conditions)
        .order_by(BillingPayment.captured_at.desc(), BillingPayment.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return AdminPaymentPage(items=[await payments.admin_payment(db, payment) for payment in rows], total=total or 0)


@router.get("/billing/payments/{payment_id}")
async def get_payment(payment_id: Id, db: AsyncSession = Depends(get_db)) -> AdminPayment:
    return await payments.admin_payment(db, await _payment(db, payment_id))


@router.post("/billing/payments/{payment_id}/refund")
async def refund_payment(
    payment_id: Id, body: RefundRequest, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> AdminPayment:
    await refunds.start(db, admin, payment_id, body)
    return await payments.admin_payment(db, await _payment(db, payment_id))


@router.post("/billing/offline-payments", status_code=status.HTTP_201_CREATED)
async def record_offline_payment(
    body: OfflinePaymentRequest, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> AdminPayment:
    payment = await payments.record_offline(db, admin, body)
    return await payments.admin_payment(db, await _payment(db, payment.id))


@router.get(
    "/billing/receipts/{receipt_id}",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The receipt as a PDF"}},
)
async def download_receipt(receipt_id: Id, db: AsyncSession = Depends(get_db)) -> Response:
    receipt = await db.get(BillingReceipt, receipt_id)
    if receipt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return await receipts.pdf_response(db, receipt)


# ---------------- A user's plan ----------------


async def _user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id, populate_existing=True)
    if user is None or user.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/billing/users/{user_id}")
async def user_billing(user_id: Id, db: AsyncSession = Depends(get_db)) -> UserBilling:
    return await summary.user_billing(db, await _user(db, user_id))


@router.post("/billing/users/{user_id}/grants")
async def grant(
    user_id: Id, body: GrantRequest, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> UserBilling:
    user = await _user(db, user_id)
    plan = await _plan(db, body.plan_id)
    await periods.grant(db, user, plan, days=body.days, reason=body.reason, actor=admin.username, start=body.start)
    await db.commit()
    return await summary.user_billing(db, await _user(db, user_id))


@router.post("/billing/users/{user_id}/end-complimentary")
async def end_complimentary(
    user_id: Id,
    body: EndComplimentaryRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> UserBilling:
    await periods.end_complimentary(db, await _user(db, user_id), reason=body.reason, actor=admin.username)
    await db.commit()
    return await summary.user_billing(db, await _user(db, user_id))


@router.post("/billing/users/{user_id}/change-plan")
async def change_plan(
    user_id: Id, body: ChangePlanRequest, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> UserBilling:
    user = await _user(db, user_id)
    plan = await _plan(db, body.plan_id)
    await periods.change_plan(db, user, plan, reason=body.reason, actor=admin.username)
    await db.commit()
    return await summary.user_billing(db, await _user(db, user_id))


@router.post("/billing/periods/{period_id}/revoke")
async def revoke_period(
    period_id: Id, body: RevokeRequest, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> UserBilling:
    period = await db.get(PlanPeriod, period_id)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")
    user_id = period.user_id
    await periods.revoke(db, period, reason=body.reason, actor=admin.username, source="admin")
    await db.commit()
    return await summary.user_billing(db, await _user(db, user_id))
