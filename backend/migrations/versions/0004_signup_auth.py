"""Sign-up, sign-in by email and Google, password reset and transactional email

The tables of pending sign-ups and emails, link tokens, Google identities, the email outbox, account-change events,
suppressed addresses and retired usernames (docs/briefs/signup-auth.md, 9.1 and 9.2). `users` gains the verified email
(unique by its canonical form), the sign-up source and the terms record, and `password_hash` becomes nullable;
`login_events` gains the sign-in method. Then the data: the env admin's `signup_source` becomes `env` (everyone else
keeps the default `admin`), the usernames kept on billing records of deleted users are tombstoned, and
`user_billing_profiles.email` is dropped after counting its values. No email is invented for anyone.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01 01:55:12.186364
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    _create_tables()
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email", sa.String(length=254), nullable=True))
        batch.add_column(sa.Column("email_canonical", sa.String(length=254), nullable=True))
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("signup_source", sa.String(length=16), server_default="admin", nullable=False))
        batch.add_column(sa.Column("terms_version", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
        batch.alter_column("password_hash", existing_type=sa.String(length=255), nullable=True)
        batch.create_index("uq_users_email_canonical", ["email_canonical"], unique=True)
    users = sa.table("users", sa.column("username"), sa.column("is_admin", sa.Boolean()), sa.column("signup_source"))
    op.execute(users.update().where(users.c.is_admin == sa.true()).values(signup_source="env"))
    with op.batch_alter_table("login_events") as batch:
        batch.add_column(sa.Column("method", sa.String(length=16), server_default="password", nullable=False))
    _tombstone_deleted_usernames(users)
    _drop_profile_emails()


def _tombstone_deleted_usernames(users: sa.TableClause) -> None:
    """Billing records keep the username of a deleted user (user_id NULL): those names are never issued again."""
    bind = op.get_bind()
    kept: set[str] = set()
    for name in ("billing_orders", "billing_payments"):
        records = sa.table(name, sa.column("username"), sa.column("user_id"))
        kept |= set(bind.scalars(sa.select(records.c.username).where(records.c.user_id.is_(None)).distinct()))
    retired = sorted(kept - set(bind.scalars(sa.select(users.c.username))))
    if retired:
        tombstones = sa.table(
            "username_tombstones", sa.column("username"), sa.column("deleted_at", sa.DateTime(timezone=True))
        )
        now = datetime.now(timezone.utc)
        bind.execute(tombstones.insert(), [{"username": username, "deleted_at": now} for username in retired])
    logger.info("Revision 0004: %d username(s) of deleted users tombstoned", len(retired))


def _drop_profile_emails() -> None:
    """Receipts and reminders now go to the verified users.email: the profile's own address is dropped (counted, never
    logged)."""
    profiles = sa.table("user_billing_profiles", sa.column("email"))
    dropped = op.get_bind().scalar(
        sa.select(sa.func.count()).select_from(profiles).where(profiles.c.email.is_not(None), profiles.c.email != "")
    )
    with op.batch_alter_table("user_billing_profiles") as batch:
        batch.drop_column("email")
    logger.info("Revision 0004: %d billing profile email(s) dropped", dropped)


def _create_tables() -> None:
    op.create_table(
        "pending_signups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=254), nullable=False),
        sa.Column("canonical", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("browser_sha256", sa.String(length=64), nullable=True),
        sa.Column("next_path", sa.String(length=512), nullable=True),
        sa.Column("terms_version", sa.String(length=32), nullable=False),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("password_failures", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_signups_canonical_created", "pending_signups", ["canonical", "created_at"])
    op.create_index(op.f("ix_pending_signups_expires_at"), "pending_signups", ["expires_at"])
    op.create_table(
        "pending_emails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=254), nullable=False),
        sa.Column("canonical", sa.String(length=254), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_pending_emails_user_id"),
    )
    op.create_index(op.f("ix_pending_emails_canonical"), "pending_emails", ["canonical"])
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("address_sha256", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("pending_signup_id", sa.Integer(), nullable=True),
        sa.Column("pending_email_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pending_email_id"], ["pending_emails.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pending_signup_id"], ["pending_signups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_sha256", name="uq_auth_tokens_token_sha256"),
    )
    op.create_index(op.f("ix_auth_tokens_expires_at"), "auth_tokens", ["expires_at"])
    op.create_index("ix_auth_tokens_user_purpose", "auth_tokens", ["user_id", "purpose"])
    op.create_table(
        "user_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("issuer", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email_at_link", sa.String(length=254), nullable=False),
        sa.Column("email_kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_user_identities_issuer_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("mail_class", sa.String(length=16), nullable=False),
        sa.Column("to_address", sa.String(length=254), nullable=True),
        sa.Column("canonical", sa.String(length=254), nullable=True),
        sa.Column("recipient_sha256", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("pending_signup_id", sa.Integer(), nullable=True),
        sa.Column("pending_email_id", sa.Integer(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pending_email_id"], ["pending_emails.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pending_signup_id"], ["pending_signups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_email_outbox_public_id"),
    )
    op.create_index("ix_email_outbox_class_created", "email_outbox", ["mail_class", "created_at"])
    op.create_index(op.f("ix_email_outbox_sent_at"), "email_outbox", ["sent_at"])
    op.create_index("ix_email_outbox_status_next", "email_outbox", ["status", "next_attempt_at"])
    op.create_table(
        "auth_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_events_type_created", "auth_events", ["type", "created_at"])
    op.create_index("ix_auth_events_user_created", "auth_events", ["user_id", "created_at"])
    op.create_table(
        "mail_suppressions",
        sa.Column("recipient_sha256", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("recipient_sha256"),
    )
    op.create_table(
        "username_tombstones",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )


def downgrade() -> None:
    raise NotImplementedError("Revision 0004 can't be downgraded: restore the backup taken before the upgrade instead")
