"""The audit trail of account changes (docs/briefs/signup-auth.md 8.4): sign-ups, confirmations, resets, email
changes and undos, Google links, step-ups and the admin's sign-up settings. Sign-ins stay in login_events."""

from collections.abc import Mapping

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthEvent, utcnow
from app.services import account


async def record(
    db: AsyncSession,
    type: str,
    *,
    actor: str,
    user_id: int | None = None,
    request: Request | None = None,
    detail: Mapping[str, object] | None = None,
) -> None:
    """Adds an auth_events row (the caller commits). `actor`: "self", "admin:<username>" or "system"; `detail` holds
    masked addresses only (emails.mask); the IP is the request's, approximate."""
    host = request.client.host if request is not None and request.client else None
    db.add(
        AuthEvent(
            created_at=utcnow(),
            user_id=user_id,
            type=type,
            actor=actor,
            ip=account.approximate_ip(host),
            detail=dict(detail or {}),
        )
    )
