"""Flow 15: monitoring and alerts (GET /api/health/ready, the monitor service, Admin -> System; SPEC.md section 10).

Readiness goes unready when Qdrant or the worker stops and recovers when they are back; the monitor sends one problem
and one recovery alert per outage to the fake webhook and the fake ntfy topic (integration/fake_alerts), with nothing
secret or personal in them; nginx going away is noticed; a failed delivery is retried; Admin -> System shows it all,
to the admin only; a test alert reaches every channel."""

import json

import httpx
import pytest
from harness import alerts_received, check, client, compose, leaked_secrets, python_in, wait_until

PROBLEM_WAIT = 60  # MONITOR_INTERVAL_SECONDS=2 and ALERT_CONFIRM_CHECKS=2 in the generated env file


def readiness() -> int:
    try:
        return client().get("/api/health/ready", timeout=10).status_code
    except httpx.HTTPError:
        return 0


def lines(prefix: str, label: str, after: int) -> dict[str, list[dict]]:
    """channel -> the alerts received after `after` holding a "<prefix> <label>" line."""
    found: dict[str, list[dict]] = {"webhook": [], "ntfy": []}
    for alert in alerts_received()[after:]:
        if any(line.startswith(f"{prefix} {label}") for line in alert["text"].splitlines()):
            found[alert["channel"]].append(alert)
    return found


def on_both_channels(prefix: str, label: str, after: int) -> dict[str, list[dict]] | None:
    found = lines(prefix, label, after)
    return found if found["webhook"] and found["ntfy"] else None


def outage(service: str, label: str, *, check_readiness: bool) -> None:
    """Stops `service`, waits for the problem alert (and readiness 503), starts it, waits for the recovery alert; one of
    each per channel."""
    start = len(alerts_received())
    compose("stop", service, timeout=150)
    try:
        if check_readiness:
            wait_until(lambda: readiness() == 503, 30, f"readiness to fail without {service}", interval=0.5)
        problem = wait_until(lambda: on_both_channels("PROBLEM", label, start), PROBLEM_WAIT, f"the {label} alert")
    finally:
        compose("start", service, timeout=150)
    if check_readiness:
        wait_until(lambda: readiness() == 200, 120, f"readiness with {service} back", interval=0.5)
    wait_until(lambda: on_both_channels("RESOLVED", label, start), PROBLEM_WAIT + 60, f"the {label} recovery")

    assert all(len(items) == 1 for items in lines("PROBLEM", label, start).values()), problem
    assert all(len(items) == 1 for items in lines("RESOLVED", label, start).values())
    webhook, ntfy = problem["webhook"][0], problem["ntfy"][0]
    title = webhook["text"].splitlines()[0]
    assert title.startswith("[RAG Assistant] Problem: ") and label in title
    assert ntfy["headers"]["title"].startswith("[RAG Assistant] Problem")
    assert ntfy["headers"]["priority"] == "high" and ntfy["headers"]["tags"] == "rotating_light"
    assert ntfy["text"] in webhook["text"]


def test_readiness_is_public_and_says_only_whether_the_app_is_ready(stack: None) -> None:
    response = client().get("/api/health/ready")
    assert (response.status_code, response.json()) == (200, {"status": "ready"})


def test_admin_system_is_for_the_admin_only(admin: httpx.Client, make_user) -> None:
    user = make_user("system")
    assert user.client.get("/api/admin/system").status_code == 403
    assert client().get("/api/admin/system").status_code == 401
    assert user.client.post("/api/admin/system/test-alert").status_code == 403

    def reported() -> dict | None:
        body = check(admin.get("/api/admin/system")).json()
        return body if body["monitor"]["running"] else None

    body = wait_until(reported, 60, "the monitor to report")
    statuses = {item["id"]: item["status"] for item in body["checks"]}
    assert {name: statuses[name] for name in ("postgres", "redis", "qdrant", "worker", "proxy")} == dict.fromkeys(
        ("postgres", "redis", "qdrant", "worker", "proxy"), "ok"
    )
    assert body["monitor"]["channels"] == ["webhook", "ntfy"] and body["monitor"]["interval_seconds"] == 2
    assert body["app_version"] == "it-run" and body["database_size_bytes"] > 0
    assert body["worker"]["heartbeat_age_seconds"] is not None and body["worker"]["heartbeat_age_seconds"] < 6
    assert {disk["id"] for disk in body["disks"]} == {"uploads", "backups"}


@pytest.mark.deadline(300)
def test_qdrant_down_makes_the_app_unready_and_alerts_once(stack: None) -> None:
    outage("qdrant", "Vector store (Qdrant)", check_readiness=True)


@pytest.mark.deadline(300)
def test_a_stopped_worker_makes_the_app_unready_and_alerts_once(stack: None) -> None:
    outage("worker", "Ingestion worker", check_readiness=True)


@pytest.mark.deadline(300)
def test_nginx_going_away_is_noticed(stack: None) -> None:
    outage("frontend", "Web server (nginx)", check_readiness=False)
    wait_until(lambda: readiness() == 200, 60, "nginx to answer again", interval=0.5)


@pytest.mark.deadline(180)
def test_a_test_alert_reaches_every_channel_even_after_a_failed_delivery(admin: httpx.Client) -> None:
    start = len(alerts_received())
    python_in(
        "fake-alerts",
        "import json, urllib.request\n"
        "request = urllib.request.Request('http://127.0.0.1:8000/_fail', data=json.dumps({'count': 2}).encode(),"
        " headers={'Content-Type': 'application/json'})\n"
        "urllib.request.urlopen(request, timeout=10)",
    )
    queued = admin.post("/api/admin/system/test-alert")
    assert queued.status_code == 202, queued.text
    assert admin.post("/api/admin/system/test-alert").status_code in (202, 409)

    def delivered() -> list[dict] | None:
        found = [
            item for item in alerts_received()[start:] if "Test alert" in (item["headers"]["title"] + item["text"])
        ]
        return found if {item["channel"] for item in found} == {"webhook", "ntfy"} else None

    wait_until(delivered, 60, "the test alert on both channels")

    def recorded() -> dict | None:
        alerts = check(admin.get("/api/admin/system")).json()["alerts"]
        test = next((item for item in alerts if item["kind"] == "test"), None)
        return test if test and sorted(test["delivered"]) == ["ntfy", "webhook"] else None

    record = wait_until(recorded, 30, "the test alert in Admin -> System")
    assert record["failed"] == [] and record["title"] == "[RAG Assistant] Test alert"


def test_alerts_hold_no_secrets_usernames_or_document_names(admin: httpx.Client) -> None:
    received = alerts_received()
    assert received
    text = json.dumps(received)
    assert not leaked_secrets(text)
    assert "it-" not in text.replace("rag-it-alerts", "")  # the suite's usernames all start with it-
    for word in ("quokka", "lantern", "alpha", "beta", ".txt", ".pdf", "@example.com"):
        assert word not in text, word
    shown = json.dumps(check(admin.get("/api/admin/system")).json()["alerts"])
    assert not leaked_secrets(shown) and "fake-alerts" not in shown
