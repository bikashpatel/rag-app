"""Direct UPI payments

`billing_orders` gains the direct-UPI columns (docs/briefs/upi-direct.md 5): the order's reference (the UPI note,
unique), the payee UPI ID and name it asks to pay, the customer's UTR and when it was submitted, and the admin's
decision. A partial unique index keeps a UTR on one order while it waits, is confirmed or is under review (a rejected
order's UTR is free again); an index on (status, upi_submitted_at) serves the admin's list of UTRs to check. No row
changes: every existing order has these columns empty.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01 23:39:53.847507
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UTR_HELD = "upi_utr IS NOT NULL AND status IN ('submitted', 'paid', 'review')"


def upgrade() -> None:
    with op.batch_alter_table("billing_orders") as batch:
        batch.add_column(sa.Column("upi_reference", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("upi_vpa", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("upi_payee_name", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("upi_utr", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("upi_submitted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("upi_decided_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("upi_decided_by", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("upi_rejection", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("upi_rejection_note", sa.String(length=200), nullable=True))
        batch.create_unique_constraint("uq_billing_orders_upi_reference", ["upi_reference"])
    op.create_index(
        "uq_billing_orders_upi_utr",
        "billing_orders",
        ["upi_utr"],
        unique=True,
        sqlite_where=sa.text(_UTR_HELD),
        postgresql_where=sa.text(_UTR_HELD),
    )
    op.create_index("ix_billing_orders_status_submitted", "billing_orders", ["status", "upi_submitted_at"])


def downgrade() -> None:
    raise NotImplementedError("Revision 0005 can't be downgraded: restore the backup taken before the upgrade instead")
