"""A fake Cloudflare Turnstile siteverify and a fake Have I Been Pwned range API for the integration stack
(docs/briefs/signup-auth.md 7.5, 15.2), run with the backend image. Never the real services.

    python /fake_turnstile/server.py --host 0.0.0.0 --port 8000

The stack's TURNSTILE_SITEVERIFY_URL is http://fake-turnstile:8000/turnstile/v0/siteverify and its
PASSWORD_BREACH_CHECK_URL http://fake-turnstile:8000/range/; FAKE_TURNSTILE_SECRET is the stack's TURNSTILE_SECRET_KEY.

- `POST /turnstile/v0/siteverify` (form: secret, response, remoteip, idempotency_key): a token starting `pass-`
  succeeds, `fail-` fails with invalid-input-response, anything else too; a token seen before answers
  timeout-or-duplicate, as Cloudflare does (a retry under the same idempotency key gets the first answer again). The
  answer carries `hostname` example.com and the action named by a `pass-<action>-…` token, as Cloudflare's test
  secret does. A wrong secret answers invalid-input-secret.
- `GET /range/<5 hex>`: Have I Been Pwned's padded answer; nothing is breached until `POST /_control/breached`.
- `POST /_control {outage: N, mode: "500"|"internal-error"|"timeout", reset: bool}`: the next N siteverify calls fail
  that way. `POST /_control/breached {password}`: that password appears in breaches.
- `GET /_state`: the siteverify calls answered (by outcome) and the range requests (prefixes only).
"""

import argparse
import asyncio
import hashlib
import os
import secrets
from collections import Counter
from typing import Any

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

SECRET = os.environ.get("FAKE_TURNSTILE_SECRET", "")
ACTIONS = ("signup", "resend", "reset", "login")
PADDING = 3
TIMEOUT_SECONDS = 12  # beyond the backend's 10 s


class Control(BaseModel):
    outage: int = 0
    mode: str = "500"
    reset: bool = False


class Breached(BaseModel):
    password: str


class State:
    def __init__(self) -> None:
        self.seen: dict[str, dict[str, Any]] = {}  # token -> the first answer
        self.answers_by_key: dict[str, dict[str, Any]] = {}  # idempotency key -> its answer
        self.outage = 0
        self.mode = "500"
        self.breached: dict[str, int] = {}  # SHA-1 (upper hex) -> count
        self.outcomes: Counter[str] = Counter()
        self.ranges: list[str] = []


def _answer(state: State, token: str) -> dict[str, Any]:
    if token in state.seen:
        return {"success": False, "error-codes": ["timeout-or-duplicate"]}
    if token.startswith("pass-"):
        action = token.removeprefix("pass-").split("-", 1)[0]
        answer = {
            "success": True,
            "challenge_ts": "2026-11-01T00:00:00.000Z",
            "hostname": "example.com",
            "action": action if action in ACTIONS else "",
            "cdata": "",
            "error-codes": [],
        }
    else:
        answer = {"success": False, "error-codes": ["invalid-input-response"]}
    state.seen[token] = answer
    return answer


def build_app(state: State | None = None) -> FastAPI:
    state = state or State()
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/turnstile/v0/siteverify")
    async def siteverify(
        secret: str = Form(""), response: str = Form(""), idempotency_key: str = Form("")
    ) -> JSONResponse:
        if state.outage > 0:
            state.outage -= 1
            state.outcomes[f"outage-{state.mode}"] += 1
            if state.mode == "timeout":
                await asyncio.sleep(TIMEOUT_SECONDS)
            if state.mode == "internal-error":
                return JSONResponse({"success": False, "error-codes": ["internal-error"]})
            return JSONResponse({"error": "fake outage"}, status_code=500)
        if not SECRET or secret != SECRET:
            state.outcomes["bad-secret"] += 1
            return JSONResponse({"success": False, "error-codes": ["invalid-input-secret"]})
        if idempotency_key and idempotency_key in state.answers_by_key:
            return JSONResponse(state.answers_by_key[idempotency_key])
        answer = _answer(state, response)
        if idempotency_key:
            state.answers_by_key[idempotency_key] = answer
        state.outcomes["pass" if answer["success"] else "fail"] += 1
        return JSONResponse(answer)

    @app.get("/range/{prefix}")
    async def hibp_range(prefix: str, request: Request) -> PlainTextResponse:
        prefix = prefix.upper()
        state.ranges.append(prefix)
        lines = [f"{sha1[5:]}:{count}" for sha1, count in state.breached.items() if sha1.startswith(prefix)]
        if request.headers.get("add-padding", "").lower() == "true":
            lines += [f"{secrets.token_hex(18)[:35].upper()}:0" for _ in range(PADDING)]
        return PlainTextResponse("\r\n".join(lines))

    @app.post("/_control")
    async def control(body: Control) -> dict[str, Any]:
        if body.reset:
            state.outage, state.mode = 0, "500"
            state.breached.clear()
        if body.outage:
            state.outage, state.mode = body.outage, body.mode
        return {"outage": state.outage, "mode": state.mode}

    @app.post("/_control/breached")
    async def breached(body: Breached) -> dict[str, bool]:
        sha1 = hashlib.sha1(body.password.encode(), usedforsecurity=False).hexdigest().upper()
        state.breached[sha1] = 42
        return {"ok": True}

    @app.get("/_state")
    async def state_view() -> dict[str, Any]:
        return {"outcomes": dict(state.outcomes), "ranges": list(state.ranges), "outage": state.outage}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="A fake Turnstile siteverify and HIBP range API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(build_app(), host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
