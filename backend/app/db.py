import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    URL,
    CheckConstraint,
    Connection,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    event,
    inspect,
    make_url,
    text,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import CreateTable

from app.config import settings

logger = logging.getLogger(__name__)

# Arbitrary constant used as a Postgres advisory-lock key, so that the backend, the worker and the alembic command line
# never migrate the schema at the same time.
_SCHEMA_LOCK_KEY = 4242001
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# The schema create_all made before migrations existed (migrations/README.md, "Databases made before migrations").
BASELINE_REVISION = "0001"
# What a comparison with the database covers: besides tables, columns, indexes, unique and foreign keys, also types,
# server defaults and named CHECK constraints (an opt-in plugin). env.py's autogenerate uses the same.
COMPARE_OPTIONS: dict[str, Any] = {
    "compare_type": True,
    "compare_server_default": True,
    "autogenerate_plugins": ["alembic.autogenerate.*", "alembic.ext.checkconstraint_byname"],
}
# A foreign key's (table, columns, referred "table.column"s), the same in the database and the baseline whatever its
# ON DELETE rule.
_FkKey = tuple[str, tuple[str, ...], tuple[str, ...]]


def make_engine(url: str | URL) -> AsyncEngine:
    """Async engine that behaves the same on Postgres and SQLite.

    Postgres sessions run in UTC, so date() on timestamptz splits days in UTC whatever the server's
    TimeZone. SQLite enforces foreign keys, so ON DELETE CASCADE / SET NULL work as on Postgres.
    """
    if make_url(url).get_driver_name() == "asyncpg":
        return create_async_engine(url, pool_pre_ping=True, connect_args={"server_settings": {"timezone": "UTC"}})
    new_engine = create_async_engine(url, pool_pre_ping=True)
    if new_engine.dialect.name == "sqlite":
        event.listen(new_engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    return new_engine


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


engine = make_engine(settings.sqlalchemy_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


class SchemaMismatchError(RuntimeError):
    """The database was made before migrations and differs from the baseline in ways the align migration doesn't
    repair. Nothing was changed."""


async def init_db() -> None:
    """Bring the schema to the newest migration (migrations/README.md), in one transaction (schema_transaction). Safe
    to call from several processes: on Postgres that transaction holds an advisory lock, so the others wait, then find
    nothing to do.

    Raises SchemaMismatchError, with the database unchanged, for tables made before migrations that differ from the
    baseline in a way the align migration doesn't repair.
    """
    async with schema_transaction(engine) as connection:
        await connection.run_sync(_migrate)


@asynccontextmanager
async def schema_transaction(bind: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One transaction to migrate in, which a failure rolls back whole. On Postgres it holds the schema lock until it
    ends (SQLite serves tests and development, where one process starts at a time).

    On SQLite, foreign keys are off for its duration: a batch operation copies the table and drops the old one, and
    with foreign keys on, that DROP deletes the rows referring to the table (ON DELETE CASCADE) or nulls their
    references (SET NULL). They are checked before the commit instead. SQLite ignores the pragma inside a transaction,
    and its Python driver runs DDL outside one unless it is begun explicitly.
    """
    async with bind.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            await _set_sqlite_foreign_keys(connection, "OFF")
        try:
            async with connection.begin():
                if sqlite:
                    await connection.exec_driver_sql("BEGIN")
                elif connection.dialect.name == "postgresql":
                    await connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
                yield connection
                if sqlite:
                    await connection.run_sync(_check_foreign_keys)
        finally:
            if sqlite:
                await _set_sqlite_foreign_keys(connection, "ON")


async def _set_sqlite_foreign_keys(connection: AsyncConnection, state: str) -> None:
    await connection.exec_driver_sql(f"PRAGMA foreign_keys={state}")
    await connection.commit()


def _check_foreign_keys(connection: Connection) -> None:
    broken = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if broken:
        listing = ", ".join(f"{table} row {rowid} -> {parent}" for table, rowid, parent, _ in broken[:10])
        raise RuntimeError(
            f"After the migration {len(broken)} row(s) refer to rows that don't exist ({listing}), so it was rolled "
            "back: delete or update the referring rows in the migration too"
        )


def _alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


def _migrate(connection: Connection) -> None:
    config = _alembic_config(connection)
    tables = set(inspect(connection).get_table_names())
    if tables and "alembic_version" not in tables:
        _adopt_legacy_schema(connection, config)
    context = MigrationContext.configure(connection)
    before = context.get_current_revision()
    command.upgrade(config, "head")
    after = context.get_current_revision()
    if before == after:
        logger.info("Database schema up to date (revision %s)", after)
    else:
        logger.info("Database schema migrated from %s to revision %s", before or "an empty database", after)


def _adopt_legacy_schema(connection: Connection, config: Config) -> None:
    """Tables that create_all made, before migrations: stamp them at the baseline when every difference from it is one
    the align migration repairs. Otherwise refuse, leaving the database as it was."""
    differences = legacy_differences(connection)
    repairs, refused = legacy_repairs(differences, connection.dialect.name)
    if refused:
        _refuse(refused)
    logger.info(
        "Database made before migrations: stamping revision %s, %d difference(s) for the align migration to repair",
        BASELINE_REVISION,
        len(repairs),
    )
    command.stamp(config, BASELINE_REVISION)


def _refuse(refused: list[Any]) -> None:
    listing = "\n".join(f"  - {describe_difference(diff)}" for diff in refused)
    logger.error(
        "The database was made before migrations and differs from the baseline (revision %s) in ways that can't be "
        "repaired automatically, so it was left unchanged and the app won't start:\n%s\n"
        "See backend/migrations/README.md, 'Databases made before migrations'.",
        BASELINE_REVISION,
        listing,
    )
    raise SchemaMismatchError(
        f"The database differs from the baseline in {len(refused)} way(s) that can't be repaired automatically (listed "
        "in the log); nothing was changed"
    )


class _SchemaRecorder:
    """Stands in for alembic's `op` while the baseline's create_schema runs, collecting its tables and indexes."""

    def __init__(self) -> None:
        self.metadata = MetaData()

    def create_table(self, name: str, *items: Any) -> None:
        Table(name, self.metadata, *items)

    def create_index(self, name: str, table: str, columns: list[str], unique: bool = False) -> None:
        Index(name, *(self.metadata.tables[table].c[column] for column in columns), unique=unique)

    def f(self, name: str) -> str:
        return name


@cache
def baseline_metadata() -> MetaData:
    """The schema of the baseline revision, frozen there (the models move on)."""
    script = ScriptDirectory.from_config(_alembic_config()).get_revision(BASELINE_REVISION)
    if script is None:
        raise RuntimeError(f"Migration {BASELINE_REVISION} is missing from {MIGRATIONS_DIR}")
    recorder = _SchemaRecorder()
    script.module.create_schema(recorder)
    return recorder.metadata


def legacy_differences(connection: Connection) -> list[Any]:
    """What alembic's autogenerate finds between the database and the baseline (COMPARE_OPTIONS)."""
    context = MigrationContext.configure(connection, opts=COMPARE_OPTIONS)
    return compare_metadata(context, baseline_metadata())


def _fk_key(constraint: ForeignKeyConstraint) -> _FkKey:
    table = constraint.parent
    assert isinstance(table, Table)
    return table.name, tuple(constraint.column_keys), tuple(element.target_fullname for element in constraint.elements)


def legacy_repairs(differences: list[Any], dialect: str) -> tuple[list[Any], list[Any]]:
    """Splits the differences from the baseline into the ones align_legacy_schema repairs, none of which touches a
    row, and the rest. Repaired: a missing table; a missing index (a unique one only on a missing table, since existing
    rows could break it); and on Postgres a foreign key with other ON DELETE (or ON UPDATE) rules, which shows as an
    extra and a missing key on the same columns."""
    items = [diff for diff in differences if isinstance(diff, tuple)]
    missing_tables = {diff[1].name for diff in items if diff[0] == "add_table"}
    fk_kinds: dict[_FkKey, set[str]] = {}
    for kind, *_, item in items:
        if kind in ("add_fk", "remove_fk"):
            fk_kinds.setdefault(_fk_key(item), set()).add(kind)

    def repairable(diff: Any) -> bool:
        if not isinstance(diff, tuple):
            return False
        kind, item = diff[0], diff[-1]
        if kind == "add_index":
            return not item.unique or item.table.name in missing_tables
        if kind in ("add_fk", "remove_fk"):
            return dialect == "postgresql" and len(fk_kinds[_fk_key(item)]) == 2
        return kind == "add_table"

    return [diff for diff in differences if repairable(diff)], [diff for diff in differences if not repairable(diff)]


def align_legacy_schema(operations: Operations) -> None:
    """The align migration (0002): makes the repairs legacy_repairs allows, so the database matches the baseline.
    Refuses, before changing anything, if the database differs in any other way (it was stamped by hand)."""
    connection = operations.get_bind()
    repairs, refused = legacy_repairs(legacy_differences(connection), connection.dialect.name)
    if refused:
        _refuse(refused)
    missing_tables = {diff[1].name for diff in repairs if diff[0] == "add_table"}
    for table in baseline_metadata().sorted_tables:
        if table.name in missing_tables:
            logger.info("Align: creating the missing table %s", table.name)
            operations.execute(CreateTable(table))
    names: dict[_FkKey, str] = {}  # the database's names stay
    for kind, *_, item in repairs:
        if kind == "remove_fk":
            names[_fk_key(item)] = item.name
            operations.drop_constraint(item.name, _fk_key(item)[0], type_="foreignkey")
    for kind, *_, item in repairs:
        if kind == "add_index":
            logger.info("Align: creating the missing index %s", item.name)
            operations.create_index(
                item.name, item.table.name, [column.name for column in item.columns], unique=item.unique
            )
        elif kind == "add_fk":
            source, columns, targets = _fk_key(item)
            logger.info("Align: %s gets ON DELETE %s", names[_fk_key(item)], item.ondelete or "NO ACTION")
            operations.create_foreign_key(
                names[_fk_key(item)],
                source,
                targets[0].split(".")[0],
                list(columns),
                [target.split(".")[1] for target in targets],
                onupdate=item.onupdate,
                ondelete=item.ondelete,
                deferrable=item.deferrable,
                initially=item.initially,
            )


def describe_difference(diff: Any) -> str:
    """One line per difference: "missing" is in the baseline but not in the database, "extra" the other way round."""
    if isinstance(diff, list):  # one column's changes: (kind, schema, table, column, existing, database, baseline)
        return "; ".join(
            f"{change[0].removeprefix('modify_')} of {change[2]}.{change[3]}: the database has {change[5]!r}, the "
            f"baseline {change[6]!r}"
            for change in diff
        )
    kind, item = diff[0], diff[-1]
    action, _, what = kind.partition("_")
    state = {"add": "missing", "remove": "extra"}.get(action, action)
    if what == "table":
        return f"{state} table {item.name}"
    if what == "column":
        return f"{state} column {diff[2]}.{item.name} {item.type}"
    if what == "index":
        unique = "unique " if item.unique else ""
        return f"{state} {unique}index {item.name} on {item.table.name} ({', '.join(item.columns.keys())})"
    if what == "fk":
        table, columns, targets = _fk_key(item)
        rules = f"ON DELETE {item.ondelete or 'NO ACTION'}"
        return f"{state} foreign key {table} ({', '.join(columns)}) -> {', '.join(targets)} {rules}"
    if what == "constraint":
        keys = ", ".join(item.columns.keys())
        rule = f"CHECK ({item.sqltext})" if isinstance(item, CheckConstraint) else f"UNIQUE ({keys})"
        return f"{state} constraint {item.name or '(unnamed)'} on {item.table.name}: {rule}"
    return f"{kind}: {diff[1:]!r}"


async def close_db() -> None:
    await engine.dispose()
