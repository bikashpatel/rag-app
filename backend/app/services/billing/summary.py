"""What the admin and the user see of billing as a whole (docs/briefs/plans-billing.md 7.1, 8.5 and 8.11): the month's
summary with "Needs attention", the expiring and lapsed lists, a user's billing page and status. Months are IST
months; test payments count only while test keys run."""

import re
from datetime import datetime, timedelta
from typing import Final

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import (
    BillingDispute,
    BillingEvent,
    BillingOrder,
    BillingPayment,
    BillingRefund,
    Plan,
    PlanPeriod,
    User,
    UserBillingProfile,
    utcnow,
)
from app.schemas_billing import (
    AttentionItem,
    BillingEventView,
    BillingProfile,
    BillingStatus,
    BillingSummary,
    ComplimentaryView,
    MonthTotal,
    PeriodView,
    PlanRevenue,
    PlanSnapshot,
    PlanUsers,
    UserBilling,
    UserPlanRow,
    UserRef,
)
from app.services.billing import business, checkout, entitlements, payments, periods, plans
from app.services.billing.disputes import NEEDS_ACTION
from app.services.billing.money import IST, format_inr, format_ist_date, ist_date
from app.services.billing.webhooks import MAX_ATTEMPTS

EXPIRING_DAYS: Final = 7
LAPSED_DAYS: Final = 30
STALE_REFUND: Final = timedelta(days=7)
RECENT: Final = timedelta(days=30)
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def month_range(month: str | None, now: datetime | None = None) -> tuple[str, datetime, datetime]:
    """An IST month "YYYY-MM" (default: the current one) and its start and end as instants."""
    if month is None:
        today = ist_date(now or utcnow())
        year, number = today.year, today.month
    else:
        match = _MONTH.fullmatch(month)
        if match is None or not 1 <= int(match[2]) <= 12 or not 2000 <= int(match[1]) <= 2999:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The month must look like 2026-10.")
        year, number = int(match[1]), int(match[2])
    start = datetime(year, number, 1, tzinfo=IST)
    end = datetime(year + number // 12, number % 12 + 1, 1, tzinfo=IST)
    return f"{year:04d}-{number:02d}", start, end


def summary_modes() -> tuple[str, ...]:
    """Money that counts: live and offline payments, and test payments while test keys run."""
    return ("live", "offline", "test") if settings.razorpay_mode == "test" else ("live", "offline")


def _received(start: datetime, end: datetime) -> tuple[ColumnElement[bool], ...]:
    """Payments received in [start, end): money that came in, refunded or not."""
    return (
        BillingPayment.captured_at >= start,
        BillingPayment.captured_at < end,
        BillingPayment.status.in_(payments.LISTED_STATUSES),
        BillingPayment.mode.in_(summary_modes()),
    )


async def _totals(db: AsyncSession, start: datetime, end: datetime) -> tuple[int, int, int, int, int, int]:
    """(gross, fees, refunded, disputed, payments, refunds) of the instants [start, end)."""
    gross, fees, count = (
        await db.execute(
            select(
                func.coalesce(func.sum(BillingPayment.amount_paise), 0),
                func.coalesce(func.sum(BillingPayment.fee_paise), 0),
                func.count(BillingPayment.id),
            ).where(*_received(start, end))
        )
    ).one()
    refunded, refunds = (
        await db.execute(
            select(func.coalesce(func.sum(BillingRefund.amount_paise), 0), func.count(BillingRefund.id))
            .join(BillingPayment, BillingPayment.id == BillingRefund.payment_id)
            .where(
                BillingRefund.status == "processed",
                BillingRefund.processed_at >= start,
                BillingRefund.processed_at < end,
                BillingPayment.mode.in_(summary_modes()),
            )
        )
    ).one()
    disputed = await db.scalar(
        select(func.coalesce(func.sum(BillingDispute.amount_paise), 0))
        .join(BillingPayment, BillingPayment.id == BillingDispute.payment_id)
        .where(
            BillingDispute.status == "lost",
            BillingDispute.access_ended_at >= start,
            BillingDispute.access_ended_at < end,
            BillingPayment.mode.in_(summary_modes()),
        )
    )
    return int(gross), int(fees), int(refunded), int(disputed or 0), int(count), int(refunds)


async def _by_plan(db: AsyncSession, start: datetime, end: datetime) -> list[PlanRevenue]:
    rows = (
        await db.execute(
            select(BillingOrder.plan_snapshot, BillingPayment.amount_paise)
            .join(BillingOrder, BillingOrder.id == BillingPayment.order_id)
            .where(*_received(start, end))
        )
    ).tuples()
    totals: dict[str, list[int]] = {}
    for snapshot, amount in rows:
        entry = totals.setdefault(PlanSnapshot.model_validate(snapshot).name, [0, 0])
        entry[0] += 1
        entry[1] += amount
    return [
        PlanRevenue(plan=name, payments=count, gross_paise=gross) for name, (count, gross) in sorted(totals.items())
    ]


def _previous(label: str, months: int) -> str:
    year, number = map(int, label.split("-"))
    index = year * 12 + number - 1 - months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


async def _last_payments(db: AsyncSession) -> dict[int, datetime]:
    return {
        user_id: moment
        for user_id, moment in (
            await db.execute(
                select(BillingPayment.user_id, func.max(BillingPayment.captured_at))
                .where(BillingPayment.user_id.is_not(None), BillingPayment.status.in_(payments.LISTED_STATUSES))
                .group_by(BillingPayment.user_id)
            )
        ).tuples()
        if user_id is not None and moment is not None
    }


async def summary(db: AsyncSession, month: str | None) -> BillingSummary:
    label, start, end = month_range(month)
    gross, fees, refunded, disputed, count, refunds = await _totals(db, start, end)
    months = []
    for back in range(11, -1, -1):
        other, other_start, other_end = month_range(_previous(label, back))
        totals = await _totals(db, other_start, other_end)
        months.append(
            MonthTotal(month=other, gross_paise=totals[0], net_paise=totals[0] - totals[1] - totals[2] - totals[3])
        )
    now = utcnow()
    users = list(await db.scalars(select(User).where(User.is_admin.is_(False), User.is_active.is_(True))))
    standing = await entitlements.load_many(db, users, now=now)
    last_paid = await _last_payments(db)
    current: dict[str, list[int]] = {}
    expiring: list[UserPlanRow] = []
    lapsed: list[UserPlanRow] = []
    for user in users:
        ent = standing[user.id]
        if ent.access:
            entry = current.setdefault(ent.plan_name or "", [0, 0])
            entry[1 if ent.complimentary else 0] += 1
        ref = UserRef(id=user.id, username=user.username)
        if (
            ent.access
            and not ent.open_ended
            and user.comp_plan_id is None
            and ent.paid_through is not None
            and ent.valid_through is not None
            and ent.paid_through - now <= timedelta(days=EXPIRING_DAYS)
        ):
            expiring.append(
                UserPlanRow(
                    user=ref, plan=ent.plan_name, date=ent.valid_through, last_payment_at=last_paid.get(user.id)
                )
            )
        if (
            ent.state == "expired"
            and ent.paid_through is not None
            and ent.valid_through is not None
            and now - ent.paid_through <= timedelta(days=LAPSED_DAYS)
        ):
            lapsed.append(
                UserPlanRow(
                    user=ref, plan=ent.plan_name, date=ent.valid_through, last_payment_at=last_paid.get(user.id)
                )
            )
    return BillingSummary(
        month=label,
        gross_paise=gross,
        fee_paise=fees,
        refunded_paise=refunded,
        disputed_paise=disputed,
        net_paise=gross - fees - refunded - disputed,
        payments=count,
        refunds=refunds,
        by_plan=await _by_plan(db, start, end),
        current_users=[
            PlanUsers(plan=name, paid=paid, complimentary=comps) for name, (paid, comps) in sorted(current.items())
        ],
        users_without_plan=sum(1 for user in users if not standing[user.id].access),
        months=months,
        expiring=sorted(expiring, key=lambda row: row.date),
        lapsed=sorted(lapsed, key=lambda row: row.date, reverse=True),
        attention=await attention(db),
    )


# ---------------- Needs attention ----------------


def _item(kind: str, message: str, **fields: object) -> AttentionItem:
    return AttentionItem.model_validate(
        {"kind": kind, "message": message, "detail": None, "order_public_id": None, "payment_id": None, "count": None}
        | fields
    )


async def _setup_items(db: AsyncSession) -> list[AttentionItem]:
    items: list[AttentionItem] = []
    mode = settings.razorpay_mode
    rejected = await business.keys_rejected()
    if rejected:
        items.append(
            _item("keys_rejected", "Razorpay rejected the API keys: payment checks are paused.", detail=rejected)
        )
    if mode is None:
        items.append(
            _item(
                "payments_not_configured",
                "Online payment isn't set up yet.",
                detail="Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env, then run docker compose up -d backend "
                "worker.",
            )
        )
    setup = await business.payments_setup(db)
    if mode == "test":
        items.append(
            _item(
                "test_mode",
                "Test mode: no real money moves, and only testers can pay.",
                detail=f"{setup.test_links_created} of about 30 test payment links used.",
                count=setup.test_links_created,
            )
        )
        if business.public_https_origin():
            items.append(
                _item(
                    "test_keys_public",
                    "Test keys run on a public address.",
                    detail="Switch to live keys before going live; until then only testers can pay.",
                )
            )
    if mode == "live" and not settings.razorpay_webhook_secret:
        items.append(
            _item(
                "live_without_webhook",
                "Live keys run without a webhook secret.",
                detail="Set RAZORPAY_WEBHOOK_SECRET and register the webhook in Razorpay's Dashboard.",
            )
        )
    if setup.paused_orders:
        other = "live" if mode == "test" else "test"
        items.append(
            _item(
                "paused_orders",
                f"{setup.paused_orders} {other} order{'' if setup.paused_orders == 1 else 's'} can't be checked with "
                f"{mode or 'no'} keys.",
                count=setup.paused_orders,
            )
        )
    return items


async def _duplicates(db: AsyncSession, now: datetime) -> list[AttentionItem]:
    """Two applied payments by one user within 24 hours from checkouts that were open at the same time (the second
    started before the first was paid; both stay applied). A renewal or an upgrade bought after a payment isn't one."""
    rows = list(
        (
            await db.execute(
                select(BillingPayment, BillingOrder.created_at)
                .join(BillingOrder, BillingOrder.id == BillingPayment.order_id)
                .where(
                    BillingPayment.applied_at.is_not(None),
                    BillingPayment.applied_at >= now - RECENT,
                    BillingPayment.user_id.is_not(None),
                    BillingPayment.mode.in_(summary_modes()),
                )
                .order_by(BillingPayment.user_id, BillingPayment.applied_at)
            )
        ).tuples()
    )
    items = []
    for (first, _), (second, opened) in zip(rows, rows[1:], strict=False):
        assert first.applied_at is not None and second.applied_at is not None
        if (
            first.user_id == second.user_id
            and first.order_id != second.order_id
            and second.applied_at - first.applied_at <= timedelta(hours=24)
            and opened < first.applied_at
        ):
            items.append(
                _item(
                    "duplicate",
                    f"Possible duplicate payment by {second.username}",
                    detail=f"{format_inr(first.amount_paise)} and {format_inr(second.amount_paise)} within 24 hours "
                    "from checkouts open at the same time; both added a period.",
                    payment_id=second.id,
                )
            )
    return items


async def _reversed(db: AsyncSession, now: datetime) -> list[AttentionItem]:
    """Lost disputes of the last 30 days, and payments refunded or charged back in that time whose credit still runs
    in an upgrade (4.10): the upgrade stays until the admin revokes it or changes the plan."""
    items: list[AttentionItem] = []
    lost = (
        await db.execute(
            select(BillingDispute, BillingPayment)
            .join(BillingPayment, BillingPayment.id == BillingDispute.payment_id)
            .where(BillingDispute.status == "lost", BillingDispute.access_ended_at >= now - RECENT)
            .order_by(BillingDispute.id)
        )
    ).tuples()
    for dispute, payment in lost:
        items.append(
            _item(
                "dispute_lost",
                f"Dispute of {format_inr(dispute.amount_paise)} by {payment.username} was lost",
                detail="What the payment bought has ended; the amount counts as disputed in the month's summary.",
                payment_id=payment.id,
            )
        )
    refunded = select(BillingRefund.payment_id).where(
        BillingRefund.status == "processed", BillingRefund.processed_at >= now - RECENT
    )
    disputed = select(BillingDispute.payment_id).where(
        BillingDispute.status == "lost", BillingDispute.access_ended_at >= now - RECENT
    )
    for payment in await db.scalars(
        select(BillingPayment)
        .where(BillingPayment.id.in_(refunded.union(disputed).scalar_subquery()))
        .order_by(BillingPayment.id)
    ):
        unbacked = await payments.unbacked_credit(db, payment)
        period = await payments.bought_period(db, payment)
        if not unbacked or period is None:
            continue
        upgrade = await db.scalar(
            select(BillingOrder)
            .where(
                BillingOrder.kind == "now",
                BillingOrder.status == "paid",
                BillingOrder.replaces_period_id == period.id,
            )
            .order_by(BillingOrder.paid_at.desc(), BillingOrder.id.desc())
            .limit(1)
        )
        if upgrade is None or upgrade.paid_at is None:
            continue
        how = "disputed" if payment.status == "disputed" else "refunded"
        items.append(
            _item(
                "credit_reversed",
                f"{format_inr(unbacked)} of a {how} payment by {payment.username} went as credit to the upgrade on "
                f"{format_ist_date(ist_date(upgrade.paid_at))}",
                detail="The upgrade's period still runs. Revoke it or change the plan in Admin → Users if it should "
                "end.",
                order_public_id=upgrade.public_id,
                payment_id=upgrade.payment_id,
            )
        )
    return items


async def attention(db: AsyncSession) -> list[AttentionItem]:
    now = utcnow()
    items = await _setup_items(db)
    for order in await db.scalars(
        select(BillingOrder)
        .where(BillingOrder.status == "review", BillingOrder.reviewed_at.is_(None))
        .order_by(BillingOrder.id)
    ):
        plan = PlanSnapshot.model_validate(order.plan_snapshot).name
        items.append(
            _item(
                "review",
                f"Payment needs review: {order.review_reason or 'no reason given'}",
                detail=f"{order.username} · {plan} · {format_inr(order.amount_paise)}",
                order_public_id=order.public_id,
                payment_id=order.payment_id,
            )
        )
    items += await _duplicates(db, now)
    items += await _reversed(db, now)
    refunds = (
        await db.execute(
            select(BillingRefund, BillingPayment)
            .join(BillingPayment, BillingPayment.id == BillingRefund.payment_id)
            .order_by(BillingRefund.id)
        )
    ).tuples()
    for refund, payment in refunds:
        amount = format_inr(refund.amount_paise)
        if refund.status == "failed" and now - refund.updated_at <= RECENT:
            message = f"Refund of {amount} to {payment.username} failed"
            items.append(_item("refund_failed", message, detail=refund.last_error, payment_id=payment.id))
        elif refund.status in ("pending", "creating") and now - refund.created_at > STALE_REFUND:
            message = f"Refund of {amount} to {payment.username} is still {refund.status} after 7 days"
            items.append(_item("refund_pending", message, detail=refund.last_error, payment_id=payment.id))
        elif (
            refund.status == "processed"
            and refund.initiated_by == "razorpay"
            and payment.status == "partially_refunded"
            and refund.processed_at is not None
            and now - refund.processed_at <= RECENT
        ):
            items.append(
                _item(
                    "partial_refund",
                    f"Partial refund of {amount} to {payment.username} made in Razorpay's Dashboard",
                    detail="Access didn't change. Revoke the period in Admin → Users if it should end.",
                    payment_id=payment.id,
                )
            )
    disputes = (
        await db.execute(
            select(BillingDispute, BillingPayment)
            .join(BillingPayment, BillingPayment.id == BillingDispute.payment_id)
            .where(BillingDispute.status.in_(NEEDS_ACTION))
            .order_by(BillingDispute.respond_by, BillingDispute.id)
        )
    ).tuples()
    for dispute, payment in disputes:
        deadline = f" by {format_ist_date(ist_date(dispute.respond_by))}" if dispute.respond_by else ""
        items.append(
            _item(
                "dispute",
                f"Dispute of {format_inr(dispute.amount_paise)} by {payment.username}: respond in Razorpay's "
                f"Dashboard{deadline}",
                detail=dispute.reason_code,
                payment_id=payment.id,
            )
        )
    failed = await db.scalar(
        select(func.count())
        .select_from(BillingEvent)
        .where(
            BillingEvent.source == "webhook",
            BillingEvent.processed.is_(False),
            BillingEvent.attempts >= MAX_ATTEMPTS,
        )
    )
    if failed:
        items.append(
            _item(
                "webhook_errors",
                f"{failed} webhook event{'' if failed == 1 else 's'} failed 3 times",
                detail="The reconciler still checks their orders.",
                count=failed,
            )
        )
    return items


# ---------------- A user's billing ----------------


def period_view(period: PlanPeriod, now: datetime) -> PeriodView:
    snapshot = periods.snapshot(period)
    return PeriodView(
        id=period.id,
        plan=plans.snapshot_ref(snapshot) if period.plan_id is not None else None,
        plan_name=snapshot.name,
        starts_at=period.starts_at,
        ends_at=period.ends_at,
        valid_through=periods.valid_through(period.ends_at),
        source="comp" if period.source == "comp" else "payment",
        current=period.revoked_at is None and period.starts_at <= now < period.ends_at,
        test_mode=period.mode == "test",
        reason=period.reason if period.source == "comp" else None,
    )


async def _complimentary(db: AsyncSession, user: User) -> ComplimentaryView | None:
    if user.comp_plan_id is None:
        return None
    plan = await db.get(Plan, user.comp_plan_id)
    if plan is None:
        return None
    return ComplimentaryView(
        plan=plans.ref(plan),
        reason=user.comp_reason or "",
        granted_by=user.comp_granted_by or "",
        granted_at=user.comp_granted_at or user.updated_at,
    )


async def profile(db: AsyncSession, user_id: int) -> BillingProfile:
    row = await db.get(UserBillingProfile, user_id)
    return BillingProfile(name=row.name if row else None, email=row.email if row else None)


async def billing_status(db: AsyncSession, user: User) -> BillingStatus:
    """GET /billing/status."""
    now = utcnow()
    ent = await entitlements.load(db, user, now=now)
    rows = await periods.user_periods(db, user.id)
    open_order = await db.scalar(
        select(BillingOrder)
        .where(BillingOrder.user_id == user.id, BillingOrder.status == "pending")
        .order_by(BillingOrder.created_at.desc(), BillingOrder.id.desc())
        .limit(1)
    )
    return BillingStatus(
        plan=await entitlements.plan_access(db, user, ent),
        periods=[period_view(row, now) for row in rows if row.revoked_at is None and row.ends_at > now],
        complimentary=await _complimentary(db, user),
        open_order=await checkout.order_view(db, open_order) if open_order else None,
        profile=await profile(db, user.id),
        payments=await business.payments_info(db, user),
    )


async def user_billing(db: AsyncSession, user: User) -> UserBilling:
    """GET /admin/billing/users/{id}."""
    now = utcnow()
    ent = await entitlements.load(db, user, now=now)
    rows = await db.scalars(
        select(PlanPeriod)
        .where(PlanPeriod.user_id == user.id)
        .order_by(PlanPeriod.starts_at.desc(), PlanPeriod.id.desc())
        .limit(100)
    )
    orders = await db.scalars(
        select(BillingOrder).where(BillingOrder.user_id == user.id).order_by(BillingOrder.id.desc()).limit(50)
    )
    paid = await db.scalars(
        select(BillingPayment)
        .where(BillingPayment.user_id == user.id, BillingPayment.status != "failed")
        .order_by(BillingPayment.id.desc())
        .limit(50)
    )
    events = await db.scalars(
        select(BillingEvent).where(BillingEvent.user_id == user.id).order_by(BillingEvent.id.desc()).limit(50)
    )
    return UserBilling(
        plan=await entitlements.plan_access(db, user, ent),
        periods=[period_view(row, now) for row in rows],
        complimentary=await _complimentary(db, user),
        orders=[await checkout.admin_order(db, order) for order in orders],
        payments=[await payments.admin_payment(db, payment) for payment in paid],
        profile=await profile(db, user.id),
        events=[
            BillingEventView(
                at=event.created_at, source=event.source, type=event.type, actor=event.actor, detail=event.detail or {}
            )
            for event in events
        ],
    )


# ---------------- The monitor's billing check (11.3) ----------------

DEADLINE_SOON: Final = timedelta(days=3)
STALLED: Final = timedelta(minutes=15)
BAD_SIGNATURES: Final = 20  # in the last hour, webhooks and returns together
WEBHOOK_GRACE: Final = timedelta(minutes=10)  # a capture a webhook hasn't reported after this long


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


async def problems(now: datetime | None = None) -> list[str]:
    """What the `billing` check warns about, as counts (never usernames or amounts); empty when all is well."""
    now = now or utcnow()
    mode = settings.razorpay_mode
    found: list[str] = []
    async with SessionLocal() as db:
        reviews = await db.scalar(
            select(func.count())
            .select_from(BillingOrder)
            .where(BillingOrder.status == "review", BillingOrder.reviewed_at.is_(None))
        )
        if reviews:
            found.append(_plural(reviews, "payment to review", "payments to review"))
        disputes = await db.scalar(
            select(func.count())
            .select_from(BillingDispute)
            .where(BillingDispute.status.in_(NEEDS_ACTION), BillingDispute.respond_by <= now + DEADLINE_SOON)
        )
        if disputes:
            found.append(_plural(disputes, "dispute to answer within 3 days", "disputes to answer within 3 days"))
        stalled = await db.scalar(
            select(func.count())
            .select_from(BillingOrder)
            .where(
                BillingOrder.mode == mode,
                BillingOrder.status.in_(("pending", "expired", "cancelled")),
                BillingOrder.next_check_at < now - STALLED,
            )
        )
        if stalled and not await business.keys_rejected():
            found.append(f"the payment checks are stalled ({_plural(stalled, 'order', 'orders')} overdue)")
        if await business.keys_rejected():
            found.append("Razorpay rejected the API keys")
        refunds = await db.scalar(
            select(func.count())
            .select_from(BillingRefund)
            .where(
                (BillingRefund.status.in_(("pending", "creating")) & (BillingRefund.created_at < now - STALE_REFUND))
                | ((BillingRefund.status == "failed") & (BillingRefund.updated_at >= now - STALE_REFUND))
            )
        )
        if refunds:
            found.append(_plural(refunds, "refund stuck or failed", "refunds stuck or failed"))
        failed = await db.scalar(
            select(func.count())
            .select_from(BillingEvent)
            .where(
                BillingEvent.source == "webhook",
                BillingEvent.processed.is_(False),
                BillingEvent.attempts >= MAX_ATTEMPTS,
            )
        )
        if failed:
            found.append(_plural(failed, "webhook event failed 3 times", "webhook events failed 3 times"))
        if settings.razorpay_webhook_secret:
            captured = set(
                await db.scalars(
                    select(BillingPayment.razorpay_payment_id).where(
                        BillingPayment.razorpay_payment_id.is_not(None),
                        BillingPayment.captured_at >= now - timedelta(hours=24),
                        BillingPayment.captured_at < now - WEBHOOK_GRACE,
                        BillingPayment.mode == mode,
                    )
                )
            )
            if captured:
                reported = {
                    (event.detail or {}).get("payment")
                    for event in await db.scalars(
                        select(BillingEvent).where(
                            BillingEvent.source == "webhook", BillingEvent.created_at >= now - timedelta(hours=25)
                        )
                    )
                }
                silent = len(captured - reported)
                if silent:
                    found.append(
                        f"no webhook reported {_plural(silent, 'payment', 'payments')} of the last 24 hours (Razorpay "
                        "may have disabled the webhook)"
                    )
    signatures = await business.counted("billing:wh:badsig") + await business.counted("billing:cb:badsig")
    if signatures >= BAD_SIGNATURES:
        found.append(f"{signatures} bad webhook or return signatures in the last hour")
    if mode == "live" and not settings.razorpay_webhook_secret:
        found.append("live keys run without RAZORPAY_WEBHOOK_SECRET")
    if mode == "test" and business.public_https_origin():
        found.append("test keys run on a public https address")
    return [problem[0].upper() + problem[1:] for problem in found]
