"""The operations settings are read leniently: a bad value falls back to its default and never stops a service."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ops.settings import AlertSettings, OpsSettings, load

BACKEND = Path(__file__).resolve().parents[1]


def test_a_bad_value_falls_back_to_its_default_and_is_named(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALERT_DISK_MIN_FREE_PERCENT", "10%")
    monkeypatch.setenv("ALERT_QUEUE_STUCK_MINUTES", "45")
    monkeypatch.setenv("BACKUP_MAX_AGE_HOURS", "-3")
    with caplog.at_level("WARNING", logger="app.ops"):
        loaded = load(OpsSettings)
    assert (loaded.alert_disk_min_free_percent, loaded.backup_max_age_hours) == (10, 26)
    assert loaded.alert_queue_stuck_minutes == 45  # the valid ones are kept
    assert caplog.messages == [
        "The setting ALERT_DISK_MIN_FREE_PERCENT is invalid; using its default (10)",
        "The setting BACKUP_MAX_AGE_HOURS is invalid; using its default (26)",
    ]
    assert "10%" not in caplog.text and "-3" not in caplog.text


def test_the_heartbeat_must_outlive_two_beats(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(ValidationError, match="at least twice"):
        OpsSettings(worker_heartbeat_seconds=60, worker_heartbeat_max_age_seconds=119)
    assert OpsSettings(worker_heartbeat_seconds=60, worker_heartbeat_max_age_seconds=120).worker_heartbeat_seconds == 60

    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "60")
    monkeypatch.setenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "100")
    with caplog.at_level("WARNING", logger="app.ops"):
        loaded = load(OpsSettings)
    assert (loaded.worker_heartbeat_seconds, loaded.worker_heartbeat_max_age_seconds) == (15, 60)
    assert {message.split()[2] for message in caplog.messages} == {
        "WORKER_HEARTBEAT_SECONDS",
        "WORKER_HEARTBEAT_MAX_AGE_SECONDS",
    }


def test_alert_settings_fall_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_REPEAT_HOURS", "six")
    monkeypatch.setenv("ALERT_SMTP_PORT", "0")
    monkeypatch.setenv("ALERT_NAME", "Docs")
    loaded = load(AlertSettings)
    assert (loaded.alert_repeat_hours, loaded.alert_smtp_port, loaded.alert_name) == (6, 587, "Docs")


def test_a_typo_in_a_monitoring_setting_never_stops_the_backend_or_the_worker() -> None:
    environment = os.environ | {"ALERT_DISK_MIN_FREE_PERCENT": "10%", "WORKER_HEARTBEAT_MAX_AGE_SECONDS": "1"}
    result = subprocess.run(
        [sys.executable, "-c", "import app.main, app.worker, app.ops.heartbeat"],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "ALERT_DISK_MIN_FREE_PERCENT is invalid" in result.stderr
    assert "WORKER_HEARTBEAT_MAX_AGE_SECONDS is invalid" in result.stderr
    assert "10%" not in result.stderr
