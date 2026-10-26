"""The alert state machine (app.ops.alerts): confirmation, problems, reminders, recoveries, one notification per
evaluation, persistence, and messages free of secrets."""

from typing import Any

import pytest

from app.ops import checks
from app.ops.alerts import AlertEngine, CheckState, Scrubber
from app.schemas_system import CheckId, CheckStatus, SystemCheck

T0 = 1_790_000_000.0
MINUTE = 60.0


def engine(
    confirm: int = 2, repeat_hours: float = 6, link: str | None = "http://localhost:8080/admin#system"
) -> AlertEngine:
    return AlertEngine(
        name="RAG Assistant", confirm=confirm, repeat_seconds=repeat_hours * 3600, link=link, scrub=Scrubber([])
    )


def result(check_id: CheckId, status: CheckStatus, detail: str = "detail") -> SystemCheck:
    return checks.check(check_id, status, detail)


def run(alerts: AlertEngine, statuses: list[dict[CheckId, CheckStatus]], start: float = T0) -> list[Any]:
    """One evaluation per minute; the notifications (None when nothing changed)."""
    return [
        alerts.evaluate([result(check_id, status) for check_id, status in step.items()], start + index * MINUTE)
        for index, step in enumerate(statuses)
    ]


def test_a_problem_is_sent_once_confirmed_and_then_not_repeated() -> None:
    alerts = engine()
    sent = run(alerts, [{"qdrant": "ok"}, {"qdrant": "critical"}, {"qdrant": "critical"}, {"qdrant": "critical"}])

    assert sent[:2] == [None, None]
    notification = sent[2]
    assert notification.kind == "problem"
    assert notification.title == "[RAG Assistant] Problem: Vector store (Qdrant)"
    assert notification.lines == [
        "PROBLEM Vector store (Qdrant) (critical): detail",
        "At 2026-09-21 14:15:20 UTC.",
        "Details: http://localhost:8080/admin#system",
    ]
    assert notification.check_ids == ["qdrant"]
    assert sent[3] is None


def test_a_single_blip_sends_nothing() -> None:
    alerts = engine(confirm=2)
    assert run(alerts, [{"redis": "critical"}, {"redis": "ok"}, {"redis": "critical"}, {"redis": "ok"}]) == [None] * 4


def test_with_confirm_one_the_first_failure_alerts() -> None:
    (notification,) = run(engine(confirm=1), [{"redis": "critical"}])
    assert notification.kind == "problem"


def test_a_recovery_says_how_long_it_failed() -> None:
    alerts = engine()
    steps: list[dict[CheckId, CheckStatus]] = [{"worker": "critical"}] * 12 + [{"worker": "ok"}] * 3
    sent = [item for item in run(alerts, steps) if item is not None]

    assert [item.kind for item in sent] == ["problem", "recovery"]
    recovery = sent[1]
    assert recovery.title == "[RAG Assistant] Resolved: Ingestion worker"
    # Failing from the first critical result (minute 0) to the first OK one (minute 12).
    assert recovery.lines[0] == "RESOLVED Ingestion worker: OK again after 12 min (detail)"


def test_still_failing_reminders_every_repeat_interval_and_none_with_zero() -> None:
    alerts = engine(confirm=1, repeat_hours=1)
    sent = run(alerts, [{"backup": "warning"}] * 130)
    kinds = [(index, item.kind) for index, item in enumerate(sent) if item is not None]
    assert kinds == [(0, "problem"), (60, "reminder"), (120, "reminder")]
    assert sent[60].title == "[RAG Assistant] Still failing: Backups"
    assert sent[60].lines[0] == "STILL FAILING Backups (warning) for 60 min: detail"

    quiet = engine(confirm=1, repeat_hours=0)
    assert [item.kind for item in run(quiet, [{"backup": "warning"}] * 200) if item is not None] == ["problem"]


def test_unknown_results_change_nothing() -> None:
    alerts = engine(confirm=2)
    steps: list[dict[CheckId, CheckStatus]] = [
        {"queue": "warning"},
        {"queue": "unknown"},
        {"queue": "warning"},
        {"queue": "unknown"},
        {"queue": "unknown"},
    ]
    sent = run(alerts, steps)
    assert [item.kind if item else None for item in sent] == [None, None, "problem", None, None]
    assert alerts.states["queue"].status == "warning"


def test_a_change_of_severity_is_a_new_problem() -> None:
    alerts = engine(confirm=1)
    sent = run(alerts, [{"disk_uploads": "warning"}, {"disk_uploads": "critical"}, {"disk_uploads": "warning"}])
    assert [(item.kind, item.lines[0].split(":")[0]) for item in sent] == [
        ("problem", "PROBLEM Disk space for uploads (warning)"),
        ("problem", "PROBLEM Disk space for uploads (critical)"),
        ("problem", "PROBLEM Disk space for uploads (warning)"),
    ]


def test_everything_that_changed_at_once_is_one_notification() -> None:
    alerts = engine(confirm=1, link=None)
    alerts.evaluate([result("redis", "critical")], T0)
    notification = alerts.evaluate(
        [
            result("postgres", "critical", "Unreachable: connection refused"),
            result("redis", "ok", "Reachable"),
            result("qdrant", "critical", "Unreachable: timed out"),
            result("worker", "critical", "No heartbeat"),
            result("proxy", "critical", "HTTP 502"),
            result("backup", "ok"),
        ],
        T0 + 300,
    )

    assert notification is not None and notification.kind == "problem"
    assert notification.title == (
        "[RAG Assistant] Problem: Database (Postgres), Vector store (Qdrant), Ingestion worker and 1 more"
    )
    assert notification.lines == [
        "PROBLEM Database (Postgres) (critical): Unreachable: connection refused",
        "PROBLEM Vector store (Qdrant) (critical): Unreachable: timed out",
        "PROBLEM Ingestion worker (critical): No heartbeat",
        "PROBLEM Web server (nginx) (critical): HTTP 502",
        "RESOLVED Redis: OK again after 5 min (Reachable)",
        "At 2026-09-21 14:18:20 UTC.",
    ]
    assert notification.text == "\n".join(notification.lines)


def test_states_survive_a_restart() -> None:
    first = engine(confirm=1)
    run(first, [{"qdrant": "critical"}])
    dumped = first.dump()

    second = engine(confirm=1)
    second.load(dumped)
    assert second.evaluate([result("qdrant", "critical")], T0 + MINUTE) is None
    recovery = second.evaluate([result("qdrant", "ok")], T0 + 2 * MINUTE)
    assert recovery is not None and recovery.kind == "recovery"


@pytest.mark.parametrize(
    "data",
    [None, [], {"qdrant": {"status": "ok", "bogus": 1}}, {"qdrant": {"status": "exploded"}}, {1: {"status": "ok"}}],
)
def test_loading_garbage_keeps_a_clean_state(data: object) -> None:
    alerts = engine()
    alerts.load(data)
    assert alerts.states == {}


def test_a_loaded_state_keeps_its_fields() -> None:
    alerts = engine()
    alerts.load({"backup": {"status": "warning", "since": 1.0, "notified_at": 2.0}})
    assert alerts.states == {"backup": CheckState(status="warning", since=1.0, notified_at=2.0)}


def test_the_test_notification() -> None:
    notification = engine().test_notification(T0)
    assert notification.kind == "test"
    assert notification.title == "[RAG Assistant] Test alert"
    assert notification.lines[0].startswith("This is a test alert sent from Admin -> System.")


def test_scrubber_removes_secret_values_and_url_credentials() -> None:
    environ = {
        "SECRET_KEY": "s3cr3t-signing-key-value",
        "POSTGRES_PASSWORD": "db-password-value",
        "ALERT_WEBHOOK_URL": "https://hooks.example.com/services/T000/B000/XXXX",
        "OPENAI_API_KEY": "sk-live-abcdef",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "7200000",
        "COOKIE_SECURE": "false",
        "SHORT_TOKEN": "abc",
        "ADMIN_USERNAME": "admin",
    }
    scrub = Scrubber.from_environment(environ, extra=["https://ntfy.sh/private-topic-name"])

    text = scrub(
        "key s3cr3t-signing-key-value, db db-password-value, hook https://hooks.example.com/services/T000/B000/XXXX, "
        "sk-live-abcdef, postgres://rag:pw@postgres:5432/rag, https://ntfy.sh/private-topic-name, "
        "7200000 false abc admin"
    )

    assert text == (
        "key [redacted], db [redacted], hook [redacted], [redacted], postgres://[redacted]@postgres:5432/rag, "
        "[redacted], 7200000 false abc admin"
    )


def test_messages_are_scrubbed() -> None:
    alerts = AlertEngine(
        name="RAG Assistant", confirm=1, repeat_seconds=0, link=None, scrub=Scrubber(["leaked-secret-value"])
    )
    notification = alerts.evaluate([result("backup", "critical", "failed: leaked-secret-value")], T0)
    assert notification is not None
    assert notification.lines[0] == "PROBLEM Backups (critical): failed: [redacted]"
