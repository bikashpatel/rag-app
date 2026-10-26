"""What the monitor publishes in Redis for Admin -> System (its last checks, recent alerts, its alert states), and the
test-alert request the backend leaves for it. The backend never sends alerts itself."""

import json
import time

from pydantic import BaseModel, Field, ValidationError

from app.ops.settings import AlertChannelName
from app.redis_client import get_redis
from app.schemas_system import AlertRecord, SystemCheck

MONITOR_STATUS_KEY = "rag:monitor:status"
TEST_ALERT_KEY = "rag:monitor:test_alert"
TEST_ALERT_TTL_SECONDS = 600
ALERT_HISTORY = 50


class PublishedStatus(BaseModel):
    updated_at: float
    interval_seconds: int = Field(ge=1)
    channels: list[AlertChannelName] = []
    checks: list[SystemCheck] = []
    alerts: list[AlertRecord] = []  # newest first
    states: dict = {}

    def fresh(self, now: float) -> bool:
        """The monitor ran within three intervals (plus a slow cycle's margin)."""
        return now - self.updated_at <= 3 * self.interval_seconds + 30


async def read_published() -> PublishedStatus | None:
    raw = await get_redis().get(MONITOR_STATUS_KEY)
    if raw is None:
        return None
    try:
        return PublishedStatus.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return None


async def publish(status: PublishedStatus) -> None:
    await get_redis().set(MONITOR_STATUS_KEY, status.model_dump_json())


async def request_test_alert(now: float | None = None) -> bool:
    """False when a test alert is already waiting for the monitor."""
    stamp = str(time.time() if now is None else now)
    return bool(await get_redis().set(TEST_ALERT_KEY, stamp, nx=True, ex=TEST_ALERT_TTL_SECONDS))


async def pending_test_alert() -> bool:
    return bool(await get_redis().exists(TEST_ALERT_KEY))


async def take_test_alert() -> bool:
    """True once per request (the monitor's side)."""
    return await get_redis().getdel(TEST_ALERT_KEY) is not None
