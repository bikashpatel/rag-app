"""The ingestion worker's heartbeat: a Redis key the worker refreshes while it takes jobs, with a TTL.

`python -m app.ops.heartbeat` exits 0 when the heartbeat is fresh (the worker container's healthcheck).
"""

import json
import sys
import time
from dataclasses import dataclass

from app.ops.settings import ops_settings
from app.redis_client import get_redis

WORKER_HEARTBEAT_KEY = "rag:worker:heartbeat"


@dataclass(frozen=True)
class Heartbeat:
    at: float  # Unix time of the beat
    jobs: int  # jobs running then

    def age(self, now: float) -> float:
        return max(0.0, now - self.at)


def encode(jobs: int, now: float | None = None) -> str:
    return json.dumps({"at": time.time() if now is None else now, "jobs": jobs})


def decode(value: str | bytes | None) -> Heartbeat | None:
    """The heartbeat in a key's value; None when absent or malformed."""
    if value is None:
        return None
    try:
        data = json.loads(value)
        at, jobs = float(data["at"]), int(data["jobs"])
    except (ValueError, TypeError, KeyError):
        return None
    return Heartbeat(at, max(0, jobs))


async def beat(jobs: int) -> None:
    await get_redis().set(WORKER_HEARTBEAT_KEY, encode(jobs), ex=ops_settings.worker_heartbeat_max_age_seconds)


async def clear() -> None:
    await get_redis().delete(WORKER_HEARTBEAT_KEY)


async def read() -> Heartbeat | None:
    return decode(await get_redis().get(WORKER_HEARTBEAT_KEY))


def is_fresh(heartbeat: Heartbeat | None, now: float) -> bool:
    return heartbeat is not None and heartbeat.age(now) <= ops_settings.worker_heartbeat_max_age_seconds


def main() -> int:
    from redis import Redis
    from redis.exceptions import RedisError

    from app.config import settings

    try:
        with Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3) as client:
            value = client.get(WORKER_HEARTBEAT_KEY)
    except RedisError:
        return 1
    return 0 if is_fresh(decode(value), time.time()) else 1  # type: ignore[arg-type]


if __name__ == "__main__":
    sys.exit(main())
