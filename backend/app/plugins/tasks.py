"""Background tasks of plugins that must finish even when the request or answer that started them is cancelled (a send,
an import being stored, a draft): strong references until they end, and a drain at shutdown (brief 4.9)."""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The event loop keeps only weak references to tasks.
_running: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, T], *, name: str) -> asyncio.Task[T]:
    """Run `coro` as a task that lives until it ends; await it through asyncio.shield to survive a cancel. An exception
    it ends with is logged (its caller may have been cancelled before it could see it)."""
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_finished)
    return task


def _finished(task: asyncio.Task[Any]) -> None:
    _running.discard(task)
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("Plugin task %s failed", task.get_name(), exc_info=exc)


def running() -> set[asyncio.Task[Any]]:
    return set(_running)


async def drain(timeout: float) -> None:
    """At shutdown: wait up to `timeout` seconds for the running tasks, then log the names of those still running."""
    if not _running:
        return
    _, pending = await asyncio.wait(set(_running), timeout=timeout)
    if pending:
        logger.warning(
            "Shutting down with %d plugin task(s) still running: %s",
            len(pending),
            ", ".join(sorted(task.get_name() for task in pending)),
        )
