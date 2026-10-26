"""The monitor service: `python -m app.ops.monitor` (SPEC.md section 10).

Every MONITOR_INTERVAL_SECONDS it runs the system checks plus the app through nginx, turns state changes into alerts
(app.ops.alerts), sends them to the configured channels (retrying a failed channel for up to an hour), and publishes
its results for Admin -> System. With no channel configured, alerts are only logged and shown there. Nothing in a
cycle can stop the loop. `python -m app.ops.monitor --check` is the container's healthcheck.
"""

import asyncio
import contextlib
import logging
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.db import close_db
from app.ops import checks, monitor_state
from app.ops.alerts import AlertEngine, Notification, Scrubber
from app.ops.channels import Channel, ChannelError, configured
from app.ops.settings import AlertChannelName, AlertSettings, load
from app.redis_client import close_redis
from app.schemas_system import AlertRecord, SystemCheck
from app.services.vectorstore import close_qdrant

logger = logging.getLogger("app.monitor")

ALIVE_FILE = Path("/tmp/rag-monitor-alive")
RETRY_SECONDS = 3600


@dataclass
class _Pending:
    notification: Notification
    record: AlertRecord
    remaining: list[AlertChannelName]
    first_at: float
    attempts: int = 0


@dataclass
class Monitor:
    alert_settings: AlertSettings
    channels: list[Channel]
    engine: AlertEngine
    clock: Callable[[], float] = time.time
    history: list[AlertRecord] = field(default_factory=list)  # newest first
    outbox: list[_Pending] = field(default_factory=list)
    loaded: bool = False
    stopping: asyncio.Event = field(default_factory=asyncio.Event)

    async def cycle(self) -> None:
        now = self.clock()
        if not self.loaded:
            await self._load()
        snapshot = await checks.collect(self.alert_settings.monitor_app_url or None)
        notification = self.engine.evaluate(snapshot.checks, now)
        if notification is not None:
            self._queue(notification, now)
        if await self._test_requested():
            self._queue(self.engine.test_notification(now), now)
        await self._deliver(now)
        await self._publish(snapshot.checks, now)

    async def _load(self) -> None:
        """The states and alerts the previous run published; retried every cycle until Redis answers."""
        try:
            published = await monitor_state.read_published()
        except Exception as exc:
            logger.warning("Can't read the monitor's previous state: %s", checks.describe_error(exc))
            return
        if published is not None:
            self.engine.load(published.states)
            self.history = (self.history + published.alerts)[: monitor_state.ALERT_HISTORY]
        self.loaded = True

    async def _test_requested(self) -> bool:
        try:
            return await monitor_state.take_test_alert()
        except Exception:
            return False

    def _queue(self, notification: Notification, now: float) -> None:
        record = AlertRecord(
            at=datetime.fromtimestamp(notification.at, timezone.utc),
            kind=notification.kind,
            title=notification.title,
            lines=notification.lines,
            delivered=[],
            failed=[],
        )
        self.history.insert(0, record)
        del self.history[monitor_state.ALERT_HISTORY :]
        logger.warning("Alert: %s | %s", notification.title, " | ".join(notification.lines))
        if self.channels:
            self.outbox.append(_Pending(notification, record, [channel.name for channel in self.channels], now))
        else:
            logger.warning(
                "No alert channel is configured: the alert above is only logged and shown in Admin -> System"
            )

    async def _deliver(self, now: float) -> None:
        for pending in list(self.outbox):
            targets = [channel for channel in self.channels if channel.name in pending.remaining]
            results = await asyncio.gather(*(self._send(channel, pending.notification) for channel in targets))
            pending.attempts += 1
            for channel, ok in zip(targets, results, strict=True):
                if ok:
                    pending.remaining.remove(channel.name)
                    pending.record.delivered.append(channel.name)
            pending.record.failed = list(pending.remaining)
            if not pending.remaining:
                self.outbox.remove(pending)
            elif now - pending.first_at >= RETRY_SECONDS:
                logger.warning(
                    "Gave up on an alert after %d attempts; undelivered: %s",
                    pending.attempts,
                    ", ".join(pending.remaining),
                )
                self.outbox.remove(pending)

    async def _send(self, channel: Channel, notification: Notification) -> bool:
        try:
            await channel.send(notification)
        except ChannelError as exc:
            logger.warning("The %s alert channel failed: %s", channel.name, exc)
            return False
        except Exception as exc:
            logger.warning("The %s alert channel failed: %s", channel.name, checks.describe_error(exc))
            return False
        return True

    async def _publish(self, results: list[SystemCheck], now: float) -> None:
        if not self.loaded:
            return  # would replace the previous run's alerts before they were read
        status = monitor_state.PublishedStatus(
            updated_at=now,
            interval_seconds=self.alert_settings.monitor_interval_seconds,
            channels=[channel.name for channel in self.channels],
            checks=results,
            alerts=self.history,
            states=self.engine.dump(),
        )
        try:
            await monitor_state.publish(status)
        except Exception as exc:
            logger.warning("Can't publish the monitor's results: %s", checks.describe_error(exc))

    async def run(self) -> None:
        interval = self.alert_settings.monitor_interval_seconds
        loop = asyncio.get_running_loop()
        while not self.stopping.is_set():
            started = loop.time()
            try:
                await self.cycle()
            except Exception as exc:  # no traceback: messages can hold connection details
                logger.error("The monitor's cycle failed: %s", checks.describe_error(exc))
            mark_alive()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), max(0.0, interval - (loop.time() - started)))

    def stop(self) -> None:
        self.stopping.set()


def mark_alive() -> None:
    with contextlib.suppress(OSError):
        ALIVE_FILE.touch()


def alive(interval: int, now: float | None = None) -> bool:
    try:
        last = ALIVE_FILE.stat().st_mtime
    except OSError:
        return False
    return (time.time() if now is None else now) - last <= 3 * interval + 60


def admin_link() -> str | None:
    origin = next((value.strip() for value in settings.public_base_urls.split(",") if value.strip()), "")
    return f"{origin.rstrip('/')}/admin#system" if origin else None


def build(alert_settings: AlertSettings) -> Monitor:
    scrub = Scrubber.from_environment(extra=[alert_settings.alert_webhook_url, alert_settings.alert_ntfy_url])
    engine = AlertEngine(
        name=alert_settings.alert_name,
        confirm=alert_settings.alert_confirm_checks,
        repeat_seconds=alert_settings.alert_repeat_hours * 3600,
        link=admin_link(),
        scrub=scrub,
    )
    return Monitor(alert_settings, configured(alert_settings), engine)


async def main() -> None:
    monitor = build(load(AlertSettings))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, monitor.stop)
    names = ", ".join(channel.name for channel in monitor.channels) or "none (alerts are only logged)"
    logger.info(
        "Monitor started: checks every %d s; alert channels: %s", monitor.alert_settings.monitor_interval_seconds, names
    )
    try:
        await monitor.run()
    finally:
        await close_redis()
        await close_qdrant()
        await close_db()
    logger.info("Monitor stopped")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(0 if alive(load(AlertSettings).monitor_interval_seconds) else 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())
