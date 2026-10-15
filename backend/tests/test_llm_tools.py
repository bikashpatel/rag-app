"""Tool calling in app.services.llm: the streamed response recorded in order (ChatStep), its replay as ordered
assistant segments, and, against a local fake provider server, the request bodies the real LiteLLM sends."""

import json
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import litellm
import pytest
import uvicorn
from conftest import FakeLLM, ScriptedCall, tool_call
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from app.models import LLMModel
from app.services import document_tool, llm

pytestmark = [  # raised inside LiteLLM's own response models, in the tests that run the real LiteLLM
    pytest.mark.filterwarnings("ignore:Item 'summary' on TypedDict:UserWarning"),
    pytest.mark.filterwarnings("ignore:Accessing the 'model_"),
]

OPUS = "anthropic/claude-opus-5-5"
MINI = "openai/gpt-5.4-mini"
TOOLS = [{"type": "function", "function": {"name": "create_document", "parameters": {"type": "object"}}}]
_REAL_ACOMPLETION = litellm.acompletion  # the autouse fake_llm fixture replaces it in every test


def chat_model(litellm_model: str = OPUS, **fields: Any) -> LLMModel:
    return LLMModel(litellm_model=litellm_model, display_name="Model", kind="chat", **fields)


async def collect(
    model: LLMModel, step: llm.ChatStep, usage: llm.Usage | None = None, **kwargs: Any
) -> list[str | llm.ToolCallStarted | llm.ToolCallProgress]:
    items = []
    async for item in llm.stream_chat(
        model, [{"role": "user", "content": "Hi"}], usage or llm.Usage(), step=step, **kwargs
    ):
        items.append(item)
    return items


# ---------------- stream_chat ----------------


@pytest.mark.parametrize("shape", ["anthropic", "openai"])
async def test_tool_calls_are_streamed_and_recorded(fake_llm: FakeLLM, shape: str) -> None:
    arguments = {"title": "Q3 report", "format": "pdf", "content": "# Q3\n\nBody"}
    fake_llm.script = [ScriptedCall([("text", "Sure."), tool_call(**arguments)], shape=shape)]  # type: ignore[arg-type]
    step = llm.ChatStep()

    items = await collect(chat_model(), step, tools=TOOLS)

    assert items[0] == "Sure."
    assert items[1] == llm.ToolCallStarted(0, "create_document")
    assert set(items[2:]) == {llm.ToolCallProgress(0)} and len(items) > 3
    [call] = step.tool_calls
    prefix = "toolu_" if shape == "anthropic" else "call_"
    assert (call.index, call.id, call.name) == (0, f"{prefix}0", "create_document")
    assert json.loads(call.arguments) == arguments
    assert step.sequence == [("text", "Sure."), ("tool", 0)]
    assert (step.finish_reason, step.received) == ("tool_calls", True)


async def test_openai_calls_that_arrive_whole_are_recorded_in_order(fake_llm: FakeLLM) -> None:
    fake_llm.script = [ScriptedCall([tool_call(title="a"), tool_call(title="b")], shape="openai")]
    step = llm.ChatStep()

    items = await collect(chat_model(MINI), step, tools=TOOLS)

    assert [call.id for call in step.tool_calls] == ["call_0", "call_1"]
    assert [json.loads(call.arguments)["title"] for call in step.tool_calls] == ["a", "b"]
    assert llm.ToolCallStarted(1, "create_document") in items and llm.ToolCallProgress(1) in items


async def test_thinking_blocks_keep_their_place_and_text(fake_llm: FakeLLM) -> None:
    fake_llm.script = [
        ScriptedCall(
            [
                ("thinking", (["Plan: ", "write the ", "report."], "SIG-1")),
                ("text", "Sure."),
                ("redacted", "REDACTED-DATA"),
                ("thinking", ([""], "SIG-2")),
                tool_call(title="x", format="md", content="y"),
            ]
        )
    ]
    step = llm.ChatStep()

    items = await collect(chat_model(), step, tools=TOOLS)

    assert [kind for kind, _ in step.sequence] == ["thinking", "text", "thinking", "thinking", "tool"]
    assert step.sequence[0][1] == {"type": "thinking", "thinking": "Plan: write the report.", "signature": "SIG-1"}
    assert step.sequence[2][1] == {"type": "redacted_thinking", "data": "REDACTED-DATA"}
    assert step.sequence[3][1] == {"type": "thinking", "thinking": "", "signature": "SIG-2"}
    assert len(step.thinking_blocks) == 3  # the unsigned deltas aren't kept
    assert "Plan" not in "".join(item for item in items if isinstance(item, str))


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("tool_calls", "tool_calls"), ("length", "length"), ("content_filter", "content_filter"), ("", None)],
)
async def test_the_finish_reason_is_recorded(fake_llm: FakeLLM, finish: str, expected: str | None) -> None:
    fake_llm.script = [ScriptedCall([tool_call(title="x")], finish_reason=finish)]
    step = llm.ChatStep()

    await collect(chat_model(), step, tools=TOOLS)

    assert step.finish_reason == expected


async def test_usage_is_reported_or_estimated_with_the_arguments(fake_llm: FakeLLM) -> None:
    content = "word " * 400
    fake_llm.script = [
        ScriptedCall([tool_call(title="x", content=content)], usage=(1000, 900)),
        ScriptedCall([tool_call(title="x", content=content)], usage=None),
    ]
    reported, estimated = llm.Usage(), llm.Usage()

    await collect(chat_model(), llm.ChatStep(), reported, tools=TOOLS)
    await collect(chat_model(), llm.ChatStep(), estimated, tools=TOOLS)

    assert (reported.prompt_tokens, reported.completion_tokens, reported.estimated) == (1000, 900, False)
    assert estimated.estimated and estimated.completion_tokens > 300  # the arguments count as output


async def test_the_step_is_complete_when_the_stream_breaks_or_is_left(fake_llm: FakeLLM) -> None:
    fake_llm.script = [
        ScriptedCall([("text", "Hi"), tool_call(title="x")], error=RuntimeError("broken"), finish_reason=""),
        ScriptedCall([("text", "Hi"), tool_call(title="abcdefghijklmnopqrstuvwxyz")]),
    ]
    broken, left = llm.ChatStep(), llm.ChatStep()

    with pytest.raises(llm.LLMError):
        await collect(chat_model(), broken, tools=TOOLS)
    usage = llm.Usage()
    stream = llm.stream_chat(chat_model(), [{"role": "user", "content": "Hi"}], usage, step=left, tools=TOOLS)
    async for item in stream:
        if isinstance(item, llm.ToolCallProgress):
            break
    await stream.aclose()

    assert broken.received and json.loads(broken.tool_calls[0].arguments) == {"title": "x"}
    assert left.tool_calls[0].name == "create_document" and left.tool_calls[0].arguments
    assert usage.total_tokens > 0


async def test_tool_arguments_reach_acompletion_only_when_given(fake_llm: FakeLLM) -> None:
    await collect(chat_model(), llm.ChatStep())
    await collect(
        chat_model(), llm.ChatStep(), tools=TOOLS, tool_choice="auto", parallel_tool_calls=False, max_tokens=48000
    )

    plain, with_tools = fake_llm.chat_calls
    assert not {"tools", "tool_choice", "parallel_tool_calls"} & set(plain) and plain["max_tokens"] == 16000
    assert (with_tools["tools"], with_tools["tool_choice"], with_tools["parallel_tool_calls"]) == (TOOLS, "auto", False)
    assert with_tools["max_tokens"] == 48000


async def test_without_tools_only_text_is_yielded(fake_llm: FakeLLM) -> None:
    fake_llm.script = [ScriptedCall([("text", "Hello"), tool_call(title="x")])]

    items = await collect(chat_model(), llm.ChatStep())

    assert items == ["Hello"]


async def test_bad_requests_are_marked_for_the_recoveries(fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    long_detail = "Invalid `signature` in `thinking` block " + "x" * 2000
    bad = litellm.BadRequestError(message=long_detail, model=OPUS, llm_provider="anthropic")
    window = litellm.ContextWindowExceededError(message="prompt is too long", model=OPUS, llm_provider="anthropic")
    fake_llm.script = [ScriptedCall(error=bad, before_output=True), ScriptedCall(error=window, before_output=True)]

    with caplog.at_level(logging.WARNING), pytest.raises(llm.LLMError) as bad_error:
        await collect(chat_model(), llm.ChatStep(), tools=TOOLS)
    with pytest.raises(llm.LLMError) as window_error:
        await collect(chat_model(), llm.ChatStep(), tools=TOOLS)

    assert bad_error.value.bad_request and "signature" in bad_error.value.detail
    assert not bad_error.value.context_window
    assert len(bad_error.value.detail) <= 300
    assert not window_error.value.bad_request and window_error.value.context_window
    warnings = [record.getMessage() for record in caplog.records if record.name == "app.services.llm"]
    assert warnings and all(len(message) < 400 for message in warnings)


# ---------------- Replaying a response ----------------


def recorded(*items: tuple[str, Any], calls: list[tuple[str, str]] = ()) -> llm.ChatStep:  # type: ignore[assignment]
    step = llm.ChatStep()
    step.sequence = list(items)
    step.tool_calls = [
        llm.ToolCall(index, f"id{index}", name, arguments) for index, (name, arguments) in enumerate(calls)
    ]
    return step


THINK_A = {"type": "thinking", "thinking": "A", "signature": "S1"}
THINK_B = {"type": "thinking", "thinking": "", "signature": "S2"}


def test_a_response_without_thinking_is_one_message() -> None:
    step = recorded(("text", "Hi\x00 there"), ("tool", 0), calls=[("create_document", '{"title": "x"}')])

    assert llm.assistant_turn(step, ["c1"]) == [
        {
            "role": "assistant",
            "content": "Hi\x00 there",  # replayed as produced, NUL included
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "create_document", "arguments": '{"title": "x"}'}}
            ],
        }
    ]


def test_interleaved_thinking_is_replayed_as_segments_in_order() -> None:
    call = [("create_document", '{"title": "x"}')]

    thinking_text = llm.assistant_turn(
        recorded(("thinking", THINK_A), ("text", "Sure."), ("thinking", THINK_B), ("tool", 0), calls=call), ["c1"]
    )
    two_tools = llm.assistant_turn(
        recorded(("thinking", THINK_A), ("tool", 0), ("thinking", THINK_B), ("tool", 1), calls=call * 2), ["c1", "c2"]
    )
    text_first = llm.assistant_turn(recorded(("text", "Sure."), ("thinking", THINK_B), ("tool", 0), calls=call), ["c1"])

    assert [
        (m["content"], m.get("thinking_blocks"), [c["id"] for c in m.get("tool_calls", [])]) for m in thinking_text
    ] == [
        ("Sure.", [THINK_A], []),
        (None, [THINK_B], ["c1"]),
    ]
    assert [
        (m["content"], m.get("thinking_blocks"), [c["id"] for c in m.get("tool_calls", [])]) for m in two_tools
    ] == [
        (None, [THINK_A], ["c1"]),
        (None, [THINK_B], ["c2"]),
    ]
    assert [
        (m["content"], m.get("thinking_blocks"), [c["id"] for c in m.get("tool_calls", [])]) for m in text_first
    ] == [
        ("Sure.", None, []),
        (None, [THINK_B], ["c1"]),
    ]
    assert all("tool_calls" not in m for m in (thinking_text[0], text_first[0]))


@pytest.mark.parametrize("arguments", ['{"title": "x", "content": "a"b', '{"title": "cut', "[1, 2]", '"text"', ""])
def test_arguments_that_are_not_a_json_object_are_echoed_as_empty(arguments: str) -> None:
    step = recorded(("tool", 0), calls=[("create_document", arguments)])

    [message] = llm.assistant_turn(step, ["c1"])

    assert message["tool_calls"][0]["function"]["arguments"] == "{}"


def test_strip_thinking_and_tool_results() -> None:
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": None, "thinking_blocks": [THINK_A]},
        {"role": "assistant", "content": "Sure.", "thinking_blocks": [THINK_B]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}], "thinking_blocks": [THINK_B]},
        llm.tool_result_message("c1", {"ok": True, "filename": "Bericht ü.pdf"}),
    ]

    llm.strip_thinking(messages)

    assert messages == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Sure."},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true, "filename": "Bericht ü.pdf"}'},
    ]


async def test_tool_support_is_looked_up_once_and_can_be_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    lookups: list[str] = []
    real = litellm.supports_function_calling

    def counted(model: str) -> bool:
        lookups.append(model)
        return real(model)

    monkeypatch.setattr(litellm, "supports_function_calling", counted)
    opus, unknown = chat_model(), chat_model("openai/some-house-model")

    assert [await llm.supports_tools(opus) for _ in range(3)] == [True, True, True]
    assert await llm.supports_tools(unknown) is False
    assert lookups == [OPUS, "openai/some-house-model"]

    llm.mark_tools_rejected(opus)
    assert await llm.supports_tools(opus) is False
    assert await llm.supports_tools(chat_model(api_base="http://other.example")) is True  # another endpoint


async def test_a_failing_tool_support_lookup_means_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(model: str) -> bool:
        raise RuntimeError("lookup failed")

    monkeypatch.setattr(litellm, "supports_function_calling", broken)

    assert await llm.supports_tools(chat_model()) is False


def test_usage_add_sums_and_keeps_estimates() -> None:
    total = llm.Usage(10, 5, 0.5)
    total.add(llm.Usage(1, 2, 0.25, estimated=True))

    assert (total.prompt_tokens, total.completion_tokens, total.cost_usd, total.estimated) == (11, 7, 0.75, True)


# ---------------- The real LiteLLM against a local provider ----------------


class FakeProvider:
    """Canned Anthropic Messages and OpenAI chat-completions streams; records every request body."""

    def __init__(self) -> None:
        self.responses: list[Callable[[], Iterator[str]]] = []
        self.bodies: list[dict[str, Any]] = []
        self.port = _free_port()
        app = Starlette(routes=[Route("/{path:path}", self.handle, methods=["POST"])])
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    async def handle(self, request: Request) -> StreamingResponse:
        self.bodies.append(await request.json())
        return StreamingResponse(self.responses.pop(0)(), media_type="text/event-stream")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def provider() -> Iterator[FakeProvider]:
    server = FakeProvider()
    server.thread.start()
    deadline = time.monotonic() + 10
    while not server.server.started:
        assert time.monotonic() < deadline, "the fake provider didn't start"
        time.sleep(0.02)
    yield server
    server.server.should_exit = True
    server.thread.join(timeout=5)


@pytest.fixture
def real_litellm(provider: FakeProvider, monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    monkeypatch.setattr(litellm, "acompletion", _REAL_ACOMPLETION)
    provider.responses.clear()
    provider.bodies.clear()
    return provider


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def anthropic_stream(*blocks: tuple[str, Any], stop_reason: str = "tool_use") -> Callable[[], Iterator[str]]:
    def stream() -> Iterator[str]:
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 1000, "output_tokens": 1},
        }
        yield _sse("message_start", {"type": "message_start", "message": message})
        for index, (kind, value) in enumerate(blocks):

            def delta(payload: dict[str, Any], index: int = index) -> str:
                return _sse("content_block_delta", {"type": "content_block_delta", "index": index, "delta": payload})

            if kind == "thinking":
                deltas, signature = value
                start = {"type": "thinking", "thinking": "", "signature": ""}
                yield _sse(
                    "content_block_start", {"type": "content_block_start", "index": index, "content_block": start}
                )
                for text in deltas:
                    yield delta({"type": "thinking_delta", "thinking": text})
                yield delta({"type": "signature_delta", "signature": signature})
            elif kind == "text":
                start = {"type": "text", "text": ""}
                yield _sse(
                    "content_block_start", {"type": "content_block_start", "index": index, "content_block": start}
                )
                yield delta({"type": "text_delta", "text": value})
            else:
                call_id, arguments = value
                start = {"type": "tool_use", "id": call_id, "name": "create_document", "input": {}}
                yield _sse(
                    "content_block_start", {"type": "content_block_start", "index": index, "content_block": start}
                )
                yield delta({"type": "input_json_delta", "partial_json": arguments})
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": index})
        yield _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 50},
            },
        )
        yield _sse("message_stop", {"type": "message_stop"})

    return stream


def openai_stream(*tool_arguments: str) -> Callable[[], Iterator[str]]:
    def stream() -> Iterator[str]:
        base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-5.4-mini"}

        def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
            return (
                "data: "
                + json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
                + "\n\n"
            )

        yield chunk({"role": "assistant", "content": "ok"})
        for index, arguments in enumerate(tool_arguments):
            function = {"name": "create_document", "arguments": arguments}
            yield chunk(
                {"tool_calls": [{"index": index, "id": f"call_{index}", "type": "function", "function": function}]}
            )
        yield chunk({}, "tool_calls" if tool_arguments else "stop")
        yield (
            "data: "
            + json.dumps(
                {**base, "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}}
            )
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    return stream


async def run_call(model: LLMModel, messages: list[dict], **kwargs: Any) -> llm.ChatStep:
    step = llm.ChatStep()
    async for _ in llm.stream_chat(model, messages, llm.Usage(), step=step, **kwargs):
        pass
    return step


def _block_types(message: dict[str, Any]) -> list[str]:
    return [block["type"] for block in message["content"]]


async def test_litellm_replays_interleaved_thinking_in_order(real_litellm: FakeProvider) -> None:
    model = chat_model(api_base=real_litellm.base)
    tools = [document_tool.tool_spec(model)]
    arguments = '{"title": "Q3", "format": "md", "content": "Body"}'
    real_litellm.responses += [
        anthropic_stream(
            ("thinking", (["Plan: ", "write the ", "report."], "SIG-1")),
            ("text", "Sure."),
            ("thinking", ([""], "SIG-2")),
            ("tool", ("toolu_A", arguments)),
        ),
        anthropic_stream(("text", "Done."), stop_reason="end_turn"),
    ]
    messages: list[dict] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "make a file"}]

    step = await run_call(model, messages, tools=tools, tool_choice="auto", parallel_tool_calls=False)
    ids = [call.id for call in step.tool_calls]
    messages += llm.assistant_turn(step, ids)
    messages.append(llm.tool_result_message(ids[0], {"ok": True, "filename": "Q3.md"}))
    await run_call(model, messages, tools=tools, tool_choice="none")

    first, follow_up = real_litellm.bodies
    assert first["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert first["tools"][0]["eager_input_streaming"] is True
    assert follow_up["tool_choice"] == {"type": "none"}
    assert follow_up["tools"] == first["tools"] and follow_up["system"] == first["system"]
    assistant, results = follow_up["messages"][1], follow_up["messages"][2]
    assert _block_types(assistant) == ["thinking", "text", "thinking", "tool_use"]
    thinking, text, empty, tool = assistant["content"]
    assert (thinking["thinking"], thinking["signature"]) == ("Plan: write the report.", "SIG-1")
    assert text["text"] == "Sure." and (empty["thinking"], empty["signature"]) == ("", "SIG-2")
    assert (tool["id"], tool["name"], tool["input"]) == ("toolu_A", "create_document", json.loads(arguments))
    assert results["role"] == "user" and _block_types(results) == ["tool_result"]
    assert results["content"][0]["tool_use_id"] == "toolu_A"


async def test_litellm_replays_two_tool_calls_with_their_thinking(real_litellm: FakeProvider) -> None:
    model = chat_model(api_base=real_litellm.base)
    real_litellm.responses += [
        anthropic_stream(
            ("thinking", (["one"], "S1")),
            ("tool", ("toolu_A", '{"title": "a"}')),
            ("thinking", (["two"], "S2")),
            ("tool", ("toolu_B", '{"title": "b"}')),
        ),
        anthropic_stream(("text", "Done."), stop_reason="end_turn"),
    ]
    messages: list[dict] = [{"role": "user", "content": "two files"}]

    step = await run_call(model, messages, tools=TOOLS, tool_choice="auto")
    ids = [call.id for call in step.tool_calls]
    messages += llm.assistant_turn(step, ids)
    messages += [llm.tool_result_message(call_id, {"ok": True}) for call_id in ids]
    await run_call(model, messages, tools=TOOLS, tool_choice="none")

    assistant, results = real_litellm.bodies[1]["messages"][1:3]
    assert _block_types(assistant) == ["thinking", "tool_use", "thinking", "tool_use"]
    assert [block["id"] for block in assistant["content"] if block["type"] == "tool_use"] == ["toolu_A", "toolu_B"]
    assert [block["tool_use_id"] for block in results["content"]] == ["toolu_A", "toolu_B"]


async def test_invalid_arguments_are_echoed_as_an_empty_object_without_leaking(
    real_litellm: FakeProvider, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    model = chat_model(api_base=real_litellm.base)
    secret = '{"title": "SECRET-ARGUMENTS", "content": "a"b'
    real_litellm.responses += [
        anthropic_stream(("tool", ("toolu_A", secret))),
        anthropic_stream(("text", "Sorry."), stop_reason="end_turn"),
    ]
    litellm_logger = logging.getLogger("LiteLLM")
    litellm_logger.addHandler(caplog.handler)
    messages: list[dict] = [{"role": "user", "content": "a file"}]
    try:
        with caplog.at_level(logging.DEBUG, logger="app"):
            step = await run_call(model, messages, tools=TOOLS, tool_choice="auto")
            messages += llm.assistant_turn(step, [step.tool_calls[0].id])
            messages.append(llm.tool_result_message(step.tool_calls[0].id, {"ok": False, "error": "not JSON"}))
            await run_call(model, messages, tools=TOOLS, tool_choice="none")
    finally:
        litellm_logger.removeHandler(caplog.handler)

    [tool] = [block for block in real_litellm.bodies[1]["messages"][1]["content"] if block["type"] == "tool_use"]
    assert tool["input"] == {}
    captured = capfd.readouterr()
    assert "SECRET-ARGUMENTS" not in caplog.text + captured.out + captured.err


async def test_litellm_sends_the_openai_flags(real_litellm: FakeProvider) -> None:
    model = chat_model(MINI, api_base=real_litellm.base + "/v1")
    tools = [document_tool.tool_spec(model)]
    real_litellm.responses += [openai_stream('{"title": "a"}'), openai_stream()]
    messages: list[dict] = [{"role": "user", "content": "a file"}]

    step = await run_call(model, messages, tools=tools, tool_choice="auto", parallel_tool_calls=False)
    messages += llm.assistant_turn(step, [step.tool_calls[0].id])
    messages.append(llm.tool_result_message(step.tool_calls[0].id, {"ok": True}))
    await run_call(model, messages, tools=tools, tool_choice="none")

    first, follow_up = real_litellm.bodies
    assert (first["tool_choice"], first["parallel_tool_calls"]) == ("auto", False)
    assert "eager_input_streaming" not in json.dumps(first["tools"])
    assert follow_up["tool_choice"] == "none" and "parallel_tool_calls" not in follow_up
    assert follow_up["messages"][-1] == {"role": "tool", "tool_call_id": "call_0", "content": '{"ok": true}'}
    assert follow_up["messages"][-2]["tool_calls"][0]["function"]["arguments"] == '{"title": "a"}'
