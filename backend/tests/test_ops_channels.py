"""The monitor's alert channels (app.ops.channels): what each sends, and failures that name no URL or credential."""

import functools
import json
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import httpx
import pytest

from app.ops import channels
from app.ops.alerts import Notification
from app.ops.channels import ChannelError, EmailChannel, NtfyChannel, WebhookChannel
from app.ops.settings import AlertSettings

WEBHOOK = "https://hooks.slack.example/services/T0/B0/very-secret-token"
NTFY = "https://ntfy.example/private-topic"


def notification(kind: str = "problem", title: str = "[RAG Assistant] Problem: Redis") -> Notification:
    return Notification(kind, title, ["PROBLEM Redis (critical): Unreachable", "At now."], ["redis"], at=1_790_000_000)  # type: ignore[arg-type]


def mock_http(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(record))
    )
    return sent


async def test_the_webhook_posts_slack_style_text(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = mock_http(monkeypatch, lambda request: httpx.Response(200, text="ok"))

    await WebhookChannel(WEBHOOK).send(notification())

    (request,) = sent
    assert (request.method, str(request.url)) == ("POST", WEBHOOK)
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "text": "[RAG Assistant] Problem: Redis\nPROBLEM Redis (critical): Unreachable\nAt now."
    }


async def test_ntfy_sends_the_title_priority_and_tags_as_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = mock_http(monkeypatch, lambda request: httpx.Response(200, json={"id": "x"}))

    await NtfyChannel(NTFY, token="tk_secret").send(notification(title="[RAG Assistant] Problem: Café"))
    await NtfyChannel(NTFY).send(notification("recovery", "[RAG Assistant] Resolved: Redis"))

    problem, recovery = sent
    assert str(problem.url) == NTFY
    assert problem.content.decode() == "PROBLEM Redis (critical): Unreachable\nAt now."
    assert problem.headers["title"] == "[RAG Assistant] Problem: Caf?"
    assert (problem.headers["priority"], problem.headers["tags"]) == ("high", "rotating_light")
    assert problem.headers["authorization"] == "Bearer tk_secret"
    assert (recovery.headers["priority"], recovery.headers["tags"]) == ("default", "white_check_mark")
    assert "authorization" not in recovery.headers


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (lambda request: httpx.Response(404, text="no such hook"), "HTTP 404"),
        (lambda request: httpx.Response(500), "HTTP 500"),
        (lambda request: (_ for _ in ()).throw(httpx.ConnectError(f"cannot reach {request.url}")), "connection failed"),
        (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout(f"timeout {request.url}")), "timed out"),
    ],
)
async def test_http_failures_name_no_url(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response], reason: str
) -> None:
    mock_http(monkeypatch, handler)

    with pytest.raises(ChannelError) as raised:
        await WebhookChannel(WEBHOOK).send(notification())

    assert str(raised.value) == reason
    assert raised.value.__cause__ is None


class FakeSMTP:
    """Records an SMTP session; `fail` makes the named step raise."""

    instances: list["FakeSMTP"] = []
    fail: dict[str, Exception] = {}

    def __init__(self, host: str, port: int, timeout: float = 0, context: Any = None) -> None:
        self.address = (host, port)
        self.ssl_context = context
        self.steps: list[str] = []
        self.messages: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        self.steps.append("quit")

    def _step(self, name: str) -> None:
        self.steps.append(name)
        if name in FakeSMTP.fail:
            raise FakeSMTP.fail[name]

    def starttls(self, context: Any = None) -> None:
        self._step("starttls")

    def login(self, username: str, password: str) -> None:
        self._step(f"login {username}")

    def send_message(self, message: EmailMessage) -> None:
        self._step("send")
        self.messages.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.fail = {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def email(security: str = "starttls", username: str = "alerts@example.com") -> EmailChannel:
    return EmailChannel(
        "smtp.example.com",
        587,
        "RAG Assistant <alerts@example.com>",
        ["ops@example.com", "owner@example.com"],
        username=username,
        password="smtp-password-secret",
        security=security,
    )


async def test_email_uses_starttls_logs_in_and_sends(smtp: type[FakeSMTP]) -> None:
    await email().send(notification())

    (session,) = smtp.instances
    assert session.address == ("smtp.example.com", 587)
    assert session.steps == ["starttls", "login alerts@example.com", "send", "quit"]
    (message,) = session.messages
    assert message["Subject"] == "[RAG Assistant] Problem: Redis"
    assert message["From"] == "RAG Assistant <alerts@example.com>"
    assert message["To"] == "ops@example.com, owner@example.com"
    assert message["Date"] == "Mon, 21 Sep 2026 14:13:20 +0000"
    assert message.get_content() == "PROBLEM Redis (critical): Unreachable\nAt now.\n"


async def test_email_over_ssl_or_plain_without_login(smtp: type[FakeSMTP]) -> None:
    await email("ssl").send(notification())
    await email("none", username="").send(notification())

    with_ssl, plain = smtp.instances
    assert with_ssl.ssl_context is not None and "starttls" not in with_ssl.steps
    assert plain.steps == ["send", "quit"]


@pytest.mark.parametrize(
    ("step", "error", "reason"),
    [
        (
            "login alerts@example.com",
            smtplib.SMTPAuthenticationError(535, b"bad smtp-password-secret"),
            "SMTP login refused",
        ),
        ("send", smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"no")}), "recipients refused"),
        ("starttls", smtplib.SMTPNotSupportedError("STARTTLS not supported"), "SMTP error (SMTPNotSupportedError)"),
        ("send", ConnectionRefusedError("refused smtp.example.com"), "connection refused"),
    ],
)
async def test_email_failures_name_no_credential(
    smtp: type[FakeSMTP], step: str, error: Exception, reason: str
) -> None:
    smtp.fail = {step: error}

    with pytest.raises(ChannelError) as raised:
        await email().send(notification())

    assert str(raised.value) == reason


def test_configured_channels_follow_the_settings() -> None:
    assert channels.configured(AlertSettings()) == []

    configured = channels.configured(
        AlertSettings(
            alert_webhook_url=WEBHOOK,
            alert_ntfy_url=NTFY,
            alert_ntfy_token="tk",
            alert_smtp_host="smtp.example.com",
            alert_smtp_from="alerts@example.com",
            alert_smtp_to=" ops@example.com , ,owner@example.com",
            alert_smtp_security="ssl",
            alert_smtp_port=465,
        )
    )
    assert [channel.name for channel in configured] == ["webhook", "ntfy", "email"]
    mail = configured[2]
    assert isinstance(mail, EmailChannel)
    assert (mail.recipients, mail.security, mail.port) == (["ops@example.com", "owner@example.com"], "ssl", 465)

    # Email needs a host, a sender and at least one recipient.
    assert channels.configured(AlertSettings(alert_smtp_host="smtp.example.com", alert_smtp_from="a@example.com")) == []
