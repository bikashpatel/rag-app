"""The backend tests' fake Razorpay (backend/tests/fake_razorpay.py, mounted read-only at /fake_razorpay_src) as a
server for the integration suite, plus the hosted payment page and the test's controls. Never the real Razorpay.

    python /fake_razorpay/server.py --host 0.0.0.0 --port 8000 \\
        --webhook-url http://frontend/api/billing/razorpay/webhook

The keys and webhook secrets come from the environment (FAKE_RAZORPAY_KEY_ID, FAKE_RAZORPAY_KEY_SECRET,
FAKE_RAZORPAY_WEBHOOK_SECRET, FAKE_RAZORPAY_WEBHOOK_SECRET_PREVIOUS), the same values the backend gets.

- `/v1/...`: the REST API the backend calls (Payment Links, payments, refunds, disputes), with Basic auth checked.
- `/i/<short id>`: the hosted page a pay_url (`https://rzp.io/i/<short id>`) stands for, with Pay, Fail and Pay late
  buttons. Pay captures a payment and redirects (303) to the link's callback_url with a correctly signed query; Fail
  records a failed attempt and stays on the page; Pay late authorises a payment that `/_control/capture` captures.
- `/_control/...`: what the test does as the payer or in Razorpay's Dashboard (pay with another amount, capture, fail
  the next API call, expire a link, refund, raise and settle a dispute) and webhooks delivered to the app, signed with
  the current, the previous or a wrong secret, possibly duplicated, replayed under a new event id, stale or out of
  order.
- `GET /_state`: the links, payments, refunds and disputes (ids, statuses, amounts) and the API requests served.
"""

import argparse
import html
import os
import sys
import time
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, os.environ.get("FAKE_RAZORPAY_SRC", "/fake_razorpay_src"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fake_razorpay import FakeRazorpay, RazorpayError, fake_razorpay_app  # noqa: E402
from fastapi import FastAPI, Form, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class Pay(BaseModel):
    link_id: str
    amount: int | None = None
    method: str = "upi"


class LinkRef(BaseModel):
    link_id: str


class PaymentRef(BaseModel):
    payment_id: str


class Refund(BaseModel):
    payment_id: str
    amount: int | None = None
    status: str = "processed"


class Status(BaseModel):
    id: str
    status: str


class Dispute(BaseModel):
    payment_id: str
    status: str = "open"
    respond_by: int | None = None


class FailNext(BaseModel):
    status: int
    description: str = "Something went wrong"
    path: str = ""


class Webhook(BaseModel):
    event: str
    link_id: str | None = None
    payment_id: str | None = None
    refund_id: str | None = None
    dispute_id: str | None = None
    created_at: int | None = None
    secret: str = "current"  # current | previous | wrong
    copies: int = 1  # the same event id and body this many times
    new_ids: int = 0  # then the same body under this many new event ids


class Settings(BaseModel):
    refund_status: str


def _short(link: dict[str, Any]) -> str:
    return str(link["short_url"]).rsplit("/", 1)[-1]


def _summary(fake: FakeRazorpay) -> dict[str, Any]:
    return {
        "links": [
            {
                "id": link["id"],
                "reference_id": link["reference_id"],
                "status": link["status"],
                "amount": link["amount"],
                "order_id": link["order_id"],
                "short_url": link["short_url"],
                "payments": list(link["payments"]),
            }
            for link in fake.links.values()
        ],
        "payments": [
            {"id": item["id"], "status": item["status"], "amount": item["amount"], "order_id": item["order_id"]}
            for item in fake.payments.values()
        ],
        "refunds": [
            {"id": item["id"], "payment_id": item["payment_id"], "status": item["status"], "amount": item["amount"]}
            for item in fake.refunds.values()
        ],
        "disputes": [
            {"id": item["id"], "payment_id": item["payment_id"], "status": item["status"]}
            for item in fake.disputes.values()
        ],
        "requests": [f"{method} {path}" for method, path in fake.requests],
    }


def _page(link: dict[str, Any]) -> str:
    short = html.escape(_short(link))
    rupees = f"₹{link['amount'] / 100:,.2f}"
    buttons = "".join(
        f'<form method="post" action="/i/{short}/{action}"><button>{label}</button></form>'
        for action, label in (("pay", "Pay"), ("fail", "Fail"), ("late", "Pay late"))
    )
    return (
        "<!doctype html><meta charset=utf-8><title>Fake Razorpay</title>"
        f"<h1>{html.escape(link['description'])}</h1><p>{rupees} · {html.escape(link['status'])}</p>{buttons}"
    )


def build_app(fake: FakeRazorpay, webhook_url: str, previous_secret: str) -> FastAPI:
    app = FastAPI()

    def link_by_short(short: str) -> dict[str, Any]:
        link = next((link for link in fake.links.values() if _short(link) == short), None)
        if link is None:
            raise HTTPException(404, "No such payment link")
        return link

    def link(link_id: str) -> dict[str, Any]:
        if link_id not in fake.links:
            raise HTTPException(404, "No such payment link")
        return fake.links[link_id]

    @app.exception_handler(RazorpayError)
    async def razorpay_error(_: Request, exc: RazorpayError) -> JSONResponse:
        return JSONResponse(exc.body, status_code=exc.status)

    @app.get("/i/{short}", response_class=HTMLResponse)
    async def page(short: str) -> str:
        return _page(link_by_short(short))

    @app.post("/i/{short}/pay")
    async def pay(short: str, method: str = Form("upi")) -> RedirectResponse:
        found = link_by_short(short)
        query = fake.pay(found["id"], method=method)
        return RedirectResponse(f"{found['callback_url']}?{urlencode(query)}", status_code=303)

    @app.post("/i/{short}/fail")
    async def fail(short: str) -> RedirectResponse:
        fake.fail(link_by_short(short)["id"])
        return RedirectResponse(f"/i/{short}", status_code=303)

    @app.post("/i/{short}/late")
    async def late(short: str) -> dict[str, str]:
        return {"payment_id": fake.authorize(link_by_short(short)["id"])["id"]}

    @app.get("/_state")
    async def state() -> dict[str, Any]:
        return _summary(fake)

    @app.post("/_control/pay")
    async def control_pay(body: Pay) -> dict[str, Any]:
        found = link(body.link_id)
        query = fake.pay(found["id"], method=body.method, amount=body.amount)
        return {"query": query, "callback_url": f"{found['callback_url']}?{urlencode(query)}"}

    @app.post("/_control/fail")
    async def control_fail(body: LinkRef) -> dict[str, str]:
        return {"payment_id": fake.fail(link(body.link_id)["id"])["id"]}

    @app.post("/_control/authorize")
    async def control_authorize(body: LinkRef) -> dict[str, str]:
        return {"payment_id": fake.authorize(link(body.link_id)["id"])["id"]}

    @app.post("/_control/capture")
    async def control_capture(body: PaymentRef) -> dict[str, bool]:
        fake.get(fake.payments, body.payment_id)
        fake.capture(body.payment_id)
        return {"ok": True}

    @app.post("/_control/expire")
    async def control_expire(body: LinkRef) -> dict[str, bool]:
        fake.expire(link(body.link_id)["id"])
        return {"ok": True}

    @app.post("/_control/refund")
    async def control_refund(body: Refund) -> dict[str, Any]:
        return fake.refund_in_dashboard(body.payment_id, body.amount, body.status)

    @app.post("/_control/refund-status")
    async def control_refund_status(body: Status) -> dict[str, bool]:
        fake.get(fake.refunds, body.id)
        fake.set_refund_status(body.id, body.status)
        return {"ok": True}

    @app.post("/_control/dispute")
    async def control_dispute(body: Dispute) -> dict[str, Any]:
        fake.get(fake.payments, body.payment_id)
        return fake.raise_dispute(body.payment_id, status=body.status, respond_by=body.respond_by)

    @app.post("/_control/dispute-status")
    async def control_dispute_status(body: Status) -> dict[str, bool]:
        fake.get(fake.disputes, body.id)
        fake.set_dispute_status(body.id, body.status)
        return {"ok": True}

    @app.post("/_control/fail-next")
    async def control_fail_next(body: FailNext) -> dict[str, bool]:
        fake.fail_next(body.status, body.description, path=body.path)
        return {"ok": True}

    @app.post("/_control/settings")
    async def control_settings(body: Settings) -> dict[str, bool]:
        fake.refund_status = body.refund_status
        return {"ok": True}

    @app.post("/_control/webhook")
    async def control_webhook(body: Webhook) -> dict[str, Any]:
        secret = {"current": None, "previous": previous_secret, "wrong": "not-the-webhook-secret"}[body.secret]
        raw, headers = fake.webhook(
            body.event,
            link_id=body.link_id,
            payment_id=body.payment_id,
            refund_id=body.refund_id,
            dispute_id=body.dispute_id,
            created_at=body.created_at,
            secret=secret,
        )
        deliveries = [headers] * body.copies + [
            {**headers, "X-Razorpay-Event-Id": f"evt_Replay{index}{int(time.time() * 1000)}"}
            for index in range(body.new_ids)
        ]
        statuses = []
        async with httpx.AsyncClient(timeout=10) as client:
            for delivery in deliveries:
                response = await client.post(webhook_url, content=raw, headers=delivery)
                statuses.append(response.status_code)
        return {"statuses": statuses, "event_id": headers["X-Razorpay-Event-Id"]}

    app.mount("/", fake_razorpay_app(fake))  # the API, Basic auth checked; after the routes above
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="The fake Razorpay with a hosted page and controls, for tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--webhook-url", required=True)
    args = parser.parse_args()
    fake = FakeRazorpay(
        key_id=os.environ["FAKE_RAZORPAY_KEY_ID"],
        key_secret=os.environ["FAKE_RAZORPAY_KEY_SECRET"],
        webhook_secret=os.environ["FAKE_RAZORPAY_WEBHOOK_SECRET"],
    )
    app = build_app(fake, args.webhook_url, os.environ["FAKE_RAZORPAY_WEBHOOK_SECRET_PREVIOUS"])
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
