"""The password policy (docs/briefs/signup-auth.md 5), applied wherever a password is set and never at sign-in: a
length in code points after NFC, the bundled common-password list, context words and Have I Been Pwned's range check
(which fails open). ADMIN_PASSWORD is exempt; weak_admin_password() only warns about it."""

import hashlib
import logging
import unicodedata
from collections.abc import Sequence
from functools import cache
from pathlib import Path

import httpx

from app.config import settings
from app.errors import AppError
from app.schemas_auth import PASSWORD_MAX_LENGTH, PasswordReason

logger = logging.getLogger(__name__)

BLOCKLIST = Path(__file__).resolve().parents[2] / "data" / "common-passwords.txt"
APP_WORDS = ("asknuvexusdoc", "nuvexus", "nuvexusdoc")
ADMIN_MIN_LENGTH = 15
_CONTEXT_MIN = 4  # shorter context words are ignored
_BREACH_TIMEOUT = 2.0
_MESSAGES: dict[PasswordReason, str] = {
    "too_short": "Use at least {minimum} characters.",
    "too_long": f"Use at most {PASSWORD_MAX_LENGTH} characters.",
    "common": "This password is too common. Choose one that's harder to guess.",
    "context": "Don't use the app's name, your username or your email in the password.",
    "breached": "This password has appeared in a data breach. Choose a different one.",
}


@cache
def _common() -> frozenset[str]:
    lines = BLOCKLIST.read_text(encoding="utf-8").splitlines()
    return frozenset(line for line in lines if line and not line.startswith("#"))


def _letters(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalpha())


def _reject(reason: PasswordReason) -> AppError:
    message = _MESSAGES[reason].format(minimum=settings.password_min_length)
    return AppError(422, message, "password_rejected", reason=reason)


async def check(password: str, *, context: Sequence[str] = ()) -> None:
    """Raises 422 password_rejected (with `reason`) for a password the policy refuses. `context`: the username, the
    email and its local part, refused like the app's name (compared by their letters)."""
    text = unicodedata.normalize("NFC", password)
    if len(text) < settings.password_min_length:
        raise _reject("too_short")
    if len(text) > PASSWORD_MAX_LENGTH:
        raise _reject("too_long")
    if text.lower() in _common():
        raise _reject("common")
    words = {_letters(word) for word in (*APP_WORDS, *context)}
    if _letters(text) in {word for word in words if len(word) >= _CONTEXT_MIN}:
        raise _reject("context")
    if await breached(text):
        raise _reject("breached")


def weak_admin_password(password: str) -> bool:
    """ADMIN_PASSWORD is under 15 characters, common, or contains the app's name or the admin's username. No network:
    the Sign-up card asks breached() once a day."""
    text = unicodedata.normalize("NFC", password)
    letters = _letters(text)
    words = (*APP_WORDS, _letters(settings.admin_username))
    return (
        len(text) < ADMIN_MIN_LENGTH
        or text.lower() in _common()
        or any(len(word) >= _CONTEXT_MIN and word in letters for word in words)
    )


async def breached(password: str) -> bool | None:
    """Whether Have I Been Pwned has seen the password (only the first 5 hex digits of its SHA-1 leave, with padding);
    None when it couldn't tell: the check is off, or the request failed (logged, never with the password)."""
    if not settings.password_breach_check:
        return None
    sha1 = hashlib.sha1(unicodedata.normalize("NFC", password).encode(), usedforsecurity=False).hexdigest().upper()
    headers = {"Add-Padding": "true", "User-Agent": f"{settings.app_name} password check"}
    try:
        async with httpx.AsyncClient(timeout=_BREACH_TIMEOUT) as client:
            response = await client.get(f"{settings.password_breach_check_url}{sha1[:5]}", headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("The breached-password check failed (%s): the password was allowed", type(exc).__name__)
        return None
    for line in response.text.splitlines():
        suffix, _, count = line.strip().partition(":")
        if suffix.upper() == sha1[5:]:
            return count.strip().isdigit() and int(count) > 0
    return False
