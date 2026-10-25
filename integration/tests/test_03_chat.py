"""Flow 3: chat over SSE through nginx. Event order per SPEC, sources from the right documents, a streamed answer whose
citations map to the sources, persisted conversations and messages, and usage that matches what the fake reported,
/api/auth/me, /api/me/usage and the admin usage report."""

import json
import re
from dataclasses import dataclass

import pytest
from conftest import PRICES
from harness import (
    User,
    assert_sse_contract,
    chat,
    check,
    sql,
    system_prompt,
    unique,
    upload,
    wait_document,
    wait_until,
)
from samples import md, txt


@dataclass
class Library:
    user: User
    alpha: str
    beta: str
    doc_a: dict
    doc_b: dict


@pytest.fixture(scope="module")
def library(user_factory) -> Library:
    user = user_factory("chat")
    alpha, beta = unique("alpha"), unique("beta")
    doc_a = upload(
        user.client,
        f"{alpha}-handbook.md",
        md(f"The {alpha} handbook says refunds take fourteen days after the {alpha} return form arrives."),
    )
    doc_b = upload(
        user.client,
        f"{beta}-guide.txt",
        txt(f"The {beta} guide says shipping to the islands costs nine euros per {beta} parcel."),
    )
    return Library(user, alpha, beta, wait_document(user.client, doc_a["id"]), wait_document(user.client, doc_b["id"]))


def _title(message: str) -> str:
    """A new conversation's title: the whitespace-collapsed message, its first 60 characters + "…" when longer."""
    collapsed = " ".join(message.split())
    return collapsed if len(collapsed) <= 60 else collapsed[:60] + "…"


def _cost(prompt_tokens: int, completion_tokens: int, kind: str = "chat") -> float:
    prompt_price, completion_price = PRICES[kind]
    return (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000


def test_answer_streams_with_cited_sources(library: Library, fake, models: dict) -> None:
    message = f"How long do refunds take according to the {library.alpha} handbook?"
    mark = fake.mark()
    with fake.knobs(chunk_delay=0.03):
        result = chat(library.user.client, message)
    assert_sse_contract(result)
    assert result.names[-1] == "done"
    assert result.meta["title"] == _title(message)
    assert result.meta["model"] == {"id": models["chat"]["id"], "display_name": "IT Fake Chat"}

    deltas = [index for index, name in enumerate(result.names) if name == "delta"]
    assert len(deltas) >= 5
    assert result.times[deltas[-1]] - result.times[deltas[0]] > 0.1, "the deltas arrived at once: nginx buffered them"

    sources = result.sources
    assert [source["index"] for source in sources] == list(range(1, len(sources) + 1))
    assert sources[0]["document_id"] == library.doc_a["id"] and sources[0]["filename"] == library.doc_a["filename"]
    assert library.alpha in sources[0]["snippet"]
    cited = {int(number) for number in re.findall(r"\[(\d+)\]", result.text)}
    assert 1 in cited and cited <= {source["index"] for source in sources}

    calls = fake.log("chat", after=mark)
    assert len(calls) == 1
    call = calls[0]
    assert call["stream"] and call["auth"] == "placeholder"
    assert call["messages"][-1] == {"role": "user", "content": message}
    prompt = system_prompt(call)
    assert f"[1] (source: {library.doc_a['filename']})" in prompt
    assert "create_document" in call["tools"] and call["parallel_tool_calls"] is False
    assert result.text == call["answer"]
    queries = fake.log("embedding", after=mark)
    assert [entry["input"] for entry in queries] == [[message]]

    usage = result.done["usage"]
    reported = call["usage"]
    assert (usage["prompt_tokens"], usage["completion_tokens"]) == (
        reported["prompt_tokens"],
        reported["completion_tokens"],
    )
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert usage["cost_usd"] == pytest.approx(_cost(usage["prompt_tokens"], usage["completion_tokens"]))


def test_conversation_and_history_persist(library: Library, fake) -> None:
    first_message = f"What does the {library.alpha} handbook say about refunds?"
    first = chat(library.user.client, first_message)
    conversation_id = first.meta["conversation_id"]
    mark = fake.mark()
    second_message = f"And what does the {library.beta} guide say about shipping?"
    second = chat(library.user.client, second_message, conversation_id=conversation_id)
    assert_sse_contract(second)
    assert second.meta["conversation_id"] == conversation_id
    # The retrieval query is the previous question plus the new one, so both documents match.
    assert {library.doc_a["id"], library.doc_b["id"]} <= {source["document_id"] for source in second.sources}

    call = fake.log("chat", after=mark)[0]
    history = [(m["role"], m["content"]) for m in call["messages"][1:]]
    assert history == [("user", first_message), ("assistant", first.text), ("user", second_message)]
    (query,) = fake.log("embedding", after=mark)
    assert first_message in query["input"][0] and second_message in query["input"][0]

    detail = check(library.user.client.get(f"/api/conversations/{conversation_id}")).json()
    assert detail["title"] == _title(first_message)
    messages = detail["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["id"] == first.meta["user_message_id"] and messages[0]["content"] == first_message
    for answer, result in ((messages[1], first), (messages[3], second)):
        assert answer["id"] == result.done["assistant_message_id"]
        assert answer["content"] == result.text
        assert answer["sources"] == result.sources
        assert answer["model_name"] == "IT Fake Chat" and answer["error"] is None
        assert answer["prompt_tokens"] == result.done["usage"]["prompt_tokens"]
        assert answer["completion_tokens"] == result.done["usage"]["completion_tokens"]
    listed = check(library.user.client.get("/api/conversations")).json()
    assert listed[0]["id"] == conversation_id


def test_document_selection_narrows_or_skips_retrieval(library: Library, fake) -> None:
    narrowed = chat(
        library.user.client, f"Tell me about the {library.alpha} handbook.", document_ids=[library.doc_b["id"]]
    )
    assert narrowed.sources and {source["document_id"] for source in narrowed.sources} == {library.doc_b["id"]}
    mark = fake.mark()
    skipped = chat(library.user.client, f"Tell me about the {library.alpha} handbook.", document_ids=[])
    assert_sse_contract(skipped)
    assert skipped.sources == [] and "[1]" not in skipped.text
    assert fake.log("embedding", after=mark) == []
    assert "\n<context>\n" not in system_prompt(fake.log("chat", after=mark)[0])


def test_other_users_conversations_are_404(library: Library, make_user) -> None:
    result = chat(library.user.client, "Hello there")
    conversation_id = result.meta["conversation_id"]
    other = make_user("intruder")
    assert other.client.get(f"/api/conversations/{conversation_id}").status_code == 404
    continued = chat(other.client, "Continue this", conversation_id=conversation_id)
    assert continued.status == 404


def test_provider_failure_ends_with_an_error_event_and_is_saved(library: Library, fake) -> None:
    with fake.knobs(fail_chat=20):
        result = chat(library.user.client, f"What does the {library.alpha} handbook say?")
    assert_sse_contract(result)
    assert result.names[-1] == "error"
    error = result.first("error")
    assert error["message"] and error["assistant_message_id"]
    detail = check(library.user.client.get(f"/api/conversations/{result.meta['conversation_id']}")).json()
    saved = detail["messages"][-1]
    assert saved["role"] == "assistant" and saved["id"] == error["assistant_message_id"] and saved["error"]


def test_client_disconnect_saves_the_partial_answer(library: Library, fake) -> None:
    """Closing the stream mid-answer (Stop) travels through nginx to the backend, which saves what was written."""
    body = {"conversation_id": None, "message": f"What does the {library.alpha} handbook say?", "document_ids": None}
    events: list[dict] = []
    mark = fake.mark()
    with fake.knobs(chunk_delay=0.3):
        with library.user.client.stream("POST", "/api/chat/stream", json=body) as response:
            for line in response.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
                if sum("content" in data for data in events) == 2:
                    break  # leaving the block closes the connection
        conversation_id = events[0]["conversation_id"]

        def saved() -> dict | None:
            last = check(library.user.client.get(f"/api/conversations/{conversation_id}")).json()["messages"][-1]
            return last if last["role"] == "assistant" else None

        answer = wait_until(saved, 30, "the stopped answer to be saved")
    full = fake.log("chat", after=mark)[0]["answer"]
    assert answer["error"] == "Stopped before completion"
    assert answer["content"] and full.startswith(answer["content"]) and len(answer["content"]) < len(full)
    assert answer["prompt_tokens"] > 0


def test_usage_matches_me_and_the_admin_report(make_user, admin, fake) -> None:
    user = make_user("usage")
    mark = fake.mark()
    marker = unique("usage")
    item = upload(user.client, f"{marker}.txt", txt(f"The {marker} memo lists three usage checks for the audit."))
    wait_document(user.client, item["id"])
    results = [chat(user.client, f"What does the {marker} memo list?") for _ in range(2)]
    assert all(result.names[-1] == "done" for result in results)

    chat_calls = fake.log("chat", after=mark)
    assert len(chat_calls) == 2
    chat_tokens = sum(result.done["usage"]["total_tokens"] for result in results)
    assert chat_tokens == sum(c["usage"]["prompt_tokens"] + c["usage"]["completion_tokens"] for c in chat_calls)
    chat_cost = sum(result.done["usage"]["cost_usd"] for result in results)
    embedding_tokens = sum(entry["usage"]["prompt_tokens"] for entry in fake.log("embedding", after=mark))
    embedding_cost = _cost(embedding_tokens, 0, "embedding")

    me = check(user.client.get("/api/auth/me")).json()["usage"]
    assert me["chat_tokens"] == chat_tokens and me["embedding_tokens"] == embedding_tokens
    assert me["cost_usd"] == pytest.approx(chat_cost + embedding_cost, abs=1e-6)

    mine = check(user.client.get("/api/me/usage")).json()
    assert mine["chat_tokens"]["used"] == chat_tokens and mine["requests"] == 2
    assert mine["embedding_tokens"] == embedding_tokens
    assert mine["cost_usd"]["chat"] == pytest.approx(chat_cost, abs=1e-6)
    assert sum(day["chat_tokens"] for day in mine["daily"]) == chat_tokens

    report = check(admin.get("/api/admin/usage")).json()
    row = next(row for row in report["by_user"] if row["user_id"] == user.id)
    assert (row["chat_tokens"], row["embedding_tokens"], row["requests"]) == (chat_tokens, embedding_tokens, 2)
    assert row["cost_usd"] == pytest.approx(chat_cost + embedding_cost, abs=1e-5)
    listed = next(entry for entry in check(admin.get("/api/admin/users")).json() if entry["id"] == user.id)
    assert listed["usage"]["chat_tokens"] == chat_tokens

    events = sql(f"select kind, count(*), sum(total_tokens) from usage_events where user_id = {user.id} group by kind")
    by_kind = {kind: (int(count), int(total)) for kind, count, total in events}
    assert by_kind["chat"] == (2, chat_tokens)
    assert by_kind["embedding"][1] == embedding_tokens
