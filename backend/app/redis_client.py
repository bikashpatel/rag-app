from redis.asyncio import Redis

from app.config import settings

INGEST_QUEUE = "rag:ingest_queue"

_redis: Redis | None = None


def get_redis() -> Redis:
    """Process-wide Redis client (decoded str responses). Tests may call set_redis()."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def set_redis(client: Redis) -> None:
    global _redis
    _redis = client


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
