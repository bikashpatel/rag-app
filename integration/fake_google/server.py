"""The backend tests' fake Google (OAuth and Gmail: backend/tests/fake_google.py and fake_gmail.py, mounted read-only at
/fake_google_src) with a state route for the integration suite. Never a real Google.

    python /fake_google/server.py --host 0.0.0.0 --port 8099 \\
        --redirect-uri http://127.0.0.1:18090/api/plugins/gmail/oauth/callback \\
        --signin-redirect-uri http://127.0.0.1:18090/api/auth/google/callback

GET /_state: the drafts created and the emails sent through the fake (recipients, subject, the start of the body), the
number of revocations and of active grants, so tests can check what reached "Gmail" and that nothing was sent before
the user confirmed; and Google sign-in's authorize and token requests (their parameter names and the PKCE, nonce and
max_age values' presence, never a code).

Google sign-in (its own client, fake_google.SIGNIN_CLIENT_ID): the authorize page answers at once for the account
`POST /_control/signin {sub, email, email_verified, hd, auth_time, deny}` chose (fields left out keep their value),
the token endpoint returns an RS256 ID token with the request's nonce, signed with the key at /oauth2/v3/certs, and
/.well-known/openid-configuration describes it.
"""

import argparse
import os
import sys
from email import message_from_bytes, policy
from typing import Any

sys.path.insert(0, os.environ.get("FAKE_GOOGLE_SRC", "/fake_google_src"))

import uvicorn  # noqa: E402
from fake_gmail import FakeMailbox, Recorded, default_mailbox, gmail_router  # noqa: E402
from fake_google import CLIENT_ID, CLIENT_SECRET, FakeGoogle, FakeGoogleSignin, fake_google_app  # noqa: E402
from fake_google_server import ACCOUNT, SUBJECT  # noqa: E402
from fastapi import APIRouter, FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class SigninChoice(BaseModel):
    """The Google account the next sign-in consents use (fields left out keep their value)."""

    sub: str | None = None
    email: str | None = None
    email_verified: bool | str | None = None
    hd: str | None = None
    auth_time: int | None = None
    deny: bool = False
    clear: list[str] = []  # fields to reset to None (hd, auth_time, email_verified)


def _summary(record: Recorded) -> dict[str, Any]:
    message = message_from_bytes(record.raw, policy=policy.default)
    body = message.get_body(preferencelist=("plain",))
    return {
        "id": record.id,
        "thread_id": record.thread_id,
        "to": str(message.get("To", "")),
        "cc": str(message.get("Cc", "")),
        "bcc": str(message.get("Bcc", "")),
        "subject": str(message.get("Subject", "")),
        "body": body.get_content()[:500] if body is not None else "",
    }


def _signin_request(params: dict[str, str]) -> dict[str, Any]:
    """What a test may read of a sign-in request: the parameter names and a few values, never a code or a secret."""
    shown = {"scope", "prompt", "login_hint", "max_age", "code_challenge_method", "response_type", "grant_type"}
    return {"keys": sorted(params), **{key: value for key, value in params.items() if key in shown}}


def state_router(google: FakeGoogle, mailbox: FakeMailbox, signin: FakeGoogleSignin) -> APIRouter:
    router = APIRouter()

    @router.get("/_state")
    async def state() -> dict[str, Any]:
        return {
            "account": ACCOUNT,
            "drafts": [_summary(record) for record in mailbox.drafts],
            "sent": [_summary(record) for record in mailbox.sent],
            "revocations": google.revocations,
            "active_grants": sum(1 for grant in google.grants.values() if not grant.revoked),
            "gmail_requests": len(mailbox.requests),
            "signin": {
                "account": signin.account.__dict__,
                "authorize": [_signin_request(params) for params in signin.authorize_requests],
                "token": [_signin_request(form) for form in signin.token_requests],
            },
        }

    @router.post("/_control/signin")
    async def choose(body: SigninChoice) -> dict[str, Any]:
        fields = body.model_dump(exclude={"deny", "clear"}, exclude_none=True)
        signin.choose(**fields, **dict.fromkeys(body.clear))
        signin.deny_next = body.deny
        return signin.account.__dict__

    return router


def build_app(redirect_uris: list[str], signin_redirect_uris: list[str]) -> FastAPI:
    google = FakeGoogle(clients={CLIENT_ID: (CLIENT_SECRET, redirect_uris)}, accounts={ACCOUNT: SUBJECT})
    mailbox = default_mailbox(account=ACCOUNT)
    signin = FakeGoogleSignin(redirect_uris=signin_redirect_uris)
    extra = [gmail_router(google, mailbox), state_router(google, mailbox, signin)]
    return fake_google_app(google, extra=extra, signin=signin)


def main() -> None:
    parser = argparse.ArgumentParser(description="The fake Google with a state route, for the integration suite.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--redirect-uri", action="append", required=True)
    parser.add_argument("--signin-redirect-uri", action="append", default=[])
    args = parser.parse_args()
    app = build_app(args.redirect_uri, args.signin_redirect_uri)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
