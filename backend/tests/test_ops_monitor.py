"""The monitor (app.ops.monitor): delivery and retries, test alerts, persistence across restarts, a loop nothing
stops; plus the checks' building blocks (app.ops.checks, app.ops.backup_status) and the worker heartbeat."""

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app import worker
from app.config import settings
from app.ops import backup_status, checks, heartbeat, monitor, monitor_state
from app.ops.alerts import Notification
from app.ops.channels import ChannelError
from app.ops.settings import AlertSettings, ops_settings
from app.redis_client import get_redis, set_redis
from app.schemas_system import BackupInfo, CheckStatus, QueueInfo, SystemCheck, WorkerInfo

T0 = 1_790_000_000.0


class FakeChannel:
    def __init__(self, name: str, failures: int = 0, error: Exception | None = None) -> None:
        self.name = name
        self.failures = failures
        self.error = error or ChannelError("HTTP 500")
        self.sent: list[Notification] = []
        self.attempts = 0

    async def send(self, notification: Notification) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.error
        self.sent.append(notification)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def snapshot(statuses: dict[str, CheckStatus]) -> checks.Snapshot:
    return checks.Snapshot(
        checked_at=checks._utc(T0),
        checks=[checks.check(check_id, status, f"{check_id} is {status}") for check_id, status in statuses.items()],  # type: ignore[arg-type]
        worker=WorkerInfo(heartbeat_age_seconds=1, running_jobs=0),
        queue=QueueInfo(queued=0, pending=0, processing=0, oldest_pending_age_seconds=None),
        failed_documents_24h=0,
        database_size_bytes=None,
        disks=[],
        backup=BackupInfo(status_seen=False, max_age_hours=26),
        gmail_reconnect_needed=0,
    )


@pytest.fixture
def results(monkeypatch: pytest.MonkeyPatch) -> dict[str, CheckStatus]:
    """What checks.collect returns: edit the dict between cycles."""
    current: dict[str, CheckStatus] = {"qdrant": "ok"}
    asked: list[str | None] = []

    async def collect(proxy_url: str | None = None) -> checks.Snapshot:
        asked.append(proxy_url)
        return snapshot(current)

    monkeypatch.setattr(checks, "collect", collect)
    return current


def make_monitor(*channels: FakeChannel, confirm: int = 1, clock: Clock | None = None) -> monitor.Monitor:
    built = monitor.build(
        AlertSettings(alert_confirm_checks=confirm, alert_repeat_hours=0, monitor_interval_seconds=60)
    )
    built.channels = list(channels)  # type: ignore[arg-type]
    built.clock = clock or Clock()
    return built


# ---------------- Delivery ----------------


async def test_a_state_change_goes_to_every_channel_and_is_published(results: dict[str, CheckStatus]) -> None:
    webhook, ntfy = FakeChannel("webhook"), FakeChannel("ntfy")
    watcher = make_monitor(webhook, ntfy)

    await watcher.cycle()
    assert webhook.sent == ntfy.sent == []

    results["qdrant"] = "critical"
    await watcher.cycle()
    await watcher.cycle()

    assert [item.kind for item in webhook.sent] == [item.kind for item in ntfy.sent] == ["problem"]
    published = await monitor_state.read_published()
    assert published is not None
    assert published.channels == ["webhook", "ntfy"] and published.interval_seconds == 60
    assert [item.id for item in published.checks] == ["qdrant"]
    (alert,) = published.alerts
    assert alert.kind == "problem" and alert.title == "[AskNuvexusDoc] Problem: Vector store (Qdrant)"
    assert (alert.delivered, alert.failed) == (["webhook", "ntfy"], [])
    assert published.states["qdrant"]["status"] == "critical"


async def test_a_failing_channel_is_retried_on_later_cycles(results: dict[str, CheckStatus]) -> None:
    webhook, ntfy = FakeChannel("webhook"), FakeChannel("ntfy", failures=2)
    watcher = make_monitor(webhook, ntfy)
    results["qdrant"] = "critical"

    await watcher.cycle()
    assert watcher.history[0].failed == ["ntfy"] and watcher.history[0].delivered == ["webhook"]
    await watcher.cycle()
    await watcher.cycle()

    assert len(webhook.sent) == 1 and len(ntfy.sent) == 1 and ntfy.attempts == 3
    assert watcher.history[0].delivered == ["webhook", "ntfy"] and watcher.history[0].failed == []
    assert watcher.outbox == []


async def test_an_undeliverable_alert_is_dropped_after_an_hour(
    results: dict[str, CheckStatus], caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    email = FakeChannel("email", failures=10**6, error=ChannelError("SMTP login refused"))
    watcher = make_monitor(email, clock=clock)
    results["qdrant"] = "critical"

    await watcher.cycle()
    clock.now += monitor.RETRY_SECONDS
    with caplog.at_level(logging.WARNING, "app.monitor"):
        await watcher.cycle()

    assert watcher.outbox == [] and watcher.history[0].failed == ["email"]
    assert "Gave up on an alert after 2 attempts; undelivered: email" in caplog.text
    assert "The email alert channel failed: SMTP login refused" in caplog.text


async def test_a_channel_that_raises_anything_does_not_stop_the_cycle(
    results: dict[str, CheckStatus], caplog: pytest.LogCaptureFixture
) -> None:
    broken = FakeChannel("webhook", failures=1, error=RuntimeError("boom at https://hooks.example/secret"))
    working = FakeChannel("ntfy")
    watcher = make_monitor(broken, working)
    results["qdrant"] = "critical"

    with caplog.at_level(logging.WARNING, "app.monitor"):
        await watcher.cycle()

    assert len(working.sent) == 1
    assert "The webhook alert channel failed: failed (RuntimeError)" in caplog.text
    assert "hooks.example" not in caplog.text


async def test_without_channels_alerts_are_logged_and_kept(
    results: dict[str, CheckStatus], caplog: pytest.LogCaptureFixture
) -> None:
    watcher = make_monitor()
    results["qdrant"] = "critical"

    with caplog.at_level(logging.WARNING, "app.monitor"):
        await watcher.cycle()

    assert "Alert: [AskNuvexusDoc] Problem: Vector store (Qdrant) | PROBLEM Vector store (Qdrant)" in caplog.text
    assert "No alert channel is configured" in caplog.text
    assert watcher.outbox == [] and len(watcher.history) == 1


async def test_a_test_alert_request_is_sent_once(results: dict[str, CheckStatus]) -> None:
    webhook = FakeChannel("webhook")
    watcher = make_monitor(webhook)
    assert await monitor_state.request_test_alert() is True

    await watcher.cycle()
    await watcher.cycle()

    assert [item.kind for item in webhook.sent] == ["test"]
    assert await monitor_state.pending_test_alert() is False


async def test_history_keeps_the_newest_alerts(results: dict[str, CheckStatus]) -> None:
    watcher = make_monitor(FakeChannel("webhook"))
    for index in range(monitor_state.ALERT_HISTORY + 5):
        results["qdrant"] = "critical" if index % 2 == 0 else "ok"
        await watcher.cycle()
    assert len(watcher.history) == monitor_state.ALERT_HISTORY
    assert watcher.history[0].kind == "problem"


# ---------------- Restarts and Redis outages ----------------


async def test_a_restarted_monitor_neither_repeats_a_problem_nor_misses_its_recovery(
    results: dict[str, CheckStatus],
) -> None:
    first_channel = FakeChannel("webhook")
    first = make_monitor(first_channel)
    results["qdrant"] = "critical"
    await first.cycle()
    assert len(first_channel.sent) == 1

    second_channel = FakeChannel("webhook")
    second = make_monitor(second_channel)
    await second.cycle()
    assert second_channel.sent == []
    results["qdrant"] = "ok"
    await second.cycle()

    assert [item.kind for item in second_channel.sent] == ["recovery"]
    assert [item.kind for item in second.history] == ["recovery", "problem"]


class BrokenRedis:
    def __getattr__(self, name: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise RedisConnectionError("down")

        return fail

    async def aclose(self) -> None:
        return None


async def test_the_previous_state_is_read_once_redis_answers(results: dict[str, CheckStatus]) -> None:
    server = FakeServer()
    set_redis(FakeRedis(server=server, decode_responses=True))
    first = make_monitor(FakeChannel("webhook"))
    results["qdrant"] = "critical"
    await first.cycle()

    set_redis(BrokenRedis())  # type: ignore[arg-type]
    clock = Clock()
    clock.now += 60
    second = make_monitor(FakeChannel("webhook"), clock=clock)
    await second.cycle()
    assert second.loaded is False  # and nothing was published over the first run's alerts

    set_redis(FakeRedis(server=server, decode_responses=True))
    await second.cycle()
    assert second.loaded is True
    assert [item.kind for item in second.history] == ["problem", "problem"]
    assert second.history[0].at > second.history[1].at  # newest first: its own alert, then the first run's
    published = await monitor_state.read_published()
    assert published is not None and len(published.alerts) == 2


async def test_a_state_read_late_does_not_replace_what_this_run_saw(results: dict[str, CheckStatus]) -> None:
    server = FakeServer()
    set_redis(FakeRedis(server=server, decode_responses=True))
    await make_monitor(FakeChannel("webhook")).cycle()  # publishes Qdrant as OK

    set_redis(BrokenRedis())  # type: ignore[arg-type]
    channel = FakeChannel("webhook")
    second = make_monitor(channel)
    results["qdrant"] = "critical"
    await second.cycle()
    set_redis(FakeRedis(server=server, decode_responses=True))
    await second.cycle()  # reads the old "OK" state now, and keeps its own "critical"
    results["qdrant"] = "ok"
    await second.cycle()

    assert [item.kind for item in channel.sent] == ["problem", "recovery"]


async def test_a_publish_that_fails_is_logged(
    results: dict[str, CheckStatus], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    watcher = make_monitor()
    await watcher.cycle()

    async def fail(status: Any) -> None:
        raise RedisConnectionError("down")

    monkeypatch.setattr(monitor_state, "publish", fail)
    with caplog.at_level(logging.WARNING, "app.monitor"):
        await watcher.cycle()
    assert "Can't publish the monitor's results: connection failed" in caplog.text


# ---------------- The loop ----------------


async def test_the_loop_survives_a_failing_cycle_marks_itself_alive_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(monitor, "ALIVE_FILE", tmp_path / "alive")
    watcher = make_monitor()
    watcher.alert_settings.monitor_interval_seconds = 0
    cycles = 0

    async def cycle() -> None:
        nonlocal cycles
        cycles += 1
        if cycles == 1:
            raise OSError("password=hunter2")
        if cycles == 3:
            watcher.stop()

    monkeypatch.setattr(watcher, "cycle", cycle)
    with caplog.at_level(logging.ERROR, "app.monitor"):
        await asyncio.wait_for(watcher.run(), 5)

    assert cycles == 3
    assert "The monitor's cycle failed: connection failed" in caplog.text and "hunter2" not in caplog.text
    assert monitor.alive(60) is True
    os.utime(tmp_path / "alive", (time.time() - 1000, time.time() - 1000))
    assert monitor.alive(60) is False
    (tmp_path / "alive").unlink()
    assert monitor.alive(60) is False


async def test_main_runs_the_monitor_and_closes_its_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    async def run(self: monitor.Monitor) -> None:
        ran.append("run")

    async def close(name: str) -> None:
        ran.append(name)

    monkeypatch.setattr(monitor.Monitor, "run", run)
    for name in ("close_redis", "close_qdrant", "close_db"):
        monkeypatch.setattr(monitor, name, lambda name=name: close(name))

    await monitor.main()

    assert ran == ["run", "close_redis", "close_qdrant", "close_db"]


def test_the_admin_link_uses_the_first_public_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_base_urls", " https://rag.example.com/ , http://localhost:8080")
    assert monitor.admin_link() == "https://rag.example.com/admin#system"
    monkeypatch.setattr(settings, "public_base_urls", " ")
    assert monitor.admin_link() is None


def test_build_reads_the_channels_from_the_settings() -> None:
    built = monitor.build(AlertSettings(alert_webhook_url="https://hooks.example/x", alert_name="Docs Bot"))
    assert [channel.name for channel in built.channels] == ["webhook"]
    assert built.engine.name == "Docs Bot"
    assert built.engine.scrub("see https://hooks.example/x") == "see [redacted]"


# ---------------- Checks ----------------


@pytest.mark.parametrize(
    ("error", "described"),
    [
        (TimeoutError(), "timed out"),
        (httpx.ConnectTimeout("t"), "timed out"),
        (
            httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=httpx.Response(503)),
            "HTTP 503",
        ),
        (ConnectionRefusedError("refused 10.0.0.1"), "connection refused"),
        (OSError("Name or service not known: db.internal"), "connection failed"),
        (RedisConnectionError("redis://:pw@redis"), "connection failed"),
        (ValueError("user=bob"), "failed (ValueError)"),
    ],
)
def test_errors_are_described_without_their_message(error: Exception, described: str) -> None:
    assert checks.describe_error(error) == described


def test_a_wrapped_error_is_described_by_its_cause() -> None:
    try:
        try:
            raise ConnectionRefusedError("refused")
        except ConnectionRefusedError as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as outer:
        assert checks.describe_error(outer) == "connection refused"


def test_formatting() -> None:
    assert [checks.format_bytes(size) for size in (0, 1023, 1536, 10 * 2**20, 3.5 * 2**30, 2**50)] == [
        "0 B",
        "1023 B",
        "1.5 KB",
        "10 MB",
        "3.5 GB",
        "1024 TB",
    ]
    assert [checks.format_age(seconds) for seconds in (-5, 45, 89, 600, 7200, 3 * 86400)] == [
        "0 s",
        "45 s",
        "89 s",
        "10 min",
        "2.0 h",
        "3.0 days",
    ]


@pytest.mark.parametrize(
    ("free", "total", "status"), [(50, 100, "ok"), (9, 100, "warning"), (4, 100, "critical"), (0, 0, "unknown")]
)
def test_disk_thresholds(free: int, total: int, status: str) -> None:
    assert checks.disk_check("disk_uploads", free, total).status == status


def test_the_overall_status_is_the_worst() -> None:
    def items(*statuses: CheckStatus) -> list[SystemCheck]:
        return [checks.check("redis", status, "") for status in statuses]

    assert checks.overall([]) == "ok"
    assert checks.overall(items("ok", "unknown")) == "warning"
    assert checks.overall(items("warning", "critical", "ok")) == "critical"


def test_a_missing_upload_folder_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "missing"))
    result, info = checks.check_uploads_disk(T0)
    assert (result.status, result.detail, info) == ("unknown", "The upload folder doesn't exist", None)
    monkeypatch.setattr(checks.os, "statvfs", lambda path: (_ for _ in ()).throw(PermissionError(path)))
    result, _ = checks.check_uploads_disk(T0)
    assert result.detail == "The upload folder can't be read"


def iso(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


@pytest.fixture
def write_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    path = tmp_path / "status.json"
    monkeypatch.setattr(ops_settings, "backup_status_file", str(path))
    return lambda content: path.write_text(json.dumps(content))


def backup_detail() -> tuple[str, str]:
    result, _, _, _ = checks.check_backup(time.time())
    return result.status, result.detail


def test_backup_check_cases(write_status: Callable[[dict[str, Any]], None], monkeypatch: pytest.MonkeyPatch) -> None:
    success = {"name": "20261025T023000Z", "finished_at": iso(3600), "size_bytes": 1000, "duration_seconds": 3}
    base = {"updated_at": iso(10), "last_success": success, "disk": {"free_bytes": 1, "total_bytes": 100}}

    write_status(base)
    assert backup_detail() == ("warning", "Only manual backups so far: the backup service isn't running its schedule")

    write_status(base | {"scheduler": {"seen_at": iso(3 * 3600), "schedule": "02:30"}})
    assert backup_detail() == ("warning", "The backup service hasn't reported for 3.0 h")

    write_status(base | {"scheduler": {"seen_at": iso(3 * 3600), "schedule": "02:30"}, "running_since": iso(3600)})
    assert backup_detail() == ("ok", "Last backup 60 min ago (1000 B)")

    write_status({"updated_at": iso(10), "last_failure": {"at": iso(600), "error": "uploads archive failed"}})
    assert backup_detail() == ("critical", "The last backup failed 10 min ago: uploads archive failed")

    write_status({"updated_at": iso(10)})
    assert backup_detail() == ("warning", "No successful backup yet")

    monkeypatch.setattr(ops_settings, "backup_max_age_hours", 0)
    assert backup_detail() == ("ok", "Backup checks are off (BACKUP_MAX_AGE_HOURS=0)")
    write_status(base)
    _, disk, _, info = checks.check_backup(time.time())
    assert disk.status == "critical" and info is not None and info.free_bytes == 1


def test_a_missing_or_old_backup_is_no_problem_while_a_new_one_is_coming(
    write_status: Callable[[dict[str, Any]], None],
) -> None:
    fresh = {"seen_at": iso(30), "schedule": "02:30", "next_run_at": iso(-20 * 3600)}
    soon = time.gmtime(time.time() + 90)
    catch_up, due = time.strftime("%Y-%m-%dT%H:%M:%SZ", soon), time.strftime("%H:%M UTC", soon)

    # The backup service just started on a new install: its start-up backup is due in 90 s.
    write_status({"updated_at": iso(30), "scheduler": fresh | {"catch_up_at": catch_up}})
    assert backup_detail() == ("unknown", f"No backup yet; one is due at {due}")
    write_status({"updated_at": iso(30), "scheduler": fresh, "running_since": iso(20)})
    assert backup_detail() == ("unknown", "No backup yet; one is running")

    # The stack was down at the scheduled time: the last backup is 3 days old, and a catch-up backup is due.
    old = {"name": "20261022T023000Z", "finished_at": iso(3 * 86400), "size_bytes": 10, "duration_seconds": 1}
    write_status({"updated_at": iso(30), "last_success": old, "scheduler": fresh | {"catch_up_at": catch_up}})
    assert backup_detail() == (
        "unknown",
        f"The last successful backup is 3.0 days old (the limit is 26 h); one is due at {due}",
    )
    _, _, info, _ = checks.check_backup(time.time())
    assert info.next_run_at is not None and abs(info.next_run_at.timestamp() - (time.time() + 90)) < 5

    # Overdue: the catch-up backup didn't start within the grace period, or the backup service went silent.
    late = fresh | {"catch_up_at": iso(checks.BACKUP_START_GRACE_SECONDS + 60)}
    write_status({"updated_at": iso(30), "last_success": old, "scheduler": late})
    assert backup_detail()[0] == "warning"
    write_status({"updated_at": iso(30), "scheduler": late})
    assert backup_detail() == ("warning", "No successful backup yet")
    silent = {"seen_at": iso(3 * 3600), "schedule": "02:30", "catch_up_at": iso(-90)}
    write_status({"updated_at": iso(30), "scheduler": silent, "running_since": iso(20)})
    assert backup_detail() == ("warning", "No successful backup yet")


def test_a_backup_service_that_never_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_settings, "backup_status_file", str(tmp_path / "none.json"))
    monkeypatch.setattr(checks, "_STARTED", time.time() - 30)
    assert backup_detail() == ("unknown", "Waiting for the backup service's first report")
    monkeypatch.setattr(checks, "_STARTED", time.time() - checks.BACKUP_START_GRACE_SECONDS - 1)
    assert backup_detail()[0] == "warning"


def test_backup_check_with_checks_off_and_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_settings, "backup_status_file", str(tmp_path / "none.json"))
    monkeypatch.setattr(ops_settings, "backup_max_age_hours", 0)
    result, disk, info, _ = checks.check_backup(time.time())
    assert (result.status, disk.status, info.status_seen) == ("ok", "unknown", False)


def test_the_status_file_parser(tmp_path: Path) -> None:
    assert backup_status.read(tmp_path / "missing.json") is None
    with pytest.raises(backup_status.StatusUnreadable):
        backup_status.read(tmp_path)  # a directory
    big = tmp_path / "big.json"
    big.write_text(" " * (70 * 1024))
    with pytest.raises(backup_status.StatusUnreadable):
        backup_status.read(big)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"updated_at": "yesterday"}))
    with pytest.raises(backup_status.StatusUnreadable):
        backup_status.read(wrong)
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"updated_at": iso(0), "extra": "ignored", "last_failure": {"at": iso(0), "error": "x"}})
    )
    parsed = backup_status.read(good)
    assert parsed is not None and parsed.failing is True


async def test_the_proxy_check(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter([httpx.Response(200), httpx.Response(502)])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "down":
            raise httpx.ConnectError("no route to http://down")
        return next(replies)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))

    assert (await checks.check_proxy("http://frontend/api/health")).status == "ok"
    bad = await checks.check_proxy("http://frontend/api/health")
    assert (bad.status, bad.detail) == ("critical", "The app answers through nginx with HTTP 502")
    down = await checks.check_proxy("http://down/api/health")
    assert down.detail == "The app doesn't answer through nginx: connection failed"


async def test_collect_runs_the_proxy_check_only_with_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def proxy(url: str, timeout: float = 5) -> SystemCheck:
        return checks.check("proxy", "ok", url)

    monkeypatch.setattr(checks, "check_proxy", proxy)

    without = await checks.collect()
    with_url = await checks.collect("http://frontend/api/health")

    assert "proxy" not in [item.id for item in without.checks]
    assert with_url.checks[-1].detail == "http://frontend/api/health"


# ---------------- Heartbeat ----------------


def test_heartbeat_values() -> None:
    beat = heartbeat.decode(heartbeat.encode(3, now=T0))
    assert beat == heartbeat.Heartbeat(T0, 3)
    assert beat.age(T0 - 5) == 0 and beat.age(T0 + 5) == 5
    assert heartbeat.decode(None) is None
    assert heartbeat.decode("{bad") is None
    assert heartbeat.decode(json.dumps({"at": "x", "jobs": 1})) is None
    assert heartbeat.decode(json.dumps({"at": T0, "jobs": -2})) == heartbeat.Heartbeat(T0, 0)
    assert heartbeat.is_fresh(None, T0) is False


async def test_heartbeat_key_has_a_ttl_and_can_be_cleared() -> None:
    await heartbeat.beat(1)
    assert 0 < await get_redis().ttl(heartbeat.WORKER_HEARTBEAT_KEY) <= ops_settings.worker_heartbeat_max_age_seconds
    beat = await heartbeat.read()
    assert beat is not None and heartbeat.is_fresh(beat, time.time())
    await heartbeat.clear()
    assert await heartbeat.read() is None


class SyncRedis:
    value: str | None = None
    error: bool = False

    def __enter__(self) -> "SyncRedis":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, key: str) -> str | None:
        if SyncRedis.error:
            raise RedisError("down")
        return SyncRedis.value


def test_the_healthcheck_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redis.Redis, "from_url", lambda url, **kwargs: SyncRedis())
    SyncRedis.value, SyncRedis.error = heartbeat.encode(0), False
    assert heartbeat.main() == 0
    SyncRedis.value = heartbeat.encode(0, now=time.time() - 3600)
    assert heartbeat.main() == 1
    SyncRedis.error = True
    assert heartbeat.main() == 1


@pytest.fixture
def fast_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_POLL_SECONDS", 0.05)
    monkeypatch.setattr(ops_settings, "worker_heartbeat_seconds", 0.02)


async def wait_for(predicate: Callable[[], Any], seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not await predicate():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


async def test_the_worker_beats_while_it_runs_and_clears_the_beat_when_it_stops(app: Any, fast_worker: None) -> None:
    running = worker.Worker(concurrency=2)
    task = asyncio.create_task(running.run())

    await wait_for(lambda: heartbeat.read())
    beat = await heartbeat.read()
    assert beat is not None and beat.jobs == 0

    running.stop()
    await asyncio.wait_for(task, 5)
    assert await heartbeat.read() is None


async def test_a_failing_heartbeat_is_logged_once_per_outage(
    app: Any, fast_worker: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    attempts = 0

    async def beat(jobs: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts in (1, 2, 3, 5):
            raise RedisConnectionError("down")

    monkeypatch.setattr(heartbeat, "beat", beat)
    running = worker.Worker(concurrency=1)
    with caplog.at_level(logging.WARNING, "app.worker"):
        task = asyncio.create_task(running.run())

        async def enough() -> bool:
            return attempts >= 6

        await wait_for(enough)
        running.stop()
        await asyncio.wait_for(task, 5)

    warnings = [record.getMessage() for record in caplog.records if "heartbeat" in record.getMessage()]
    assert warnings == ["Could not refresh the worker heartbeat (ConnectionError)"] * 2
