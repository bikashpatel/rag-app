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

Google sign-in (docs/briefs/signup-auth.md 4.5, its own client in its own project) is `FakeGoogleSignin`: OpenID
Connect's authorization-code flow for one client, answered at once for the account the test chose (`account`), with
PKCE S256, the nonce, `max_age` (a fresh `auth_time`), RS256 ID tokens signed with a key published at
`/oauth2/v3/certs`, and the discovery document. `fake_google_app(state, signin=...)` sends the sign-in client's
authorize and token requests to it; backend tests route GOOGLE_SIGNIN_TOKEN_URL to `FakeGoogleSignin.handle` and play
the browser with `consent()`.
"""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jwt.algorithms import RSAAlgorithm
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


def fake_google_app(
    state: FakeGoogle, extra: Sequence[APIRouter] = (), signin: "FakeGoogleSignin | None" = None
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(_Recorder, state=state)

    @app.exception_handler(GoogleError)
    async def google_error(_: Request, exc: GoogleError) -> JSONResponse:
        return JSONResponse(exc.body, status_code=exc.status)

    if signin is not None:

        @app.get("/oauth2/v3/certs")
        async def certs() -> Response:
            return JSONResponse(signin.jwks(), headers={"Cache-Control": f"public, max-age={signin.max_age}"})

        @app.get("/.well-known/openid-configuration")
        async def discovery(request: Request) -> JSONResponse:
            return JSONResponse(signin.discovery(str(request.base_url).rstrip("/")))

    @app.get("/o/oauth2/v2/auth")
    async def authorize(request: Request) -> Response:
        params = request.query_params
        if signin is not None and params.get("client_id") == signin.client_id:
            status, target = signin.authorize(dict(params))
            return RedirectResponse(target, 302) if status == 302 else _bad_request(target)
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
        if signin is not None and form.get("client_id") == signin.client_id:
            status, body = signin.token({key: str(value) for key, value in form.items()})
            return JSONResponse(body, status_code=status)
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


# ---------------- Google sign-in (OpenID Connect) ----------------

SIGNIN_CLIENT_ID = "fake-signin-client.apps.googleusercontent.com"
SIGNIN_CLIENT_SECRET = "fake-signin-secret"
SIGNIN_SCOPE = "openid email"


@cache
def _signing_key(kid: str) -> rsa.RSAPrivateKey:
    """One RSA key per kid for the process (generating one takes a while)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class SigninAccount:
    """The Google account the next consent signs in as. `email_verified` may be a bool, "true" or None (left out);
    `auth_time` None: now when the request asked for max_age, else left out."""

    sub: str = "100000000000000000777"
    email: str = "it-person@gmail.com"
    email_verified: bool | str | None = True
    hd: str | None = None
    auth_time: int | None = None


@dataclass
class _SigninCode:
    redirect_uri: str
    challenge: str
    nonce: str | None
    account: SigninAccount
    fresh: bool  # max_age was asked for: Google signed the user in again
    expires: float
    used: bool = False


class FakeGoogleSignin:
    """Google's OIDC side for one sign-in client: the consent answers at once for `account`, the code is exchanged
    (PKCE S256, the exact redirect_uri) for an RS256 ID token with the request's nonce. `mint` signs the claims (by
    default with this fake's key, published by `jwks()`; backend tests pass their own JWKS fake's minter)."""

    def __init__(
        self,
        client_id: str = SIGNIN_CLIENT_ID,
        client_secret: str = SIGNIN_CLIENT_SECRET,
        redirect_uris: Sequence[str] | None = None,
        mint: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uris = list(redirect_uris) if redirect_uris is not None else None  # None: any
        self.account = SigninAccount()
        self.deny_next = False  # the next consent ends with error=access_denied
        self.token_failures: list[int] = []  # statuses the next token calls answer
        self.claims: dict[str, Any] = {}  # overrides of the next ID tokens' claims (None removes one)
        self.authorize_requests: list[dict[str, str]] = []
        self.token_requests: list[dict[str, str]] = []
        self.codes: dict[str, _SigninCode] = {}
        self.kid = "fake-signin-1"
        self.max_age = 3600  # the JWKS answer's Cache-Control
        self.mint = mint or self._sign  # replace it to sign otherwise

    def choose(self, **fields: Any) -> None:
        """The account the next consents sign in as (fields of SigninAccount; the rest as before)."""
        self.account = replace(self.account, **fields)

    def _sign(self, claims: dict[str, Any]) -> str:
        present = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(present, _signing_key(self.kid), algorithm="RS256", headers={"kid": self.kid})

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(RSAAlgorithm.to_jwk(_signing_key(self.kid).public_key()))
        return {"keys": [{**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def discovery(self, base: str) -> dict[str, Any]:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{base}/o/oauth2/v2/auth",
            "token_endpoint": f"{base}/token",
            "jwks_uri": f"{base}/oauth2/v3/certs",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
            "code_challenge_methods_supported": ["plain", "S256"],
            "claims_supported": ["aud", "email", "email_verified", "exp", "iat", "iss", "sub", "nonce", "hd"],
        }

    def authorize(self, params: Mapping[str, str]) -> tuple[int, str]:
        """(302, where Google sends the browser back) or (400, Google's error page text)."""
        self.authorize_requests.append(dict(params))
        redirect_uri = params.get("redirect_uri", "")
        if self.redirect_uris is not None and redirect_uri not in self.redirect_uris:
            return 400, "Error 400: redirect_uri_mismatch."
        problems = [
            name
            for name, ok in (
                ("response_type", params.get("response_type") == "code"),
                ("scope", params.get("scope", "").split()[:1] == ["openid"]),
                ("code_challenge_method", params.get("code_challenge_method") == "S256"),
                ("code_challenge", bool(params.get("code_challenge"))),
                ("state", bool(params.get("state"))),
                ("nonce", bool(params.get("nonce"))),
            )
            if not ok
        ]
        if problems:
            return 400, f"Error 400: invalid_request. Bad or missing: {', '.join(problems)}."
        target = redirect_uri + ("&" if "?" in redirect_uri else "?")
        if self.deny_next:
            self.deny_next = False
            return 302, target + urlencode({"error": "access_denied", "state": params["state"]})
        code = "4/fake-signin-" + secrets.token_urlsafe(24)
        self.codes[code] = _SigninCode(
            redirect_uri=redirect_uri,
            challenge=params["code_challenge"],
            nonce=params.get("nonce"),
            account=replace(self.account),
            fresh="max_age" in params,
            expires=time.time() + CODE_SECONDS,
        )
        return 302, target + urlencode({"state": params["state"], "code": code, "scope": SIGNIN_SCOPE})

    def id_token(self, code: _SigninCode) -> str:
        account, now = code.account, int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": self.client_id,
            "azp": self.client_id,
            "sub": account.sub,
            "email": account.email,
            "email_verified": account.email_verified,
            "hd": account.hd,
            "iat": now,
            "exp": now + 3600,
            "nonce": code.nonce,
            "auth_time": account.auth_time if account.auth_time is not None else (now if code.fresh else None),
            **self.claims,
        }
        return self.mint(claims)  # a None value is a claim left out

    def token(self, form: Mapping[str, str]) -> tuple[int, dict[str, Any]]:
        self.token_requests.append(dict(form))
        if self.token_failures:
            status = self.token_failures.pop(0)
            return status, {"error": "invalid_grant" if status == 400 else "internal_failure"}
        if form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret:
            return 401, {"error": "invalid_client", "error_description": "The OAuth client was not found."}
        if form.get("grant_type") != "authorization_code":
            return 400, {"error": "unsupported_grant_type"}
        code = self.codes.get(form.get("code", ""))
        if code is None or code.used or code.expires < time.time():
            return 400, {"error": "invalid_grant", "error_description": "Malformed auth code."}
        code.used = True
        if code.redirect_uri != form.get("redirect_uri"):
            return 400, {"error": "redirect_uri_mismatch", "error_description": "Bad Request"}
        verifier = form.get("code_verifier", "")
        if not verifier or _challenge(verifier) != code.challenge:
            return 400, {"error": "invalid_grant", "error_description": "Invalid code verifier."}
        return 200, {
            "access_token": "ya29.fake-signin-" + secrets.token_urlsafe(24),
            "expires_in": 3599,
            "scope": "openid https://www.googleapis.com/auth/userinfo.email",
            "token_type": "Bearer",
            "id_token": self.id_token(code),
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        """For an httpx transport route (backend tests): the token endpoint, or the JWKS for any other path."""
        if request.method == "POST":
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            status, body = self.token(form)
            return httpx.Response(status, json=body, request=request)
        headers = {"Cache-Control": f"public, max-age={self.max_age}"}
        return httpx.Response(200, json=self.jwks(), headers=headers, request=request)

    def consent(self, authorize_url: str) -> str:
        """Plays the browser at Google: the URL of the redirect back to the app (the callback, with its query)."""
        query = {key: values[0] for key, values in parse_qs(urlsplit(authorize_url).query).items()}
        status, target = self.authorize(query)
        if status != 302:
            raise AssertionError(target)
        return target
