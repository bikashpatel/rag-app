"""Helpers of the payment tests (test_billing_*.py): a tester checking out through the fake Razorpay (the conftest
fixture `fake_razorpay`), paying and coming back, webhooks as Razorpay sends them, the worker's processing, and reading
the billing rows."""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from conftest import ApiClient, make_tester
from fake_razorpay import FakeRazorpay
from sqlalchemy import select

from app import db
from app.models import BillingEvent, BillingOrder, BillingPayment, Plan, PlanPeriod
from app.redis_client import get_redis
from app.services.billing import business, webhooks

CreateUser = Callable[..., Awaitable[ApiClient]]
RETURN = "/api/billing/razorpay/return"
WEBHOOK = "/api/billing/razorpay/webhook"


async def plan_id(code: str) -> int:
    async with db.SessionLocal() as session:
        found = await session.scalar(select(Plan.id).where(Plan.code == code))
    assert found is not None, code
    return found


async def new_tester(create_user: CreateUser, name: str, *, plan: str | None = None) -> ApiClient:
    """A user who may pay under test keys, without a plan unless given."""
    client = await create_user(name, plan=plan)
    assert client.user_id is not None
    await make_tester(client.user_id)
    return client


async def checkout(client: ApiClient, code: str, kind: str = "period") -> httpx.Response:
    body = {"plan_id": await plan_id(code), "kind": kind, "accept_terms": True}
    return await client.post("/api/billing/checkout", json=body)


async def order_row(public_id: str) -> BillingOrder:
    async with db.SessionLocal() as session:
        found = await session.scalar(select(BillingOrder).where(BillingOrder.public_id == public_id))
    assert found is not None, public_id
    return found


async def link_of(public_id: str) -> str:
    link = (await order_row(public_id)).razorpay_link_id
    assert link is not None
    return link


async def started(client: ApiClient, code: str, kind: str = "period") -> str:
    """A checkout that created its payment link; returns the order's public id."""
    response = await checkout(client, code, kind)
    assert response.status_code == 201, response.text
    return response.json()["order"]["public_id"]


async def come_back(client: ApiClient, query: dict[str, str]) -> httpx.Response:
    """The browser's return from Razorpay's page (it needs no session: the cookie is SameSite=Strict)."""
    return await client.get(RETURN, params=query)


async def buy(client: ApiClient, fake: FakeRazorpay, code: str, kind: str = "period", **payment: Any) -> str:
    """Checks out, pays on Razorpay's page and comes back; returns the order's public id."""
    public_id = await started(client, code, kind)
    response = await come_back(client, fake.pay(await link_of(public_id), **payment))
    assert response.status_code == 303, response.text
    return public_id


async def periods_of(user_id: int) -> list[PlanPeriod]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(
            select(PlanPeriod).where(PlanPeriod.user_id == user_id).order_by(PlanPeriod.starts_at, PlanPeriod.id)
        )
        return list(rows)


async def payments_of(user_id: int) -> list[BillingPayment]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(
            select(BillingPayment).where(BillingPayment.user_id == user_id).order_by(BillingPayment.id)
        )
        return list(rows)


async def events(kind: str | None = None) -> list[BillingEvent]:
    async with db.SessionLocal() as session:
        query = select(BillingEvent).order_by(BillingEvent.id)
        if kind is not None:
            query = query.where(BillingEvent.type == kind)
        return list(await session.scalars(query))


async def send_webhook(client: ApiClient, body: bytes, headers: dict[str, str]) -> httpx.Response:
    return await client.post(WEBHOOK, content=body, headers=headers)


async def run_worker() -> list[int]:
    """What the worker's billing loop does with the woken events: processes each; returns their ids."""
    ids = []
    while (item := await get_redis().lpop(business.WAKE_QUEUE)) is not None:
        ids.append(int(item))
        await webhooks.process(int(item))
    return ids
