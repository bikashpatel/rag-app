"""Align legacy schema

Aligns a database made before migrations with the baseline. app.db.init_db stamps such a database at 0001 only when
every difference from the baseline is one app.db.align_legacy_schema repairs (README.md, "Databases made before
migrations"); this revision then repairs them. A database that 0001 created in the same run has nothing to align, so it
skips the comparison.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 14:00:00.000000
"""

from collections.abc import Sequence
from typing import cast

from alembic import context, op
from alembic.operations import Operations

from app.db import align_legacy_schema

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.config.attributes.get("baseline_created"):
        align_legacy_schema(cast(Operations, op))  # `op` is the module proxying this run's Operations


def downgrade() -> None:
    pass  # the repairs only brought the database to the baseline, which 0001 describes
