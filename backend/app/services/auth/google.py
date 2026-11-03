"""Google sign-in (docs/briefs/signup-auth.md 4.5, 4.6): OpenID Connect's authorization-code flow with PKCE S256,
`state`, `nonce` and the rag_gsignin cookie binding the flow to the browser that started it; the ID token's signature
checked against Google's JWKS with its issuer, audience, expiry and nonce. Users are keyed on (issuer, subject); only
`sub`, the email, `email_verified` and `hd` are read, and no Google token is kept. Separate from the Gmail plugin,
whose Google project, client, callback, cookie and Redis keys are its own."""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from fastapi import HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import AppError
from app.models import PendingEmail, User, UserIdentity, utcnow
from app.plugins import oauth
from app.redis_client import get_redis
from app.schemas_auth import GoogleFrom, GoogleIntent, GooglePending
from app.services import account
from app.services.auth import (
    audit,
    cookies,
    emails,
    identities,
    login,
    signup,
    signup_settings,
    stepup,
    throttle,
    tokens,
)
from app.services.mail import outbox

logger = logging.getLogger(__name__)

CALLBACK_PATH: Final = "/api/auth/google/callback"
TICKET_PATH: Final = "/api/auth/google/"
SCOPE: Final = "openid email"
STATE_SECONDS: Final = 600
TICKET_SECONDS: Final = 900
REAUTH_SECONDS: Final = 300  # a step-up's Google sign-in must be this fresh (auth_time)
HTTP_TIMEOUT: Final = 10.0
JWKS_CACHE: Final = (300, 86_400)  # Cache-Control max-age, clamped
LEEWAY: Final = 60
IAT_AHEAD: Final = 600  # an ID token issued at most this far in the future (clock skew)
PENDING_EMAIL_HOURS: Final = 24
_STATE_KEY = "auth:google:{}"
_TICKET_KEY = "auth:gticket:{}"
_JWKS_KEY = "auth:google:jwks:{}"
_MAX_AGE = re.compile(r"max-age=(\d+)")
_SUBJECT = re.compile(r"[\x21-\x7e]{1,255}")
_ERROR_CODE = re.compile(r"[^a-z0-9_.-]")

ACCOUNT_PAGE: Final = "/?settings=account"

Result = str  # the `google` query value the page shows


class TokenRejected(Exception):
    """The ID token failed a check; str() is the check's name (logged, never the token)."""


@dataclass(frozen=True)
class GoogleAccount:
    issuer: str  # https://accounts.google.com
    subject: str
    email: emails.Email
    kind: identities.Kind
    auth_time: int | None


@dataclass
class Outcome:
    """Where the callback sends the browser, and what the response also carries."""

    url: str
    sign_in: User | None = None  # a new session (method google)
    ticket: str | None = None  # rag_gticket: a pending Google sign-up
    wake: bool = False  # mail was queued


def configured() -> bool:
    return settings.google_signin_configured


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def redirect_base(origin: str | None) -> str:
    """The request's Origin when it is one of PUBLIC_BASE_URLS; else 400 naming where to sign in."""
    base = oauth.normalize_origin(origin) if origin and origin != "null" else None
    if base is None or base not in oauth.public_base_urls():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Use {tokens.base_url()} to sign in with Google."
        )
    return base


def _with_query(path: str, **values: str) -> str:
    parts = urlsplit(path)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in values]
    return urlunsplit(("", "", parts.path or "/", urlencode([*query, *values.items()]), ""))


# ---------------- Start ----------------


async def start(
    *,
    intent: GoogleIntent,
    base: str,
    next_path: str,
    from_: GoogleFrom | None = None,
    user: User | None = None,
    sid: int | None = None,
    login_hint: str | None = None,
) -> tuple[str, str]:
    """Stores the flow's state (10 minutes) and returns Google's authorize URL and the rag_gsignin cookie's value."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    redirect_uri = base + CALLBACK_PATH
    payload = {
        "intent": intent,
        "from": from_,
        "user_id": user.id if user is not None else None,
        "token_version": user.token_version if user is not None else None,
        "sid": sid,
        "verifier": verifier,
        "nonce": nonce,
        "binding_sha256": _sha256(binding),
        "redirect_uri": redirect_uri,
        "base": base,
        "next": next_path,
        "created_at": utcnow().isoformat(),
    }
    if not await get_redis().set(_STATE_KEY.format(_sha256(state)), json.dumps(payload), ex=STATE_SECONDS, nx=True):
        raise AppError(503, "Try again.", "google_unavailable")
    params = {
        "response_type": "code",
        "client_id": settings.google_signin_client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if login_hint is not None:
        params |= {"login_hint": login_hint, "max_age": "0"}
    separator = "&" if "?" in settings.google_signin_authorize_url else "?"
    return settings.google_signin_authorize_url + separator + urlencode(params), binding


# ---------------- The ID token ----------------


def _max_age(header: str | None) -> int:
    found = _MAX_AGE.search(header or "")
    low, high = JWKS_CACHE
    return min(max(int(found.group(1)) if found else low, low), high)


async def _fetch_keys(cache_key: str) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(settings.google_signin_jwks_url)
        response.raise_for_status()
        keys = response.json()["keys"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Google's signing keys couldn't be fetched (%s)", type(exc).__name__)
        raise TokenRejected("jwks") from None
    if not isinstance(keys, list):
        raise TokenRejected("jwks")
    await get_redis().set(cache_key, json.dumps(keys), ex=_max_age(response.headers.get("cache-control")))
    return keys


def _key(keys: list[dict[str, Any]], kid: str) -> jwt.PyJWK | None:
    for entry in keys:
        if isinstance(entry, dict) and entry.get("kid") == kid and entry.get("kty") == "RSA":
            try:
                return jwt.PyJWK(entry, algorithm="RS256")
            except jwt.PyJWTError:
                return None
    return None


async def _signing_key(kid: str) -> jwt.PyJWK:
    """The JWKS key with this kid: cached by Cache-Control (5 minutes to a day), fetched again once for an unknown
    kid (Google rotates its keys)."""
    cache_key = _JWKS_KEY.format(_sha256(settings.google_signin_jwks_url)[:16])
    cached = await get_redis().get(cache_key)
    keys = json.loads(cached) if cached else await _fetch_keys(cache_key)
    found = _key(keys, kid)
    if found is None and cached:
        found = _key(await _fetch_keys(cache_key), kid)
    if found is None:
        raise TokenRejected("kid")
    return found


_DECODE_ERRORS: tuple[tuple[type[jwt.PyJWTError], str], ...] = (
    (jwt.InvalidSignatureError, "signature"),
    (jwt.ExpiredSignatureError, "exp"),
    (jwt.InvalidAudienceError, "aud"),
    (jwt.MissingRequiredClaimError, "claims"),
)


async def verify_id_token(id_token: str, *, nonce: str) -> GoogleAccount:
    """Google's checks on an ID token: RS256 by `kid` from GOOGLE_SIGNIN_JWKS_URL; `iss`; `aud` (a list must hold the
    client id, and then `azp` must be it); `exp` (60 s leeway); `iat` at most 10 minutes ahead; `nonce`; `sub` of 1 to
    255 printable ASCII characters; a parseable `email`. Raises TokenRejected with the failed check's name."""
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError:
        raise TokenRejected("header") from None
    if header.get("alg") != "RS256":
        raise TokenRejected("alg")
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise TokenRejected("kid")
    key = await _signing_key(kid)
    client_id = settings.google_signin_client_id
    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=client_id,
            leeway=LEEWAY,
            options={"require": ["iss", "aud", "sub", "exp", "iat"], "verify_iat": False},
        )
    except jwt.PyJWTError as exc:
        raise TokenRejected(next((name for kind, name in _DECODE_ERRORS if isinstance(exc, kind)), "token")) from None
    if claims["iss"] not in identities.ISSUERS:
        raise TokenRejected("iss")
    if isinstance(claims["aud"], list) and claims.get("azp") != client_id:
        raise TokenRejected("azp")
    iat = claims["iat"]
    if not isinstance(iat, int) or isinstance(iat, bool) or iat > time.time() + IAT_AHEAD:
        raise TokenRejected("iat")
    found = claims.get("nonce")
    if not isinstance(found, str) or not hmac.compare_digest(found.encode(), nonce.encode()):
        raise TokenRejected("nonce")
    subject = claims["sub"]
    if not isinstance(subject, str) or not _SUBJECT.fullmatch(subject):
        raise TokenRejected("sub")
    try:
        address = emails.parse(claims["email"]) if isinstance(claims.get("email"), str) else None
    except emails.EmailError:
        address = None
    if address is None:
        raise TokenRejected("email")
    verified = claims.get("email_verified") in (True, "true")
    hosted_domain = claims.get("hd") if isinstance(claims.get("hd"), str) else None
    auth_time = claims.get("auth_time")
    return GoogleAccount(
        issuer=identities.ISSUER,
        subject=subject,
        email=address,
        kind=identities.kind(address, verified, hosted_domain),
        auth_time=auth_time if isinstance(auth_time, int) and not isinstance(auth_time, bool) else None,
    )


async def _exchange(code: str, state: dict[str, Any]) -> str | None:
    """The authorization code's ID token (the access token is dropped); None when the exchange fails (logged by
    status and Google's error code only)."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(state["redirect_uri"]),
        "client_id": settings.google_signin_client_id,
        "client_secret": settings.google_signin_client_secret,
        "code_verifier": str(state["verifier"]),
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(settings.google_signin_token_url, data=form)
    except httpx.HTTPError as exc:
        logger.warning("The Google sign-in token exchange failed (%s)", type(exc).__name__)
        return None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("id_token"), str):
        error = body.get("error") if isinstance(body, dict) else None
        code_name = _ERROR_CODE.sub("", str(error).lower())[:40] if error else ""
        logger.warning("The Google sign-in token exchange failed: HTTP %s %s", response.status_code, code_name)
        return None
    return str(body["id_token"])


# ---------------- The callback ----------------


async def _take_state(state: str | None) -> dict[str, Any] | None:
    if not state:
        return None
    raw = await get_redis().getdel(_STATE_KEY.format(_sha256(state)))
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _page(base: str, intent: object, result: Result) -> str:
    """Where a result is shown: the sign-in page for a sign-in, Settings → Account for a link or a step-up."""
    if intent in ("link", "reauth"):
        return f"{base}/?settings=account&google={result}"
    return f"{base}/login?google={result}"


async def callback(db: AsyncSession, request: Request, params: dict[str, str], binding: str | None) -> Outcome:
    """GET /auth/google/callback: always an Outcome (a 303), never an error. The state is taken once, the browser
    must hold its binding cookie, then the code is exchanged and the ID token checked before anything is decided."""
    bases = oauth.public_base_urls()
    fallback = tokens.base_url()
    data = await _take_state(params.get("state"))
    if data is None:
        return Outcome(f"{fallback}/login?google=expired")
    stored = data.get("base")
    base = stored if isinstance(stored, str) and stored in bases else fallback
    intent = data.get("intent")
    expected = data.get("binding_sha256")
    if not binding or not isinstance(expected, str) or not hmac.compare_digest(_sha256(binding), expected):
        return Outcome(_page(base, intent, "error"))
    if error := params.get("error"):
        if error == "access_denied":
            return Outcome(_page(base, intent, "denied"))
        logger.warning("Google sign-in ended with error %s", _ERROR_CODE.sub("", error.lower())[:40])
        return Outcome(_page(base, intent, "error"))
    code = params.get("code")
    id_token = await _exchange(code, data) if code else None
    if id_token is None:
        return Outcome(_page(base, intent, "error"))
    try:
        google = await verify_id_token(id_token, nonce=str(data.get("nonce", "")))
    except TokenRejected as exc:
        logger.warning("A Google ID token failed the %s check", exc)
        return Outcome(_page(base, intent, "error"))
    if intent == "link":
        return await _link(db, request, data, base, google)
    if intent == "reauth":
        return await _reauth(db, request, data, base, google)
    return await _sign_in(db, request, data, base, google)


async def _sign_in(
    db: AsyncSession, request: Request, data: dict[str, Any], base: str, google: GoogleAccount
) -> Outcome:
    next_path = signup.safe_next(data.get("next")) or "/"
    identity = await identities.find(db, google.issuer, google.subject)
    if identity is not None:
        user = await db.get(User, identity.user_id)
        if user is None or not user.is_active or user.is_admin:
            return Outcome(f"{base}/login?google=unavailable")
        identity.last_used_at = utcnow()
        return Outcome(f"{base}{next_path}", sign_in=user)
    if google.kind != "unverified":
        holder = await db.scalar(select(User).where(User.email_canonical == google.email.canonical))
        if holder is not None:
            linkable = (
                google.kind == "gmail"
                and holder.is_active
                and not holder.is_admin
                and account.google_identity(holder) is None
            )
            if not linkable:
                return Outcome(f"{base}/login?google=link_required")
            await identities.link(
                db,
                holder,
                issuer=google.issuer,
                subject=google.subject,
                address=google.email.address,
                email_kind=google.kind,
                method="auto",
                request=request,
            )
            return Outcome(f"{base}{next_path}", sign_in=holder, wake=True)
    if not (await signup_settings.availability(db)).google:
        return Outcome(f"{base}/login?google=signup_closed")
    ticket = secrets.token_urlsafe(32)
    payload = {
        "issuer": google.issuer,
        "subject": google.subject,
        "email": google.email.address,
        "kind": google.kind,
        "next": signup.safe_next(data.get("next")),
        "from": data.get("from") if data.get("from") in ("login", "signup") else "signup",
    }
    await get_redis().set(_TICKET_KEY.format(_sha256(ticket)), json.dumps(payload), ex=TICKET_SECONDS)
    return Outcome(f"{base}/signup/google", ticket=ticket)


async def _state_user(db: AsyncSession, data: dict[str, Any]) -> User | None:
    """The account that started a link or a step-up, if it still exists, is active and its sessions weren't ended
    since (token_version)."""
    user_id = data.get("user_id")
    user = await db.get(User, user_id) if isinstance(user_id, int) else None
    if user is None or not user.is_active or user.is_admin or user.token_version != data.get("token_version"):
        return None
    return user


async def _adopt_email(db: AsyncSession, user: User, google: GoogleAccount) -> None:
    """An account without an email takes a `gmail` or `workspace` address nobody holds as its verified email (its
    pending email for that address dropped, its reset and email links revoked)."""
    canonical = google.email.canonical
    if user.email is not None or google.kind == "unverified" or await signup.email_held(db, canonical):
        return
    try:
        async with db.begin_nested():
            user.email, user.email_canonical, user.email_verified_at = google.email.address, canonical, utcnow()
    except IntegrityError:  # another account took it meanwhile
        await db.refresh(user)
        return
    await db.execute(delete(PendingEmail).where(PendingEmail.user_id == user.id, PendingEmail.canonical == canonical))
    await tokens.revoke(db, ["reset", "email"], user_id=user.id)
    await signup.drop_pending(db, canonical)
    await audit.record(db, "email.confirmed", actor="self", user_id=user.id, detail={"source": "google"})


async def _link(db: AsyncSession, request: Request, data: dict[str, Any], base: str, google: GoogleAccount) -> Outcome:
    """Settings → Account → Connect Google, after the password: linked, in_use, already_linked or expired."""
    user = await _state_user(db, data)
    if user is None:
        return Outcome(_page(base, "link", "expired"))
    identity = await identities.find(db, google.issuer, google.subject)
    if identity is not None:
        return Outcome(_page(base, "link", "already_linked" if identity.user_id == user.id else "in_use"))
    if account.google_identity(user) is not None:
        return Outcome(_page(base, "link", "already_linked"))
    try:
        async with db.begin_nested():
            await _adopt_email(db, user, google)  # first, so the notice goes to the address it gives
            await identities.link(
                db,
                user,
                issuer=google.issuer,
                subject=google.subject,
                address=google.email.address,
                email_kind=google.kind,
                method="manual",
                request=request,
            )
    except IntegrityError:  # linked to someone (or to this user) at the same moment
        await db.rollback()
        return Outcome(_page(base, "link", "in_use"))
    await db.commit()
    return Outcome(_page(base, "link", "linked"), wake=True)


async def _reauth(
    db: AsyncSession, request: Request, data: dict[str, Any], base: str, google: GoogleAccount
) -> Outcome:
    """The step-up: a fresh Google sign-in as the account's linked identity grants this session 5 minutes for one
    email change or a first password."""
    user = await _state_user(db, data)
    sid = data.get("sid")
    if user is None or not isinstance(sid, int):
        return Outcome(_page(base, "reauth", "expired"))
    identity = account.google_identity(user)
    if identity is None or (identity.issuer, identity.subject) != (google.issuer, google.subject):
        return Outcome(_page(base, "reauth", "reauth_mismatch"))
    if google.auth_time is not None and google.auth_time < time.time() - REAUTH_SECONDS:
        return Outcome(_page(base, "reauth", "expired"))
    await stepup.grant(user.id, sid)
    identity.last_used_at = utcnow()
    await audit.record(db, "google.reauth", actor="self", user_id=user.id, request=request)
    await db.commit()
    next_path = signup.safe_next(data.get("next")) or ACCOUNT_PAGE
    return Outcome(base + _with_query(next_path, google="reauth_ok"))


# ---------------- The pending Google sign-up (/signup/google) ----------------


def _ticket_value(request: Request) -> str | None:
    return request.cookies.get(cookies.cookie_name(cookies.GOOGLE_TICKET)) or None


async def pending(request: Request) -> GooglePending:
    """GET /auth/google/pending: the ticket the cookie names, without taking it."""
    await throttle.check(request, "token")
    ticket = _ticket_value(request)
    redis = get_redis()
    key = _TICKET_KEY.format(_sha256(ticket)) if ticket else None
    raw = await redis.get(key) if key else None
    left = await redis.pttl(key) if key and raw is not None else 0
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or left <= 0:
        raise login.link_invalid()
    return GooglePending(
        email=str(data["email"]),
        email_verified=data.get("kind") in ("gmail", "workspace"),
        from_="login" if data.get("from") == "login" else "signup",
        expires_at=utcnow() + timedelta(milliseconds=left),
    )


async def _take_ticket(request: Request) -> dict[str, Any] | None:
    ticket = _ticket_value(request)
    raw = await get_redis().getdel(_TICKET_KEY.format(_sha256(ticket))) if ticket else None
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def clear_ticket(response: Response) -> None:
    cookies.clear_auth_cookie(response, cookies.GOOGLE_TICKET, path=TICKET_PATH)


async def cancel(request: Request, response: Response) -> None:
    """POST /auth/google/cancel: the ticket and its cookie dropped."""
    await _take_ticket(request)
    clear_ticket(response)


async def finish(db: AsyncSession, request: Request, response: Response, name: str) -> User:
    """POST /auth/google/finish (the terms accepted): the ticket taken once and everything checked again; the account
    (no password, the name given), its identity and, for a `gmail` or `workspace` address, its verified email; an
    `unverified` address becomes a pending email with a confirmation queued (when mail can go: else Settings resends
    it). Signed in with a new session."""
    await throttle.check(request, "signup")
    data = await _take_ticket(request)
    if data is None:
        raise login.link_invalid()
    issuer, subject, address, kind = data.get("issuer"), data.get("subject"), data.get("email"), data.get("kind")
    if not (isinstance(issuer, str) and isinstance(subject, str) and isinstance(address, str)):
        raise login.link_invalid()
    if await identities.find(db, issuer, subject) is not None:
        raise login.link_invalid()
    verified = kind in ("gmail", "workspace")
    canonical = address.lower()
    taken = "This address already has an account. Sign in with your password, then connect Google in Settings."
    if verified and await signup.email_held(db, canonical):
        raise login.email_taken(taken)
    if not (await signup_settings.availability(db)).google:
        raise login.signup_closed()
    now = utcnow()
    user = await signup.new_account(
        db,
        "google",
        display_name=name,
        password_hash=None,
        address=address if verified else None,
        canonical=canonical if verified else None,
        terms_version=settings.terms_version,
        terms_accepted_at=now,
    )
    if user is None:
        raise login.email_taken(taken)
    try:
        async with db.begin_nested():
            db.add(
                UserIdentity(
                    user_id=user.id,
                    provider=identities.PROVIDER,
                    issuer=issuer,
                    subject=subject,
                    email_at_link=address,
                    email_kind=kind if kind in ("gmail", "workspace") else "unverified",
                    created_at=now,
                    last_used_at=now,
                )
            )
    except IntegrityError:  # the same Google account finished a sign-up at the same moment
        await db.rollback()
        raise login.link_invalid() from None
    await db.refresh(user, ["identities"])
    queued = False
    if verified:
        await signup.drop_pending(db, canonical)
    else:
        row = PendingEmail(
            user_id=user.id,
            address=address,
            canonical=canonical,
            source="google",
            created_at=now,
            expires_at=now + timedelta(hours=PENDING_EMAIL_HOURS),
        )
        db.add(row)
        await db.flush()
        await audit.record(
            db,
            "email.pending",
            actor="self",
            user_id=user.id,
            request=request,
            detail={"source": "google", "email": emails.mask(address)},
        )
        # A new account hasn't paid. Within the day's room and the silent per-address limit, as an email change.
        delivery_key = emails.parse(address).delivery_key
        if await outbox.available(db, "public") and await throttle.address_allowed("confirm", delivery_key):
            await outbox.enqueue(
                db, "email_confirm", to=address, canonical=canonical, user_id=user.id, pending_email_id=row.id
            )
            queued = True
    await audit.record(
        db,
        "signup.completed",
        actor="self",
        user_id=user.id,
        request=request,
        detail={"method": "google", "email": emails.mask(address)},
    )
    await login.start_session(db, request, response, user, "google")
    clear_ticket(response)
    if queued:
        await outbox.wake()
    return user
