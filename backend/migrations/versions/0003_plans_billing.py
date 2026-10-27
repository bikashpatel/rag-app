"""Plans and billing

The tables of plans, plan periods, checkouts, payments, refunds, disputes, receipts and the billing audit trail
(docs/briefs/plans-billing.md, 6.2 and 11.1). The users' five limit columns become nullable overrides (NULL = the plan's
value) and gain the image and vision allowances, the tester flag and an open-ended complimentary plan; usage events gain
`allowance_tokens`. Then the data (4.11): one `rollout.limits_cleared` billing event per non-admin user with their old
limits (the `user_image_limits` row included), every user's limits cleared, `user_image_limits` dropped, and chat
events' `allowance_tokens` set to their `total_tokens`. On Postgres an exclusion constraint keeps a user's counting
periods from overlapping. Seeding the plans and giving existing users a plan happen in app.bootstrap.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26 21:42:34.289687
"""

from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The users' limit columns before this revision, in the order of the rollout event's detail.
_LIMITS = ("monthly_token_limit", "rate_limit_per_min", "max_upload_mb", "max_documents", "max_storage_mb")


def upgrade() -> None:
    _create_tables()
    with op.batch_alter_table("usage_events") as batch:
        batch.add_column(sa.Column("allowance_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False))
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("monthly_image_limit", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("vision_monthly_token_limit", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("billing_tester", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("comp_plan_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("comp_reason", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("comp_granted_by", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("comp_granted_at", sa.DateTime(timezone=True), nullable=True))
        batch.alter_column("monthly_token_limit", existing_type=sa.BigInteger(), nullable=True)
        for column in _LIMITS[1:]:
            batch.alter_column(column, existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key("users_comp_plan_id_fkey", "plans", ["comp_plan_id"], ["id"], ondelete="SET NULL")
    _clear_limits()
    op.drop_table("user_image_limits")
    op.execute("UPDATE usage_events SET allowance_tokens = total_tokens WHERE kind = 'chat'")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")  # a trusted extension: the database owner may add it
        op.execute(
            "ALTER TABLE plan_periods ADD CONSTRAINT ex_plan_periods_no_overlap EXCLUDE USING gist "
            "(user_id WITH =, tstzrange(starts_at, ends_at) WITH &&) WHERE (revoked_at IS NULL AND mode <> 'test') "
            "DEFERRABLE INITIALLY DEFERRED"
        )


def _clear_limits() -> None:
    """One rollout.limits_cleared event per non-admin user with their old limits, then every user's limits NULL."""
    users = sa.table(
        "users",
        sa.column("id"),
        sa.column("username"),
        sa.column("is_admin", sa.Boolean()),
        *(sa.column(column) for column in _LIMITS),
    )
    image_limits = sa.table("user_image_limits", sa.column("user_id"), sa.column("monthly_image_limit"))
    events = sa.table(
        "billing_events",
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("source"),
        sa.column("type"),
        sa.column("user_id"),
        sa.column("actor"),
        sa.column("detail", sa.JSON()),
        sa.column("processed", sa.Boolean()),
        sa.column("attempts"),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(users, image_limits.c.monthly_image_limit)
        .outerjoin(image_limits, image_limits.c.user_id == users.c.id)
        .where(users.c.is_admin == sa.false())
        .order_by(users.c.id)
    ).mappings()
    now = datetime.now(timezone.utc)
    cleared = [
        {
            "created_at": now,
            "source": "system",
            "type": "rollout.limits_cleared",
            "user_id": row["id"],
            "actor": "system",
            "detail": {
                "username": row["username"],
                **{column: row[column] for column in _LIMITS},
                "monthly_image_limit": row["monthly_image_limit"],
            },
            "processed": True,
            "attempts": 0,
        }
        for row in rows
    ]
    if cleared:
        bind.execute(events.insert(), cleared)
    bind.execute(users.update().values(dict.fromkeys(_LIMITS)))


def _create_tables() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("price_paise", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recommended", sa.Boolean(), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("monthly_token_limit", sa.BigInteger(), nullable=False),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=False),
        sa.Column("max_upload_mb", sa.Integer(), nullable=False),
        sa.Column("max_documents", sa.Integer(), nullable=False),
        sa.Column("max_storage_mb", sa.Integer(), nullable=False),
        sa.Column("monthly_image_limit", sa.Integer(), nullable=False),
        sa.Column("vision_monthly_token_limit", sa.BigInteger(), nullable=False),
        sa.Column("restrict_models", sa.Boolean(), nullable=False),
        sa.Column("default_chat_model_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["default_chat_model_id"], ["llm_models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_plans_code"),
        sa.UniqueConstraint("rank", name="uq_plans_rank"),
    )
    op.create_table(
        "plan_chat_models",
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["llm_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("plan_id", "model_id"),
    )
    # Orders, payments and periods refer to each other. The two keys marked use_alter are created inline on SQLite
    # (which can't add a key to a table) and, as create_all does, added once the tables exist on Postgres.
    op.create_table(
        "billing_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("replaces_period_id", sa.Integer(), nullable=True),
        sa.Column("replaces_plan_id", sa.Integer(), nullable=True),
        sa.Column("replaces_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credit_paise", sa.BigInteger(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("review_reason", sa.String(length=200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("razorpay_link_id", sa.String(length=40), nullable=True),
        sa.Column("razorpay_order_id", sa.String(length=40), nullable=True),
        sa.Column("pay_url", sa.String(length=200), nullable=True),
        sa.Column("origin", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("watch_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_step", sa.Integer(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checks", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("last_payment_error", sa.String(length=200), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["billing_payments.id"],
            name="billing_orders_payment_id_fkey",
            ondelete="SET NULL",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["replaces_period_id"],
            ["plan_periods.id"],
            name="billing_orders_replaces_period_id_fkey",
            ondelete="SET NULL",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_billing_orders_public_id"),
        sa.UniqueConstraint("razorpay_link_id", name="uq_billing_orders_razorpay_link_id"),
    )
    op.create_index(op.f("ix_billing_orders_next_check_at"), "billing_orders", ["next_check_at"], unique=False)
    op.create_index(op.f("ix_billing_orders_razorpay_order_id"), "billing_orders", ["razorpay_order_id"], unique=False)
    op.create_index("ix_billing_orders_user_status", "billing_orders", ["user_id", "status"], unique=False)
    op.create_table(
        "user_billing_profiles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "billing_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(length=40), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("method", sa.String(length=24), nullable=True),
        sa.Column("fee_paise", sa.BigInteger(), nullable=True),
        sa.Column("fee_tax_paise", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_reason", sa.String(length=200), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_paise", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("offline_reference", sa.String(length=64), nullable=True),
        sa.Column("recorded_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["billing_orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_payment_id", name="uq_billing_payments_razorpay_payment_id"),
    )
    op.create_index(op.f("ix_billing_payments_order_id"), "billing_payments", ["order_id"], unique=False)
    op.create_index(op.f("ix_billing_payments_user_id"), "billing_payments", ["user_id"], unique=False)
    op.create_table(
        "plan_periods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("charge_basis_paise", sa.BigInteger(), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("granted_by", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["billing_orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plan_periods_order_id"), "plan_periods", ["order_id"], unique=False)
    op.create_index("ix_plan_periods_user_ends", "plan_periods", ["user_id", "ends_at"], unique=False)
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "billing_orders_payment_id_fkey",
            "billing_orders",
            "billing_payments",
            ["payment_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            "billing_orders_replaces_period_id_fkey",
            "billing_orders",
            "plan_periods",
            ["replaces_period_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "billing_disputes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("razorpay_dispute_id", sa.String(length=40), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("respond_by", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["billing_payments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_dispute_id", name="uq_billing_disputes_razorpay_dispute_id"),
    )
    op.create_index(op.f("ix_billing_disputes_payment_id"), "billing_disputes", ["payment_id"], unique=False)
    op.create_table(
        "billing_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("razorpay_event_id", sa.String(length=64), nullable=True),
        sa.Column("body_sha256", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["billing_orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_id"], ["billing_payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("body_sha256", name="uq_billing_events_body_sha256"),
        sa.UniqueConstraint("razorpay_event_id", name="uq_billing_events_razorpay_event_id"),
    )
    op.create_index(op.f("ix_billing_events_order_id"), "billing_events", ["order_id"], unique=False)
    op.create_index(op.f("ix_billing_events_payment_id"), "billing_events", ["payment_id"], unique=False)
    op.create_index("ix_billing_events_processed_source", "billing_events", ["processed", "source"], unique=False)
    op.create_index("ix_billing_events_type_created", "billing_events", ["type", "created_at"], unique=False)
    op.create_index(op.f("ix_billing_events_user_id"), "billing_events", ["user_id"], unique=False)
    op.create_table(
        "billing_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("financial_year", sa.String(length=7), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seller", sa.JSON(), nullable=False),
        sa.Column("buyer", sa.JSON(), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=24), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["billing_payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mode", "financial_year", "seq", name="uq_billing_receipts_series"),
        sa.UniqueConstraint("number", name="uq_billing_receipts_number"),
        sa.UniqueConstraint("payment_id", name="uq_billing_receipts_payment_id"),
    )
    op.create_index(op.f("ix_billing_receipts_user_id"), "billing_receipts", ["user_id"], unique=False)
    op.create_table(
        "billing_refunds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("razorpay_refund_id", sa.String(length=40), nullable=True),
        sa.Column("idempotency_key", sa.String(length=40), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("initiated_by", sa.String(length=16), nullable=False),
        sa.Column("admin_username", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("end_access", sa.Boolean(), nullable=False),
        sa.Column("access_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["billing_payments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_billing_refunds_idempotency_key"),
        sa.UniqueConstraint("razorpay_refund_id", name="uq_billing_refunds_razorpay_refund_id"),
    )
    op.create_index(op.f("ix_billing_refunds_payment_id"), "billing_refunds", ["payment_id"], unique=False)


def downgrade() -> None:
    raise NotImplementedError("Revision 0003 can't be downgraded: restore the backup taken before the upgrade instead")
