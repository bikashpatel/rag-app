"""The host watchdog (scripts/watchdog/watchdog.sh; SPEC.md section 10), run against stand-ins for curl, docker and
sleep on PATH: it never touches a real stack, Docker or alert channel.

One failed probe restarts nothing; two in a row start the stack's containers as they are (`docker compose start`, never
a build or a recreated container); whether that worked is decided by the app answering, not by an exit code; a
maintenance file (a deploy, a running restore, an app held stopped) keeps it away, unless it was left behind."""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from harness import ROOT

WATCHDOG = ROOT / "scripts" / "watchdog" / "watchdog.sh"

# The app answers while ./health says "ok"; ./answers, when it has lines, gives the next answers first. Alerts (curl
# with a config on stdin) are appended to ./alerts.
CURL = """#!/usr/bin/env bash
cd "$WATCHDOG_TEST"
for argument in "$@"; do
  if [ "$argument" = -K ]; then
    { printf '%s ' "$@"; echo; cat; echo; } >> alerts
    exit 0
  fi
done
if [ -s answers ]; then
  answer=$(head -1 answers)
  sed -i.bak 1d answers
else
  answer=$(cat health)
fi
[ "$answer" = ok ]
"""
# Records every call; `docker compose start` or `restart` brings the app back when ./fixes names it.
DOCKER = """#!/usr/bin/env bash
cd "$WATCHDOG_TEST"
echo "$*" >> docker
case "$*" in
  info) [ ! -e docker-down ] ;;
  "compose start" | "compose restart")
    if grep -qx "${2}" fixes 2> /dev/null; then echo ok > health; fi
    exit "$(cat "exit-$2" 2> /dev/null || echo 0)"
    ;;
esac
"""
# Returns at once; on its first call it can start a deploy (./deploy-on-sleep holds the maintenance file's content).
SLEEP = """#!/usr/bin/env bash
cd "$WATCHDOG_TEST"
echo "$*" >> sleeps
if [ -e deploy-on-sleep ]; then mv deploy-on-sleep "$RAG_MAINTENANCE_FILE"; fi
"""


@dataclass
class Run:
    code: int
    log: str
    docker: list[str]
    alerts: str
    state: str


class Watchdog:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.app = base / "app"
        self.app.mkdir()
        (self.app / ".env").write_text("ALERT_WEBHOOK_URL=https://alerts.invalid/hook\nALERT_NAME=Docs\n")
        bin_dir = base / "bin"
        bin_dir.mkdir()
        for name, body in {"curl": CURL, "docker": DOCKER, "sleep": SLEEP}.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)
        self.maintenance = self.app / ".maintenance"
        self.environment = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WATCHDOG_TEST": str(base),
            "RAG_APP_DIR": str(self.app),
            "RAG_APP_URL": "http://127.0.0.1:9/api/health",
            "RAG_MAINTENANCE_FILE": str(self.maintenance),
            "RAG_MAINTENANCE_MAX_MINUTES": "120",
            "RAG_WATCHDOG_STATE": str(base / "state"),
            "RAG_WATCHDOG_WAIT_SECONDS": "10",
        }
        self.health("down")

    def health(self, answer: str, *answers: str) -> None:
        (self.base / "health").write_text(f"{answer}\n")
        (self.base / "answers").write_text("".join(f"{item}\n" for item in answers))

    def write(self, name: str, text: str) -> None:
        (self.base / name).write_text(text)

    def read(self, name: str) -> str:
        path = self.base / name
        return path.read_text() if path.exists() else ""

    def run(self) -> Run:
        for name in ("docker", "alerts"):
            (self.base / name).unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", str(WATCHDOG)], env=self.environment, capture_output=True, text=True, timeout=60, check=False
        )
        return Run(
            result.returncode,
            result.stdout + result.stderr,
            self.read("docker").splitlines(),
            self.read("alerts"),
            self.read("state").strip(),
        )


@pytest.fixture
def watchdog(tmp_path: Path) -> Watchdog:
    return Watchdog(tmp_path)


def test_an_answering_app_is_left_alone(watchdog: Watchdog) -> None:
    watchdog.health("ok")
    run = watchdog.run()
    assert (run.code, run.docker, run.alerts) == (0, [], "")
    assert run.state.startswith("up ")


def test_one_failed_probe_restarts_nothing(watchdog: Watchdog) -> None:
    watchdog.health("ok", "down")
    run = watchdog.run()
    assert (run.code, run.docker, run.alerts) == (0, [], "")
    assert "asking again in 30 s" in run.log and "It answered the second time" in run.log
    assert watchdog.read("sleeps").splitlines() == ["30"]


def test_two_failed_probes_start_the_containers_as_they_are(watchdog: Watchdog) -> None:
    watchdog.write("fixes", "start\n")
    run = watchdog.run()
    assert run.code == 0, run.log
    assert run.docker == ["info", "info", "compose start"]  # never up, build or a recreated container
    assert "Problem: the app was down and was restarted" in run.alerts and "https://alerts.invalid/hook" in run.alerts
    assert run.state.startswith("up ")


def test_whether_the_restart_worked_is_decided_by_the_app_answering(watchdog: Watchdog) -> None:
    watchdog.write("fixes", "start\n")
    watchdog.write("exit-start", "1")  # a service of the stack failed to start, yet the app answers
    run = watchdog.run()
    assert run.code == 0 and "the app was down and was restarted" in run.alerts

    watchdog.health("down")
    watchdog.write("fixes", "")
    watchdog.write("exit-start", "0")  # every container started, yet the app doesn't answer
    run = watchdog.run()
    assert run.code == 1
    assert run.docker == ["info", "info", "compose start", "compose restart"]
    assert "Problem: the app is down" in run.alerts and "couldn't start it again" in run.alerts
    assert run.state.startswith("down ")

    watchdog.health("ok")
    run = watchdog.run()
    assert run.code == 0 and "Resolved: the app answers again" in run.alerts


@pytest.mark.parametrize(
    ("content", "age_minutes", "left_alone"),
    [
        ("", 5, True),  # a deploy
        (f"pid={os.getpid()}", 600, True),  # a restore still running, however long it takes
        ("hold", 600, True),  # restore.sh --no-start: the app is stopped on purpose
        ("", 600, False),  # left behind by a deploy that didn't finish
        ("pid=999999", 600, False),  # left behind by a restore that was killed
    ],
    ids=["deploy", "running-restore", "held", "stale", "dead-restore"],
)
def test_a_maintenance_file_keeps_the_watchdog_away(
    watchdog: Watchdog, content: str, age_minutes: int, left_alone: bool
) -> None:
    watchdog.maintenance.write_text(f"{content}\n" if content else "")
    then = time.time() - age_minutes * 60
    os.utime(watchdog.maintenance, (then, then))
    watchdog.write("fixes", "start\n")
    run = watchdog.run()
    assert run.code == 0
    if left_alone:
        assert (run.docker, run.alerts) == ([], "") and "not checking" in run.log
    else:
        assert run.docker == ["info", "info", "compose start"] and "left behind" in run.log


def test_a_deploy_starting_between_the_probes_is_left_alone(watchdog: Watchdog) -> None:
    watchdog.write("deploy-on-sleep", "")
    run = watchdog.run()
    assert (run.code, run.docker, run.alerts) == (0, [], "")
    assert "Maintenance in progress" in run.log
