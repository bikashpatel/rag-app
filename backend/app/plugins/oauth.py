"""The OAuth 2.0 authorization-code flow of plugins (brief 5.1 and 5.2): PUBLIC_BASE_URLS, connect (state, PKCE, the
browser-binding nonce cookie), the callback, the token exchange and Google's identity checks.

The callback can't see the SameSite=Strict session cookie (the browser arrives from the provider's site), so it
identifies the user from the server-side state, and the nonce cookie (SameSite=Lax) binds that state to the browser
that started it. Redirect URIs are built only from PUBLIC_BASE_URLS entries matching the request's Origin header.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import PluginConnection, User, utcnow
from app.plugins import connections, http, registry
from app.plugins.base import AccountIdentity, Plugin, PluginError, TokenResponse
from app.redis_client import get_redis
from app.schemas_plugins import OAuthResult

logger = logging.getLogger(__name__)

STATE_SECONDS = 600
_CONNECTS_PER_MINUTE = 10
_STATE_KEY = "plugin:oauth:{}"
_RATE_KEY = "plugin:connect:{}:{}"
_GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
_CLOCK_SKEW_SECONDS = 300
_ERROR_CODE = re.compile(r"[^a-z0-9_.-]")
_HOST = re.compile(r"[a-z0-9_.-]+|[0-9a-f:.]+")  # a host name or IPv4 address, or an IPv6 address (in brackets)


def callback_path(plugin_id: str) -> str:
    return f"/api/plugins/{plugin_id}/oauth/callback"


def cookie_name(plugin_id: str) -> str:
    return f"rag_oauth_{plugin_id}"


def cookie_path(plugin_id: str) -> str:
    return f"/api/plugins/{plugin_id}/oauth/"


# ---------------- PUBLIC_BASE_URLS ----------------


def normalize_origin(value: str) -> str | None:
    """`scheme://host[:port]` in canonical form (lowercase, default ports dropped, no trailing slash), or None when
    it isn't an http(s) origin without path, query, fragment or user info."""
    value = value.strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in ("http", "https") or not host or not _HOST.fullmatch(host) or parts.path not in ("", "/"):
        return None
    if parts.query or parts.fragment or parts.username is not None or parts.password is not None:
        return None
    if "?" in value or "#" in value:
        return None
    if (scheme, port) in (("http", 80), ("https", 443)):
        port = None
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")


_bases: tuple[str, list[str]] | None = None


def public_base_urls() -> list[str]:
    """The valid PUBLIC_BASE_URLS entries, normalized, in order; invalid ones are logged once and ignored."""
    global _bases
    source = settings.public_base_urls
    if _bases is None or _bases[0] != source:
        valid: list[str] = []
        invalid: list[str] = []
        for part in source.split(","):
            if not part.strip():
                continue
            normalized = normalize_origin(part)
            if normalized is None:
                invalid.append(part.strip())
            elif normalized not in valid:
                valid.append(normalized)
        if invalid:
            logger.warning(
                "Ignoring invalid PUBLIC_BASE_URLS entries (scheme://host[:port] only): %s", ", ".join(invalid)
            )
        _bases = (source, valid)
    return list(_bases[1])


def redirect_uri(base: str, plugin_id: str) -> str:
    return base + callback_path(plugin_id)


def log_startup(plugins: Sequence[Plugin]) -> None:
    """Startup warnings: invalid PUBLIC_BASE_URLS entries, and plugins pointed at test endpoints."""
    public_base_urls()
    for plugin in plugins:
        if plugin.test_endpoints():
            logger.warning("The %s plugin uses test endpoints instead of the provider's (a test setup)", plugin.id)


# ---------------- Connect ----------------


@dataclass(frozen=True)
class ConnectStart:
    authorize_url: str
    nonce: str  # the value of the rag_oauth_<plugin> cookie


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def start_connect(
    db: AsyncSession, user: User, plugin: Plugin, requested: Sequence[str] | None, origin: str | None
) -> ConnectStart:
    """Checks, the server-side state and the provider's authorize URL (brief 5.1). Raises HTTPException."""
    if registry.configured(plugin):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"{plugin.name} isn't set up yet. Ask the admin."
        )
    current = await registry.state(db, plugin)
    if not current.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{plugin.name} is turned off.")
    enabled = current.enabled_capabilities
    if requested is None:
        wanted = enabled
    else:
        unknown = sorted(set(requested) - enabled)
        if unknown or not requested:
            detail = (
                f"Unknown or turned off: {', '.join(unknown)}." if unknown else "Choose at least one capability."
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
        wanted = frozenset(requested)
    redis = get_redis()
    rate_key = _RATE_KEY.format(user.id, int(time.time() // 60))
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(rate_key, 120)
    if attempts > _CONNECTS_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many connection attempts. Try again in a minute."
        )
    bases = public_base_urls()
    base = normalize_origin(origin) if origin and origin != "null" else None
    if base is None or base not in bases:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connect from {bases[0]}: this address isn't in PUBLIC_BASE_URLS.",
        )
    existing = await db.scalar(
        select(PluginConnection.account_email).where(
            PluginConnection.user_id == user.id, PluginConnection.plugin_id == plugin.id
        )
    )
    await db.commit()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    callback = redirect_uri(base, plugin.id)
    capabilities = [capability.id for capability in plugin.capabilities if capability.id in wanted]
    payload = {
        "user_id": user.id,
        "token_version": user.token_version,
        "plugin_id": plugin.id,
        "verifier": verifier,
        "redirect_uri": callback,
        "base": base,
        "nonce_sha256": _hash(nonce),
        "capabilities": capabilities,
        "created_at": utcnow().isoformat(),
    }
    if not await redis.set(_STATE_KEY.format(_hash(state)), json.dumps(payload), ex=STATE_SECONDS, nx=True):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Try again.")
    endpoints = plugin.oauth()
    params = {
        "response_type": "code",
        "client_id": endpoints.client_id,
        "redirect_uri": callback,
        "scope": " ".join(registry.requested_scopes(plugin, frozenset(capabilities))),
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        **endpoints.authorize_params,
    }
    if existing:
        params["login_hint"] = existing
    separator = "&" if "?" in endpoints.authorize_url else "?"
    return ConnectStart(endpoints.authorize_url + separator + urlencode(params), nonce)


# ---------------- Callback ----------------


async def finish_callback(
    plugin_id: str, params: Mapping[str, str], nonce_cookie: str | None
) -> tuple[str, OAuthResult]:
    """Check the provider's redirect and store the connection (brief 5.2). Returns the base to send the browser back to
    ("" when PUBLIC_BASE_URLS has no valid entry) and the result. Never raises for bad input; logs no code, state or
    token."""
    bases = public_base_urls()
    fallback = bases[0] if bases else ""
    state = params.get("state")
    if not state:
        return fallback, "expired"
    raw = await get_redis().getdel(_STATE_KEY.format(_hash(state)))
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return fallback, "expired"
    stored = data.get("base")
    base = stored if isinstance(stored, str) and stored in bases else fallback
    if data.get("plugin_id") != plugin_id:
        return base, "invalid"
    expected = data.get("nonce_sha256")
    if not nonce_cookie or not isinstance(expected, str) or not hmac.compare_digest(_hash(nonce_cookie), expected):
        return base, "invalid"
    if error := params.get("error"):
        if error == "access_denied":
            return base, "denied"
        logger.warning("The %s consent ended with error %s", plugin_id, _ERROR_CODE.sub("", error.lower())[:40])
        return base, "error"
    code = params.get("code")
    if not code:
        return base, "error"
    plugin = registry.get_plugin(plugin_id)
    if plugin is None:
        return base, "disabled"
    async with SessionLocal() as db:
        user = await db.get(User, data.get("user_id"))
        if user is None or not user.is_active or user.token_version != data.get("token_version"):
            return base, "invalid"
        current = await registry.state(db, plugin)
    if not current.enabled or registry.configured(plugin):
        return base, "disabled"
    token = await exchange_code(plugin, code, str(data.get("redirect_uri")), str(data.get("verifier")))
    if token is None:
        return base, "error"
    try:
        identity = plugin.identify(token)
    except PluginError as exc:
        logger.warning("The %s identity check failed: %s", plugin_id, exc)
        return base, "error"
    if not registry.granted_capabilities(plugin, token.scopes):
        logger.info("A %s consent of user %s granted no capability", plugin_id, user.id)
        await connections.revoke_unless_shared(plugin, identity.subject, token.refresh_token or token.access_token)
        return base, "no_access"
    result = await connections.store(user, plugin, token, identity)
    return base, result


async def exchange_code(plugin: Plugin, code: str, redirect: str, verifier: str) -> TokenResponse | None:
    """POST the authorization code (with the PKCE verifier). None when it fails (logged: HTTP status and the
    provider's error code)."""
    endpoints = plugin.oauth()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": endpoints.client_id,
        "client_secret": endpoints.client_secret,
        "code_verifier": verifier,
    }
    try:
        response = await http.client().post(endpoints.token_url, data=form, timeout=http.TOKEN_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("The %s token exchange failed: %s", plugin.id, type(exc).__name__)
        return None
    if response.status_code != 200:
        logger.warning(
            "The %s token exchange failed: HTTP %s %s",
            plugin.id,
            response.status_code,
            connections.error_code(response) or "",
        )
        return None
    try:
        return connections.parse_token_response(response.json())
    except ValueError as exc:
        logger.warning("The %s token exchange returned an unusable response: %s", plugin.id, exc)
        return None


def identify_google(token: TokenResponse, client_id: str) -> AccountIdentity:
    """The Google account of a token response, from its id_token. The signature isn't verified: the token came
    straight from Google's token endpoint over TLS, which OpenID Connect Core 3.1.3.7 allows in place of it. The
    audience, issuer, expiry, address and its verification are checked. Raises PluginError."""
    if not token.id_token:
        raise PluginError("Google didn't say which account this is (no id_token).")
    try:
        claims = jwt.decode(token.id_token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise PluginError("Google's id_token couldn't be read.") from exc
    audience = claims.get("aud")
    if not (audience == client_id or (isinstance(audience, list) and client_id in audience)):
        raise PluginError("Google's id_token is for another client.")
    if claims.get("iss") not in _GOOGLE_ISSUERS:
        raise PluginError("Google's id_token has an unexpected issuer.")
    expires = claims.get("exp")
    if not isinstance(expires, int | float) or time.time() > expires + _CLOCK_SKEW_SECONDS:
        raise PluginError("Google's id_token has expired.")
    subject, email = claims.get("sub"), claims.get("email")
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise PluginError("Google's id_token has no account id.")
    if not isinstance(email, str) or not 0 < len(email) <= 320:
        raise PluginError("Google didn't share the account's email address.")
    if claims.get("email_verified") not in (True, "true"):
        raise PluginError("The Google account's email address isn't verified.")
    return AccountIdentity(subject=subject, email=email)
