"""Alerts on state changes (SPEC.md section 10): a check must give the same new result `confirm` times in a row
before a problem or a recovery is announced; a check that stays failing is announced again every `repeat_seconds`;
everything that changed in one evaluation goes out as one notification. "unknown" results change nothing (another
check, e.g. Postgres, reports the cause).

Messages are built from fixed sentences and the checks' details only, then scrubbed of every secret value in the
environment and of credentials in URLs.
"""

import os
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.ops.checks import format_age
from app.schemas_system import AlertKind, CheckStatus, SystemCheck

EventKind = Literal["problem", "reminder", "recovery"]
_KIND_ORDER: dict[EventKind, int] = {"problem": 0, "reminder": 1, "recovery": 2}
_TITLES: dict[AlertKind, str] = {
    "problem": "Problem",
    "reminder": "Still failing",
    "recovery": "Resolved",
    "test": "Test alert",
}
_SECRET_NAME = re.compile(r"(KEY|SECRET|PASSWORD|PASSWD|TOKEN|CREDENTIAL|WEBHOOK|NTFY_URL|DATABASE_URL)", re.IGNORECASE)
_URL_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s@]+@", re.IGNORECASE)
_MIN_SECRET_LENGTH = 6
_MAX_LABELS_IN_TITLE = 3


class Scrubber:
    """Replaces known secret values and URL credentials with "[redacted]"."""

    def __init__(self, secrets: Iterable[str]) -> None:
        # Numbers and switches (ACCESS_TOKEN_EXPIRE_MINUTES=720, ...) aren't secrets, and would mangle the details.
        kept = {
            value
            for value in secrets
            if len(value) >= _MIN_SECRET_LENGTH and not value.isdigit() and value.lower() not in ("true", "false")
        }
        self._secrets = sorted(kept, key=len, reverse=True)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None, extra: Iterable[str] = ()) -> "Scrubber":
        values = environ if environ is not None else os.environ
        return cls([value for name, value in values.items() if _SECRET_NAME.search(name)] + list(extra))

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[redacted]")
        return _URL_CREDENTIALS.sub(r"\1[redacted]@", text)


@dataclass
class Event:
    kind: EventKind
    check_id: str
    label: str
    status: CheckStatus
    detail: str
    duration: float  # problem: 0; reminder: failing for; recovery: was failing for


@dataclass
class Notification:
    kind: AlertKind  # the most important event's kind
    title: str
    lines: list[str]
    check_ids: list[str]
    at: float = field(default_factory=time.time)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class CheckState:
    status: CheckStatus = "ok"  # the confirmed status
    since: float = 0.0  # when the confirmed status was first seen
    notified_at: float = 0.0  # the last problem or reminder sent for a failing status
    candidate: CheckStatus | None = None  # a different status seen `streak` times in a row
    candidate_since: float = 0.0
    streak: int = 0


class AlertEngine:
    def __init__(self, *, name: str, confirm: int, repeat_seconds: float, link: str | None, scrub: Scrubber) -> None:
        self.name = name
        self.confirm = max(1, confirm)
        self.repeat_seconds = repeat_seconds
        self.link = link
        self.scrub = scrub
        self.states: dict[str, CheckState] = {}

    def evaluate(self, checks: list[SystemCheck], now: float) -> Notification | None:
        events = [event for item in checks if (event := self._observe(item, now)) is not None]
        return self.notification(events, now) if events else None

    def _observe(self, item: SystemCheck, now: float) -> Event | None:
        state = self.states.setdefault(item.id, CheckState(since=now))
        observed = item.status
        if observed == "unknown":
            return None
        if observed == state.status:
            state.candidate, state.streak = None, 0
            due = state.notified_at + self.repeat_seconds
            if observed != "ok" and self.repeat_seconds > 0 and now >= due:
                state.notified_at = now
                return Event("reminder", item.id, item.label, observed, item.detail, now - state.since)
            return None
        if state.candidate == observed:
            state.streak += 1
        else:
            state.candidate, state.candidate_since, state.streak = observed, now, 1
        if state.streak < self.confirm:
            return None
        failing_since = state.since
        state.status, state.since = observed, state.candidate_since
        state.candidate, state.streak = None, 0
        if observed == "ok":
            return Event("recovery", item.id, item.label, "ok", item.detail, state.since - failing_since)
        state.notified_at = now
        return Event("problem", item.id, item.label, observed, item.detail, 0.0)

    def notification(self, events: list[Event], now: float) -> Notification:
        events = sorted(events, key=lambda event: _KIND_ORDER[event.kind])
        kind = events[0].kind
        labels = [event.label for event in events if event.kind == kind]
        shown = ", ".join(labels[:_MAX_LABELS_IN_TITLE])
        if len(labels) > _MAX_LABELS_IN_TITLE:
            shown += f" and {len(labels) - _MAX_LABELS_IN_TITLE} more"
        lines = [_line(event) for event in events]
        lines.append(f"At {_clock(now)}.")
        if self.link:
            lines.append(f"Details: {self.link}")
        return Notification(
            kind=kind,
            title=self.scrub(f"[{self.name}] {_TITLES[kind]}: {shown}"),
            lines=[self.scrub(line) for line in lines],
            check_ids=[event.check_id for event in events],
            at=now,
        )

    def test_notification(self, now: float) -> Notification:
        lines = [
            "This is a test alert sent from Admin -> System. If you can read it, this channel works.",
            f"At {_clock(now)}.",
        ]
        return Notification("test", self.scrub(f"[{self.name}] {_TITLES['test']}"), lines, [], now)

    def dump(self) -> dict[str, Any]:
        return {check_id: asdict(state) for check_id, state in self.states.items()}

    def load(self, data: object) -> None:
        """Restores dumped states (a restarted monitor neither repeats a problem nor misses its recovery). A check
        this engine has already seen keeps its own state: it is newer."""
        if not isinstance(data, dict):
            return
        for check_id, raw in data.items():
            try:
                state = CheckState(**raw)
            except TypeError:
                continue
            if state.status in ("ok", "warning", "critical") and isinstance(check_id, str):
                self.states.setdefault(check_id, state)


def _line(event: Event) -> str:
    if event.kind == "problem":
        return f"PROBLEM {event.label} ({event.status}): {event.detail}"
    if event.kind == "reminder":
        return f"STILL FAILING {event.label} ({event.status}) for {format_age(event.duration)}: {event.detail}"
    return f"RESOLVED {event.label}: OK again after {format_age(event.duration)} ({event.detail})"


def _clock(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
