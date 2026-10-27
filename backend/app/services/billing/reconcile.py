"""The reconciler and the worker's billing loop (docs/briefs/plans-billing.md B16 and 6.4): open checkouts are polled
every 30 s while their link lives, then on a backing-off schedule until BILLING_LATE_CAPTURE_DAYS after it expired;
woken webhook events are processed; pending refunds are followed up; reminders go out hourly. One check of an order
at a time (a Redis lock shared with the callback and "Check now"), and Razorpay is never called inside a transaction.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from datetime import datetime, timedelta
from typing import Any, Final

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import BillingEvent, BillingOrder, BillingPayment, utcnow
from app.redis_client import get_redis
from app.services.billing import audit, business, payments, razorpay

logger = logging.getLogger(__name__)

LOCK_SECONDS: Final = 15
REFRESH_SECONDS: Final = 10  # a user's refreshes of one order: at most one check this often
REFRESH_BUDGET: Final = 30  # and checks an hour per user
BATCH: Final = 20
LIVE_EVERY: Final = timedelta(seconds=30)
IN_FLIGHT_EVERY: Final = timedelta(hours=1)
AFTER_EXPIRY: Final = (timedelta(minutes=1), timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
MAX_BACKOFF: Final = timedelta(minutes=15)
REMINDER_EVERY_SECONDS: Final = 3600
WATCHED: Final = ("pending", "expired", "cancelled")
PAYMENT_EVENTS: Final = ("payment.captured", "payment.failed")
_IN_FLIGHT = ("created", "authorized")


def next_check(order: BillingOrder, now: datetime, *, in_flight: bool) -> datetime | None:
    """When to look at the order again: every 30 s while its link lives, a minute after it expired, then 1 h, 6 h and
    24 h after that and daily (hourly while a payment is in flight), until watch_until; None when it is final."""
    if order.status in ("paid", "review") or now >= order.watch_until:
        return None
    if now + LIVE_EVERY < order.expires_at:
        return now + LIVE_EVERY
    steps = [order.expires_at + step for step in AFTER_EXPIRY]
    after = next((step for step in steps if step > now), None)
    if after is None:
        days = (now - order.expires_at) // timedelta(days=1) + 1
        after = order.expires_at + timedelta(days=days)
    if in_flight:
        after = min(after, now + IN_FLIGHT_EVERY)
    return min(after, order.watch_until)


async def _rematch(order: BillingOrder) -> None:
    """Payment events that arrived before the order's Razorpay order id was known are woken again."""
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(BillingEvent).where(
                BillingEvent.processed.is_(False),
                BillingEvent.source == "webhook",
                BillingEvent.type.in_(PAYMENT_EVENTS),
            )
        )
        found = [event.id for event in rows if (event.detail or {}).get("order") == order.razorpay_order_id]
    for event_id in found:
        await business.wake(event_id)


async def _failed(db: AsyncSession, order_id: int, exc: razorpay.RazorpayError) -> None:
    """Razorpay unreachable, rate-limited or refusing: the next check doubles from 30 s up to 15 minutes. Rejected keys
    stop every order's checks (a configuration error, not this order's)."""
    if exc.keys_rejected:
        await business.set_keys_rejected(exc.description)
    order = await db.get(BillingOrder, order_id, populate_existing=True)
    if order is None:
        return
    now = utcnow()
    order.check_step += 1
    order.checks += 1
    order.last_checked_at = now
    order.last_error = (
        "Razorpay unreachable" if exc.status is None else f"Razorpay answered {exc.status} ({exc.code})"
    )[:200]
    delay = min(LIVE_EVERY * 2**order.check_step, MAX_BACKOFF)
    order.next_check_at = None if now >= order.watch_until else now + delay
    await db.commit()
    logger.info("Checking a payment link failed (%s %s); next try in %s", exc.status, exc.code, delay)


async def _fetch(order: BillingOrder, recorded: dict[str, tuple[str, bool]]) -> tuple[dict[str, Any], list[dict], bool]:
    """The link and the payments to look at: those not yet recorded with the status the link lists (applied ones are
    final). Payments created or authorized are in flight: nothing to fetch yet."""
    link_id = order.razorpay_link_id
    if link_id is None:
        found = await razorpay.find_links(f"rag-{order.public_id}")
        link_id = next((razorpay.entity_id(link.get("id")) for link in found), None)
        if link_id is None:
            return {}, [], False
    link = await razorpay.fetch_link(link_id)
    entities: list[dict] = []
    in_flight = False
    for item in link.get("payments") or []:
        payment_id = razorpay.entity_id(item.get("payment_id")) if isinstance(item, dict) else None
        if payment_id is None:
            continue
        listed = item.get("status")
        known = recorded.get(payment_id)
        if known is not None and (known[1] or known[0] == listed):
            continue
        if listed in _IN_FLIGHT:
            in_flight = True
            continue
        entities.append(await razorpay.fetch_payment(payment_id))
    return {**link, "id": link_id}, entities, in_flight


async def _check(db: AsyncSession, order_id: int, source: str) -> bool:
    order = await db.get(BillingOrder, order_id, populate_existing=True)
    if order is None or order.mode != settings.razorpay_mode or await business.keys_rejected():
        await db.commit()
        return False
    recorded = {
        payment.razorpay_payment_id: (payment.status, payment.applied_at is not None)
        for payment in await db.scalars(select(BillingPayment).where(BillingPayment.order_id == order.id))
        if payment.razorpay_payment_id
    }
    await db.commit()
    try:
        link, entities, in_flight = await _fetch(order, recorded)
    except razorpay.RazorpayError as exc:
        await _failed(db, order_id, exc)
        return True
    razorpay_order = razorpay.entity_id(link.get("order_id"))
    order = await db.get(BillingOrder, order_id, populate_existing=True)
    assert order is not None
    learned = razorpay_order is not None and order.razorpay_order_id != razorpay_order
    if link.get("id") and order.razorpay_link_id is None:
        order.razorpay_link_id = link["id"]
    if learned:
        order.razorpay_order_id = razorpay_order
    await db.commit()
    if learned:
        await _rematch(order)
    for entity in entities:
        if entity.get("status") in _IN_FLIGHT:
            in_flight = True
        elif razorpay.entity_id(entity.get("id")) is not None:
            await payments.apply_payment(db, order_id, entity, source=source)
    order = await db.get(BillingOrder, order_id, populate_existing=True)
    assert order is not None
    now = utcnow()
    link_status = link.get("status")
    if order.status == "pending" and link_status in ("expired", "cancelled"):
        order.status = "expired" if link_status == "expired" else "cancelled"
        await audit.record_event(
            db,
            source=source,
            type=f"order.{order.status}",
            user_id=order.user_id,
            order_id=order.id,
            detail={"public_id": order.public_id, "link_status": link_status},
        )
    if not link and order.expires_at < now and order.status == "pending":
        order.status = "expired"
    order.checks += 1
    order.check_step = 0
    order.last_checked_at = now
    order.last_error = None
    order.next_check_at = next_check(order, now, in_flight=in_flight)
    live = order.status == "cancelled" and link_status == "created" and not in_flight
    await db.commit()
    if live:
        await _cancel_live_link(db, order_id, link["id"], source)
    return True


async def _cancel_live_link(db: AsyncSession, order_id: int, link_id: str, source: str) -> None:
    """A cancelled order's link that is still payable (its cancel failed, or the order was replaced while the link
    was made) is cancelled now, best effort; the next check tries again."""
    try:
        await razorpay.cancel_link(link_id)
    except razorpay.RazorpayError as exc:
        logger.info("Cancelling a cancelled order's payment link failed (%s %s)", exc.status, exc.code)
        return
    order = await db.get(BillingOrder, order_id, populate_existing=True)
    await audit.record_event(
        db,
        source=source,
        type="order.link_cancelled",
        user_id=order.user_id if order else None,
        order_id=order_id,
        detail={"link": link_id},
    )
    await db.commit()


async def may_refresh(user_id: int, order_id: int) -> bool:
    """Whether a user's ?refresh=true may check the order with Razorpay now: once every REFRESH_SECONDS per order
    and REFRESH_BUDGET times an hour per user (the admin's "Check now" isn't limited). Otherwise the stored view is
    answered; the reconciler and the webhooks keep confirming payments."""
    redis = get_redis()
    if not await redis.set(f"billing:refresh-recent:{order_id}", "1", nx=True, ex=REFRESH_SECONDS):
        return False
    budget = f"billing:refresh-budget:{user_id}"
    used = await redis.incr(budget)
    if used == 1:
        await redis.expire(budget, 3600)
    return used <= REFRESH_BUDGET


async def check_order(db: AsyncSession, order_id: int, *, source: str) -> bool:
    """Fetches the order's link and new payments from Razorpay and applies what was captured. False when another
    check of the order runs, the order is of the other mode (paused) or the keys were rejected."""
    lock = f"billing:refresh:{order_id}"
    redis = get_redis()
    if not await redis.set(lock, "1", nx=True, ex=LOCK_SECONDS):
        return False
    try:
        return await _check(db, order_id, source)
    finally:
        with contextlib.suppress(RedisError, OSError):
            await redis.delete(lock)


async def run_due(*, limit: int | None = BATCH, source: str = "reconcile") -> int:
    """Checks the orders whose next check is due, oldest first (orders of the other mode are paused)."""
    if settings.razorpay_mode is None or await business.keys_rejected():
        return 0
    async with SessionLocal() as db:
        query = (
            select(BillingOrder.id)
            .where(
                BillingOrder.next_check_at.is_not(None),
                BillingOrder.next_check_at <= utcnow(),
                BillingOrder.status.in_(WATCHED),
                BillingOrder.mode == settings.razorpay_mode,
            )
            .order_by(BillingOrder.next_check_at, BillingOrder.id)
        )
        ids = list(await db.scalars(query.limit(limit) if limit else query))
        await db.commit()
        checked = 0
        for order_id in ids:
            checked += await check_order(db, order_id, source=source)
    return checked


class BillingLoop:
    """Runs in the worker beside the ingestion jobs: woken webhook events, the reconciler's tick every
    BILLING_RECONCILE_SECONDS, refunds, and the hourly reminders. Every step catches its own errors."""

    def __init__(self, stopping: asyncio.Event) -> None:
        self._stopping = stopping
        self._next_reminders = 0.0

    @staticmethod
    def wanted() -> bool:
        """Payments are set up, or reminder emails can be sent: otherwise the loop would have nothing to do."""
        return settings.razorpay_mode is not None or business.email_reminders()

    async def run(self) -> None:
        from app.services.billing import webhooks  # webhooks imports this module

        with contextlib.suppress(Exception):
            await business.clear_keys_rejected()  # a new start tries the keys again
        for event_id in await self._safely("requeue", webhooks.pending(started=True)) or []:
            with contextlib.suppress(Exception):
                await business.wake(event_id)
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while not self._stopping.is_set():
            timeout = max(1, min(5, settings.billing_reconcile_seconds))
            try:
                item = await get_redis().blpop([business.WAKE_QUEUE], timeout=timeout)
            except (RedisError, OSError) as exc:
                logger.warning("Redis unavailable for the billing loop (%s)", type(exc).__name__)
                await self._pause(5)
                continue
            if item is not None and str(item[1]).isdigit():
                await self._safely("webhook", webhooks.process(int(item[1])))
            if loop.time() >= next_tick:
                await self.tick()
                next_tick = loop.time() + settings.billing_reconcile_seconds

    async def tick(self) -> None:
        from app.services.billing import refunds, reminders, webhooks

        await self._safely("reconcile", run_due())
        for event_id in await self._safely("events", webhooks.pending()) or []:
            await self._safely("webhook", webhooks.process(event_id))
        await self._safely("refunds", refunds.retry_creating())
        await self._safely("refunds", refunds.refresh_pending())
        if loop_time() >= self._next_reminders:
            await self._safely("reminders", reminders.send_due())
            self._next_reminders = loop_time() + REMINDER_EVERY_SECONDS
        await self._safely("heartbeat", business.mark_reconciler_run())

    async def _pause(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), seconds)

    @staticmethod
    async def _safely(step: str, work: Awaitable[Any]) -> Any:
        try:
            return await work
        except Exception:
            logger.exception("The billing loop's %s step failed", step)
            return None


def loop_time() -> float:
    return asyncio.get_running_loop().time()
