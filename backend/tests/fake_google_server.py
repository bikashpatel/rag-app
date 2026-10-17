"""The fake Google (OAuth and Gmail) as a standalone server, for the Docker smoke (docs/briefs/plugins-gmail.md,
section 18.3). Never a real Google: the fixed fake client, one account and the default fake mailbox.

    python tests/fake_google_server.py --host 0.0.0.0 --port 8099 \\
        --redirect-uri http://localhost:8080/api/plugins/gmail/oauth/callback

Point the backend at it with GOOGLE_OAUTH_CLIENT_ID/SECRET = the fake client, GOOGLE_OAUTH_AUTHORIZE_URL =
http://localhost:8099/o/oauth2/v2/auth (the browser's view), and GOOGLE_OAUTH_TOKEN_URL, GOOGLE_OAUTH_REVOKE_URL and
GMAIL_API_BASE_URL on http://host.docker.internal:8099 (the backend container's view).
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # fake_google and fake_gmail sit next to this file

import uvicorn  # noqa: E402
from fake_gmail import default_mailbox, gmail_router  # noqa: E402
from fake_google import CLIENT_ID, CLIENT_SECRET, FakeGoogle, fake_google_app  # noqa: E402
from fastapi import FastAPI  # noqa: E402

ACCOUNT = "smoke@example.test"
SUBJECT = "100000000000000000099"


def build_app(redirect_uris: Sequence[str], account: str = ACCOUNT) -> FastAPI:
    google = FakeGoogle(clients={CLIENT_ID: (CLIENT_SECRET, list(redirect_uris))}, accounts={account: SUBJECT})
    return fake_google_app(google, extra=[gmail_router(google, default_mailbox(account=account))])


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="A fake Google OAuth and Gmail server for smoke tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument(
        "--redirect-uri", action="append", required=True, help="an OAuth redirect URI of the app (repeatable)"
    )
    parser.add_argument("--account", default=ACCOUNT, help="the Google account every consent signs in as")
    args = parser.parse_args(argv)
    uvicorn.run(build_app(args.redirect_uri, args.account), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
