"""SMTP delivery (docs/briefs/signup-auth.md 6.2), shared by the mailer and the monitor's email alert channel: stdlib
smtplib, `ssl` (implicit TLS), `starttls` or `none`, 15 s per operation. Failures become MailError with a kind the
worker acts on and a short reason that never holds the server's text, an address or a credential."""

import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Literal

from app.config import settings

MailErrorKind = Literal["temporary", "permanent", "auth"]
Security = Literal["ssl", "starttls", "none"]
TIMEOUT_SECONDS = 15.0
_BOUNCE_CODES = range(550, 554)  # a recipient refusal that counts as a bounce


class MailError(Exception):
    """`temporary`: retry later (connection failures, timeouts, 4xx); `permanent`: this message can't go (5xx for the
    recipient or the message); `auth`: the server refused our login (535 and the like). `bounce`: the server refused the
    recipient with 550 to 553."""

    def __init__(self, kind: MailErrorKind, reason: str, *, bounce: bool = False) -> None:
        super().__init__(reason)
        self.kind: MailErrorKind = kind
        self.reason = reason
        self.bounce = bounce


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: Security
    username: str = ""
    password: str = field(default="", repr=False)
    timeout: float = TIMEOUT_SECONDS


def mail_config() -> SmtpConfig:
    """The mailer's server (MAIL_SMTP_*)."""
    return SmtpConfig(
        host=settings.mail_smtp_host,
        port=settings.mail_smtp_port,
        security=settings.mail_smtp_security,
        username=settings.mail_smtp_username,
        password=settings.mail_smtp_password,
    )


def _by_code(code: int, what: str, *, recipient: bool = False) -> MailError:
    if 400 <= code < 500:
        return MailError("temporary", f"{what} deferred ({code})")
    return MailError("permanent", f"{what} refused ({code})", bounce=recipient and code in _BOUNCE_CODES)


def _connection(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    return "connection failed"


def _deliver(message: EmailMessage, smtp: SmtpConfig) -> None:
    context = ssl.create_default_context()
    client: smtplib.SMTP
    if smtp.security == "ssl":
        client = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=smtp.timeout, context=context)
    else:
        client = smtplib.SMTP(smtp.host, smtp.port, timeout=smtp.timeout)
    with client:
        if smtp.security == "starttls":
            client.starttls(context=context)
        if smtp.username:
            client.login(smtp.username, smtp.password)
        client.send_message(message)


def send_message(message: EmailMessage, smtp: SmtpConfig) -> None:
    """Sends one message (blocking: run it in a thread). Raises MailError."""
    try:
        _deliver(message, smtp)
    except smtplib.SMTPAuthenticationError:
        raise MailError("auth", "SMTP login refused") from None
    except smtplib.SMTPRecipientsRefused as exc:
        codes = [code for code, _ in exc.recipients.values()]
        raise _by_code(min(codes, default=550), "recipient", recipient=True) from None
    except smtplib.SMTPSenderRefused as exc:
        raise _by_code(exc.smtp_code, "sender") from None
    except smtplib.SMTPDataError as exc:
        raise _by_code(exc.smtp_code, "message") from None
    except (smtplib.SMTPConnectError, smtplib.SMTPHeloError) as exc:
        raise MailError("temporary", f"SMTP server refused the connection ({exc.smtp_code})") from None
    except smtplib.SMTPResponseException as exc:
        raise _by_code(exc.smtp_code, "SMTP command") from None
    except smtplib.SMTPServerDisconnected as exc:
        raise MailError("temporary", _connection(exc)) from None
    except smtplib.SMTPException as exc:
        raise MailError("temporary", f"SMTP error ({type(exc).__name__})") from None
    except OSError as exc:
        raise MailError("temporary", _connection(exc)) from None
