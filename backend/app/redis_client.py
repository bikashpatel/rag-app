import logging
from collections.abc import Iterable

from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)

INGEST_QUEUE = "rag:ingest_queue"

_redis: Redis | None = None


def get_redis() -> Redis:
    """Process-wide Redis client (decoded str responses). Tests may call set_redis()."""
    global _redis
    if _redis is None:
        # The read timeout stays well above the worker's 5 s BLPOP poll; it stops a silently dropped
        # connection from blocking forever.
        _redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=30,
            health_check_interval=30,
        )
    return _redis


def set_redis(client: Redis) -> None:
    global _redis
    _redis = client


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def ingest_attempts_key(doc_id: int) -> str:
    """Counts the runs of a document that started but never finished (see ingestion.recover_pending)."""
    return f"rag:ingest_attempts:{doc_id}"


async def forget_ingest_attempts(doc_ids: Iterable[int]) -> None:
    """Best effort: a deliberate retry or a deleted document starts from zero interrupted runs."""
    keys = [ingest_attempts_key(doc_id) for doc_id in doc_ids]
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception as exc:
        logger.warning("Could not reset the ingestion attempts of %d document(s): %s", len(keys), exc)
