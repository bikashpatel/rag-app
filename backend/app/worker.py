"""Ingestion worker process: `python -m app.worker` (SPEC section 7)."""

import asyncio
import contextlib
import logging
import signal

from redis.exceptions import RedisError

from app.config import settings
from app.db import close_db, init_db
from app.redis_client import INGEST_QUEUE, close_redis, get_redis
from app.schemas import MAX_INT
from app.services.ingestion import process_document, reclaim_interrupted, recover_pending, requeue_orphans
from app.services.vectorstore import close_qdrant

logger = logging.getLogger("app.worker")

_DB_STARTUP_SECONDS = 60
_POLL_SECONDS = 5
_MAX_BACKOFF_SECONDS = 30
_SHUTDOWN_GRACE_SECONDS = 60
# How often to look for pending documents missing from the queue, and how old they must be.
_SWEEP_SECONDS = 60
_ORPHAN_AGE_SECONDS = 10
# How long a document must have been processing, without a job of this worker running it, to be retried.
_INTERRUPTED_AGE_SECONDS = 60


class Worker:
    """Pops document ids from the queue and processes them with bounded concurrency.

    A job is popped only when a slot is free, so stopping never strands a popped id: running jobs get a
    grace period, and anything still unfinished stays "processing" until recover_pending() on the next start.
    A job that ends without recording a result (e.g. the database failed its final update) leaves its document
    "processing" too; the periodic sweep retries it.
    """

    def __init__(self, concurrency: int) -> None:
        self._concurrency = max(1, concurrency)
        self._tasks: dict[asyncio.Task[None], int] = {}  # running job -> document id
        self._stopping = asyncio.Event()
        self._slot_freed = asyncio.Event()

    def stop(self) -> None:
        if not self._stopping.is_set():
            logger.info("Shutdown requested; no new jobs will be started")
            self._stopping.set()
        self._slot_freed.set()

    async def run(self) -> None:
        if not await self._init_db() or not await self._recover():
            return
        await self._consume()
        await self._drain()

    async def _pause(self, seconds: float) -> None:
        """Sleep, waking early on shutdown."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), seconds)

    async def _init_db(self) -> bool:
        """Create tables, retrying while the database starts up. False if shutdown came first."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _DB_STARTUP_SECONDS
        delay = 1.0
        while not self._stopping.is_set():
            try:
                await init_db()
                return True
            except Exception as exc:
                if loop.time() + delay > deadline:
                    raise
                logger.warning("Database not ready (%s); retrying in %.0fs", exc, delay)
                await self._pause(delay)
                delay = min(delay * 2, 10.0)
        return False

    async def _recover(self) -> bool:
        """Re-queue unfinished documents, retrying while Redis is unavailable. False if shutdown came first."""
        delay = 1.0
        while not self._stopping.is_set():
            try:
                count = await recover_pending()
            except Exception as exc:
                logger.warning("Could not re-queue unfinished documents (%s); retrying in %.0fs", exc, delay)
                await self._pause(delay)
                delay = min(delay * 2, _MAX_BACKOFF_SECONDS)
                continue
            if count:
                logger.info("Re-queued %d unfinished document(s)", count)
            return True
        return False

    async def _consume(self) -> None:
        logger.info("Worker ready (concurrency %d)", self._concurrency)
        loop = asyncio.get_running_loop()
        delay = 1.0
        next_sweep = loop.time() + _SWEEP_SECONDS
        while not self._stopping.is_set():
            if len(self._tasks) >= self._concurrency:
                self._slot_freed.clear()
                await self._slot_freed.wait()
                continue
            try:
                item = await get_redis().blpop([INGEST_QUEUE], timeout=_POLL_SECONDS)
            except (RedisError, OSError) as exc:
                logger.warning("Redis unavailable (%s); retrying in %.0fs", exc, delay)
                await self._pause(delay)
                delay = min(delay * 2, _MAX_BACKOFF_SECONDS)
                next_sweep = loop.time()  # uploads during the outage couldn't queue their documents
                continue
            delay = 1.0
            if item is not None:
                self._start(item[1])
            if loop.time() >= next_sweep:
                await self._requeue_orphans()
                next_sweep = loop.time() + _SWEEP_SECONDS

    async def _requeue_orphans(self) -> None:
        try:
            # Only this coroutine starts jobs, so the running set can only shrink meanwhile.
            count = await reclaim_interrupted(set(self._tasks.values()), _INTERRUPTED_AGE_SECONDS)
        except Exception as exc:
            logger.warning("Could not look for interrupted documents (%s)", exc)
        else:
            if count:
                logger.info("Queued %d document(s) whose processing ended without a result", count)
        try:
            count = await requeue_orphans(_ORPHAN_AGE_SECONDS)
        except Exception as exc:
            logger.warning("Could not look for unqueued documents (%s)", exc)
            return
        if count:
            logger.info("Queued %d pending document(s) that were missing from the queue", count)

    def _start(self, payload: str | bytes) -> None:
        text = payload if isinstance(payload, str) else payload.decode("utf-8", "replace")
        doc_id = int(text) if text.isascii() and text.isdigit() and len(text) <= 10 else 0
        if not 1 <= doc_id <= MAX_INT:
            logger.warning("Skipping invalid queue entry %r", text[:100])
            return
        task = asyncio.create_task(self._process(doc_id), name=f"ingest-{doc_id}")
        self._tasks[task] = doc_id
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)
        self._slot_freed.set()

    async def _process(self, doc_id: int) -> None:
        try:
            await process_document(doc_id)
        except Exception:
            logger.exception("Unexpected error while processing document %s", doc_id)

    async def _drain(self) -> None:
        if not self._tasks:
            return
        logger.info("Waiting up to %ds for %d running job(s)", _SHUTDOWN_GRACE_SECONDS, len(self._tasks))
        _, unfinished = await asyncio.wait(set(self._tasks), timeout=_SHUTDOWN_GRACE_SECONDS)
        for task in unfinished:
            task.cancel()
        await asyncio.gather(*unfinished, return_exceptions=True)
        if unfinished:
            logger.warning("Cancelled %d unfinished job(s); they are retried on the next start", len(unfinished))


async def main() -> None:
    worker = Worker(settings.worker_concurrency)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # Windows event loops have no signal handlers
            loop.add_signal_handler(sig, worker.stop)
    try:
        await worker.run()
    finally:
        await close_redis()
        await close_qdrant()
        await close_db()
    logger.info("Worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
