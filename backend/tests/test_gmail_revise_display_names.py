"""Display names survive the Edit email dialog (POST …/sends/{id}/revise): the dialog prefills each recipient with
formatAddressForInput (frontend/src/components/chat/plugins/email.ts), which quotes a name containing an RFC 5322
special that getaddresses reads as structure. Unquoted, "John Smith [EXTERNAL]" was rejected as an invalid address and
"Support: Acme" lost "Support:" (group syntax)."""

import re
from typing import Any

import pytest
from gmail_support import gmail  # noqa: F401

from app.db import SessionLocal
from app.models import GmailPendingSend, User
from app.plugins.base import ConnectionInfo
from app.plugins.gmail import compose, sends
from app.plugins.gmail.mime import Address, OutgoingEmail, parse_addresses

# Keep in sync with formatAddressForInput in frontend/src/components/chat/plugins/email.ts.
_NEEDS_QUOTES = re.compile(r'[",;:<>()\[\]@\\]')

NAMES = [
    "John Smith [EXTERNAL]",
    "dependabot[bot]",
    "Re: Bob",
    "Support: Acme",
    "Doe, Jane",
    'Ann "the boss" Lee',
    "back\\slash",
    "Ann (Sales)",
    "x;y",
    "Dr. Jane Doe",
    "José Müller",
    "Bob",
]


def modal_entry(name: str | None, address: str) -> str:
    """One recipient as the Edit email dialog prefills it."""
    if not name:
        return address
    quoted = '"' + re.sub(r'(["\\])', r"\\\1", name) + '"' if _NEEDS_QUOTES.search(name) else name
    return f"{quoted} <{address}>"


@pytest.mark.parametrize("name", NAMES)
def test_the_dialogs_form_parses_back_to_the_same_name(name: str) -> None:
    assert parse_addresses([modal_entry(name, "someone@example.com")]) == [Address(name, "someone@example.com")]


def test_plain_names_stay_unquoted() -> None:
    assert modal_entry("Dr. Jane Doe", "j@example.com") == "Dr. Jane Doe <j@example.com>"
    assert modal_entry("José Müller", "j@example.com") == "José Müller <j@example.com>"
    assert modal_entry(None, "j@example.com") == "j@example.com"


async def _propose(client: Any, connection: ConnectionInfo, to: list[dict[str, Any]]) -> GmailPendingSend:
    email = OutgoingEmail(
        account_email=connection.account_email,
        to=compose.stored_addresses(to),
        cc=[],
        bcc=[],
        subject="Q3 numbers",
        body="Hello,\nthe numbers look good.\n",
        attachments=[],
        in_reply_to=None,
        references=None,
    )
    async with SessionLocal() as db:
        user = await db.get(User, client.user_id)
        assert user is not None
        return await sends.propose(
            db, user, connection, email, conversation_id=None, to=to, cc=[], bcc=[], attachments_meta=[],
            thread_id=None, reply_to=None, origin="chat",
        )


async def test_editing_only_the_body_keeps_bracket_and_colon_names(gmail, create_user) -> None:  # noqa: F811
    client = await create_user("smoke-names")
    connection = await gmail.connect(client)
    original = [
        {"name": "John Smith [EXTERNAL]", "address": "js@corp.example", "unmentioned": False},
        {"name": "Support: Acme", "address": "support@acme.example", "unmentioned": False},
    ]
    row = await _propose(client, connection, original)
    shown = (await client.get(f"/api/plugins/gmail/sends/{row.id}")).json()
    # The dialog prefills To from the card's recipients and the user only changes the text.
    to = [modal_entry(item["name"], item["address"]) for item in shown["to"]]
    assert to == ['"John Smith [EXTERNAL]" <js@corp.example>', '"Support: Acme" <support@acme.example>']
    response = await client.post(
        f"/api/plugins/gmail/sends/{row.id}/revise", json={"to": to, "subject": "Q3 numbers", "body": "Edited"}
    )
    assert response.status_code == 200, response.text
    revised = response.json()
    assert [(item["name"], item["address"]) for item in revised["to"]] == [
        ("John Smith [EXTERNAL]", "js@corp.example"),
        ("Support: Acme", "support@acme.example"),
    ]
    sent = await client.post(
        f"/api/plugins/gmail/sends/{revised['id']}/send", json={"content_hash": revised["content_hash"]}
    )
    assert sent.status_code == 200, sent.text
    message = gmail.mailbox.sent[-1].message
    assert [(address.display_name, address.addr_spec) for address in message["To"].addresses] == [
        ("John Smith [EXTERNAL]", "js@corp.example"),
        ("Support: Acme", "support@acme.example"),
    ]
