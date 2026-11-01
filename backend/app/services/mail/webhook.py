"""Resend's bounce and complaint webhook (docs/briefs/signup-auth.md 6.6, S42): a Svix-signed POST (HMAC-SHA256 of
"<svix-id>.<svix-timestamp>.<body>" under the base64-decoded MAIL_WEBHOOK_SECRET after its `whsec_` prefix, the
timestamp within 5 minutes), each delivery handled once (its svix-id remembered for 24 hours). A permanent bounce or a
complaint suppresses each recipient's address hash; every other event is acknowledged and ignored. Logs carry the type
and masked addresses only."""

import base64
import binascii
import hashlib
import hmac
import logging
import time
from collections.abc import Mapping
from email.utils import parseaddr
from typing import Any, Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.redis_client import get_redis
from app.services.auth import emails
from app.services.mail import outbox

logger = logging.getLogger(__name__)

MAX_BODY: Final = 64 * 1024
TOLERANCE_SECONDS: Final = 300
REPLAY_SECONDS: Final = 24 * 3600
_NOT_PERMANENT = ("transient", "undetermined")  # bounce types that don't mean the address is gone


class SignatureError(Exception):
    """The request isn't a delivery signed with our secret (or it is too old)."""


def configured() -> bool:
    return bool(settings.mail_webhook_secret)


def _key() -> bytes:
    try:
        return base64.b64decode(settings.mail_webhook_secret.removeprefix("whsec_"))
    except binascii.Error:
        raise SignatureError() from None


def verify(headers: Mapping[str, str], body: bytes, *, now: float | None = None) -> str:
    """The delivery's svix-id when its signature and timestamp are good; raises SignatureError otherwise."""
    msg_id = headers.get("svix-id", "")
    stamp = headers.get("svix-timestamp", "")
    signatures = headers.get("svix-signature", "")
    if not (msg_id and stamp.isdigit() and signatures) or len(msg_id) > 256:
        raise SignatureError()
    if abs((now if now is not None else time.time()) - int(stamp)) > TOLERANCE_SECONDS:
        raise SignatureError()
    digest = hmac.new(_key(), f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    for signature in signatures.split():
        version, _, value = signature.partition(",")
        if version == "v1" and hmac.compare_digest(value.encode(), expected.encode()):
            return msg_id
    raise SignatureError()


def _replay_key(msg_id: str) -> str:
    return f"mail:webhook:{hashlib.sha256(msg_id.encode()).hexdigest()}"


async def first_delivery(msg_id: str) -> bool:
    """True the first time a delivery id is seen in 24 hours (Resend retries with the same id)."""
    return bool(await get_redis().set(_replay_key(msg_id), "1", nx=True, ex=REPLAY_SECONDS))


async def forget(msg_id: str) -> None:
    """A delivery we failed to handle: its retry is handled again."""
    await get_redis().delete(_replay_key(msg_id))


def _recipients(data: Mapping[str, Any]) -> list[str]:
    to = data.get("to")
    values = [to] if isinstance(to, str) else to if isinstance(to, list) else []
    return [parseaddr(value)[1] for value in values if isinstance(value, str)]


def _reason(event: Mapping[str, Any]) -> Literal["bounce", "complaint"] | None:
    kind = event.get("type")
    if kind == "email.complained":
        return "complaint"
    if kind != "email.bounced":
        return None
    data = event.get("data")
    bounce = data.get("bounce") if isinstance(data, dict) else None
    bounce_type = str(bounce.get("type", "")).lower() if isinstance(bounce, dict) else ""
    return None if bounce_type in _NOT_PERMANENT else "bounce"


async def handle(db: AsyncSession, event: Mapping[str, Any]) -> int:
    """Suppresses the recipients of a permanent bounce or a complaint (the caller commits); returns how many."""
    reason = _reason(event)
    data = event.get("data")
    if reason is None or not isinstance(data, dict):
        logger.info("Mail webhook: ignored a %s event", str(event.get("type"))[:40])
        return 0
    count = 0
    for value in _recipients(data):
        try:
            address = emails.parse(value)
        except emails.EmailError:
            continue
        await outbox.suppress(db, emails.digest(address.canonical), reason=reason, source="webhook")
        logger.warning("Mail webhook: %s for %s", event.get("type"), emails.mask(address.address))
        count += 1
    return count
