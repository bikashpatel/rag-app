"""Person names

`users.display_name` holds the person's name as they write it (SPEC.md section 4: normalized and checked by
app.schemas_auth.person_name, at most 80 characters), and `pending_signups.display_name` keeps the name given at an
email sign-up until the account exists. Both are nullable: no row changes, and an existing account has no name until
the person or the admin sets one.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("display_name", sa.String(length=80), nullable=True))
    with op.batch_alter_table("pending_signups") as batch:
        batch.add_column(sa.Column("display_name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Revision 0006 can't be downgraded: restore the backup taken before the upgrade instead")
