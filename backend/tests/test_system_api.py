"""GET /api/health/ready (public, status only), GET /api/admin/system and POST /api/admin/system/test-alert (SPEC.md
section 10). The admin routes' 401 and 403 are in test_admin.py (ADMIN_ROUTES)."""

import json
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import ON_POSTGRES, ApiClient
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.db import SessionLocal
from app.models import Document, PluginConnection, utcnow
from app.ops import checks, heartbeat, monitor_state
from app.ops.settings import ops_settings
from app.plugins import registry
from app.plugins.registry import builtin_plugins
from app.redis_client import INGEST_QUEUE, get_redis, set_redis
from app.routers import system

NOW = time.time()


@pytest.fixture(autouse=True)
def no_ready_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system, "READY_CACHE_SECONDS", 0.0)


@pytest.fixture
def status_file(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any] | str], Path]:
    """Writes the backup service's status file (a dict as JSON, or raw text) where the checks read it."""
    path = isolated_state / "backup-status.json"
    monkeypatch.setattr(ops_settings, "backup_status_file", str(path))

    def write(content: dict[str, Any] | str) -> Path:
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    return write


def iso(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


def healthy_backup() -> dict[str, Any]:
    return {
        "updated_at": iso(60),
        "scheduler": {"seen_at": iso(60), "schedule": "02:30", "next_run_at": iso(-3600)},
        "running_since": None,
        "last_success": {
            "name": "20261025T023000Z",
            "finished_at": iso(3600),
            "size_bytes": 5_000_000,
            "duration_seconds": 12,
        },
        "last_failure": None,
        "backups": {"count": 3, "total_bytes": 15_000_000},
        "disk": {"free_bytes": 40 * 2**30, "total_bytes": 100 * 2**30},
    }


async def publish_monitor(channels: list[str], updated_ago: float = 5, **extra: Any) -> None:
    status = monitor_state.PublishedStatus(
        updated_at=time.time() - updated_ago,
        interval_seconds=60,
        channels=channels,  # type: ignore[arg-type]
        **extra,
    )
    await monitor_state.publish(status)


class BrokenRedis:
    """Every command fails as if Redis were down."""

    def __getattr__(self, name: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise RedisConnectionError("Error 111 connecting to redis:6379. secret-password-123")

        return fail

    async def aclose(self) -> None:
        return None


def by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in body["checks"]}


# ---------------- Readiness ----------------


async def test_ready_when_every_service_answers_and_the_worker_beats(client: ApiClient) -> None:
    await heartbeat.beat(0)

    response = await client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["cache-control"] == "no-store"


async def test_unready_without_a_worker_heartbeat_and_the_answer_is_only_the_status(client: ApiClient) -> None:
    response = await client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unready"}


async def test_unready_with_a_stale_heartbeat(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    await get_redis().set(heartbeat.WORKER_HEARTBEAT_KEY, heartbeat.encode(1, now=time.time() - 600))

    assert (await client.get("/api/health/ready")).status_code == 503

    await heartbeat.beat(1)
    assert (await client.get("/api/health/ready")).status_code == 200


async def test_unready_when_redis_is_down(client: ApiClient) -> None:
    await heartbeat.beat(0)
    set_redis(BrokenRedis())  # type: ignore[arg-type]

    response = await client.get("/api/health/ready")

    assert (response.status_code, response.json()) == (503, {"status": "unready"})


async def test_unready_when_qdrant_fails(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    await heartbeat.beat(0)

    class Down:
        async def get_collections(self) -> None:
            raise ConnectionRefusedError("refused")

    monkeypatch.setattr(checks, "get_qdrant", lambda: Down())

    assert (await client.get("/api/health/ready")).status_code == 503


async def test_readiness_answers_share_one_round_of_checks(client: ApiClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def ready() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(system, "READY_CACHE_SECONDS", 60.0)
    monkeypatch.setattr(system, "_ready", None)
    monkeypatch.setattr(checks, "ready", ready)

    for _ in range(3):
        assert (await client.get("/api/health/ready")).status_code == 200
    assert calls == 1


# ---------------- Admin -> System ----------------


async def test_system_status_reports_every_check(
    admin_client: ApiClient, status_file: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops_settings, "app_version", "v1.2.3-4-gabc")
    Path(settings.upload_dir).mkdir(parents=True)
    await heartbeat.beat(2)
    status_file(healthy_backup())
    proxy = checks.check("proxy", "ok", "The app answers through nginx")
    alert = {
        "at": iso(120),
        "kind": "problem",
        "title": "[RAG Assistant] Problem: Redis",
        "lines": ["PROBLEM Redis (critical): Unreachable: connection failed"],
        "delivered": ["webhook"],
        "failed": [],
    }
    await publish_monitor(["webhook"], checks=[proxy], alerts=[alert])

    response = await admin_client.get("/api/admin/system")

    assert response.status_code == 200, response.text
    body = response.json()
    results = by_id(body)
    assert list(results) == [
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
    ]
    assert {key: item["status"] for key, item in results.items() if item["status"] != "ok"} == {}
    assert body["status"] == "ok"
    assert body["app_version"] == "v1.2.3-4-gabc"
    assert results["worker"]["detail"].endswith("2 jobs running")
    assert results["queue"]["detail"] == "Idle"
    assert results["backup"]["detail"] == "Last backup 60 min ago (4.8 MB)"
    assert results["disk_backups"]["detail"] == "40 GB free of 100 GB (40 %)"
    assert results["monitor"]["detail"].endswith("alerts go to webhook")
    assert results["proxy"] == proxy.model_dump()
    assert body["worker"]["running_jobs"] == 2 and body["worker"]["heartbeat_age_seconds"] < 5
    assert body["queue"] == {"queued": 0, "pending": 0, "processing": 0, "oldest_pending_age_seconds": None}
    assert body["failed_documents_24h"] == 0
    if ON_POSTGRES:
        assert body["database_size_bytes"] > 0
    else:
        assert body["database_size_bytes"] is None  # SQLite has no database size
    assert [disk["id"] for disk in body["disks"]] == ["uploads", "backups"]
    assert body["backup"]["last_success_name"] == "20261025T023000Z"
    assert body["backup"]["backups_kept"] == 3 and body["backup"]["schedule"] == "02:30"
    assert body["backup"]["failing"] is False and 0.9 < body["backup"]["age_hours"] < 1.1
    assert body["gmail_reconnect_needed"] == 0
    assert body["monitor"]["running"] is True and body["monitor"]["channels"] == ["webhook"]
    assert body["monitor"]["test_pending"] is False
    assert [item["title"] for item in body["alerts"]] == ["[RAG Assistant] Problem: Redis"]


async def test_queue_failed_documents_and_gmail_reconnects_are_counted(
    admin_client: ApiClient, create_user: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert admin_client.user_id is not None
    users = [(await create_user(login=False)).user_id for _ in range(3)]
    now = utcnow()
    async with SessionLocal() as db:
        for index, (status, age) in enumerate(
            [("pending", 50), ("pending", 5), ("processing", 1), ("failed", 60), ("failed", 60 * 30), ("ready", 45)]
        ):
            db.add(
                Document(
                    owner_id=admin_client.user_id,
                    scope="private",
                    filename=f"secret-plans-{index}.txt",
                    stored_name=f"{index:032x}.txt",
                    sha256="a" * 64,
                    status=status,
                    created_at=now - timedelta(minutes=age),
                    updated_at=now - timedelta(minutes=age),
                )
            )
        for index, (status, connected_days) in enumerate([("connected", 1), ("reconnect_needed", 1), ("connected", 9)]):
            db.add(
                PluginConnection(
                    user_id=users[index],
                    plugin_id="gmail",
                    account_subject=f"sub-{index}",
                    account_email=f"person{index}@example.com",
                    refresh_token_enc="x",
                    key_id="k",
                    status=status,
                    connected_at=now - timedelta(days=connected_days),
                )
            )
        await db.commit()
    await get_redis().rpush(INGEST_QUEUE, "1", "2")
    monkeypatch.setattr(settings, "google_oauth_testing_mode", True)

    with registry.use_plugins(builtin_plugins()):
        body = (await admin_client.get("/api/admin/system")).json()

    assert body["queue"]["queued"] == 2
    assert body["queue"]["pending"] == 2 and body["queue"]["processing"] == 1
    assert 49 * 60 < body["queue"]["oldest_pending_age_seconds"] < 51 * 60
    assert body["failed_documents_24h"] == 1
    # The reconnect_needed row, and the 9-day-old one: Google ends connections after 7 days in testing mode.
    assert body["gmail_reconnect_needed"] == 2
    queue = by_id(body)["queue"]
    # The oldest waited 50 min (over the 30-minute limit) and a document finished 45 minutes ago: stuck.
    assert queue["status"] == "warning"
    assert queue["detail"] == "Stuck: 2 waiting, 1 processing; the oldest has waited 50 min and none finished since"
    assert "secret-plans" not in json.dumps(body) and "example.com" not in json.dumps(body)

    monkeypatch.setattr(ops_settings, "alert_queue_stuck_minutes", 60)
    queue = by_id((await admin_client.get("/api/admin/system")).json())["queue"]
    assert queue["status"] == "ok"
    assert queue["detail"] == "2 waiting, 1 processing; the oldest waiting for 50 min"


async def test_without_the_monitor_alerts_are_off_and_the_proxy_is_unknown(admin_client: ApiClient) -> None:
    await heartbeat.beat(0)

    body = (await admin_client.get("/api/admin/system")).json()

    results = by_id(body)
    assert results["monitor"] == {
        "id": "monitor",
        "label": "Monitor and alerts",
        "status": "warning",
        "detail": "The monitor service hasn't reported: no alerts are sent",
    }
    assert results["proxy"]["status"] == "unknown"
    assert body["monitor"] == {
        "running": False,
        "last_run_at": None,
        "interval_seconds": None,
        "channels": [],
        "test_pending": False,
    }
    assert body["alerts"] == []
    assert body["status"] == "warning"


async def test_a_stale_monitor_and_one_without_channels_are_warnings(admin_client: ApiClient) -> None:
    await publish_monitor(["ntfy"], updated_ago=3600)
    body = (await admin_client.get("/api/admin/system")).json()
    assert by_id(body)["monitor"]["detail"] == "Last reported 60 min ago: no alerts are sent while it is down"
    assert body["monitor"]["running"] is False and body["monitor"]["channels"] == ["ntfy"]

    await publish_monitor([])
    monitor = by_id((await admin_client.get("/api/admin/system")).json())["monitor"]
    assert monitor["status"] == "warning"
    assert monitor["detail"].endswith("No alert channel is configured: alerts are only logged and shown here")

    await publish_monitor(["email"])
    proxy = by_id((await admin_client.get("/api/admin/system")).json())["proxy"]
    assert proxy["detail"] == "Not checked (MONITOR_APP_URL is empty)"


async def test_backup_problems_are_reported(
    admin_client: ApiClient, status_file: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def backup() -> dict[str, Any]:
        return by_id((await admin_client.get("/api/admin/system")).json())["backup"]

    def status_and_detail(item: dict[str, Any]) -> tuple[str, str]:
        return item["status"], item["detail"]

    monkeypatch.setattr(checks, "_STARTED", time.time() - 60)  # this process started a minute ago
    assert status_and_detail(await backup()) == ("unknown", "Waiting for the backup service's first report")
    monkeypatch.setattr(checks, "_STARTED", time.time() - 3600)
    assert status_and_detail(await backup()) == (
        "warning",
        "The backup service hasn't reported: it writes its status as soon as it starts",
    )

    failed = healthy_backup() | {"last_failure": {"at": iso(600), "error": "pg_dump failed"}}
    status_file(failed)
    assert await backup() == {
        "id": "backup",
        "label": "Backups",
        "status": "critical",
        "detail": "The last backup failed 10 min ago: pg_dump failed",
    }

    old = healthy_backup()
    old["last_success"]["finished_at"] = iso(30 * 3600)
    status_file(old)
    assert (await backup())["detail"] == "The last successful backup is 30.0 h old (the limit is 26 h)"

    status_file("{not json")
    assert (await backup())["detail"] == "The backup status file can't be read"


async def test_redis_down_is_reported_without_failing_the_page(admin_client: ApiClient) -> None:
    set_redis(BrokenRedis())  # type: ignore[arg-type]

    response = await admin_client.get("/api/admin/system")

    assert response.status_code == 200
    body = response.json()
    results = by_id(body)
    assert results["redis"]["status"] == "critical"
    assert results["redis"]["detail"] == "Unreachable: connection failed"
    assert results["worker"]["status"] == "unknown"
    assert results["monitor"]["status"] == "unknown"
    assert body["status"] == "critical"
    assert body["queue"]["queued"] is None
    assert "secret-password-123" not in response.text and "6379" not in response.text


async def test_a_database_failure_names_no_detail_of_the_error(
    admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_session() -> Any:
        raise OSError("could not connect to server: user=rag password=hunter2-secret")

    monkeypatch.setattr(checks, "SessionLocal", broken_session)

    body = (await admin_client.get("/api/admin/system")).json()

    results = by_id(body)
    assert results["postgres"] == {
        "id": "postgres",
        "label": "Database (Postgres)",
        "status": "critical",
        "detail": "Unreachable: connection failed",
    }
    assert results["queue"]["status"] == "unknown"
    assert body["gmail_reconnect_needed"] is None and body["failed_documents_24h"] is None
    assert "hunter2" not in json.dumps(body)


# ---------------- Test alert ----------------


async def test_a_test_alert_needs_a_running_monitor_with_a_channel(admin_client: ApiClient) -> None:
    response = await admin_client.post("/api/admin/system/test-alert")
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "The monitor service isn't running, so no alert can be sent.",
    )

    await publish_monitor([])
    response = await admin_client.post("/api/admin/system/test-alert")
    assert response.status_code == 409
    assert response.json()["detail"].startswith("No alert channel is configured.")

    await publish_monitor(["webhook", "email"])
    response = await admin_client.post("/api/admin/system/test-alert")
    assert response.status_code == 202
    assert response.json()["queued_at"].startswith(time.strftime("%Y-%m-%d", time.gmtime()))
    assert (await admin_client.get("/api/admin/system")).json()["monitor"]["test_pending"] is True

    again = await admin_client.post("/api/admin/system/test-alert")
    assert (again.status_code, again.json()["detail"]) == (409, "A test alert is already waiting for the monitor.")

    assert await monitor_state.take_test_alert() is True
    assert await monitor_state.take_test_alert() is False


async def test_published_status_that_cannot_be_parsed_counts_as_none() -> None:
    await get_redis().set(monitor_state.MONITOR_STATUS_KEY, "{broken")
    assert await monitor_state.read_published() is None
    set_redis(FakeRedis(server=FakeServer(), decode_responses=True))
    assert await monitor_state.read_published() is None
