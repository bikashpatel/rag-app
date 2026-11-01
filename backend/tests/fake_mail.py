"""An in-memory mail sink for the worker's SMTP transport (docs/briefs/signup-auth.md 6.8): conftest's `fake_mail`
replaces app.services.mail.transport.send_message with FakeMail.send, which records each EmailMessage, or raises the
transport's MailError when told to. Helpers read the link and token of the last mail to an address, and svix_sign
builds the headers of a signed Resend webhook."""

import base64
import hashlib
import hmac
import re
import secrets
import time
from email.message import EmailMessage
from typing import Literal

FailureKind = Literal["temporary", "permanent", "auth"]
_LINK = re.compile(r"https?://[^\s\"'<>]+#t=([A-Za-z0-9_-]+)")


class FakeMail:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.failures: list[tuple[FailureKind, str]] = []
        self.attempts = 0

    def fail_next(self, kind: FailureKind, reason: str = "fake failure", times: int = 1) -> None:
        """The next `times` sends raise MailError(kind, reason)."""
        self.failures += [(kind, reason)] * times

    def send(self, message: EmailMessage, smtp: object) -> None:
        self.attempts += 1
        if self.failures:
            from app.services.mail.transport import MailError

            kind, reason = self.failures.pop(0)
            raise MailError(kind, reason)
        self.sent.append(message)

    def to(self, address: str) -> list[EmailMessage]:
        """The mails sent to `address` (compared lowercased), oldest first."""
        return [message for message in self.sent if str(message["To"]).lower() == address.lower()]

    def last(self, address: str) -> EmailMessage:
        found = self.to(address)
        assert found, f"no mail to {address} (sent: {len(self.sent)})"
        return found[-1]

    def last_link(self, address: str) -> str:
        """The token link (…#t=<token>) in the text part of the last mail to `address`."""
        found = _LINK.search(text(self.last(address)))
        assert found, "the last mail holds no token link"
        return found.group(0)

    def last_token(self, address: str) -> str:
        return self.last_link(address).rsplit("#t=", 1)[1]


def text(message: EmailMessage) -> str:
    """The plain-text part (the first text/plain one)."""
    part = message.get_body(preferencelist=("plain",))
    assert part is not None, "no text/plain part"
    return str(part.get_content())


def html(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("html",))
    assert part is not None, "no text/html part"
    return str(part.get_content())


def svix_sign(
    secret: str, body: bytes, *, msg_id: str | None = None, timestamp: int | None = None
) -> dict[str, str]:
    """The svix-id, svix-timestamp and svix-signature headers Resend sends with `body`, signed with a `whsec_…`
    secret (HMAC-SHA256 of "<id>.<timestamp>.<body>" with the base64-decoded key)."""
    msg_id = msg_id or f"msg_{secrets.token_hex(12)}"
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    digest = hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    return {"svix-id": msg_id, "svix-timestamp": stamp, "svix-signature": f"v1,{base64.b64encode(digest).decode()}"}
