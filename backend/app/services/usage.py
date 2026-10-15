"""Usage controls: rate limiting, monthly budgets and usage accounting."""

import logging
import secrets
import time
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import LLMModel, UsageEvent, User
from app.redis_client import get_redis
from app.services.llm import Usage

logger = logging.getLogger(__name__)

# Answers a user with a monthly budget may have generating at once. The budget is checked before an answer and
# charged after it, so without this every request started in parallel would pass the check.
MAX_CHATS_IN_FLIGHT = 2
_CHAT_IN_FLIGHT = "Another answer is still being generated. Wait for it to finish, then try again."


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_bounds(month: str | None) -> tuple[datetime, datetime, str]:
    """Parse "YYYY-MM" (None = current month). Returns (start, end_exclusive, label). Raises ValueError."""
    if month:
        try:
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError("month must look like YYYY-MM") from exc
    else:
        start = month_start()
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end, start.strftime("%Y-%m")


def user_limits(user: User) -> dict:
    """UserLimits shape. The admin bypasses limits, so it is reported as unlimited."""
    if user.is_admin:
        return {
            "monthly_token_limit": 0,
            "rate_limit_per_min": 0,
            "max_upload_mb": settings.max_upload_mb_hard,
            "max_documents": 0,
            "max_storage_mb": 0,
        }
    return {
        "monthly_token_limit": user.monthly_token_limit,
        "rate_limit_per_min": user.rate_limit_per_min,
        "max_upload_mb": min(user.max_upload_mb, settings.max_upload_mb_hard),
        "max_documents": user.max_documents,
        "max_storage_mb": user.max_storage_mb,
    }


async def check_rate_limit(user: User) -> None:
    """Fixed one-minute window per user, in Redis. Raises 429 with Retry-After."""
    if user.is_admin or user.rate_limit_per_min <= 0:
        return
    redis = get_redis()
    now = time.time()
    key = f"rl:chat:{user.id}:{int(now // 60)}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 120)
    if count > user.rate_limit_per_min:
        retry_after = max(1, 60 - int(now % 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit reached ({user.rate_limit_per_min} requests per minute). "
                f"Try again in {retry_after}s."
            ),
            headers={"Retry-After": str(retry_after)},
        )


async def _tokens_this_month(db: AsyncSession, user_id: int, kind: str) -> int:
    result = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.total_tokens), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == kind,
            UsageEvent.created_at >= month_start(),
        )
    )
    return int(result.scalar_one() or 0)


async def chat_tokens_this_month(db: AsyncSession, user_id: int) -> int:
    return await _tokens_this_month(db, user_id, "chat")


async def vision_tokens_this_month(db: AsyncSession, user_id: int) -> int:
    """Checked against VISION_MONTHLY_TOKEN_LIMIT when an ingestion run starts."""
    return await _tokens_this_month(db, user_id, "vision")


async def check_budget(db: AsyncSession, user: User) -> int | None:
    """The chat tokens left this month (None: no budget, or the admin). Raises 429 when none are left."""
    if user.is_admin or user.monthly_token_limit <= 0:
        return None
    used = await chat_tokens_this_month(db, user.id)
    if used >= user.monthly_token_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Monthly token budget used up ({used:,} of {user.monthly_token_limit:,} tokens). "
                "Ask the admin to raise your limit."
            ),
        )
    return user.monthly_token_limit - used


async def acquire_chat_slot(user: User) -> str | None:
    """Take one of the user's MAX_CHATS_IN_FLIGHT answer slots (429 when all are taken). Returns the slot to pass
    to release_chat_slot, or None for users without a monthly budget."""
    if user.is_admin or user.monthly_token_limit <= 0:
        return None
    redis = get_redis()
    key, slot, now = _slots_key(user.id), secrets.token_hex(8), time.time()
    ttl = _slot_ttl()
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, "-inf", now - ttl)  # slots of streams that never ran, so were never released
        pipe.zadd(key, {slot: now})
        pipe.zcard(key)
        pipe.expire(key, ttl)
        _, _, taken, _ = await pipe.execute()
    if taken > MAX_CHATS_IN_FLIGHT:
        await redis.zrem(key, slot)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_CHAT_IN_FLIGHT, headers={"Retry-After": "5"}
        )
    return slot


async def release_chat_slot(user_id: int, slot: str | None) -> None:
    if slot is None:
        return
    try:
        await get_redis().zrem(_slots_key(user_id), slot)
    except Exception as exc:  # the slot expires on its own
        logger.warning("Could not release a chat slot of user %s: %s", user_id, exc)


async def refresh_chat_slot(user_id: int, slot: str | None) -> None:
    """Re-score a slot with the current time while its answer still streams (an answer that writes files can outlast
    the slot's TTL). XX: a slot released meanwhile isn't created again. Best effort."""
    if slot is None:
        return
    try:
        async with get_redis().pipeline(transaction=True) as pipe:
            pipe.zadd(_slots_key(user_id), {slot: time.time()}, xx=True)
            pipe.expire(_slots_key(user_id), _slot_ttl())
            await pipe.execute()
    except Exception as exc:
        logger.warning("Could not refresh a chat slot of user %s: %s", user_id, exc)


def _slots_key(user_id: int) -> str:
    return f"chat:inflight:{user_id}"


def _slot_ttl() -> int:
    """Longer than any answer can take."""
    return settings.llm_timeout_seconds * 2 + 60


async def record_usage(
    db: AsyncSession,
    *,
    user_id: int,
    model: LLMModel | None,
    kind: str,
    usage: Usage,
    commit: bool = True,
    model_name: str | None = None,
) -> None:
    """`model_name` names a model that was deleted meanwhile (`model` None: a new row can't reference it)."""
    db.add(
        UsageEvent(
            user_id=user_id,
            model_id=model.id if model is not None else None,
            model_name=model_name or (model.litellm_model if model is not None else "unknown"),
            kind=kind,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=float(usage.cost_usd or 0.0),
        )
    )
    if commit:
        await db.commit()


async def usage_summary(db: AsyncSession, user: User) -> dict:
    """UsageSummary shape for the current month."""
    start = month_start()
    rows = await db.execute(
        select(
            UsageEvent.kind,
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
        )
        .where(UsageEvent.user_id == user.id, UsageEvent.created_at >= start)
        .group_by(UsageEvent.kind)
    )
    totals = {"chat": (0, 0.0), "embedding": (0, 0.0), "vision": (0, 0.0)}
    for kind, tokens, cost in rows.all():
        totals[kind] = (int(tokens or 0), float(cost or 0.0))
    return {
        "month": start.strftime("%Y-%m"),
        "chat_tokens": totals["chat"][0],
        "embedding_tokens": totals["embedding"][0],
        "vision_tokens": totals["vision"][0],
        "cost_usd": round(sum(cost for _, cost in totals.values()), 6),
        "monthly_token_limit": 0 if user.is_admin else user.monthly_token_limit,
    }
