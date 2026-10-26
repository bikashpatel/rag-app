"""System checks shared by the readiness route, Admin -> System and the monitor (SPEC.md section 10).

Every check has a timeout and turns any failure into a status with a short detail. Details never hold an exception's
message (it can name hosts, users or URLs), document names or usernames: they go into alerts.
"""

import asyncio
import os
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

import httpx
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import func, select, text

from app.config import settings
from app.db import SessionLocal
from app.models import Document, PluginConnection
from app.ops import backup_status, heartbeat
from app.ops.settings import ops_settings
from app.plugins import connections, registry
from app.redis_client import INGEST_QUEUE, get_redis
from app.schemas_system import (
    BackupInfo,
    CheckId,
    CheckStatus,
    DiskInfo,
    QueueInfo,
    SystemCheck,
    WorkerInfo,
)
from app.services.vectorstore import get_qdrant

T = TypeVar("T")

CHECK_TIMEOUT_SECONDS = 5.0
LABELS: dict[CheckId, str] = {
    "postgres": "Database (Postgres)",
    "redis": "Redis",
    "qdrant": "Vector store (Qdrant)",
    "worker": "Ingestion worker",
    "queue": "Ingestion queue",
    "backup": "Backups",
    "disk_uploads": "Disk space for uploads",
    "disk_backups": "Disk space for backups",
    "proxy": "Web server (nginx)",
    "monitor": "Monitor and alerts",
}
# The backup service refreshes its status every 5 minutes while it waits; a backup in progress may take longer.
SCHEDULER_STALE_SECONDS = 30 * 60
BACKUP_RUNNING_GRACE_SECONDS = 6 * 3600
# A start-up backup due this long ago that hasn't started (or a backup service that hasn't written its status this long
# after this process started) is overdue.
BACKUP_START_GRACE_SECONDS = 10 * 60
_STARTED = time.time()
_SEVERITY: dict[CheckStatus, int] = {"ok": 0, "unknown": 1, "warning": 1, "critical": 2}


def check(check_id: CheckId, status: CheckStatus, detail: str) -> SystemCheck:
    return SystemCheck(id=check_id, label=LABELS[check_id], status=status, detail=detail)


def overall(checks: list[SystemCheck]) -> CheckStatus:
    worst = max((_SEVERITY[item.status] for item in checks), default=0)
    return "critical" if worst == 2 else "warning" if worst == 1 else "ok"


def describe_error(exc: BaseException) -> str:
    """What went wrong, without the exception's message."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, httpx.TimeoutException, RedisTimeoutError)):
            return "timed out"
        if isinstance(current, httpx.HTTPStatusError):
            return f"HTTP {current.response.status_code}"
        if isinstance(current, ConnectionRefusedError):
            return "connection refused"
        if isinstance(current, (OSError, httpx.TransportError, RedisConnectionError)):
            return "connection failed"
        current = current.__cause__ or current.__context__
    return f"failed ({type(exc).__name__})"


def format_bytes(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value, unit = float(size), 0
    while value >= 1024 and unit < len(units) - 1:
        value, unit = value / 1024, unit + 1
    return f"{value:.0f} {units[unit]}" if value >= 10 or unit == 0 else f"{value:.1f} {units[unit]}"


def format_age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def _utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc)


async def _guard(awaitable: Awaitable[T], timeout: float) -> tuple[T | None, str | None]:
    """(result, None), or (None, what went wrong) when it failed or took longer than `timeout` seconds."""
    try:
        return await asyncio.wait_for(awaitable, timeout), None
    except Exception as exc:
        return None, describe_error(exc)


# ---------------- Services ----------------


async def _postgres_size() -> int | None:
    async with SessionLocal() as db:
        await db.execute(text("SELECT 1"))
        if db.get_bind().dialect.name != "postgresql":
            return None
        return int(await db.scalar(text("SELECT pg_database_size(current_database())")) or 0)


async def check_postgres(timeout: float = CHECK_TIMEOUT_SECONDS) -> tuple[SystemCheck, int | None]:
    """The check and the database's size in bytes (None on SQLite)."""
    size, error = await _guard(_postgres_size(), timeout)
    if error:
        return check("postgres", "critical", f"Unreachable: {error}"), None
    detail = "Reachable" if size is None else f"Reachable; the database holds {format_bytes(size)}"
    return check("postgres", "ok", detail), size


async def check_redis(timeout: float = CHECK_TIMEOUT_SECONDS) -> SystemCheck:
    _, error = await _guard(get_redis().ping(), timeout)
    return check("redis", "critical", f"Unreachable: {error}") if error else check("redis", "ok", "Reachable")


async def check_qdrant(timeout: float = CHECK_TIMEOUT_SECONDS) -> SystemCheck:
    collections, error = await _guard(get_qdrant().get_collections(), timeout)
    if error or collections is None:
        return check("qdrant", "critical", f"Unreachable: {error}")
    count = len(collections.collections)
    return check("qdrant", "ok", f"Reachable; {count} collection{'' if count == 1 else 's'}")


async def check_worker(now: float, timeout: float = CHECK_TIMEOUT_SECONDS) -> tuple[SystemCheck, WorkerInfo]:
    beat, error = await _guard(heartbeat.read(), timeout)
    if error:
        return check("worker", "unknown", f"The heartbeat can't be read: {error}"), WorkerInfo(
            heartbeat_age_seconds=None, running_jobs=None
        )
    if beat is None:
        return check("worker", "critical", "No heartbeat: the worker isn't running"), WorkerInfo(
            heartbeat_age_seconds=None, running_jobs=None
        )
    age = beat.age(now)
    info = WorkerInfo(heartbeat_age_seconds=round(age, 1), running_jobs=beat.jobs)
    if not heartbeat.is_fresh(beat, now):
        return check("worker", "critical", f"The last heartbeat was {format_age(age)} ago"), info
    jobs = f"{beat.jobs} job{'' if beat.jobs == 1 else 's'} running"
    return check("worker", "ok", f"Heartbeat {format_age(age)} ago; {jobs}"), info


async def ready(timeout: float = 3.0) -> bool:
    """Postgres, Redis and Qdrant answer, and the worker's heartbeat is fresh."""
    now = time.time()
    (postgres, _), redis, qdrant, (worker, _) = await asyncio.gather(
        check_postgres(timeout), check_redis(timeout), check_qdrant(timeout), check_worker(now, timeout)
    )
    return all(item.status == "ok" for item in (postgres, redis, qdrant, worker))


# ---------------- Queue and documents ----------------


@dataclass
class _DocumentCounts:
    pending: int
    processing: int
    oldest_pending: datetime | None
    last_finished: datetime | None
    failed_24h: int


async def _document_counts(now: float) -> _DocumentCounts:
    async with SessionLocal() as db:
        by_status = dict(
            (
                await db.execute(
                    select(Document.status, func.count())
                    .where(Document.status.in_(("pending", "processing")))
                    .group_by(Document.status)
                )
            )
            .tuples()
            .all()
        )
        oldest = await db.scalar(select(func.min(Document.updated_at)).where(Document.status == "pending"))
        finished = await db.scalar(
            select(func.max(Document.updated_at)).where(Document.status.in_(("ready", "failed")))
        )
        failed = await db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.status == "failed", Document.updated_at >= _utc(now) - timedelta(hours=24))
        )
    return _DocumentCounts(by_status.get("pending", 0), by_status.get("processing", 0), oldest, finished, failed or 0)


def _age(moment: datetime | None, now: float) -> float | None:
    if moment is None:
        return None
    if moment.tzinfo is None:  # SQLite's func.min/func.max skip the column type's conversion
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, now - moment.timestamp())


async def check_queue(now: float, timeout: float = CHECK_TIMEOUT_SECONDS) -> tuple[SystemCheck, QueueInfo, int | None]:
    """The check, the queue's numbers and the documents that failed in the last 24 hours."""
    (counts, db_error), (queued, _) = await asyncio.gather(
        _guard(_document_counts(now), timeout), _guard(get_redis().llen(INGEST_QUEUE), timeout)
    )
    if db_error or counts is None:
        info = QueueInfo(queued=queued, pending=None, processing=None, oldest_pending_age_seconds=None)
        return check("queue", "unknown", f"The documents can't be read: {db_error}"), info, None
    oldest = _age(counts.oldest_pending, now)
    finished = _age(counts.last_finished, now)
    info = QueueInfo(
        queued=queued,
        pending=counts.pending,
        processing=counts.processing,
        oldest_pending_age_seconds=None if oldest is None else round(oldest, 1),
    )
    limit = ops_settings.alert_queue_stuck_minutes * 60
    waiting = f"{counts.pending} waiting, {counts.processing} processing"
    if oldest is not None and oldest > limit and (finished is None or finished > limit):
        detail = f"Stuck: {waiting}; the oldest has waited {format_age(oldest)} and none finished since"
        return check("queue", "warning", detail), info, counts.failed_24h
    if oldest is None and counts.processing == 0:
        return check("queue", "ok", "Idle"), info, counts.failed_24h
    longest = f"; the oldest waiting for {format_age(oldest)}" if oldest is not None else ""
    return check("queue", "ok", f"{waiting}{longest}"), info, counts.failed_24h


async def gmail_reconnect_needed(timeout: float = CHECK_TIMEOUT_SECONDS) -> int | None:
    """Gmail connections whose users must reconnect, those ended by Google's 7-day testing limit included."""

    async def count() -> int:
        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(PluginConnection.status, PluginConnection.connected_at).where(
                        PluginConnection.plugin_id == "gmail"
                    )
                )
            ).tuples()
            plugin = registry.get_plugin("gmail")
            return sum(
                1
                for status, connected_at in rows
                if status != "connected" or (plugin is not None and connections.testing_expired(plugin, connected_at))
            )

    result, _ = await _guard(count(), timeout)
    return result


# ---------------- Disks and backups ----------------


def disk_check(check_id: CheckId, free: int, total: int) -> SystemCheck:
    if total <= 0:
        return check(check_id, "unknown", "The disk size is unknown")
    percent = free / total * 100
    threshold = ops_settings.alert_disk_min_free_percent
    status: CheckStatus = "critical" if percent < threshold / 2 else "warning" if percent < threshold else "ok"
    return check(check_id, status, f"{format_bytes(free)} free of {format_bytes(total)} ({percent:.0f} %)")


def check_uploads_disk(now: float) -> tuple[SystemCheck, DiskInfo | None]:
    try:
        stats = os.statvfs(settings.upload_dir)
    except FileNotFoundError:
        return check("disk_uploads", "unknown", "The upload folder doesn't exist"), None
    except OSError:
        return check("disk_uploads", "unknown", "The upload folder can't be read"), None
    free, total = stats.f_bavail * stats.f_frsize, stats.f_blocks * stats.f_frsize
    info = DiskInfo(id="uploads", label="Uploads", free_bytes=free, total_bytes=total, checked_at=_utc(now))
    return disk_check("disk_uploads", free, total), info


def _clock(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%H:%M UTC")


def _backup_coming(status: backup_status.BackupStatusFile, now: float) -> str | None:
    """Why a missing or old backup is no problem yet: one is running, or the backup service's start-up backup is due
    soon. None when neither (or the backup service is silent)."""
    scheduler = status.scheduler
    if scheduler is None or now - scheduler.seen_at.timestamp() > SCHEDULER_STALE_SECONDS:
        return None
    if status.running_since is not None and now - status.running_since.timestamp() < BACKUP_RUNNING_GRACE_SECONDS:
        return "one is running"
    due = scheduler.catch_up_at
    if due is not None and now <= due.timestamp() + BACKUP_START_GRACE_SECONDS:
        return f"one is due at {_clock(due)}"
    return None


def check_backup(now: float) -> tuple[SystemCheck, SystemCheck, BackupInfo, DiskInfo | None]:
    """The backup check, the backup disk's check, what the backup service reported and its disk. A missing or old
    backup is "unknown" (no alert) while a new one is running or due shortly after the backup service started."""
    max_age = ops_settings.backup_max_age_hours
    off = max_age == 0
    no_disk = check("disk_backups", "unknown", "Reported by the backup service after its first run")
    try:
        status = backup_status.read(ops_settings.backup_status_file)
    except backup_status.StatusUnreadable:
        info = BackupInfo(status_seen=False, max_age_hours=max_age)
        return check("backup", "warning", "The backup status file can't be read"), no_disk, info, None
    if status is None:
        info = BackupInfo(status_seen=False, max_age_hours=max_age)
        if off:
            return check("backup", "ok", "Backup checks are off (BACKUP_MAX_AGE_HOURS=0)"), no_disk, info, None
        if now - _STARTED < BACKUP_START_GRACE_SECONDS:
            return check("backup", "unknown", "Waiting for the backup service's first report"), no_disk, info, None
        detail = "The backup service hasn't reported: it writes its status as soon as it starts"
        return check("backup", "warning", detail), no_disk, info, None

    success, failure, scheduler = status.last_success, status.last_failure, status.scheduler
    age = None if success is None else max(0.0, now - success.finished_at.timestamp())
    info = BackupInfo(
        status_seen=True,
        max_age_hours=max_age,
        last_success_at=success.finished_at if success else None,
        last_success_name=success.name if success else None,
        last_success_size_bytes=success.size_bytes if success else None,
        last_success_duration_seconds=success.duration_seconds if success else None,
        age_hours=None if age is None else round(age / 3600, 2),
        last_failure_at=failure.at if failure else None,
        last_failure_error=failure.error if failure else None,
        failing=status.failing,
        running_since=status.running_since,
        schedule=scheduler.schedule if scheduler else None,
        next_run_at=_next_backup(scheduler),
        scheduler_seen_at=scheduler.seen_at if scheduler else None,
        backups_kept=status.backups.count if status.backups else None,
        total_size_bytes=status.backups.total_bytes if status.backups else None,
    )
    disk = None
    disk_status = no_disk
    if status.disk is not None:
        disk = DiskInfo(
            id="backups",
            label="Backups",
            free_bytes=status.disk.free_bytes,
            total_bytes=status.disk.total_bytes,
            checked_at=status.updated_at,
        )
        disk_status = disk_check("disk_backups", status.disk.free_bytes, status.disk.total_bytes)

    if off:
        return check("backup", "ok", "Backup checks are off (BACKUP_MAX_AGE_HOURS=0)"), disk_status, info, disk
    if status.failing and failure is not None:
        detail = f"The last backup failed {format_age(now - failure.at.timestamp())} ago: {failure.error}"
        return check("backup", "critical", detail), disk_status, info, disk
    coming = _backup_coming(status, now)
    if success is None or age is None:
        if coming:
            return check("backup", "unknown", f"No backup yet; {coming}"), disk_status, info, disk
        return check("backup", "warning", "No successful backup yet"), disk_status, info, disk
    if age > max_age * 3600:
        detail = f"The last successful backup is {format_age(age)} old (the limit is {max_age} h)"
        if coming:
            return check("backup", "unknown", f"{detail}; {coming}"), disk_status, info, disk
        return check("backup", "warning", detail), disk_status, info, disk
    running = status.running_since is not None and now - status.running_since.timestamp() < BACKUP_RUNNING_GRACE_SECONDS
    if scheduler is None:
        detail = "Only manual backups so far: the backup service isn't running its schedule"
        return check("backup", "warning", detail), disk_status, info, disk
    if now - scheduler.seen_at.timestamp() > SCHEDULER_STALE_SECONDS and not running:
        detail = f"The backup service hasn't reported for {format_age(now - scheduler.seen_at.timestamp())}"
        return check("backup", "warning", detail), disk_status, info, disk
    detail = f"Last backup {format_age(age)} ago ({format_bytes(success.size_bytes)})"
    return check("backup", "ok", detail), disk_status, info, disk


def _next_backup(scheduler: backup_status.SchedulerInfo | None) -> datetime | None:
    if scheduler is None:
        return None
    times = [moment for moment in (scheduler.next_run_at, scheduler.catch_up_at) if moment is not None]
    return min(times, default=None)


# ---------------- Proxy (the monitor only) ----------------


async def check_proxy(url: str, timeout: float = CHECK_TIMEOUT_SECONDS) -> SystemCheck:
    async def get() -> int:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            return (await client.get(url)).status_code

    code, error = await _guard(get(), timeout + 1)
    if error:
        return check("proxy", "critical", f"The app doesn't answer through nginx: {error}")
    if code != 200:
        return check("proxy", "critical", f"The app answers through nginx with HTTP {code}")
    return check("proxy", "ok", "The app answers through nginx")


# ---------------- Everything ----------------


@dataclass
class Snapshot:
    checked_at: datetime
    checks: list[SystemCheck]
    worker: WorkerInfo
    queue: QueueInfo
    failed_documents_24h: int | None
    database_size_bytes: int | None
    disks: list[DiskInfo]
    backup: BackupInfo
    gmail_reconnect_needed: int | None


async def collect(proxy_url: str | None = None) -> Snapshot:
    """Every check (the proxy's only with a URL), run at once."""
    now = time.time()
    (postgres, size), redis, qdrant, (worker, worker_info), (queue, queue_info, failed), gmail = await asyncio.gather(
        check_postgres(), check_redis(), check_qdrant(), check_worker(now), check_queue(now), gmail_reconnect_needed()
    )
    backup, backup_disk, backup_info, backup_disk_info = check_backup(now)
    uploads_disk, uploads_disk_info = check_uploads_disk(now)
    checks = [postgres, redis, qdrant, worker, queue, backup, uploads_disk, backup_disk]
    if proxy_url:
        checks.append(await check_proxy(proxy_url))
    return Snapshot(
        checked_at=_utc(now),
        checks=checks,
        worker=worker_info,
        queue=queue_info,
        failed_documents_24h=failed,
        database_size_bytes=size,
        disks=[disk for disk in (uploads_disk_info, backup_disk_info) if disk is not None],
        backup=backup_info,
        gmail_reconnect_needed=gmail,
    )
