"""Backend test harness: isolated app state per test, a mocked LiteLLM, and shared helpers.

Every test gets a fresh SQLite database, upload directory, FakeRedis server and in-memory Qdrant
client, and LiteLLM is always replaced by `FakeLLM`, so nothing touches the network. Test modules
import the plain helpers from here, e.g. `from conftest import chat, ingest, pdf_bytes, upload`.
"""

import asyncio
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# app.config reads the environment when it is first imported, so configure it before any app import.
_ROOT = Path(tempfile.mkdtemp(prefix="rag-tests-"))
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin-password-123"
USER_PASSWORD = "user-password-123"
os.environ.update(
    {
        "SECRET_KEY": "test-secret-key-with-at-least-32-characters",
        "ADMIN_USERNAME": ADMIN_USERNAME,
        "ADMIN_PASSWORD": ADMIN_PASSWORD,
        # Each test swaps in its own database (see isolated_state); this one is never opened.
        "DATABASE_URL": f"sqlite+aiosqlite:///{_ROOT / 'unused.db'}",
        # Unreachable on purpose: every test installs a FakeRedis.
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "QDRANT_URL": ":memory:",
        "UPLOAD_DIR": str(_ROOT / "uploads"),
        "COOKIE_SECURE": "false",
        # Non-empty provider keys make bootstrap enable the seeded models. LiteLLM is mocked anyway.
        "OPENAI_API_KEY": "test-openai-key",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        # Use LiteLLM's bundled cost map instead of downloading it at import.
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    }
)

import httpx  # noqa: E402
import litellm  # noqa: E402
import pytest  # noqa: E402
from docx import Document as new_docx  # noqa: E402
from fakeredis import FakeServer  # noqa: E402
from fakeredis.aioredis import FakeRedis  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import Document  # noqa: E402
from app.redis_client import INGEST_QUEUE, close_redis, get_redis, set_redis  # noqa: E402
from app.services.ingestion import process_document  # noqa: E402
from app.services.vectorstore import close_qdrant  # noqa: E402

SseEvent = tuple[str, dict[str, Any]]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    shutil.rmtree(_ROOT, ignore_errors=True)


# ---------------- Isolated state ----------------


@pytest.fixture(autouse=True)
async def isolated_state(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Path]:
    """Fresh database, upload directory, Redis and Qdrant for every test; yields the test's scratch dir.

    Tables exist only once the `app` fixture has run its lifespan. Override settings per test with
    `monkeypatch.setattr(settings, "<field>", value)` (every module shares that one object).
    """
    workdir = Path(tempfile.mkdtemp(dir=_ROOT))
    engine = db.make_engine(f"sqlite+aiosqlite:///{workdir / 'app.db'}")
    monkeypatch.setattr(db, "engine", engine)
    db.SessionLocal.configure(bind=engine)
    monkeypatch.setattr(settings, "upload_dir", str(workdir / "uploads"))
    set_redis(FakeRedis(server=FakeServer(), decode_responses=True))
    await close_qdrant()
    yield workdir
    await close_redis()
    await close_qdrant()
    await engine.dispose()
    shutil.rmtree(workdir, ignore_errors=True)


# ---------------- LiteLLM mock ----------------


def fake_embedding(text: str, dim: int = 64) -> list[float]:
    """Deterministic unit vector (signed hashed bag of words): texts that share words score higher."""
    vector = [0.0] * dim
    for word in re.findall(r"\w+", text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % dim] += 1.0 if digest[4] % 2 else -1.0
    if not any(vector):
        vector[0] = 1.0
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


@dataclass
class FakeLLM:
    """Stand-in for `litellm.acompletion` and `litellm.aembedding`; change its attributes inside a test.

    Streaming chat: one delta per `reasoning` item (thinking: `delta.content` is None and
    `delta.reasoning_content` holds the text), then one delta per `chunks` item (sleeping `chunk_delay`
    seconds before each), then a usage-only chunk reporting `usage` = (prompt_tokens, completion_tokens);
    with `usage=None` nothing is reported and the app estimates. `fail_after=n` raises `error` after n
    content chunks; `fail_after=0` makes the call itself raise. Non-streaming calls (the admin model
    test) answer `reply`. Embeddings return `fake_embedding(text, embedding_dim)` per input with
    `prompt_tokens` = word count (no usage when `embedding_usage` is False); `embedding_error` makes every
    embedding call raise it. Keyword arguments of every call are kept in `chat_calls` / `embedding_calls`
    (e.g. `chat_calls[-1]["messages"]` is the prompt the model got).
    """

    chunks: list[str] = field(default_factory=lambda: ["Hello", " from", " the", " fake", " model."])
    usage: tuple[int, int] | None = (42, 5)
    reasoning: list[str] = field(default_factory=list)
    chunk_delay: float = 0.0
    fail_after: int | None = None
    error: BaseException = field(default_factory=lambda: RuntimeError("The provider stream broke"))
    reply: str = "OK"
    embedding_dim: int = 64
    embedding_usage: bool = True
    embedding_error: BaseException | None = None
    chat_calls: list[dict[str, Any]] = field(default_factory=list)
    embedding_calls: list[dict[str, Any]] = field(default_factory=list)

    async def acompletion(self, **kwargs: Any) -> Any:
        self.chat_calls.append(kwargs)
        if self.fail_after == 0:
            raise self.error
        if not kwargs.get("stream"):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1, total_tokens=6),
            )
        return self._stream()

    async def _stream(self) -> AsyncIterator[Any]:
        for text in self.reasoning:
            yield _stream_chunk(content=None, reasoning_content=text)
        for sent, text in enumerate(self.chunks):
            if self.fail_after is not None and sent >= self.fail_after:
                raise self.error
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            yield _stream_chunk(content=text)
        if self.fail_after is not None and self.fail_after >= len(self.chunks):
            raise self.error
        if self.usage is not None:
            prompt_tokens, completion_tokens = self.usage
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
            )

    async def aembedding(self, **kwargs: Any) -> Any:
        self.embedding_calls.append(kwargs)
        if self.embedding_error is not None:
            raise self.embedding_error
        texts = kwargs["input"]
        tokens = sum(len(text.split()) for text in texts)
        return SimpleNamespace(
            data=[
                {"object": "embedding", "index": index, "embedding": fake_embedding(text, self.embedding_dim)}
                for index, text in enumerate(texts)
            ],
            usage=SimpleNamespace(prompt_tokens=tokens, total_tokens=tokens) if self.embedding_usage else None,
        )


def _stream_chunk(*, content: str | None, reasoning_content: str | None = None) -> Any:
    delta = SimpleNamespace(role="assistant", content=content, reasoning_content=reasoning_content)
    return SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta, finish_reason=None)], usage=None)


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """The FakeLLM installed for this test (always active; request it to configure or inspect it)."""
    fake = FakeLLM()
    monkeypatch.setattr(litellm, "acompletion", fake.acompletion)
    monkeypatch.setattr(litellm, "aembedding", fake.aembedding)
    return fake


# ---------------- App and clients ----------------


class ApiClient(httpx.AsyncClient):
    """In-process HTTP client for the app with its own cookie jar.

    `user_id`, `username` and `password` identify the account it is logged in as (None while anonymous).
    """

    def __init__(self, app: FastAPI) -> None:
        super().__init__(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        self.user_id: int | None = None
        self.username: str | None = None
        self.password: str | None = None

    async def login(self, username: str, password: str) -> httpx.Response:
        """POST /api/auth/login. On 200 the session cookie is stored and the identity attributes are set."""
        response = await self.post("/api/auth/login", json={"username": username, "password": password})
        if response.status_code == 200:
            me = response.json()
            self.user_id, self.username, self.password = me["id"], me["username"], password
        return response


@pytest.fixture
async def app(isolated_state: Path) -> AsyncIterator[FastAPI]:
    """The FastAPI app with its lifespan running: tables created, admin synced, models seeded."""
    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest.fixture
async def new_client(app: FastAPI) -> AsyncIterator[Callable[[], ApiClient]]:
    """Factory for extra anonymous clients (e.g. a second session of the same user); closed at teardown."""
    clients: list[ApiClient] = []

    def factory() -> ApiClient:
        client = ApiClient(app)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
def client(new_client: Callable[[], ApiClient]) -> ApiClient:
    """An anonymous client."""
    return new_client()


@pytest.fixture
async def admin_client(new_client: Callable[[], ApiClient]) -> ApiClient:
    """A client logged in as the env admin (ADMIN_USERNAME / ADMIN_PASSWORD)."""
    admin = new_client()
    response = await admin.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    assert response.status_code == 200, response.text
    return admin


@pytest.fixture
def create_user(admin_client: ApiClient, new_client: Callable[[], ApiClient]) -> Callable[..., Awaitable[ApiClient]]:
    """Factory: `await create_user("alice", **fields)` creates a user through POST /api/admin/users and
    returns a client logged in as them.

    `fields` are any AdminUserCreate fields (limits, restrict_models, allowed_model_ids, is_active, ...);
    omitted ones get the server defaults (e.g. 20 chat requests per minute). The name defaults to
    user1, user2, ... and the password to USER_PASSWORD. With `login=False` the client stays anonymous
    (needed for `is_active=False`); its `user_id`/`username`/`password` are set either way. Asserts the
    user was created.
    """
    created = 0

    async def factory(
        username: str | None = None, password: str = USER_PASSWORD, *, login: bool = True, **fields: Any
    ) -> ApiClient:
        nonlocal created
        created += 1
        name = username or f"user{created}"
        response = await admin_client.post("/api/admin/users", json={"username": name, "password": password, **fields})
        assert response.status_code == 201, response.text
        user = new_client()
        if login:
            login_response = await user.login(name, password)
            assert login_response.status_code == 200, login_response.text
        account = response.json()
        user.user_id, user.username, user.password = account["id"], account["username"], password
        return user

    return factory


# ---------------- Documents and ingestion ----------------


async def upload(
    client: httpx.AsyncClient,
    filename: str,
    data: bytes,
    scope: str = "private",
    content_type: str | None = None,
) -> httpx.Response:
    """POST /api/documents as multipart (`file` + `scope`). Returns the raw response (201 on success)."""
    file = (filename, data) if content_type is None else (filename, data, content_type)
    return await client.post("/api/documents", files={"file": file}, data={"scope": scope})


async def process(doc_id: int) -> Document | None:
    """Take the document off the ingestion queue and process it in-process, like the worker does.

    Returns its row afterwards: a detached but fully readable ORM object (`status`, `error`,
    `num_chunks`, `embedding_model_id`, `owner`, ...), or None if the document no longer exists.
    """
    await get_redis().lrem(INGEST_QUEUE, 0, str(doc_id))
    await process_document(doc_id)
    async with db.SessionLocal() as session:
        return await session.get(Document, doc_id)


async def process_queue() -> list[int]:
    """Pop every id from the ingestion queue and process it, like the worker. Returns the ids in order."""
    redis = get_redis()
    processed: list[int] = []
    while (item := await redis.lpop(INGEST_QUEUE)) is not None:
        processed.append(int(item))
        await process_document(int(item))
    return processed


async def ingest(client: ApiClient, filename: str, data: bytes, scope: str = "private") -> dict[str, Any]:
    """Upload (asserting 201), process, and return the document's DocumentItem JSON as `client` now sees it."""
    response = await upload(client, filename, data, scope)
    assert response.status_code == 201, response.text
    doc_id = response.json()["id"]
    await process(doc_id)
    listing = await client.get("/api/documents")
    assert listing.status_code == 200, listing.text
    return next(item for item in listing.json() if item["id"] == doc_id)


# ---------------- Chat (SSE) ----------------


def sse_events(response: httpx.Response) -> list[SseEvent]:
    """Parse a text/event-stream body into (event name, JSON data) pairs, in order."""
    events: list[SseEvent] = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        name, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").removeprefix(" "))
        events.append((name, json.loads("\n".join(data))))
    return events


async def chat(
    client: httpx.AsyncClient,
    message: str,
    *,
    conversation_id: int | None = None,
    model_id: int | None = None,
    document_ids: list[int] | None = None,
) -> tuple[httpx.Response, list[SseEvent]]:
    """POST /api/chat/stream and read the whole stream. Returns the response and its parsed events
    (an empty list unless the status is 200)."""
    body = {"conversation_id": conversation_id, "message": message, "model_id": model_id, "document_ids": document_ids}
    response = await client.post("/api/chat/stream", json=body)
    return response, sse_events(response) if response.status_code == 200 else []


# ---------------- File builders ----------------


def _pdf_string(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_bytes(*pages: str) -> bytes:
    """A PDF with one page per argument and a real text layer (Helvetica, one text line per input line).

    An empty or blank page has no text layer at all, so `pdf_bytes("")` acts as a scanned PDF. Text must
    be Latin-1 (other characters become "?").
    """
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    kids = []
    for number, text in enumerate(pages or ("",)):
        page_id, content_id = 4 + 2 * number, 5 + 2 * number
        kids.append(f"{page_id} 0 R")
        shown = " ".join(f"({_pdf_string(line)}) Tj T*" for line in text.splitlines())
        stream = f"BT /F1 12 Tf 14 TL 72 760 Td {shown} ET".encode("latin-1", "replace") if text.strip() else b""
        objects[content_id] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (object_id, objects[object_id])
    xref = len(out)
    size = len(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    out += b"".join(b"%010d 00000 n \n" % offsets[object_id] for object_id in range(1, size))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(out)


def docx_bytes(*paragraphs: str, table: Sequence[Sequence[str]] | None = None) -> bytes:
    """A .docx with these paragraphs, then `table` (rows of cell texts) if given."""
    document = new_docx()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        grid = document.add_table(rows=len(table), cols=max(len(row) for row in table))
        for grid_row, values in zip(grid.rows, table, strict=True):
            for cell, value in zip(grid_row.cells, values, strict=False):
                cell.text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pptx_bytes(*slides: str, notes: Sequence[str] = ()) -> bytes:
    """A .pptx with one slide per argument (a text box holding the text); `notes[i]` adds speaker notes."""
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    for number, text in enumerate(slides):
        slide = presentation.slides.add_slide(blank)
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(5)).text_frame.text = text
        if number < len(notes) and notes[number]:
            slide.notes_slide.notes_text_frame.text = notes[number]
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def xlsx_bytes(rows: Sequence[Sequence[Any]], title: str = "Sheet1") -> bytes:
    """A one-sheet .xlsx named `title` holding `rows` (cell values: str, int, float, datetime, None)."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = title
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def html_bytes(text: str, title: str = "Test page") -> bytes:
    """An HTML page: `title`, a <script> the parser must drop ("SCRIPT_MARKER"), and one <p> per line of `text`."""
    paragraphs = "".join(f"<p>{escape(line)}</p>" for line in text.splitlines())
    return (
        f"<!doctype html><html><head><title>{escape(title)}</title>"
        "<script>var marker = 'SCRIPT_MARKER';</script></head>"
        f"<body>{paragraphs}</body></html>"
    ).encode()


def md_bytes(text: str) -> bytes:
    """A Markdown document: a heading followed by `text`."""
    return f"# Notes\n\n{text}\n".encode()


def txt_bytes(text: str) -> bytes:
    """`text` as UTF-8."""
    return text.encode()


def csv_bytes(rows: Sequence[Sequence[Any]]) -> bytes:
    """CSV of `rows`."""
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


def json_bytes(value: Any) -> bytes:
    """`value` as compact JSON (the parser pretty-prints it)."""
    return json.dumps(value).encode()


def sample_file(extension: str, text: str) -> bytes:
    """A valid file of any supported extension (e.g. ".pdf") whose extracted text contains `text`.

    Keep `text` Latin-1 for ".pdf".
    """
    builders: dict[str, Callable[[], bytes]] = {
        ".pdf": lambda: pdf_bytes(text),
        ".docx": lambda: docx_bytes(text, table=[["Column A", "Column B"], ["cell one", "cell two"]]),
        ".pptx": lambda: pptx_bytes(text),
        ".xlsx": lambda: xlsx_bytes([["Text"], [text]]),
        ".html": lambda: html_bytes(text),
        ".htm": lambda: html_bytes(text),
        ".md": lambda: md_bytes(text),
        ".markdown": lambda: md_bytes(text),
        ".txt": lambda: txt_bytes(text),
        ".csv": lambda: csv_bytes([["text"], [text]]),
        ".json": lambda: json_bytes({"text": text}),
    }
    return builders[extension.lower()]()
