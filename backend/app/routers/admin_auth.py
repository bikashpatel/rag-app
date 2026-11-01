"""The admin's sign-up controls and the mailer's test (SPEC.md section 6, Admin, "Sign-up"; docs/briefs/signup-auth.md
S27, 13.9): open or close sign-up and its methods, with what makes it work (mail, Turnstile, Google, payments) and the
warnings to read before opening it."""

import hashlib
import hmac
import ipaddress
from datetime import timedelta
from typing import Final
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_admin
from app.errors import AppError
from app.models import BillingPayment, User, utcnow
from app.plugins import oauth
from app.redis_client import get_redis
from app.schemas_auth import Accepted, MailTest, SignupSettings, SignupStatus, SignupWarning
from app.services.auth import login, passwords, signup_settings, throttle
from app.services.billing import paid
from app.services.mail import outbox

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])

BREACH_CHECK_SECONDS: Final = 86_400  # ADMIN_PASSWORD's breach check, at most once a day
_SELF = ("email", "google")


def _real_ip_missing(request: Request) -> bool:
    """The admin's request reached a public https host from a private address: nginx doesn't pass visitors'
    addresses (NGINX_REAL_IP_FROM), so every per-network limit would be one global limit."""
    public_hosts = {urlsplit(base).hostname for base in oauth.public_base_urls() if base.startswith("https://")}
    host = urlsplit(f"//{request.headers.get('host', '')}").hostname
    try:
        client = ipaddress.ip_address(throttle.client_ip(request))
    except ValueError:
        return False
    return host in public_hosts and (client.is_private or client.is_loopback)


async def _admin_password_breached() -> bool:
    """Have I Been Pwned on ADMIN_PASSWORD, its answer kept a day (keyed by an HMAC of the password, so a new one is
    checked at once); a failed check counts as not breached and is tried again within the hour."""
    key = (
        "auth:admin_breach:"
        + hmac.new(settings.secret_key.encode(), settings.admin_password.encode(), hashlib.sha256).hexdigest()[:16]
    )
    redis = get_redis()
    cached = await redis.get(key)
    if cached is not None:
        return cached == "1"
    found = await passwords.breached(settings.admin_password)
    await redis.set(key, "1" if found else "0", ex=BREACH_CHECK_SECONDS if found is not None else 3600)
    return bool(found)


async def _status(db: AsyncSession, request: Request) -> SignupStatus:
    current = await signup_settings.load(db)
    mail = await outbox.mail_status(db)
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    recent = select(User.id).where(User.signup_source.in_(_SELF), User.created_at >= now - timedelta(days=7))
    signups_today = await db.scalar(
        select(func.count()).select_from(User).where(User.signup_source.in_(_SELF), User.created_at >= day_start)
    )
    signups_7d = await db.scalar(select(func.count()).select_from(recent.subquery()))
    paid_7d = await db.scalar(
        select(func.count(func.distinct(BillingPayment.user_id))).where(
            BillingPayment.user_id.in_(recent),
            BillingPayment.applied_at.is_not(None),
            BillingPayment.mode.in_(("live", "offline")),
        )
    )
    payments_live = await paid.payments_live()
    warnings: list[SignupWarning] = []
    if _real_ip_missing(request):
        warnings.append("real_ip")
    if not payments_live:
        warnings.append("payments_not_live")
    if current.open and not settings.mail_webhook_secret:
        warnings.append("mail_webhook_missing")
    if passwords.weak_admin_password(settings.admin_password) or await _admin_password_breached():
        warnings.append("admin_password_weak")
    return SignupStatus(
        settings=current,
        effective=await signup_settings.availability(db),
        mail_configured=settings.mail_configured,
        mail_webhook_configured=bool(settings.mail_webhook_secret),
        turnstile_configured=settings.turnstile_configured,
        google_configured=settings.google_signin_configured,
        payments_live=payments_live,
        mail_sent_today=mail.sent_today,
        mail_queued=mail.queued,
        mail_daily_limit=mail.limit,
        public_mail_left=mail.public_left,
        public_paused=mail.public_paused,
        bounces_today=mail.bounces_today,
        complaints_7d=mail.complaints_7d,
        mail_last_error=mail.last_error,
        signups_today=int(signups_today or 0),
        signups_7d=int(signups_7d or 0),
        paid_7d=int(paid_7d or 0),
        warnings=warnings,
    )


@router.get("/signup-settings")
async def get_signup_settings(request: Request, db: AsyncSession = Depends(get_db)) -> SignupStatus:
    return await _status(db, request)


@router.put("/signup-settings")
async def put_signup_settings(
    body: SignupSettings,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> SignupStatus:
    """An omitted field takes its default; a signup.settings event records the values before and after. Closing
    sign-up leaves existing accounts working."""
    await signup_settings.save(db, body, actor=f"admin:{admin.username}")
    await db.commit()
    return await _status(db, request)


@router.post("/mail/test", status_code=status.HTTP_202_ACCEPTED)
async def send_test_mail(
    body: MailTest, request: Request, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
) -> Accepted:
    """A `test` mail to the typed address (5 an hour)."""
    if not outbox.configured():
        raise AppError(503, "Email isn't set up: set MAIL_SMTP_HOST and MAIL_FROM in .env.", "mail_unavailable")
    address = login.parse_email(body.to)
    await throttle.check(request, "mail_test", subject=str(admin.id))
    await outbox.enqueue(db, "test", to=address.address, canonical=address.canonical)
    await db.commit()
    await outbox.wake()
    return Accepted()
