from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import URL, event, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Arbitrary constant used as a Postgres advisory-lock key so that the backend
# and the worker never run create_all concurrently.
_SCHEMA_LOCK_KEY = 4242001


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


async def init_db() -> None:
    """Create tables if missing. Safe to call from several processes."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()
