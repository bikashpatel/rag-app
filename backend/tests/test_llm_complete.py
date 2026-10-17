"""llm.complete, the request check's call (docs/briefs/plugins-gmail.md, section 4.5): one request without tools or SDK
retries, low reasoning effort, max_tokens capped at the check's limit, usage reported or estimated; and what the real
LiteLLM sends to Anthropic and OpenAI (its HTTP layer replaced, no network)."""

import json
from typing import Any

import litellm
import pytest
from conftest import FakeLLM

from app.models import LLMModel
from app.plugins import intent
from app.services import llm

SONNET = "anthropic/claude-sonnet-5-5"  # adaptive thinking, missing from LiteLLM 1.103.1's model map
MINI = "openai/gpt-5.4-mini"
MESSAGES = [{"role": "system", "content": "Classify."}, {"role": "user", "content": "Draft a reply."}]


def _model(name: str, max_output_tokens: int | None = None, temperature: float | None = None) -> LLMModel:
    return LLMModel(
        display_name=name,
        litellm_model=name,
        kind="chat",
        api_key_env=None,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )


async def test_complete_sends_one_request_without_tools(fake_llm: FakeLLM) -> None:
    fake_llm.reply = '{"draft": true}'
    text, usage, finish = await llm.complete(_model(MINI), MESSAGES, output_cap=4096)
    assert (text, finish) == ('{"draft": true}', "stop")
    assert (usage.prompt_tokens, usage.completion_tokens, usage.estimated) == (5, 1, False)
    [call] = fake_llm.chat_calls
    assert call["max_retries"] == 0 and call["reasoning_effort"] == "low" and call["timeout"] == 30
    assert call["max_tokens"] == 4096
    assert not call.get("stream") and "tools" not in call and "tool_choice" not in call
    assert "temperature" not in call  # Claude 5.5 models reject a custom one; only a configured one is sent


@pytest.mark.parametrize(("own", "expected"), [(None, 4096), (1000, 1000), (64_000, 4096)])
async def test_max_tokens_is_the_lower_of_the_model_limit_and_the_cap(
    fake_llm: FakeLLM, own: int | None, expected: int
) -> None:
    await llm.complete(_model(SONNET, own, temperature=0.2), MESSAGES, output_cap=intent.INTENT_MAX_TOKENS)
    assert fake_llm.chat_calls[-1]["max_tokens"] == expected
    assert fake_llm.chat_calls[-1]["temperature"] == 0.2


async def test_usage_is_estimated_when_not_reported(fake_llm: FakeLLM) -> None:
    fake_llm.intent_usage = None
    fake_llm.intent_reply = '{"file": false}'
    messages = intent.build_messages(latest="Hello", earlier=[], previous_reply=None)
    text, usage, _ = await llm.complete(_model(MINI), messages, output_cap=4096)
    assert text == '{"file": false}'
    assert usage.estimated and usage.prompt_tokens > 0 and usage.completion_tokens > 0


async def test_failures_raise_llm_errors(fake_llm: FakeLLM) -> None:
    fake_llm.fail_after = 0
    fake_llm.error = litellm.RateLimitError(message="slow down", llm_provider="openai", model="gpt-5.4-mini")
    with pytest.raises(llm.LLMError, match="rate limiting"):
        await llm.complete(_model(MINI), MESSAGES, output_cap=4096)


@pytest.mark.filterwarnings("ignore:Item 'summary' on TypedDict:UserWarning")  # from LiteLLM's response types
async def test_real_litellm_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiteLLM's own request transformation for an adaptive-thinking Claude 5.5 model missing from its map and for
    GPT-5.4 mini, with the HTTP layer replaced."""
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
    from litellm.llms.openai.openai import OpenAIChatCompletion

    bodies: dict[str, dict[str, Any]] = {}

    class Sent(Exception):
        pass

    async def post(self: Any, url: str, data: Any = None, **kwargs: Any) -> Any:
        bodies[url] = kwargs["json"] if kwargs.get("json") is not None else json.loads(data)
        raise Sent

    async def openai_request(self: Any, openai_aclient: Any, data: dict[str, Any], **kwargs: Any) -> Any:
        bodies["openai"] = data
        raise Sent

    monkeypatch.setattr(litellm, "acompletion", litellm.main.acompletion)
    monkeypatch.setattr(AsyncHTTPHandler, "post", post)
    monkeypatch.setattr(OpenAIChatCompletion, "make_openai_chat_completion_request", openai_request)
    messages = intent.build_messages(latest="Draft a reply to Alice.", earlier=[], previous_reply=None)
    for name in (SONNET, MINI):
        with pytest.raises(llm.LLMError):
            await llm.complete(_model(name), messages, output_cap=intent.INTENT_MAX_TOKENS)

    anthropic = next(body for url, body in bodies.items() if "anthropic" in url)
    assert anthropic["thinking"]["type"] == "adaptive"
    assert anthropic["output_config"] == {"effort": "low"}
    assert anthropic["max_tokens"] == 4096
    assert "tools" not in anthropic and not anthropic.get("stream")
    openai = bodies["openai"]
    assert (openai["reasoning_effort"], openai["max_completion_tokens"]) == ("low", 4096)
    assert "tools" not in openai and not openai.get("stream")
