"""The admin's sign-up switches (docs/briefs/signup-auth.md S27): app_state "signup_settings", closed by default, and
what is effectively available once the mailer, Turnstile and Google sign-in are taken into account."""

import json
import logging
from typing import Final

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppState
from app.schemas_auth import SignupAvailability, SignupSettings
from app.services.auth import audit

logger = logging.getLogger(__name__)

KEY: Final = "signup_settings"


async def load(db: AsyncSession) -> SignupSettings:
    """The stored settings; missing or unreadable: the defaults (closed, both methods on). Fields a newer release
    stored are ignored."""
    row = await db.get(AppState, KEY)
    if row is None or not row.value:
        return SignupSettings()
    try:
        stored = json.loads(row.value)
        known = {name: stored[name] for name in SignupSettings.model_fields if name in stored}
        return SignupSettings.model_validate(known)
    except (ValueError, TypeError, ValidationError):
        logger.warning("The stored sign-up settings are invalid; sign-up stays closed")
        return SignupSettings()


async def save(db: AsyncSession, value: SignupSettings, *, actor: str) -> None:
    """Stores the settings with a signup.settings event (before and after); the caller commits."""
    before = await load(db)
    row = await db.get(AppState, KEY)
    if row is None:
        row = AppState(key=KEY, value="")
        db.add(row)
    row.value = value.model_dump_json()
    await audit.record(
        db, "signup.settings", actor=actor, detail={"before": before.model_dump(), "after": value.model_dump()}
    )


async def availability(db: AsyncSession) -> SignupAvailability:
    """Email sign-up: open, email on, the mailer and Turnstile configured. Google sign-up: open, google on, Google
    sign-in configured."""
    current = await load(db)
    email = current.open and current.email and settings.mail_configured and settings.turnstile_configured
    google = current.open and current.google and settings.google_signin_configured
    return SignupAvailability(open=email or google, email=email, google=google)
