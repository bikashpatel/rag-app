"""What only Postgres can show (SQLite has no row locks): the guards between several backend processes, with the app
itself on Postgres. Run with TEST_POSTGRES_URL set (tests/README.md); skipped otherwise. The in-process asyncio locks
are replaced by a fresh lock per caller, so only the database serializes, as between two processes."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from conftest import POSTGRES_URL, ApiClient, txt_bytes, upload
from sqlalchemy import inspect, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app import db
from app.db import Base, SessionLocal
from app.models import Document
from app.services import storage

pytestmark = pytest.mark.postgres

CreateUser = Callable[..., Awaitable[ApiClient]]


class FreshLocks(dict[int, asyncio.Lock]):
    """A new lock for every caller: an asyncio lock covers only one process."""

    def setdefault(self, key: int, default: asyncio.Lock | None = None) -> asyncio.Lock:  # type: ignore[override]
        return asyncio.Lock()

    def __delitem__(self, key: int) -> None:
        pass


@pytest.fixture(autouse=True)
def separate_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "_upload_locks", FreshLocks())


@pytest.fixture
def slow_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The quota totals take a while, so parallel uploads all reach the checks before any of them commits."""
    totals = storage.owned_totals

    async def slow_totals(session: AsyncSession, user_id: int) -> tuple[int, int]:
        found = await totals(session, user_id)
        await asyncio.sleep(0.1)
        return found

    monkeypatch.setattr(storage, "owned_totals", slow_totals)


async def owned_documents(user_id: int | None) -> int:
    async with SessionLocal() as session:
        return len(list(await session.scalars(select(Document.id).where(Document.owner_id == user_id))))


async def test_parallel_uploads_stop_at_the_document_limit(create_user: CreateUser, slow_checks: None) -> None:
    alice = await create_user("alice", max_documents=2)

    notes = [txt_bytes(f"Note {number} about the harbour tides.") for number in range(6)]

    responses = await asyncio.gather(*(upload(alice, f"note{number}.txt", data) for number, data in enumerate(notes)))

    assert sorted(response.status_code for response in responses) == [201, 201, 409, 409, 409, 409]
    assert await owned_documents(alice.user_id) == 2


async def test_parallel_uploads_stop_at_the_storage_limit(create_user: CreateUser, slow_checks: None) -> None:
    alice = await create_user("alice", max_storage_mb=1)
    chunk = "tide " * 80_000  # 400,000 bytes: two fit in 1 MiB, three don't

    responses = await asyncio.gather(
        *(upload(alice, f"log{number}.txt", txt_bytes(f"{number} {chunk}")) for number in range(5))
    )

    assert sorted(response.status_code for response in responses) == [201, 201, 409, 409, 409]
    refused = [response.json()["detail"] for response in responses if response.status_code == 409]
    assert all("storage limit of 1 MB" in detail for detail in refused)
    assert await owned_documents(alice.user_id) == 2


async def test_parallel_uploads_of_one_file_make_one_document(create_user: CreateUser, slow_checks: None) -> None:
    alice = await create_user("alice", max_documents=10)  # the totals are read after the duplicate check
    data = txt_bytes("The same tide table, uploaded from several tabs at once.")

    responses = await asyncio.gather(*(upload(alice, f"copy{number}.txt", data) for number in range(4)))

    assert sorted(response.status_code for response in responses) == [201, 409, 409, 409]
    assert await owned_documents(alice.user_id) == 1


async def test_processes_starting_together_create_the_schema_once() -> None:
    """The backend and the worker run the migrations at start-up; the advisory lock in init_db keeps them apart (with
    separate processes in test_migrations.py)."""
    await asyncio.gather(*(db.init_db() for _ in range(4)))

    async with db.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert set(tables) == {*Base.metadata.tables, "alembic_version"}


async def test_sessions_run_in_utc_whatever_the_databases_time_zone() -> None:
    url = make_url(POSTGRES_URL)
    plain = create_async_engine(url, poolclass=NullPool)  # each connection new, so it sees the ALTER
    database = '"' + str(url.database).replace('"', '""') + '"'
    async with plain.begin() as connection:
        await connection.execute(text(f"ALTER DATABASE {database} SET timezone TO 'Asia/Kathmandu'"))
    app_engine = db.make_engine(url)
    day = text("SELECT current_setting('TimeZone'), date(timestamptz '2026-10-05 18:45:00+00')::text")
    try:
        async with plain.connect() as connection:
            assert tuple((await connection.execute(day)).one()) == ("Asia/Kathmandu", "2026-10-06")
        async with app_engine.connect() as connection:
            assert tuple((await connection.execute(day)).one()) == ("UTC", "2026-10-05")
    finally:
        async with plain.begin() as connection:
            await connection.execute(text(f"ALTER DATABASE {database} RESET timezone"))
        await plain.dispose()
        await app_engine.dispose()
