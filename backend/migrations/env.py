"""Alembic's environment. app.db.init_db passes its own connection (config.attributes["connection"]), inside its
transaction and under the schema lock; the alembic command line connects with the app's settings (DATABASE_URL or the
POSTGRES_* variables) through app.db.make_engine and migrates the same way (app.db.schema_transaction: the lock, and on
SQLite foreign keys off until a check before the commit). See README.md."""

import asyncio
from logging.config import fileConfig
from typing import Any, Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import TypeDecorator
from sqlalchemy.engine import Connection

from app import models  # noqa: F401  (registers the models on Base.metadata)
from app.config import settings
from app.db import COMPARE_OPTIONS, Base, make_engine, schema_transaction


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | Literal[False]:
    """A TypeDecorator (models.UTCDateTime) renders as its database type, so no migration imports the app's models."""
    if type_ == "type" and isinstance(obj, TypeDecorator):
        return f"sa.{obj.impl!r}"
    return False


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        # Autogenerate writes batch operations, which SQLite runs as a table copy and other databases as plain ALTERs.
        render_as_batch=True,
        render_item=render_item,
        **COMPARE_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_from_the_command_line() -> None:
    engine = make_engine(settings.sqlalchemy_url)
    try:
        async with schema_transaction(engine) as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) mode isn't supported: run the migrations against a database.")
shared = context.config.attributes.get("connection")
if shared is not None:
    run_migrations(shared)
else:
    if context.config.config_file_name is not None:
        fileConfig(context.config.config_file_name, disable_existing_loggers=False)
    asyncio.run(run_from_the_command_line())
