"""Plugins in the chat loop (docs/briefs/plugins-gmail.md, sections 4.4, 4.5 and 7), with the framework's FakePlugin
(tests/fake_plugin.py): when tools and notes are offered, the shared loop, email sources and cards, the request check,
taint, history, Stop and conversation deletion."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import litellm
import pytest
from conftest import (
    ApiClient,
    FakeLLM,
    PluginHarness,
    ScriptedCall,
    SseEvent,
    chat,
    ingest,
    intent_answer,
    oauth_result,
    process_queue,
    tool_call,
)
from fake_plugin import (
    INSTRUCTIONS,
    NO_TOOLS_NOTE,
    NOTE_CONNECT,
    NOTE_NO_TOOLS,
    READ,
    FakePlugin,
    fake_api_router,
    note_reconnect,
)
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import Message, MessagePluginItem, PluginAuditEvent, PluginConnection, PluginSetting
from app.plugins import importing, intent, registry, tasks
from app.plugins.importing import ImportFile
from app.routers import chat as chat_router
from app.routers.chat import _Turn
from app.services import llm, rag

USER = Callable[..., Awaitable[ApiClient]]
ALICE = "alice@example.test"
STOPPED = "Stopped before completion"
StartTurn = Callable[..., Awaitable[_Turn]]


@pytest.fixture
async def fake(
    plugins: PluginHarness, admin_client: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FakePlugin]:
    """The FakePlugin, enabled, with only its tools offered (CHAT_FILE_TOOLS off unless a test turns it on)."""
    plugin = FakePlugin()
    plugins.app.include_router(fake_api_router(plugins.google))
    monkeypatch.setattr(settings, "chat_file_tools", False)
    with registry.use_plugins([plugin]):
        response = await admin_client.patch("/api/admin/plugins/fake", json={"enabled": True})
        assert response.status_code == 200, response.text
        yield plugin


@pytest.fixture
async def alice(fake: FakePlugin, plugins: PluginHarness, create_user: USER) -> ApiClient:
    """A user connected to Fake Mail."""
    user = await create_user("alice")
    assert oauth_result(await plugins.connect(user, "fake", account=ALICE)) == "connected"
    return user


def lookup(query: str = "invoices") -> tuple[str, Any]:
    return tool_call("fake_lookup", query=query)


def act(note: str = "a draft") -> tuple[str, Any]:
    return tool_call("fake_act", note=note)


def system_prompt(call: dict[str, Any]) -> str:
    return call["messages"][0]["content"]


def tool_names(call: dict[str, Any]) -> list[str]:
    return [tool["function"]["name"] for tool in call.get("tools") or []]


def names(events: list[SseEvent]) -> list[str]:
    return [name for name, _ in events]


def of(events: list[SseEvent], name: str) -> list[dict[str, Any]]:
    return [data for event, data in events if event == name]


def tool_results(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [json.loads(message["content"]) for message in call["messages"] if message["role"] == "tool"]


async def audit_rows(user_id: int | None) -> list[tuple[str, str]]:
    async with SessionLocal() as db:
        rows = await db.execute(
            select(PluginAuditEvent.action, PluginAuditEvent.outcome)
            .where(PluginAuditEvent.user_id == user_id, PluginAuditEvent.origin == "chat")
            .order_by(PluginAuditEvent.id)
        )
        return [(action, outcome) for action, outcome in rows.tuples()]


async def connection_id(user_id: int | None) -> int:
    async with SessionLocal() as db:
        found = await db.scalar(select(PluginConnection.id).where(PluginConnection.user_id == user_id))
        assert found is not None
        return found


# ---------------- What a turn offers ----------------


async def test_tools_and_instructions_when_connected(alice: ApiClient, fake_llm: FakeLLM) -> None:
    await chat(alice, "Any invoices?", document_ids=[])
    [call] = fake_llm.chat_calls
    assert tool_names(call) == ["fake_lookup", "fake_act"]
    prompt = system_prompt(call)
    assert INSTRUCTIONS in prompt and rag.APP_NOTE_SENTENCE in prompt
    assert prompt.index(INSTRUCTIONS) < prompt.index(rag.APP_NOTE_SENTENCE)


async def test_notes_instead_of_tools(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakePlugin,
    plugins: PluginHarness,
    admin_client: ApiClient,
    create_user: USER,
    fake_llm: FakeLLM,
) -> None:
    bob = await create_user("bob")
    await chat(bob, "Any mail?", document_ids=[])
    assert "tools" not in fake_llm.chat_calls[-1]
    assert NOTE_CONNECT in system_prompt(fake_llm.chat_calls[-1])
    assert rag.APP_NOTE_SENTENCE not in system_prompt(fake_llm.chat_calls[-1])  # no tool set: no App-note rule

    assert oauth_result(await plugins.connect(bob, "fake", account=ALICE)) == "connected"
    await bob.patch("/api/plugins/fake/connection", json={"chat_enabled": False})
    await chat(bob, "Any mail?", document_ids=[])
    assert "tools" not in fake_llm.chat_calls[-1] and "Fake Mail" not in system_prompt(fake_llm.chat_calls[-1])
    await bob.patch("/api/plugins/fake/connection", json={"chat_enabled": True})

    await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"draft": False}})
    await chat(bob, "Any mail?", document_ids=[])
    assert tool_names(fake_llm.chat_calls[-1]) == ["fake_lookup"]
    await admin_client.patch("/api/admin/plugins/fake", json={"capabilities": {"draft": True}})

    supports = litellm.supports_function_calling
    monkeypatch.setattr(litellm, "supports_function_calling", lambda model: False)
    monkeypatch.setattr(llm, "_tool_support", {})
    await chat(bob, "Any mail?", document_ids=[])
    assert "tools" not in fake_llm.chat_calls[-1] and NOTE_NO_TOOLS in system_prompt(fake_llm.chat_calls[-1])
    monkeypatch.setattr(litellm, "supports_function_calling", supports)
    monkeypatch.setattr(llm, "_tool_support", {})

    async with SessionLocal() as db:
        await db.execute(update(PluginConnection).values(status="reconnect_needed", status_reason="revoked"))
        await db.commit()
    await chat(bob, "Any mail?", document_ids=[])
    expected = note_reconnect("Google access was removed or has expired. Reconnect to keep using Fake Mail.")
    assert "tools" not in fake_llm.chat_calls[-1] and expected in system_prompt(fake_llm.chat_calls[-1])

    await admin_client.patch("/api/admin/plugins/fake", json={"enabled": False})
    await chat(bob, "Any mail?", document_ids=[])
    assert "Fake Mail" not in system_prompt(fake_llm.chat_calls[-1])


async def test_partial_grant_offers_only_granted_tools(
    fake: FakePlugin, plugins: PluginHarness, create_user: USER, fake_llm: FakeLLM
) -> None:
    carol = await create_user("carol")
    plugins.google.grant_scopes = {READ}
    assert oauth_result(await plugins.connect(carol, "fake")) == "connected"
    await chat(carol, "Mail?", document_ids=[])
    assert tool_names(fake_llm.chat_calls[-1]) == ["fake_lookup"]


async def test_a_broken_plugin_never_breaks_chat(fake: FakePlugin, alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake.fail_plan = True
    response, events = await chat(alice, "Hello", document_ids=[])
    assert names(events)[-1] == "done"
    assert "tools" not in fake_llm.chat_calls[-1]


async def test_tool_order_and_identical_prompts_across_calls(
    monkeypatch: pytest.MonkeyPatch, alice: ApiClient, fake_llm: FakeLLM
) -> None:
    monkeypatch.setattr(settings, "chat_file_tools", True)
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([("text", "Two emails [1][2].")])]
    _, events = await chat(alice, "Summarize my invoices", document_ids=[])
    assert names(events)[-1] == "done"
    first, second = fake_llm.chat_calls
    assert tool_names(first) == tool_names(second) == ["create_document", "fake_lookup", "fake_act"]
    assert first["tools"] == second["tools"]
    assert system_prompt(first) == system_prompt(second)
    assert system_prompt(first).count(rag.APP_NOTE_SENTENCE) == 1  # said once, in the file instructions
    assert second["tool_choice"] == "auto"


async def test_model_call_cap_counts_the_plugin_calls(
    monkeypatch: pytest.MonkeyPatch, alice: ApiClient, fake_llm: FakeLLM
) -> None:
    monkeypatch.setattr("fake_plugin.FakeRunner.can_create", lambda self: True)
    fake_llm.script = [ScriptedCall([lookup(f"q{number}")]) for number in range(10)]
    _, events = await chat(alice, "Look everywhere", document_ids=[])
    assert len(fake_llm.chat_calls) == 3 + 2  # the fake tool set's max_calls + 2
    assert fake_llm.chat_calls[-1]["tool_choice"] == "none"
    assert names(events)[-1] == "done"


# ---------------- Sources and cards ----------------


async def test_email_sources_follow_the_documents_and_uncited_ones_are_dropped(
    alice: ApiClient, fake_llm: FakeLLM
) -> None:
    await ingest(alice, "notes.txt", b"Invoice 42 is due on Friday, says the finance team.")
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([("text", "Pay by Friday [2] and see [1].")])]
    _, events = await chat(alice, "When is invoice 42 due?")
    assert names(events)[-1] == "done"
    lists = of(events, "sources")
    assert [[source.get("kind", "document") for source in item["sources"]] for item in lists] == [
        ["document"],
        ["document", "email", "email"],
        ["document", "email"],
    ]
    emails = lists[1]["sources"][1:]
    assert [(email["index"], email["message_id"], email["subject"]) for email in emails] == [
        (2, "m1", "Invoice 42"),
        (3, "m2", "Hello"),
    ]
    assert emails[0]["plugin_id"] == "fake" and emails[0]["date"] == "2026-10-12T14:03:00Z"
    assert names(events).index("sources", 3) < names(events).index("done")
    cards = of(events, "plugin")
    assert [(card["card_id"], card["type"], card["status"]) for card in cards] == [
        ("g1", "gmail_activity", "running"),
        ("g1", "gmail_activity", "done"),
    ]
    [result] = tool_results(fake_llm.chat_calls[1])
    assert [item["cite_as"] for item in result["messages"]] == [2, 3]
    assert result["messages"][1]["content"].startswith('<email cite_as="3">')

    conversation_id = events[0][1]["conversation_id"]
    detail = (await alice.get(f"/api/conversations/{conversation_id}")).json()
    answer = detail["messages"][1]
    assert [source.get("kind") for source in answer["sources"]] == [None, "email"]
    assert answer["sources"][1]["index"] == 2
    [card] = answer["plugin_cards"]
    assert (card["type"], card["status"], card["count"]) == ("gmail_activity", "done", 2)
    async with SessionLocal() as db:
        [item] = list(await db.scalars(select(MessagePluginItem)))
    assert card["card_id"] == f"p{item.id}" and (item.plugin_id, item.card_type) == ("fake", "gmail_activity")
    assert await audit_rows(alice.user_id) == [("search", "ok")]


async def test_no_extra_sources_event_when_every_email_is_cited(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([("text", "Both [1] and [2].")])]
    _, events = await chat(alice, "Mail?", document_ids=[])
    assert len(of(events, "sources")) == 2


async def test_history_notes_reach_the_next_turn(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([("text", "Invoice 42 is due Friday [1].")])]
    _, events = await chat(alice, "Invoice?", document_ids=[])
    conversation_id = events[0][1]["conversation_id"]
    await chat(alice, "Thanks", conversation_id=conversation_id, document_ids=[])
    history = fake_llm.chat_calls[-1]["messages"]
    assert history[2] == {
        "role": "assistant",
        "content": (
            'Invoice 42 is due Friday [1].\n\n(App note: this answer used fake emails [1] "Invoice 42"; 1 cards.)'
        ),
    }


# ---------------- The request check ----------------


async def test_one_check_per_answer_and_its_tokens_count(
    alice: ApiClient, fake_llm: FakeLLM, fake: FakePlugin
) -> None:
    fake_llm.script = [
        ScriptedCall([act("one")], usage=(100, 20)),
        ScriptedCall([act("two")], usage=(100, 20)),
        ScriptedCall([("text", "Done.")], usage=(100, 20)),
    ]
    _, events = await chat(alice, "Draft two notes", document_ids=[])
    assert len(fake_llm.intent_calls) == 1
    assert fake.done == ["one", "two"]
    usage = of(events, "done")[0]["usage"]
    assert (usage["prompt_tokens"], usage["completion_tokens"]) == (300 + 300, 60 + 20)
    assert await audit_rows(alice.user_id) == [("draft", "ok"), ("draft", "ok")]
    statuses = [card["status"] for card in of(events, "plugin")]
    assert statuses == ["writing", "creating", "created", "writing", "creating", "created"]


@pytest.mark.parametrize(
    ("reply", "error", "tool_error", "card_error", "outcome"),
    [
        (intent_answer(), None, intent.REFUSED_TOOL, intent.REFUSED_CARD, "refused"),
        ("Sure thing!", None, intent.UNCONFIRMED_TOOL, intent.UNCONFIRMED_CARD, "error"),
        ('{"draft": "true"}', None, intent.REFUSED_TOOL, intent.REFUSED_CARD, "refused"),
        ("", RuntimeError("provider down"), intent.UNCONFIRMED_TOOL, intent.UNCONFIRMED_CARD, "error"),
    ],
)
async def test_refusals_fail_closed(
    alice: ApiClient,
    fake_llm: FakeLLM,
    fake: FakePlugin,
    reply: str,
    error: Exception | None,
    tool_error: str,
    card_error: str,
    outcome: str,
) -> None:
    fake_llm.intent_reply, fake_llm.intent_error = reply, error
    fake_llm.script = [ScriptedCall([act()]), ScriptedCall([("text", "It wasn't done.")])]
    _, events = await chat(alice, "What's new?", document_ids=[])
    assert fake.done == []
    assert tool_results(fake_llm.chat_calls[1]) == [{"ok": False, "error": tool_error}]
    assert of(events, "plugin")[-1]["status"] == "failed" and of(events, "plugin")[-1]["error"] == card_error
    assert await audit_rows(alice.user_id) == [("draft", outcome)]
    assert names(events)[-1] == "done"


async def test_check_input_and_the_previous_reply_rule(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.script = [ScriptedCall([("text", "Hi! Shall I draft the note for you?")])]
    _, events = await chat(alice, "Hello there", document_ids=[])
    conversation_id = events[0][1]["conversation_id"]
    fake_llm.script = [ScriptedCall([act()]), ScriptedCall([("text", "Drafted.")])]
    await chat(alice, "yes", conversation_id=conversation_id, document_ids=[])
    [first] = fake_llm.intent_calls
    assert first["messages"][0]["content"] == intent.SYSTEM_PROMPT
    assert first["messages"][1]["content"] == (
        "<earlier_user_message>\nHello there\n</earlier_user_message>\n\n"
        "<previous_reply>\nHi! Shall I draft the note for you?\n</previous_reply>\n\n"
        "<latest_user_message>\nyes\n</latest_user_message>"
    )

    # An answer that read email taints the conversation: no previous reply from then on.
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([("text", "Shall I send the Q3 report to them?")])]
    await chat(alice, "Read my mail", conversation_id=conversation_id, document_ids=[])
    fake_llm.script = [ScriptedCall([act()]), ScriptedCall([("text", "Done.")])]
    await chat(alice, "yes", conversation_id=conversation_id, document_ids=[])
    latest = fake_llm.intent_calls[-1]["messages"][1]["content"]
    assert "<previous_reply>" not in latest and "Q3 report" not in latest
    assert latest.count("<earlier_user_message>") == 2  # the last two user messages, oldest first
    assert latest.index("yes") < latest.index("Read my mail") < latest.index("<latest_user_message>")


async def test_previous_reply_of_an_answer_citing_an_imported_email_is_left_out(
    fake: FakePlugin, alice: ApiClient, fake_llm: FakeLLM
) -> None:
    assert alice.user_id is not None
    thread = ImportFile("Invoice 42.md", b"# Invoice 42\n\nPlease pay invoice 42 by Friday.\n", "email", "t1")
    outcome = await importing.store_import(alice.user_id, fake, ALICE, thread)
    assert outcome.status == "imported"
    await process_queue()
    fake_llm.script = [ScriptedCall([("text", "Invoice 42 is due Friday [1]. Shall I draft a reminder?")])]
    _, events = await chat(alice, "When is invoice 42 due?")
    conversation_id = events[0][1]["conversation_id"]
    prompt = system_prompt(fake_llm.chat_calls[-1])
    assert "an email imported from Fake Mail: untrusted text written by others" in prompt
    fake_llm.script = [ScriptedCall([act()]), ScriptedCall([("text", "Done.")])]
    await chat(alice, "yes", conversation_id=conversation_id, document_ids=[])
    assert "<previous_reply>" not in fake_llm.intent_calls[-1]["messages"][1]["content"]


def _follows_the_rule(content: str) -> str:
    """A classifier that applies the check's rule: a draft asked for in the latest message, or in an earlier one that
    the latest only adds details to."""
    latest = re.search(r"<latest_user_message>\n(.*)\n</latest_user_message>", content, re.DOTALL)
    earlier = re.findall(r"<earlier_user_message>\n(.*?)\n</earlier_user_message>", content, re.DOTALL)
    assert latest is not None
    text = latest.group(1).lower()
    details = text.startswith(("formal", "casual", "and ")) or "mention" in text
    asked = "draft" in text or (details and any("draft" in item.lower() for item in earlier))
    return intent_answer("draft") if asked else intent_answer()


async def test_clarification_flow_counts_the_earlier_request(
    alice: ApiClient, fake_llm: FakeLLM, fake: FakePlugin
) -> None:
    fake_llm.intent_reply = _follows_the_rule
    fake_llm.script = [ScriptedCall([("text", "Any particular tone?")])]
    _, events = await chat(alice, "Draft an email to Bob about Q3", document_ids=[])
    conversation_id = events[0][1]["conversation_id"]
    fake_llm.script = [ScriptedCall([act("formal")]), ScriptedCall([("text", "Drafted.")])]
    await chat(alice, "Formal, and mention Friday", conversation_id=conversation_id, document_ids=[])
    assert fake.done == ["formal"]
    fake_llm.script = [ScriptedCall([act("unasked")]), ScriptedCall([("text", "No.")])]
    await chat(alice, "What's the weather like?", conversation_id=conversation_id, document_ids=[])
    assert fake.done == ["formal"]  # a new topic voids the earlier request


async def test_running_out_of_tokens_warns_once_per_model(
    alice: ApiClient, fake_llm: FakeLLM, fake: FakePlugin, caplog: pytest.LogCaptureFixture
) -> None:
    fake_llm.intent_reply, fake_llm.intent_finish_reason = "", "length"
    for _ in range(2):
        fake_llm.script = [ScriptedCall([act()]), ScriptedCall([("text", "Not done.")])]
        await chat(alice, "Draft it", document_ids=[])
    assert fake.done == []
    warnings = [record for record in caplog.records if "ran out of output tokens" in record.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING


async def test_taint_puts_files_through_the_check(
    monkeypatch: pytest.MonkeyPatch, alice: ApiClient, fake_llm: FakeLLM
) -> None:
    monkeypatch.setattr(settings, "chat_file_tools", True)
    md = tool_call("create_document", title="Notes", format="md", content="Pay by Friday [1].", source_numbers=[1])
    fake_llm.intent_reply = intent_answer()
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([md]), ScriptedCall([("text", "Not created.")])]
    _, events = await chat(alice, "Summarize my invoices", document_ids=[])
    [failed] = [event for event in of(events, "file") if event["status"] == "failed"]
    assert failed["error"] == intent.REFUSED_CARD
    assert tool_results(fake_llm.chat_calls[2])[-1] == {"ok": False, "error": intent.REFUSED_TOOL}
    assert (await alice.get("/api/documents")).json() == []

    fake_llm.intent_reply = intent_answer("file")
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([md]), ScriptedCall([("text", "Created.")])]
    _, events = await chat(alice, "Make a file of my invoices", document_ids=[])
    [created] = [event for event in of(events, "file") if event["status"] == "created"]
    download = await alice.get(f"/api/documents/{created['file']['document_id']}/download")
    # The Markdown writer escapes brackets and angle brackets.
    assert "\\[1\\] Email: Invoice 42 (Alice \\<alice@example.test\\>, 2026-10-12)" in download.text

    calls = len(fake_llm.intent_calls)
    fake_llm.script = [ScriptedCall([md]), ScriptedCall([("text", "Created.")])]
    await chat(alice, "Make a file", document_ids=[])  # not tainted: no check
    assert len(fake_llm.intent_calls) == calls


async def test_taint_puts_images_through_the_check(
    alice: ApiClient, image_tools: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tool_call("create_image", prompt="A fox", title="Fox", alt_text="A fox.")
    image_tools.intent_reply = intent_answer()
    image_tools.script = [ScriptedCall([lookup()]), ScriptedCall([image]), ScriptedCall([("text", "No image.")])]
    _, events = await chat(alice, "Read my mail", document_ids=[])
    assert image_tools.image_calls == []
    [failed] = [event for event in of(events, "file") if event["status"] == "failed"]
    assert (failed["kind"], failed["error"]) == ("image", intent.REFUSED_CARD)


async def test_rejected_tools_drop_the_plugin_tools_too(alice: ApiClient, fake_llm: FakeLLM) -> None:
    rejected = litellm.BadRequestError(message="tools.0: unsupported schema", model="x", llm_provider="anthropic")
    fake_llm.script = [ScriptedCall(error=rejected, before_output=True), ScriptedCall([("text", "Plain.")])]
    _, events = await chat(alice, "Mail?", document_ids=[])
    assert names(events)[-1] == "done"
    first, retry = fake_llm.chat_calls
    assert tool_names(first) == ["fake_lookup", "fake_act"] and "tools" not in retry
    assert NO_TOOLS_NOTE in system_prompt(retry) and INSTRUCTIONS not in system_prompt(retry)


async def test_turning_the_plugin_off_refuses_the_next_action_of_a_streaming_answer(
    monkeypatch: pytest.MonkeyPatch, alice: ApiClient, fake_llm: FakeLLM, fake: FakePlugin
) -> None:
    calls = fake_llm.acompletion

    async def switch_off_after_the_first_call(**kwargs: Any) -> Any:
        if kwargs.get("stream") and len(fake_llm.chat_calls) == 1:
            async with SessionLocal() as db:
                await db.execute(update(PluginSetting).where(PluginSetting.plugin_id == "fake").values(enabled=False))
                await db.commit()
        return await calls(**kwargs)

    monkeypatch.setattr(litellm, "acompletion", switch_off_after_the_first_call)
    fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([act()]), ScriptedCall([("text", "Off.")])]
    _, events = await chat(alice, "Read and draft", document_ids=[])
    assert fake.done == [] and fake_llm.intent_calls == []
    assert tool_results(fake_llm.chat_calls[2])[-1] == {
        "ok": False,
        "error": "Fake Mail was turned off by the admin, so this wasn't done.",
    }


# ---------------- Stop, deletion and shutdown ----------------


async def answers_settled() -> None:
    await asyncio.gather(*list(chat_router._answer_tasks), return_exceptions=True)
    await asyncio.gather(*list(chat_router._pending_saves), return_exceptions=True)


async def read_until(stream: AsyncIterator[str], marker: str) -> None:
    received: list[str] = []
    while marker not in "".join(received):
        received.append(await asyncio.wait_for(anext(stream), 30))


async def _no_events() -> AsyncIterator[str]:
    for event in ():
        yield event


_real_sse_with_keepalive = chat_router._sse_with_keepalive


@pytest.fixture
def start_turn(monkeypatch: pytest.MonkeyPatch) -> StartTurn:
    turns: list[_Turn] = []

    def capture(turn: _Turn) -> AsyncIterator[str]:
        turns.append(turn)
        return _no_events()

    monkeypatch.setattr(chat_router, "_sse_with_keepalive", capture)

    async def start(client: ApiClient, message: str, **fields: Any) -> _Turn:
        response, _ = await chat(client, message, **fields)
        assert response.status_code == 200, response.text
        return turns[-1]

    return start


async def test_stop_waits_for_a_tracked_side_effect(
    alice: ApiClient, fake_llm: FakeLLM, fake: FakePlugin, start_turn: StartTurn
) -> None:
    fake.act_gate = asyncio.Event()
    fake_llm.script = [ScriptedCall([("text", "Drafting."), act("kept")])]
    turn = await start_turn(alice, "Draft it", document_ids=[])
    stream = _real_sse_with_keepalive(turn)
    await read_until(stream, '"status": "creating"')
    await stream.aclose()
    await asyncio.sleep(0.05)
    async with SessionLocal() as db:
        assert list(await db.scalars(select(Message).where(Message.role == "assistant"))) == []  # still waiting
    fake.act_gate.set()
    await answers_settled()
    assert fake.done == ["kept"]
    async with SessionLocal() as db:
        [answer] = list(await db.scalars(select(Message).where(Message.role == "assistant")))
        [item] = list(await db.scalars(select(MessagePluginItem)))
    assert (answer.content, answer.error) == ("Drafting.", STOPPED)
    assert item.message_id == answer.id and item.data["status"] == "creating"


async def test_deleting_a_conversation_removes_its_cards_and_calls_the_hook(
    alice: ApiClient, fake_llm: FakeLLM, fake: FakePlugin
) -> None:
    for _ in range(2):
        fake_llm.script = [ScriptedCall([lookup()]), ScriptedCall([("text", "Done [1].")])]
        _, events = await chat(alice, "Mail?", document_ids=[])
    conversation_id = events[0][1]["conversation_id"]
    fake.fail_conversation_hook = True
    assert (await alice.delete(f"/api/conversations/{conversation_id}")).status_code == 204
    assert fake.events == [f"conversation_deleted:{conversation_id}"]
    async with SessionLocal() as db:
        remaining = list(await db.scalars(select(MessagePluginItem)))
    assert len(remaining) == 1  # the other conversation's card


async def test_drain_waits_for_spawned_tasks_and_logs_the_rest(caplog: pytest.LogCaptureFixture) -> None:
    gate = asyncio.Event()
    quick = tasks.spawn(asyncio.sleep(0.01), name="quick")
    stuck = tasks.spawn(gate.wait(), name="stuck send")
    await tasks.drain(0.2)
    assert quick.done() and not stuck.done()
    assert "stuck send" in caplog.text
    gate.set()
    await stuck
    failing = tasks.spawn(_fail(), name="failing import")
    with pytest.raises(RuntimeError):
        await failing
    assert "Plugin task failing import failed" in caplog.text


async def _fail() -> None:
    raise RuntimeError("boom")



async def test_an_answer_with_plugin_cards_taints_the_history(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.script = [ScriptedCall([act()]), ScriptedCall([("text", "Drafted. Shall I draft another one?")])]
    _, events = await chat(alice, "Draft a note", document_ids=[])
    conversation_id = events[0][1]["conversation_id"]
    fake_llm.script = [ScriptedCall([act()]), ScriptedCall([("text", "Done.")])]
    await chat(alice, "yes", conversation_id=conversation_id, document_ids=[])
    latest = fake_llm.intent_calls[-1]["messages"][1]["content"]
    assert "<previous_reply>" not in latest and "Draft a note" in latest
