"""The LiteLLM wrappers (app.services.llm): which key and which max_tokens each call sends."""

from typing import Any

import pytest
from conftest import FakeLLM

from app.config import settings
from app.models import LLMModel
from app.services import llm


def chat_model(litellm_model: str = "openai/gpt-5.5", **fields: Any) -> LLMModel:
    return LLMModel(litellm_model=litellm_model, display_name="Model", kind="chat", **fields)


async def streamed_call(model: LLMModel, fake_llm: FakeLLM) -> dict[str, Any]:
    async for _ in llm.stream_chat(model, [{"role": "user", "content": "Hi"}], llm.Usage()):
        pass
    return fake_llm.chat_calls[-1]


# ---------------- API keys ----------------


async def test_a_custom_endpoint_never_gets_the_providers_default_key(
    fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")
    model = chat_model("openai/local-model", api_base="http://127.0.0.1:8001/v1")

    result = await llm.test_model(model)

    assert result["ok"] is True
    call = fake_llm.chat_calls[-1]
    assert (call["api_base"], call["api_key"]) == ("http://127.0.0.1:8001/v1", "unused")


async def test_a_custom_endpoint_gets_the_key_named_for_it(fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "local-key")
    model = chat_model("openai/local-model", api_base="http://127.0.0.1:8001/v1", api_key_env="LOCAL_API_KEY")

    await streamed_call(model, fake_llm)

    assert fake_llm.chat_calls[-1]["api_key"] == "local-key"


async def test_the_provider_endpoint_uses_the_providers_default_key(fake_llm: FakeLLM) -> None:
    await streamed_call(chat_model(), fake_llm)

    assert "api_key" not in fake_llm.chat_calls[-1]  # LiteLLM reads OPENAI_API_KEY itself


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
async def test_a_named_key_that_is_missing_is_reported_instead_of_replaced(
    value: str | None, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    if value is None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GROQ_API_KEY", value)
    model = chat_model("openai/llama-3.3-70b", api_base="https://api.groq.example/v1", api_key_env="GROQ_API_KEY")
    embedding = LLMModel(litellm_model="openai/embed", display_name="E", kind="embedding", api_key_env="GROQ_API_KEY")

    result = await llm.test_model(model)
    with pytest.raises(llm.LLMError) as embedding_error:
        await llm.embed_texts(embedding, ["text"])

    expected = (
        "This model's API key (GROQ_API_KEY) is not set on the server. Add it to .env and run docker compose up -d."
    )
    assert (result["ok"], result["message"]) == (False, expected)
    assert str(embedding_error.value) == expected
    assert fake_llm.chat_calls == [] and fake_llm.embedding_calls == []


# ---------------- max_tokens ----------------


@pytest.mark.parametrize(
    ("litellm_model", "max_output_tokens", "expected"),
    [
        ("openai/gpt-3.5-turbo", None, 4096),  # LiteLLM's map lists a 4096-token output cap
        ("deepseek/deepseek-chat", None, 8192),
        ("openai/gpt-5.5", None, 16000),  # its cap is higher than the server default
        ("openai/some-unknown-model", None, 16000),
        ("openai/gpt-3.5-turbo", 1000, 1000),  # the admin's setting wins
        ("openai/some-unknown-model", 64000, 64000),
    ],
)
async def test_max_tokens_never_exceeds_the_models_known_cap(
    litellm_model: str, max_output_tokens: int | None, expected: int, fake_llm: FakeLLM
) -> None:
    assert settings.default_max_output_tokens == 16000
    model = chat_model(litellm_model, max_output_tokens=max_output_tokens)

    streamed = await streamed_call(model, fake_llm)
    await llm.test_model(model)

    assert streamed["max_tokens"] == expected
    assert fake_llm.chat_calls[-1]["max_tokens"] == expected  # the admin's Test sends what chats send
