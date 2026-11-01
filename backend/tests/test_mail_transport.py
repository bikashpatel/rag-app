"""The SMTP transport shared by the mailer and the alert email channel (app.services.mail.transport;
docs/briefs/signup-auth.md 6.2): implicit TLS, STARTTLS or none, 15 s per operation, and failures turned into the
kinds the worker acts on (temporary, permanent, auth; a refused recipient is a bounce) with short reasons that never
hold the server's text, an address or a credential."""

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import pytest

from app.config import settings
from app.services.mail import transport
from app.services.mail.transport import MailError, SmtpConfig

SERVER_TEXT = b"5.1.1 <someone@example.test> no such mailbox, password hunter2"


class FakeSMTP:
    """Records an SMTP session; `fail` makes the named step ("starttls", "login <user>", "send") raise."""

    instances: list["FakeSMTP"] = []
    fail: dict[str, BaseException] = {}

    def __init__(self, host: str, port: int, timeout: float = 0, context: Any = None) -> None:
        self.address = (host, port)
        self.timeout = timeout
        self.ssl_context = context
        self.steps: list[str] = []
        self.messages: list[EmailMessage] = []
        FakeSMTP.instances.append(self)
        if "connect" in FakeSMTP.fail:
            raise FakeSMTP.fail["connect"]

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


def install_fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.fail = {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    return install_fake_smtp(monkeypatch)


def config(security: transport.Security = "ssl", username: str = "resend") -> SmtpConfig:
    return SmtpConfig("smtp.example.test", 465, security, username, "re_secret_api_key")


def message() -> EmailMessage:
    mail = EmailMessage()
    mail["To"] = "someone@example.test"
    mail.set_content("Hello")
    return mail


def test_implicit_tls_logs_in_and_sends(smtp: type[FakeSMTP]) -> None:
    transport.send_message(message(), config("ssl"))

    (session,) = smtp.instances
    assert session.address == ("smtp.example.test", 465)
    assert isinstance(session.ssl_context, ssl.SSLContext)
    assert session.timeout == transport.TIMEOUT_SECONDS == 15
    assert session.steps == ["login resend", "send", "quit"]
    assert [mail["To"] for mail in session.messages] == ["someone@example.test"]


def test_starttls_and_plain_without_a_login(smtp: type[FakeSMTP]) -> None:
    transport.send_message(message(), config("starttls"))
    transport.send_message(message(), config("none", username=""))

    starttls, plain = smtp.instances
    assert starttls.ssl_context is None and starttls.steps == ["starttls", "login resend", "send", "quit"]
    assert plain.steps == ["send", "quit"]


def test_the_password_never_shows_in_the_config() -> None:
    assert "re_secret_api_key" not in repr(config())


def test_the_mailers_server_comes_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "mail_smtp_host": "smtp.resend.com",
        "mail_smtp_port": 2465,
        "mail_smtp_security": "ssl",
        "mail_smtp_username": "resend",
        "mail_smtp_password": "re_key",
    }.items():
        monkeypatch.setattr(settings, name, value)

    assert transport.mail_config() == SmtpConfig("smtp.resend.com", 2465, "ssl", "resend", "re_key")


@pytest.mark.parametrize(
    ("step", "error", "kind", "reason", "bounce"),
    [
        ("login resend", smtplib.SMTPAuthenticationError(535, SERVER_TEXT), "auth", "SMTP login refused", False),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"someone@example.test": (550, SERVER_TEXT)}),
            "permanent",
            "recipient refused (550)",
            True,
        ),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"someone@example.test": (553, SERVER_TEXT)}),
            "permanent",
            "recipient refused (553)",
            True,
        ),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"someone@example.test": (554, SERVER_TEXT)}),
            "permanent",
            "recipient refused (554)",
            False,
        ),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"someone@example.test": (451, SERVER_TEXT)}),
            "temporary",
            "recipient deferred (451)",
            False,
        ),
        (
            "send",
            smtplib.SMTPSenderRefused(553, SERVER_TEXT, "no-reply@example.test"),
            "permanent",
            "sender refused (553)",
            False,
        ),
        ("send", smtplib.SMTPDataError(552, SERVER_TEXT), "permanent", "message refused (552)", False),
        ("send", smtplib.SMTPDataError(421, SERVER_TEXT), "temporary", "message deferred (421)", False),
        ("send", smtplib.SMTPResponseException(500, SERVER_TEXT), "permanent", "SMTP command refused (500)", False),
        (
            "connect",
            smtplib.SMTPConnectError(554, SERVER_TEXT),
            "temporary",
            "SMTP server refused the connection (554)",
            False,
        ),
        ("send", smtplib.SMTPServerDisconnected("smtp.example.test closed"), "temporary", "connection failed", False),
        (
            "starttls",
            smtplib.SMTPNotSupportedError("STARTTLS not supported"),
            "temporary",
            "SMTP error (SMTPNotSupportedError)",
            False,
        ),
        ("connect", ConnectionRefusedError("refused smtp.example.test"), "temporary", "connection refused", False),
        ("connect", TimeoutError("smtp.example.test timed out"), "temporary", "timed out", False),
        ("send", OSError("network unreachable"), "temporary", "connection failed", False),
    ],
)
def test_failures_have_a_kind_and_a_reason_without_the_servers_text(
    smtp: type[FakeSMTP], step: str, error: BaseException, kind: str, reason: str, bounce: bool
) -> None:
    smtp.fail = {step: error}

    with pytest.raises(MailError) as raised:
        transport.send_message(message(), config("starttls"))

    assert (raised.value.kind, raised.value.reason, raised.value.bounce) == (kind, reason, bounce)
    assert str(raised.value) == reason
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    for secret in ("someone@example.test", "hunter2", "re_secret_api_key", "smtp.example.test"):
        assert secret not in reason
