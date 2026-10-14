"""Thin LiteLLM wrappers. All model calls in the app go through this module."""

import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import litellm

from app.config import settings
from app.models import LLMModel

logger = logging.getLogger(__name__)

litellm.drop_params = True  # silently drop params a provider doesn't support
litellm.suppress_debug_info = True
if hasattr(litellm, "telemetry"):
    litellm.telemetry = False


class LLMError(Exception):
    """A provider call failed. str(exc) is safe to show to end users."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    usage: Usage


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, LLMError):
        return str(exc)
    if isinstance(exc, litellm.AuthenticationError):
        return "The model provider rejected the API key. Check the provider key in .env."
    if isinstance(exc, litellm.RateLimitError):
        return "The model provider is rate limiting requests. Please try again shortly."
    if isinstance(exc, litellm.ContextWindowExceededError):
        return "The conversation is too long for this model's context window. Start a new chat."
    if isinstance(exc, litellm.NotFoundError):
        return "The model was not found at the provider. Check the model name in the admin page."
    if isinstance(exc, litellm.Timeout):
        return "The model provider timed out."
    if isinstance(exc, litellm.APIConnectionError):
        return "Could not connect to the model provider."
    if isinstance(exc, litellm.BadRequestError):
        return f"The model provider rejected the request: {_short(str(exc))}"
    return f"Model call failed: {_short(str(exc))}"


def _base_kwargs(model: LLMModel) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model": model.litellm_model, "timeout": settings.llm_timeout_seconds}
    if model.api_base:
        kwargs["api_base"] = model.api_base
    if model.api_key_env:
        key = os.environ.get(model.api_key_env)
        if key:
            kwargs["api_key"] = key
    return kwargs


def compute_cost(model: LLMModel, prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost. Explicit per-1M prices on the model win; else LiteLLM's cost map; else 0."""
    if model.input_cost_per_mtok is not None or model.output_cost_per_mtok is not None:
        return (
            prompt_tokens * (model.input_cost_per_mtok or 0.0)
            + completion_tokens * (model.output_cost_per_mtok or 0.0)
        ) / 1_000_000
    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model.litellm_model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )
        return float(prompt_cost) + float(completion_cost)
    except Exception:
        return 0.0


def count_tokens(model: LLMModel, *, messages: list[dict] | None = None, text: str | None = None) -> int:
    try:
        if messages is not None:
            return int(litellm.token_counter(model=model.litellm_model, messages=messages))
        return int(litellm.token_counter(model=model.litellm_model, text=text or ""))
    except Exception:
        chars = sum(len(str(m.get("content") or "")) for m in messages) if messages is not None else len(text or "")
        return max(1, chars // 4)


def _fill_usage(
    usage: Usage, model: LLMModel, messages: list[dict], completion: str, reported: tuple[int, int] | None
) -> None:
    if reported is not None:
        usage.prompt_tokens, usage.completion_tokens = reported
        usage.estimated = False
    else:
        usage.prompt_tokens = count_tokens(model, messages=messages)
        usage.completion_tokens = count_tokens(model, text=completion) if completion else 0
        usage.estimated = True
    usage.cost_usd = compute_cost(model, usage.prompt_tokens, usage.completion_tokens)


async def stream_chat(model: LLMModel, messages: list[dict], usage: Usage) -> AsyncIterator[str]:
    """Stream text deltas from a chat model.

    `usage` is filled in when the generator finishes — normally, on error, or when the
    consumer stops early (provider-reported numbers if available, else estimates).
    Provider failures are raised as LLMError with a user-safe message.
    """
    kwargs = _base_kwargs(model)
    kwargs.update(
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        max_tokens=model.max_output_tokens or settings.default_max_output_tokens,
    )
    if model.temperature is not None:
        kwargs["temperature"] = model.temperature

    parts: list[str] = []
    reported: tuple[int, int] | None = None
    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            chunk_usage = _get(chunk, "usage")
            if chunk_usage is not None:
                pt = int(_get(chunk_usage, "prompt_tokens", 0) or 0)
                ct = int(_get(chunk_usage, "completion_tokens", 0) or 0)
                if pt or ct:
                    reported = (pt, ct)
            choices = _get(chunk, "choices") or []
            if not choices:
                continue
            text = _get(_get(choices[0], "delta"), "content")
            if text:
                parts.append(text)
                yield text
    except Exception as exc:
        logger.warning("Chat completion failed for %s: %s", model.litellm_model, exc)
        raise LLMError(friendly_error(exc)) from exc
    finally:
        _fill_usage(usage, model, messages, "".join(parts), reported)


async def embed_texts(model: LLMModel, texts: list[str]) -> EmbeddingResult:
    """Embed a batch of texts. Returns vectors in input order plus usage."""
    if not texts:
        return EmbeddingResult(vectors=[], usage=Usage())
    kwargs = _base_kwargs(model)
    kwargs["input"] = texts
    try:
        response = await litellm.aembedding(**kwargs)
    except Exception as exc:
        logger.warning("Embedding failed for %s: %s", model.litellm_model, exc)
        raise LLMError(friendly_error(exc)) from exc

    items: list[tuple[int, list[float]]] = []
    for position, item in enumerate(_get(response, "data") or []):
        index = _get(item, "index", position)
        vector = _get(item, "embedding")
        items.append((position if index is None else int(index), vector))
    items.sort(key=lambda pair: pair[0])
    vectors = [[float(x) for x in vector] for _, vector in items if vector]
    if len(vectors) != len(texts):
        raise LLMError("The embedding provider returned an unexpected response.")

    prompt_tokens = int(_get(_get(response, "usage"), "prompt_tokens", 0) or 0)
    estimated = False
    if not prompt_tokens:
        prompt_tokens = sum(max(1, len(t) // 4) for t in texts)
        estimated = True
    usage = Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
        cost_usd=compute_cost(model, prompt_tokens, 0),
        estimated=estimated,
    )
    return EmbeddingResult(vectors=vectors, usage=usage)


async def test_model(model: LLMModel) -> dict[str, Any]:
    """Make a tiny real call. Returns the ModelTestResult shape from SPEC.md."""
    start = time.perf_counter()
    try:
        if model.kind == "embedding":
            result = await embed_texts(model, ["connection test"])
            dim = len(result.vectors[0])
            return {
                "ok": True,
                "message": f"OK: returned {dim}-dimensional embeddings",
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "embedding_dim": dim,
            }
        kwargs = _base_kwargs(model)
        kwargs.update(messages=[{"role": "user", "content": "Reply with the single word: OK"}], max_tokens=256)
        if model.temperature is not None:
            kwargs["temperature"] = model.temperature
        response = await litellm.acompletion(**kwargs)
        choices = _get(response, "choices") or []
        text = _get(_get(choices[0], "message"), "content") if choices else None
        return {
            "ok": True,
            "message": f"OK: model replied {_short(text or '(empty reply)', 80)!r}",
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "embedding_dim": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": friendly_error(exc),
            "latency_ms": int((time.perf_counter() - start) * 1000),
            "embedding_dim": None,
        }
