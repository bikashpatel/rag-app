"""The direct-UPI payment request and its QR codes (services/billing/upi.py; docs/briefs/upi-direct.md 7): the exact
upi://pay strings, the reference's alphabet and a taken reference drawn again, the SVG (no script, a viewBox, a white
background) and the PNG with their headers, the QR endpoints only for the caller's own order while it waits for its
UTR, and the admin's ₹1 test code."""

import re
from datetime import timedelta

import pytest
from billing_support import give_email, started
from conftest import ApiClient, make_tester
from upi_support import PAYEE, UPI_ID, CreateUser, change_order, send_utr, set_upi, upi_order

from app.models import utcnow
from app.services.billing import upi


@pytest.mark.parametrize(
    ("name", "paise", "expected"),
    [
        (
            "Asha Rao",
            200_000,
            "upi://pay?pa=asha.rao@okhdfcbank&pn=Asha%20Rao&am=2000.00&cu=INR&tn=AN-7K4M-Q9TX",
        ),
        (
            "Rao & Sons",
            50_000,
            "upi://pay?pa=asha.rao@okhdfcbank&pn=Rao%20%26%20Sons&am=500.00&cu=INR&tn=AN-7K4M-Q9TX",
        ),
        (
            "D'Souza (A/B), Ltd.",
            100,
            "upi://pay?pa=asha.rao@okhdfcbank&pn=D%27Souza%20%28A%2FB%29%2C%20Ltd.&am=1.00&cu=INR&tn=AN-7K4M-Q9TX",
        ),
    ],
)
def test_the_request_is_exactly_the_upi_link(name: str, paise: int, expected: str) -> None:
    uri = upi.request_uri(UPI_ID, name, paise, "AN-7K4M-Q9TX")

    assert uri == expected
    assert "tr=" not in uri and "mc=" not in uri and "+" not in uri


def test_only_whole_rupees_are_requested() -> None:
    for paise in (0, -100, 150_050):
        with pytest.raises(ValueError, match="whole number of rupees"):
            upi.request_uri(UPI_ID, PAYEE, paise, "AN-7K4M-Q9TX")


def test_references_use_the_unambiguous_alphabet() -> None:
    drawn = {upi.new_reference() for _ in range(500)}

    assert len(drawn) == 500
    assert all(re.fullmatch(r"AN-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}", reference) for reference in drawn)
    assert not set("".join(drawn)) & set("01OIL")


def test_the_svg_is_black_on_white_with_a_viewbox_and_no_script() -> None:
    svg = upi.qr_svg(upi.request_uri(UPI_ID, PAYEE, 200_000, "AN-7K4M-Q9TX")).decode()

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ')
    assert '<path fill="#fff" d="M0 0h' in svg and 'stroke="#000"' in svg
    assert not re.search(r"<script|on\w+=|href|width=|height=|class=|<\?xml", svg)


def test_the_png_is_a_png() -> None:
    png = upi.qr_png(upi.request_uri(UPI_ID, PAYEE, 200_000, "AN-7K4M-Q9TX"))

    assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 300


async def test_a_taken_reference_is_drawn_again(create_user: CreateUser, monkeypatch: pytest.MonkeyPatch) -> None:
    await set_upi()
    first = await upi_order(await create_user("smoke-ref-a", plan=None), "starter")
    draws = iter([first["upi"]["reference"], first["upi"]["reference"], "AN-2222-3333"])
    monkeypatch.setattr(upi, "new_reference", lambda: next(draws))

    second = await upi_order(await create_user("smoke-ref-b", plan=None), "starter")

    assert second["upi"]["reference"] == "AN-2222-3333"


async def test_the_qr_codes_of_an_order_waiting_for_its_utr(create_user: CreateUser) -> None:
    await set_upi()
    maya = await create_user("smoke-maya", plan=None)
    order = await upi_order(maya, "standard")
    path = f"/api/billing/orders/{order['public_id']}"
    expected = upi.request_uri(UPI_ID, PAYEE, 200_000, order["upi"]["reference"])

    svg = await maya.get(f"{path}/upi-qr.svg")
    png = await maya.get(f"{path}/upi-qr.png")

    assert order["upi"]["uri"] == expected
    assert svg.status_code == 200 and svg.headers["content-type"] == "image/svg+xml"
    assert svg.content == upi.qr_svg(expected)  # rendered from the stored order
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert png.content == upi.qr_png(expected)
    assert png.headers["content-disposition"] == f'attachment; filename="upi-{order["upi"]["reference"]}.png"'
    for response in (svg, png):
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'; sandbox"


async def test_the_order_keeps_the_upi_id_it_was_made_with(create_user: CreateUser) -> None:
    await set_upi()
    neha = await create_user("smoke-neha", plan=None)
    order = await upi_order(neha, "starter")
    await set_upi(upi_id="new.id@ybl", name="Asha R")

    svg = await neha.get(f"/api/billing/orders/{order['public_id']}/upi-qr.svg")
    again = (await neha.get(f"/api/billing/orders/{order['public_id']}")).json()

    assert svg.content == upi.qr_svg(upi.request_uri(UPI_ID, PAYEE, 50_000, order["upi"]["reference"]))
    assert (again["upi"]["upi_id"], again["upi"]["payee_name"]) == (UPI_ID, PAYEE)


async def test_qr_codes_are_only_the_callers_while_the_order_waits_for_its_utr(
    create_user: CreateUser, fake_razorpay: object
) -> None:
    await set_upi()
    owner = await create_user("smoke-owner", plan=None)
    other = await create_user("smoke-other", plan=None)
    waiting = await upi_order(owner, "starter")
    sent = await upi_order(other, "starter")
    assert (await send_utr(other, sent["public_id"])).status_code == 200
    tester = await create_user("smoke-tester", plan=None)
    assert tester.user_id is not None
    await make_tester(tester.user_id)
    await give_email(tester.user_id, "smoke-tester@account.example.test")
    online = await started(tester, "starter")
    late = await upi_order(tester, "standard")
    await change_order(late["public_id"], expires_at=utcnow() - timedelta(seconds=1))

    cases: list[tuple[ApiClient, str]] = [
        (other, waiting["public_id"]),  # another user's
        (other, sent["public_id"]),  # submitted
        (tester, online),  # a Razorpay order
        (tester, late["public_id"]),  # expired
        (owner, "nope"),
    ]
    for client, public_id in cases:
        for kind in ("svg", "png"):
            response = await client.get(f"/api/billing/orders/{public_id}/upi-qr.{kind}")
            assert response.status_code == 404, (public_id, kind)


async def test_the_admins_one_rupee_test_code(admin_client: ApiClient, create_user: CreateUser) -> None:
    missing = await admin_client.get("/api/admin/billing/upi-preview.svg")
    await set_upi(enabled=False)

    off = await admin_client.get("/api/admin/billing/upi-preview.svg")
    user = await (await create_user("smoke-peek", plan=None)).get("/api/admin/billing/upi-preview.svg")

    assert missing.status_code == 404
    assert off.status_code == 200 and off.headers["content-type"] == "image/svg+xml"
    assert off.content == upi.qr_svg(f"upi://pay?pa={UPI_ID}&pn=Asha%20Rao&am=1.00&cu=INR&tn=AN-TEST")
    assert off.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    assert user.status_code == 403
