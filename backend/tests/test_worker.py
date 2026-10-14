"""Ingestion worker: queue recovery, the BLPOP loop, bounded concurrency, graceful stop and retries.

The worker runs in-process on the test's FakeRedis. Tests wait on events the jobs set; the timeout is only a
guard against a hung worker.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from conftest import ApiClient, ingest, txt_bytes, upload
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from app import worker
from app.db import SessionLocal
from app.models import Document, utcnow
from app.redis_client import INGEST_QUEUE, get_redis, ingest_attempts_key
from app.routers import documents
from app.services import ingestion, vectorstore
from app.services.ingestion import MAX_INTERRUPTED_RUNS, recover_pending, requeue_orphans

CreateUser = Callable[..., Awaitable[ApiClient]]
Job = Callable[[int], Awaitable[None]]

GUARD_SECONDS = 10


class Jobs:
    """Stands in for process_document inside the worker: records ids and signals once `expected` jobs finished."""

    def __init__(self, expected: int, job: Job = ingestion.process_document) -> None:
        self.expected = expected
        self.job = job
        self.started: list[int] = []
        self.finished: list[int] = []
        self._done = asyncio.Event()

    async def __call__(self, doc_id: int) -> None:
        self.started.append(doc_id)
        try:
            await self.job(doc_id)
        finally:
            self.finished.append(doc_id)
            if len(self.finished) >= self.expected:
                self._done.set()

    async def wait(self) -> None:
        await asyncio.wait_for(self._done.wait(), GUARD_SECONDS)


async def do_nothing(doc_id: int) -> None:
    return None


@pytest.fixture(autouse=True)
def short_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_POLL_SECONDS", 0.05)


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Backoff delays the worker asked for; the pauses themselves return at once."""
    requested: list[float] = []

    async def pause(self: worker.Worker, seconds: float) -> None:
        requested.append(seconds)

    monkeypatch.setattr(worker.Worker, "_pause", pause)
    return requested


def use_jobs(monkeypatch: pytest.MonkeyPatch, jobs: Jobs) -> Jobs:
    monkeypatch.setattr(worker, "process_document", jobs)
    return jobs


def queue_after_recovery(monkeypatch: pytest.MonkeyPatch, payloads: list[str]) -> None:
    """Replace the queue right after the worker's start-up recovery (which rebuilds it from the database)."""
    recover = worker.recover_pending

    async def recover_then_queue() -> int:
        count = await recover()
        redis = get_redis()
        await redis.delete(INGEST_QUEUE)
        await redis.rpush(INGEST_QUEUE, *payloads)
        return count

    monkeypatch.setattr(worker, "recover_pending", recover_then_queue)


@asynccontextmanager
async def running(instance: worker.Worker) -> AsyncIterator[None]:
    task = asyncio.create_task(instance.run())
    try:
        yield
    finally:
        instance.stop()
        await asyncio.wait_for(task, GUARD_SECONDS)


def worker_messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.worker" and record.levelno >= level]


async def queued() -> list[str]:
    return await get_redis().lrange(INGEST_QUEUE, 0, -1)


async def uploaded_ids(client: ApiClient, count: int) -> list[int]:
    ids = []
    for number in range(count):
        response = await upload(client, f"doc{number}.txt", txt_bytes(f"Document number {number} about tide tables."))
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


async def set_statuses(statuses: dict[int, str]) -> None:
    async with SessionLocal() as session:
        for doc_id, status in statuses.items():
            await session.execute(update(Document).where(Document.id == doc_id).values(status=status))
        await session.commit()


async def statuses() -> dict[int, str]:
    async with SessionLocal() as session:
        return dict((await session.execute(select(Document.id, Document.status))).tuples().all())


# ---------------- recover_pending ----------------


async def test_recover_pending_resets_processing_and_requeues_in_id_order(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    ids = await uploaded_ids(alice, 5)
    await set_statuses({ids[1]: "processing", ids[2]: "ready", ids[3]: "failed", ids[4]: "processing"})
    redis = get_redis()
    await redis.delete(INGEST_QUEUE)
    await redis.rpush(INGEST_QUEUE, "999", str(ids[2]), str(ids[0]), str(ids[0]))

    count = await recover_pending()

    assert count == 3
    assert await queued() == [str(ids[0]), str(ids[1]), str(ids[4])]
    assert await statuses() == {
        ids[0]: "pending",
        ids[1]: "pending",
        ids[2]: "ready",
        ids[3]: "failed",
        ids[4]: "pending",
    }


INTERRUPTED = "Processing was interrupted 3 times; the file may be too large or complex to process."


async def errors() -> dict[int, str | None]:
    async with SessionLocal() as session:
        return dict((await session.execute(select(Document.id, Document.error))).tuples().all())


async def test_a_document_that_keeps_crashing_the_worker_fails_instead_of_being_retried_forever(
    create_user: CreateUser,
) -> None:
    alice = await create_user("alice")
    [doc_id] = await uploaded_ids(alice, 1)
    redis = get_redis()

    for run in range(1, MAX_INTERRUPTED_RUNS + 1):
        assert await recover_pending() == 1, run  # the worker (re)starts
        assert await ingestion._claim(doc_id) is not None  # ...starts the document and is killed meanwhile
        assert await redis.get(ingest_attempts_key(doc_id)) == str(run)
    count = await recover_pending()

    assert count == 0
    assert await queued() == []
    assert await statuses() == {doc_id: "failed"}
    assert await errors() == {doc_id: INTERRUPTED}
    assert await redis.exists(ingest_attempts_key(doc_id)) == 0
    await redis.set(ingest_attempts_key(doc_id), "2")
    assert (await alice.post(f"/api/documents/{doc_id}/reprocess")).status_code == 200
    assert await redis.exists(ingest_attempts_key(doc_id)) == 0  # a deliberate retry starts over


async def test_recover_pending_drops_the_vectors_of_a_document_it_gives_up_on(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    item = await ingest(alice, "notes.txt", txt_bytes("Tide tables for the northern harbour in spring."))
    await set_statuses({item["id"]: "processing"})
    await get_redis().set(ingest_attempts_key(item["id"]), str(MAX_INTERRUPTED_RUNS))
    client = vectorstore.get_qdrant()
    collections = [collection.name for collection in (await client.get_collections()).collections]

    await recover_pending()

    assert await statuses() == {item["id"]: "failed"}
    assert sum([(await client.count(name, exact=True)).count for name in collections]) == 0


async def test_recover_pending_with_nothing_unfinished_empties_the_queue(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    ids = await uploaded_ids(alice, 2)
    await set_statuses({ids[0]: "ready", ids[1]: "failed"})

    count = await recover_pending()

    assert count == 0
    assert await queued() == []


# ---------------- The worker loop ----------------


async def test_worker_processes_documents_left_from_a_previous_run(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    ids = await uploaded_ids(alice, 3)
    await set_statuses({ids[1]: "processing", ids[2]: "ready"})
    await get_redis().delete(INGEST_QUEUE)
    jobs = use_jobs(monkeypatch, Jobs(expected=2))

    async with running(worker.Worker(2)):
        await jobs.wait()

    assert sorted(jobs.finished) == ids[:2]
    assert await statuses() == {ids[0]: "ready", ids[1]: "ready", ids[2]: "ready"}
    assert await queued() == []


async def test_worker_skips_invalid_queue_entries(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    alice = await create_user("alice")
    [doc_id] = await uploaded_ids(alice, 1)
    invalid = ["abc", "", "0", "-3", "1.5", " 7", "²", "2147483648", "99999999999"]
    queue_after_recovery(monkeypatch, [*invalid, "424242", str(doc_id)])
    jobs = use_jobs(monkeypatch, Jobs(expected=2))

    with caplog.at_level(logging.WARNING, logger="app.worker"):
        async with running(worker.Worker(1)):
            await jobs.wait()

    assert jobs.started == [424242, doc_id]
    assert await statuses() == {doc_id: "ready"}
    assert await queued() == []
    assert worker_messages(caplog, logging.WARNING) == [
        f"Skipping invalid queue entry {payload!r}" for payload in invalid
    ]


async def test_worker_runs_at_most_its_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    gate, two_running = asyncio.Event(), asyncio.Event()
    active = peak = 0

    async def job(doc_id: int) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_running.set()
        await gate.wait()
        active -= 1

    queue_after_recovery(monkeypatch, ["1", "2", "3", "4"])
    jobs = use_jobs(monkeypatch, Jobs(expected=4, job=job))

    async with running(worker.Worker(2)):
        await asyncio.wait_for(two_running.wait(), GUARD_SECONDS)
        assert await queued() == ["3", "4"], "no job is popped while every slot is busy"
        gate.set()
        await jobs.wait()

    assert peak == 2
    assert sorted(jobs.finished) == [1, 2, 3, 4]


async def test_stopping_finishes_running_jobs_and_leaves_the_rest_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    gate, started = asyncio.Event(), asyncio.Event()

    async def job(doc_id: int) -> None:
        started.set()
        await gate.wait()

    queue_after_recovery(monkeypatch, ["1", "2", "3"])
    jobs = use_jobs(monkeypatch, Jobs(expected=1, job=job))
    instance = worker.Worker(1)
    task = asyncio.create_task(instance.run())

    await asyncio.wait_for(started.wait(), GUARD_SECONDS)
    instance.stop()
    gate.set()
    await asyncio.wait_for(task, GUARD_SECONDS)

    assert (jobs.started, jobs.finished) == ([1], [1])
    assert await queued() == ["2", "3"]


async def test_a_failing_job_does_not_stop_the_worker(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def job(doc_id: int) -> None:
        if doc_id == 1:
            raise RuntimeError("database hiccup")

    queue_after_recovery(monkeypatch, ["1", "2"])
    jobs = use_jobs(monkeypatch, Jobs(expected=2, job=job))

    with caplog.at_level(logging.ERROR, logger="app.worker"):
        async with running(worker.Worker(1)):
            await jobs.wait()

    assert jobs.finished == [1, 2]
    assert worker_messages(caplog, logging.ERROR) == ["Unexpected error while processing document 1"]


async def test_a_stopped_worker_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected() -> None:
        raise AssertionError("init_db must not run after stop()")

    monkeypatch.setattr(worker, "init_db", unexpected)
    instance = worker.Worker(1)
    instance.stop()

    await asyncio.wait_for(instance.run(), GUARD_SECONDS)


# ---------------- Retries ----------------


async def test_worker_backs_off_while_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, pauses: list[float]
) -> None:
    redis = get_redis()
    blpop = redis.blpop
    calls = 0

    async def flaky_blpop(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls <= 6 or calls == 8:
            raise RedisConnectionError("Connection refused")
        return await blpop(*args, **kwargs)

    monkeypatch.setattr(redis, "blpop", flaky_blpop)
    queue_after_recovery(monkeypatch, ["7", "8"])
    jobs = use_jobs(monkeypatch, Jobs(expected=2, job=do_nothing))

    async with running(worker.Worker(2)):
        await jobs.wait()

    assert jobs.finished == [7, 8]
    assert pauses == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 1.0], "doubling up to 30 s, reset after a success"


async def test_worker_retries_until_the_database_is_up(monkeypatch: pytest.MonkeyPatch, pauses: list[float]) -> None:
    init_db = worker.init_db
    attempts = 0

    async def flaky_init_db() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("connection refused")
        await init_db()

    monkeypatch.setattr(worker, "init_db", flaky_init_db)
    queue_after_recovery(monkeypatch, ["5"])
    jobs = use_jobs(monkeypatch, Jobs(expected=1, job=do_nothing))

    async with running(worker.Worker(1)):
        await jobs.wait()

    assert attempts == 3
    assert pauses == [1.0, 2.0]
    assert jobs.finished == [5]


async def test_worker_gives_up_when_the_database_never_comes_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def database_down() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(worker, "init_db", database_down)
    monkeypatch.setattr(worker, "_DB_STARTUP_SECONDS", 0)

    with pytest.raises(OSError, match="connection refused"):
        await asyncio.wait_for(worker.Worker(1).run(), GUARD_SECONDS)


async def test_worker_retries_queue_recovery_while_redis_is_down(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch, pauses: list[float]
) -> None:
    alice = await create_user("alice")
    [doc_id] = await uploaded_ids(alice, 1)
    recover = worker.recover_pending
    attempts = 0

    async def flaky_recover() -> int:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RedisConnectionError("Connection refused")
        return await recover()

    monkeypatch.setattr(worker, "recover_pending", flaky_recover)
    jobs = use_jobs(monkeypatch, Jobs(expected=1))

    async with running(worker.Worker(1)):
        await jobs.wait()

    assert attempts == 3
    assert pauses == [1.0, 2.0]
    assert await statuses() == {doc_id: "ready"}


# ---------------- Pending documents missing from the queue ----------------


async def backdate(doc_ids: list[int], seconds: float) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(Document).where(Document.id.in_(doc_ids)).values(updated_at=utcnow() - timedelta(seconds=seconds))
        )
        await session.commit()


async def upload_while_redis_is_down(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> int:
    """Upload a document whose enqueue fails: the upload still succeeds and the document stays pending."""

    async def redis_down(doc_id: int) -> None:
        raise RedisConnectionError("Connection refused")

    with monkeypatch.context() as patch:
        patch.setattr(documents, "enqueue_document", redis_down)
        [doc_id] = await uploaded_ids(client, 1)
    assert await statuses() == {doc_id: "pending"}
    assert await queued() == []
    return doc_id


async def test_requeue_orphans_queues_old_pending_documents_missing_from_the_queue(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    ids = await uploaded_ids(alice, 5)
    await set_statuses({ids[2]: "processing", ids[3]: "ready"})
    await backdate(ids[:4], 120)
    redis = get_redis()
    await redis.delete(INGEST_QUEUE)
    await redis.rpush(INGEST_QUEUE, str(ids[0]))

    assert await requeue_orphans(60) == 1
    assert await queued() == [str(ids[0]), str(ids[1])], "running, finished and fresh documents are left alone"
    assert await requeue_orphans(60) == 0


async def test_worker_queues_documents_uploaded_while_redis_was_down(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch, pauses: list[float]
) -> None:
    alice = await create_user("alice")
    monkeypatch.setattr(worker, "_ORPHAN_AGE_SECONDS", 0)
    redis = get_redis()
    blpop = redis.blpop
    polling = asyncio.Event()
    outage = False

    async def flaky_blpop(*args: Any, **kwargs: Any) -> Any:
        nonlocal outage
        polling.set()
        if outage:
            outage = False
            raise RedisConnectionError("Connection refused")
        return await blpop(*args, **kwargs)

    monkeypatch.setattr(redis, "blpop", flaky_blpop)
    jobs = use_jobs(monkeypatch, Jobs(expected=1))

    async with running(worker.Worker(1)):
        await asyncio.wait_for(polling.wait(), GUARD_SECONDS)
        doc_id = await upload_while_redis_is_down(alice, monkeypatch)
        outage = True
        await jobs.wait()

    assert jobs.finished == [doc_id]
    assert await statuses() == {doc_id: "ready"}
    assert pauses == [1.0]


async def test_worker_retries_a_document_whose_final_update_failed(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    [doc_id] = await uploaded_ids(alice, 1)
    monkeypatch.setattr(worker, "_SWEEP_SECONDS", 0)
    monkeypatch.setattr(worker, "_INTERRUPTED_AGE_SECONDS", 0)
    update_document = ingestion._update_document
    outage = {"ready", "failed"}  # the database goes away just as the first run records its result

    async def flaky_update(*args: Any, **values: Any) -> bool:
        if values.get("status") in outage:
            outage.discard(values["status"])
            raise OperationalError("UPDATE documents", {}, ConnectionError("server closed the connection"))
        return await update_document(*args, **values)

    monkeypatch.setattr(ingestion, "_update_document", flaky_update)
    jobs = use_jobs(monkeypatch, Jobs(expected=2))

    async with running(worker.Worker(1)):
        await jobs.wait()

    assert jobs.finished == [doc_id, doc_id]
    assert await statuses() == {doc_id: "ready"}  # without a restart of the worker


async def test_the_sweep_leaves_running_documents_alone(create_user: CreateUser) -> None:
    alice = await create_user("alice")
    ids = await uploaded_ids(alice, 2)
    await set_statuses({doc_id: "processing" for doc_id in ids})
    await get_redis().delete(INGEST_QUEUE)

    assert await ingestion.reclaim_interrupted({ids[0]}, 0) == 1

    assert await statuses() == {ids[0]: "processing", ids[1]: "pending"}
    assert await queued() == [str(ids[1])]


async def test_worker_periodically_queues_pending_documents_missing_from_the_queue(
    create_user: CreateUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await create_user("alice")
    monkeypatch.setattr(worker, "_SWEEP_SECONDS", 0)
    monkeypatch.setattr(worker, "_ORPHAN_AGE_SECONDS", 0)
    redis = get_redis()
    blpop = redis.blpop
    polling = asyncio.Event()

    async def watched_blpop(*args: Any, **kwargs: Any) -> Any:
        polling.set()
        return await blpop(*args, **kwargs)

    monkeypatch.setattr(redis, "blpop", watched_blpop)
    jobs = use_jobs(monkeypatch, Jobs(expected=1))

    async with running(worker.Worker(1)):
        await asyncio.wait_for(polling.wait(), GUARD_SECONDS)  # start-up recovery is over
        doc_id = await upload_while_redis_is_down(alice, monkeypatch)
        await jobs.wait()

    assert jobs.finished == [doc_id]
    assert await statuses() == {doc_id: "ready"}
