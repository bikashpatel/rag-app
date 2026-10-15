"""Thin LiteLLM wrappers. All model calls in the app go through this module."""

import asyncio
import json
import logging
import math
import os
import time
from base64 import b64encode
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
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
    """A provider call failed. str(exc) is safe to show to end users.

    `bad_request`: the provider rejected the request itself (a BadRequestError other than a context-window error),
    which is decided before any output. `context_window`: the messages and max_tokens don't fit the model's context
    window (ContextWindowExceededError). `detail`: the start of the provider's message, for recovery checks and logs
    only; never shown or stored.
    """

    def __init__(
        self, message: str, *, bad_request: bool = False, context_window: bool = False, detail: str = ""
    ) -> None:
        super().__init__(message)
        self.bad_request = bad_request
        self.context_window = context_window
        self.detail = detail


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

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost_usd += other.cost_usd
        self.estimated = self.estimated or other.estimated


class ToolCall:
    """A tool call of a streamed response. `index` is its position in ChatStep.tool_calls, `id` the provider's id
    ("" if it sent none), `arguments` the raw JSON text, possibly incomplete when the response stopped early."""

    def __init__(self, index: int, id: str, name: str, arguments: str = "") -> None:
        self.index = index
        self.id = id
        self.name = name
        self._fragments = [arguments]

    @property
    def arguments(self) -> str:
        if len(self._fragments) > 1:  # joined only when read: a long file's arguments arrive in thousands of pieces
            self._fragments = ["".join(self._fragments)]
        return self._fragments[0]

    @arguments.setter
    def arguments(self, value: str) -> None:
        self._fragments = [value]

    def append(self, fragment: str) -> None:
        self._fragments.append(fragment)

    def __repr__(self) -> str:
        return f"ToolCall(index={self.index}, id={self.id!r}, name={self.name!r}, {len(self.arguments)} characters)"


@dataclass(frozen=True)
class ToolCallStarted:
    """Yielded by stream_chat once a tool call's name is known."""

    index: int
    name: str


@dataclass(frozen=True)
class ToolCallProgress:
    """Yielded by stream_chat after each argument fragment of a started tool call."""

    index: int


@dataclass
class ChatStep:
    """One model response, filled in while stream_chat runs and complete when it finishes, on every path.

    `sequence` keeps the response's parts in arrival order: ("thinking", signed or redacted block), ("text", raw
    text, NUL characters included) and ("tool", index into tool_calls). The follow-up request must replay them
    in that order (see assistant_turn).
    """

    tool_calls: list[ToolCall] = field(default_factory=list)
    sequence: list[tuple[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    received: bool = False  # any chunk arrived
    _positions: dict[int, int] = field(default_factory=dict, repr=False)  # provider index -> position

    @property
    def thinking_blocks(self) -> list[dict[str, Any]]:
        return [item for kind, item in self.sequence if kind == "thinking"]


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
        chars = sum(_message_chars(m) for m in messages) if messages is not None else len(text or "")
        return max(1, chars // 4)


def _message_chars(message: dict) -> int:
    arguments = (_get(_get(call, "function"), "arguments") or "" for call in message.get("tool_calls") or [])
    return len(str(message.get("content") or "")) + sum(len(str(text)) for text in arguments)


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


async def stream_chat(
    model: LLMModel,
    messages: list[dict],
    usage: Usage,
    *,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
    parallel_tool_calls: bool | None = None,
    max_tokens: int | None = None,
    step: ChatStep | None = None,
) -> AsyncGenerator[str | ToolCallStarted | ToolCallProgress]:
    """Stream text deltas from a chat model; with `tools`, also ToolCallStarted and ToolCallProgress events.

    `usage` is filled in when the generator finishes — normally, on error, or when the
    consumer stops early (provider-reported numbers if available, else estimates). `step` records the whole
    response (thinking blocks, text and tool calls in order, the finish reason).
    Provider failures are raised as LLMError with a user-safe message.
    """
    kwargs = _base_kwargs(model)
    kwargs.update(
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
        max_tokens=max_tokens if max_tokens is not None else resolve_max_tokens(model),
    )
    if model.temperature is not None:
        kwargs["temperature"] = model.temperature
    for name, value in (("tools", tools), ("tool_choice", tool_choice), ("parallel_tool_calls", parallel_tool_calls)):
        if value is not None:
            kwargs[name] = value

    step = step if step is not None else ChatStep()
    parts: list[str] = []
    text_start = 0  # parts[text_start:] is the text since the last thinking block or tool call

    def end_text() -> None:  # one ("text", ...) item per run of text, joined once (a long answer has many deltas)
        nonlocal text_start
        if len(parts) > text_start:
            step.sequence.append(("text", "".join(parts[text_start:])))
            text_start = len(parts)

    reported: tuple[int, int] | None = None
    response: Any = None
    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            step.received = True
            chunk_usage = _get(chunk, "usage")
            if chunk_usage is not None:
                pt = int(_get(chunk_usage, "prompt_tokens", 0) or 0)
                ct = int(_get(chunk_usage, "completion_tokens", 0) or 0)
                if pt or ct:
                    reported = (pt, ct)
            choices = _get(chunk, "choices") or []
            if not choices:
                continue
            if (finish_reason := _get(choices[0], "finish_reason")) is not None:
                step.finish_reason = finish_reason
            delta = _get(choices[0], "delta")
            for block in _get(delta, "thinking_blocks") or []:
                block_type = _get(block, "type")
                if isinstance(block, dict) and (
                    block_type == "redacted_thinking" or (block_type == "thinking" and block.get("signature"))
                ):
                    end_text()
                    step.sequence.append(("thinking", dict(block)))
            text = _get(delta, "content")
            if text:
                parts.append(text)
                yield text
            for item in _get(delta, "tool_calls") or []:
                end_text()
                for event in _add_tool_delta(step, item):
                    if tools:
                        yield event
    except Exception as exc:
        logger.warning("Chat completion failed for %s: %s", model.litellm_model, _short(str(exc), 300))
        context_window = isinstance(exc, litellm.ContextWindowExceededError)
        bad_request = isinstance(exc, litellm.BadRequestError) and not context_window
        raise LLMError(
            friendly_error(exc), bad_request=bad_request, context_window=context_window, detail=_short(str(exc), 300)
        ) from exc
    finally:
        end_text()
        completion = "".join(parts) + "".join(call.arguments for call in step.tool_calls)
        _fill_usage(usage, model, messages, completion, reported)
        await _close_stream(response)


def _add_tool_delta(step: ChatStep, item: Any) -> list[ToolCallStarted | ToolCallProgress]:
    """Add one streamed tool-call fragment to `step`. Fragments belong to the call with the same index (or, without
    an index, the same non-empty id); anything else starts a new call."""
    index, call_id = _get(item, "index"), _get(item, "id") or ""
    function = _get(item, "function")
    name, fragment = _get(function, "name") or "", _get(function, "arguments") or ""
    if index is not None:
        position = step._positions.get(index)
    else:
        position = next((call.index for call in step.tool_calls if call_id and call.id == call_id), None)
    events: list[ToolCallStarted | ToolCallProgress] = []
    if position is None:
        call = ToolCall(len(step.tool_calls), call_id, name)
        step.tool_calls.append(call)
        step.sequence.append(("tool", call.index))
        if index is not None:
            step._positions[index] = call.index
        if name:
            events.append(ToolCallStarted(call.index, name))
    else:
        call = step.tool_calls[position]
        call.id = call.id or call_id
        if name and not call.name:
            call.name = name
            events.append(ToolCallStarted(call.index, name))
    if fragment:
        call.append(fragment)
        if call.name:
            events.append(ToolCallProgress(call.index))
    return events


def _not_json(constant: str) -> Any:
    raise ValueError(f"{constant} isn't JSON")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is out of range")
    return value


def loads_json(text: str) -> Any:
    """json.loads without Python's extensions: NaN, Infinity and numbers too large for a float raise ValueError. Other
    JSON parsers reject them, and LiteLLM would send them on to the provider as invalid JSON."""
    return json.loads(text, parse_constant=_not_json, parse_float=_finite_float)


def _echo_arguments(arguments: str) -> str:
    """The arguments as sent back to the provider: unchanged when they are a JSON object, else "{}". LiteLLM parses
    echoed arguments and puts all of them into the error it raises when it can't."""
    try:
        return arguments if isinstance(loads_json(arguments), dict) else "{}"
    except (ValueError, RecursionError):
        return "{}"


def assistant_turn(step: ChatStep, ids: list[str]) -> list[dict]:
    """The response as assistant messages for the next request; ids[i] is the id to use for step.tool_calls[i].

    Without thinking blocks, one message. With them (Anthropic), consecutive messages ("segments") that LiteLLM
    merges into one assistant message in order: a new segment starts when a thinking block follows text or a tool
    call, or text follows a tool call. Anthropic binds each thinking block to everything before it, so the original
    order must be kept.
    """

    def tool_call(position: int) -> dict:
        call = step.tool_calls[position]
        return {
            "id": ids[position],
            "type": "function",
            "function": {"name": call.name, "arguments": _echo_arguments(call.arguments)},
        }

    def message(thinking: list[dict], text: str, calls: list[int]) -> dict:
        result: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            result["tool_calls"] = [tool_call(position) for position in calls]
        if thinking:
            result["thinking_blocks"] = thinking
        return result

    if not step.thinking_blocks:
        text = "".join(item for kind, item in step.sequence if kind == "text")
        return [message([], text, [call.index for call in step.tool_calls])]
    segments: list[tuple[list[dict], list[str], list[int]]] = []
    for kind, item in step.sequence:
        current = segments[-1] if segments else None
        if (
            current is None
            or (kind == "thinking" and (current[1] or current[2]))
            or (kind == "text" and current[2])
        ):
            current = ([], [], [])
            segments.append(current)
        if kind == "thinking":
            current[0].append(item)
        elif kind == "text":
            current[1].append(item)
        else:
            current[2].append(item)
    return [message(thinking, "".join(texts), calls) for thinking, texts, calls in segments]


def strip_thinking(messages: list[dict]) -> None:
    """Remove the thinking blocks from the assistant messages in `messages` (in place), and the messages left with
    neither text nor tool calls."""
    for message in messages:
        if message.get("role") == "assistant":
            message.pop("thinking_blocks", None)
    messages[:] = [
        message
        for message in messages
        if message.get("role") != "assistant" or message.get("content") or message.get("tool_calls")
    ]


def tool_result_message(call_id: str, result: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)}


_tool_support: dict[str, bool] = {}  # litellm_model -> litellm.supports_function_calling
_tools_rejected: set[tuple[str, str | None]] = set()  # (litellm_model, api_base) that rejected tools


async def supports_tools(model: LLMModel) -> bool:
    """Whether the model gets tools: LiteLLM says it supports function calling, and it hasn't rejected tools since
    the process started (mark_tools_rejected)."""
    if (model.litellm_model, model.api_base) in _tools_rejected:
        return False
    supported = _tool_support.get(model.litellm_model)
    if supported is None:
        try:  # some providers' model-info lookups are blocking calls
            supported = bool(await asyncio.to_thread(litellm.supports_function_calling, model.litellm_model))
        except Exception:
            supported = False
        _tool_support[model.litellm_model] = supported
    return supported


def mark_tools_rejected(model: LLMModel, reason: str = "") -> None:
    """Remember for the life of the process that the model's endpoint rejects the tool. `reason`: the provider's
    message (LLMError.detail), for the log."""
    key = (model.litellm_model, model.api_base)
    if key not in _tools_rejected:
        _tools_rejected.add(key)
        logger.warning(
            "%s rejected the file tool (%s); it is no longer offered to this model",
            model.litellm_model,
            reason or "no detail",
        )


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
