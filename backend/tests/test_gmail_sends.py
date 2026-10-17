"""Pending sends (brief P12, 13, 19.2): only the user's click sends, exactly the previewed content, once, before it
expires, within the daily cap and from the same Google account; edits, cancels, staleness and retention."""

import asyncio
from datetime import timedelta
from email import message_from_bytes, policy
from typing import Any

import pytest
from conftest import chat, md_bytes, upload
from fake_gmail import LONG_SUBJECT
from gmail_support import ACCOUNT, audit_rows, gmail, pending_sends, update_send  # noqa: F401
from sqlalchemy import Text

from app.config import settings
from app.db import SessionLocal
from app.models import Document, GmailPendingSend, MessagePluginItem, User, utcnow
from app.plugins import tasks
from app.plugins.base import ConnectionInfo
from app.plugins.gmail import compose, sends
from app.plugins.gmail.client import GmailClient
from app.plugins.gmail.mime import Address, OutgoingEmail, reply_subject
from app.plugins.gmail.schemas import GmailSendCard
from app.services import storage


async def propose(
    client: Any,
    connection: ConnectionInfo,
    *,
    conversation_id: int | None = None,
    to: list[dict[str, Any]] | None = None,
    bcc: list[dict[str, Any]] | None = None,
    subject: str = "Q3 numbers",
    body: str = "Hello Bob,\nthe numbers look good.\n",
    attachments: list[compose.ResolvedAttachment] | None = None,
    reply: compose.ReplyContext | None = None,
) -> GmailPendingSend:
    to = to or [{"name": "Bob", "address": "bob@company.com", "unmentioned": False}]
    bcc = bcc or []
    email = OutgoingEmail(
        account_email=connection.account_email,
        to=compose.stored_addresses(to),
        cc=[],
        bcc=compose.stored_addresses(bcc),
        subject=subject,
        body=body,
        attachments=[],
        in_reply_to=reply.in_reply_to if reply else None,
        references=reply.references if reply else None,
    )
    async with SessionLocal() as db:
        user = await db.get(User, client.user_id)
        assert user is not None
        return await sends.propose(
            db,
            user,
            connection,
            email,
            conversation_id=conversation_id,
            to=to,
            cc=[],
            bcc=bcc,
            attachments_meta=[item.meta(False) for item in attachments or []],
            thread_id=reply.thread_id if reply else None,
            reply_to=reply.info if reply else None,
            origin="chat",
        )


async def confirm(client: Any, row: GmailPendingSend, digest: str | None = None) -> Any:
    return await client.post(
        f"/api/plugins/gmail/sends/{row.id}/send", json={"content_hash": digest or row.content_hash}
    )


@pytest.fixture
async def alice(gmail, create_user) -> tuple[Any, ConnectionInfo]:  # noqa: F811
    client = await create_user("alice")
    return client, await gmail.connect(client)


async def test_send_happy_path(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    assert gmail.mailbox.sent == []  # proposing never sends
    shown = (await client.get(f"/api/plugins/gmail/sends/{row.id}")).json()
    assert shown["status"] == "pending" and shown["to"] == [
        {"address": "bob@company.com", "name": "Bob", "unicode_domain": None, "unmentioned": False}
    ]
    response = await confirm(client, row)
    assert response.status_code == 200, response.text
    sent = response.json()
    assert sent["status"] == "sent" and sent["sent_at"] and sent["error"] is None
    assert len(gmail.mailbox.sent) == 1
    record = gmail.mailbox.sent[0]
    assert sent["gmail_url"] == f"https://mail.google.com/mail/u/0/#all/{record.thread_id}"
    message = record.message
    assert message["From"] == ACCOUNT and message["To"] == "Bob <bob@company.com>"
    assert message["Subject"] == "Q3 numbers"
    assert message.get_content().replace("\r\n", "\n") == "Hello Bob,\nthe numbers look good.\n"
    assert ("send", "ok", "ui", 0) in await audit_rows(client.user_id)
    again = await confirm(client, row)
    assert again.status_code == 409 and again.json()["detail"] == "This email was already sent."
    assert len(gmail.mailbox.sent) == 1


async def test_changed_hash_and_expiry(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    response = await confirm(client, row, "0" * 64)
    assert response.status_code == 409 and "changed since it was shown" in response.json()["detail"]
    await update_send(row.id, expires_at=utcnow() - timedelta(seconds=1))
    response = await confirm(client, row)
    assert response.status_code == 409 and response.json()["detail"].startswith("This email expired")
    assert (await pending_sends(client.user_id))[0].status == "expired"
    assert gmail.mailbox.sent == []


async def test_concurrent_confirms_send_once(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    first, second = await asyncio.gather(confirm(client, row), confirm(client, row))
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert len(gmail.mailbox.sent) == 1


async def test_cancel(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    response = await client.post(f"/api/plugins/gmail/sends/{row.id}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    again = await client.post(f"/api/plugins/gmail/sends/{row.id}/cancel")
    assert again.json()["status"] == "cancelled"
    assert (await confirm(client, row)).status_code == 409
    assert [entry for entry in await audit_rows(client.user_id) if entry[0] == "send_cancelled"] == [
        ("send_cancelled", "ok", "ui", 0)
    ]


async def test_revise_chain_and_hydration(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    flagged = [
        {"name": None, "address": "x@evil.test", "unmentioned": True},
        {"name": "Bob", "address": "bob@company.com", "unmentioned": False},
    ]
    row = await propose(client, connection, to=flagged)
    revision = {"to": ["x@evil.test", "Bob <bob@company.com>", "carol@example.com"], "subject": "Q3, edited",
                "body": "Edited body"}
    response = await client.post(f"/api/plugins/gmail/sends/{row.id}/revise", json=revision)
    assert response.status_code == 200, response.text
    new = response.json()
    assert new["id"] != row.id and new["status"] == "pending" and new["subject"] == "Q3, edited"
    assert [(item["address"], item["unmentioned"]) for item in new["to"]] == [
        ("x@evil.test", True), ("bob@company.com", False), ("carol@example.com", False)
    ]  # a flag stays only where the old email had it
    old = (await client.get(f"/api/plugins/gmail/sends/{row.id}")).json()
    assert old["status"] == "replaced" and old["replaced_by"] == new["id"]
    second = await client.post(f"/api/plugins/gmail/sends/{new['id']}/revise", json={**revision, "body": "Third"})
    assert second.status_code == 200
    item = MessagePluginItem(
        id=41, message_id=1, plugin_id="gmail", card_type="gmail_send",
        data=GmailSendCard(card_id="g1", status="proposed", pending=sends.view(row, utcnow())).model_dump(mode="json"),
    )
    async with SessionLocal() as db:
        user = await db.get(User, client.user_id)
        assert user is not None
        [card] = await gmail.plugin.hydrate_cards(db, user, [item])
    assert isinstance(card, GmailSendCard) and card.card_id == "p41"
    assert card.pending is not None and card.pending.id == second.json()["id"] and card.pending.body == "Third"
    assert ("send_proposed", "ok", "ui", 0) in await audit_rows(client.user_id)
    assert (await confirm(client, row)).status_code == 409


async def test_revise_is_refused_after_sending_and_validates(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    bad = await client.post(f"/api/plugins/gmail/sends/{row.id}/revise", json={"to": ["not an address"], "body": "x"})
    assert bad.status_code == 422 and "Not a valid email address" in bad.json()["detail"]
    assert (await confirm(client, row)).status_code == 200
    late = await client.post(f"/api/plugins/gmail/sends/{row.id}/revise", json={"to": ["a@b.co"], "body": "x"})
    assert late.status_code == 409


async def test_daily_cap(gmail, alice, admin_client, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(settings, "gmail_daily_send_limit", 1)
    client, connection = alice
    first, second = await propose(client, connection), await propose(client, connection)
    assert (await confirm(client, first)).status_code == 200
    capped = await confirm(client, second)
    assert capped.status_code == 429
    assert capped.json()["detail"] == "You've sent 1 emails today, the daily limit. Try again tomorrow (UTC)."
    admin_connection = await gmail.connect(admin_client, account="bob@example.test")
    one, two = await propose(admin_client, admin_connection), await propose(admin_client, admin_connection)
    assert (await confirm(admin_client, one)).status_code == 200
    assert (await confirm(admin_client, two)).status_code == 429  # the admin too


async def test_another_account_is_refused(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    await update_send(row.id, account_subject="another-google-account")  # same address, another account
    response = await confirm(client, row)
    assert response.status_code == 409
    assert response.json()["detail"] == "Your connected Gmail account changed since this email was prepared."


async def test_a_changed_attachment_uses_no_slot(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    document = (await upload(client, "notes.md", md_bytes("# Notes\nFirst version."))).json()
    attachments = await compose.attachments_by_id(client.user_id, [document["id"]])
    row = await propose(client, connection, attachments=attachments)
    async with SessionLocal() as db:
        stored = await db.get(Document, document["id"])
        assert stored is not None
        path = storage.stored_path(stored.stored_name)
    original = path.read_bytes()
    path.write_bytes(b"# Notes\nChanged.")
    response = await confirm(client, row)
    assert response.status_code == 409 and "attachment was changed or deleted" in response.json()["detail"]
    [current] = await pending_sends(client.user_id)
    assert current.status == "pending" and current.attempted_at is None
    path.write_bytes(original)
    sent = await confirm(client, row)
    assert sent.status_code == 200 and sent.json()["attachments"][0]["filename"] == "notes.md"
    attached = list(gmail.mailbox.sent[0].message.iter_attachments())
    assert [part.get_filename() for part in attached] == ["notes.md"]
    assert attached[0].get_payload(decode=True) == original


async def test_turned_off_plugin_or_capability(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    await gmail.set_state(capabilities={"send": False})
    response = await confirm(client, row)
    assert response.status_code == 403
    await gmail.set_state(capabilities={"send": True}, enabled=False)
    assert (await confirm(client, row)).status_code == 403
    shown = await client.get(f"/api/plugins/gmail/sends/{row.id}")
    assert shown.status_code == 200 and shown.json()["status"] == "pending"  # GET works while Gmail is off
    assert (await client.post(f"/api/plugins/gmail/sends/{row.id}/cancel")).json()["status"] == "cancelled"
    assert gmail.mailbox.sent == []


async def test_an_unanswered_send_may_have_been_sent(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    gmail.mailbox.drop_after_send = True
    response = await confirm(client, row)
    assert response.status_code == 200
    assert response.json()["status"] == "failed" and response.json()["error"] == sends.UNCERTAIN
    assert len(gmail.mailbox.sent) == 1
    assert ("send", "error", "ui", 0) in await audit_rows(client.user_id)


async def test_other_users_get_404(gmail, alice, create_user) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    other = await create_user("bob")
    await gmail.connect(other, account="bob@example.test")
    for method, path, body in [
        ("GET", "", None),
        ("POST", "/cancel", None),
        ("POST", "/send", {"content_hash": row.content_hash}),
        ("POST", "/revise", {"to": ["a@b.co"], "body": "x"}),
    ]:
        response = await other.request(method, f"/api/plugins/gmail/sends/{row.id}{path}", json=body)
        assert response.status_code == 404 and response.json()["detail"] == "Email not found", path
    assert (await client.get("/api/plugins/gmail/sends/0")).status_code == 422


async def test_cancelling_the_request_mid_send_still_sends_once(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    gmail.mailbox.send_gate = asyncio.Event()
    async with SessionLocal() as db:
        user = await db.get(User, client.user_id)
    request = asyncio.create_task(sends.confirm(user, row.id, row.content_hash))
    for _ in range(200):
        if any(call[1].endswith("/messages/send") for call in gmail.mailbox.requests):
            break
        await asyncio.sleep(0.01)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    gmail.mailbox.send_gate.set()
    await tasks.drain(5)
    assert len(gmail.mailbox.sent) == 1
    [stored] = await pending_sends(client.user_id)
    assert stored.status == "sent" and stored.gmail_message_id == gmail.mailbox.sent[0].message_id


async def test_a_stale_sending_reads_as_failed_and_can_be_revised(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    row = await propose(client, connection)
    await update_send(row.id, status="sending", attempted_at=utcnow() - timedelta(minutes=6))
    shown = (await client.get(f"/api/plugins/gmail/sends/{row.id}")).json()
    assert shown["status"] == "failed" and shown["error"] == sends.UNCERTAIN
    response = await confirm(client, row)
    assert response.status_code == 409 and response.json()["detail"] == sends.UNCERTAIN
    assert (await pending_sends(client.user_id))[0].status == "failed"
    revised = await client.post(f"/api/plugins/gmail/sends/{row.id}/revise", json={"to": ["bob@company.com"],
                                                                                   "subject": "Again", "body": "B"})
    assert revised.status_code == 200 and revised.json()["status"] == "pending"
    await update_send(revised.json()["id"], status="sending", attempted_at=utcnow() - timedelta(minutes=2))
    fresh = (await client.get(f"/api/plugins/gmail/sends/{revised.json()['id']}")).json()
    assert fresh["status"] == "sending"
    refused = await client.post(f"/api/plugins/gmail/sends/{revised.json()['id']}/revise",
                                json={"to": ["bob@company.com"], "subject": "Again", "body": "B"})
    assert refused.status_code == 409


async def test_a_long_reply_subject_is_stored_as_text_and_sent(gmail, alice) -> None:  # noqa: F811
    assert isinstance(GmailPendingSend.__table__.c.subject.type, Text)
    client, connection = alice
    stored = gmail.message("long_subject")
    reply = await compose.reply_context(GmailClient(connection), stored.id)
    assert reply.subject == reply_subject(LONG_SUBJECT) and len(reply.subject) == 304
    row = await propose(client, connection, subject=reply.subject, reply=reply)
    response = await confirm(client, row)
    assert response.status_code == 200 and response.json()["subject"] == reply.subject
    assert response.json()["reply_to"]["sender"]["address"] == "gina@example.com"
    record = gmail.mailbox.sent[0]
    assert record.metadata == {"threadId": stored.thread_id}
    parsed = message_from_bytes(record.raw, policy=policy.default)
    assert parsed["Subject"] == reply.subject


async def test_deleting_the_conversation_scrubs_its_emails(gmail, alice, monkeypatch) -> None:  # noqa: F811
    client, connection = alice
    _, events = await chat(client, "hello")
    conversation_id = events[0][1]["conversation_id"]
    pending = await propose(client, connection, conversation_id=conversation_id)
    sent = await propose(client, connection, conversation_id=conversation_id)
    assert (await confirm(client, sent)).status_code == 200
    other = await propose(client, connection)  # no conversation: scrubbed at startup
    response = await client.delete(f"/api/conversations/{conversation_id}")
    assert response.status_code == 204
    rows = {row.id: row for row in await pending_sends(client.user_id)}
    for row_id in (pending.id, sent.id):
        row = rows[row_id]
        assert (row.subject, row.body, row.to_addrs, row.attachments, row.reply_to, row.conversation_id) == (
            "", "", [], [], None, None
        )
        assert row.content_hash and row.account_subject == connection.account_subject
    assert rows[pending.id].status == "cancelled" and rows[sent.id].status == "sent"
    assert ("send_cancelled", "ok", "system", 0) in await audit_rows(client.user_id)
    assert rows[other.id].body != ""
    assert await sends.scrub_orphans() == 1
    assert (await pending_sends(client.user_id))[-1].body == ""
    monkeypatch.setattr(settings, "gmail_daily_send_limit", 1)
    later = await propose(client, connection)
    assert (await confirm(client, later)).status_code == 429  # the scrubbed sent row still counts


async def test_disconnecting_cancels_pending_emails(gmail, alice) -> None:  # noqa: F811
    client, connection = alice
    await propose(client, connection)
    assert (await client.delete("/api/plugins/gmail/connection")).status_code == 204
    [stored] = await pending_sends(client.user_id)
    assert stored.status == "cancelled" and stored.body  # content stays with its conversation


def test_unmentioned_flags_and_participants() -> None:
    asked = compose.Requested({"alice@company.com"}, [], set())
    stored = compose.recipients(
        [Address(None, "alice@company.com"), Address(None, "alice@company.co"), Address("Me", ACCOUNT),
         Address(None, "bob@company.com")],
        asked, {"bob@company.com"}, ACCOUNT, bcc=False,
    )
    assert [item["unmentioned"] for item in stored] == [False, True, False, False]
    hidden = compose.recipients([Address(None, "bob@company.com")], asked, {"bob@company.com"}, ACCOUNT, bcc=True)
    assert hidden[0]["unmentioned"] is True  # Bcc gets no participant exemption
