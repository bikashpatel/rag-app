"""The host watchdog (scripts/watchdog/watchdog.sh; SPEC.md section 10), run against stand-ins for curl, docker and
sleep (and launchctl and systemctl) on PATH: it never touches a real stack, Docker, tunnel or alert channel.

One failed probe restarts nothing; two in a row start the stack's containers as they are (`docker compose start`, never
a build or a recreated container); whether that worked is decided by the app answering, not by an exit code; a
maintenance file (a deploy, a running restore, an app held stopped) keeps it away, unless it was left behind.

The public address (RAG_PUBLIC_URL, off by default) is asked only while the app answers: two failures in a row restart
the tunnel (a launchd job or a systemd unit, its name validated), never Docker or the stack, with alerts and a state of
its own."""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from harness import ROOT

WATCHDOG = ROOT / "scripts" / "watchdog" / "watchdog.sh"

PUBLIC_URL = "https://public.invalid/api/health"
LABEL = "invalid.rag-watchdog-test.tunnel"  # a launchd job that can't exist
KICKSTART = f"launchctl kickstart -k gui/{os.getuid()}/{LABEL}"

# The app answers while ./health says "ok"; ./answers, when it has lines, gives the next answers first. Alerts (curl
# with a config on stdin) are appended to ./alerts. The public address answers with the HTTP status in ./public (000:
# no answer), ./public-answers first; each probe adds a line to ./public-probes.
CURL = """#!/usr/bin/env bash
cd "$WATCHDOG_TEST"
for argument in "$@"; do
  if [ "$argument" = -K ]; then
    { printf '%s ' "$@"; echo; cat; echo; } >> alerts
    exit 0
  fi
done
case "$*" in
  *https://public.invalid/*)
    echo probe >> public-probes
    if [ -s public-answers ]; then
      status=$(head -1 public-answers)
      sed -i.bak 1d public-answers
    else
      status=$(cat public)
    fi
    printf '%s' "$status"
    [ "$status" != 000 ] || exit 7
    exit 0
    ;;
esac
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
# launchctl and systemctl: records the call; the public address answers afterwards when ./tunnel-fixes exists.
TUNNEL = """#!/usr/bin/env bash
cd "$WATCHDOG_TEST"
echo "$(basename "$0") $*" >> tunnel
if [ -e tunnel-fixes ]; then echo 200 > public; fi
exit "$(cat exit-tunnel 2> /dev/null || echo 0)"
"""


@dataclass
class Run:
    code: int
    log: str
    docker: list[str]
    alerts: str
    state: str
    tunnel: list[str]
    public_probes: int


class Watchdog:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.app = base / "app"
        self.app.mkdir()
        (self.app / ".env").write_text("ALERT_WEBHOOK_URL=https://alerts.invalid/hook\nALERT_NAME=Docs\n")
        bin_dir = base / "bin"
        bin_dir.mkdir()
        stand_ins = {"curl": CURL, "docker": DOCKER, "sleep": SLEEP, "launchctl": TUNNEL, "systemctl": TUNNEL}
        for name, body in stand_ins.items():
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
            "RAG_PUBLIC_URL": "",  # off, whatever the caller's environment says
            "RAG_TUNNEL_LAUNCHD_LABEL": "",
            "RAG_TUNNEL_SYSTEMD_UNIT": "",
        }
        self.health("down")
        self.public("200")

    def health(self, answer: str, *answers: str) -> None:
        (self.base / "health").write_text(f"{answer}\n")
        (self.base / "answers").write_text("".join(f"{item}\n" for item in answers))

    def public(self, status: str, *statuses: str) -> None:
        (self.base / "public").write_text(f"{status}\n")
        (self.base / "public-answers").write_text("".join(f"{item}\n" for item in statuses))

    def write(self, name: str, text: str) -> None:
        (self.base / name).write_text(text)

    def read(self, name: str) -> str:
        path = self.base / name
        return path.read_text() if path.exists() else ""

    def run(self) -> Run:
        for name in ("docker", "alerts", "sleeps", "tunnel", "public-probes"):
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
            self.read("tunnel").splitlines(),
            len(self.read("public-probes").splitlines()),
        )


@pytest.fixture
def watchdog(tmp_path: Path) -> Watchdog:
    return Watchdog(tmp_path)


@pytest.fixture
def public(watchdog: Watchdog) -> Watchdog:
    """An answering app whose public address is checked, with the tunnel restarted through launchd."""
    watchdog.environment |= {"RAG_PUBLIC_URL": PUBLIC_URL, "RAG_TUNNEL_LAUNCHD_LABEL": LABEL}
    watchdog.health("ok")
    return watchdog


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


def test_without_a_public_address_only_the_app_is_checked(watchdog: Watchdog) -> None:
    watchdog.health("ok")
    watchdog.public("530")
    watchdog.environment["RAG_TUNNEL_LAUNCHD_LABEL"] = LABEL  # inert without RAG_PUBLIC_URL
    run = watchdog.run()
    assert (run.code, run.docker, run.alerts, run.tunnel, run.public_probes, run.log) == (0, [], "", [], 0, "")
    assert len(run.state.splitlines()) == 1 and run.state.startswith("up ")


def test_one_failed_public_probe_restarts_nothing(public: Watchdog) -> None:
    public.public("301", "502")  # a redirect counts as an answer
    run = public.run()
    assert (run.code, run.docker, run.tunnel, run.alerts) == (0, [], [], "")
    assert run.public_probes == 2 and public.read("sleeps").splitlines() == ["30"]
    assert f"{PUBLIC_URL} answers HTTP 502; asking again in 30 s" in run.log and "answered the second time" in run.log
    assert run.state.splitlines()[1].startswith("public up ")


@pytest.mark.parametrize(
    ("setting", "value", "command"),
    [
        ("RAG_TUNNEL_LAUNCHD_LABEL", LABEL, KICKSTART),
        ("RAG_TUNNEL_SYSTEMD_UNIT", "tunnel@it.service", "systemctl --no-ask-password restart tunnel@it.service"),
    ],
    ids=["launchd", "systemd"],
)
def test_two_failed_public_probes_restart_the_tunnel(public: Watchdog, setting: str, value: str, command: str) -> None:
    public.environment |= {"RAG_TUNNEL_LAUNCHD_LABEL": "", setting: value}
    public.public("530")
    public.write("tunnel-fixes", "")
    run = public.run()
    assert run.code == 0, run.log
    assert run.tunnel == [command] and run.docker == []  # the tunnel only: never Docker or the stack
    assert "Problem: the public address was down and the tunnel was restarted" in run.alerts
    assert f"{PUBLIC_URL} answers HTTP 530 while the app answers" in run.alerts
    app, public_state = run.state.splitlines()
    assert app.startswith("up ") and public_state.startswith("public up ")


def test_a_public_address_that_stays_down_alerts_reminds_and_resolves(public: Watchdog) -> None:
    public.public("530")
    run = public.run()
    assert run.code == 1 and run.tunnel == [KICKSTART] and run.docker == []
    assert "Problem: the public address is down" in run.alerts
    assert "restarted the tunnel (launchd job invalid.rag-watchdog-test.tunnel), but it still answers HTTP 530" in (
        run.alerts
    )
    app, public_state = run.state.splitlines()
    assert app.startswith("up ") and public_state.startswith("public down ")

    run = public.run()  # still down: the tunnel is restarted again, without a second alert
    assert (run.code, run.tunnel, run.alerts) == (1, [KICKSTART], "")

    seven_hours_ago = int(time.time()) - 7 * 3600
    public.write("state", f"{app}\npublic down {seven_hours_ago} {seven_hours_ago}\n")
    run = public.run()
    assert run.code == 1 and "Still failing: the public address is down" in run.alerts
    assert "has been down for 420 min" in run.alerts
    assert run.state.splitlines()[1].startswith(f"public down {seven_hours_ago} ")

    public.public("200")
    run = public.run()
    assert (run.code, run.tunnel, run.public_probes) == (0, [], 1)
    assert "Resolved: the public address answers again" in run.alerts and "Problem" not in run.alerts
    assert run.state.splitlines()[1].startswith("public up ")


def test_a_tunnel_restart_that_ends_an_outage_resolves_it(public: Watchdog) -> None:
    hour_ago = int(time.time()) - 3600
    public.write("state", f"up {hour_ago} 0\npublic down {hour_ago} {hour_ago}\n")
    public.public("530")
    public.write("tunnel-fixes", "")
    run = public.run()
    assert run.code == 0 and run.tunnel == [KICKSTART]
    assert "Resolved: the public address answers again" in run.alerts and "Problem" not in run.alerts
    assert f"{PUBLIC_URL} answers again after 60 min (the watchdog on" in run.alerts


def test_a_tunnel_restart_that_fails_is_alerted(public: Watchdog) -> None:
    public.public("530")
    public.write("exit-tunnel", "113")  # launchctl: no such job
    run = public.run()
    assert (run.code, run.tunnel, run.docker) == (1, [KICKSTART], [])
    assert "The tunnel restart failed" in run.log and public.read("sleeps").splitlines() == ["30"]
    assert "Problem: the public address is down" in run.alerts and "couldn't restart the tunnel" in run.alerts
    assert run.state.splitlines()[1].startswith("public down ")


def test_an_app_that_is_down_leaves_the_tunnel_and_its_state_alone(public: Watchdog) -> None:
    hour_ago = int(time.time()) - 3600
    public.write("state", f"up {hour_ago} 0\npublic down {hour_ago} {hour_ago}\n")
    public.health("down")
    public.write("fixes", "start\n")
    run = public.run()
    assert run.code == 0, run.log
    assert run.docker == ["info", "info", "compose start"] and (run.tunnel, run.public_probes) == ([], 0)
    assert "the app was down and was restarted" in run.alerts and "public address" not in run.alerts
    app, public_state = run.state.splitlines()
    assert app.startswith("up ") and public_state == f"public down {hour_ago} {hour_ago}"


def test_the_app_stopping_during_the_public_check_leaves_the_tunnel_alone(public: Watchdog) -> None:
    public.health("down", "ok")
    public.public("530")
    run = public.run()
    assert (run.code, run.docker, run.tunnel, run.alerts) == (1, [], [], "")
    assert "stopped answering" in run.log and run.public_probes == 2


@pytest.mark.parametrize("deploy", ["before", "between-probes"])
def test_a_maintenance_file_keeps_the_public_check_away(public: Watchdog, deploy: str) -> None:
    public.public("530")
    public.write("tunnel-fixes", "")
    if deploy == "before":
        public.maintenance.write_text("")
    else:
        public.write("deploy-on-sleep", "")
    run = public.run()
    assert (run.code, run.docker, run.tunnel, run.alerts) == (0, [], [], "")
    assert "Maintenance in progress" in run.log and run.public_probes == (0 if deploy == "before" else 2)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("RAG_TUNNEL_LAUNCHD_LABEL", ""),
        ("RAG_TUNNEL_LAUNCHD_LABEL", "com.example.tunnel; launchctl reboot"),
        ("RAG_TUNNEL_LAUNCHD_LABEL", "gui/501/com.example.tunnel"),
        ("RAG_TUNNEL_LAUNCHD_LABEL", "-p"),
        ("RAG_TUNNEL_LAUNCHD_LABEL", "com.exämple.tunnel"),
        ("RAG_TUNNEL_SYSTEMD_UNIT", "-Hattacker.example"),
        ("RAG_TUNNEL_SYSTEMD_UNIT", "cloudflared.service\ndocker.service"),
        ("RAG_TUNNEL_SYSTEMD_UNIT", "$(id).service"),
    ],
    ids=["none", "command", "domain", "option", "non-ascii", "unit-option", "two-units", "substitution"],
)
def test_a_bad_tunnel_name_is_refused_and_the_failure_only_alerts(public: Watchdog, setting: str, value: str) -> None:
    public.environment |= {"RAG_TUNNEL_LAUNCHD_LABEL": "", setting: value}
    public.public("000")
    run = public.run()
    assert (run.code, run.docker, run.tunnel) == (1, [], [])
    assert (f"Refusing {setting}" in run.log) == bool(value)
    assert "Problem: the public address is down" in run.alerts and "restart it by hand" in run.alerts
    assert f"{PUBLIC_URL} doesn't answer while the app answers" in run.alerts
    assert public.read("sleeps").splitlines() == ["30"]  # nothing restarted, so nothing to wait for


def test_a_public_address_that_isnt_http_is_refused(public: Watchdog) -> None:
    public.environment["RAG_PUBLIC_URL"] = "-K/etc/hosts"
    public.public("530")
    run = public.run()
    assert (run.code, run.docker, run.tunnel, run.alerts, run.public_probes) == (0, [], [], "", 0)
    assert "Refusing RAG_PUBLIC_URL" in run.log and len(run.state.splitlines()) == 1
