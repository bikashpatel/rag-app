"""A fake SMTP server with an HTTP side for the integration stack (docs/briefs/signup-auth.md 6.8), standard library
only.

SMTP (port 2525, no TLS, so the stack runs MAIL_SMTP_SECURITY=none): EHLO/HELO, AUTH PLAIN and LOGIN against fixed test
credentials, MAIL, RCPT, DATA, RSET, NOOP and QUIT. Each accepted message is parsed and kept in memory; a message whose
Resend-Idempotency-Key was seen before is accepted but not kept again (as Resend drops it).

HTTP (port 8025):
- GET /health
- GET /_mail?to=<address>: the messages (to that address, compared lowercased), oldest first: envelope, headers,
  subject, text, html and attachments (file name, type, size).
- DELETE /_mail: forget every message (and the idempotency keys seen).
- GET /_control and POST /_control {fail_code: 451|550, fail_count: N, refuse_logins: bool, drop_after_data: N,
  hold_data: bool, reset: bool}: the next N recipients refused with the code; every login refused; the connection
  dropped after the next N messages' data (the message is kept: it arrived, only the answer was lost); every message's
  answer held after its data until hold_data is false again (the conversation hangs; the message is then kept, and
  answered if the client is still there). Answers the state and the counters.
- POST /_control/event {type, to, bounce_type?}: a Resend event (`email.bounced` or `email.complained`, `to` one address
  or a list) signed the Svix way with FAKE_MAIL_WEBHOOK_SECRET and posted to FAKE_MAIL_WEBHOOK_URL; answers the
  backend's status.

Settings (environment): FAKE_MAIL_USERNAME and FAKE_MAIL_PASSWORD (the credentials the stack's MAIL_SMTP_USERNAME and
MAIL_SMTP_PASSWORD must match; defaults below), FAKE_MAIL_WEBHOOK_URL (default http://frontend/api/mail/resend-events),
FAKE_MAIL_WEBHOOK_SECRET (the stack's MAIL_WEBHOOK_SECRET; /_control/event answers 503 without it)."""

import argparse
import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any
from urllib.parse import parse_qs, urlsplit

USERNAME = os.environ.get("FAKE_MAIL_USERNAME", "it-mailer")
PASSWORD = os.environ.get("FAKE_MAIL_PASSWORD", "it-mailer-password")
WEBHOOK_URL = os.environ.get("FAKE_MAIL_WEBHOOK_URL", "http://frontend/api/mail/resend-events")
WEBHOOK_SECRET = os.environ.get("FAKE_MAIL_WEBHOOK_SECRET", "")
IDEMPOTENCY_HEADER = "Resend-Idempotency-Key"
MAX_LINE = 64 * 1024
MAX_MESSAGE = 30 * 1024 * 1024


@dataclass
class State:
    messages: list[dict[str, Any]] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)
    fail_code: int = 0
    fail_count: int = 0
    refuse_logins: bool = False
    drop_after_data: int = 0
    hold_data: bool = False
    counters: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            (
                "connections",
                "logins",
                "refused_logins",
                "refused_recipients",
                "accepted",
                "duplicates",
                "dropped",
                "held",
            ),
            0,
        )
    )

    def control(self) -> dict[str, Any]:
        return {
            "fail_code": self.fail_code,
            "fail_count": self.fail_count,
            "refuse_logins": self.refuse_logins,
            "drop_after_data": self.drop_after_data,
            "hold_data": self.hold_data,
            "counters": dict(self.counters),
        }


STATE = State()


# ---------------- SMTP ----------------


def _summary(raw: bytes, sender: str, recipients: list[str]) -> dict[str, Any]:
    message: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)  # type: ignore[assignment]
    text = message.get_body(preferencelist=("plain",))
    html = message.get_body(preferencelist=("html",))
    return {
        "id": len(STATE.messages) + 1,
        "received_at": time.time(),
        "envelope": {"from": sender, "to": recipients},
        "headers": {name: str(value) for name, value in message.items()},
        "subject": str(message.get("Subject", "")),
        "to": [str(value) for value in message.get_all("To", [])],
        "text": text.get_content().replace("\r\n", "\n") if text is not None else None,
        "html": html.get_content().replace("\r\n", "\n") if html is not None else None,
        "attachments": [
            {
                "filename": part.get_filename(),
                "content_type": part.get_content_type(),
                "size": len(part.get_payload(decode=True) or b""),
            }
            for part in message.iter_attachments()
        ],
        "size": len(raw),
    }


def _address(argument: str) -> str:
    """The address in "FROM:<a@b> SIZE=1" or "TO:<a@b>"."""
    _, _, rest = argument.partition(":")
    value = rest.strip().split(" ", 1)[0]
    return value.strip("<>")


class Session:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.authenticated = False
        self.sender: str | None = None
        self.recipients: list[str] = []

    async def reply(self, line: str) -> None:
        self.writer.write(line.encode() + b"\r\n")
        await self.writer.drain()

    async def line(self) -> str | None:
        raw = await self.reader.readline()
        if not raw or len(raw) > MAX_LINE:
            return None
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    async def run(self) -> None:
        STATE.counters["connections"] += 1
        await self.reply("220 fake-mail ESMTP ready")
        while (command := await self.line()) is not None:
            verb, _, argument = command.partition(" ")
            if not await self.handle(verb.upper(), argument):
                break

    async def handle(self, verb: str, argument: str) -> bool:
        """False ends the session."""
        if verb in ("EHLO", "HELO"):
            await self.reply("250-fake-mail" if verb == "EHLO" else "250 fake-mail")
            if verb == "EHLO":
                await self.reply("250-AUTH PLAIN LOGIN")
                await self.reply("250-8BITMIME")
                await self.reply(f"250 SIZE {MAX_MESSAGE}")
        elif verb == "AUTH":
            await self.auth(argument)
        elif verb == "MAIL":
            if not self.authenticated:
                await self.reply("530 5.7.0 Authentication required")
            else:
                self.sender, self.recipients = _address(argument), []
                await self.reply("250 2.1.0 Ok")
        elif verb == "RCPT":
            await self.rcpt(argument)
        elif verb == "DATA":
            return await self.data()
        elif verb == "RSET":
            self.sender, self.recipients = None, []
            await self.reply("250 2.0.0 Ok")
        elif verb == "NOOP":
            await self.reply("250 2.0.0 Ok")
        elif verb == "QUIT":
            await self.reply("221 2.0.0 Bye")
            return False
        else:
            await self.reply("502 5.5.2 Command not implemented")
        return True

    async def auth(self, argument: str) -> None:
        mechanism, _, initial = argument.partition(" ")
        mechanism = mechanism.upper()
        try:
            if mechanism == "PLAIN":
                if not initial:
                    await self.reply("334 ")
                    initial = await self.line() or ""
                _, user, password = base64.b64decode(initial).decode().split("\0", 2)
            elif mechanism == "LOGIN":
                await self.reply("334 " + base64.b64encode(b"Username:").decode())
                user = base64.b64decode(await self.line() or "").decode()
                await self.reply("334 " + base64.b64encode(b"Password:").decode())
                password = base64.b64decode(await self.line() or "").decode()
            else:
                await self.reply("504 5.5.4 Unrecognized authentication type")
                return
        except (ValueError, binascii.Error):
            await self.reply("501 5.5.2 Cannot decode the credentials")
            return
        if STATE.refuse_logins or not (hmac.compare_digest(user, USERNAME) and hmac.compare_digest(password, PASSWORD)):
            STATE.counters["refused_logins"] += 1
            await self.reply("535 5.7.8 Authentication credentials invalid")
            return
        STATE.counters["logins"] += 1
        self.authenticated = True
        await self.reply("235 2.7.0 Authentication successful")

    async def rcpt(self, argument: str) -> None:
        if self.sender is None:
            await self.reply("503 5.5.1 Need MAIL first")
            return
        if STATE.fail_count > 0 and STATE.fail_code:
            STATE.fail_count -= 1
            STATE.counters["refused_recipients"] += 1
            if STATE.fail_code >= 500:
                await self.reply(f"{STATE.fail_code} 5.1.1 The mailbox doesn't exist")
            else:
                await self.reply(f"{STATE.fail_code} 4.3.0 Try again later")
            return
        self.recipients.append(_address(argument))
        await self.reply("250 2.1.5 Ok")

    async def data(self) -> bool:
        if not self.recipients:
            await self.reply("503 5.5.1 Need RCPT first")
            return True
        await self.reply("354 End data with <CR><LF>.<CR><LF>")
        lines: list[bytes] = []
        size = 0
        while True:
            raw = await self.reader.readline()
            if not raw:
                return False
            if raw in (b".\r\n", b".\n"):
                break
            size += len(raw)
            if size > MAX_MESSAGE:
                await self.reply("552 5.3.4 Message too big")
                return False
            lines.append(raw[1:] if raw.startswith(b"..") else raw)
        if STATE.hold_data:
            STATE.counters["held"] += 1
            while STATE.hold_data:
                await asyncio.sleep(0.1)
        summary = _summary(b"".join(lines), self.sender or "", self.recipients)
        key = summary["headers"].get(IDEMPOTENCY_HEADER)
        if key and key in STATE.keys:
            STATE.counters["duplicates"] += 1
        else:
            if key:
                STATE.keys.add(key)
            STATE.messages.append(summary)
            STATE.counters["accepted"] += 1
        self.sender, self.recipients = None, []
        if STATE.drop_after_data > 0:
            STATE.drop_after_data -= 1
            STATE.counters["dropped"] += 1
            return False
        await self.reply(f"250 2.0.0 Ok: queued as {summary['id']}")
        return True


async def smtp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await Session(reader, writer).run()
    except (ConnectionError, asyncio.IncompleteReadError, ValueError):
        pass
    finally:
        writer.close()


# ---------------- HTTP ----------------


def signed_event(kind: str, to: list[str], bounce_type: str) -> tuple[bytes, dict[str, str]]:
    data: dict[str, Any] = {"email_id": secrets.token_hex(8), "from": "no-reply@example.test", "to": to}
    if kind == "email.bounced":
        data["bounce"] = {"message": "The mailbox doesn't exist", "subType": "General", "type": bounce_type}
    body = json.dumps({"type": kind, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data}).encode()
    msg_id, stamp = f"msg_{secrets.token_hex(12)}", str(int(time.time()))
    key = base64.b64decode(WEBHOOK_SECRET.removeprefix("whsec_"))
    signature = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    headers = {
        "Content-Type": "application/json",
        "svix-id": msg_id,
        "svix-timestamp": stamp,
        "svix-signature": f"v1,{signature}",
    }
    return body, headers


def post_event(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if not WEBHOOK_SECRET:
        return 503, {"detail": "FAKE_MAIL_WEBHOOK_SECRET isn't set"}
    kind = str(body.get("type", "email.bounced"))
    to = body.get("to")
    recipients = [to] if isinstance(to, str) else [str(item) for item in to or []]
    payload, headers = signed_event(kind, recipients, str(body.get("bounce_type", "Permanent")))
    request = urllib.request.Request(WEBHOOK_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200, {"status": response.status}
    except urllib.error.HTTPError as error:
        return 200, {"status": error.code}
    except OSError as error:
        return 502, {"detail": f"the webhook couldn't be reached ({type(error).__name__})"}


def apply_control(body: dict[str, Any]) -> None:
    if body.get("reset"):
        STATE.fail_code = STATE.fail_count = STATE.drop_after_data = 0
        STATE.refuse_logins = STATE.hold_data = False
    if "fail_code" in body:
        STATE.fail_code = int(body["fail_code"])
        STATE.fail_count = int(body.get("fail_count", 1))
    if "refuse_logins" in body:
        STATE.refuse_logins = bool(body["refuse_logins"])
    if "drop_after_data" in body:
        STATE.drop_after_data = int(body["drop_after_data"])
    if "hold_data" in body:
        STATE.hold_data = bool(body["hold_data"])


async def route(method: str, target: str, body: bytes) -> tuple[int, Any]:
    parts = urlsplit(target)
    path, query = parts.path, parse_qs(parts.query)
    if method == "GET" and path == "/health":
        return 200, {"ok": True}
    if path == "/_mail" and method == "GET":
        wanted = query.get("to", [""])[0].lower()
        found = [
            message
            for message in STATE.messages
            if not wanted or wanted in [address.lower() for address in message["envelope"]["to"]]
        ]
        return 200, {"messages": found}
    if path == "/_mail" and method == "DELETE":
        STATE.messages.clear()
        STATE.keys.clear()
        return 200, {"ok": True}
    if path == "/_control" and method in ("GET", "POST"):
        if method == "POST":
            apply_control(json.loads(body or b"{}"))
        return 200, STATE.control()
    if path == "/_control/event" and method == "POST":
        return await asyncio.to_thread(post_event, json.loads(body or b"{}"))
    return 404, {"detail": "Not found"}


async def http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request_line = (await reader.readline()).decode("latin-1").strip()
        method, target, _ = (request_line.split(" ", 2) + ["", ""])[:3]
        length = 0
        while (header := (await reader.readline()).decode("latin-1").strip()) != "":
            name, _, value = header.partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip() or 0)
        body = await reader.readexactly(length) if length else b""
        try:
            status, payload = await route(method.upper(), target, body)
        except (ValueError, TypeError) as error:
            status, payload = 400, {"detail": f"bad request ({type(error).__name__})"}
        data = json.dumps(payload).encode()
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 502: "Bad Gateway", 503: "Unavailable"}
        writer.write(
            f"HTTP/1.1 {status} {reason.get(status, 'OK')}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
            + data
        )
        await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ValueError):
        pass
    finally:
        writer.close()


async def main(host: str, smtp_port: int, http_port: int) -> None:
    smtp = await asyncio.start_server(smtp_client, host, smtp_port)
    http = await asyncio.start_server(http_client, host, http_port)
    print(f"fake mail: SMTP on {host}:{smtp_port}, HTTP on {host}:{http_port}", flush=True)
    async with smtp, http:
        await asyncio.gather(smtp.serve_forever(), http.serve_forever())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--smtp-port", type=int, default=2525)
    parser.add_argument("--http-port", type=int, default=8025)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.host, arguments.smtp_port, arguments.http_port))
