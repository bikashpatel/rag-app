"""Receipts (docs/briefs/plans-billing.md 9; the layout of plans-billing-visual.md 10): one per applied payment,
numbered per mode and Indian financial year without gaps, kept as an immutable snapshot and rendered to PDF on demand
(fpdf2 with the bundled Noto Sans, which has ₹). No GST line: version 1 is built for a seller without a GSTIN."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from fastapi import Response
from fpdf import FPDF
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppState,
    BillingDispute,
    BillingOrder,
    BillingPayment,
    BillingReceipt,
    BillingRefund,
    PlanPeriod,
    UserBillingProfile,
)
from app.schemas_billing import PlanSnapshot
from app.services.billing import business, periods
from app.services.billing.money import (
    IST,
    PERIOD_DAYS,
    financial_year,
    format_inr,
    format_ist_date,
    ist_date,
    receipt_seq_key,
)

TEST_PREFIX: Final = "TEST"
FONTS = Path(__file__).resolve().parents[2] / "fonts"
INK, INK2, INK3 = (42, 37, 32), (90, 82, 73), (110, 101, 90)
RULE, HAIR, FILL = (184, 173, 155), (229, 223, 211), (243, 240, 232)
CLAY, SUCCESS, SUCCESS_SOFT = (169, 77, 43), (57, 112, 63), (228, 239, 225)
DANGER, WATERMARK = (176, 42, 58), (236, 230, 220)
LEFT, RIGHT, WIDTH = 18, 192, 174
_METHODS = {
    "upi": "UPI",
    "card": "Card",
    "netbanking": "Netbanking",
    "wallet": "Wallet",
    "emi": "EMI",
    "bank_transfer": "Bank transfer",
}
_PILLS = {
    "partially_refunded": "PARTIALLY REFUNDED",
    "refunded": "REFUNDED",
    "disputed": "DISPUTED",
}
_NOTE = "One-time payment for {days}; it doesn't renew by itself."


def _days(days: int) -> str:
    return f"{days} day{'' if days == 1 else 's'}"


def _span(start: datetime, end: datetime) -> str:
    """ "26 Oct 2026 – 25 Nov 2026": the first and the last IST day."""
    return f"{format_ist_date(ist_date(start))} – {format_ist_date(periods.valid_through(end))}"


def money(paise: int) -> str:
    """Two decimals, as receipts show amounts: "₹2,000.00", "−₹300.00"."""
    text = format_inr(paise)
    return text if paise % 100 else f"{text}.00"


async def _next_number(db: AsyncSession, series: str, year: str) -> int:
    """The next number of a series: its app_state counter read FOR UPDATE (last in the lock order); a missing counter
    is inserted in a savepoint, and a racing insert is re-read."""
    key = receipt_seq_key(series, year)
    query = select(AppState).where(AppState.key == key).with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(query)
    if row is None:
        try:
            async with db.begin_nested():
                db.add(AppState(key=key, value="0"))
        except IntegrityError:
            pass
        row = await db.scalar(query)
        assert row is not None
    seq = int(row.value or 0) + 1
    row.value = str(seq)
    return seq


async def _lines(
    db: AsyncSession, order: BillingOrder, payment: BillingPayment, period: PlanPeriod | None
) -> list[dict[str, Any]]:
    """The plan line, and for an upgrade the credit line (negative), so that they add up to the amount paid."""
    snapshot = PlanSnapshot.model_validate(order.plan_snapshot)
    days = periods.TEST_PERIOD_DAYS if order.mode == "test" else PERIOD_DAYS
    bank = f"Bank transfer, ref {payment.offline_reference}" if payment.method == "bank_transfer" else None
    price = int(order.plan_snapshot.get("price_paise", payment.amount_paise)) if order.kind == "now" else None
    lines: list[dict[str, Any]] = [
        {
            "description": f"{snapshot.name} plan, {_days(days)}" if period or not bank else f"{snapshot.name} plan",
            "detail": bank or _NOTE.format(days=_days(days)),
            "period": _span(period.starts_at, period.ends_at) if period else None,
            "amount_paise": price if price is not None else payment.amount_paise,
        }
    ]
    if price is not None and price != payment.amount_paise:
        replaced = await db.get(PlanPeriod, order.replaces_period_id) if order.replaces_period_id else None
        name = periods.snapshot(replaced).name if replaced else "the previous plan"
        span = _span(replaced.starts_at, order.replaces_ends_at) if replaced and order.replaces_ends_at else None
        lines.append(
            {
                "description": f"Credit for unused {name}",
                "detail": None,
                "period": span,
                "amount_paise": payment.amount_paise - price,
            }
        )
    return lines


async def issue(
    db: AsyncSession, payment: BillingPayment, order: BillingOrder, period: PlanPeriod | None, *, now: datetime
) -> BillingReceipt:
    """The receipt of an applied payment, in its transaction: live and offline payments share the live series."""
    series = "test" if payment.mode == "test" else "live"
    year = financial_year(now)
    seller = await business.get_settings(db)
    prefix = TEST_PREFIX if series == "test" else seller.receipt_prefix
    profile = await db.get(UserBillingProfile, order.user_id) if order.user_id is not None else None
    seq = await _next_number(db, series, year)
    receipt = BillingReceipt(
        number=f"{prefix}/{year}/{seq:06d}",
        mode=series,
        financial_year=year,
        seq=seq,
        payment_id=payment.id,
        user_id=order.user_id,
        issued_at=now,
        seller={
            "name": seller.business_name,
            "address": seller.address,
            "email": seller.contact_email,
            "phone": seller.contact_phone,
        },
        buyer={
            "name": (profile.name if profile and profile.name else None) or order.username,
            "username": order.username,
            "email": profile.email if profile else None,
        },
        lines=await _lines(db, order, payment, period),
        amount_paise=payment.amount_paise,
        method=payment.method,
    )
    db.add(receipt)
    await db.flush()
    return receipt


# ---------------- The PDF ----------------


@dataclass(frozen=True)
class _Adjustment:
    label: str
    amount_paise: int
    note: str | None


@dataclass(frozen=True)
class _Document:
    receipt: BillingReceipt
    status: str
    reference_label: str
    reference: str | None
    paid_at: datetime | None
    adjustments: list[_Adjustment]


async def _document(db: AsyncSession, receipt: BillingReceipt) -> _Document:
    payment = await db.get(BillingPayment, receipt.payment_id)
    assert payment is not None
    refunds = await db.scalars(
        select(BillingRefund)
        .where(BillingRefund.payment_id == payment.id, BillingRefund.status == "processed")
        .order_by(BillingRefund.processed_at, BillingRefund.id)
    )
    disputes = await db.scalars(
        select(BillingDispute)
        .where(BillingDispute.payment_id == payment.id, BillingDispute.status == "lost")
        .order_by(BillingDispute.id)
    )
    adjustments = [
        _Adjustment(
            f"Refunded on {format_ist_date(ist_date(refund.processed_at or refund.created_at))}",
            -refund.amount_paise,
            " · ".join(part for part in (f"Refund {refund.razorpay_refund_id or refund.id}", refund.reason) if part),
        )
        for refund in refunds
    ] + [
        _Adjustment(
            f"Disputed, lost on {format_ist_date(ist_date(dispute.updated_at))}",
            -dispute.amount_paise,
            f"Dispute {dispute.razorpay_dispute_id}",
        )
        for dispute in disputes
    ]
    offline = payment.razorpay_payment_id is None
    return _Document(
        receipt=receipt,
        status=_PILLS.get(payment.status, "PAID"),
        reference_label="Bank reference" if offline else "Razorpay payment",
        reference=payment.offline_reference if offline else payment.razorpay_payment_id,
        paid_at=payment.captured_at,
        adjustments=adjustments,
    )


def _pdf(doc: _Document) -> bytes:
    receipt = doc.receipt
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_title(f"Receipt {receipt.number}")
    pdf.set_creation_date(receipt.issued_at)  # the same bytes on every download
    pdf.set_auto_page_break(False)
    pdf.set_margins(LEFT, 18, 210 - RIGHT)
    pdf.add_page()
    pdf.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))

    def text(x: float, y: float, value: str, size: float = 9.5, style: str = "", color=INK, align="L", w=0.0) -> None:
        pdf.set_font("Noto", style, size)
        pdf.set_text_color(*color)
        pdf.set_xy(x, y)
        pdf.cell(w, size * 0.45, value, align=align)

    def label(x: float, y: float, value: str, align: str = "L", w: float = 0.0) -> None:
        pdf.set_char_spacing(0.6)
        text(x, y, value.upper(), 7.2, "B", INK3, align, w)
        pdf.set_char_spacing(0)

    def hline(y: float, color=HAIR, width: float = 0.25, x1: float = LEFT, x2: float = RIGHT) -> None:
        pdf.set_draw_color(*color)
        pdf.set_line_width(width)
        pdf.line(x1, y, x2, y)

    if receipt.mode == "test":  # first, so everything prints over it
        pdf.set_font("Noto", "B", 40)
        pdf.set_text_color(*WATERMARK)
        mark = "TEST: not a real payment"
        with pdf.rotation(35, 105, 160):
            pdf.text(105 - pdf.get_string_width(mark) / 2, 164, mark)

    pdf.set_fill_color(*CLAY)
    pdf.rect(0, 0, 210, 2.6, style="F")
    seller = receipt.seller
    text(LEFT, 18, seller.get("name") or "", 15, "B")
    seller_lines = [line for line in (seller.get("address") or "").splitlines() if line.strip()][:5]
    contact = " · ".join(part for part in (seller.get("email"), seller.get("phone")) if part)
    for index, line in enumerate([*seller_lines, *([contact] if contact else [])]):
        text(LEFT, 27 + index * 4.6, line, 8.8, color=INK2)
    label(120, 18.5, "Receipt", "R", RIGHT - 120)
    text(120, 24, receipt.number, 13, "B", align="R", w=RIGHT - 120)
    text(120, 31.5, f"Issued {format_ist_date(ist_date(receipt.issued_at))}", 8.8, color=INK2, align="R", w=RIGHT - 120)
    paid = doc.status == "PAID"
    fg, bg = (SUCCESS, SUCCESS_SOFT) if paid else (INK2, FILL)
    pdf.set_font("Noto", "B", 7.5)
    pill = pdf.get_string_width(doc.status) + 7 + len(doc.status) * 0.5
    pdf.set_fill_color(*bg)
    pdf.rect(RIGHT - pill, 37.5, pill, 5.6, style="F", round_corners=True, corner_radius=2.8)
    pdf.set_char_spacing(0.5)
    text(RIGHT - pill, 38.3, doc.status, 7.5, "B", fg, "C", pill)
    pdf.set_char_spacing(0)
    hline(52, RULE, 0.35)

    buyer = receipt.buyer
    label(LEFT, 57, "Billed to")
    text(LEFT, 62, buyer.get("name") or buyer.get("username") or "", 10.5, "B")
    details = [f"{buyer.get('username')} (account)", *([buyer["email"]] if buyer.get("email") else [])]
    for index, line in enumerate(details):
        text(LEFT, 67.2 + index * 4.6, line, 8.8, color=INK2)
    label(112, 57, "Payment")
    paid_on = doc.paid_at.astimezone(IST) if doc.paid_at else receipt.issued_at.astimezone(IST)
    method = receipt.method or ""
    rows = [
        ("Method", _METHODS.get(method, method.replace("_", " ").capitalize() or "—")),
        (doc.reference_label, doc.reference or "—"),
        ("Paid on", f"{format_ist_date(paid_on.date())}, {paid_on:%H:%M} IST"),
    ]
    for index, (term, value) in enumerate(rows):
        text(112, 62 + index * 4.8, term, 8.8, color=INK3)
        text(140, 62 + index * 4.8, value, 8.8, "B" if index == 0 else "", INK, "R", RIGHT - 140)

    y = 86.0
    pdf.set_fill_color(*FILL)
    pdf.rect(LEFT, y, WIDTH, 8, style="F")
    label(LEFT + 3, y + 2.6, "Description")
    label(104, y + 2.6, "Period")
    label(150, y + 2.6, "Amount", "R", RIGHT - 153)
    y += 11
    for line in receipt.lines:
        credit = line["amount_paise"] < 0
        pdf.set_xy(LEFT + 3, y)
        pdf.set_font("Noto", "" if credit else "B", 10)
        pdf.set_text_color(*(INK2 if credit else INK))
        pdf.multi_cell(80, 4.5, line["description"])
        below = pdf.get_y()
        if line.get("detail"):
            text(LEFT + 3, below + 0.5, line["detail"], 8.2, color=INK3)
            below += 5
        text(104, y, line.get("period") or "", 9.2, color=INK2)
        text(150, y, money(line["amount_paise"]), 10, "" if credit else "B", INK2 if credit else INK, "R", RIGHT - 153)
        y = max(below, y + 5) + 3
    hline(y + 2)

    y += 7
    hline(y, RULE, 0.35, 118, RIGHT)
    y += 3
    text(118, y, "Total paid", 11, "B")
    text(140, y - 0.6, money(receipt.amount_paise), 13, "B", align="R", w=RIGHT - 140)
    text(LEFT, y + 0.4, "One-time payment; plans don't renew by themselves.", 8.5, color=INK3)
    for adjustment in doc.adjustments:
        y += 10
        text(118, y, adjustment.label, 9.2, color=DANGER)
        text(150, y, money(adjustment.amount_paise), 9.2, "B", DANGER, "R", RIGHT - 150)
        if adjustment.note:
            text(118, y + 5, adjustment.note[:70], 7.8, color=INK3)

    hline(276, HAIR)
    text(LEFT, 279.5, "This receipt was generated electronically and needs no signature.", 8, color=INK3)
    text(120, 279.5, f"{receipt.number} · page 1 of 1", 8, color=INK3, align="R", w=RIGHT - 120)
    return bytes(pdf.output())


async def pdf_response(db: AsyncSession, receipt: BillingReceipt) -> Response:
    data = await asyncio.to_thread(_pdf, await _document(db, receipt))
    filename = f"receipt-{receipt.number.replace('/', '-')}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )
