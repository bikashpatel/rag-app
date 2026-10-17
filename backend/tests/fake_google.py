"""A fake Google OAuth 2.0 / OpenID Connect provider for tests and the Docker smoke (docs/briefs/plugins-gmail.md,
section 18.1). Pure Python: no app imports.

`fake_google_app(state)` serves the consent endpoint (`GET /o/oauth2/v2/auth`, answering at once as if the user
consented), `POST /token` (authorization code with PKCE, refresh) and `POST /revoke`; Gmail routes come from
`tests/fake_gmail.py` and use `FakeGoogle.check_bearer`. Tests route the app's plugin HTTP client here with
`httpx.ASGITransport` (`app.plugins.http.set_transport`).

Modelled as Google documents it: grants per (client, account), shared by every consent of that account (two app users
connecting one Google account share one grant, and revoking any token of a grant revokes all of it); the token's
`scope` lists identity scopes as URLs (`openid https://www.googleapis.com/auth/userinfo.email`, never `email`) and,
with `include_granted_scopes=true`, the union of this consent and the grant's earlier scopes; codes live 60 s and are
one-time; refresh answers `400 invalid_grant` once the grant is revoked.

Not modelled, on purpose: Google's "Access blocked" page for accounts that aren't test users (Google never redirects
back, so there is nothing for the app to handle), and the difference between a testing-mode expiry and a revocation
(Google sends the same `invalid_grant` body for both, so `expired_testing` vs `revoked` stays the app's heuristic).
Consent screens, granular checkboxes (see `grant_scopes`), signature keys (id_tokens are HS256 with a random key)
and rate limits are simplified.
"""

import asyncio
import base64
import hashlib
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

CLIENT_ID = "fake-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "fake-client-secret"
ISSUER = "https://accounts.google.com"
IDENTITY_SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email")
SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}
CODE_SECONDS = 60


def normalize_scope(scope: str) -> str:
    return SCOPE_ALIASES.get(scope, scope)


class GoogleError(Exception):
    """An error answer in Google's JSON shape (rendered by fake_google_app's handler)."""

    def __init__(self, status: int, body: Mapping[str, Any]) -> None:
        super().__init__(status)
        self.status = status
        self.body = dict(body)


@dataclass
class Grant:
    """One account's grant to one client: every refresh and access token issued for it."""

    client_id: str
    account: str  # email
    subject: str
    scopes: list[str] = field(default_factory=list)
    refresh_tokens: set[str] = field(default_factory=set)
    access_tokens: dict[str, float] = field(default_factory=dict)  # token -> expiry (time.time())
    revoked: bool = False


@dataclass
class _Code:
    client_id: str
    redirect_uri: str
    challenge: str
    scopes: list[str]
    account: str
    expires: float
    used: bool = False


@dataclass
class RecordedCall:
    method: str
    path: str
    query_keys: list[str]
    form_keys: list[str] = field(default_factory=list)


class FakeGoogle:
    """The fake's state and knobs; change them inside a test."""

    def __init__(
        self,
        clients: Mapping[str, tuple[str, Sequence[str]]] | None = None,
        accounts: Mapping[str, str] | None = None,
    ) -> None:
        self.clients: dict[str, tuple[str, list[str]]] = {
            client_id: (secret, list(uris)) for client_id, (secret, uris) in (clients or {}).items()
        }
        self.accounts: dict[str, str] = dict(accounts or {"alice@example.test": "100000000000000000001"})
        self.login_as: str = next(iter(self.accounts))  # the account the next consent uses
        self.grant_scopes: set[str] | None = None  # the scopes the user ticks (identity scopes are always granted)
        self.deny_next = False  # the next consent ends with error=access_denied
        self.issue_refresh_token = True
        self.token_failures: list[int] = []  # statuses the next token calls answer (400 invalid_grant, 401, 5xx)
        self.token_gate: asyncio.Event | None = None  # refresh grants wait for it
        self.access_ttl = 3599
        self.union_scopes = True  # include_granted_scopes=true adds the grant's earlier scopes (False: never)
        self.id_token_claims: dict[str, Any] = {}  # overrides of the next id_tokens' claims (None removes one)
        self.calls: list[RecordedCall] = []
        self.grants: dict[tuple[str, str], Grant] = {}
        self.codes: dict[str, _Code] = {}
        self.revocations = 0  # successful POST /revoke calls
        self._key = secrets.token_bytes(32)

    # ---------------- Helpers for tests ----------------

    def add_client(self, client_id: str, secret: str, redirect_uris: Sequence[str]) -> None:
        self.clients[client_id] = (secret, list(redirect_uris))

    def grant(self, account: str, client_id: str = CLIENT_ID) -> Grant | None:
        return self.grants.get((client_id, account))

    def active_grant(self, account: str, client_id: str = CLIENT_ID) -> Grant | None:
        found = self.grant(account, client_id)
        return found if found is not None and not found.revoked else None

    def expire_access_tokens(self) -> None:
        for grant in self.grants.values():
            for token in grant.access_tokens:
                grant.access_tokens[token] = 0.0

    def revoke_account(self, account: str, client_id: str = CLIENT_ID) -> None:
        """As if the user removed the app at myaccount.google.com/permissions."""
        if (found := self.grant(account, client_id)) is not None:
            self._revoke(found)

    def check_bearer(self, header: str | None, scope_any: set[str]) -> Grant:
        """For the Gmail routes: the grant of a valid access token that has one of `scope_any`. 401 when the token is
        missing, unknown, expired or revoked; 403 insufficientPermissions without the scope."""
        scheme, _, token = (header or "").partition(" ")
        found = self._grant_of_access(token) if scheme.lower() == "bearer" and token else None
        if found is None or found[0].revoked or found[1] < time.time():
            raise GoogleError(
                401,
                {
                    "error": {
                        "code": 401,
                        "message": "Request had invalid authentication credentials.",
                        "errors": [{"reason": "authError", "domain": "global", "message": "Invalid Credentials"}],
                        "status": "UNAUTHENTICATED",
                    }
                },
            )
        grant = found[0]
        if not {normalize_scope(scope) for scope in scope_any} & set(grant.scopes):
            raise GoogleError(
                403,
                {
                    "error": {
                        "code": 403,
                        "message": "Request had insufficient authentication scopes.",
                        "errors": [
                            {
                                "reason": "insufficientPermissions",
                                "domain": "global",
                                "message": "Insufficient Permission",
                            }
                        ],
                        "status": "PERMISSION_DENIED",
                        "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}],
                    }
                },
            )
        return grant

    # ---------------- Internals ----------------

    def _grant_of_access(self, token: str) -> tuple[Grant, float] | None:
        for grant in self.grants.values():
            if token in grant.access_tokens:
                return grant, grant.access_tokens[token]
        return None

    def _grant_of_refresh(self, token: str) -> Grant | None:
        return next((grant for grant in self.grants.values() if token in grant.refresh_tokens), None)

    def _revoke(self, grant: Grant) -> None:
        grant.revoked = True
        grant.refresh_tokens.clear()
        grant.access_tokens.clear()
        grant.scopes = []

    def _access_token(self, grant: Grant) -> str:
        token = "ya29.fake-" + secrets.token_urlsafe(24)
        grant.access_tokens[token] = time.time() + self.access_ttl
        return token

    def _id_token(self, grant: Grant, client_id: str) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": client_id,
            "sub": grant.subject,
            "email": grant.account,
            "email_verified": True,
            "iat": now,
            "exp": now + 3600,
        }
        for key, value in self.id_token_claims.items():
            if value is None:
                claims.pop(key, None)
            else:
                claims[key] = value
        return jwt.encode(claims, self._key, algorithm="HS256")


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _bad_request(message: str) -> HTMLResponse:
    return HTMLResponse(f"<html><body><h1>400. That's an error.</h1><p>{escape(message)}</p></body></html>", 400)


def _oauth_error(status: int, error: str, description: str = "") -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status)


class _Recorder(BaseHTTPMiddleware):
    def __init__(self, app: Any, state: FakeGoogle) -> None:
        super().__init__(app)
        self.state = state

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        call = RecordedCall(request.method, request.url.path, sorted(request.query_params.keys()))
        self.state.calls.append(call)
        request.state.recorded = call
        return await call_next(request)


def fake_google_app(state: FakeGoogle, extra: Sequence[APIRouter] = ()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(_Recorder, state=state)

    @app.exception_handler(GoogleError)
    async def google_error(_: Request, exc: GoogleError) -> JSONResponse:
        return JSONResponse(exc.body, status_code=exc.status)

    @app.get("/o/oauth2/v2/auth")
    async def authorize(request: Request) -> Response:
        params = request.query_params
        client = state.clients.get(params.get("client_id", ""))
        redirect_uri = params.get("redirect_uri", "")
        if client is None:
            return _bad_request("Error 401: invalid_client. The OAuth client was not found.")
        if redirect_uri not in client[1]:
            return _bad_request("Error 400: redirect_uri_mismatch.")
        problems = [
            name
            for name, ok in (
                ("response_type", params.get("response_type") == "code"),
                ("code_challenge_method", params.get("code_challenge_method") == "S256"),
                ("code_challenge", bool(params.get("code_challenge"))),
                ("state", bool(params.get("state"))),
                ("access_type", params.get("access_type") == "offline"),
                ("prompt", params.get("prompt") == "consent"),
            )
            if not ok
        ]
        requested = [normalize_scope(scope) for scope in params.get("scope", "").split()]
        if not set(IDENTITY_SCOPES) <= set(requested):
            problems.append("scope")
        if problems:
            return _bad_request(f"Error 400: invalid_request. Bad or missing: {', '.join(problems)}.")
        target = redirect_uri + ("&" if "?" in redirect_uri else "?")
        if state.deny_next:
            state.deny_next = False
            return RedirectResponse(target + urlencode({"error": "access_denied", "state": params["state"]}), 302)
        allowed = requested if state.grant_scopes is None else [normalize_scope(item) for item in state.grant_scopes]
        ticked = set(IDENTITY_SCOPES) | set(allowed)
        granted = [scope for scope in requested if scope in ticked]
        if params.get("include_granted_scopes") == "true" and state.union_scopes:
            earlier = state.active_grant(state.login_as, params["client_id"])
            if earlier is not None:
                granted = list(dict.fromkeys([*granted, *earlier.scopes]))
        code = "4/fake-" + secrets.token_urlsafe(24)
        state.codes[code] = _Code(
            client_id=params["client_id"],
            redirect_uri=redirect_uri,
            challenge=params["code_challenge"],
            scopes=sorted(granted),
            account=state.login_as,
            expires=time.time() + CODE_SECONDS,
        )
        query = {"state": params["state"], "code": code, "scope": " ".join(sorted(granted))}
        return RedirectResponse(target + urlencode(query), 302)

    @app.post("/token")
    async def token(request: Request) -> Response:
        form = await request.form()
        request.state.recorded.form_keys = sorted(form.keys())
        if state.token_failures:
            status = state.token_failures.pop(0)
            if status == 400:
                return _oauth_error(400, "invalid_grant", "Token has been expired or revoked.")
            if status == 401:
                return _oauth_error(401, "invalid_client", "The OAuth client was not found.")
            return _oauth_error(status, "internal_failure")
        client = state.clients.get(str(form.get("client_id", "")))
        if client is None or client[0] != form.get("client_secret"):
            return _oauth_error(401, "invalid_client", "The OAuth client was not found.")
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            return _exchange(state, form)
        if grant_type == "refresh_token":
            if state.token_gate is not None:
                await state.token_gate.wait()
            found = state._grant_of_refresh(str(form.get("refresh_token", "")))
            if found is None or found.revoked or found.client_id != form.get("client_id"):
                return _oauth_error(400, "invalid_grant", "Token has been expired or revoked.")
            return JSONResponse(
                {
                    "access_token": state._access_token(found),
                    "expires_in": state.access_ttl,
                    "scope": " ".join(found.scopes),
                    "token_type": "Bearer",
                }
            )
        return _oauth_error(400, "unsupported_grant_type")

    @app.post("/revoke")
    async def revoke(request: Request) -> Response:
        form = await request.form()
        request.state.recorded.form_keys = sorted(form.keys())
        token = str(form.get("token") or request.query_params.get("token") or "")
        found = state._grant_of_refresh(token)
        if found is None and (by_access := state._grant_of_access(token)) is not None:
            found = by_access[0]
        if found is None or found.revoked:
            return _oauth_error(400, "invalid_token", "Token expired or revoked")
        state._revoke(found)
        state.revocations += 1
        return JSONResponse({})

    for router in extra:
        app.include_router(router)
    return app


def _exchange(state: FakeGoogle, form: Any) -> Response:
    code = state.codes.get(str(form.get("code", "")))
    if code is None or code.used or code.expires < time.time() or code.client_id != form.get("client_id"):
        return _oauth_error(400, "invalid_grant", "Malformed auth code.")
    code.used = True
    if code.redirect_uri != form.get("redirect_uri"):
        return _oauth_error(400, "redirect_uri_mismatch", "Bad Request")
    verifier = str(form.get("code_verifier", ""))
    if not verifier or _challenge(verifier) != code.challenge:
        return _oauth_error(400, "invalid_grant", "Invalid code verifier.")
    subject = state.accounts[code.account]
    grant = state.grants.get((code.client_id, code.account))
    if grant is None or grant.revoked:
        grant = state.grants[(code.client_id, code.account)] = Grant(code.client_id, code.account, subject)
    grant.scopes = sorted(set(grant.scopes) | set(code.scopes))
    body: dict[str, Any] = {
        "access_token": state._access_token(grant),
        "expires_in": state.access_ttl,
        "scope": " ".join(code.scopes),
        "token_type": "Bearer",
        "id_token": state._id_token(grant, code.client_id),
    }
    if state.issue_refresh_token:
        refresh = "1//fake-" + secrets.token_urlsafe(32)
        grant.refresh_tokens.add(refresh)
        body["refresh_token"] = refresh
    return JSONResponse(body)
