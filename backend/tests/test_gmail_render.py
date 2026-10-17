"""The Markdown an imported thread becomes (brief 11, 19.2): deterministic bytes, file names, caps, and large bodies
fetched by attachmentId."""

import asyncio
from datetime import datetime, timezone

from fake_gmail import default_mailbox
from gmail_support import ACCOUNT, gmail  # noqa: F401

from app.db import SessionLocal
from app.models import Document, User
from app.plugins.gmail import importer
from app.plugins.gmail.mime import ParsedThread, parse_message
from app.plugins.gmail.render import MAX_MESSAGE_CHARS, file_name, render_thread
from app.services import storage


def _thread(box, *names: str, total: int | None = None) -> ParsedThread:
    messages = [parse_message(box.resource(box.named[name], "full")) for name in names]
    return ParsedThread(messages[0].thread_id, messages, total or len(messages))


def test_thread_markdown_is_deterministic_and_complete() -> None:
    box = default_mailbox()
    thread = _thread(box, "thread3_1", "thread3_2", "thread3_3")
    name, data = render_thread(thread, ACCOUNT)
    assert render_thread(thread, ACCOUNT) == (name, data)  # byte-identical: re-imports link the earlier document
    text = data.decode("utf-8")
    first, last = thread.messages[0].date, thread.messages[-1].date
    assert name == f"Project kickoff (Gmail {last:%Y-%m-%d}).md"
    lines = text.split("\n")
    assert lines[0] == "# Project kickoff"
    assert lines[2] == f"Email conversation imported from Gmail ({ACCOUNT}), 3 messages, {first:%Y-%m-%d}."
    assert f"## 1. Bob <bob@company.com> · {first:%Y-%m-%d %H:%M} UTC" in lines
    assert "- From: Bob <bob@company.com>" in lines
    assert "- To: Alice <alice@example.test>" in lines
    assert "- Subject: Re: Project kickoff" in lines  # differs from the thread's subject
    assert "- Cc: Carol <carol@example.com>" in lines and text.count("- Cc:") == 1
    assert "Kickoff is on Monday at 10." in text and "Monday works for me." in text
    assert text.endswith("Great, see you then. Agenda attached later.\n")
    assert "\r" not in text


def test_attachments_line_and_span_of_dates() -> None:
    box = default_mailbox()
    _, data = render_thread(_thread(box, "attachments"), ACCOUNT)
    assert "- Attachments: report.pdf (1 KB), notes.docx (" in data.decode()
    thread = _thread(box, "thread150_1", "thread150_150", total=150)
    _, data = render_thread(thread, ACCOUNT)
    first, last = thread.messages[0].date, thread.messages[-1].date
    assert first is not None and last is not None and first.date() == last.date()
    assert f"150 messages (the newest 2 are included), {first:%Y-%m-%d}." in data.decode()


def test_file_names() -> None:
    day = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert file_name("Invoice: 42/43 <draft>", day) == "Invoice 42 43 draft (Gmail 2026-10-12).md"
    assert file_name("", day) == "Email (Gmail 2026-10-12).md"
    assert file_name("...", None) == "Email (Gmail).md"
    long_name = file_name("word " * 60, day)
    assert long_name.startswith("word word") and len(long_name.removesuffix(" (Gmail 2026-10-12).md")) <= 120


def test_each_message_is_capped() -> None:
    box = default_mailbox()
    message = parse_message(box.resource(box.named["plain"], "full"))
    huge = message.__class__(**{**message.__dict__, "body": "word " * 100_000})
    _, data = render_thread(ParsedThread("t", [huge], 1), ACCOUNT)
    body = data.decode().split("\n\n")[-1]
    assert len(body.rstrip("\n")) <= MAX_MESSAGE_CHARS and body.rstrip("\n").endswith("…")


async def test_large_bodies_are_fetched_for_an_import(gmail, create_user) -> None:  # noqa: F811
    client = await create_user()
    connection = await gmail.connect(client)
    stored = gmail.message("long_body")
    async with SessionLocal() as db:
        user = await db.get(User, client.user_id)
    items = await importer.import_selection(
        connection,
        user,
        [importer.ImportSelection(stored.thread_id, True)],
        origin="ui",
        deadline=asyncio.get_running_loop().time() + 60,
    )
    assert [item.status for item in items] == ["imported"]
    downloads = [call for call in gmail.mailbox.requests if "/attachments/" in call[1]]
    assert len(downloads) == 1
    async with SessionLocal() as db:
        document = await db.get(Document, items[0].document_id)
        assert document is not None
        text = storage.stored_path(document.stored_name).read_text("utf-8")
    assert "[part too large to read]" not in text
    assert "Paragraph 0:" in text and "Paragraph 400:" in text
