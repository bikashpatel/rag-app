"""The backend tests' fake Google (OAuth and Gmail: backend/tests/fake_google.py and fake_gmail.py, mounted read-only at
/fake_google_src) with a state route for the integration suite. Never a real Google.

    python /fake_google/server.py --host 0.0.0.0 --port 8099 \\
        --redirect-uri http://127.0.0.1:18090/api/plugins/gmail/oauth/callback

GET /_state: the drafts created and the emails sent through the fake (recipients, subject, the start of the body), the
number of revocations and of active grants, so tests can check what reached "Gmail" and that nothing was sent before
the user confirmed.
"""

import argparse
import os
import sys
from email import message_from_bytes, policy
from typing import Any

sys.path.insert(0, os.environ.get("FAKE_GOOGLE_SRC", "/fake_google_src"))

import uvicorn  # noqa: E402
from fake_gmail import FakeMailbox, Recorded, default_mailbox, gmail_router  # noqa: E402
from fake_google import CLIENT_ID, CLIENT_SECRET, FakeGoogle, fake_google_app  # noqa: E402
from fake_google_server import ACCOUNT, SUBJECT  # noqa: E402
from fastapi import APIRouter, FastAPI  # noqa: E402


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


def state_router(google: FakeGoogle, mailbox: FakeMailbox) -> APIRouter:
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
        }

    return router


def build_app(redirect_uris: list[str]) -> FastAPI:
    google = FakeGoogle(clients={CLIENT_ID: (CLIENT_SECRET, redirect_uris)}, accounts={ACCOUNT: SUBJECT})
    mailbox = default_mailbox(account=ACCOUNT)
    return fake_google_app(google, extra=[gmail_router(google, mailbox), state_router(google, mailbox)])


def main() -> None:
    parser = argparse.ArgumentParser(description="The fake Google with a state route, for the integration suite.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--redirect-uri", action="append", required=True)
    args = parser.parse_args()
    uvicorn.run(build_app(args.redirect_uri), host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
