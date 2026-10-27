"""Shapes of the health and system routes (SPEC.md section 6, "Health" and "Admin"; section 10)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.ops.settings import AlertChannelName

CheckStatus = Literal["ok", "warning", "critical", "unknown"]
CheckId = Literal[
    "postgres",
    "redis",
    "qdrant",
    "worker",
    "queue",
    "backup",
    "disk_uploads",
    "disk_backups",
    "proxy",
    "monitor",
    "billing",
]
AlertKind = Literal["problem", "reminder", "recovery", "test"]


class Readiness(BaseModel):
    status: Literal["ready", "unready"]


class SystemCheck(BaseModel):
    id: CheckId
    label: str
    status: CheckStatus
    detail: str


class WorkerInfo(BaseModel):
    heartbeat_age_seconds: float | None  # None: no heartbeat
    running_jobs: int | None


class QueueInfo(BaseModel):
    queued: int | None  # ids waiting in the Redis queue
    pending: int | None  # documents waiting to be processed
    processing: int | None
    oldest_pending_age_seconds: float | None


class DiskInfo(BaseModel):
    id: Literal["uploads", "backups"]
    label: str
    free_bytes: int
    total_bytes: int
    checked_at: datetime


class BackupInfo(BaseModel):
    status_seen: bool  # the backup service's status file exists and could be read
    max_age_hours: int  # 0: backup checks are off
    last_success_at: datetime | None = None
    last_success_name: str | None = None
    last_success_size_bytes: int | None = None
    last_success_duration_seconds: float | None = None
    age_hours: float | None = None
    last_failure_at: datetime | None = None
    last_failure_error: str | None = None
    failing: bool = False  # the latest attempt failed
    running_since: datetime | None = None
    schedule: str | None = None
    next_run_at: datetime | None = None
    scheduler_seen_at: datetime | None = None
    backups_kept: int | None = None
    total_size_bytes: int | None = None


class AlertRecord(BaseModel):
    at: datetime
    kind: AlertKind
    title: str
    lines: list[str]
    delivered: list[AlertChannelName]
    failed: list[AlertChannelName]


class MonitorInfo(BaseModel):
    running: bool
    last_run_at: datetime | None
    interval_seconds: int | None
    channels: list[AlertChannelName]
    test_pending: bool


class SystemStatus(BaseModel):
    status: CheckStatus  # the worst check ("unknown" counts as a warning)
    checked_at: datetime
    app_version: str | None
    checks: list[SystemCheck]
    worker: WorkerInfo
    queue: QueueInfo
    failed_documents_24h: int | None
    database_size_bytes: int | None
    disks: list[DiskInfo]
    backup: BackupInfo
    gmail_reconnect_needed: int | None  # connections whose users must reconnect (expired in testing mode included)
    monitor: MonitorInfo
    alerts: list[AlertRecord]  # newest first


class AlertTestQueued(BaseModel):
    queued_at: datetime
