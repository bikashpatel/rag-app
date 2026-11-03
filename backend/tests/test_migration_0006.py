"""Revision 0006 (person names) on a database of revision 0005 holding users and pending sign-ups (migrations/versions/
0006_person_names.py; SPEC.md section 4): every row kept, no name invented, the schema equal to the models, and a name
of 80 characters in any script fits. Tests marked `both` run on SQLite and, marked postgres, on the TEST_POSTGRES_URL
database."""

import logging
from datetime import datetime, timezone
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, MetaData, insert, inspect, select, text, update
from sqlalchemy.exc import DBAPIError

from app import db
from app.db import Base
from app.models import User

both = pytest.mark.usefixtures("on_both")
WHEN = datetime(2026, 11, 1, tzinfo=timezone.utc)
USER = {
    "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$legacy",
    "is_admin": False,
    "is_active": True,
    "token_version": 0,
    "restrict_models": False,
    "billing_tester": False,
    "created_at": WHEN,
    "updated_at": WHEN,
}


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


async def rows_of(table_name: str) -> list[dict[str, Any]]:
    table = (await run(reflected)).tables[table_name]
    async with db.engine.begin() as connection:
        return [dict(row._mapping) for row in await connection.execute(select(table).order_by(table.c.id))]


async def at_0005() -> None:
    """The schema of revision 0005 with users of each source and a pending sign-up, as that release wrote them."""
    async with db.schema_transaction(db.engine) as connection:
        await connection.run_sync(lambda sync: command.upgrade(db._alembic_config(sync), "0005"))
    tables = (await run(reflected)).tables
    async with db.engine.begin() as connection:
        await connection.execute(
            insert(tables["users"]),
            [
                {**USER, "username": "bikash", "signup_source": "admin"},
                {
                    **USER,
                    "username": "user-xtimkcnf",
                    "signup_source": "email",
                    "email": "maya@example.com",
                    "email_canonical": "maya@example.com",
                    "email_verified_at": WHEN,
                    "terms_version": "2026-11-01",
                    "terms_accepted_at": WHEN,
                },
            ],
        )
        await connection.execute(
            insert(tables["pending_signups"]).values(
                address="Ravi@example.com",
                canonical="ravi@example.com",
                password_hash="$argon2id$v=19$m=65536,t=3,p=4$pending",
                terms_version="2026-11-01",
                terms_accepted_at=WHEN,
                password_failures=0,
                created_at=WHEN,
                expires_at=WHEN,
            )
        )


@both
async def test_the_migration_adds_the_name_columns_and_keeps_every_row(
    migration_logs: pytest.LogCaptureFixture,
) -> None:
    await at_0005()
    before = {name: await rows_of(name) for name in ("users", "pending_signups")}
    counts = await run(row_counts)

    await db.init_db()

    assert await run(row_counts) == counts
    for name, rows in before.items():
        after = await rows_of(name)
        assert [{key: row[key] for key in rows[0]} for row in after] == rows, name
        assert {row["display_name"] for row in after} == {None}, name  # no name invented
    assert "Running upgrade 0005 -> 0006, Person names" in migration_logs.text


@both
async def test_the_migrated_schema_matches_the_models() -> None:
    await at_0005()

    await db.init_db()

    differences = await run(
        lambda connection: compare_metadata(
            MigrationContext.configure(connection, opts=db.COMPARE_OPTIONS), Base.metadata
        )
    )
    assert differences == []
    for name in ("users", "pending_signups"):
        [column] = [c for c in await run(lambda c, n=name: inspect(c).get_columns(n)) if c["name"] == "display_name"]
        assert (column["nullable"], column["type"].length) == (True, 80), name


async def test_a_name_of_80_characters_in_any_script_fits(on_both: str) -> None:
    await at_0005()
    await db.init_db()
    name = "क्ष" * 26 + "ि" + "ñ"
    assert len(name) == 80

    async with db.engine.begin() as connection:
        await connection.execute(update(User).where(User.username == "bikash").values(display_name=name))
    async with db.engine.begin() as connection:
        assert await connection.scalar(select(User.display_name).where(User.username == "bikash")) == name

    if on_both == "postgres":  # SQLite doesn't hold a VARCHAR to its length; the schema refuses longer names first
        with pytest.raises(DBAPIError):
            async with db.engine.begin() as connection:
                await connection.execute(update(User).values(display_name=name + "a"))



async def test_the_migration_is_one_revision_after_direct_upi() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(db.MIGRATIONS_DIR)).get_revision("0006")

    assert script is not None and script.down_revision == "0005"
    with pytest.raises(NotImplementedError, match="restore the backup"):
        script.module.downgrade()
