"""Alert channels of the monitor (SPEC.md section 10): a JSON webhook, an ntfy topic and SMTP email. Each is optional;
`send` raises ChannelError with a short reason that never holds the channel's URL, address or credentials."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

import httpx

from app.ops.alerts import Notification
from app.ops.checks import describe_error
from app.ops.settings import AlertChannelName, AlertSettings

SEND_TIMEOUT_SECONDS = 15.0
_NTFY_PRIORITY = {"problem": "high", "reminder": "default", "recovery": "default", "test": "default"}
_NTFY_TAGS = {"problem": "rotating_light", "reminder": "warning", "recovery": "white_check_mark", "test": "bell"}


class ChannelError(Exception):
    pass


class Channel(Protocol):
    name: AlertChannelName

    async def send(self, notification: Notification) -> None: ...


def _ascii(text: str) -> str:
    """HTTP header values must be Latin-1; titles are ASCII by construction, this keeps them so."""
    return text.encode("ascii", "replace").decode("ascii")


async def _post(request: httpx.Request) -> None:
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.send(request)
    except Exception as exc:
        raise ChannelError(describe_error(exc)) from None
    if not 200 <= response.status_code < 300:
        raise ChannelError(f"HTTP {response.status_code}")


class WebhookChannel:
    """POSTs {"text": title + body}: Slack and Google Chat incoming webhooks, Discord's /slack endpoint, Teams."""

    name: AlertChannelName = "webhook"

    def __init__(self, url: str) -> None:
        self.url = url

    async def send(self, notification: Notification) -> None:
        body = {"text": f"{notification.title}\n{notification.text}"}
        await _post(httpx.Request("POST", self.url, json=body))


class NtfyChannel:
    """POSTs the body to an ntfy topic URL, with the title, priority and tags as headers."""

    name: AlertChannelName = "ntfy"

    def __init__(self, url: str, token: str = "") -> None:
        self.url = url
        self.token = token

    async def send(self, notification: Notification) -> None:
        headers = {
            "Title": _ascii(notification.title),
            "Priority": _NTFY_PRIORITY[notification.kind],
            "Tags": _NTFY_TAGS[notification.kind],
            "Content-Type": "text/plain; charset=utf-8",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        await _post(httpx.Request("POST", self.url, content=notification.text.encode("utf-8"), headers=headers))


class EmailChannel:
    name: AlertChannelName = "email"

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipients: list[str],
        *,
        username: str = "",
        password: str = "",
        security: str = "starttls",
    ) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.recipients = recipients
        self.username = username
        self.password = password
        self.security = security

    def message(self, notification: Notification) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = notification.title
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Date"] = formatdate(notification.at, usegmt=True)
        message["Message-ID"] = make_msgid(domain="rag-assistant.monitor")
        message.set_content(notification.text + "\n")
        return message

    def _send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        client: smtplib.SMTP
        if self.security == "ssl":
            client = smtplib.SMTP_SSL(self.host, self.port, timeout=SEND_TIMEOUT_SECONDS, context=context)
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=SEND_TIMEOUT_SECONDS)
        with client:
            if self.security == "starttls":
                client.starttls(context=context)
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)

    async def send(self, notification: Notification) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(self._send, self.message(notification)), SEND_TIMEOUT_SECONDS * 2)
        except smtplib.SMTPAuthenticationError:
            raise ChannelError("SMTP login refused") from None
        except smtplib.SMTPRecipientsRefused:
            raise ChannelError("recipients refused") from None
        except smtplib.SMTPException as exc:
            raise ChannelError(f"SMTP error ({type(exc).__name__})") from None
        except Exception as exc:
            raise ChannelError(describe_error(exc)) from None


def configured(alert_settings: AlertSettings) -> list[Channel]:
    """The channels the settings configure, in a fixed order."""
    channels: list[Channel] = []
    if alert_settings.alert_webhook_url:
        channels.append(WebhookChannel(alert_settings.alert_webhook_url))
    if alert_settings.alert_ntfy_url:
        channels.append(NtfyChannel(alert_settings.alert_ntfy_url, alert_settings.alert_ntfy_token))
    recipients = [address.strip() for address in alert_settings.alert_smtp_to.split(",") if address.strip()]
    if alert_settings.alert_smtp_host and alert_settings.alert_smtp_from and recipients:
        channels.append(
            EmailChannel(
                alert_settings.alert_smtp_host,
                alert_settings.alert_smtp_port,
                alert_settings.alert_smtp_from,
                recipients,
                username=alert_settings.alert_smtp_username,
                password=alert_settings.alert_smtp_password,
                security=alert_settings.alert_smtp_security,
            )
        )
    return channels
