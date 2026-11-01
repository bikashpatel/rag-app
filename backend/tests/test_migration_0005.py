"""Revision 0005 (direct UPI) on a database of revision 0004 holding orders of every kind (migrations/versions/
0005_upi_direct.py; docs/briefs/upi-direct.md 5.2): every row kept with the new columns empty, the schema equal to the
models, and the partial unique index holding a UTR on one waiting, paid or reviewed order while a rejected one frees
it. Tests marked `both` run on SQLite and, marked postgres, on the TEST_POSTGRES_URL database."""

import logging
from datetime import datetime, timezone
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, MetaData, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from app import db
from app.db import Base
from app.models import BillingOrder

both = pytest.mark.usefixtures("on_both")
WHEN = datetime(2026, 10, 1, tzinfo=timezone.utc)
UPI_COLUMNS = (
    "upi_reference",
    "upi_vpa",
    "upi_payee_name",
    "upi_utr",
    "upi_submitted_at",
    "upi_decided_at",
    "upi_decided_by",
    "upi_rejection",
    "upi_rejection_note",
)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def on_both(request: pytest.FixtureRequest) -> str:
    """The test runs on SQLite and, marked postgres, on TEST_POSTGRES_URL (isolated_state reads the mark)."""
    return request.param


@pytest.fixture(autouse=True)
def migration_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="alembic.runtime.migration")
    return caplog


async def run(function: Any) -> Any:
    async with db.engine.begin() as connection:
        return await connection.run_sync(function)


def reflected(connection: Connection) -> MetaData:
    metadata = MetaData()
    metadata.reflect(connection)
    return metadata


def row_counts(connection: Connection) -> dict[str, int]:
    names = set(inspect(connection).get_table_names()) - {"alembic_version"}
    return {name: connection.scalar(text(f'SELECT count(*) FROM "{name}"')) for name in sorted(names)}


ORDER = {
    "plan_snapshot": {"plan_id": 1, "code": "starter", "name": "Starter"},
    "kind": "period",
    "credit_paise": 0,
    "amount_paise": 50_000,
    "currency": "INR",
    "origin": "https://asknuvexusdoc.com",
    "expires_at": WHEN,
    "watch_until": WHEN,
    "check_step": 0,
    "checks": 0,
    "created_at": WHEN,
    "updated_at": WHEN,
}


async def at_0004() -> list[dict[str, Any]]:
    """The schema of revision 0004 with a user and orders as that release wrote them; returns the orders."""
    async with db.schema_transaction(db.engine) as connection:
        await connection.run_sync(lambda sync: command.upgrade(db._alembic_config(sync), "0004"))
    tables = (await run(reflected)).tables
    async with db.engine.begin() as connection:
        user_id = await connection.scalar(
            insert(tables["users"])
            .values(
                username="bikash",
                password_hash="$argon2id$v=19$m=65536,t=3,p=4$legacy",
                is_admin=False,
                is_active=True,
                token_version=0,
                restrict_models=False,
                billing_tester=False,
                signup_source="admin",
                created_at=WHEN,
                updated_at=WHEN,
            )
            .returning(tables["users"].c.id)
        )
        for number, (mode, status) in enumerate(
            (("live", "paid"), ("test", "pending"), ("offline", "paid"), ("live", "review"), ("live", "expired"))
        ):
            await connection.execute(
                insert(tables["billing_orders"]).values(
                    public_id=f"order{number}", user_id=user_id, username="bikash", mode=mode, status=status, **ORDER
                )
            )
        rows = await connection.execute(select(tables["billing_orders"]).order_by(tables["billing_orders"].c.id))
        return [dict(row._mapping) for row in rows]


@both
async def test_the_migration_adds_the_upi_columns_and_keeps_every_row(
    migration_logs: pytest.LogCaptureFixture,
) -> None:
    orders = await at_0004()
    before = await run(row_counts)

    await db.init_db()

    assert await run(row_counts) == before
    table = (await run(reflected)).tables["billing_orders"]
    async with db.engine.begin() as connection:
        after = [dict(row._mapping) for row in await connection.execute(select(table).order_by(table.c.id))]
    assert [{name: row[name] for name in orders[0]} for row in after] == [
        {name: row[name] for name in orders[0]} for row in orders
    ]
    assert {row[name] for row in after for name in UPI_COLUMNS} == {None}
    assert "Running upgrade 0004 -> 0005, Direct UPI payments" in migration_logs.text


@both
async def test_the_migrated_schema_matches_the_models() -> None:
    await at_0004()

    await db.init_db()

    differences = await run(
        lambda connection: compare_metadata(
            MigrationContext.configure(connection, opts=db.COMPARE_OPTIONS), Base.metadata
        )
    )
    assert differences == []
    indexes = {index["name"]: index for index in await run(lambda c: inspect(c).get_indexes("billing_orders"))}
    assert indexes["uq_billing_orders_upi_utr"]["unique"]
    assert indexes["ix_billing_orders_status_submitted"]["column_names"] == ["status", "upi_submitted_at"]


@both
async def test_a_utr_is_held_by_one_waiting_paid_or_reviewed_order() -> None:
    await at_0004()
    await db.init_db()

    async def add(public_id: str, status: str, utr: str, reference: str) -> None:
        async with db.engine.begin() as connection:
            await connection.execute(
                insert(BillingOrder).values(
                    public_id=public_id,
                    username="bikash",
                    mode="offline",
                    status=status,
                    upi_reference=reference,
                    upi_utr=utr,
                    **ORDER,
                )
            )

    await add("upi1", "submitted", "630512345678", "AN-2222-2222")
    for status in ("submitted", "paid", "review"):
        with pytest.raises(IntegrityError):
            await add(f"upi-{status}", status, "630512345678", f"AN-{status[:4].upper()}-3333")
    with pytest.raises(IntegrityError):  # a reference is unique too
        await add("upi2", "pending", "630500000000", "AN-2222-2222")
    for status in ("rejected", "expired", "cancelled", "pending"):
        await add(f"other-{status}", status, "630512345678", f"AN-{status[:4].upper()}-4444")

    async with db.engine.begin() as connection:
        await connection.execute(update(BillingOrder).where(BillingOrder.public_id == "upi1").values(status="rejected"))
    await add("upi3", "submitted", "630512345678", "AN-5555-5555")  # the rejected order's UTR is free again
    async with db.engine.begin() as connection:
        held = await connection.scalar(
            select(BillingOrder.public_id).where(
                BillingOrder.upi_utr == "630512345678", BillingOrder.status == "submitted"
            )
        )
    assert held == "upi3"


async def test_the_migration_is_one_revision_after_sign_up() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(db.MIGRATIONS_DIR)).get_revision("0005")

    assert script is not None and script.down_revision == "0004"
    with pytest.raises(NotImplementedError, match="restore the backup"):
        script.module.downgrade()
