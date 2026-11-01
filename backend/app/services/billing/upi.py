"""Direct UPI payments to the owner's own UPI ID (docs/briefs/upi-direct.md): the order and its payment request (a
reference for the UPI note, the upi://pay link and its QR codes, rendered with segno), the customer's UTR, the admin's
check (a confirm records the payment and applies it once through payments.apply_matched; a reject tells the customer
why) and the expiry of orders that got no UTR. The request is built only from the stored order or the validated
settings. Logs never carry a UTR, UPI ID or payee name."""

import io
import logging
import math
import secrets
from datetime import datetime, timedelta
from typing import Final, Literal
from urllib.parse import quote

import segno
from fastapi import HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.errors import AppError
from app.models import BillingOrder, BillingPayment, Plan, User, utcnow
from app.redis_client import get_redis
from app.schemas_billing import (
    AdminOrder,
    BillingSettings,
    CheckoutOption,
    CheckoutRequest,
    OrderChannel,
    OrderView,
    UpiOrderInfo,
    UpiRejection,
    UpiRejectRequest,
)
from app.services.auth import throttle
from app.services.billing import audit, business, checkout, paid, payments, periods
from app.services.billing.locks import lock_user
from app.services.billing.money import IST, format_inr, ist_date
from app.services.mail import outbox

logger = logging.getLogger(__name__)

ALPHABET: Final = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0, O, 1, I or L
OPEN_FOR: Final = timedelta(hours=24)  # an order without a UTR expires this long after it was made
REUSE_LEFT: Final = timedelta(hours=1)  # an open order with this long left is returned instead of a new one
DAY_CAP: Final = 5  # direct-UPI orders a user makes per IST day
UNPAID_DAY_CAP: Final = 3  # the same for a user who hasn't paid yet
UNPAID_WAITING_CAP: Final = 20  # UTRs of every unpaid user together waiting for the admin
UTR_DAY_CAP: Final = 10  # UTR submissions per user and IST day
PAUSE_REJECTIONS: Final = 3  # rejections in PAUSE_WINDOW that pause direct UPI for the user
PAUSE_WINDOW: Final = timedelta(days=30)
REJECTION_SHOWN: Final = timedelta(days=7)  # Me.plan.rejected_order
PREVIEW_PAISE: Final = 100
PREVIEW_NOTE: Final = "AN-TEST"
_REFERENCE_TRIES = 5
_QR_HEADERS: Final = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}
_WORDS: Final = {
    "pending": "waiting for UTR",
    "paid": "paid",
    "review": "under review",
    "rejected": "rejected",
    "expired": "expired",
    "cancelled": "cancelled",
}
_ALREADY_SENT = "This payment was already sent for checking."
_UTR_USED = "This UTR is already used for another payment. Check the 12-digit number in your UPI app."
_TEST_CREDIT = (
    "Your current period was paid in test mode, so it gives no credit towards a UPI payment. Pay online instead, or "
    "choose the plan to start after this period."
)
_UNPAID_BUSY = "billing:upi-unpaid-busy"  # set for an hour when UNPAID_WAITING_CAP refuses an order (billing check)


# ---------------- The request and its QR codes (7) ----------------


def channel(order: BillingOrder) -> OrderChannel:
    """How the order is paid: no column holds it (docs/briefs/upi-direct.md D11)."""
    return "upi_direct" if order.upi_reference else "bank_transfer" if order.mode == "offline" else "razorpay"


def new_reference() -> str:
    """ "AN-7K4M-Q9TX": 8 characters of ALPHABET drawn with secrets."""
    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"AN-{code[:4]}-{code[4:]}"


def request_uri(upi_id: str, payee_name: str, amount_paise: int, note: str) -> str:
    """The UPI payment request: the UPI ID as it is (validated, so nothing to encode), the name percent-encoded (%20
    for a space), whole rupees with ".00", INR and the note; no tr, mc, mode or sign (2.2)."""
    if amount_paise <= 0 or amount_paise % 100:
        raise ValueError("A direct-UPI amount is a whole number of rupees")
    return f"upi://pay?pa={upi_id}&pn={quote(payee_name, safe='')}&am={amount_paise // 100}.00&cu=INR&tn={note}"


def order_uri(order: BillingOrder) -> str:
    """The order's request, from the stored order only."""
    assert order.upi_vpa and order.upi_payee_name and order.upi_reference, order.id
    return request_uri(order.upi_vpa, order.upi_payee_name, order.amount_paise, order.upi_reference)


def qr_svg(uri: str) -> bytes:
    """Black on white (a white background in the file, so a dark theme can't make it unscannable), a 4-module quiet
    zone, a viewBox and no size: the page sizes it."""
    out = io.BytesIO()
    segno.make(uri, error="m", micro=False).save(
        out,
        kind="svg",
        scale=1,
        border=4,
        dark="#000",
        light="#fff",
        xmldecl=False,
        omitsize=True,
        svgclass=None,
        lineclass=None,
    )
    return out.getvalue()


def qr_png(uri: str) -> bytes:
    out = io.BytesIO()
    segno.make(uri, error="m", micro=False).save(out, kind="png", scale=10, border=4)
    return out.getvalue()


def qr_response(content: bytes, media_type: str, filename: str | None = None) -> Response:
    headers = dict(_QR_HEADERS)
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=content, media_type=media_type, headers=headers)


def open_now(order: BillingOrder, now: datetime) -> bool:
    """A direct-UPI order still waiting for its UTR."""
    return order.upi_reference is not None and order.status == "pending" and order.expires_at > now


def order_qr(order: BillingOrder, kind: Literal["svg", "png"]) -> Response:
    """The caller's own direct-UPI order's QR code while it waits for its UTR; 404 otherwise."""
    if not open_now(order, utcnow()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    uri = order_uri(order)
    if kind == "svg":
        return qr_response(qr_svg(uri), "image/svg+xml")
    return qr_response(qr_png(uri), "image/png", f"upi-{order.upi_reference}.png")


async def preview_qr(db: AsyncSession) -> Response:
    """The admin's ₹1 test request to the saved UPI ID and payee name, whether direct UPI is on or not."""
    current = await business.get_settings(db)
    if not (current.upi_id and current.upi_payee_name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Save a UPI ID and the payee name first")
    uri = request_uri(current.upi_id, current.upi_payee_name, PREVIEW_PAISE, PREVIEW_NOTE)
    return qr_response(qr_svg(uri), "image/svg+xml")


# ---------------- Views ----------------


def group_utr(utr: str) -> str:
    """ "6305 1234 5678"."""
    return " ".join(utr[index : index + 4] for index in range(0, len(utr), 4))


def rejection_text(reason: str, amount_paise: int, utr: str) -> tuple[str, bool]:
    """The customer's sentence for a reason, and whether it goes on to ask them to write with a screenshot."""
    amount, grouped = format_inr(amount_paise), group_utr(utr)
    if reason == "not_received":
        return f"We couldn't find a payment of {amount} with UTR {grouped}.", True
    if reason == "amount_differs":
        return (
            f"The payment with UTR {grouped} wasn't for {amount}. We'll send what you paid back to you by UPI. Start "
            "a new payment for the exact amount."
        ), False
    if reason == "utr_used":
        return (
            f"UTR {grouped} belongs to another payment. Check the 12-digit number in your UPI app, then start a new "
            "payment."
        ), False
    return f"We couldn't confirm your payment of {amount}.", False


def rejection_message(reason: str, amount_paise: int, utr: str, contact: str | None) -> str:
    """What the customer reads (SPEC.md, Billing, Direct UPI)."""
    text, write = rejection_text(reason, amount_paise, utr)
    if not write:
        return text
    reach = f"write to {contact} with a screenshot of the payment." if contact else "write to us from Settings."
    return f"{text} If money left your account, {reach}"


def rejection(order: BillingOrder, contact: str | None) -> UpiRejection | None:
    if order.upi_rejection is None or order.upi_utr is None:
        return None
    return UpiRejection(
        reason=order.upi_rejection,  # type: ignore[arg-type]
        message=rejection_message(order.upi_rejection, order.amount_paise, order.upi_utr, contact),
        note=order.upi_rejection_note,
    )


def info(order: BillingOrder, contact: str | None, now: datetime) -> UpiOrderInfo:
    """OrderView.upi of a direct-UPI order; `contact` (the business email) goes into a rejection's message."""
    assert order.upi_reference is not None, order.id
    return UpiOrderInfo(
        reference=order.upi_reference,
        upi_id=order.upi_vpa or "",
        payee_name=order.upi_payee_name or "",
        uri=order_uri(order) if open_now(order, now) else None,
        utr=order.upi_utr,
        submitted_at=order.upi_submitted_at,
        decided_at=order.upi_decided_at,
        rejection=rejection(order, contact),
    )


# ---------------- Starting an order (8.1) ----------------


def _contact(sentence: str, email: str | None) -> str:
    """ " Contact x@y." style tails: left out without the business email."""
    return f" {sentence} {email}." if email else ""


def _midnight(now: datetime) -> tuple[datetime, int]:
    """The IST midnight that started today, and the seconds until the next one (a Retry-After)."""
    start = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, max(1, math.ceil((start + timedelta(days=1) - now).total_seconds()))


async def refuse_while_waiting(db: AsyncSession, user: User) -> None:
    """While a UTR of the user waits for the admin, no checkout of either channel starts (D12): 409 upi_waiting."""
    waiting = await db.scalar(
        select(BillingOrder)
        .where(
            BillingOrder.user_id == user.id,
            BillingOrder.upi_reference.is_not(None),
            BillingOrder.status == "submitted",
        )
        .order_by(BillingOrder.id.desc())
        .limit(1)
    )
    if waiting is None:
        return
    detail = (
        f"We're still checking your UPI payment of {format_inr(waiting.amount_paise)} ({waiting.upi_reference}). You "
        "can start another payment once it's confirmed or rejected."
    )
    await db.rollback()
    raise AppError(409, detail, "upi_waiting")


async def _refuse_when_paused(db: AsyncSession, user: User, now: datetime, contact: str | None) -> None:
    rejected = await db.scalar(
        select(func.count())
        .select_from(BillingOrder)
        .where(
            BillingOrder.user_id == user.id,
            BillingOrder.upi_rejection.is_not(None),
            BillingOrder.upi_decided_at >= now - PAUSE_WINDOW,
        )
    )
    if (rejected or 0) < PAUSE_REJECTIONS:
        return
    detail = "Paying by UPI is paused for your account because recent payments couldn't be confirmed."
    detail += _contact("Write to", contact) + (" Or pay online." if business.can_pay(user) else "")
    await db.rollback()
    raise AppError(409, detail, "upi_paused")


async def _reusable(
    db: AsyncSession, user: User, plan: Plan, kind: str, amount: int, upi_id: str, now: datetime
) -> BillingOrder | None:
    return await db.scalar(
        select(BillingOrder)
        .where(
            BillingOrder.user_id == user.id,
            BillingOrder.upi_reference.is_not(None),
            BillingOrder.status == "pending",
            BillingOrder.plan_id == plan.id,
            BillingOrder.kind == kind,
            BillingOrder.amount_paise == amount,
            BillingOrder.upi_vpa == upi_id,
            BillingOrder.expires_at >= now + REUSE_LEFT,
        )
        .order_by(BillingOrder.id.desc())
        .limit(1)
    )


async def unpaid_cap_reached() -> bool:
    """UNPAID_WAITING_CAP refused an unpaid user's order in the last hour."""
    return bool(await get_redis().exists(_UNPAID_BUSY))


async def _refuse_over_caps(db: AsyncSession, user: User, request: Request, now: datetime) -> None:
    """Under the user's lock: the user's orders today (429 too_many_checkouts), and for an unpaid user the UTRs of
    every unpaid user waiting for the admin (503 checkout_busy) and the network's orders of unpaid users (429
    too_many_attempts, counted last). A UTR is never refused by these."""
    midnight, retry = _midnight(now)
    unpaid = not await paid.has_paid(db, user.id)
    made = await db.scalar(
        select(func.count())
        .select_from(BillingOrder)
        .where(
            BillingOrder.user_id == user.id,
            BillingOrder.upi_reference.is_not(None),
            BillingOrder.created_at >= midnight,
        )
    )
    if (made or 0) >= (UNPAID_DAY_CAP if unpaid else DAY_CAP):
        await db.rollback()
        raise AppError(
            429,
            "Too many payment requests today. Try again tomorrow.",
            "too_many_checkouts",
            headers={"Retry-After": str(retry)},
        )
    if not unpaid:
        return
    payers = select(BillingPayment.user_id).where(
        BillingPayment.user_id.is_not(None),
        BillingPayment.applied_at.is_not(None),
        BillingPayment.mode.in_(("live", "offline")),
    )
    held = await db.scalar(
        select(func.count())
        .select_from(BillingOrder)
        .where(
            BillingOrder.upi_reference.is_not(None),
            BillingOrder.status == "submitted",
            BillingOrder.user_id.is_not(None),
            BillingOrder.user_id.not_in(payers),
        )
    )
    if (held or 0) >= UNPAID_WAITING_CAP:
        detail = "Many UPI payments are waiting to be checked. Try again in a few hours."
        detail += " Or pay online." if business.can_pay(user) else ""
        await db.rollback()
        await get_redis().set(_UNPAID_BUSY, "1", ex=3600)
        logger.warning("Unpaid users' UTRs waiting to be checked reached %d: an order was refused", UNPAID_WAITING_CAP)
        raise AppError(503, detail, "checkout_busy", headers={"Retry-After": "3600"})
    try:
        await throttle.check(request, "upi_unpaid")
    except AppError:
        await db.rollback()
        raise


async def _insert(
    db: AsyncSession, ctx: checkout.Context, plan: Plan, option: CheckoutOption, current: BillingSettings, origin: str
) -> BillingOrder:
    """The order with a fresh reference: inserted in a savepoint, a reference taken already drawn again."""
    user = ctx.user
    replaced = ctx.current if option.kind == "now" else None
    for attempt in range(_REFERENCE_TRIES):
        order = BillingOrder(
            public_id=secrets.token_urlsafe(15),
            user_id=user.id,
            username=user.username,
            plan_id=plan.id,
            plan_snapshot={**periods.snapshot_of(plan).model_dump(mode="json"), "price_paise": plan.price_paise},
            kind=option.kind,
            replaces_period_id=replaced.id if replaced else None,
            replaces_plan_id=replaced.plan_id if replaced else None,
            replaces_ends_at=replaced.ends_at if replaced else None,
            credit_paise=option.plan_price_paise - option.amount_paise,
            amount_paise=option.amount_paise,
            currency="INR",
            mode="offline",
            status="pending",
            terms_accepted_at=ctx.now,
            origin=origin,
            expires_at=ctx.now + OPEN_FOR,
            watch_until=ctx.now + OPEN_FOR,
            next_check_at=None,
            created_at=ctx.now,  # the quote's time, as for a Razorpay order
            upi_reference=new_reference(),
            upi_vpa=current.upi_id,
            upi_payee_name=current.upi_payee_name,
        )
        try:
            async with db.begin_nested():
                db.add(order)
        except IntegrityError:
            if attempt == _REFERENCE_TRIES - 1:
                raise
            continue
        return order
    raise AssertionError("unreachable")


async def start(db: AsyncSession, user: User, body: CheckoutRequest, request: Request) -> OrderView:
    """POST /billing/upi/checkout: a direct-UPI order for the option the caller chose, quoted as a Razorpay order
    would be (but no credit from a test-mode period). The user's other pending orders are cancelled (their Razorpay
    links after the commit, best effort)."""
    if user.is_admin:
        raise AppError(403, "The admin doesn't need a plan.", "admin_exempt")
    current = await business.get_settings(db)
    if not business.upi_ready(current) or not current.upi_id:
        detail = "Paying by UPI isn't available right now." + _contact("Contact", current.contact_email)
        raise AppError(503, detail, "upi_unavailable")
    plan = await db.get(Plan, body.plan_id)
    if plan is None:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    now = utcnow()
    await lock_user(db, user.id)
    ctx = await checkout.context(db, user, now=now)
    option = await checkout.chosen_option(db, ctx, plan, body.kind)
    if option.credit is not None and ctx.current is not None and ctx.current.mode == "test":
        await db.rollback()
        raise AppError(409, _TEST_CREDIT, "checkout_unavailable")
    await refuse_while_waiting(db, user)
    await _refuse_when_paused(db, user, now, current.contact_email)
    reused = await _reusable(db, user, plan, body.kind, option.amount_paise, current.upi_id, now)
    if reused is not None:
        await db.commit()
        return await checkout.order_view(db, reused)
    await _refuse_over_caps(db, user, request, now)
    order = await _insert(db, ctx, plan, option, current, business.origin_for(request.headers.get("origin")))
    links = await checkout.cancel_others(db, user, order)
    await audit.record_event(
        db,
        source="user",
        type="order.created",
        actor=user.username,
        user_id=user.id,
        order_id=order.id,
        detail={
            "public_id": order.public_id,
            "channel": "upi_direct",
            "reference": order.upi_reference,
            "plan": plan.code,
            "kind": order.kind,
            "amount": order.amount_paise,
        },
    )
    await db.commit()
    for link_id in links:
        await checkout.cancel_link(link_id)
    return await checkout.order_view(db, order)


# ---------------- The UTR (8.2) ----------------


async def _locked(db: AsyncSession, order_id: int) -> BillingOrder:
    order = await db.scalar(
        select(BillingOrder)
        .where(BillingOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert order is not None, order_id
    return order


def _not_open(order: BillingOrder, now: datetime) -> str:
    if order.status == "cancelled":
        gone = "was cancelled"
    elif order.status == "expired" or (order.status == "pending" and order.expires_at <= now):
        gone = "has expired"
    else:
        return _ALREADY_SENT
    return f"This payment request {gone}. If you paid, start a new payment for the same plan and enter this UTR there."


async def _count_attempt(user: User, now: datetime, contact: str | None) -> None:
    key = f"rl:upi-utr:{user.id}:{ist_date(now).isoformat()}"
    redis = get_redis()
    tries = await redis.incr(key)
    await redis.expire(key, 2 * 86_400)
    if tries > UTR_DAY_CAP:
        detail = "Too many tries today. Try again tomorrow." + _contact("Or write to", contact)
        raise AppError(429, detail, "too_many_attempts", headers={"Retry-After": str(_midnight(now)[1])})


async def submit_utr(db: AsyncSession, user: User, order: BillingOrder, utr: str) -> OrderView:
    """POST /billing/orders/{public_id}/utr on the caller's own order: the order waits for the admin's check; the
    owner is told (Needs attention, and an email to the business contact email when the mailer is configured)."""
    if order.upi_reference is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    now = utcnow()
    current = await business.get_settings(db)
    await _count_attempt(user, now, current.contact_email)
    await lock_user(db, user.id)
    order = await _locked(db, order.id)
    if order.status == "submitted" and order.upi_utr == utr:
        await db.commit()
        return await checkout.order_view(db, order)
    if order.status != "pending" or order.expires_at <= now:
        detail = _not_open(order, now)
        await db.rollback()
        raise AppError(409, detail, "order_not_open")
    if await payments.recorded_with_utr(db, utr) is not None:
        await db.rollback()
        raise AppError(409, _UTR_USED, "utr_used")
    try:
        async with db.begin_nested():
            order.upi_utr, order.upi_submitted_at, order.status = utr, now, "submitted"
    except IntegrityError:
        await db.rollback()
        raise AppError(409, _UTR_USED, "utr_used") from None
    await audit.record_event(
        db,
        source="user",
        type="upi.submitted",
        actor=user.username,
        user_id=user.id,
        order_id=order.id,
        detail={"reference": order.upi_reference, "utr": utr, "amount": order.amount_paise},
    )
    tell = outbox.configured() and bool(current.contact_email)
    if tell and current.contact_email:
        await outbox.enqueue(db, "upi_submitted", to=current.contact_email, params={"order_id": order.id})
    await db.commit()
    if tell:
        await outbox.wake()
    return await checkout.order_view(db, order)


# ---------------- The admin's check (8.3, 8.4) ----------------


def _word(order: BillingOrder, now: datetime) -> str:
    if order.upi_reference is None:
        return "a bank transfer" if order.mode == "offline" else "an online payment"
    if order.status == "pending" and order.expires_at <= now:
        return _WORDS["expired"]
    return _WORDS.get(order.status, order.status)


def _waits(order: BillingOrder, utr: str, now: datetime) -> bool:
    """Whether a confirm with `utr` has work to do: False when the order was already confirmed with it (a double
    click, another admin). Raises 409 when it can't be confirmed."""
    if order.upi_reference is not None and order.status in ("paid", "review") and order.upi_decided_at is not None:
        if order.upi_utr == utr:
            return False
    if order.upi_reference is None or order.status != "submitted":
        raise AppError(409, f"This payment isn't waiting for a check: it is {_word(order, now)}.", "upi_not_waiting")
    if order.upi_utr != utr:
        detail = f"The UTR on this payment is {group_utr(order.upi_utr or '')}. Check it again."
        raise AppError(409, detail, "utr_mismatch")
    if order.user_id is None:
        detail = "The account was deleted. If you received this payment, pay it back by UPI, then reject it here."
        raise AppError(409, detail, "user_deleted")
    return True


async def confirm(db: AsyncSession, admin: User, order: BillingOrder, utr: str) -> AdminOrder:
    """POST /admin/billing/orders/{public_id}/upi-confirm: records the payment (method upi, mode offline, the UTR as
    its reference, captured when the UTR came) and applies it under the user's lock and the order's row lock, once."""
    if _waits(order, utr, utcnow()) and order.user_id is not None:
        await lock_user(db, order.user_id)
        order = await _locked(db, order.id)
        try:
            waits = _waits(order, utr, utcnow())
            if waits:
                await _refuse_recorded(db, utr)
        except AppError:
            await db.rollback()
            raise
        if waits:
            await _apply(db, admin, order)
    await db.commit()
    await outbox.wake()
    return await checkout.admin_order(db, order)


async def _refuse_recorded(db: AsyncSession, utr: str) -> None:
    """409 utr_used when a payment already recorded (a bank transfer) carries the UTR: the same money twice."""
    recorded = await payments.recorded_with_utr(db, utr)
    if recorded is None:
        return
    how = "a bank transfer" if recorded.method == "bank_transfer" else "a payment"
    raise AppError(
        409,
        f"UTR {group_utr(utr)} is already recorded for {how} from {recorded.username}. Reject this payment with "
        '"This UTR belongs to another payment".',
        "utr_used",
    )


async def _apply(db: AsyncSession, admin: User, order: BillingOrder) -> None:
    payment = BillingPayment(
        razorpay_payment_id=None,
        order_id=order.id,
        user_id=order.user_id,
        username=order.username,
        status="captured",
        amount_paise=order.amount_paise,
        currency="INR",
        method="upi",
        mode="offline",
        offline_reference=order.upi_utr,
        recorded_by=admin.username,
        captured_at=order.upi_submitted_at,
    )
    db.add(payment)
    await db.flush()
    outcome = await payments.apply_matched(db, order, payment, source="admin")
    order.upi_decided_at, order.upi_decided_by = utcnow(), admin.username
    await audit.record_event(
        db,
        source="admin",
        type="upi.confirmed",
        actor=admin.username,
        user_id=order.user_id,
        order_id=order.id,
        payment_id=payment.id,
        detail={
            "reference": order.upi_reference,
            "utr": order.upi_utr,
            "amount": order.amount_paise,
            "outcome": outcome,
        },
    )


async def reject(db: AsyncSession, admin: User, order: BillingOrder, body: UpiRejectRequest) -> AdminOrder:
    """POST /admin/billing/orders/{public_id}/upi-reject: the order is rejected with a reason the customer reads (and
    an email when they have a verified one and the mailer is configured); its UTR is free again."""
    if order.user_id is not None:
        await lock_user(db, order.user_id)
    order = await _locked(db, order.id)
    now = utcnow()
    if order.upi_reference is not None and order.status == "rejected":
        await db.commit()
        return await checkout.admin_order(db, order)
    if order.upi_reference is None or order.status != "submitted":
        detail = f"This payment isn't waiting for a check: it is {_word(order, now)}."
        await db.rollback()
        raise AppError(409, detail, "upi_not_waiting")
    order.status, order.upi_rejection, order.upi_rejection_note = "rejected", body.reason, body.note
    order.upi_decided_at, order.upi_decided_by = now, admin.username
    await audit.record_event(
        db,
        source="admin",
        type="upi.rejected",
        actor=admin.username,
        user_id=order.user_id,
        order_id=order.id,
        detail={
            "reference": order.upi_reference,
            "utr": order.upi_utr,
            "amount": order.amount_paise,
            "reason": body.reason,
            "note": body.note,
        },
    )
    user = await db.get(User, order.user_id) if order.user_id is not None else None
    tell = user is not None and bool(user.email) and outbox.configured()
    if tell and user is not None and user.email:
        params = {"order_id": order.id}
        await outbox.enqueue(
            db, "upi_rejected", to=user.email, canonical=user.email_canonical, user_id=user.id, params=params
        )
    await db.commit()
    if tell:
        await outbox.wake()
    return await checkout.admin_order(db, order)


# ---------------- Expiry (8.5) ----------------


async def expire_due() -> int:
    """The billing loop's tick: pending direct-UPI orders past their expiry become `expired` (reads already treat them
    so), one `order.expired` event each."""
    async with SessionLocal() as db:
        expired = (
            await db.execute(
                update(BillingOrder)
                .where(
                    BillingOrder.upi_reference.is_not(None),
                    BillingOrder.status == "pending",
                    BillingOrder.expires_at <= utcnow(),
                )
                .values(status="expired")
                .returning(BillingOrder.id, BillingOrder.user_id, BillingOrder.public_id)
                .execution_options(synchronize_session=False)
            )
        ).all()
        for order_id, user_id, public_id in expired:
            await audit.record_event(
                db,
                source="system",
                type="order.expired",
                user_id=user_id,
                order_id=order_id,
                detail={"public_id": public_id, "channel": "upi_direct"},
            )
        await db.commit()
    return len(expired)
