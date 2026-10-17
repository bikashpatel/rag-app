"""An email conversation rendered to the Markdown document an import stores (docs/briefs/plugins-gmail.md, section 11).

Deterministic (no import time in the bytes), so re-importing an unchanged thread gives identical bytes and links the
earlier document.
"""

import re
from datetime import datetime

from app.plugins.gmail.mime import ParsedMessage, ParsedThread, capped, clean_line, format_addresses

MAX_MESSAGE_CHARS = 200_000
_MAX_NAME_CHARS = 120
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def render_thread(thread: ParsedThread, account_email: str) -> tuple[str, bytes]:
    """(file name, Markdown bytes) of a thread whose messages are oldest first."""
    messages = thread.messages
    subject = next((message.subject for message in messages if message.subject), "")
    dates = [message.date for message in messages if message.date is not None]
    count = f"{thread.total} message{'s' if thread.total != 1 else ''}"
    if thread.total > len(messages):
        count += f" (the newest {len(messages)} are included)"
    span = ""
    if dates:
        first, last = _day(dates[0]), _day(dates[-1])
        span = f", {first}" if first == last else f", {first} to {last}"
    lines = [
        f"# {subject or '(no subject)'}",
        "",
        f"Email conversation imported from Gmail ({account_email}), {count}{span}.",
    ]
    for number, message in enumerate(messages, start=1):
        lines.extend(["", *_message(number, message, subject)])
    text = "\n".join(lines) + "\n"
    return file_name(subject, dates[-1] if dates else None), text.encode("utf-8")


def _message(number: int, message: ParsedMessage, thread_subject: str) -> list[str]:
    sender = message.sender_text or "(unknown sender)"
    when = f" · {message.date:%Y-%m-%d %H:%M} UTC" if message.date else ""
    lines = [f"## {number}. {sender}{when}", "", f"- From: {sender}"]
    if message.to:
        lines.append(f"- To: {format_addresses(message.to)}")
    if message.cc:
        lines.append(f"- Cc: {format_addresses(message.cc)}")
    if message.subject and message.subject != thread_subject:
        lines.append(f"- Subject: {message.subject}")
    if message.attachments:
        listed = ", ".join(f"{item.filename or '(no name)'} ({_size(item.size_bytes)})" for item in message.attachments)
        lines.append(f"- Attachments: {listed}")
    body, _ = capped(message.body, MAX_MESSAGE_CHARS)
    if body:
        lines.extend(["", body])
    return lines


def file_name(subject: str, last: datetime | None) -> str:
    """`<subject> (Gmail <date of the last message>).md`, the subject made safe for a file name."""
    stem = " ".join(_UNSAFE_NAME.sub(" ", clean_line(subject)).split())[:_MAX_NAME_CHARS].strip(" .") or "Email"
    return f"{stem} (Gmail {_day(last)}).md" if last else f"{stem} (Gmail).md"


def _day(value: datetime) -> str:
    return f"{value:%Y-%m-%d}"


def _size(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{max(1, round(size_bytes / 1024))} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"
