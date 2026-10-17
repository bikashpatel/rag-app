"""Shared fixtures of the Gmail plugin's tests (docs/briefs/plugins-gmail.md, 19.2): the fake Gmail mounted on the
fake Google of the `plugins` fixture, the Gmail plugin installed and enabled, and helpers to connect users and look
at their connection, pending sends and audit rows.

Use it from a test module with `from gmail_support import gmail  # noqa: F401` (the fixture) plus the helpers.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import PluginHarness, docx_bytes, oauth_result, pdf_bytes
from fake_gmail import FakeMailbox, StoredMessage, default_mailbox, gmail_router
from sqlalchemy import select

from app.db import SessionLocal
from app.models import GmailPendingSend, PluginAuditEvent, PluginConnection
from app.plugins import registry
from app.plugins.base import ConnectionInfo
from app.plugins.connections import connection_info
from app.plugins.gmail import client as gmail_client
from app.plugins.gmail.plugin import GmailPlugin

ACCOUNT = "alice@example.test"
REPORT_TEXT = "Quarterly report: revenue grew 12 percent."


async def _no_sleep(seconds: float) -> None:
    return None


@dataclass
class Gmail:
    harness: PluginHarness
    plugin: GmailPlugin
    mailbox: FakeMailbox

    @property
    def google(self) -> Any:
        return self.harness.google

    def message(self, name: str) -> StoredMessage:
        return self.mailbox.named[name]

    async def connect(
        self, client: Any, *, account: str | None = None, capabilities: list[str] | None = None
    ) -> ConnectionInfo:
        """Connect `client`'s user through the whole OAuth flow at the fake; returns the connection."""
        response = await self.harness.connect(client, "gmail", account=account, capabilities=capabilities)
        assert oauth_result(response) == "connected", response.headers.get("location")
        found = await connection_of(client.user_id)
        assert found is not None
        return found

    async def set_state(self, *, enabled: bool | None = None, capabilities: dict[str, bool] | None = None) -> None:
        async with SessionLocal() as db:
            await registry.set_state(db, self.plugin, enabled=enabled, capabilities=capabilities)


@pytest.fixture
async def gmail(app: Any, plugins: PluginHarness, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Gmail]:
    """The Gmail plugin installed (registry) and enabled with all capabilities, the default fake mailbox served by the
    fake Google, and the client's backoff sleeps skipped."""
    mailbox = default_mailbox(pdf_bytes(REPORT_TEXT), docx_bytes("Meeting notes for the review."))
    plugins.app.include_router(gmail_router(plugins.google, mailbox))
    monkeypatch.setattr(gmail_client, "_sleep", _no_sleep)
    plugin = GmailPlugin()
    with registry.use_plugins([plugin]):
        found = Gmail(plugins, plugin, mailbox)
        await found.set_state(enabled=True)
        yield found


async def connection_of(user_id: int) -> ConnectionInfo | None:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(PluginConnection).where(PluginConnection.user_id == user_id, PluginConnection.plugin_id == "gmail")
        )
        return connection_info(row) if row is not None else None


async def update_connection(user_id: int, **values: Any) -> None:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(PluginConnection).where(PluginConnection.user_id == user_id, PluginConnection.plugin_id == "gmail")
        )
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()


async def pending_sends(user_id: int | None = None) -> list[GmailPendingSend]:
    async with SessionLocal() as db:
        query = select(GmailPendingSend).order_by(GmailPendingSend.id)
        if user_id is not None:
            query = query.where(GmailPendingSend.user_id == user_id)
        return list(await db.scalars(query))


async def update_send(send_id: int, **values: Any) -> None:
    async with SessionLocal() as db:
        row = await db.get(GmailPendingSend, send_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()


async def audit_rows(user_id: int | None = None) -> list[tuple[str, str, str, int]]:
    """(action, outcome, origin, items) of the Gmail audit rows, oldest first."""
    async with SessionLocal() as db:
        query = select(PluginAuditEvent).where(PluginAuditEvent.plugin_id == "gmail").order_by(PluginAuditEvent.id)
        if user_id is not None:
            query = query.where(PluginAuditEvent.user_id == user_id)
        return [(row.action, row.outcome, row.origin, row.items) for row in await db.scalars(query)]


def gmail_calls(mailbox: FakeMailbox, method: str | None = None) -> list[tuple[str, str, list[tuple[str, str]]]]:
    return [call for call in mailbox.requests if method is None or call[0] == method]


def query_of(call: tuple[str, str, Sequence[tuple[str, str]]]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for key, value in call[2]:
        found.setdefault(key, []).append(value)
    return found
