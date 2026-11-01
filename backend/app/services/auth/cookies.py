"""The sign-up and Google sign-in cookies (docs/briefs/signup-auth.md 8.3): HttpOnly, path-scoped, and behind HTTPS
(COOKIE_SECURE) Secure with the __Secure- prefix, which a plain-http page can't plant. The session cookie is
app.deps.session_cookie_name()."""

from typing import Final, Literal

from fastapi import Response

from app.config import settings

SIGNUP: Final = "rag_signup"  # the sign-up browser's binding (Path /api/auth/signup)
GOOGLE_SIGNIN: Final = "rag_gsignin"  # the Google state's browser binding (Path /api/auth/google/callback)
GOOGLE_TICKET: Final = "rag_gticket"  # a pending Google sign-up (Path /api/auth/google/)


def cookie_name(base: str) -> str:
    return f"__Secure-{base}" if settings.cookie_secure else base


def set_auth_cookie(
    response: Response, base: str, value: str, *, path: str, max_age: int, samesite: Literal["lax", "strict"]
) -> None:
    response.set_cookie(
        key=cookie_name(base),
        value=value,
        max_age=max_age,
        path=path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=samesite,
    )


def clear_auth_cookie(response: Response, base: str, *, path: str) -> None:
    response.delete_cookie(key=cookie_name(base), path=path, httponly=True, secure=settings.cookie_secure)
