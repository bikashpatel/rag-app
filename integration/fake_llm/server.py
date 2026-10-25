"""A fake OpenAI-compatible model server for the integration suite: no network, no keys, no cost.

Runs inside the test stack with the backend image (FastAPI and uvicorn are installed there):

    python /fake_llm/server.py --host 0.0.0.0 --port 8000

Models reach it through an `api_base` of http://fake-llm:8000/v1 set in the admin API. Everything is deterministic:

- POST /v1/embeddings: a signed, hashed bag of words, L2-normalised. `fake-embed-a` has 256 dimensions,
  `fake-embed-b` 384 (any other name: 256). A question shares its nearest neighbours with the passages whose words it
  shares. Honours `encoding_format` ("float" or "base64").
- POST /v1/chat/completions, streaming and not, in OpenAI's wire format (usage included, tool calls streamed as
  `tool_calls` deltas). It answers from the <context> block the app puts in the system prompt and cites [1]. When the
  latest user message asks for a file, an image or a Gmail action and the matching tool is offered, it calls that
  tool with arguments built from the tool schema in the request, then answers after the tool result. It also answers
  the plugins' request check (JSON), vision calls (an image part: `vision_reply`) and the admin Test.
- POST /v1/images/generations: a valid PNG of the requested size, as `b64_json`.
- GET /_log?kind=&after=: every request (path, kind, model, messages, tool names, the reported usage); POST /_reset
  clears it and the knobs; POST /_config sets knobs: `vision_reply` (str), `fail_embeddings` (int: the next n
  embedding requests answer 500), `fail_chat` (int), `chunk_delay` (seconds between streamed chunks).
"""

import argparse
import asyncio
import base64
import hashlib
import itertools
import json
import math
import re
import struct
import time
import zlib
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

EMBEDDING_DIMS = {"fake-embed-a": 256, "fake-embed-b": 384}
DEFAULT_DIM = 256
DEFAULT_VISION_REPLY = (
    "Scanned page transcript: the cobalt lighthouse inventory lists 4712 brass lamps kept in the north tower."
)
REQUEST_CHECK_PROMPT = "You decide which actions a user explicitly asked"
TEST_PROMPT = "Reply with the single word: OK"
PROVIDER_KEYS = ("sk-it-fake", "sk-ant-it-fake")

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "the and for are but not you all any can had her was one our out has his how its may new now old see two who "
    "did get let say she too use what when where which while with this that from they them then than there these "
    "those have will would could should about into over under your yours please tell me my is it of to in on at a an "
    "be by as or if so do does document documents file files say says said".split()
)
_CONTEXT = re.compile(r"<context>\n(.*?)\n</context>", re.DOTALL)
_PASSAGE = re.compile(r"^\[(\d+)\] \(([^\n]*)\)\n", re.MULTILINE)
_QUOTED = re.compile(r'"([^"\n]{1,120})"')
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CREATE = re.compile(r"\b(create|make|generate|write|export|produce|draw|save)\b")
_FORMATS = (
    ("pdf", re.compile(r"\bpdf\b")),
    ("docx", re.compile(r"\b(docx|word)\b")),
    ("xlsx", re.compile(r"\b(xlsx|excel|spreadsheet|workbook)\b")),
    ("md", re.compile(r"\b(markdown|md)\b")),
)
_IMAGE = re.compile(r"\b(image|picture|illustration|drawing|logo)\b")
_EMAIL = re.compile(r"\b(email|emails|e-mail|mail|inbox|gmail)\b")


class State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.log: list[dict[str, Any]] = []
        self.seq = itertools.count(1)
        self.config: dict[str, Any] = {
            "vision_reply": DEFAULT_VISION_REPLY,
            "fail_embeddings": 0,
            "fail_chat": 0,
            "chunk_delay": 0.0,
        }

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry["seq"] = next(self.seq)
        entry["time"] = time.time()
        self.log.append(entry)
        return entry

    def take_failure(self, knob: str) -> bool:
        if self.config.get(knob, 0) > 0:
            self.config[knob] -= 1
            return True
        return False


state = State()
app = FastAPI(title="fake-llm")


def _auth(request: Request) -> str:
    """Which kind of key the backend sent, never the key: "placeholder" (the app's "unused" for a custom api_base
    without api_key_env), "provider-default" (a provider key from the environment: must never happen), "none" or
    "other"."""
    value = request.headers.get("authorization", "")
    token = value.removeprefix("Bearer ").strip()
    if not token:
        return "none"
    if token == "unused":
        return "placeholder"
    if token in PROVIDER_KEYS:
        return "provider-default"
    return "other"


# ---------------- Embeddings ----------------


def embed(text: str, dim: int) -> list[float]:
    vector = [0.0] * dim
    for word in _WORD.findall(text.lower()):
        if len(word) < 3 or word in _STOPWORDS:
            continue
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        vector[int.from_bytes(digest[:4], "little") % dim] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        vector[0] = 1.0
        return vector
    return [value / norm for value in vector]


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> Response:
    body = await request.json()
    model = str(body.get("model", ""))
    inputs = body.get("input")
    texts = [inputs] if isinstance(inputs, str) else [str(item) for item in inputs or []]
    entry = state.record(
        {"path": "/v1/embeddings", "kind": "embedding", "model": model, "auth": _auth(request), "input": texts}
    )
    if state.take_failure("fail_embeddings"):
        entry["failed"] = True
        return JSONResponse({"error": {"message": "fake embedding outage", "type": "server_error"}}, status_code=500)
    dim = EMBEDDING_DIMS.get(model, DEFAULT_DIM)
    data = []
    for index, text in enumerate(texts):
        vector = embed(text, dim)
        if body.get("encoding_format") == "base64":
            encoded: Any = base64.b64encode(struct.pack(f"<{dim}f", *vector)).decode()
        else:
            encoded = vector
        data.append({"object": "embedding", "index": index, "embedding": encoded})
    prompt_tokens = sum(_tokens(text) for text in texts)
    entry["usage"] = {"prompt_tokens": prompt_tokens, "completion_tokens": 0}
    return JSONResponse(
        {
            "object": "list",
            "data": data,
            "model": model,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }
    )


# ---------------- Chat ----------------


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _has_image(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(message.get("content"), list)
        and any(isinstance(part, dict) and part.get("type") == "image_url" for part in message["content"])
        for message in messages
    )


def _kind(messages: list[dict[str, Any]]) -> str:
    if _has_image(messages):
        return "vision"
    system = next((_text(m.get("content")) for m in messages if m.get("role") == "system"), "")
    if system.startswith(REQUEST_CHECK_PROMPT):
        return "request_check"
    if len(messages) == 1 and _text(messages[0].get("content")) == TEST_PROMPT:
        return "test"
    return "chat"


def passages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The numbered passages of the <context> block in the system prompt: [{n, origin, text}]."""
    system = next((_text(m.get("content")) for m in messages if m.get("role") == "system"), "")
    found = _CONTEXT.search(system)
    if not found:
        return []
    block = found.group(1)
    heads = list(_PASSAGE.finditer(block))
    result = []
    for position, head in enumerate(heads):
        end = heads[position + 1].start() if position + 1 < len(heads) else len(block)
        result.append({"n": int(head.group(1)), "origin": head.group(2), "text": block[head.end() : end].strip()})
    return result


def _snippet(text: str, words: int = 18) -> str:
    return " ".join(text.split()[:words]).rstrip(".,;:")


def _latest_user(messages: list[dict[str, Any]]) -> str:
    return next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")


def _file_format(text: str) -> str | None:
    lowered = text.lower()
    if not _CREATE.search(lowered):
        return None
    return next((name for name, pattern in _FORMATS if pattern.search(lowered)), None)


def _wants_image(text: str) -> bool:
    lowered = text.lower()
    return bool(_CREATE.search(lowered) and _IMAGE.search(lowered)) and _file_format(text) is None


def _title(text: str, default: str) -> str:
    quoted = _QUOTED.search(text)
    return quoted.group(1) if quoted else default


def _fit(arguments: dict[str, Any], tool: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Keep only the properties the offered schema has; report required ones that are missing."""
    parameters = tool.get("function", {}).get("parameters", {})
    properties = parameters.get("properties", {})
    fitted = {name: value for name, value in arguments.items() if name in properties}
    missing = [name for name in parameters.get("required", []) if name not in fitted]
    return fitted, missing


def _tool_call(messages: list[dict[str, Any]], tools: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """The tool the latest user message asks for, when it is offered: (name, arguments)."""
    text = _latest_user(messages)
    lowered = text.lower()
    found = passages(messages)
    fmt = _file_format(text)
    if fmt and "create_document" in tools:
        title = _title(text, f"Fake {fmt} report")
        body = f"# {title}\n\nThis file was written by the fake model.\n"
        if found:
            body += f"\nFrom the documents: {_snippet(found[0]['text'])} [1]\n"
        body += "\n| Item | Value |\n|---|---|\n| alpha | 1 |\n| beta | 2 |\n"
        arguments: dict[str, Any] = {"title": title, "format": fmt, "content": body}
        if fmt == "xlsx":
            arguments["sheets"] = [
                {"name": "Data", "columns": ["Item", "Value"], "rows": [["alpha", "1"], ["beta", "2"], [title, "3"]]}
            ]
        if found:
            arguments["source_numbers"] = [1]
        return "create_document", arguments
    if _wants_image(text) and "create_image" in tools:
        title = _title(text, "Fake picture")
        return "create_image", {
            "prompt": f"A simple flat illustration: {title}",
            "title": title,
            "alt_text": f"An illustration of {title}",
            "aspect": "square",
            "quality": "low",
        }
    addresses = _ADDRESS.findall(text)
    if "gmail_propose_send" in tools and re.search(r"\bsend\b", lowered) and addresses:
        return "gmail_propose_send", {
            "to": addresses[:1],
            "subject": _title(text, "Hello from the fake model"),
            "body": "Hello,\n\nThis email was written by the fake model.\n\nBest regards",
        }
    if "gmail_create_draft" in tools and re.search(r"\bdraft\b", lowered) and addresses:
        return "gmail_create_draft", {
            "to": addresses[:1],
            "subject": _title(text, "Draft from the fake model"),
            "body": "Hello,\n\nThis draft was written by the fake model.\n\nBest regards",
        }
    if "gmail_search" in tools and _EMAIL.search(lowered) and re.search(r"\b(search|find|look|check)\b", lowered):
        return "gmail_search", {"query": _title(text, "in:inbox"), "max_results": 5}
    return None


def _cite_numbers(value: Any) -> list[int]:
    """The cite_as numbers in a Gmail tool result, in order."""
    if isinstance(value, dict):
        found = [value["cite_as"]] if isinstance(value.get("cite_as"), int) else []
        return found + [number for item in value.values() for number in _cite_numbers(item)]
    if isinstance(value, list):
        return [number for item in value for number in _cite_numbers(item)]
    return []


def _after_tool(messages: list[dict[str, Any]]) -> str:
    """The answer once the last tool call returned."""
    result_text = _text(messages[-1].get("content"))
    try:
        result = json.loads(result_text)
    except ValueError:
        result = {}
    calls = next((m.get("tool_calls") or [] for m in reversed(messages) if m.get("role") == "assistant"), [])
    name = calls[-1]["function"]["name"] if calls else ""
    if isinstance(result, dict) and result.get("error"):
        return f"The {name} tool reported an error: {result['error']}"
    if name.startswith("gmail_search") or name.startswith("gmail_read"):
        cited = _cite_numbers(result)
        if cited:
            return f"I found a matching email in your inbox [{cited[0]}]."
        return "I found no matching email."
    if name == "gmail_create_draft":
        return "I created the draft in Gmail. Nothing was sent."
    if name == "gmail_propose_send":
        return "I prepared the email. Review it and click Send to send it."
    if name == "create_image":
        return "I generated the image; it is in your documents."
    filename = result.get("filename") if isinstance(result, dict) else None
    return f"I created the file {filename or ''} and saved it to your documents.".replace("  ", " ")


def chat_answer(messages: list[dict[str, Any]], tools: dict[str, dict[str, Any]], tool_choice: Any) -> dict[str, Any]:
    """{"text": str} or {"tool": (name, arguments)}."""
    if messages and messages[-1].get("role") == "tool":
        return {"text": _after_tool(messages)}
    if tools and tool_choice != "none" and (call := _tool_call(messages, tools)) is not None:
        return {"tool": call}
    found = passages(messages)
    if found:
        answer = f"According to your documents, {_snippet(found[0]['text'])} [1]."
        if len(found) > 1:
            answer += f" A second passage adds: {_snippet(found[1]['text'], 10)} [2]."
        return {"text": answer}
    return {"text": "I couldn't find anything about that in your documents, so this answer is from the fake model."}


def request_check(messages: list[dict[str, Any]]) -> str:
    user = _text(messages[-1].get("content"))
    found = re.search(r"<latest_user_message>\n(.*?)\n</latest_user_message>", user, re.DOTALL)
    latest = (found.group(1) if found else user).lower()
    answer = {
        "file": _file_format(latest) is not None,
        "image": _wants_image(latest),
        "draft": bool(re.search(r"\bdraft\b", latest)),
        "send": bool(re.search(r"\bsend\b", latest)),
        "import": bool(re.search(r"\bimport\b", latest)),
    }
    return json.dumps(answer)


def _usage(messages: list[dict[str, Any]], tools: list[Any], output: str) -> dict[str, int]:
    prompt = sum(_tokens(_text(m.get("content")) + json.dumps(m.get("tool_calls") or "")) for m in messages)
    prompt += _tokens(json.dumps(tools)) if tools else 0
    completion = _tokens(output)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _chunk(completion_id: str, model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "system_fingerprint": "fp_fake",
        "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _pieces(text: str, size: int) -> list[str]:
    return [text[start : start + size] for start in range(0, len(text), size)] or [""]


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    model = str(body.get("model", ""))
    messages = body.get("messages") or []
    tool_list = body.get("tools") or []
    tools = {tool["function"]["name"]: tool for tool in tool_list if isinstance(tool, dict) and "function" in tool}
    stream = bool(body.get("stream"))
    kind = _kind(messages)
    entry = state.record(
        {
            "path": "/v1/chat/completions",
            "kind": kind,
            "model": model,
            "auth": _auth(request),
            "stream": stream,
            "messages": messages,
            "tools": list(tools),
            "tool_choice": body.get("tool_choice"),
            "parallel_tool_calls": body.get("parallel_tool_calls"),
            "max_tokens": body.get("max_tokens", body.get("max_completion_tokens")),
        }
    )
    if kind == "chat" and state.take_failure("fail_chat"):
        entry["failed"] = True
        return JSONResponse({"error": {"message": "fake chat outage", "type": "server_error"}}, status_code=500)

    tool: tuple[str, dict[str, Any]] | None = None
    if kind == "vision":
        text = str(state.config["vision_reply"])
    elif kind == "request_check":
        text = request_check(messages)
    elif kind == "test":
        text = "OK"
    else:
        answer = chat_answer(messages, tools, body.get("tool_choice"))
        text = answer.get("text", "")
        tool = answer.get("tool")

    arguments = ""
    if tool is not None:
        name, raw = tool
        fitted, missing = _fit(raw, tools[name])
        arguments = json.dumps(fitted, ensure_ascii=False)
        entry["tool_call"] = {"name": name, "arguments": fitted, "missing_required": missing}
    else:
        entry["answer"] = text
    usage = _usage(messages, tool_list, text + arguments)
    entry["usage"] = {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}
    completion_id = f"chatcmpl-fake-{entry['seq']}"
    finish = "tool_calls" if tool is not None else "stop"

    if not stream:
        message: dict[str, Any] = {"role": "assistant", "content": text if tool is None else None}
        if tool is not None:
            message["tool_calls"] = [
                {
                    "id": f"call_{entry['seq']}",
                    "type": "function",
                    "function": {"name": tool[0], "arguments": arguments},
                }
            ]
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "system_fingerprint": "fp_fake",
                "choices": [{"index": 0, "message": message, "logprobs": None, "finish_reason": finish}],
                "usage": usage,
            }
        )

    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    delay = float(state.config.get("chunk_delay") or 0)

    async def events() -> Any:
        yield _chunk(completion_id, model, {"role": "assistant", "content": ""})
        if tool is None:
            for piece in _pieces(text, 12):
                if delay:
                    await asyncio.sleep(delay)
                yield _chunk(completion_id, model, {"content": piece})
        else:
            call_id = f"call_{entry['seq']}"
            start = {"index": 0, "id": call_id, "type": "function", "function": {"name": tool[0], "arguments": ""}}
            yield _chunk(completion_id, model, {"tool_calls": [start]})
            for piece in _pieces(arguments, 40):
                if delay:
                    await asyncio.sleep(delay)
                yield _chunk(completion_id, model, {"tool_calls": [{"index": 0, "function": {"arguments": piece}}]})
        yield _chunk(completion_id, model, {}, finish)
        if include_usage:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "system_fingerprint": "fp_fake",
                "choices": [],
                "usage": usage,
            }
            yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


# ---------------- Images ----------------


def png(width: int, height: int, seed: str) -> bytes:
    """A valid RGB PNG: two horizontal bands in colours derived from `seed`."""
    digest = hashlib.sha256(seed.encode()).digest()
    top, bottom = bytes(digest[0:3]), bytes(digest[3:6])
    rows = []
    for y in range(height):
        rows.append(b"\x00" + (top if y < height // 2 else bottom) * width)
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


@app.post("/v1/images/generations")
async def images(request: Request) -> Response:
    body = await request.json()
    size = str(body.get("size") or "1024x1024")
    try:
        width, height = (int(part) for part in size.lower().split("x", 1))
    except ValueError:
        width, height = 1024, 1024
    prompt = str(body.get("prompt", ""))
    input_tokens, output_tokens = _tokens(prompt), 272
    state.record(
        {
            "path": "/v1/images/generations",
            "kind": "image",
            "model": str(body.get("model", "")),
            "auth": _auth(request),
            "prompt": prompt,
            "size": size,
            "quality": body.get("quality"),
            "output_format": body.get("output_format"),
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
        }
    )
    data = png(width, height, prompt)
    return JSONResponse(
        {
            "created": int(time.time()),
            "data": [{"b64_json": base64.b64encode(data).decode(), "revised_prompt": f"{prompt} (revised by fake)"}],
            "usage": {
                "input_tokens": input_tokens,
                "input_tokens_details": {"text_tokens": input_tokens, "image_tokens": 0},
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            "background": "opaque",
            "output_format": "png",
            "quality": body.get("quality") or "low",
            "size": size,
        }
    )


# ---------------- Test controls ----------------


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/_log")
async def get_log(kind: str | None = None, after: int = 0) -> dict[str, Any]:
    entries = [entry for entry in state.log if entry["seq"] > after and (kind is None or entry["kind"] == kind)]
    return {"entries": entries, "last": state.log[-1]["seq"] if state.log else 0}


@app.post("/_reset")
async def reset() -> dict[str, bool]:
    state.reset()
    return {"ok": True}


@app.post("/_config")
async def configure(request: Request) -> Response:
    changes = await request.json()
    unknown = set(changes) - set(state.config)
    if unknown:
        return JSONResponse({"error": f"unknown knobs: {sorted(unknown)}"}, status_code=422)
    state.config.update(changes)
    return JSONResponse(state.config)


def main() -> None:
    parser = argparse.ArgumentParser(description="A fake OpenAI-compatible model server for integration tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
