"""Usage controls: rate limiting, monthly budgets and usage accounting."""

import time
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import LLMModel, UsageEvent, User
from app.redis_client import get_redis
from app.services.llm import Usage


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


async def chat_tokens_this_month(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(func.coalesce(func.sum(UsageEvent.total_tokens), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.kind == "chat",
            UsageEvent.created_at >= month_start(),
        )
    )
    return int(result.scalar_one() or 0)


async def check_budget(db: AsyncSession, user: User) -> None:
    """Raises 429 when the user's monthly chat-token budget is used up."""
    if user.is_admin or user.monthly_token_limit <= 0:
        return
    used = await chat_tokens_this_month(db, user.id)
    if used >= user.monthly_token_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Monthly token budget used up ({used:,} of {user.monthly_token_limit:,} tokens). "
                "Ask the admin to raise your limit."
            ),
        )


async def record_usage(
    db: AsyncSession, *, user_id: int, model: LLMModel | None, kind: str, usage: Usage, commit: bool = True
) -> None:
    db.add(
        UsageEvent(
            user_id=user_id,
            model_id=model.id if model is not None else None,
            model_name=model.litellm_model if model is not None else "unknown",
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
    totals = {"chat": (0, 0.0), "embedding": (0, 0.0)}
    for kind, tokens, cost in rows.all():
        totals[kind] = (int(tokens or 0), float(cost or 0.0))
    return {
        "month": start.strftime("%Y-%m"),
        "chat_tokens": totals["chat"][0],
        "embedding_tokens": totals["embedding"][0],
        "cost_usd": round(totals["chat"][1] + totals["embedding"][1], 6),
        "monthly_token_limit": 0 if user.is_admin else user.monthly_token_limit,
    }
