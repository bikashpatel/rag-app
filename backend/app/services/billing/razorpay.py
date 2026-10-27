"""Razorpay's REST API and its two signatures (docs/briefs/plans-billing.md 6.1; payments-razorpay-research.md 2.5,
3.2 and 7). Plain httpx with Basic auth, JSON and a 10 s timeout; never called inside a database transaction or under
a lock. Errors carry Razorpay's code and description (no secrets), never the key."""

import hashlib
import hmac
import re
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from app.config import RAZORPAY_API_BASE, settings

TIMEOUT_SECONDS: Final = 10.0
_AUTH_FAILED = "authentication failed"
_ENTITY_ID = re.compile(r"^[A-Za-z0-9_]{1,40}$")


class RazorpayError(Exception):
    """A failed call: `status` None when Razorpay couldn't be reached (a timeout or a connection error)."""

    def __init__(self, status: int | None, code: str, description: str) -> None:
        super().__init__(description)
        self.status = status
        self.code = code
        self.description = description[:200]

    @property
    def transient(self) -> bool:
        """Unreachable, rate-limited or a server error: the same request may work later."""
        return self.status is None or self.status == 429 or self.status >= 500

    @property
    def keys_rejected(self) -> bool:
        """The keys are wrong (a 401, or a 400 "Authentication failed"): a configuration error, not the order's."""
        return self.status == 401 or (self.status == 400 and _AUTH_FAILED in self.description.lower())


def entity_id(value: object) -> str | None:
    """A Razorpay id as it may go into a path ("pay_Q2xS9e1LkvM8tz"), else None."""
    return value if isinstance(value, str) and _ENTITY_ID.fullmatch(value) else None


def _path_id(value: str) -> str:
    if entity_id(value) is None:
        raise RazorpayError(400, "BAD_REQUEST_ERROR", "Not a Razorpay id")
    return value


def hmac_sha256_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _same(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode(), given.strip().lower().encode())


def callback_signature_ok(link_id: str, reference_id: str, status: str, payment_id: str, signature: str) -> bool:
    """The Payment Link callback's signature: HMAC-SHA256 with the key secret over "link|reference|status|payment"."""
    if not settings.razorpay_key_secret or not signature:
        return False
    message = f"{link_id}|{reference_id}|{status}|{payment_id}".encode()
    return _same(hmac_sha256_hex(settings.razorpay_key_secret, message), signature)


def webhook_signature_ok(raw_body: bytes, signature: str) -> bool:
    """X-Razorpay-Signature: HMAC-SHA256 of the raw body with the webhook secret, or the previous one after a
    rotation."""
    secrets = [settings.razorpay_webhook_secret, settings.razorpay_webhook_secret_previous]
    return bool(signature) and any(_same(hmac_sha256_hex(secret, raw_body), signature) for secret in secrets if secret)


def pay_url_ok(url: object) -> bool:
    """A link's short_url must be Razorpay's: https on rzp.io or a razorpay.com subdomain. Any https origin only with
    test keys and another API base (a fake)."""
    if not isinstance(url, str) or len(url) > 200:
        return False
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None:
        return False
    if settings.razorpay_mode == "test" and settings.razorpay_api_base != RAZORPAY_API_BASE:
        return True
    return port is None and (host == "rzp.io" or host.endswith(".razorpay.com"))


async def _call(method: str, path: str, *, body: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=settings.razorpay_api_base,
            auth=httpx.BasicAuth(settings.razorpay_key_id, settings.razorpay_key_secret),
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = await client.request(method, f"/v1{path}", json=body, **kwargs)
    except httpx.HTTPError as exc:
        raise RazorpayError(None, "UNREACHABLE", f"Razorpay unreachable ({type(exc).__name__})") from None
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.is_success and isinstance(data, dict):
        return data
    error = data.get("error") if isinstance(data, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or f"HTTP_{response.status_code}")[:64]
    description = str(error.get("description") or f"Razorpay answered HTTP {response.status_code}")
    raise RazorpayError(response.status_code, code, description)


async def create_link(body: dict[str, Any]) -> dict[str, Any]:
    return await _call("POST", "/payment_links", body=body)


async def fetch_link(link_id: str) -> dict[str, Any]:
    return await _call("GET", f"/payment_links/{_path_id(link_id)}")


async def find_links(reference_id: str) -> list[dict[str, Any]]:
    found = (await _call("GET", "/payment_links", params={"reference_id": reference_id})).get("payment_links")
    return [link for link in found or [] if isinstance(link, dict)]


async def cancel_link(link_id: str) -> None:
    await _call("POST", f"/payment_links/{_path_id(link_id)}/cancel")


async def fetch_payment(payment_id: str) -> dict[str, Any]:
    return await _call("GET", f"/payments/{_path_id(payment_id)}")


async def create_refund(payment_id: str, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    return await _call(
        "POST", f"/payments/{_path_id(payment_id)}/refund", body=body, headers={"X-Refund-Idempotency": idempotency_key}
    )


async def fetch_refund(refund_id: str) -> dict[str, Any]:
    return await _call("GET", f"/refunds/{_path_id(refund_id)}")


async def fetch_dispute(dispute_id: str) -> dict[str, Any]:
    return await _call("GET", f"/disputes/{_path_id(dispute_id)}")
