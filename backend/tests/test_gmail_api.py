"""Gmail's own API (brief 6, 11, 19.2): the browser's thread list and detail, imports, status mapping (409 and never
401), and the plugin's declarations (registry, scopes, settings)."""

import asyncio
from typing import Any

import httpx
import pytest
from fake_google import CLIENT_ID
from fake_google_server import ACCOUNT as SMOKE_ACCOUNT
from fake_google_server import build_app
from gmail_support import ACCOUNT, audit_rows, connection_of, gmail  # noqa: F401

from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.plugins import connections, registry
from app.plugins.base import AccountChanged
from app.plugins.gmail import importer
from app.plugins.gmail.plugin import COMPOSE, FULL_ACCESS, MODIFY, READONLY, SEND, GmailPlugin


@pytest.fixture
async def alice(gmail, create_user) -> Any:  # noqa: F811
    client = await create_user("alice")
    await gmail.connect(client)
    return client


async def test_thread_list_shapes_default_query_and_pages(gmail, alice) -> None:  # noqa: F811
    response = await alice.post("/api/plugins/gmail/threads/search", json={"max_results": 3})
    assert response.status_code == 200
    page = response.json()
    [request] = [call for call in gmail.mailbox.requests if call[1].endswith("/threads")]
    assert ("q", "in:inbox") in request[2] and ("maxResults", "3") in request[2]
    assert page["next_page_token"] == "3" and page["result_size_estimate"] > 3
    newest = page["threads"][0]
    lookalike = gmail.message("invoice_lookalike")
    assert newest == {
        "thread_id": lookalike.thread_id,
        "subject": "Invoice 42",
        "sender": "Bob <bob@evil.test>",
        "date": newest["date"],
        "snippet": "Updated bank details for invoice 42.",
        "message_count": 1,
        "unread": False,
        "gmail_url": f"https://mail.google.com/mail/u/0/#all/{lookalike.thread_id}",
    }
    response = await alice.post("/api/plugins/gmail/threads/search", json={"max_results": 3, "page_token": "3"})
    following = response.json()
    seen = {item["thread_id"] for item in page["threads"]}
    assert seen.isdisjoint(item["thread_id"] for item in following["threads"])
    kickoff = (await alice.post("/api/plugins/gmail/threads/search", json={"q": "subject:kickoff"})).json()["threads"]
    assert [(item["subject"], item["message_count"], item["unread"], item["sender"]) for item in kickoff] == [
        ("Project kickoff", 3, True, "Bob <bob@company.com>")
    ]
    assert not any(("format", "full") in call[2] for call in gmail.mailbox.requests if "/threads/" in call[1])
    assert ("search", "ok", "ui", 3) in await audit_rows(alice.user_id)
    for bad in ({"max_results": 26}, {"q": "x" * 501}, {"page_token": "a b"}):
        assert (await alice.post("/api/plugins/gmail/threads/search", json=bad)).status_code == 422


async def test_thread_detail(gmail, alice) -> None:  # noqa: F811
    thread_id = gmail.message("attachments").thread_id
    detail = (await alice.get(f"/api/plugins/gmail/threads/{thread_id}")).json()
    assert detail["subject"] == "Documents for review" and detail["messages_truncated"] is False
    [message] = detail["messages"]
    assert message["body_text"] == "Please review the attached files." and message["body_truncated"] is False
    assert message["sender"] == "Dana <dana@example.com>" and message["to"] == ["Alice <alice@example.test>"]
    assert [(item["part_id"], item["filename"], item["importable"]) for item in message["attachments"]] == [
        ("1", "report.pdf", True), ("2", "notes.docx", True), ("3", "archive.zip", False), ("4", "legacy.doc", False)
    ]
    digest = (await alice.get(f"/api/plugins/gmail/threads/{gmail.message('thread150_1').thread_id}")).json()
    assert len(digest["messages"]) == 50 and digest["messages_truncated"] is True
    assert digest["messages"][-1]["body_text"] == "Digest message 150"
    assert (await alice.get("/api/plugins/gmail/threads/ffffffffffffffff")).status_code == 404
    assert (await alice.get("/api/plugins/gmail/threads/a.b")).status_code == 422


async def test_ui_import_items_in_request_order(gmail, alice) -> None:  # noqa: F811
    stored = gmail.message("attachments")
    other = gmail.message("plain")
    body = {
        "threads": [
            {
                "thread_id": stored.thread_id,
                "include_thread": True,
                "attachments": [
                    {"message_id": stored.id, "part_id": "1"},
                    {"message_id": stored.id, "part_id": "3"},
                    {"message_id": stored.id, "part_id": "4"},
                    {"message_id": stored.id, "part_id": "9"},
                ],
            },
            {"thread_id": other.thread_id, "include_thread": False,
             "attachments": [{"message_id": stored.id, "part_id": "2"}]},
        ]
    }
    response = await alice.post("/api/plugins/gmail/import", json=body)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(item["kind"], item["label"], item["status"]) for item in items] == [
        ("thread", "Documents for review", "imported"),
        ("attachment", "report.pdf", "imported"),
        ("attachment", "archive.zip", "skipped"),
        ("attachment", "legacy.doc", "skipped"),
        ("attachment", "9", "failed"),
        ("attachment", "2", "failed"),
    ]
    assert items[3]["reason"] == "Legacy .doc files can't be imported. Save it as .docx first."
    assert items[5]["reason"] == "This attachment isn't in that conversation."
    assert items[1]["document"]["imported_from"] == "Gmail" and items[1]["message_id"] == stored.id
    documents = {doc["filename"]: doc for doc in (await alice.get("/api/documents")).json()}
    assert "report.pdf" in documents and documents["report.pdf"]["imported_from"] == "Gmail"
    again = (await alice.post("/api/plugins/gmail/import", json={"threads": body["threads"][:1]})).json()["items"]
    assert [item["status"] for item in again[:2]] == ["already_existed", "already_existed"]
    assert again[1]["document_id"] == items[1]["document_id"]
    assert ("import", "ok", "ui", 2) in await audit_rows(alice.user_id)


async def test_ui_import_limits_and_one_at_a_time(gmail, alice, monkeypatch) -> None:  # noqa: F811
    thread_id = gmail.message("plain").thread_id
    empty = await alice.post("/api/plugins/gmail/import", json={"threads": [{"thread_id": thread_id,
                                                                            "include_thread": False}]})
    assert empty.status_code == 422
    monkeypatch.setattr(settings, "gmail_import_max_items", 2)
    many = {"threads": [{"thread_id": thread_id, "include_thread": True}] * 3}
    assert (await alice.post("/api/plugins/gmail/import", json=many)).status_code == 422
    lock = importer.user_lock(alice.user_id)
    await lock.acquire()
    try:
        busy = await alice.post("/api/plugins/gmail/import", json={"threads": [{"thread_id": thread_id,
                                                                               "include_thread": True}]})
    finally:
        lock.release()
    assert busy.status_code == 409 and busy.json()["detail"] == "An import is already running."
    bad_thread = {"threads": [{"thread_id": "../x", "include_thread": True}]}
    bad = await alice.post("/api/plugins/gmail/import", json=bad_thread)
    assert bad.status_code == 422


async def test_ui_import_deadline_skips_later_items(gmail, alice, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(importer, "IMPORT_SECONDS", 0.05)
    gmail.mailbox.delay = 0.03
    threads = [gmail.message(name).thread_id for name in ("plain", "alternative", "latin1")]
    body = {"threads": [{"thread_id": thread_id, "include_thread": True} for thread_id in threads]}
    items = (await alice.post("/api/plugins/gmail/import", json=body)).json()["items"]
    assert items[0]["status"] == "imported"  # started in time, so it finished
    assert [(item["status"], item["reason"]) for item in items[1:]] == [("skipped", importer.LATE)] * 2


async def test_import_stops_between_items(gmail, alice) -> None:  # noqa: F811
    stopping = asyncio.Event()
    stopping.set()
    connection = await connection_of(alice.user_id)
    assert connection is not None
    async with SessionLocal() as db:
        user = await db.get(User, alice.user_id)
    items = await importer.chat_import(
        connection, user, [gmail.message("plain").thread_id], False, max_items=5,
        deadline=asyncio.get_running_loop().time() + 60, stopping=stopping,
    )
    assert [(item.status, item.reason) for item in items] == [("skipped", importer.STOPPED)]


async def test_plugin_problems_are_409_or_403_never_401(gmail, alice, monkeypatch) -> None:  # noqa: F811
    async def changed(*args: Any, **kwargs: Any) -> str:
        raise AccountChanged("Your connected Gmail account changed. Ask again.")

    with monkeypatch.context() as patch:
        patch.setattr(connections, "access_token", changed)
        response = await alice.post("/api/plugins/gmail/threads/search", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == "Your connected Gmail account changed. Ask again."
    gmail.google.revoke_account(ACCOUNT)
    gmail.google.expire_access_tokens()
    response = await alice.post("/api/plugins/gmail/threads/search", json={})
    assert response.status_code == 409 and "Reconnect" in response.json()["detail"]
    again = await alice.post("/api/plugins/gmail/threads/search", json={})
    assert again.status_code == 409  # now from the connection's status
    await gmail.set_state(enabled=False)
    assert (await alice.post("/api/plugins/gmail/threads/search", json={})).status_code == 403


async def test_unavailable_gmail_is_503(gmail, alice) -> None:  # noqa: F811
    gmail.mailbox.fail_next = [503] * 4
    response = await alice.post("/api/plugins/gmail/threads/search", json={})
    assert response.status_code == 503
    assert response.json()["detail"] == "Gmail is busy right now. Try again in a minute."


async def test_routes_need_the_capability_and_a_connection(gmail, create_user) -> None:  # noqa: F811
    stranger = await create_user("stranger")
    response = await stranger.post("/api/plugins/gmail/threads/search", json={})
    assert response.status_code == 409 and response.json()["detail"] == "Gmail isn't connected."
    await gmail.connect(stranger, capabilities=["draft"])
    response = await stranger.post("/api/plugins/gmail/threads/search", json={})
    assert response.status_code == 409 and "access wasn't granted" in response.json()["detail"]
    await gmail.set_state(capabilities={"read": False})
    assert (await stranger.post("/api/plugins/gmail/threads/search", json={})).status_code == 403


async def test_the_default_registry_loads_gmail_and_lists_it_once_enabled(gmail, alice, admin_client) -> None:  # noqa: F811
    assert [type(plugin) for plugin in registry.builtin_plugins()] == [GmailPlugin]
    listed = (await alice.get("/api/plugins")).json()
    [info] = listed
    assert info["id"] == "gmail" and info["name"] == "Gmail" and info["enabled"] and info["supports_import"]
    assert info["import_max_items"] == settings.gmail_import_max_items and info["testing_mode"] is True
    assert [(item["id"], item["granted"]) for item in info["capabilities"]] == [
        ("read", True), ("draft", True), ("send", True)
    ]
    assert info["connection"]["account_email"] == ACCOUNT
    admin = (await admin_client.get("/api/admin/plugins")).json()[0]
    assert admin["requested_scopes"] == ["openid", "email", READONLY, COMPOSE]
    notes = {item["id"]: item["note"] for item in admin["capabilities"]}
    assert notes["draft"].startswith("Google's permission for drafts also allows sending email.")
    assert notes["read"] is None and notes["send"] is None
    assert admin["redirect_uris"] == ["http://testserver/api/plugins/gmail/oauth/callback"]


def test_scopes_and_what_grants_each_capability() -> None:
    plugin = GmailPlugin()
    assert plugin.scopes_for(frozenset({"read", "draft", "send"})) == (READONLY, COMPOSE)
    assert plugin.scopes_for(frozenset({"draft", "send"})) == (COMPOSE,)
    assert plugin.scopes_for(frozenset({"send"})) == (SEND,)
    assert plugin.scopes_for(frozenset({"read", "send"})) == (READONLY, SEND)
    assert plugin.scopes_for(frozenset()) == ()
    granted = registry.granted_capabilities
    assert granted(plugin, [COMPOSE]) == {"draft", "send"}  # Google's compose scope can send
    assert granted(plugin, [SEND]) == {"send"}
    assert granted(plugin, [READONLY]) == {"read"}
    assert granted(plugin, [MODIFY]) == granted(plugin, [FULL_ACCESS]) == {"read", "draft", "send"}
    assert granted(plugin, ["openid", "https://www.googleapis.com/auth/userinfo.email"]) == set()


def test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = GmailPlugin()
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(settings, "google_oauth_client_secret", " ")
    assert plugin.missing_settings() == ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"]
    assert plugin.test_endpoints() is False
    monkeypatch.setattr(settings, "gmail_api_base_url", "http://localhost:8099")
    assert plugin.test_endpoints() is True
    monkeypatch.setattr(settings, "google_oauth_testing_mode", False)
    assert plugin.testing_mode() is False
    endpoints = plugin.oauth()
    assert endpoints.identity_scopes == ("openid", "email")
    assert dict(endpoints.authorize_params) == {
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"
    }


async def test_the_standalone_fake_server() -> None:
    callback = "http://localhost:8080/api/plugins/gmail/oauth/callback"
    transport = httpx.ASGITransport(app=build_app([callback]))
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as google:
        params = {
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": callback, "scope": "openid email",
            "state": "s", "code_challenge": "c", "code_challenge_method": "S256", "access_type": "offline",
            "prompt": "consent",
        }
        response = await google.get("/o/oauth2/v2/auth", params=params)
        assert response.status_code == 302 and response.headers["location"].startswith(callback + "?")
        wrong = await google.get("/o/oauth2/v2/auth", params={**params, "redirect_uri": "http://evil.test/cb"})
        assert wrong.status_code == 400
        unauthorized = await google.get("/gmail/v1/users/me/profile")
        assert unauthorized.status_code == 401
    assert SMOKE_ACCOUNT == "smoke@example.test"
