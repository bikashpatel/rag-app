"""A fake Razorpay API for tests (docs/briefs/plans-billing.md 12.1 and 12.2; payments-razorpay-research.md for the
shapes). Pure Python: no app imports. Never the real Razorpay.

`fake_razorpay_app(state)` serves, with Basic auth checked exactly as Razorpay does:

- Payment Links: `POST /v1/payment_links` (reference_id unique, expire_by at least 15 minutes ahead, callback_method
  get), `GET /v1/payment_links/<id>` (its order id and `payments`), `GET /v1/payment_links?reference_id=`, `POST
  /v1/payment_links/<id>/cancel` (only while unpaid);
- payments: `GET /v1/payments/<id>`; refunds: `POST /v1/payments/<id>/refund` (the `X-Refund-Idempotency` header: the
  same key and body answer the same refund, another body is refused), `GET /v1/refunds/<id>`; disputes: `GET
  /v1/disputes/<id>`.

Errors use Razorpay's `{"error": {"code", "description", ...}}`. The test drives the payer and the Dashboard through
`FakeRazorpay`: pay (returns the signed callback query), fail, authorize then capture, tamper with the amount, expire,
refund in the Dashboard, raise and settle a dispute, and build signed webhook bodies (`webhook`). Payment entities carry
the payer's email, contact and VPA, as Razorpay's do, so tests can check none of them is stored. `fail_next` and
`timeout_next` make the next matching request answer an error or time out. The conftest fixture `fake_razorpay` routes
every request to https://api.razorpay.com here.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

API_HOST = "api.razorpay.com"
PAYER = {"email": "payer@example.test", "contact": "+919999999999", "vpa": "payer@okbank", "bank": "HDFC"}


def hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(10).replace('-', 'A').replace('_', 'B')}"


class RazorpayError(Exception):
    def __init__(self, status: int, description: str, code: str = "BAD_REQUEST_ERROR", field: str | None = None):
        super().__init__(description)
        self.status = status
        self.body = {"error": {"code": code, "description": description, "field": field, "source": "business"}}


@dataclass
class _Failure:
    status: int | None  # None: a timeout
    body: dict[str, Any]
    path: str  # a path prefix the request must match ("" matches all)


@dataclass
class FakeRazorpay:
    """The fake's state: entities by id, the requests it served (method, path) and failures to inject."""

    key_id: str = "rzp_test_FakeKeyId0001"
    key_secret: str = "fake-key-secret"
    webhook_secret: str = "fake-webhook-secret"
    refund_status: str = "processed"  # what a new refund answers with: "processed" or "pending"
    links: dict[str, dict[str, Any]] = field(default_factory=dict)
    payments: dict[str, dict[str, Any]] = field(default_factory=dict)
    refunds: dict[str, dict[str, Any]] = field(default_factory=dict)
    disputes: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (body digest, refund id)
    requests: list[tuple[str, str]] = field(default_factory=list)
    _failures: list[_Failure] = field(default_factory=list)

    # ---------------- Signatures ----------------

    def callback_signature(self, link_id: str, reference_id: str, status: str, payment_id: str) -> str:
        return hmac_hex(self.key_secret, f"{link_id}|{reference_id}|{status}|{payment_id}".encode())

    def authorization(self) -> str:
        return "Basic " + base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()

    # ---------------- Failures ----------------

    def fail_next(self, status: int, description: str = "Something went wrong", *, path: str = "") -> None:
        """The next request whose path starts with `path` answers `status` with Razorpay's error body."""
        code = "SERVER_ERROR" if status >= 500 else "BAD_REQUEST_ERROR"
        self._failures.append(_Failure(status, RazorpayError(status, description, code).body, path))

    def timeout_next(self, *, path: str = "") -> None:
        self._failures.append(_Failure(None, {}, path))

    def injected(self, request: httpx.Request) -> httpx.Response | None:
        """The injected failure for this request, if one matches (raises httpx.ReadTimeout for a timeout)."""
        for failure in self._failures:
            if request.url.path.startswith(failure.path):
                self._failures.remove(failure)
                self.requests.append((request.method, request.url.path))
                if failure.status is None:
                    raise httpx.ReadTimeout("The fake Razorpay timed out", request=request)
                return httpx.Response(failure.status, json=failure.body, request=request)
        return None

    # ---------------- The payer ----------------

    def _payment(self, link: dict[str, Any], status: str, amount: int | None, method: str) -> dict[str, Any]:
        payment = {
            "id": _id("pay"),
            "entity": "payment",
            "amount": link["amount"] if amount is None else amount,
            "currency": link["currency"],
            "status": status,
            "order_id": link["order_id"],
            "method": method,
            "captured": status == "captured",
            "amount_refunded": 0,
            "refund_status": None,
            "fee": None,
            "tax": None,
            "error_code": None,
            "error_description": None,
            "error_reason": None,
            "notes": dict(link["notes"]),
            "created_at": int(time.time()),
            **PAYER,
            "card_id": "card_FakeCard0001" if method == "card" else None,
        }
        self.payments[payment["id"]] = payment
        link["payments"].append(payment["id"])
        if status == "captured":
            self.capture(payment["id"])
        return payment

    def callback(self, link_id: str, payment_id: str) -> dict[str, str]:
        """The query Razorpay sends the browser back with to the link's callback_url, correctly signed."""
        link = self.links[link_id]
        query = {
            "razorpay_payment_id": payment_id,
            "razorpay_payment_link_id": link_id,
            "razorpay_payment_link_reference_id": link["reference_id"],
            "razorpay_payment_link_status": link["status"],
        }
        signature = self.callback_signature(link_id, link["reference_id"], link["status"], payment_id)
        return {**query, "razorpay_signature": signature}

    def pay(self, link_id: str, *, method: str = "upi", amount: int | None = None) -> dict[str, str]:
        """A captured payment of the link (of `amount`, to tamper with it); returns the signed callback query."""
        payment = self._payment(self.links[link_id], "captured", amount, method)
        return self.callback(link_id, payment["id"])

    def fail(self, link_id: str, *, method: str = "upi", reason: str = "payment_failed") -> dict[str, Any]:
        """A failed attempt on the link; the link stays payable."""
        payment = self._payment(self.links[link_id], "failed", None, method)
        payment.update(error_code="BAD_REQUEST_ERROR", error_description="The bank declined it.", error_reason=reason)
        return payment

    def authorize(self, link_id: str, *, method: str = "upi") -> dict[str, Any]:
        """An authorised payment, captured later (`capture`): a late capture."""
        return self._payment(self.links[link_id], "authorized", None, method)

    def capture(self, payment_id: str) -> None:
        payment = self.payments[payment_id]
        fee = payment["amount"] * 236 // 10_000  # 2% and the GST on it
        payment.update(status="captured", captured=True, fee=fee, tax=fee * 18 // 118)
        link = next(link for link in self.links.values() if payment_id in link["payments"])
        link["amount_paid"] += payment["amount"]
        link["status"] = "paid"

    def expire(self, link_id: str) -> None:
        self.links[link_id]["status"] = "expired"

    # ---------------- The Dashboard ----------------

    def refund_in_dashboard(
        self, payment_id: str, amount: int | None = None, status: str = "processed"
    ) -> dict[str, Any]:
        return self._refund(payment_id, amount, status, notes={})

    def _refund(self, payment_id: str, amount: int | None, status: str, notes: dict[str, Any]) -> dict[str, Any]:
        payment = self.payments[payment_id]
        if payment["status"] not in ("captured", "refunded") or payment["captured"] is not True:
            raise RazorpayError(400, "The payment has not been captured")
        refundable = payment["amount"] - payment["amount_refunded"]
        amount = refundable if amount is None else amount
        if not 100 <= amount <= refundable:
            raise RazorpayError(400, "The refund amount provided is greater than amount captured", field="amount")
        refund = {
            "id": _id("rfnd"),
            "entity": "refund",
            "amount": amount,
            "currency": payment["currency"],
            "payment_id": payment_id,
            "notes": notes,
            "status": status,
            "speed_requested": "normal",
            "created_at": int(time.time()),
        }
        self.refunds[refund["id"]] = refund
        payment["amount_refunded"] += amount
        payment["refund_status"] = "full" if payment["amount_refunded"] == payment["amount"] else "partial"
        if payment["refund_status"] == "full":
            payment["status"] = "refunded"
        return refund

    def set_refund_status(self, refund_id: str, status: str) -> None:
        self.refunds[refund_id]["status"] = status

    def raise_dispute(
        self, payment_id: str, *, amount: int | None = None, status: str = "open", respond_by: int | None = None
    ) -> dict[str, Any]:
        payment = self.payments[payment_id]
        dispute = {
            "id": _id("disp"),
            "entity": "dispute",
            "payment_id": payment_id,
            "amount": payment["amount"] if amount is None else amount,
            "currency": payment["currency"],
            "reason_code": "chargeback",
            "respond_by": respond_by or int(time.time()) + 7 * 86_400,
            "status": status,
            "phase": "chargeback",
            "created_at": int(time.time()),
        }
        self.disputes[dispute["id"]] = dispute
        return dispute

    def set_dispute_status(self, dispute_id: str, status: str) -> None:
        self.disputes[dispute_id]["status"] = status

    # ---------------- Webhooks ----------------

    def webhook(
        self,
        event: str,
        *,
        link_id: str | None = None,
        payment_id: str | None = None,
        refund_id: str | None = None,
        dispute_id: str | None = None,
        created_at: int | None = None,
        secret: str | None = None,
        event_id: str | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        """A webhook as Razorpay delivers it: the raw body and its headers (signed with the webhook secret, or
        `secret`; a new event id unless given)."""
        payload: dict[str, Any] = {}
        if link_id is not None:
            link = self.links[link_id]
            payload["payment_link"] = {"entity": self.link_view(link)}
            payload["order"] = {"entity": {"id": link["order_id"], "entity": "order", "amount": link["amount"]}}
            payment_id = payment_id or next(iter(link["payments"]), None)
        if payment_id is not None:
            payload["payment"] = {"entity": dict(self.payments[payment_id])}
        if refund_id is not None:
            payload["refund"] = {"entity": dict(self.refunds[refund_id])}
        if dispute_id is not None:
            payload["dispute"] = {"entity": dict(self.disputes[dispute_id])}
        body = json.dumps(
            {
                "entity": "event",
                "account_id": "acc_FakeAccount01",
                "event": event,
                "contains": list(payload),
                "payload": payload,
                "created_at": int(time.time()) if created_at is None else created_at,
            }
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Razorpay-Signature": hmac_hex(secret or self.webhook_secret, body),
            "X-Razorpay-Event-Id": event_id or _id("evt"),
        }
        return body, headers

    # ---------------- The API ----------------

    def link_view(self, link: dict[str, Any]) -> dict[str, Any]:
        payments = [
            {
                "payment_id": payment_id,
                "amount": self.payments[payment_id]["amount"],
                "method": self.payments[payment_id]["method"],
                "status": self.payments[payment_id]["status"],
                "created_at": self.payments[payment_id]["created_at"],
            }
            for payment_id in link["payments"]
        ]
        return {**link, "payments": payments or None}

    def create_link(self, body: dict[str, Any]) -> dict[str, Any]:
        amount, reference = body.get("amount"), body.get("reference_id", "")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 100:
            raise RazorpayError(400, "The amount must be atleast INR 1.00", field="amount")
        if body.get("currency", "INR") != "INR":
            raise RazorpayError(400, "The currency is invalid", field="currency")
        if reference and any(link["reference_id"] == reference for link in self.links.values()):
            raise RazorpayError(400, "payment link creation with reference ID already attempted", field="reference_id")
        if int(body.get("expire_by", 0)) < time.time() + 15 * 60:
            raise RazorpayError(400, "expire by date should be at least 15 minutes ahead", field="expire_by")
        if body.get("callback_url") and body.get("callback_method") != "get":
            raise RazorpayError(400, "callback_method must be get", field="callback_method")
        link_id = _id("plink")
        link = {
            "id": link_id,
            "entity": "payment_link",
            "amount": amount,
            "amount_paid": 0,
            "currency": "INR",
            "accept_partial": bool(body.get("accept_partial", False)),
            "reference_id": reference,
            "description": body.get("description", ""),
            "notes": body.get("notes") or {},
            "expire_by": int(body["expire_by"]),
            "callback_url": body.get("callback_url", ""),
            "callback_method": body.get("callback_method", ""),
            "short_url": f"https://rzp.io/i/{link_id.removeprefix('plink_')[:10]}",
            "status": "created",
            "order_id": _id("order"),
            "payments": [],
            "created_at": int(time.time()),
        }
        self.links[link_id] = link
        return self.link_view(link)

    def cancel_link(self, link_id: str) -> dict[str, Any]:
        link = self.get(self.links, link_id)
        if link["status"] != "created":
            raise RazorpayError(400, "Payment link cannot be cancelled as it is not in created state")
        link["status"] = "cancelled"
        return self.link_view(link)

    def refund(self, payment_id: str, body: dict[str, Any], key: str | None) -> dict[str, Any]:
        self.get(self.payments, payment_id)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if key is not None:
            if len(key) < 10:
                raise RazorpayError(400, "The idempotency key is invalid")
            if key in self.idempotency:
                seen, refund_id = self.idempotency[key]
                if seen != digest:
                    raise RazorpayError(400, "The same idempotency key was used with a different request")
                return self.refunds[refund_id]
        refund = self._refund(payment_id, body.get("amount"), self.refund_status, body.get("notes") or {})
        if key is not None:
            self.idempotency[key] = (digest, refund["id"])
        return refund

    @staticmethod
    def get(entities: dict[str, dict[str, Any]], entity_id: str) -> dict[str, Any]:
        if entity_id not in entities:
            raise RazorpayError(400, "The id provided does not exist")
        return entities[entity_id]


def fake_razorpay_app(state: FakeRazorpay) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable[[Request], Any]) -> Any:
        state.requests.append((request.method, request.url.path))
        if request.headers.get("authorization") != state.authorization():
            return JSONResponse(RazorpayError(401, "Authentication failed").body, status_code=401)
        return await call_next(request)

    @app.exception_handler(RazorpayError)
    async def razorpay_error(_: Request, exc: RazorpayError) -> JSONResponse:
        return JSONResponse(exc.body, status_code=exc.status)

    @app.post("/v1/payment_links")
    async def create_link(request: Request) -> dict[str, Any]:
        return state.create_link(await request.json())

    @app.get("/v1/payment_links")
    async def find_links(reference_id: str = "") -> dict[str, Any]:
        found = [state.link_view(link) for link in state.links.values() if link["reference_id"] == reference_id]
        return {"payment_links": found}

    @app.get("/v1/payment_links/{link_id}")
    async def get_link(link_id: str) -> dict[str, Any]:
        return state.link_view(state.get(state.links, link_id))

    @app.post("/v1/payment_links/{link_id}/cancel")
    async def cancel_link(link_id: str) -> dict[str, Any]:
        return state.cancel_link(link_id)

    @app.get("/v1/payments/{payment_id}")
    async def get_payment(payment_id: str) -> dict[str, Any]:
        return state.get(state.payments, payment_id)

    @app.post("/v1/payments/{payment_id}/refund")
    async def refund(payment_id: str, request: Request) -> dict[str, Any]:
        body = json.loads(await request.body() or b"{}")
        return state.refund(payment_id, body, request.headers.get("x-refund-idempotency"))

    @app.get("/v1/refunds/{refund_id}")
    async def get_refund(refund_id: str) -> dict[str, Any]:
        return state.get(state.refunds, refund_id)

    @app.get("/v1/disputes/{dispute_id}")
    async def get_dispute(dispute_id: str) -> dict[str, Any]:
        return state.get(state.disputes, dispute_id)

    return app
