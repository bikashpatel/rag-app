"""A fake Cloudflare Turnstile siteverify for app.services.auth.turnstile (conftest's `fake_turnstile` routes
TURNSTILE_SITEVERIFY_URL's host here and configures keys).

Tokens describe their answer: `turnstile_token(action)` ("pass:<action>:<random>") succeeds for that action and the
`hostname` (by default PUBLIC_BASE_URLS's first host); "fail:..." fails with invalid-input-response; any token seen
before answers timeout-or-duplicate, as Cloudflare does. `token(...)` registers any other answer. `outage` makes the
next `outage_count` requests time out, answer 500 or internal-error."""

import secrets
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import httpx

from app.config import settings

TEST_SITE_KEY = "1x00000000000000000000AA"  # Cloudflare's always-passing test keys: action and hostname aren't checked
TEST_SECRET = "1x0000000000000000000000000000000AA"
REAL_SITE_KEY = "0x4AAAAAAAfakeSiteKey"  # keys of the production form: action and hostname are checked
REAL_SECRET = "0x4AAAAAAAfakeSecretKey"


def turnstile_token(action: str = "signup") -> str:
    """A fresh token the fake accepts for `action`."""
    return f"pass:{action}:{secrets.token_hex(4)}"


class FakeTurnstile:
    def __init__(self) -> None:
        self.answers: dict[str, dict[str, Any]] = {}
        self.seen: set[str] = set()
        self.hostname: str | None = None  # None: PUBLIC_BASE_URLS's first host
        self.secret: str | None = None  # the secret Cloudflare knows; None: TURNSTILE_SECRET_KEY
        self.outage: Literal["timeout", "500", "internal-error"] | None = None
        self.outage_count = 1_000_000
        self.requests: list[dict[str, str]] = []  # the form of each request

    def token(
        self,
        action: str = "signup",
        *,
        success: bool = True,
        hostname: str | None = None,
        codes: tuple[str, ...] = (),
    ) -> str:
        token = f"registered:{secrets.token_hex(6)}"
        self.answers[token] = {"success": success, "action": action, "hostname": hostname, "error-codes": list(codes)}
        return token

    def _host(self) -> str:
        if self.hostname is not None:
            return self.hostname
        return urlsplit(settings.public_base_urls.split(",")[0].strip()).hostname or "localhost"

    def _answer(self, token: str) -> dict[str, Any]:
        if token in self.seen:
            return {"success": False, "error-codes": ["timeout-or-duplicate"]}
        self.seen.add(token)
        if token in self.answers:
            answer = dict(self.answers[token])
            answer["hostname"] = answer["hostname"] or self._host()
            return answer
        kind, _, rest = token.partition(":")
        if kind == "pass":
            return {"success": True, "action": rest.partition(":")[0], "hostname": self._host(), "error-codes": []}
        return {"success": False, "error-codes": ["invalid-input-response"]}

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.requests.append(form)
        if self.outage is not None and self.outage_count > 0:
            self.outage_count -= 1
            if self.outage == "timeout":
                raise httpx.ReadTimeout("fake timeout", request=request)
            if self.outage == "500":
                return httpx.Response(500, text="fake outage", request=request)
            return httpx.Response(200, json={"success": False, "error-codes": ["internal-error"]}, request=request)
        if form.get("secret") != (self.secret or settings.turnstile_secret_key):
            answer: dict[str, Any] = {"success": False, "error-codes": ["invalid-input-secret"]}
        else:
            answer = self._answer(form.get("response", ""))
        return httpx.Response(200, json=answer, request=request)
