"""The business behind the payments (docs/briefs/plans-billing.md 6.9, 6.10 and 10): the billing settings in
app_state, whether a caller can pay now, what the admin and the public pages see of the setup, and the Redis state of
the payment flows (counters, the keys-rejected stop, the reconciler's last run, the worker's wake-up list)."""

import json
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Final

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppState, BillingEvent, BillingOrder, User, utcnow
from app.ops.settings import AlertSettings, load
from app.redis_client import get_redis
from app.schemas_billing import BillingSettings, PaymentsInfo, PaymentsSetup, PublicSite
from app.services.billing import audit

logger = logging.getLogger(__name__)

SETTINGS_KEY: Final = "billing_settings"
WAKE_QUEUE: Final = "rag:billing_wake"
WEBHOOK_PATH: Final = "/api/billing/razorpay/webhook"
RETURN_PATH: Final = "/api/billing/razorpay/return"
WEBHOOK_EVENTS: Final = (
    "payment_link.paid",
    "payment_link.expired",
    "payment_link.cancelled",
    "payment.captured",
    "payment.failed",
    "refund.created",
    "refund.processed",
    "refund.failed",
    "payment.dispute.created",
    "payment.dispute.under_review",
    "payment.dispute.action_required",
    "payment.dispute.won",
    "payment.dispute.lost",
    "payment.dispute.closed",
)
_KEYS_REJECTED = "billing:keys_rejected"
_LAST_RUN = "billing:reconciler:last_run"
_COUNTER_SECONDS = 48 * 3600
_SAMPLE_SECONDS = 60


# ---------------- Settings ----------------


def default_settings() -> BillingSettings:
    return BillingSettings(
        business_name=settings.app_name[:100] or "RAG Assistant",
        address="",
        contact_email=None,
        contact_phone=None,
        receipt_prefix="RA",
        upi_limit_paise=200_000,
    )


async def get_settings(db: AsyncSession) -> BillingSettings:
    row = await db.get(AppState, SETTINGS_KEY)
    if row is None:
        return default_settings()
    try:
        return BillingSettings.model_validate(json.loads(row.value))
    except (ValueError, ValidationError):
        logger.warning("The stored billing settings are invalid; using the defaults")
        return default_settings()


async def save_settings(db: AsyncSession, value: BillingSettings, *, actor: str) -> None:
    """Stores the settings and audits the change (the caller commits)."""
    before = await get_settings(db)
    row = await db.get(AppState, SETTINGS_KEY)
    if row is None:
        row = AppState(key=SETTINGS_KEY, value="")
        db.add(row)
    row.value = value.model_dump_json()
    changed = {
        name: {"before": getattr(before, name), "after": getattr(value, name)}
        for name in BillingSettings.model_fields
        if getattr(before, name) != getattr(value, name)
    }
    await audit.record_event(db, source="admin", type="settings.updated", actor=actor, detail={"changed": changed})


async def public_site(db: AsyncSession) -> PublicSite:
    current = await get_settings(db)
    return PublicSite(
        business_name=current.business_name,
        contact_email=current.contact_email,
        contact_phone=current.contact_phone,
        address=current.address or None,
    )


# ---------------- Who can pay ----------------


def mode() -> str | None:
    """ "test" or "live" by the key id's prefix; None: payments are off."""
    return settings.razorpay_mode


def can_pay(user: User) -> bool:
    """Keys are set, and under test keys the user is a tester (the admin sees what a tester sees, as a preview)."""
    current = mode()
    return current == "live" or (current == "test" and (user.billing_tester or user.is_admin))


def email_reminders() -> bool:
    """Reminder emails can be sent: the monitor's SMTP server and BILLING_EMAIL_FROM are set."""
    return bool(settings.billing_email_from and load(AlertSettings).alert_smtp_host)


async def payments_info(db: AsyncSession, user: User) -> PaymentsInfo:
    current = await get_settings(db)
    available = can_pay(user)
    return PaymentsInfo(
        available=available,
        mode="test" if available and mode() == "test" else "live" if available else None,
        contact_email=current.contact_email,
        upi_limit_paise=current.upi_limit_paise,
        email_reminders=email_reminders(),
    )


def origins() -> list[str]:
    """The app's origins (PUBLIC_BASE_URLS); the first is where emails and unknown returns point."""
    from app.plugins import oauth  # it imports the plugin registry, which imports app.deps

    return oauth.public_base_urls() or ["http://localhost:8080"]


def origin_for(header: str | None) -> str:
    """The request's Origin when it is one of the app's origins, else the first."""
    from app.plugins import oauth

    known = origins()
    normalized = oauth.normalize_origin(header or "")
    return normalized if normalized in known else known[0]


def key_id_hint() -> str | None:
    key = settings.razorpay_key_id
    if not key:
        return None
    prefix = next((p for p in ("rzp_test_", "rzp_live_") if key.startswith(p)), "")
    return f"{prefix}…{key[-4:]}"


def log_startup() -> None:
    """One line about the payments setup; a key id with another prefix turns payments off."""
    if settings.razorpay_key_id and not settings.razorpay_key_id.startswith(("rzp_test_", "rzp_live_")):
        logger.error("RAZORPAY_KEY_ID must start with rzp_test_ or rzp_live_: online payments are off")
    elif mode() is None:
        logger.info("Online payments aren't set up (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)")
    else:
        webhooks = "on" if settings.razorpay_webhook_secret else "off"
        logger.info("Online payments in %s mode (key %s); webhooks %s", mode(), key_id_hint(), webhooks)


def public_https_origin() -> bool:
    """PUBLIC_BASE_URLS holds an https origin that isn't localhost (test keys there hand out free plans to testers
    only, but the site is public)."""
    for origin in origins():
        host = origin.split("://", 1)[1].split(":", 1)[0]
        if origin.startswith("https://") and host not in ("localhost", "127.0.0.1", "[::1]"):
            return True
    return False


async def payments_setup(db: AsyncSession) -> PaymentsSetup:
    current = mode()
    now = utcnow()
    last_webhook = await db.scalar(select(func.max(BillingEvent.created_at)).where(BillingEvent.source == "webhook"))
    test_links = await db.scalar(
        select(func.count())
        .select_from(BillingOrder)
        .where(BillingOrder.mode == "test", BillingOrder.razorpay_link_id.is_not(None))
    )
    paused = await db.scalar(
        select(func.count())
        .select_from(BillingOrder)
        .where(
            BillingOrder.mode.in_(("test", "live")),
            BillingOrder.mode != (current or ""),
            BillingOrder.next_check_at.is_not(None),
            BillingOrder.watch_until > now,
        )
    )
    testers = await db.scalar(select(func.count()).select_from(User).where(User.billing_tester.is_(True)))
    return PaymentsSetup(
        configured=current is not None,
        mode="test" if current == "test" else "live" if current == "live" else None,
        key_id_hint=key_id_hint(),
        webhook_secret_set=bool(settings.razorpay_webhook_secret),
        previous_webhook_secret_set=bool(settings.razorpay_webhook_secret_previous),
        last_webhook_at=_aware(last_webhook),
        webhook_url=origins()[0] + WEBHOOK_PATH,
        webhook_events=list(WEBHOOK_EVENTS),
        test_links_created=test_links or 0,
        reconciler_last_run_at=await reconciler_last_run(),
        last_error=await keys_rejected(),
        paused_orders=paused or 0,
        testers=testers or 0,
        email_reminders=email_reminders(),
    )


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite's max() skips the column type's conversion: a naive value is UTC."""
    return moment.replace(tzinfo=timezone.utc) if moment is not None and moment.tzinfo is None else moment


# ---------------- Redis state ----------------


def hour_key(prefix: str, at: float | None = None) -> str:
    return f"{prefix}:{time.strftime('%Y%m%d%H', time.gmtime(at if at is not None else time.time()))}"


async def count(prefix: str) -> None:
    """Adds one to this hour's counter (kept 48 hours): bad signatures, unknown returns, stale webhooks."""
    key = hour_key(prefix)
    redis = get_redis()
    await redis.incr(key)
    await redis.expire(key, _COUNTER_SECONDS)


async def counted(prefix: str, *, hours: int = 1) -> int:
    """This hour's counter and the `hours - 1` before it."""
    now = time.time()
    keys = [hour_key(prefix, now - hour * 3600) for hour in range(hours)]
    values = await get_redis().mget(keys)
    return sum(int(value) for value in values if value is not None and str(value).isdigit())


async def sample_event(db: AsyncSession, *, source: str, type: str, detail: Mapping[str, object]) -> bool:
    """At most one event of this type a minute (the rest are only counted); the caller commits."""
    if not await get_redis().set(f"billing:sample:{type}", "1", nx=True, ex=_SAMPLE_SECONDS):
        return False
    await audit.record_event(db, source=source, type=type, detail=detail)
    return True


async def wake(event_id: int) -> None:
    """Hands a recorded webhook event to the worker's billing loop."""
    await get_redis().rpush(WAKE_QUEUE, str(event_id))


async def set_keys_rejected(description: str) -> None:
    """Razorpay rejected the keys: polling stops until the next start or the admin's "Check pending payments now"."""
    logger.error("Razorpay rejected the API keys (%s): payment checks are paused", description)
    await get_redis().set(_KEYS_REJECTED, f"Razorpay rejected the API keys: {description}"[:200])


async def keys_rejected() -> str | None:
    value = await get_redis().get(_KEYS_REJECTED)
    return str(value) if value else None


async def clear_keys_rejected() -> None:
    await get_redis().delete(_KEYS_REJECTED)


async def mark_reconciler_run() -> None:
    await get_redis().set(_LAST_RUN, str(time.time()))


async def reconciler_last_run() -> datetime | None:
    value = await get_redis().get(_LAST_RUN)
    try:
        return datetime.fromtimestamp(float(value), timezone.utc) if value else None
    except ValueError:
        return None
