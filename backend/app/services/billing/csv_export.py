"""The month's payments as CSV for the accounts (docs/briefs/plans-billing.md 7.2, GET /admin/billing/export.csv): one
row per payment and per processed refund or lost dispute of the IST month. Cells that a spreadsheet would take as a
formula get a leading apostrophe."""

import csv
import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingDispute, BillingOrder, BillingPayment, BillingReceipt, BillingRefund
from app.schemas_billing import PlanSnapshot
from app.services.billing.money import IST
from app.services.billing.summary import month_range, summary_modes

HEADER = (
    "Date (IST)",
    "Type",
    "Receipt",
    "User",
    "Plan",
    "Kind",
    "Amount (INR)",
    "Method",
    "Razorpay payment id or bank reference",
    "Fee (INR)",
    "Fee GST (INR)",
    "Refunded (INR)",
)


def cell(value: object) -> str:
    """A cell as text; one starting with =, +, - or @ gets a leading apostrophe so it is never a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def rupees(paise: int | None) -> str:
    return "" if paise is None else f"{paise // 100}.{paise % 100:02d}"


async def export(db: AsyncSession, month: str | None) -> tuple[str, str]:
    """(file name, CSV text) of an IST month."""
    label, start, end = month_range(month)
    rows: list[tuple[datetime, list[object]]] = []
    found = (
        await db.execute(
            select(BillingPayment, BillingOrder, BillingReceipt)
            .outerjoin(BillingOrder, BillingOrder.id == BillingPayment.order_id)
            .outerjoin(BillingReceipt, BillingReceipt.payment_id == BillingPayment.id)
            .where(BillingPayment.mode.in_(summary_modes()))
        )
    ).tuples()
    for payment, order, receipt in found:
        plan = PlanSnapshot.model_validate(order.plan_snapshot).name if order else ""
        common: list[object] = [receipt.number if receipt else "", payment.username, plan, order.kind if order else ""]
        reference = payment.razorpay_payment_id or payment.offline_reference
        captured = payment.captured_at
        if payment.status != "failed" and captured is not None and start <= captured < end:
            money = [rupees(payment.fee_paise), rupees(payment.fee_tax_paise), rupees(payment.refunded_paise)]
            rows.append(
                (captured, ["payment", *common, rupees(payment.amount_paise), payment.method, reference, *money])
            )
        refunds = await db.scalars(
            select(BillingRefund).where(BillingRefund.payment_id == payment.id, BillingRefund.status == "processed")
        )
        for refund in refunds:
            if refund.processed_at is not None and start <= refund.processed_at < end:
                rows.append(
                    (refund.processed_at, ["refund", *common, rupees(refund.amount_paise), payment.method, reference])
                )
        disputes = await db.scalars(
            select(BillingDispute).where(BillingDispute.payment_id == payment.id, BillingDispute.status == "lost")
        )
        for dispute in disputes:
            lost = dispute.access_ended_at or dispute.updated_at
            if start <= lost < end:
                rows.append((lost, ["dispute lost", *common, rupees(dispute.amount_paise), payment.method, reference]))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for when, values in sorted(rows, key=lambda row: row[0]):
        writer.writerow([when.astimezone(IST).strftime("%Y-%m-%d %H:%M"), *(cell(value) for value in values)])
    return f"payments-{label}.csv", out.getvalue()
