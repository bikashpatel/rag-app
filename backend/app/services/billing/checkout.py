"""What a user can buy and starting a checkout (docs/briefs/plans-billing.md 4.3 to 4.5 and 6.3): the options per
plan with their dates, amounts, credit, changes and reasons; the caps; the Payment Link; cancelling a checkout; and
the views of an order."""

import asyncio
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any, Final, Literal

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models import (
    BillingOrder,
    BillingPayment,
    BillingReceipt,
    Plan,
    PlanPeriod,
    PluginConnection,
    User,
    utcnow,
)
from app.plugins import registry
from app.redis_client import get_redis
from app.schemas_billing import (
    AdminOrder,
    CheckoutOption,
    CheckoutRequest,
    CheckoutStarted,
    CreditLine,
    OrderView,
    PlanCatalog,
    PlanFeatures,
    PlanOffer,
    ReceiptRef,
    UserRef,
)
from app.services import storage
from app.services.billing import audit, business, entitlements, payments, periods, plans, razorpay
from app.services.billing.entitlements import Entitlements
from app.services.billing.locks import lock_user
from app.services.billing.money import (
    IST,
    PERIOD_DAYS,
    format_inr,
    format_ist_date,
    format_ist_range,
    ist_date,
    now_charge,
)

logger = logging.getLogger(__name__)

HOUR_CAP: Final = 5  # payment links per user in any 60 minutes
DAY_CAP: Final = 15  # and per IST day
REUSE_MINUTES: Final = 10  # an open checkout with this long left is returned instead of a new one
FIRST_CHECK_SECONDS: Final = 30
PROVIDER_ERROR: Final = "The payment service didn't answer. Try again in a minute."
_RETIRED = "This plan is no longer offered."
_PAID_AHEAD = "You already have a period paid ahead."
_NO_PAYMENTS = "Online payment isn't set up yet."
_NOT_HIGHER = "You're already on this plan or a higher one."
_SUPERSEDED = "Another checkout was started at the same time. Open the plans page to see the one to pay."
_MIB = 1024 * 1024
_background: set[asyncio.Task[None]] = set()


def period_days() -> int:
    """A paid period's length: 30 days, 1 under test keys."""
    return periods.TEST_PERIOD_DAYS if settings.razorpay_mode == "test" else PERIOD_DAYS


def _days_text(days: int) -> str:
    return f"{days} day{'' if days == 1 else 's'}"


# ---------------- Options (4.3 to 4.5) ----------------


@dataclass
class Context:
    """One user's standing, computed once per request."""

    user: User
    now: datetime
    ent: Entitlements
    periods: list[PlanPeriod]
    current: PlanPeriod | None
    paid_ahead: bool  # a period starts after now
    last_plan_id: int | None  # expired: the plan of the last period
    used: Fraction  # the largest share used of the current period's allowances
    credit: int  # the credit for the rest of the current period (4.4)
    ranks: dict[int, int]
    can_pay: bool
    documents: tuple[int, int]  # the user's own documents: count and bytes
    connected: set[str]  # plugin ids the user has a connection for
    enabled_models: dict[int, str]  # enabled chat model id -> name


async def context(db: AsyncSession, user: User, *, now: datetime | None = None) -> Context:
    now = now or utcnow()
    ent = await entitlements.load(db, user, now=now)
    rows = await periods.user_periods(db, user.id)
    current = periods.current_period(rows, now)
    live = [row for row in rows if row.revoked_at is None]
    last = max((row for row in live if row.starts_at <= now), key=lambda row: row.ends_at, default=None)
    cat = await plans.catalog(db)
    used = await entitlements.allowance_used(db, ent) if current is not None else Fraction(0)
    return Context(
        user=user,
        now=now,
        ent=ent,
        periods=rows,
        current=current,
        paid_ahead=any(row.starts_at > now for row in live),
        last_plan_id=last.plan_id if last is not None and ent.state == "expired" else None,
        used=used,
        credit=await payments.upgrade_credit(db, current, used=used, at=now) if current is not None else 0,
        ranks=dict((await db.execute(select(Plan.id, Plan.rank))).tuples().all()),
        can_pay=business.can_pay(user) and not user.is_admin,
        documents=await storage.owned_totals(db, user.id),
        connected=set(await db.scalars(select(PluginConnection.plugin_id).where(PluginConnection.user_id == user.id))),
        enabled_models={model.id: model.display_name for model in cat.models},
    )


def _names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _size(size_bytes: int) -> str:
    megabytes = size_bytes / _MIB
    return f"{megabytes / 1024:.1f} GB" if megabytes >= 1000 else f"{math.ceil(megabytes)} MB"


def changes(ctx: Context, plan: Plan) -> list[str]:
    """What switching from the plan that applies now to `plan` takes away (4.5)."""
    ent = ctx.ent
    if not ent.access or ent.is_admin or ent.plan_id == plan.id:
        return []
    found: list[str] = []
    target = PlanFeatures.model_validate(plan.features)
    for plugin_id in sorted(ent.plugins - set(target.plugins)):
        name = registry.plugin_name(plugin_id)
        connected = plugin_id in ctx.connected
        found.append(
            f"{name} will stop working in chat. You stay connected." if connected else f"{name} isn't included."
        )
    pairs = (("files", ent.files, target.files), ("images", ent.images, target.images))
    lost = [label for label, had, keeps in pairs if had and not keeps]
    if lost:
        found.append(f"Creating {' and '.join(lost)} from chat isn't included. Your existing ones stay.")
    had = set(ctx.enabled_models) if ent.chat_model_ids is None else ent.chat_model_ids & set(ctx.enabled_models)
    keeps = {model.id for model in plan.chat_models} if plan.restrict_models else set(ctx.enabled_models)
    gone = sorted(ctx.enabled_models[model_id] for model_id in had - keeps)
    if gone:
        found.append(f"{_names(gone)} won't be available.")
    count, size = ctx.documents
    max_documents = ctx.user.max_documents if ctx.user.max_documents is not None else plan.max_documents
    max_storage = ctx.user.max_storage_mb if ctx.user.max_storage_mb is not None else plan.max_storage_mb
    if (max_documents and count > max_documents) or (max_storage and size > max_storage * _MIB):
        allows = f"{max_documents:,}" if max_documents else "any number"
        room = f"{max_storage:,} MB" if max_storage else "no storage limit"
        found.append(
            f"You have {count:,} documents ({_size(size)}); {plan.name} allows {allows} ({room}). You keep them all "
            "but can't add more until you're under the limit."
        )
    return found


def _period_option(ctx: Context, plan: Plan) -> CheckoutOption:
    start = periods.next_start(ctx.periods, ctx.now)
    end = periods.period_end(start, period_days())
    current = ctx.current
    if current is None:
        label = f"Renew {plan.name}" if plan.id == ctx.last_plan_id else f"Choose {plan.name}"
    elif current.plan_id == plan.id:
        label = "Renew"
    else:
        on = format_ist_date(ist_date(start))
        higher = ctx.ranks.get(plan.id, 0) > ctx.ranks.get(current.plan_id or 0, 0)
        label = f"or switch on {on}" if higher else f"Switch on {on}"
    notes = []
    if current is None and ctx.ent.open_ended:
        notes.append("Your complimentary plan continues after it.")
    reason = (
        _RETIRED
        if plan.status != "active"
        else _PAID_AHEAD
        if ctx.paid_ahead
        else _NO_PAYMENTS
        if not ctx.can_pay
        else None
    )
    return CheckoutOption(
        kind="period",
        label=label,
        starts_at=start,
        ends_at=end,
        valid_through=periods.valid_through(end),
        amount_paise=plan.price_paise,
        plan_price_paise=plan.price_paise,
        credit=None,
        changes=changes(ctx, plan),
        notes=notes,
        unavailable_reason=reason,
    )


def _now_option(ctx: Context, plan: Plan) -> CheckoutOption | None:
    """An upgrade (a higher plan) or a new period of the current plan once 90% of an allowance is used."""
    current = ctx.current
    if current is None or plan.status != "active":
        return None
    same = current.plan_id == plan.id
    if same and ctx.used < entitlements.NEW_PERIOD_SHARE:
        return None
    if not same and ctx.ranks.get(plan.id, 0) <= ctx.ranks.get(current.plan_id or 0, 0):
        return None
    amount = now_charge(plan.price_paise, ctx.credit)
    given = plan.price_paise - amount
    name = periods.snapshot(current).name
    end = periods.period_end(ctx.now, period_days())
    notes = []
    if current.source == "comp":
        notes.append(f"Your complimentary {name} period ends when {plan.name} starts.")
    if ctx.paid_ahead:
        notes.append("The period you paid ahead moves to start after this one.")
    span = format_ist_range(ist_date(current.starts_at), periods.valid_through(current.ends_at))
    action = "Start a new period now" if same else "Upgrade now"
    return CheckoutOption(
        kind="now",
        label=f"{action} · {format_inr(amount)}",
        starts_at=ctx.now,
        ends_at=end,
        valid_through=periods.valid_through(end),
        amount_paise=amount,
        plan_price_paise=plan.price_paise,
        credit=CreditLine(description=f"Credit for unused {name}, {span}", amount_paise=given) if given else None,
        changes=changes(ctx, plan),
        notes=notes,
        unavailable_reason=None if ctx.can_pay else _NO_PAYMENTS,
    )


def options(ctx: Context, plan: Plan) -> list[CheckoutOption]:
    if ctx.user.is_admin:
        return []
    found = [option for option in (_now_option(ctx, plan),) if option is not None]
    return [*found, _period_option(ctx, plan)]


async def plan_catalog(db: AsyncSession, user: User) -> PlanCatalog:
    """The active plans plus the plan of the user's current or latest period and their open-ended plan, by rank."""
    ctx = await context(db, user)
    wanted = {plan.id for plan in await plans.active_plans(db)}
    latest = max((row for row in ctx.periods if row.starts_at <= ctx.now), key=lambda row: row.ends_at, default=None)
    wanted |= {
        plan_id for plan_id in (ctx.ent.plan_id, latest.plan_id if latest else None, user.comp_plan_id) if plan_id
    }
    rows = await db.scalars(select(Plan).where(Plan.id.in_(wanted)).order_by(Plan.rank))
    cat = await plans.catalog(db)
    offers = [
        PlanOffer(
            **plans.public_plan(plan, cat).model_dump(),
            id=plan.id,
            status="retired" if plan.status == "retired" else "active",
            current=ctx.ent.access and not ctx.ent.is_admin and ctx.ent.plan_id == plan.id,
            options=options(ctx, plan),
        )
        for plan in rows
    ]
    return PlanCatalog(plans=offers, payments=await business.payments_info(db, user))


# ---------------- Caps (6.3) ----------------


def _hour_key(user_id: int) -> str:
    return f"rl:checkout:{user_id}"


def _day_key(user_id: int, now: datetime) -> str:
    return f"rl:checkout-day:{user_id}:{ist_date(now).isoformat()}"


async def _reserve(user_id: int, now: datetime) -> None:
    """Counts a payment link in both caps before it is made: each count is added first and taken back when it goes
    over, so checkouts at the same time can't pass a cap together. 429 too_many_checkouts over a cap."""
    redis = get_redis()
    stamp = now.timestamp()
    day = _day_key(user_id, now)
    if await redis.incr(day) > DAY_CAP:
        await redis.decr(day)
        midnight = (now.astimezone(IST) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        retry = math.ceil((midnight - now).total_seconds())
        detail = "Too many payment attempts. Try again tomorrow."
        raise AppError(429, detail, "too_many_checkouts", headers={"Retry-After": str(retry)})
    await redis.expire(day, 2 * 86_400)
    hour, entry = _hour_key(user_id), f"{stamp}:{secrets.token_hex(4)}"
    await redis.zremrangebyscore(hour, 0, stamp - 3600)
    await redis.zadd(hour, {entry: stamp})
    await redis.expire(hour, 3600)
    if await redis.zcard(hour) > HOUR_CAP:
        await redis.zrem(hour, entry)
        await redis.decr(day)
        oldest = await redis.zrange(hour, 0, 0, withscores=True)
        retry = max(1, math.ceil(float(oldest[0][1]) + 3600 - stamp)) if oldest else 3600
        detail = "Too many payment attempts. Try again in an hour."
        raise AppError(429, detail, "too_many_checkouts", headers={"Retry-After": str(retry)})


# ---------------- Checkout (6.3) ----------------


async def payments_unavailable(db: AsyncSession) -> AppError:
    email = (await business.get_settings(db)).contact_email
    detail = _NO_PAYMENTS + (f" Contact {email}." if email else "")
    return AppError(503, detail, "payments_unavailable")


def _link_body(order: BillingOrder, user: User, plan: Plan, days: int) -> dict[str, object]:
    return {
        "amount": order.amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "reference_id": reference_id(order),
        "description": f"{plan.name} plan, {_days_text(days)}",
        "expire_by": int(order.expires_at.timestamp()),
        "reminder_enable": False,
        "notify": {"sms": False, "email": False},
        "notes": {"order": order.public_id, "user_id": str(user.id), "plan": plan.code, "kind": order.kind},
        "callback_url": order.origin + business.RETURN_PATH,
        "callback_method": "get",
    }


def reference_id(order: BillingOrder) -> str:
    return f"rag-{order.public_id}"


async def _reusable(
    db: AsyncSession, user: User, plan: Plan, kind: str, amount: int, now: datetime
) -> BillingOrder | None:
    return await db.scalar(
        select(BillingOrder)
        .where(
            BillingOrder.user_id == user.id,
            BillingOrder.status == "pending",
            BillingOrder.plan_id == plan.id,
            BillingOrder.kind == kind,
            BillingOrder.amount_paise == amount,
            BillingOrder.mode == settings.razorpay_mode,
            BillingOrder.pay_url.is_not(None),
            BillingOrder.expires_at >= now + timedelta(minutes=REUSE_MINUTES),
        )
        .order_by(BillingOrder.id.desc())
        .limit(1)
    )


async def start(db: AsyncSession, user: User, body: CheckoutRequest, origin: str | None) -> CheckoutStarted:
    """POST /billing/checkout. No transaction is open while Razorpay is called."""
    if user.is_admin:
        raise AppError(403, "The admin doesn't need a plan.", "admin_exempt")
    if not business.can_pay(user):
        raise await payments_unavailable(db)
    now = utcnow()
    plan = await db.get(Plan, body.plan_id)
    if plan is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    await lock_user(db, user.id)
    ctx = await context(db, user, now=now)
    option = next((item for item in options(ctx, plan) if item.kind == body.kind), None)
    if option is None or option.unavailable_reason:
        reason = option.unavailable_reason if option else _RETIRED if plan.status != "active" else _NOT_HIGHER
        await db.rollback()
        raise AppError(409, reason or _NOT_HIGHER, "checkout_unavailable")
    reused = await _reusable(db, user, plan, body.kind, option.amount_paise, now)
    if reused is not None and reused.pay_url:
        await db.commit()
        return CheckoutStarted(order=await order_view(db, reused), pay_url=reused.pay_url)
    try:
        await _reserve(user.id, now)
    except AppError:
        await db.rollback()
        raise
    days = period_days()
    expires = now + timedelta(minutes=settings.billing_checkout_minutes)
    current = ctx.current if body.kind == "now" else None
    order = BillingOrder(
        public_id=secrets.token_urlsafe(15),
        user_id=user.id,
        username=user.username,
        plan_id=plan.id,
        plan_snapshot={**periods.snapshot_of(plan).model_dump(mode="json"), "price_paise": plan.price_paise},
        kind=body.kind,
        replaces_period_id=current.id if current else None,
        replaces_plan_id=current.plan_id if current else None,
        replaces_ends_at=current.ends_at if current else None,
        credit_paise=option.plan_price_paise - option.amount_paise,
        amount_paise=option.amount_paise,
        currency="INR",
        mode=settings.razorpay_mode,
        status="pending",
        terms_accepted_at=now,
        origin=business.origin_for(origin),
        expires_at=expires,
        watch_until=expires + timedelta(days=settings.billing_late_capture_days),
        next_check_at=now + timedelta(seconds=FIRST_CHECK_SECONDS),
        created_at=now,  # the quote's time: applying a `now` order checks its credit again as of then
    )
    db.add(order)
    await db.commit()
    try:
        link = await razorpay.create_link(_link_body(order, user, plan, days))
        if not razorpay.pay_url_ok(link.get("short_url")) or razorpay.entity_id(link.get("id")) is None:
            raise razorpay.RazorpayError(502, "BAD_LINK", "The payment link isn't a Razorpay address")
    except razorpay.RazorpayError as exc:
        await _create_failed(db, order, exc)
        raise AppError(502, PROVIDER_ERROR, "payment_provider_error") from None
    replaced = await _store_link(db, user, order, plan, link)
    if replaced is None:
        await _cancel_link(link["id"])
        raise AppError(409, _SUPERSEDED, "checkout_unavailable")
    for link_id in replaced:
        await _cancel_link(link_id)
    order = await db.get(BillingOrder, order.id, populate_existing=True) or order
    return CheckoutStarted(order=await order_view(db, order), pay_url=link["short_url"])


async def _store_link(
    db: AsyncSession, user: User, order: BillingOrder, plan: Plan, link: dict[str, Any]
) -> list[str] | None:
    """Under the user's lock: the order gets its link when it is still pending, and the user's other pending orders
    are cancelled (returns their links, to cancel at Razorpay). None when the order was cancelled while its link was
    made (another checkout replaced it, or the user cancelled it): it keeps the link id, for the reconciler. Both
    updates are conditional, so a checkout finishing at the same time sees one or the other. Commits."""
    await lock_user(db, user.id)
    stored = await db.execute(
        update(BillingOrder)
        .where(BillingOrder.id == order.id, BillingOrder.status == "pending")
        .values(
            razorpay_link_id=link["id"],
            pay_url=link["short_url"],
            razorpay_order_id=razorpay.entity_id(link.get("order_id")),
        )
        .returning(BillingOrder.id)
        .execution_options(synchronize_session=False)
    )
    if stored.first() is None:
        await db.execute(
            update(BillingOrder)
            .where(BillingOrder.id == order.id)
            .values(razorpay_link_id=link["id"])
            .execution_options(synchronize_session=False)
        )
        await audit.record_event(
            db,
            source="user",
            type="order.superseded",
            actor=user.username,
            user_id=user.id,
            order_id=order.id,
            detail={"public_id": order.public_id, "link": link["id"]},
        )
        await db.commit()
        return None
    await audit.record_event(
        db,
        source="user",
        type="order.created",
        actor=user.username,
        user_id=user.id,
        order_id=order.id,
        detail={"public_id": order.public_id, "plan": plan.code, "kind": order.kind, "amount": order.amount_paise},
    )
    previous = (
        await db.execute(
            update(BillingOrder)
            .where(BillingOrder.user_id == user.id, BillingOrder.status == "pending", BillingOrder.id != order.id)
            .values(status="cancelled")
            .returning(BillingOrder.id, BillingOrder.public_id, BillingOrder.razorpay_link_id, BillingOrder.mode)
            .execution_options(synchronize_session=False)
        )
    ).all()
    for order_id, public_id, _, _ in previous:
        await audit.record_event(
            db,
            source="user",
            type="order.cancelled",
            actor=user.username,
            user_id=user.id,
            order_id=order_id,
            detail={"public_id": public_id, "replaced_by": order.public_id},
        )
    await db.commit()
    return [link_id for _, _, link_id, mode in previous if link_id and mode == settings.razorpay_mode]


async def _create_failed(db: AsyncSession, order: BillingOrder, exc: razorpay.RazorpayError) -> None:
    """The link wasn't made (or can't be trusted): the order is cancelled; a link a timed-out create made anyway is
    found by our reference, stored and cancelled. The previous checkout stays as it was."""
    if exc.keys_rejected:
        await business.set_keys_rejected(exc.description)
    found_id: str | None = None
    if exc.transient or exc.code == "BAD_LINK":
        try:
            found = await razorpay.find_links(reference_id(order))
            found_id = next((razorpay.entity_id(link.get("id")) for link in found), None)
        except razorpay.RazorpayError:
            found_id = None
    order.status = "cancelled"
    order.last_error = "Razorpay unreachable" if exc.status is None else f"Razorpay refused the link ({exc.code})"
    order.razorpay_link_id = found_id
    order.next_check_at = order.next_check_at if found_id else None
    await audit.record_event(
        db,
        source="user",
        type="order.create_failed",
        user_id=order.user_id,
        order_id=order.id,
        detail={"code": exc.code, "description": exc.description, "status": exc.status},
    )
    await db.commit()
    logger.warning("Creating a payment link failed (%s %s)", exc.status, exc.code)
    if found_id:
        await _cancel_link(found_id)


async def _cancel_link(link_id: str) -> None:
    try:
        await razorpay.cancel_link(link_id)
    except razorpay.RazorpayError as exc:
        logger.info("Cancelling a payment link failed (%s %s); it expires on its own", exc.status, exc.code)


async def cancel(db: AsyncSession, user: User, order: BillingOrder) -> OrderView:
    """The user cancels a pending checkout: the link is cancelled best effort and the order stays watched, so a
    payment that still arrives is applied (and the reconciler cancels a link left live)."""
    await lock_user(db, user.id)
    row = (
        await db.execute(
            update(BillingOrder)
            .where(BillingOrder.id == order.id, BillingOrder.status == "pending")
            .values(status="cancelled")
            .returning(BillingOrder.razorpay_link_id, BillingOrder.mode)
            .execution_options(synchronize_session=False)
        )
    ).first()
    if row is None:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a checkout waiting for payment can be cancelled.")
    await audit.record_event(
        db,
        source="user",
        type="order.cancelled",
        actor=user.username,
        user_id=user.id,
        order_id=order.id,
        detail={"public_id": order.public_id},
    )
    await db.commit()
    link_id, mode = row
    if link_id and mode == settings.razorpay_mode:
        await _cancel_link(link_id)
    return await order_view(db, await db.get(BillingOrder, order.id, populate_existing=True) or order)


async def cancel_user_links(db: AsyncSession, user: User) -> None:
    """Before a user is deleted (4.12): their pending checkouts are cancelled; their links are cancelled at
    Razorpay after the request, best effort. A payment that still arrives goes to review."""
    pending = list(
        await db.scalars(select(BillingOrder).where(BillingOrder.user_id == user.id, BillingOrder.status == "pending"))
    )
    links = [
        order.razorpay_link_id for order in pending if order.razorpay_link_id and order.mode == settings.razorpay_mode
    ]
    for order in pending:
        order.status = "cancelled"
        await audit.record_event(
            db, source="admin", type="order.cancelled", order_id=order.id, detail={"reason": "The user was deleted"}
        )
    if links:
        task = asyncio.create_task(_cancel_links(links))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def _cancel_links(link_ids: list[str]) -> None:
    for link_id in link_ids:
        await _cancel_link(link_id)


# ---------------- Order views ----------------


@dataclass(frozen=True)
class _Bought:
    period: PlanPeriod | None
    payment: BillingPayment | None
    receipt: BillingReceipt | None


async def _bought(db: AsyncSession, order: BillingOrder) -> _Bought:
    period = await db.scalar(select(PlanPeriod).where(PlanPeriod.order_id == order.id).order_by(PlanPeriod.id).limit(1))
    payment = await db.get(BillingPayment, order.payment_id) if order.payment_id else None
    receipt = (
        await db.scalar(select(BillingReceipt).where(BillingReceipt.payment_id == payment.id)) if payment else None
    )
    return _Bought(period, payment, receipt)


def _status(order: BillingOrder) -> Literal["pending", "paid", "expired", "cancelled", "review"]:
    known = ("pending", "paid", "expired", "cancelled", "review")
    return order.status if order.status in known else "pending"  # type: ignore[return-value]


async def order_view(db: AsyncSession, order: BillingOrder) -> OrderView:
    bought = await _bought(db, order)
    now = utcnow()
    period = bought.period
    return OrderView(
        public_id=order.public_id,
        kind="now" if order.kind == "now" else "period",
        plan=plans.snapshot_ref(order.plan_snapshot),
        amount_paise=order.amount_paise,
        credit_paise=order.credit_paise,
        status=_status(order),
        created_at=order.created_at,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        pay_url=order.pay_url if order.status == "pending" and order.expires_at > now else None,
        last_payment_error=order.last_payment_error,
        period_start=period.starts_at if period else None,
        period_end=period.ends_at if period else None,
        valid_through=periods.valid_through(period.ends_at) if period else None,
        receipt=ReceiptRef(id=bought.receipt.id, number=bought.receipt.number) if bought.receipt else None,
        method=bought.payment.method if bought.payment else None,
        test_mode=order.mode == "test",
    )


def paused(order: BillingOrder) -> bool:
    """An order of the other mode than the server's keys, still watched: never checked, applied or cancelled."""
    return order.mode in ("test", "live") and order.mode != settings.razorpay_mode and order.next_check_at is not None


async def admin_order(db: AsyncSession, order: BillingOrder) -> AdminOrder:
    view = await order_view(db, order)
    mode: Literal["test", "live", "offline"] = (
        "test" if order.mode == "test" else "offline" if order.mode == "offline" else "live"
    )
    return AdminOrder(
        **view.model_dump(),
        id=order.id,
        user=UserRef(id=order.user_id, username=order.username),
        mode=mode,
        razorpay_link_id=order.razorpay_link_id,
        razorpay_order_id=order.razorpay_order_id,
        review_reason=order.review_reason,
        review_note=order.review_note,
        checks=order.checks,
        last_checked_at=order.last_checked_at,
        next_check_at=order.next_check_at,
        last_error=order.last_error,
        paused=paused(order),
    )
