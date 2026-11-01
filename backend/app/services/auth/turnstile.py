"""Cloudflare Turnstile's server-side check (docs/briefs/signup-auth.md 7.2): siteverify with a fresh idempotency key,
retried once with the same key when Cloudflare couldn't answer. The token is never stored or logged; log lines carry
only Cloudflare's error codes."""

import logging
import time
import uuid
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.errors import AppError
from app.redis_client import get_redis

TurnstileAction = Literal["signup", "resend", "reset", "login"]

logger = logging.getLogger(__name__)

TIMEOUT: Final = 10.0
_UNAVAILABLE_KEY = "auth:turnstile:unavailable"  # a sorted set of the last hour's failed verifications (the monitor)
_HOUR = 3600
_OUR_SECRET = frozenset({"missing-input-secret", "invalid-input-secret"})


def configured() -> bool:
    return settings.turnstile_configured


def _failed() -> AppError:
    return AppError(400, "The check expired or failed. Try again.", "challenge_failed")


def _unavailable() -> AppError:
    return AppError(503, "We couldn't check that you're not a robot. Try again in a minute.", "challenge_unavailable")


async def _count_unavailable() -> None:
    now = time.time()
    async with get_redis().pipeline(transaction=True) as pipe:
        pipe.zadd(_UNAVAILABLE_KEY, {uuid.uuid4().hex: now})
        pipe.zremrangebyscore(_UNAVAILABLE_KEY, "-inf", now - _HOUR)
        pipe.expire(_UNAVAILABLE_KEY, _HOUR)
        await pipe.execute()


async def unavailable_last_hour() -> int:
    """Verifications Cloudflare couldn't answer in the last hour (the `mail` monitor check warns from 10)."""
    count: int = await get_redis().zcount(_UNAVAILABLE_KEY, time.time() - _HOUR, "+inf")
    return count


def _codes(answer: dict[str, Any]) -> list[str]:
    codes = answer.get("error-codes")
    return [str(code) for code in codes] if isinstance(codes, list) else []


async def _siteverify(form: dict[str, str]) -> dict[str, Any] | None:
    """Cloudflare's answer, or None when it couldn't give one (a timeout, a 5xx, internal-error, an unreadable body)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(settings.turnstile_siteverify_url, data=form)
        answer = response.json() if response.status_code < 500 else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Turnstile siteverify failed (%s)", type(exc).__name__)
        return None
    if not isinstance(answer, dict) or "internal-error" in _codes(answer):
        logger.warning("Turnstile siteverify couldn't answer (HTTP %d)", response.status_code)
        return None
    return answer


def _our_hosts() -> set[str]:
    from app.plugins import oauth  # it imports the plugin registry, which imports app.deps

    return {host for host in (urlsplit(origin).hostname for origin in oauth.public_base_urls()) if host}


async def verify(token: str | None, action: TurnstileAction, remote_ip: str | None) -> None:
    """Passes when Cloudflare says the token is good, for `action` and one of our hosts (both skipped with a test
    secret, whose answers carry example.com). Raises 400 challenge_failed for a bad, expired or reused token, and 503
    challenge_unavailable when Cloudflare can't tell (counted for the monitor; sign-in then carries on)."""
    if not configured():
        raise _unavailable()
    if not token:
        raise _failed()
    form = {"secret": settings.turnstile_secret_key, "response": token, "idempotency_key": str(uuid.uuid4())}
    if remote_ip:
        form["remoteip"] = remote_ip
    answer = await _siteverify(form) or await _siteverify(form)
    codes = _codes(answer) if answer is not None else []
    if secret_refused := bool(_OUR_SECRET & set(codes)):  # no visitor could pass: our setup, not their token
        logger.error("Turnstile refused TURNSTILE_SECRET_KEY (%s)", ", ".join(codes))
    if answer is None or secret_refused:
        await _count_unavailable()
        raise _unavailable()
    if answer.get("success") is not True:
        logger.info("Turnstile refused a token (%s)", ", ".join(codes) or "no error code")
        raise _failed()
    if settings.turnstile_test_secret:
        return
    if answer.get("action") != action:
        logger.warning("Turnstile token for another action (%r, expected %r)", answer.get("action"), action)
        raise _failed()
    if answer.get("hostname") not in _our_hosts():
        logger.warning("Turnstile token from another hostname (%r)", answer.get("hostname"))
        raise _failed()
