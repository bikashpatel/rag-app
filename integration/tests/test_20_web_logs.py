"""Flow 20: the web server's logs (frontend/nginx.conf, scripts/backup/lib.sh; SPEC.md section 10).

nginx writes every request to the day's file on the weblogs volume (UTC), in the image's format or by path only, never
an OAuth callback, and nothing of it to `docker compose logs`; the backend writes no access log; the backup service
deletes a file once it was last written more than 30 days ago, when its scheduler starts and at every scheduled time.
The run-wide check for secrets in these files is in test_99_final.py."""

import re
import time
import uuid

import httpx
from harness import ACCESS_LOGS, APP_URL, compose, exec_in, wait_until, web_logs

DAY = 86400
# An access log line's request and status, as nginx's `main` format and uvicorn's access log write them
ACCESS_LINE = re.compile(r'"(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS) /\S* HTTP/[\d.]+" \d{3} ')


def utc_day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(at))


def logged(marker: str, since: float) -> list[str]:
    """The lines holding `marker` in the access log of the day `since` falls on, or of the next one past midnight."""
    days = sorted({utc_day(since), utc_day(time.time())})
    files = " ".join(f"{ACCESS_LOGS}/access-{day}.log" for day in days)
    text = exec_in("frontend", "sh", "-c", f"cat {files} 2> /dev/null || true")
    return [line for line in text.splitlines() if marker in line]


def test_a_request_is_logged_in_the_days_file_and_not_in_the_container_log(stack: None) -> None:
    marker = uuid.uuid4().hex[:12]
    started = time.time()
    headers = {"CF-Connecting-IP": "203.0.113.20", "User-Agent": f"it-weblog/{marker}"}
    assert httpx.get(f"{APP_URL}/it-weblog-{marker}?probe=1", headers=headers, timeout=10).status_code == 200
    (line,) = wait_until(lambda: logged(marker, started), 15, "the request in the day's access log")
    # The image's `main` format: address, user, local time (UTC), request, status, bytes, referrer, browser, XFF
    pattern = rf'203\.0\.113\.20 - - \[[^\]]+ \+0000\] "GET /it-weblog-{marker}\?probe=1 HTTP/1\.1" 200 \d+ "-" '
    assert re.fullmatch(pattern + rf'"it-weblog/{marker}" "-"', line), line

    container_log = compose("logs", "--no-color", "frontend", timeout=120)
    assert marker not in container_log
    assert not ACCESS_LINE.search(container_log), "an access line in docker compose logs frontend"
    names = exec_in("frontend", "ls", ACCESS_LOGS).split()
    assert names and all(re.fullmatch(r"access-\d{4}-\d{2}-\d{2}\.log", name) for name in names), names
    # The volume took the image's folder (also after test_14's `down -v`); nginx's workers created the files.
    assert exec_in("frontend", "stat", "-c", "%U:%G %a", ACCESS_LOGS).strip() == "nginx:nginx 750"
    owners = exec_in("frontend", "sh", "-c", f"stat -c %U:%G {ACCESS_LOGS}/*").split()
    assert set(owners) == {"nginx:nginx"}


def test_secret_queries_are_logged_by_path_or_not_at_all(stack: None) -> None:
    marker = uuid.uuid4().hex[:12]
    secret = f"it{marker}"  # in no line, unlike the marker in the browser's name
    started = time.time()
    # One after the other, so the callbacks would be logged before the line the test waits for
    for path in (
        f"/api/auth/google/callback?state={secret}&code={secret}",
        f"/api/plugins/gmail/oauth/callback?state={secret}&code={secret}",
        f"/api/plugins?q={secret}",
        f"/api/billing/razorpay/return?razorpay_payment_id=pay_{secret}&razorpay_signature={secret}",
    ):
        httpx.get(f"{APP_URL}{path}", headers={"User-Agent": f"it-weblog/{marker}"}, follow_redirects=False, timeout=10)
    wait_until(lambda: any("/razorpay/return" in line for line in logged(marker, started)), 15, "the return's line")
    # `path_only`: the method, the path without its query, the protocol
    requests = [line.split('"')[1] for line in logged(marker, started)]
    assert requests == ["GET /api/plugins HTTP/1.1", "GET /api/billing/razorpay/return HTTP/1.1"]
    everything = web_logs()
    assert secret not in everything
    # The whole run since test_14's restore drill: its Gmail connects, Google sign-ins and payments included
    assert "/oauth/callback" not in everything and "/api/auth/google/callback" not in everything
    assert "razorpay_signature" not in everything


def test_the_backend_writes_no_access_log(stack: None) -> None:
    marker = uuid.uuid4().hex[:12]
    started = time.time()
    assert httpx.get(f"{APP_URL}/api/health?it={marker}", timeout=10).status_code == 200
    wait_until(lambda: logged(marker, started), 15, "nginx's line of the request")
    backend_log = compose("logs", "--no-color", "backend", timeout=120)
    assert marker not in backend_log
    # Not even its healthcheck's requests (every 10 s)
    assert not ACCESS_LINE.search(backend_log), "an access line in docker compose logs backend"


def test_the_backup_service_deletes_logs_last_written_over_30_days_ago(stack: None) -> None:
    now = int(time.time())
    expired, kept = f"access-{utc_day(now - 31 * DAY)}.log", f"access-{utc_day(now - 29 * DAY)}.log"
    files = {expired: now - 31 * DAY, kept: now - 29 * DAY}
    setup = " && ".join(f"echo it > {name} && touch -d @{at} {name}" for name, at in files.items())
    exec_in("backup", "sh", "-c", f"cd /weblogs && {setup}")
    try:
        output = exec_in("backup", "sh", "-c", ". /opt/backup/lib.sh && prune_weblogs")
        names = exec_in("frontend", "ls", ACCESS_LOGS).split()
        assert expired not in names and kept in names and f"access-{utc_day(time.time())}.log" in names
        assert f"backup: Deleted the web server log {expired} (older than 30 days)" in output
        assert kept not in output
    finally:
        exec_in("backup", "rm", "-f", *(f"/weblogs/{name}" for name in files))


def test_the_scheduler_deletes_expired_logs_at_start_and_at_the_scheduled_time() -> None:
    """lib.sh sourced with its clock at 2026-10-25 02:20 UTC, moved on by each wait, backups at 02:30; the scheduler
    stops at its first backup. A copy of the volume in /tmp: the real one stays as it is."""
    start = 1792894800
    files = {
        "access-2026-09-24.log": start - 30 * DAY - 3 * 3600,  # expired at start
        "access-2026-09-25.log": start - 30 * DAY + 240,  # expires at 02:24, before the 02:30 run
        "access-2026-09-26.log": start - 29 * DAY,
    }
    setup = " && ".join(f"echo it > /tmp/w/{name} && touch -d @{at} /tmp/w/{name}" for name, at in files.items())
    command = (
        f"mkdir -p /tmp/w /tmp/r /tmp/s && {setup} && WEBLOGS=/tmp/w BACKUP_ROOT=/tmp/r STATUS_DIR=/tmp/s "
        "BACKUP_SCHEDULE=02:30 BACKUP_START_DELAY_SECONDS=3600 sh -c '. /opt/backup/lib.sh && "
        f'clock={start} && now() {{ echo "$clock"; }} && pause() {{ clock=$((clock + $1)); }} && '
        'run_backup() { log "The $1 backup"; ls /tmp/w; exit 0; } && schedule\''
    )
    output = compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "/bin/sh", "backup", "-c", command, timeout=120)
    lines = output.splitlines()

    def at(text: str) -> int:
        (index,) = [number for number, line in enumerate(lines) if text in line]
        return index

    assert (
        at("Deleted the web server log access-2026-09-24.log (older than 30 days)")
        < at("Scheduled daily at 02:30 UTC; next at 2026-10-25T02:30:00Z;")
        < at("Deleted the web server log access-2026-09-25.log (older than 30 days)")
        < at("The scheduled backup")
    ), output
    assert "web server logs 30 days" in lines[at("Scheduled daily")]
    assert lines[-1] == "access-2026-09-26.log" and "access-2026-09-26.log" not in "".join(lines[:-1])
