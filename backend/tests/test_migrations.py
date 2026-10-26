"""Database migrations (migrations/README.md): app.db.init_db on an empty database, on one that create_all made before
migrations (stamped at the baseline and aligned, or refused and left unchanged), under the schema lock, through the
app's start-up and the alembic command line, and the drift guard between the models and the migrations. Tests marked
`both` run on SQLite and, marked postgres, on the TEST_POSTGRES_URL database."""

import asyncio
import logging
import os
import shutil
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from conftest import POSTGRES_URL
from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DefaultClause,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    inspect,
    text,
)

from app import db
from app.db import Base, SchemaMismatchError
from app.main import app as fastapi_app
from app.models import Conversation, Document, LLMModel, Message, UsageEvent, User

BACKEND = Path(__file__).resolve().parent.parent
HEAD = ScriptDirectory(str(db.MIGRATIONS_DIR)).get_current_head()
both = pytest.mark.usefixtures("on_both")


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def on_both(request: pytest.FixtureRequest) -> str:
    """The test runs on SQLite and, marked postgres, on TEST_POSTGRES_URL (isolated_state reads the mark)."""
    return request.param


@pytest.fixture(autouse=True)
def migration_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="app.db")
    return caplog


def revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def model_differences(connection: Connection, metadata: MetaData = Base.metadata) -> list[Any]:
    return compare_metadata(MigrationContext.configure(connection, opts=db.COMPARE_OPTIONS), metadata)


async def run(function: Callable[[Connection], Any]) -> Any:
    async with db.engine.begin() as connection:
        return await connection.run_sync(function)


def copy_of_models() -> MetaData:
    """The models' tables (indexes and constraints included), to change before create_all makes an old database."""
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    return metadata


async def create_all(metadata: MetaData = Base.metadata, *statements: str) -> None:
    """A database as create_all made it before migrations, then changed by `statements`."""
    async with db.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        for statement in statements:
            await connection.execute(text(statement))


def foreign_key(metadata: MetaData, table: str, column: str) -> ForeignKeyConstraint:
    return next(fk for fk in metadata.tables[table].foreign_key_constraints if fk.column_keys == [column])


def drop_index(metadata: MetaData, table: str, name: str) -> None:
    metadata.tables[table].indexes.remove(next(index for index in metadata.tables[table].indexes if index.name == name))


async def add_rows() -> dict[str, int]:
    """A user with a document, a chat with an answer and its usage: rows to find again after start-up."""
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    async with db.engine.begin() as connection:
        await connection.execute(insert(User.__table__).values(id=7, username="smoke-legacy", password_hash="x"))
        await connection.execute(
            insert(LLMModel.__table__).values(id=3, display_name="Chat", litellm_model="openai/gpt-5.5", kind="chat")
        )
        await connection.execute(
            insert(Document.__table__).values(
                id=11, owner_id=7, scope="private", filename="notes.txt", stored_name="n1", sha256="0" * 64
            )
        )
        await connection.execute(insert(Conversation.__table__).values(id=5, user_id=7, updated_at=now))
        await connection.execute(
            insert(Message.__table__).values(conversation_id=5, role="assistant", content="Hi", model_id=3)
        )
        await connection.execute(
            insert(UsageEvent.__table__).values(user_id=7, model_id=3, model_name="openai/gpt-5.5", kind="chat")
        )
    return await run(row_counts)


def row_counts(connection: Connection) -> dict[str, int]:
    names = set(inspect(connection).get_table_names()) - {"alembic_version"}
    return {name: connection.scalar(text(f'SELECT count(*) FROM "{name}"')) for name in sorted(names)}


def snapshot(connection: Connection) -> dict[str, Any]:
    """Every table's reflected shape and row count, to show that a refused start-up changed nothing."""
    inspector = inspect(connection)
    shape: dict[str, Any] = {}
    for name in sorted(inspector.get_table_names()):
        columns = [(c["name"], str(c["type"]), c["nullable"], c.get("default")) for c in inspector.get_columns(name)]
        shape[name] = (
            columns,
            inspector.get_pk_constraint(name),
            sorted(map(repr, inspector.get_indexes(name))),
            sorted(map(repr, inspector.get_foreign_keys(name))),
            sorted(map(repr, inspector.get_unique_constraints(name))),
            sorted(map(repr, inspector.get_check_constraints(name))),
            connection.scalar(text(f'SELECT count(*) FROM "{name}"')),
        )
    return shape


# ---------------- New databases and the drift guard ----------------


@both
async def test_an_empty_database_gets_every_migration_and_matches_the_models(
    migration_logs: pytest.LogCaptureFixture,
) -> None:
    await db.init_db()

    assert await run(revision) == HEAD
    assert await run(model_differences) == []
    assert await run(db.legacy_differences) == []  # 0001 is still the models' schema
    assert f"Database schema migrated from an empty database to revision {HEAD}" in migration_logs.text
    assert "stamping" not in migration_logs.text

    await db.init_db()  # the next start
    assert f"Database schema up to date (revision {HEAD})" in migration_logs.text


@both
async def test_the_migrations_make_exactly_what_create_all_makes() -> None:
    """Names, column order, types, defaults, indexes, unique keys and foreign keys with their ON DELETE rules."""
    await db.init_db()
    migrated = await run(snapshot)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.execute(text("DROP TABLE alembic_version"))
    await create_all()

    created = await run(snapshot)
    assert set(migrated) - set(created) == {"alembic_version"}
    assert {name: shape for name, shape in migrated.items() if name != "alembic_version"} == created


@both
async def test_the_models_and_the_migrations_agree() -> None:
    """The drift guard: a model change without a migration fails here (migrations/README.md, "Writing a migration")."""
    await db.init_db()

    assert await run(model_differences) == [], (
        "The models differ from the migrations: write one with `alembic revision --autogenerate` "
        "(backend/migrations/README.md)"
    )
    changed = copy_of_models()
    changed.tables["users"].append_column(Column("nickname", String(40), nullable=True))
    differences = await run(lambda connection: model_differences(connection, changed))
    assert [(diff[0], diff[2], diff[3].name) for diff in differences] == [("add_column", "users", "nickname")]


def test_the_migrations_are_one_line_from_the_baseline() -> None:
    script = ScriptDirectory(str(db.MIGRATIONS_DIR))
    steps = list(script.walk_revisions())

    assert len(script.get_heads()) == 1
    assert [(step.revision, step.down_revision) for step in steps[-2:]] == [("0002", "0001"), ("0001", None)]


def test_the_baseline_cannot_be_downgraded() -> None:
    baseline = ScriptDirectory(str(db.MIGRATIONS_DIR)).get_revision("0001")
    assert baseline is not None
    with pytest.raises(NotImplementedError, match="restore the backup"):
        baseline.module.downgrade()


# ---------------- Databases made before migrations ----------------


@both
async def test_a_database_made_by_create_all_is_stamped_and_kept(migration_logs: pytest.LogCaptureFixture) -> None:
    await create_all()
    rows = await add_rows()

    await db.init_db()

    assert await run(revision) == HEAD
    assert await run(model_differences) == []
    assert await run(row_counts) == rows
    assert rows["users"] == rows["documents"] == rows["messages"] == rows["usage_events"] == 1
    assert "stamping revision 0001, 0 difference(s) for the align migration" in migration_logs.text
    assert f"Database schema migrated from 0001 to revision {HEAD}" in migration_logs.text
    assert "Align:" not in migration_logs.text


@both
async def test_missing_tables_and_indexes_are_created(migration_logs: pytest.LogCaptureFixture) -> None:
    old = copy_of_models()
    drop_index(old, "documents", "ix_documents_sha256")
    drop_index(old, "conversations", "ix_conversations_updated_at")
    old.remove(old.tables["login_events"])  # a table newer than the database, e.g. an old copy restored
    await create_all(old)
    rows = await add_rows()

    await db.init_db()

    assert await run(model_differences) == []
    assert await run(row_counts) == {**rows, "login_events": 0}
    for line in (
        "stamping revision 0001, 4 difference(s)",
        "Align: creating the missing table login_events",
        "Align: creating the missing index ix_login_events_user_created",
        "Align: creating the missing index ix_documents_sha256",
        "Align: creating the missing index ix_conversations_updated_at",
    ):
        assert line in migration_logs.text


@both
async def test_foreign_keys_with_other_delete_rules_get_the_baselines(migration_logs: pytest.LogCaptureFixture) -> None:
    """Postgres repairs them, keeping the constraint names; SQLite can't alter a key in place, so it refuses."""
    old = copy_of_models()
    foreign_key(old, "documents", "owner_id").ondelete = None
    foreign_key(old, "messages", "model_id").ondelete = "CASCADE"
    await create_all(old)
    rows = await add_rows()

    if db.engine.dialect.name == "sqlite":
        before = await run(snapshot)
        with pytest.raises(SchemaMismatchError):
            await db.init_db()
        assert await run(snapshot) == before
        assert "extra foreign key documents (owner_id) -> users.id ON DELETE NO ACTION" in migration_logs.text
        assert "missing foreign key documents (owner_id) -> users.id ON DELETE CASCADE" in migration_logs.text
        return

    await db.init_db()

    assert await run(model_differences) == []
    assert await run(row_counts) == rows
    keys = {fk["name"]: fk["options"] for name in ("documents", "messages") for fk in await run(foreign_keys(name))}
    assert keys["documents_owner_id_fkey"] == {"ondelete": "CASCADE"}
    assert keys["messages_model_id_fkey"] == {"ondelete": "SET NULL"}
    assert "Align: documents_owner_id_fkey gets ON DELETE CASCADE" in migration_logs.text
    async with db.engine.begin() as connection:
        await connection.execute(text("DELETE FROM llm_models"))
        assert (await connection.execute(text("SELECT count(*), max(model_id) FROM messages"))).one() == (1, None)
        await connection.execute(text("DELETE FROM users"))
        assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0


def foreign_keys(table: str) -> Callable[[Connection], list[Any]]:
    return lambda connection: inspect(connection).get_foreign_keys(table)


def _extra_column(metadata: MetaData) -> None:
    metadata.tables["users"].append_column(Column("nickname", String(40), nullable=True))


def _type(metadata: MetaData) -> None:
    metadata.tables["documents"].c.filename.type = String(100)


def _nullable(metadata: MetaData) -> None:
    metadata.tables["documents"].c.sha256.nullable = True


def _server_default(metadata: MetaData) -> None:
    metadata.tables["users"].c.token_version.server_default = DefaultClause(text("0"))


def _unknown_table(metadata: MetaData) -> None:
    Table("notes", metadata, Column("id", Integer, primary_key=True))


def _extra_index(metadata: MetaData) -> None:
    Index("ix_documents_filename", metadata.tables["documents"].c.filename)


def _missing_unique_index(metadata: MetaData) -> None:
    drop_index(metadata, "users", "ix_users_username")


def _missing_unique_constraint(metadata: MetaData) -> None:
    table = metadata.tables["plugin_connections"]
    table.constraints.remove(next(c for c in table.constraints if isinstance(c, UniqueConstraint)))


def _check_constraint(metadata: MetaData) -> None:
    metadata.tables["users"].append_constraint(CheckConstraint("max_upload_mb > 0", name="ck_users_upload"))


def _unchanged(metadata: MetaData) -> None:
    pass


@both
@pytest.mark.parametrize(
    ("change", "statements", "listed"),
    [
        (_extra_column, (), "extra column users.nickname VARCHAR(40)"),
        (_unchanged, ("ALTER TABLE users DROP COLUMN last_login_at",), "missing column users.last_login_at"),
        (
            _type,
            (),
            "type of documents.filename: the database has VARCHAR(length=100), the baseline String(length=255)",
        ),
        (_nullable, (), "nullable of documents.sha256: the database has True, the baseline False"),
        (_server_default, (), "default of users.token_version"),
        (_unknown_table, (), "extra table notes"),
        (_extra_index, (), "extra index ix_documents_filename on documents (filename)"),
        (_missing_unique_index, (), "missing unique index ix_users_username on users (username)"),
        (
            _missing_unique_constraint,
            (),
            "missing constraint uq_plugin_connections_user_plugin on plugin_connections: UNIQUE (user_id, plugin_id)",
        ),
        (_check_constraint, (), "extra constraint ck_users_upload on users: CHECK (max_upload_mb > 0)"),
    ],
    ids=lambda value: value.__name__.strip("_") if callable(value) else None,
)
async def test_any_other_difference_refuses_start_up_and_changes_nothing(
    change: Callable[[MetaData], None],
    statements: tuple[str, ...],
    listed: str,
    migration_logs: pytest.LogCaptureFixture,
) -> None:
    old = copy_of_models()
    change(old)
    drop_index(old, "documents", "ix_documents_sha256")  # repairable, but not on its own: nothing may change
    await create_all(old, *statements)
    await add_rows()
    before = await run(snapshot)

    with pytest.raises(SchemaMismatchError, match="can't be repaired automatically .* nothing was changed"):
        await db.init_db()

    assert await run(snapshot) == before
    assert "alembic_version" not in before
    errors = [record.getMessage() for record in migration_logs.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert listed in errors[0]
    assert "ix_documents_sha256" not in errors[0]
    assert "backend/migrations/README.md" in errors[0]


@both
async def test_the_align_migration_refuses_a_hand_stamped_database_that_differs() -> None:
    old = copy_of_models()
    _extra_column(old)
    await create_all(old)
    async with db.engine.begin() as connection:
        await connection.run_sync(lambda sync: command.stamp(db._alembic_config(sync), "0001"))

    with pytest.raises(SchemaMismatchError):
        await db.init_db()

    assert await run(revision) == "0001"
    assert "nickname" in {column["name"] for column in await run(lambda c: inspect(c).get_columns("users"))}


def test_unknown_kinds_of_difference_are_still_listed() -> None:
    assert db.describe_difference(("add_table_comment", "users")) == "add_table_comment: ('users',)"


# ---------------- Start-up ----------------


@both
async def test_the_app_starts_on_a_database_made_before_migrations() -> None:
    await create_all()
    rows = await add_rows()

    async with fastapi_app.router.lifespan_context(fastapi_app):
        assert await run(revision) == HEAD
        counts = await run(row_counts)

    assert counts["documents"] == rows["documents"] and counts["messages"] == rows["messages"]
    assert counts["users"] == rows["users"] + 1  # the admin, synced at start-up


@both
async def test_the_app_does_not_start_on_a_database_that_differs() -> None:
    old = copy_of_models()
    _extra_column(old)
    await create_all(old)

    with pytest.raises(SchemaMismatchError):
        async with fastapi_app.router.lifespan_context(fastapi_app):
            raise AssertionError("the app started")

    assert await run(row_counts) == dict.fromkeys(sorted(old.tables), 0)


@pytest.mark.postgres
async def test_two_processes_starting_together_migrate_once() -> None:
    """The backend and the worker run init_db at start-up: the second waits on the advisory lock until the first's
    transaction ends, then finds the schema at head."""
    script = (
        "import asyncio, logging; logging.basicConfig(level=logging.INFO, format='%(message)s'); "
        "from app.db import init_db; asyncio.run(init_db())"
    )
    env = {**os.environ, "DATABASE_URL": POSTGRES_URL}
    async with db.engine.begin() as holder:  # both processes start while the lock is taken
        await holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": db._SCHEMA_LOCK_KEY})
        processes = [
            await asyncio.create_subprocess_exec(
                sys.executable, "-c", script, cwd=BACKEND, env=env, stderr=asyncio.subprocess.PIPE
            )
            for _ in range(2)
        ]
        waiters = text(
            "SELECT count(*) FROM pg_locks JOIN pg_database ON pg_database.oid = pg_locks.database "
            "WHERE datname = current_database() AND locktype = 'advisory' AND NOT granted"
        )
        waiting = 0
        for _ in range(300):
            waiting = await holder.scalar(waiters)
            if waiting == 2:
                break
            await asyncio.sleep(0.1)
        assert waiting == 2
        assert await holder.run_sync(lambda sync: inspect(sync).get_table_names()) == []

    logs = [(await process.communicate())[1].decode() for process in processes]

    assert [process.returncode for process in processes] == [0, 0]
    assert sorted(f"migrated from an empty database to revision {HEAD}" in log for log in logs) == [False, True]
    assert sorted(f"Database schema up to date (revision {HEAD})" in log for log in logs) == [False, True]
    assert await run(revision) == HEAD
    assert await run(model_differences) == []


@both
async def test_an_older_release_does_not_start_on_a_database_a_newer_one_migrated() -> None:
    """Why a rollback restores the backup (migrations/README.md, "Rolling back")."""
    await db.init_db()
    async with db.engine.begin() as connection:
        await connection.execute(text("UPDATE alembic_version SET version_num = '0099'"))

    with pytest.raises(CommandError, match="Can't locate revision identified by '0099'"):
        await db.init_db()

    assert await run(revision) == "0099"
    assert await run(model_differences) == []


# ---------------- Later migrations ----------------

LATER_REVISION = '''"""A later migration, written by the test."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade() -> None:
{body}
'''
# SQLite copies the table (and drops the old one), Postgres alters it.
WIDEN_USERNAME = """    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", existing_type=sa.String(64), type_=sa.String(80), existing_nullable=False)"""


@pytest.fixture
def later_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    """A copy of the migrations, used by start-up, plus a revision 0003 with the given upgrade body. Returns an
    alembic.ini for the command line on the same copy."""
    directory = tmp_path / "migrations"
    shutil.copytree(db.MIGRATIONS_DIR, directory, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(db, "MIGRATIONS_DIR", directory)
    ini = tmp_path / "alembic.ini"
    ini.write_text(
        (BACKEND / "alembic.ini")
        .read_text()
        .replace("%(here)s/migrations", str(directory))
        .replace("prepend_sys_path = %(here)s", f"prepend_sys_path = {BACKEND}")
    )

    def add(body: str) -> Path:
        (directory / "versions" / "0003_later.py").write_text(LATER_REVISION.format(head=HEAD, body=body))
        return ini

    return add


def username_column(connection: Connection) -> Any:
    return next(column for column in inspect(connection).get_columns("users") if column["name"] == "username")


def sqlite_foreign_keys(connection: Connection) -> int | None:
    return connection.exec_driver_sql("PRAGMA foreign_keys").scalar()


@both
@pytest.mark.parametrize("by", ["start-up", "command line"])
async def test_a_migration_that_copies_a_table_keeps_the_rows_referring_to_it(
    by: str, later_migration: Callable[[str], Path]
) -> None:
    """On SQLite a batch operation copies the table and drops the old one; with foreign keys on, that DROP would
    delete the user's documents, conversations and messages (ON DELETE CASCADE) and null their references."""
    await db.init_db()
    rows = await add_rows()
    ini = later_migration(WIDEN_USERNAME)

    if by == "start-up":
        await db.init_db()
    else:
        code, output = await alembic_command("-c", str(ini), "upgrade", "head")
        assert code == 0, output

    assert await run(revision) == "0003"
    assert await run(row_counts) == rows
    assert (await run(username_column))["type"].length == 80
    async with db.engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            assert await connection.run_sync(sqlite_foreign_keys) == 1  # on again for the app
        await connection.execute(text("DELETE FROM users"))
        assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0


async def test_a_migration_leaving_rows_that_refer_to_missing_ones_is_rolled_back(
    later_migration: Callable[[str], Path],
) -> None:
    """SQLite migrates with foreign keys off, so it checks them before committing (Postgres enforces them)."""
    if db.engine.dialect.name != "sqlite":
        pytest.skip("SQLite only: Postgres enforces foreign keys during the migration")
    await db.init_db()
    rows = await add_rows()
    later_migration('    op.execute("DELETE FROM users")\n' + WIDEN_USERNAME)

    with pytest.raises(RuntimeError, match="refer to rows that don't exist .*, so it was rolled back") as refused:
        await db.init_db()

    assert "documents row 11 -> users" in str(refused.value)
    assert await run(revision) == HEAD
    assert await run(row_counts) == rows
    assert (await run(username_column))["type"].length == 64
    assert await run(sqlite_foreign_keys) == 1


# ---------------- The alembic command line ----------------


async def alembic_command(*arguments: str) -> tuple[int | None, str]:
    """`alembic ...` in a new process, on the test's database."""
    env = {**os.environ, "DATABASE_URL": db.engine.url.render_as_string(hide_password=False)}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *arguments,
        cwd=BACKEND,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output = (await process.communicate())[0].decode()
    return process.returncode, output


@both
async def test_the_command_line_migrates_and_finds_no_drift() -> None:
    assert (await alembic_command("upgrade", "head"))[0] == 0
    code, output = await alembic_command("check")
    assert code == 0, output
    assert "No new upgrade operations detected" in output
    code, output = await alembic_command("current")
    assert code == 0 and f"{HEAD} (head)" in output
    assert await run(model_differences) == []


@both
async def test_the_command_line_leaves_a_database_made_before_migrations_unchanged() -> None:
    """Only the app's start-up adopts such a database; `alembic upgrade` fails on the first existing table and rolls
    back, its alembic_version table included (on SQLite too)."""
    await create_all()
    await add_rows()
    before = await run(snapshot)

    code, output = await alembic_command("upgrade", "head")

    assert code != 0 and "already exists" in output
    assert await run(snapshot) == before
