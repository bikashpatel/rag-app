"""Flow 12: the Gmail plugin against the fake Google (backend/tests/fake_google.py and fake_gmail.py, run as the
fake-google service). The backend's Google endpoints come from the generated env file (GOOGLE_OAUTH_*_URL,
GMAIL_API_BASE_URL); the browser-facing consent URL is the fake's published port. Real Google is never contacted: the
backend has no route to the internet.

Covered: connect through the OAuth flow (state, PKCE, nonce cookie, callback), the Gmail browser (search, read,
import with attachments, indexed by the worker), chat search with an email citation, a draft, and a send that happens
only after the user's confirm with the right content hash, exactly once; disconnect revokes the grant."""

import json
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from harness import (
    APP_URL,
    FAKE_GOOGLE_URL,
    User,
    assert_sse_contract,
    chat,
    check,
    document,
    wait_document,
)


def _google_state() -> dict:
    return check(httpx.get(f"{FAKE_GOOGLE_URL}/_state", timeout=10)).json()


def connect(user: User) -> str:
    """The browser flow: connect -> the fake's consent (answers at once) -> the app's callback. Returns the result."""
    start = check(user.client.post("/api/plugins/gmail/connect", json={}, headers={"Origin": APP_URL})).json()
    authorize = urlparse(start["authorize_url"])
    assert f"{authorize.scheme}://{authorize.netloc}" == FAKE_GOOGLE_URL
    query = parse_qs(authorize.query)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    assert "code_verifier" not in query and "client_secret" not in query
    consent = httpx.get(start["authorize_url"], follow_redirects=False, timeout=10)
    assert consent.status_code == 302, consent.text
    callback = urlparse(consent.headers["location"])
    assert f"{callback.scheme}://{callback.netloc}" == APP_URL and callback.path == "/api/plugins/gmail/oauth/callback"
    finished = user.client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert finished.status_code == 303
    return parse_qs(urlparse(finished.headers["location"]).query)["oauth"][0]


def _card(result, card_type: str) -> dict:
    cards = [data for data in result.all("plugin") if data["type"] == card_type]
    assert cards, f"no {card_type} card in {result.names}"
    return cards[-1]


@pytest.fixture(scope="module")
def gmail_on(admin) -> Iterator[None]:
    plugin = check(admin.patch("/api/admin/plugins/gmail", json={"enabled": True})).json()
    assert plugin["configured"] and plugin["enabled"] and plugin["test_endpoints"]
    assert plugin["redirect_uris"] == [f"{APP_URL}/api/plugins/gmail/oauth/callback"]
    yield
    check(admin.patch("/api/admin/plugins/gmail", json={"enabled": False}))


@pytest.fixture(scope="module")
def mailbox_user(gmail_on, user_factory) -> User:
    user = user_factory("gmail")
    assert connect(user) == "connected"
    return user


def test_connect_through_the_fake_oauth_flow(mailbox_user: User, make_user) -> None:
    (gmail,) = [plugin for plugin in check(mailbox_user.client.get("/api/plugins")).json() if plugin["id"] == "gmail"]
    connection = gmail["connection"]
    assert connection["status"] == "connected" and connection["account_email"] == _google_state()["account"]
    assert connection["chat_enabled"] is True
    assert {capability["id"] for capability in gmail["capabilities"] if capability["granted"]} == {
        "read",
        "draft",
        "send",
    }
    other = make_user("gmail-other")
    (theirs,) = [plugin for plugin in check(other.client.get("/api/plugins")).json() if plugin["id"] == "gmail"]
    assert theirs["connection"] is None
    assert other.client.post("/api/plugins/gmail/threads/search", json={"q": "in:inbox"}).status_code == 409


def test_replayed_callback_is_refused(gmail_on, make_user) -> None:
    user = make_user("gmail-replay")
    start = check(user.client.post("/api/plugins/gmail/connect", json={}, headers={"Origin": APP_URL})).json()
    location = urlparse(httpx.get(start["authorize_url"], follow_redirects=False, timeout=10).headers["location"])
    path = f"{location.path}?{location.query}"
    first = user.client.get(path, follow_redirects=False)
    assert parse_qs(urlparse(first.headers["location"]).query)["oauth"] == ["connected"]
    replay = user.client.get(path, follow_redirects=False)
    assert parse_qs(urlparse(replay.headers["location"]).query)["oauth"] != ["connected"]
    wrong_origin = user.client.post("/api/plugins/gmail/connect", json={}, headers={"Origin": "http://evil.test"})
    assert wrong_origin.status_code == 400
    check(user.client.delete("/api/plugins/gmail/connection"), 204)


def test_browse_and_import_with_attachments(mailbox_user: User) -> None:
    page = check(
        mailbox_user.client.post("/api/plugins/gmail/threads/search", json={"q": "Documents for review"})
    ).json()
    (thread,) = [item for item in page["threads"] if item["subject"] == "Documents for review"]
    detail = check(mailbox_user.client.get(f"/api/plugins/gmail/threads/{thread['thread_id']}")).json()
    (message,) = detail["messages"]
    assert "Please review the attached files." in message["body_text"]
    importable = [item for item in message["attachments"] if item["importable"]]
    assert sorted(item["filename"] for item in importable) == ["notes.docx", "report.pdf"]
    assert all(item["reason"] for item in message["attachments"] if not item["importable"])

    request = {
        "threads": [
            {
                "thread_id": thread["thread_id"],
                "include_thread": True,
                "attachments": [
                    {"message_id": message["message_id"], "part_id": item["part_id"]} for item in importable
                ],
            }
        ]
    }
    items = check(mailbox_user.client.post("/api/plugins/gmail/import", json=request)).json()["items"]
    assert [item["status"] for item in items] == ["imported"] * 3, items
    for item in items:
        ready = wait_document(mailbox_user.client, item["document_id"])
        assert ready["imported_from"] == "Gmail" and ready["scope"] == "private" and ready["num_chunks"] >= 1
    again = check(mailbox_user.client.post("/api/plugins/gmail/import", json=request)).json()["items"]
    assert [item["status"] for item in again] == ["already_existed"] * 3
    result = chat(mailbox_user.client, "What does the quarterly report attachment say about revenue?")
    pdf_id = next(item["document_id"] for item in items if item["label"].endswith("report.pdf"))
    assert pdf_id in {source["document_id"] for source in result.sources}
    assert document(mailbox_user.client, pdf_id)["filename"] == "report.pdf"


def test_chat_searches_gmail_and_cites_the_email(mailbox_user: User, fake) -> None:
    mark = fake.mark()
    result = chat(mailbox_user.client, 'Search my email for "Quarterly numbers"', document_ids=[])
    assert_sse_contract(result)
    assert result.names[-1] == "done"
    emails = [source for source in result.sources if source.get("kind") == "email"]
    assert len(emails) == 1 and emails[0]["subject"] == "Quarterly numbers" and emails[0]["plugin_id"] == "gmail"
    assert f"[{emails[0]['index']}]" in result.text
    activity = _card(result, "gmail_activity")
    assert activity["status"] == "done" and activity["action"] == "search" and activity["count"] >= 1
    first, second = fake.log("chat", after=mark)
    assert {"gmail_search", "gmail_create_draft", "gmail_propose_send"} <= set(first["tools"])
    assert first["tool_call"]["name"] == "gmail_search"
    returned = json.loads(second["messages"][-1]["content"])["messages"]
    assert returned and returned[0]["content"].startswith(f'<email cite_as="{returned[0]["cite_as"]}">')
    detail = check(mailbox_user.client.get(f"/api/conversations/{result.meta['conversation_id']}")).json()
    assert [source["kind"] for source in detail["messages"][-1]["sources"] if "kind" in source] == ["email"]


def test_draft_is_created_and_nothing_is_sent(mailbox_user: User, fake) -> None:
    before = _google_state()
    mark = fake.mark()
    result = chat(
        mailbox_user.client, 'Draft an email to bob@company.com with the subject "Kickoff agenda"', document_ids=[]
    )
    assert result.names[-1] == "done", result.events[-1]
    card = _card(result, "gmail_draft")
    assert card["status"] == "created" and [recipient["address"] for recipient in card["to"]] == ["bob@company.com"]
    assert [entry["kind"] for entry in fake.log(after=mark) if entry["kind"] == "request_check"] == ["request_check"]
    after = _google_state()
    assert len(after["drafts"]) == len(before["drafts"]) + 1 and len(after["sent"]) == len(before["sent"])
    draft = after["drafts"][-1]
    assert draft["to"] == "bob@company.com" and draft["subject"] == "Kickoff agenda" and not draft["bcc"]


@pytest.mark.deadline(240)
def test_send_happens_only_after_confirm_and_once(mailbox_user: User, make_user) -> None:
    sent_before = len(_google_state()["sent"])
    result = chat(
        mailbox_user.client, 'Send an email to bob@company.com with the subject "Status update"', document_ids=[]
    )
    assert result.names[-1] == "done", result.events[-1]
    card = _card(result, "gmail_send")
    pending = card["pending"]
    assert card["status"] == "proposed" and pending["status"] == "pending"
    assert [recipient["address"] for recipient in pending["to"]] == ["bob@company.com"]
    assert len(_google_state()["sent"]) == sent_before, "proposing an email must not send it"

    send = f"/api/plugins/gmail/sends/{pending['id']}"
    other = make_user("gmail-thief")  # connected to the same Google account, but not the proposal's owner
    assert connect(other) == "connected"
    assert other.client.get(send).status_code == 404
    assert other.client.post(f"{send}/send", json={"content_hash": pending["content_hash"]}).status_code == 404
    assert mailbox_user.client.post(f"{send}/send", json={"content_hash": "0" * 64}).status_code == 409
    assert len(_google_state()["sent"]) == sent_before
    revocations = _google_state()["revocations"]
    check(other.client.delete("/api/plugins/gmail/connection"), 204)
    assert _google_state()["revocations"] == revocations, "a grant another connection shares must not be revoked"

    done = check(mailbox_user.client.post(f"{send}/send", json={"content_hash": pending["content_hash"]})).json()
    assert done["status"] == "sent" and done["gmail_url"]
    state = _google_state()
    assert len(state["sent"]) == sent_before + 1
    assert state["sent"][-1]["to"] == "bob@company.com" and state["sent"][-1]["subject"] == "Status update"
    again = mailbox_user.client.post(f"{send}/send", json={"content_hash": pending["content_hash"]})
    assert again.status_code == 409
    assert len(_google_state()["sent"]) == sent_before + 1


def test_users_without_a_connection_get_no_gmail_tools(gmail_on, make_user, fake) -> None:
    user = make_user("gmail-none")
    mark = fake.mark()
    result = chat(user.client, 'Search my email for "Quarterly numbers"', document_ids=[])
    assert result.names[-1] == "done"
    (call,) = fake.log("chat", after=mark)
    assert not [name for name in call["tools"] if name.startswith("gmail_")]


def test_disconnect_revokes_the_grant(mailbox_user: User) -> None:
    revocations = _google_state()["revocations"]
    check(mailbox_user.client.delete("/api/plugins/gmail/connection"), 204)
    assert _google_state()["revocations"] == revocations + 1
    (gmail,) = [plugin for plugin in check(mailbox_user.client.get("/api/plugins")).json() if plugin["id"] == "gmail"]
    assert gmail["connection"] is None
    assert mailbox_user.client.post("/api/plugins/gmail/threads/search", json={}).status_code == 409
