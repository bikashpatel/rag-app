"""Thin LiteLLM wrappers. All model calls in the app go through this module."""

import logging
import math
import os
import time
from base64 import b64encode
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import litellm

from app.config import settings
from app.models import LLMModel

logger = logging.getLogger(__name__)

litellm.drop_params = True  # silently drop params a provider doesn't support
litellm.suppress_debug_info = True
if hasattr(litellm, "telemetry"):
    litellm.telemetry = False
# LiteLLM logs every call at INFO through a handler of its own and again through the root logger.
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("LiteLLM").propagate = False

# Sent to a custom api_base without a configured key: OpenAI-compatible clients need a non-empty one, and servers
# without authentication ignore it.
_NO_API_KEY = "unused"


class LLMError(Exception):
    """A provider call failed. str(exc) is safe to show to end users."""


class VisionCallError(LLMError):
    """A vision call failed. `retryable`: worth retrying (rate limits, overloaded or unavailable provider, dropped
    connections, but not timeouts), after `retry_after` seconds if the provider said so. `fatal`: every other page
    would fail the same way (the key or the model is wrong)."""

    def __init__(
        self, message: str, *, retryable: bool = False, retry_after: float | None = None, fatal: bool = False
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.fatal = fatal


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


@dataclass
class VisionReply:
    text: str
    usage: Usage
    truncated: bool  # the model stopped at max_tokens


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
    """LiteLLM arguments for the model. Only the key named in api_key_env ever goes to a custom api_base: left to
    itself, LiteLLM falls back to the provider's default key (OPENAI_API_KEY, ...) and sends it to that host."""
    kwargs: dict[str, Any] = {"model": model.litellm_model, "timeout": settings.llm_timeout_seconds}
    if model.api_base:
        kwargs["api_base"] = model.api_base
    if model.api_key_env:
        key = os.environ.get(model.api_key_env)
        if not key:  # never fall back to another key
            raise LLMError(
                f"This model's API key ({model.api_key_env}) is not set on the server. "
                "Add it to .env and run docker compose up -d."
            )
        kwargs["api_key"] = key
    elif model.api_base:
        kwargs["api_key"] = _NO_API_KEY
    return kwargs


def resolve_max_tokens(model: LLMModel, default: int | None = None) -> int:
    """The model's own max_output_tokens, else `default` (DEFAULT_MAX_OUTPUT_TOKENS when None) lowered to the model's
    known output cap (providers reject a larger max_tokens outright)."""
    if model.max_output_tokens:
        return model.max_output_tokens
    cap = _known_output_cap(model.litellm_model)
    if default is None:
        default = settings.default_max_output_tokens
    return min(default, cap) if cap else default


def _known_output_cap(litellm_model: str) -> int | None:
    """The output-token cap in LiteLLM's bundled model map."""
    cap = _model_map_entry(litellm_model).get("max_output_tokens")
    return int(cap) if isinstance(cap, int | float) and cap > 0 else None


def listed_with_vision(litellm_model: str) -> bool:
    """Whether LiteLLM's bundled model map says the model reads images."""
    return _model_map_entry(litellm_model).get("supports_vision") is True


def _model_map_entry(litellm_model: str) -> dict[str, Any]:
    """The model's entry in LiteLLM's bundled model map, or {}. A plain lookup: litellm.get_model_info() can make
    blocking calls to some providers' servers."""
    provider, _, name = litellm_model.partition("/") if "/" in litellm_model else ("openai", "", litellm_model)
    for key in (litellm_model, name):
        info = litellm.model_cost.get(key)
        if isinstance(info, dict) and info.get("litellm_provider") == provider:
            return info
    return {}


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


async def stream_chat(model: LLMModel, messages: list[dict], usage: Usage) -> AsyncGenerator[str]:
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
        max_tokens=resolve_max_tokens(model),
    )
    if model.temperature is not None:
        kwargs["temperature"] = model.temperature

    parts: list[str] = []
    reported: tuple[int, int] | None = None
    response: Any = None
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
        await _close_stream(response)


async def _close_stream(stream: Any) -> None:
    """Release the provider connection, so a stream the consumer abandoned stops generating (and billing)."""
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:
        logger.warning("Could not close the chat stream: %s", exc)


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


# Claude 5.x reads an image as about one token per 28x28-pixel patch.
_IMAGE_PIXELS_PER_TOKEN = 784
_RETRYABLE = (
    litellm.RateLimitError,
    litellm.InternalServerError,  # including Anthropic's "overloaded"
    litellm.ServiceUnavailableError,
    litellm.APIConnectionError,
)
_FATAL = (litellm.AuthenticationError, litellm.PermissionDeniedError, litellm.NotFoundError)
_MAX_RETRY_AFTER_SECONDS = 3600.0


async def read_image(
    model: LLMModel, jpeg: bytes, *, system: str, instruction: str, image_size: tuple[int, int]
) -> VisionReply:
    """One vision call, made as one HTTP request (no retries): the image, then the instruction. Thinking is kept low,
    since it counts toward max_tokens and is billed as output. Failures are raised as VisionCallError."""
    try:
        kwargs = _base_kwargs(model)
    except LLMError as exc:  # the model's key isn't set: every page would fail
        raise VisionCallError(str(exc), fatal=True) from exc
    kwargs.update(
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64encode(jpeg).decode()}},
                    {"type": "text", "text": instruction},
                ],
            },
        ],
        max_tokens=vision_max_tokens(model),
        reasoning_effort="low",
        # Exactly one HTTP request: the OpenAI SDK (openai/* and OpenAI-compatible hosts) otherwise retries 429s,
        # 5xx and timeouts twice inside the call. vision.read_pages owns retries, delays and Retry-After.
        max_retries=0,
    )
    if model.temperature is not None:  # null by default: Claude 5.5 models reject a non-default temperature
        kwargs["temperature"] = model.temperature
    try:
        response = await litellm.acompletion(**kwargs)
    except Exception as exc:
        logger.warning("Vision call failed for %s: %s", model.litellm_model, _short(str(exc), 300))
        raise _vision_error(exc) from exc

    choices = _get(response, "choices") or []
    choice = choices[0] if choices else None
    content = _get(_get(choice, "message"), "content")
    text = content if isinstance(content, str) else ""
    reported = _get(response, "usage")
    usage = Usage(
        prompt_tokens=int(_get(reported, "prompt_tokens", 0) or 0),
        completion_tokens=int(_get(reported, "completion_tokens", 0) or 0),
    )
    if not usage.total_tokens:
        # Never hand the image to token_counter, which decodes images: the text parts plus the image's patches.
        width, height = image_size
        prompt = [{"role": "system", "content": system}, {"role": "user", "content": instruction}]
        usage.prompt_tokens = count_tokens(model, messages=prompt) + math.ceil(width * height / _IMAGE_PIXELS_PER_TOKEN)
        usage.completion_tokens = count_tokens(model, text=text) if text else 0
        usage.estimated = True
    usage.cost_usd = compute_cost(model, usage.prompt_tokens, usage.completion_tokens)
    return VisionReply(text=text, usage=usage, truncated=_get(choice, "finish_reason") == "length")


def vision_max_tokens(model: LLMModel) -> int:
    """max_tokens of a vision call, thinking included: the model's own, else VISION_MAX_OUTPUT_TOKENS."""
    _allow_effort(model.litellm_model)  # first, so the value never changes after the first call
    return resolve_max_tokens(model, settings.vision_max_output_tokens)


def _vision_error(exc: Exception) -> VisionCallError:
    retryable = isinstance(exc, _RETRYABLE) and not isinstance(exc, litellm.Timeout)
    return VisionCallError(
        friendly_error(exc),
        retryable=retryable,
        retry_after=_retry_after(exc) if retryable else None,
        fatal=isinstance(exc, _FATAL),
    )


def _retry_after(exc: Exception) -> float | None:
    """Seconds from the provider's Retry-After header (LiteLLM keeps the vendor's response on the exception)."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        if (milliseconds := headers.get("retry-after-ms")) is not None:
            seconds = float(milliseconds) / 1000
        elif (value := headers.get("retry-after")) is not None:
            try:
                seconds = float(value)
            except ValueError:  # an HTTP date
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
        else:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS) if math.isfinite(seconds) else None


def _allow_effort(litellm_model: str) -> None:
    """Let reasoning_effort reach Claude models that think adaptively but are missing from LiteLLM's model map
    (claude-sonnet-5-5 in LiteLLM 1.103.1). LiteLLM sends their effort as output_config.effort only when the map
    says they support it, and drop_params removes it otherwise, so they would think at their default effort (high).
    Every adaptive-thinking Claude model accepts the effort setting. The registered entry gets LiteLLM's baseline for
    unknown Claude models, so their output cap becomes 64000 tokens (above every default here)."""
    provider, _, name = litellm_model.partition("/")
    if provider != "anthropic" or not name or name in litellm.model_cost or litellm_model in litellm.model_cost:
        return
    try:
        from litellm.llms.anthropic.common_utils import AnthropicModelInfo

        if AnthropicModelInfo._is_adaptive_thinking_model(name, "anthropic"):
            litellm.register_model(
                {name: {"litellm_provider": "anthropic", "mode": "chat", "supports_output_config": True}},
                persist_across_reloads=False,
            )
    except Exception as exc:  # a LiteLLM upgrade moved this: the effort is dropped, nothing else changes
        logger.warning("Could not enable reasoning_effort for %s: %s", litellm_model, exc)


async def test_model(model: LLMModel) -> dict[str, Any]:
    """Make a tiny real call. Returns the ModelTestResult shape from SPEC.md."""
    if model.kind == "vision":
        from app.services.vision import test_vision_model  # vision imports this module

        return await test_vision_model(model)
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
        # The max_tokens chats send, so the test fails when the provider would reject it (the reply is one word).
        prompt = [{"role": "user", "content": "Reply with the single word: OK"}]
        kwargs.update(messages=prompt, max_tokens=resolve_max_tokens(model))
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
