"""Helpers of the direct-UPI tests (test_upi_*.py; docs/briefs/upi-direct.md): the owner's UPI settings, a customer's
UPI order and UTR, and the admin's confirm and reject. Customers need no email and no tester flag; Razorpay stays off
unless a test asks for the fake."""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from billing_support import plan_id
from conftest import ApiClient
from sqlalchemy import select

from app import db
from app.models import BillingOrder
from app.schemas_billing import BillingSettings
from app.services.billing import business

CreateUser = Callable[..., Awaitable[ApiClient]]
UPI_ID = "asha.rao@okhdfcbank"
PAYEE = "Asha Rao"
CONTACT = "billing@business.example.test"
UTR = "630512345678"
OTHER_UTR = "630587654321"


async def set_upi(
    *, enabled: bool = True, upi_id: str | None = UPI_ID, name: str | None = PAYEE, contact: str | None = CONTACT
) -> None:
    """The owner's direct-UPI settings (Admin → Billing → Business & payments) and contact email."""
    async with db.SessionLocal() as session:
        current = await business.get_settings(session)
        value = BillingSettings.model_validate(
            {
                **current.model_dump(),
                "upi_enabled": enabled,
                "upi_id": upi_id,
                "upi_payee_name": name,
                "contact_email": contact,
            }
        )
        await business.save_settings(session, value, actor="admin")
        await session.commit()


async def upi_checkout(client: ApiClient, code: str, kind: str = "period", **headers: str) -> httpx.Response:
    body = {"plan_id": await plan_id(code), "kind": kind, "accept_terms": True}
    return await client.post("/api/billing/upi/checkout", json=body, headers=headers)


async def upi_order(client: ApiClient, code: str, kind: str = "period") -> dict[str, Any]:
    """A new direct-UPI order (201); its OrderView."""
    response = await upi_checkout(client, code, kind)
    assert response.status_code == 201, response.text
    order: dict[str, Any] = response.json()
    return order


async def send_utr(client: ApiClient, public_id: str, utr: str = UTR) -> httpx.Response:
    return await client.post(f"/api/billing/orders/{public_id}/utr", json={"utr": utr})


async def submitted(client: ApiClient, code: str, utr: str = UTR, kind: str = "period") -> str:
    """A direct-UPI order whose UTR waits for the admin; returns its public id."""
    public_id = (await upi_order(client, code, kind))["public_id"]
    response = await send_utr(client, public_id, utr)
    assert response.status_code == 200, response.text
    return str(public_id)


async def confirm(admin: ApiClient, public_id: str, utr: str = UTR) -> httpx.Response:
    return await admin.post(f"/api/admin/billing/orders/{public_id}/upi-confirm", json={"utr": utr})


async def reject(
    admin: ApiClient, public_id: str, reason: str = "not_received", note: str | None = None
) -> httpx.Response:
    return await admin.post(f"/api/admin/billing/orders/{public_id}/upi-reject", json={"reason": reason, "note": note})


async def order_of(public_id: str) -> BillingOrder:
    async with db.SessionLocal() as session:
        found = await session.scalar(select(BillingOrder).where(BillingOrder.public_id == public_id))
    assert found is not None, public_id
    return found


async def change_order(public_id: str, **values: Any) -> None:
    """Moves an order's times (or other columns) as the tests need."""
    async with db.SessionLocal() as session:
        order = await session.scalar(select(BillingOrder).where(BillingOrder.public_id == public_id))
        assert order is not None, public_id
        for name, value in values.items():
            setattr(order, name, value)
        await session.commit()


async def make_paid(admin: ApiClient, user_id: int) -> None:
    """The user has paid before: a bank transfer the admin recorded (a receipt only, no period)."""
    body = {
        "user_id": user_id,
        "plan_id": await plan_id("starter"),
        "amount_paise": 50_000,
        "received_on": "2026-10-24",
        "reference": "NEFT-1",
        "note": None,
        "apply": False,
    }
    response = await admin.post("/api/admin/billing/offline-payments", json=body)
    assert response.status_code == 201, response.text
