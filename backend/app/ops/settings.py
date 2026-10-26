"""Settings of the operations features (SPEC.md section 3, "Operations").

Two groups, read from the environment like app.config: `ops_settings` (the backend, the worker and the monitor) and
`AlertSettings` (only the monitor). Both are read by `load`: a value that doesn't validate falls back to its default
with a warning naming the setting, so a typo in a monitoring or alert setting never stops a service.
"""

import logging
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.ops")

AlertChannelName = Literal["webhook", "ntfy", "email"]
SettingsT = TypeVar("SettingsT", bound=BaseSettings)


class OpsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)
    # The settings that fall back together when the checks across them fail
    coupled: ClassVar[tuple[str, ...]] = ("worker_heartbeat_seconds", "worker_heartbeat_max_age_seconds")

    # Shown in Admin -> System and written to backup manifests; set by the deploy (e.g. `git describe`)
    app_version: str = Field(default="", max_length=64)
    worker_heartbeat_seconds: int = Field(default=15, ge=1, le=300)
    # A heartbeat older than this (or none) makes the worker "not ready"; also the heartbeat key's TTL. At least twice
    # the interval, so the key never expires between two beats.
    worker_heartbeat_max_age_seconds: int = Field(default=60, ge=2, le=3600)
    # The backup service's status file (the backupstatus volume, mounted read-only into backend and monitor)
    backup_status_file: str = "/backup-status/status.json"
    backup_max_age_hours: int = Field(default=26, ge=0, le=2160)  # 0: no backup checks
    # Pending documents older than this while none finished as long: the queue is stuck
    alert_queue_stuck_minutes: int = Field(default=30, ge=1, le=1440)
    alert_disk_min_free_percent: float = Field(default=10, ge=0, le=99)  # below half of it: critical

    @model_validator(mode="after")
    def _heartbeat_outlives_two_beats(self) -> "OpsSettings":
        if self.worker_heartbeat_max_age_seconds < 2 * self.worker_heartbeat_seconds:
            raise ValueError("WORKER_HEARTBEAT_MAX_AGE_SECONDS must be at least twice WORKER_HEARTBEAT_SECONDS")
        return self


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    monitor_interval_seconds: int = Field(default=60, ge=1, le=3600)
    # The app through nginx, from inside the Compose network; empty: no proxy check
    monitor_app_url: str = "http://frontend/api/health"
    alert_name: str = Field(default="RAG Assistant", min_length=1, max_length=80)
    alert_confirm_checks: int = Field(default=2, ge=1, le=60)  # consecutive results before a problem or recovery
    alert_repeat_hours: float = Field(default=6, ge=0, le=720)  # "still failing" reminders; 0: none
    alert_webhook_url: str = ""  # JSON {"text": ...}: Slack, Discord (/slack), Google Chat, Teams
    alert_ntfy_url: str = ""  # an ntfy topic URL, e.g. https://ntfy.sh/<topic>
    alert_ntfy_token: str = ""  # an ntfy access token for a protected topic
    alert_smtp_host: str = ""
    alert_smtp_port: int = Field(default=587, ge=1, le=65535)
    alert_smtp_username: str = ""
    alert_smtp_password: str = ""
    alert_smtp_from: str = ""
    alert_smtp_to: str = ""  # comma-separated
    alert_smtp_security: Literal["starttls", "ssl", "none"] = "starttls"


def load(model: type[SettingsT]) -> SettingsT:
    """The settings from the environment, each value that doesn't validate replaced by its default with a warning
    that names the setting (never its value). A failed check across settings resets the model's `coupled` ones."""
    defaults: dict[str, Any] = {}
    while True:
        try:
            return model(**defaults)
        except ValidationError as exc:
            names = {str(error["loc"][0]) for error in exc.errors() if error["loc"]}
            if not names:
                names = set(getattr(model, "coupled", ()))
            names -= defaults.keys()
            if not names:
                raise
            for name in sorted(names):
                defaults[name] = model.model_fields[name].get_default(call_default_factory=True)
                logger.warning("The setting %s is invalid; using its default (%s)", name.upper(), defaults[name])


ops_settings = load(OpsSettings)
