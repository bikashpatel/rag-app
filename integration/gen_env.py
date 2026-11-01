"""Write integration/.run/it.env: the only environment the test stack gets (see compose.it.yml).

Random SECRET_KEY, ADMIN_PASSWORD, POSTGRES_PASSWORD and TOKEN_ENCRYPTION_KEY on every run; fake provider keys, so
nothing can be billed; Google (the Gmail plugin and Google sign-in) and Razorpay pointed at the fakes (random Razorpay
test keys and webhook secrets); user mail to the fake SMTP server (a random login and webhook secret), Turnstile and the
breached-password check to their fake (Cloudflare's always-passing test keys); backups in integration/.run/backups;
alerts to the fake receiver. Ports come from IT_APP_PORT (18090), IT_FAKE_LLM_PORT (18091), IT_FAKE_GOOGLE_PORT
(18092), IT_FAKE_RAZORPAY_PORT (18093), IT_FAKE_MAIL_PORT (18094) and IT_FAKE_TURNSTILE_PORT (18095). The file is
git-ignored and readable only by its owner.
"""

import base64
import os
import secrets
import socket
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".run" / "it.env"
FAKE_OPENAI_KEY = "sk-it-fake"
FAKE_ANTHROPIC_KEY = "sk-ant-it-fake"


def _port(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if not 1024 <= value <= 65535 or value == 8080:
        sys.exit(f"{name}={value}: use a free port above 1023 other than 8080 (the live app)")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", value))
        except OSError:
            sys.exit(f"Port {value} on 127.0.0.1 is in use: set {name} to a free port")
    return value


def main() -> None:
    app_port = _port("IT_APP_PORT", 18090)
    llm_port = _port("IT_FAKE_LLM_PORT", 18091)
    google_port = _port("IT_FAKE_GOOGLE_PORT", 18092)
    razorpay_port = _port("IT_FAKE_RAZORPAY_PORT", 18093)
    mail_port = _port("IT_FAKE_MAIL_PORT", 18094)
    turnstile_port = _port("IT_FAKE_TURNSTILE_PORT", 18095)
    ports = (app_port, llm_port, google_port, razorpay_port, mail_port, turnstile_port)
    if len(set(ports)) != len(ports):
        sys.exit(
            "IT_APP_PORT, IT_FAKE_LLM_PORT, IT_FAKE_GOOGLE_PORT, IT_FAKE_RAZORPAY_PORT, IT_FAKE_MAIL_PORT and "
            "IT_FAKE_TURNSTILE_PORT must differ"
        )
    values = {
        "IT_STACK": "rag-it",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ADMIN_USERNAME": "it-admin",
        "ADMIN_PASSWORD": secrets.token_urlsafe(18),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(18),
        "OPENAI_API_KEY": FAKE_OPENAI_KEY,
        "ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY,
        "APP_PORT": str(app_port),
        "IT_FAKE_LLM_PORT": str(llm_port),
        "IT_FAKE_GOOGLE_PORT": str(google_port),
        "IT_FAKE_RAZORPAY_PORT": str(razorpay_port),
        "IT_FAKE_MAIL_PORT": str(mail_port),
        "IT_FAKE_TURNSTILE_PORT": str(turnstile_port),
        "WORKER_MEM_LIMIT": "2g",
        "PUBLIC_BASE_URLS": f"http://127.0.0.1:{app_port}",
        # nginx takes CF-Connecting-IP from any client (the ports are published on 127.0.0.1 only), so the sign-up tests
        # can come from several networks; without the header every request keeps Docker's address
        "NGINX_REAL_IP_FROM": "0.0.0.0/0,::/0",
        "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "GOOGLE_OAUTH_CLIENT_ID": "fake-client-id.apps.googleusercontent.com",
        "GOOGLE_OAUTH_CLIENT_SECRET": "fake-client-secret",
        "GOOGLE_OAUTH_AUTHORIZE_URL": f"http://127.0.0.1:{google_port}/o/oauth2/v2/auth",
        "GOOGLE_OAUTH_TOKEN_URL": "http://fake-google:8099/token",
        "GOOGLE_OAUTH_REVOKE_URL": "http://fake-google:8099/revoke",
        "GMAIL_API_BASE_URL": "http://fake-google:8099",
        # Google sign-in (its own client) against the fake's OpenID Connect side; the consent page is browser-facing
        "GOOGLE_SIGNIN_CLIENT_ID": "fake-signin-client.apps.googleusercontent.com",
        "GOOGLE_SIGNIN_CLIENT_SECRET": "fake-signin-secret",
        "GOOGLE_SIGNIN_AUTHORIZE_URL": f"http://127.0.0.1:{google_port}/o/oauth2/v2/auth",
        "GOOGLE_SIGNIN_TOKEN_URL": "http://fake-google:8099/token",
        "GOOGLE_SIGNIN_JWKS_URL": "http://fake-google:8099/oauth2/v3/certs",
        # User mail to the fake SMTP server (no TLS on the internal network), its bounce webhook signed with this secret
        "MAIL_SMTP_HOST": "fake-mail",
        "MAIL_SMTP_PORT": "2525",
        "MAIL_SMTP_SECURITY": "none",
        "MAIL_SMTP_USERNAME": "it-mailer",
        "MAIL_SMTP_PASSWORD": secrets.token_urlsafe(18),
        "MAIL_FROM": "AskNuvexusDoc <no-reply@it.example.test>",
        "MAIL_REPLY_TO": "support@it.example.test",
        "MAIL_WEBHOOK_SECRET": "whsec_" + base64.b64encode(os.urandom(24)).decode(),
        # The suite's sign-ups alone send more than the default day's `public` share (40% of 80)
        "MAIL_DAILY_LIMIT": "400",
        # Cloudflare's always-passing test keys (the fake checks the secret) and the breached-password check's fake
        "TURNSTILE_SITE_KEY": "1x00000000000000000000AA",
        "TURNSTILE_SECRET_KEY": "1x0000000000000000000000000000000AA",
        "TURNSTILE_SITEVERIFY_URL": "http://fake-turnstile:8000/turnstile/v0/siteverify",
        "PASSWORD_BREACH_CHECK_URL": "http://fake-turnstile:8000/range/",
        # Payments in test mode against the fake Razorpay, its webhooks signed with these secrets, a fast reconciler
        "RAZORPAY_KEY_ID": f"rzp_test_it{secrets.token_hex(6)}",
        "RAZORPAY_KEY_SECRET": secrets.token_urlsafe(18),
        "RAZORPAY_WEBHOOK_SECRET": secrets.token_urlsafe(18),
        "RAZORPAY_WEBHOOK_SECRET_PREVIOUS": secrets.token_urlsafe(18),
        "RAZORPAY_API_BASE": "http://fake-razorpay:8000",
        "BILLING_RECONCILE_SECONDS": "2",
        # Backups into a git-ignored folder of the test run, and a fast monitor alerting the fake receiver. The start-up
        # backup comes long after the monitor would confirm a problem (2 checks 2 s apart), so the suite shows that
        # waiting for it alerts nothing.
        "BACKUP_DIR": "./integration/.run/backups",
        "BACKUP_START_DELAY_SECONDS": "20",
        "APP_VERSION": "it-run",
        "MONITOR_INTERVAL_SECONDS": "2",
        "ALERT_CONFIRM_CHECKS": "2",
        "ALERT_REPEAT_HOURS": "0",
        "ALERT_WEBHOOK_URL": "http://fake-alerts:8000/webhook",
        "ALERT_NTFY_URL": "http://fake-alerts:8000/ntfy/rag-it-alerts",
        "WORKER_HEARTBEAT_SECONDS": "1",
        "WORKER_HEARTBEAT_MAX_AGE_SECONDS": "6",
    }
    ENV_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    header = "# Generated by integration/gen_env.py for the rag-it test stack. Throwaway values, fake keys.\n"
    body = "".join(f"{name}={value}\n" for name, value in values.items())
    descriptor = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(header + body)
    os.chmod(ENV_FILE, 0o600)


if __name__ == "__main__":
    main()
