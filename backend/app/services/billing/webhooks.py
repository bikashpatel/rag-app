"""Razorpay's webhooks (docs/briefs/plans-billing.md 6.4): the endpoint only verifies, deduplicates, records and wakes
the worker; it never changes an order, payment, refund or period from a payload. The worker then fetches the entity
from Razorpay and goes through the same apply as the callback and the reconciler."""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Mapping
from typing import Any, Final

from fastapi import status
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import BillingEvent, BillingOrder, BillingPayment, utcnow
from app.services.billing import business, disputes, razorpay, refunds
from app.services.billing.reconcile import PAYMENT_EVENTS, check_order

logger = logging.getLogger(__name__)

MAX_BODY: Final = 256 * 1024
STALE_SECONDS: Final = 3 * 86_400
MAX_ATTEMPTS: Final = 3
_ENTITIES = ("payment_link", "order", "payment", "refund", "dispute")


def _ok() -> JSONResponse:
    return JSONResponse({"ok": True})


def _entity(payload: object, name: str) -> dict[str, Any]:
    wrapped = payload.get(name) if isinstance(payload, dict) else None
    entity = wrapped.get("entity") if isinstance(wrapped, dict) else None
    return entity if isinstance(entity, dict) else {}


def _detail(event: str, body: dict[str, Any]) -> dict[str, Any]:
    """Ids only, plus the main entity's status, amount, currency and method: never the payer's details."""
    payload = body.get("payload")
    detail: dict[str, Any] = {name: razorpay.entity_id(_entity(payload, name).get("id")) for name in _ENTITIES}
    detail["order"] = detail["order"] or razorpay.entity_id(_entity(payload, "payment").get("order_id"))
    main = next(
        (
            name
            for name in ("payment_link", "refund", "dispute")
            if event.startswith(f"payment.{name}") or event.startswith(name)
        ),
        "payment",
    )
    entity = _entity(payload, main)
    for key in ("status", "amount", "currency", "method"):
        value = entity.get(key)
        if isinstance(value, str | int) and not isinstance(value, bool):
            detail[key] = value if isinstance(value, int) else value[:40]
    return detail


async def receive(db: AsyncSession, raw: bytes, signature: str, event_id: str | None) -> JSONResponse:
    """POST /billing/razorpay/webhook: 200 once recorded (or already recorded, or stale); 400 a bad signature; 413 over
    256 KB; 503 without a webhook secret. A database error propagates as a 500, so Razorpay retries."""
    if not settings.razorpay_webhook_secret:
        await business.count("billing:wh:unconfigured")
        return JSONResponse({"detail": "Webhooks aren't set up."}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    if len(raw) > MAX_BODY:
        return JSONResponse({"detail": "Too large"}, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    if not razorpay.webhook_signature_ok(raw, signature):
        await business.count("billing:wh:badsig")
        await business.sample_event(db, source="webhook", type="webhook.bad_signature", detail={})
        await db.commit()
        return JSONResponse({"detail": "Invalid signature"}, status_code=status.HTTP_400_BAD_REQUEST)
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("event"), str):
        return JSONResponse({"detail": "Not a Razorpay event"}, status_code=status.HTTP_400_BAD_REQUEST)
    created = body.get("created_at")
    if isinstance(created, int | float) and time.time() - created > STALE_SECONDS:
        await business.count("billing:wh:stale")
        return _ok()
    digest = hashlib.sha256(raw).hexdigest()
    event_id = event_id.strip()[:64] if event_id and event_id.strip() else None
    seen = BillingEvent.body_sha256 == digest
    if event_id is not None:
        seen = or_(seen, BillingEvent.razorpay_event_id == event_id)
    if await db.scalar(select(BillingEvent.id).where(seen).limit(1)) is not None:
        await db.commit()
        return _ok()
    kind = body["event"][:48]
    event = BillingEvent(
        source="webhook",
        type=kind,
        razorpay_event_id=event_id,
        body_sha256=digest,
        detail=_detail(kind, body),
        processed=False,
    )
    db.add(event)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return _ok()
    try:
        await business.wake(event.id)
    except Exception as exc:  # the worker's loop picks unwoken events up on its next tick
        logger.warning("Could not wake the billing worker (%s)", type(exc).__name__)
    return _ok()


# ---------------- The worker's processing ----------------


async def _check(db: AsyncSession, event: BillingEvent, order: BillingOrder | None) -> bool:
    """Reconciles the event's order now; when another check of it runs, the reconciler takes it next."""
    if order is None:
        return False
    event.order_id = order.id
    await db.commit()
    if not await check_order(db, order.id, source="webhook"):
        busy = await db.get(BillingOrder, order.id, populate_existing=True)
        if busy is not None and busy.next_check_at is not None:
            busy.next_check_at = utcnow()
        await db.commit()
    return True


class Unmatched(Exception):
    """A refund or dispute of a payment this app doesn't know: counted as a failed attempt."""


async def _handle(db: AsyncSession, event: BillingEvent) -> bool:
    """Processes one event; False when a payment event waits for its order's Razorpay id (matched later)."""
    detail = event.detail or {}
    kind = event.type
    if kind.startswith("payment_link."):
        link_id = razorpay.entity_id(detail.get("payment_link"))
        await _check(db, event, await db.scalar(select(BillingOrder).where(BillingOrder.razorpay_link_id == link_id)))
        return True
    if kind in PAYMENT_EVENTS:
        order_id = razorpay.entity_id(detail.get("order"))
        return await _check(
            db, event, await db.scalar(select(BillingOrder).where(BillingOrder.razorpay_order_id == order_id))
        )
    if kind.startswith("refund."):
        refund_id = razorpay.entity_id(detail.get("refund"))
        if refund_id is None:
            return True
        await db.commit()
        if await refunds.apply(db, await razorpay.fetch_refund(refund_id), source="webhook") == "unknown":
            raise Unmatched("The refund's payment isn't known here")
        return True
    if kind.startswith("payment.dispute."):
        dispute_id = razorpay.entity_id(detail.get("dispute"))
        if dispute_id is None:
            return True
        await db.commit()
        if await disputes.apply(db, await razorpay.fetch_dispute(dispute_id), source="webhook") == "unknown":
            raise Unmatched("The dispute's payment isn't known here")
        return True
    return True


async def process(event_id: int) -> None:
    """The worker takes one woken event: processed once, or its attempts counted with a short error (after 3 it stays
    unprocessed, under "Needs attention"; the reconciler still covers the order)."""
    async with SessionLocal() as db:
        event = await db.get(BillingEvent, event_id)
        if event is None or event.processed or event.source != "webhook" or event.attempts >= MAX_ATTEMPTS:
            return
        try:
            done = await _handle(db, event)
        except Exception as exc:
            await db.rollback()
            if isinstance(exc, razorpay.RazorpayError) and exc.keys_rejected:
                await business.set_keys_rejected(exc.description)
            failed = await db.get(BillingEvent, event_id, populate_existing=True)
            if failed is not None:
                failed.attempts += 1
                known = isinstance(exc, razorpay.RazorpayError | Unmatched)
                failed.error = (str(exc) if known else type(exc).__name__)[:200]
                await db.commit()
            logger.warning("A Razorpay webhook event failed (%s)", type(exc).__name__)
            return
        if done:
            finished = await db.get(BillingEvent, event_id, populate_existing=True)
            if finished is not None:
                finished.processed, finished.error = True, None
                await db.commit()


async def pending(*, limit: int = 50, started: bool = False) -> list[int]:
    """Unprocessed webhook events with attempts left: at start every one; on each tick those a lost wake-up or a
    failure left behind (payment events waiting for their order's Razorpay id only after a failure: a new order id
    wakes them)."""
    async with SessionLocal() as db:
        query = select(BillingEvent.id).where(
            BillingEvent.processed.is_(False),
            BillingEvent.source == "webhook",
            BillingEvent.attempts < MAX_ATTEMPTS,
        )
        if not started:
            query = query.where(BillingEvent.type.not_in(PAYMENT_EVENTS) | (BillingEvent.attempts > 0))
        return list(await db.scalars(query.order_by(BillingEvent.id).limit(limit)))


# ---------------- The return from Razorpay's page ----------------

RETURN_FIELDS: Final = (
    "razorpay_payment_id",
    "razorpay_payment_link_id",
    "razorpay_payment_link_reference_id",
    "razorpay_payment_link_status",
    "razorpay_signature",
)
RETURN_FETCH_SECONDS: Final = 8.0


async def returned(db: AsyncSession, query: Mapping[str, str]) -> str:
    """GET /billing/razorpay/return (no session; the cookie is SameSite=Strict): where to send the browser. The query
    only locates the order: an unknown link goes to the plan page, a bad signature to the order's return page with
    check=failed, and a paid one is checked with Razorpay first (at most 8 s) unless it was applied already."""
    values = {name: value if len(value := query.get(name) or "") <= 64 else "" for name in RETURN_FIELDS}
    payment_id, link_id, reference, link_status, signature = (values[name] for name in RETURN_FIELDS)
    order = None
    if razorpay.entity_id(link_id):
        order = await db.scalar(select(BillingOrder).where(BillingOrder.razorpay_link_id == link_id))
    if order is None or reference != f"rag-{order.public_id}":
        await business.count("billing:cb:unknown")
        detail = {"link": razorpay.entity_id(link_id)}
        await business.sample_event(db, source="callback", type="callback.unknown", detail=detail)
        await db.commit()
        return f"{business.origins()[0]}/plans?payment=unknown"
    target = f"{order.origin}/billing/return?order={order.public_id}"
    if not razorpay.callback_signature_ok(link_id, reference, link_status, payment_id, signature):
        await business.count("billing:cb:badsig")
        detail = {"link": link_id}
        await business.sample_event(db, source="callback", type="callback.bad_signature", detail=detail)
        await db.commit()
        return target + "&check=failed"
    applied = await db.scalar(
        select(BillingPayment.id).where(
            BillingPayment.razorpay_payment_id == payment_id, BillingPayment.applied_at.is_not(None)
        )
    )
    order_id, paid = order.id, order.status == "paid"
    await db.commit()
    if link_status != "paid" or paid or applied is not None:
        return target
    try:
        async with asyncio.timeout(RETURN_FETCH_SECONDS):
            await check_order(db, order_id, source="callback")
    except Exception as exc:
        await db.rollback()
        logger.warning("Checking a returned payment failed (%s)", type(exc).__name__)
    late = await db.get(BillingOrder, order_id, populate_existing=True)
    if late is not None and late.status != "paid" and late.next_check_at is not None:
        late.next_check_at = utcnow()  # not confirmed yet: the reconciler checks it next
    await db.commit()
    return target
