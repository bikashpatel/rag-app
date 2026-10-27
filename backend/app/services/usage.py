"""Usage controls and accounting (docs/briefs/plans-billing.md 5.3): the rate limit, the chat allowance in standard
tokens, the image and vision counts, all in the entitlements' allowance window, and recording usage."""

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.functions import FunctionElement

from app.config import settings
from app.errors import AppError
from app.models import LLMModel, UsageEvent, User
from app.redis_client import get_redis
from app.schemas import UsageSummary
from app.services.billing.entitlements import Entitlements
from app.services.billing.money import allowance_tokens, format_ist_date, ist_date
from app.services.llm import Usage

logger = logging.getLogger(__name__)

# Answers a user with a chat allowance may have generating at once. The allowance is checked before an answer and
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
    return start, next_month(start), start.strftime("%Y-%m")


def next_month(start: datetime) -> datetime:
    """The 1st of the month after `start` (the 1st of a month); December rolls over to January."""
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


class utc_day(FunctionElement[Any]):
    """The UTC calendar day of a timestamp column, for grouping usage per day (sessions run in UTC).

    Some SQLite versions round date() to the millisecond (CI's moves 23:59:59.9995 to the next day), so on SQLite the
    day is cut from the stored "YYYY-MM-DD HH:MM:SS.ffffff" text instead.
    """

    inherit_cache = True


@compiles(utc_day)
def _utc_day(element: utc_day, compiler: SQLCompiler, **kw: Any) -> str:
    return f"date({compiler.process(element.clauses, **kw)})"


@compiles(utc_day, "sqlite")
def _utc_day_sqlite(element: utc_day, compiler: SQLCompiler, **kw: Any) -> str:
    return f"substr({compiler.process(element.clauses, **kw)}, 1, 10)"


def allowance_ending(user: User, ent: Entitlements) -> str:
    """When a used-up allowance starts again (IST dates, plans-billing 5.3): with the next period, on the 1st of next
    month (an open-ended plan, also one that follows this period), or the plan's last day when nothing follows."""
    restarts = format_ist_date(ist_date(ent.window_end))
    if ent.period_id is not None and ent.paid_through is not None and ent.paid_through > ent.window_end:
        return f"It starts again with your next period on {restarts}."
    if ent.valid_through is None or user.comp_plan_id is not None:
        return f"It starts again on {restarts}."
    return f"Your {ent.plan_name} plan is valid through {format_ist_date(ent.valid_through)}."


async def check_rate_limit(user: User, ent: Entitlements) -> None:
    """Fixed one-minute window per user, in Redis. Raises 429 with Retry-After."""
    limit = ent.limits.rate_limit_per_min
    if ent.is_admin or limit <= 0:
        return
    redis = get_redis()
    now = time.time()
    key = f"rl:chat:{user.id}:{int(now // 60)}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 120)
    if count > limit:
        retry_after = max(1, 60 - int(now % 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit reached ({limit} requests per minute). Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def _in_window(user_id: int, ent: Entitlements, kind: str) -> ColumnElement[bool]:
    return and_(
        UsageEvent.user_id == user_id,
        UsageEvent.kind == kind,
        UsageEvent.created_at >= ent.window_start,
        UsageEvent.created_at < ent.window_end,
    )


async def chat_allowance_used(db: AsyncSession, user_id: int, ent: Entitlements) -> int:
    """The standard tokens of the user's chat answers in the allowance window."""
    total = func.coalesce(func.sum(UsageEvent.allowance_tokens), 0)
    return int(await db.scalar(select(total).where(_in_window(user_id, ent, "chat"))) or 0)


async def vision_tokens_in_window(db: AsyncSession, user_id: int, ent: Entitlements) -> int:
    """Checked against the vision allowance when an ingestion run starts."""
    total = func.coalesce(func.sum(UsageEvent.total_tokens), 0)
    return int(await db.scalar(select(total).where(_in_window(user_id, ent, "vision"))) or 0)


async def images_in_window(db: AsyncSession, user_id: int, ent: Entitlements) -> int:
    """The user's image usage events in the allowance window: reservations and kept failures count."""
    return int(await db.scalar(select(func.count(UsageEvent.id)).where(_in_window(user_id, ent, "image"))) or 0)


async def check_budget(db: AsyncSession, user: User, ent: Entitlements) -> int | None:
    """The chat allowance left in the window, in standard tokens (None: unlimited, or the admin). Raises 429
    allowance_used when none is left."""
    limit = ent.limits.monthly_token_limit
    if ent.is_admin or limit <= 0:
        return None
    used = await chat_allowance_used(db, user.id, ent)
    if used >= limit:
        raise AppError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've used this period's chat allowance ({used:,} of {limit:,}). {allowance_ending(user, ent)}",
            "allowance_used",
            feature="chat_tokens",
        )
    return limit - used


async def acquire_chat_slot(user: User, ent: Entitlements) -> str | None:
    """Take one of the user's MAX_CHATS_IN_FLIGHT answer slots (429 when all are taken). Returns the slot to pass
    to release_chat_slot, or None for users without a chat allowance."""
    if ent.is_admin or ent.limits.monthly_token_limit <= 0:
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
    """`model_name` names a model that was deleted meanwhile (`model` None: a new row can't reference it). A chat
    event also records what it counts against the chat allowance (money.allowance_tokens), fixed from now on."""
    cost = float(usage.cost_usd or 0.0)
    db.add(
        UsageEvent(
            user_id=user_id,
            model_id=model.id if model is not None else None,
            model_name=model_name or (model.litellm_model if model is not None else "unknown"),
            kind=kind,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=cost,
            allowance_tokens=allowance_tokens(cost, usage.total_tokens) if kind == "chat" else 0,
        )
    )
    if commit:
        await db.commit()


def usage_window(ent: Entitlements, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The window usage is reported in: the current period; else (an open-ended plan, the admin, a user without
    access) the UTC calendar month of `now`."""
    if ent.access and ent.period_id is not None:
        return ent.window_start, ent.window_end
    start = month_start(now)
    return start, next_month(start)


async def usage_summary(db: AsyncSession, user: User, ent: Entitlements) -> UsageSummary:
    """The user's usage in the window (usage_window): what Me.usage and the export report."""
    start, end = usage_window(ent)
    rows = await db.execute(
        select(
            UsageEvent.kind,
            func.coalesce(func.sum(UsageEvent.total_tokens), 0),
            func.coalesce(func.sum(UsageEvent.allowance_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
            func.count(UsageEvent.id),
        )
        .where(UsageEvent.user_id == user.id, UsageEvent.created_at >= start, UsageEvent.created_at < end)
        .group_by(UsageEvent.kind)
    )
    tokens: dict[str, int] = {}
    events: dict[str, int] = {}
    allowance = 0
    cost = 0.0
    for kind, total, counted, spent, count in rows.tuples():
        tokens[kind], events[kind] = int(total or 0), int(count or 0)
        cost += float(spent or 0.0)
        if kind == "chat":
            allowance = int(counted or 0)
    return UsageSummary(
        month=start.strftime("%Y-%m"),
        chat_tokens=tokens.get("chat", 0),
        embedding_tokens=tokens.get("embedding", 0),
        vision_tokens=tokens.get("vision", 0),
        images=events.get("image", 0),
        cost_usd=round(cost, 6),
        monthly_token_limit=ent.limits.monthly_token_limit,
        chat_allowance_used=allowance,
        window_start=start,
        window_end=end,
    )
