"""The plugin audit trail (brief 4.7 and 14): one row per action of a user, metadata only (no subjects, addresses,
queries or text)."""

import logging
from collections import defaultdict
from typing import Literal, get_args

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import PluginAuditEvent, User
from app.plugins.base import Origin
from app.schemas_plugins import PluginAction, PluginActionCount, PluginActivityReport, PluginUserActivity
from app.services.usage import month_bounds

logger = logging.getLogger(__name__)

Outcome = Literal["ok", "error", "refused"]
ACTIONS: tuple[PluginAction, ...] = get_args(PluginAction)


def add(
    db: AsyncSession,
    user_id: int,
    plugin_id: str,
    action: PluginAction,
    *,
    outcome: Outcome = "ok",
    origin: Origin,
    items: int = 0,
) -> None:
    """Record in `db`'s transaction (committed with it)."""
    db.add(
        PluginAuditEvent(
            user_id=user_id, plugin_id=plugin_id, action=action, outcome=outcome, origin=origin, items=max(0, items)
        )
    )


async def record(
    user_id: int,
    plugin_id: str,
    action: PluginAction,
    *,
    outcome: Outcome = "ok",
    origin: Origin,
    items: int = 0,
) -> None:
    """Record in a session of its own. Never raises: a failure is logged."""
    try:
        async with SessionLocal() as db:
            if await db.get(User, user_id) is None:  # deleted meanwhile
                return
            add(db, user_id, plugin_id, action, outcome=outcome, origin=origin, items=items)
            await db.commit()
    except Exception:
        logger.exception("Could not record the %s %s action of user %s", plugin_id, action, user_id)


def _counts(rows: dict[str, dict[str, int]]) -> list[PluginActionCount]:
    """In PluginAction order; only actions that happened."""
    return [
        PluginActionCount(
            action=action,
            ok=rows[action].get("ok", 0),
            failed=rows[action].get("error", 0),
            refused=rows[action].get("refused", 0),
        )
        for action in ACTIONS
        if action in rows
    ]


async def monthly_counts(
    db: AsyncSession, plugin_id: str, month: str | None, user_id: int | None = None
) -> list[PluginActionCount]:
    """The month's ("YYYY-MM", None: this one) counts per action and outcome; ValueError for a bad month."""
    start, end, _ = month_bounds(month)
    query = (
        select(PluginAuditEvent.action, PluginAuditEvent.outcome, func.count())
        .where(
            PluginAuditEvent.plugin_id == plugin_id,
            PluginAuditEvent.created_at >= start,
            PluginAuditEvent.created_at < end,
        )
        .group_by(PluginAuditEvent.action, PluginAuditEvent.outcome)
    )
    if user_id is not None:
        query = query.where(PluginAuditEvent.user_id == user_id)
    rows: defaultdict[str, dict[str, int]] = defaultdict(dict)
    for action, outcome, count in (await db.execute(query)).tuples():
        rows[action][outcome] = int(count)
    return _counts(rows)


async def activity_report(db: AsyncSession, plugin_id: str, month: str | None) -> PluginActivityReport:
    """The month's totals and a per-user table (by username); ValueError for a bad month."""
    start, end, label = month_bounds(month)
    rows = await db.execute(
        select(User.id, User.username, PluginAuditEvent.action, PluginAuditEvent.outcome, func.count())
        .join(User, User.id == PluginAuditEvent.user_id)
        .where(
            PluginAuditEvent.plugin_id == plugin_id,
            PluginAuditEvent.created_at >= start,
            PluginAuditEvent.created_at < end,
        )
        .group_by(User.id, User.username, PluginAuditEvent.action, PluginAuditEvent.outcome)
    )
    totals: defaultdict[str, dict[str, int]] = defaultdict(dict)
    users: dict[int, tuple[str, defaultdict[str, dict[str, int]]]] = {}
    for user_id, username, action, outcome, count in rows.tuples():
        totals[action][outcome] = totals[action].get(outcome, 0) + int(count)
        users.setdefault(user_id, (username, defaultdict(dict)))[1][action][outcome] = int(count)
    return PluginActivityReport(
        month=label,
        totals=_counts(totals),
        by_user=[
            PluginUserActivity(user_id=user_id, username=username, actions=_counts(actions))
            for user_id, (username, actions) in sorted(users.items(), key=lambda item: item[1][0])
        ],
    )
