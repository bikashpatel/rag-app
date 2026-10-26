"""The backup service's status file (scripts/backup/lib.sh, `write_status`), read by the backend and the monitor.

Times are UTC ISO 8601 strings; sizes are bytes. The file holds no personal data: backup names are timestamps.
"""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_MAX_BYTES = 64 * 1024


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SchedulerInfo(_Lenient):
    seen_at: datetime
    schedule: str = Field(max_length=200)
    next_run_at: datetime | None = None
    catch_up_at: datetime | None = None  # a start-up backup is due then: none began since the last scheduled time


class LastSuccess(_Lenient):
    name: str = Field(max_length=100)
    finished_at: datetime
    size_bytes: int = Field(ge=0)
    duration_seconds: float = Field(default=0, ge=0)


class LastFailure(_Lenient):
    at: datetime
    error: str = Field(max_length=300)


class BackupCounts(_Lenient):
    count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class DiskSpace(_Lenient):
    free_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class BackupStatusFile(_Lenient):
    updated_at: datetime
    scheduler: SchedulerInfo | None = None
    running_since: datetime | None = None
    last_success: LastSuccess | None = None
    last_failure: LastFailure | None = None
    backups: BackupCounts | None = None
    disk: DiskSpace | None = None

    @property
    def failing(self) -> bool:
        """The latest attempt failed: a failure after the last success (or with no success at all)."""
        if self.last_failure is None:
            return False
        return self.last_success is None or self.last_failure.at > self.last_success.finished_at


class StatusUnreadable(Exception):
    pass


def read(path: str | Path) -> BackupStatusFile | None:
    """The status file's content; None when it doesn't exist; StatusUnreadable when it can't be parsed."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StatusUnreadable(type(exc).__name__) from None
    if len(raw) > _MAX_BYTES:
        raise StatusUnreadable("too large")
    try:
        return BackupStatusFile.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise StatusUnreadable(type(exc).__name__) from None
