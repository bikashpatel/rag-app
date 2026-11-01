"""A fake Have I Been Pwned range API (GET /range/<first 5 hex of SHA-1>) for app.services.auth.passwords. conftest's
autouse `fake_hibp` routes PASSWORD_BREACH_CHECK_URL's host here, so no test reaches the real service: every password
is unbreached until a test adds it."""

import hashlib
import secrets
from typing import Literal

import httpx

PADDING = 3  # extra suffixes with count 0 in every answer, as Add-Padding does


class FakeHibp:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}  # SHA-1 (upper hex) -> times seen in breaches (0: a padding entry)
        self.failure: Literal["timeout", "500"] | None = None
        self.requests: list[httpx.Request] = []

    def add(self, password: str, count: int = 1) -> None:
        """The password appears in breaches `count` times (0: only as a padding entry)."""
        self.counts[hashlib.sha1(password.encode()).hexdigest().upper()] = count

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure == "timeout":
            raise httpx.ReadTimeout("fake timeout", request=request)
        if self.failure == "500":
            return httpx.Response(500, text="fake outage", request=request)
        prefix = request.url.path.rsplit("/", 1)[-1].upper()
        lines = [f"{sha1[5:]}:{count}" for sha1, count in self.counts.items() if sha1.startswith(prefix)]
        lines += [f"{secrets.token_hex(18)[:35].upper()}:0" for _ in range(PADDING)]
        return httpx.Response(200, text="\r\n".join(lines), request=request)

    def prefixes(self) -> list[str]:
        """The hash prefixes asked for, in order (nothing else about a password ever leaves)."""
        return [request.url.path.rsplit("/", 1)[-1] for request in self.requests]
