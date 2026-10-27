"""Reading PDF pages with the vision model: classification and selection, the LiteLLM call, ingestion, the page
cache, limits, failures, stop checks, the monthly allowance, the worker hand-off, the admin API and bootstrap."""

import asyncio
import base64
import contextlib
import io
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import litellm
import pytest
from conftest import (
    ApiClient,
    FakeLLM,
    chat,
    image_pdf_bytes,
    ingest,
    jpeg_xobject,
    pdf_stream,
    process,
    raw_pdf_bytes,
    scanned_pdf_bytes,
    txt_bytes,
    upload,
    vision_page_and_mode,
    vision_pages,
    vision_part,
)
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter
from qdrant_client import models as qm
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.bootstrap import bootstrap
from app.config import Settings, settings
from app.db import SessionLocal
from app.models import AppState, Document, LLMModel, UsageEvent, User, VisionPage
from app.redis_client import INGEST_QUEUE, get_redis, ingest_attempts_key
from app.services import ingestion, llm, pdf_images, vectorstore, vision
from app.services.billing.money import format_ist_date, ist_date
from app.services.model_registry import get_active_embedding_model
from app.services.parsing import DocumentParseError, PageImage, ParsedDocument, Segment, VisionRequest, parse_file
from app.services.usage import month_start, next_month

CreateUser = Callable[..., Awaitable[ApiClient]]

TEXT = "The quarterly revenue report for the northern region shows growth."
SONNET = "anthropic/claude-sonnet-5-5"
NO_VISION = (
    "No extractable text: this PDF looks scanned or image-only. Ask the admin to enable a vision model to read it."
)
NOTHING_TO_READ = "No extractable text: this PDF has neither a text layer nor page images to read."
OVERLAY = "DocuSign Envelope ID: 1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D"
AUTH_FAILED = "The model provider rejected the API key. Check the provider key in .env."
GUARD_SECONDS = 15


@pytest.fixture
async def alice(create_user: CreateUser) -> ApiClient:
    return await create_user("alice")


async def vision_allowance(user_id: int, tokens: int) -> None:
    """The user's override of their plan's vision allowance (0 = unlimited)."""
    async with SessionLocal() as session:
        await session.execute(update(User).where(User.id == user_id).values(vision_monthly_token_limit=tokens))
        await session.commit()


def restarts() -> str:
    """When an open-ended plan's allowances start again: the first day of the next UTC month (an IST date)."""
    return f"It starts again on {format_ist_date(ist_date(next_month(month_start())))}."


@pytest.fixture
def one_call_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "vision_concurrency", 1)


# ---------------- Helpers ----------------


def page_text(n: int) -> str:
    return f"Page {n} of the report. {TEXT}"


def scanned_text(page: int) -> str:
    return f"Scanned text of page {page}: invoice total 1234."


def chart_text(page: int) -> str:
    return f"[Chart] Revenue by quarter on page {page}, rising every quarter."


def gray_image(width: int = 100, height: int = 12) -> bytes:
    return pdf_stream(
        b"\x80" * (width * height),
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray /BitsPerComponent 8",
    )


def banded_scan(text: bytes = b"", strips: int = 8) -> bytes:
    """A scan stored as horizontal strips covering the page, plus `text` (content stream operators)."""
    height = 792 / strips
    content = b"".join(b"q 612 0 0 %.3f 0 %.3f cm /S%d Do Q\n" % (height, height * k, k) for k in range(strips))
    objects = {100 + k: gray_image() for k in range(strips)}
    return raw_pdf_bytes(
        content + text, objects=objects, xobjects=" ".join(f"/S{k} {100 + k} 0 R" for k in range(strips))
    )


def classify(isolated_state: Path, data: bytes, cached: frozenset[tuple[int, str]] = frozenset()) -> ParsedDocument:
    path = isolated_state / "classify.pdf"
    path.write_bytes(data)
    out_dir = isolated_state / "rendered"
    out_dir.mkdir(exist_ok=True)
    return parse_file(path, ".pdf", VisionRequest(out_dir, cached))


def modes(parsed: ParsedDocument) -> list[tuple[int, str]]:
    return [(image.page, image.mode) for image in parsed.page_images]


async def uploaded_id(client: ApiClient, filename: str, data: bytes, scope: str = "private") -> int:
    response = await upload(client, filename, data, scope)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def vision_model() -> LLMModel:
    async with SessionLocal() as session:
        model = await session.scalar(select(LLMModel).where(LLMModel.kind == "vision"))
    assert model is not None
    return model


async def events(kind: str) -> list[UsageEvent]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(UsageEvent).where(UsageEvent.kind == kind).order_by(UsageEvent.id)))


async def cache_rows(doc_id: int | None = None) -> list[tuple[int, int, str, str]]:
    """(document id, page, mode, text) of the cached vision pages."""
    statement = select(VisionPage.document_id, VisionPage.page, VisionPage.mode, VisionPage.text)
    if doc_id is not None:
        statement = statement.where(VisionPage.document_id == doc_id)
    async with SessionLocal() as session:
        return sorted((await session.execute(statement)).tuples())


async def payloads(doc_id: int) -> list[dict[str, Any]]:
    """The document's chunk payloads in the active collection, in chunk order."""
    async with SessionLocal() as session:
        model = await get_active_embedding_model(session)
    assert model is not None
    client = vectorstore.get_qdrant()
    name = vectorstore.collection_name(model.id)
    if not await client.collection_exists(name):
        return []
    records, _ = await client.scroll(
        name,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]),
        limit=1000,
        with_payload=True,
    )
    return sorted((record.payload or {} for record in records), key=lambda payload: payload["chunk_index"])


async def page_texts(doc_id: int) -> dict[int, str]:
    texts: dict[int, str] = {}
    for payload in await payloads(doc_id):
        texts[payload["page"]] = (texts.get(payload["page"], "") + "\n" + payload["text"]).strip()
    return texts


async def document(doc_id: int) -> Document | None:
    async with SessionLocal() as session:
        return await session.get(Document, doc_id)


async def item(client: ApiClient, doc_id: int, path: str = "/api/documents") -> dict[str, Any]:
    listing = await client.get(path)
    assert listing.status_code == 200, listing.text
    return next(entry for entry in listing.json() if entry["id"] == doc_id)


def wrap_read_image(
    monkeypatch: pytest.MonkeyPatch, before: Callable[[int], Awaitable[None]], after: bool = False
) -> list[int]:
    """Run `before(call_number)` (1-based) ahead of every vision call, or after it with `after`."""
    calls: list[int] = []
    read_image = llm.read_image

    async def wrapped(*args: Any, **kwargs: Any) -> llm.VisionReply:
        calls.append(len(calls) + 1)
        if not after:
            await before(len(calls))
        reply = await read_image(*args, **kwargs)
        if after:
            await before(len(calls))
        return reply

    monkeypatch.setattr(llm, "read_image", wrapped)
    return calls


async def wait_for(condition: Callable[[], Awaitable[bool]], seconds: float = GUARD_SECONDS) -> None:
    async def poll() -> None:
        while not await condition():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), seconds)


def raising(*failures: tuple[int, BaseException]) -> Callable[[int, str], str]:
    """A vision_reply that raises the given exception once for each (page, exception), else answers normally."""
    pending = list(failures)

    def reply(page: int, mode: str) -> str:
        for index, (failing_page, error) in enumerate(pending):
            if failing_page == page:
                del pending[index]
                raise error
        return scanned_text(page) if mode == "transcribe" else chart_text(page)

    return reply


# ---------------- Classification ----------------


def test_a_scan_is_transcribed_and_has_no_text_segment(isolated_state: Path) -> None:
    parsed = classify(isolated_state, scanned_pdf_bytes("Invoice 1234"))

    assert parsed.segments == []
    [image] = parsed.page_images
    assert (image.page, image.pages, image.mode) == (1, (1,), "transcribe")
    assert image.image is not None and image.image.is_file()
    assert image.size is not None and max(image.size) == settings.vision_max_image_px


def test_a_figure_page_is_described_and_keeps_its_text(isolated_state: Path) -> None:
    parsed = classify(isolated_state, image_pdf_bytes((TEXT, 0.3)))

    assert [segment.text for segment in parsed.segments] == [TEXT]
    assert modes(parsed) == [(1, "describe")]


def test_a_small_image_is_not_read(isolated_state: Path) -> None:
    parsed = classify(isolated_state, image_pdf_bytes((TEXT, 0.05)))

    assert parsed.page_images == []
    assert [segment.page for segment in parsed.segments] == [1]


def test_a_short_text_with_an_image_is_transcribed(isolated_state: Path) -> None:
    parsed = classify(isolated_state, image_pdf_bytes(("Figure 3", 0.5)))

    assert modes(parsed) == [(1, "transcribe")]
    assert parsed.segments == []  # the render shows the short text too


def test_an_image_in_a_form_counts_with_its_transformed_area(isolated_state: Path) -> None:
    assert modes(classify(isolated_state, image_pdf_bytes((TEXT, 0.3), in_form=True))) == [(1, "describe")]


def test_identical_pages_are_one_unit(isolated_state: Path) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(scanned_pdf_bytes("Same page")))
    writer.add_page(writer.pages[0])  # the same content stream and resources
    buffer = io.BytesIO()
    writer.write(buffer)

    parsed = classify(isolated_state, buffer.getvalue())

    [image] = parsed.page_images
    assert (image.page, image.pages, image.mode) == (1, (1, 2), "transcribe")


async def test_identical_pages_are_read_once_with_the_text_on_both(alice: ApiClient, fake_llm: FakeLLM) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(scanned_pdf_bytes("Same page")))
    writer.add_page(writer.pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)

    result = await ingest(alice, "twice.pdf", buffer.getvalue())

    assert vision_pages(fake_llm) == [1]
    assert await page_texts(result["id"]) == {1: scanned_text(1), 2: scanned_text(1)}
    assert result["vision_pages"] == 1


def test_without_vision_a_scan_asks_for_a_vision_model(isolated_state: Path) -> None:
    path = isolated_state / "scan.pdf"
    path.write_bytes(scanned_pdf_bytes("Invoice 1234"))

    with pytest.raises(DocumentParseError) as caught:
        parse_file(path, ".pdf")

    assert str(caught.value) == NO_VISION


def test_with_vision_a_blank_pdf_has_nothing_to_read(isolated_state: Path) -> None:
    with pytest.raises(DocumentParseError) as caught:
        classify(isolated_state, raw_pdf_bytes(b"0 0 m 600 780 l S"))

    assert str(caught.value) == NOTHING_TO_READ


def test_a_scan_with_a_small_visible_overlay_is_transcribed(isolated_state: Path) -> None:
    overlay = b"BT /F1 8 Tf 20 780 Td (" + OVERLAY.encode() + b") Tj ET"

    parsed = classify(isolated_state, banded_scan(overlay))

    assert modes(parsed) == [(1, "transcribe")]


def test_a_banded_scan_with_an_ocr_layer_keeps_its_text(isolated_state: Path, child_starts: list[int]) -> None:
    ocr = b"BT 3 Tr /F1 10 Tf 72 700 Td (" + b"Invisible OCR text of the scanned page. " * 10 + b") Tj ET"

    with_ocr = classify(isolated_state, banded_scan(ocr))
    without = classify(isolated_state, banded_scan())

    assert with_ocr.page_images == [] and with_ocr.segments[0].text.startswith("Invisible OCR text")
    assert child_starts == [1]  # only for the scan without text
    assert modes(without) == [(1, "transcribe")]


def test_a_captioned_full_page_chart_is_described(isolated_state: Path) -> None:
    caption = " ".join(["Figure 1 shows the revenue of every region by quarter, with the northern region leading."] * 3)

    parsed = classify(isolated_state, image_pdf_bytes((caption, 1.0)))

    assert modes(parsed) == [(1, "describe")]
    assert parsed.segments[0].text == caption


def test_a_banner_on_every_page_is_ignored(isolated_state: Path, child_starts: list[int]) -> None:
    pages = [(page_text(n), 0.12) for n in range(1, 11)]

    parsed = classify(isolated_state, image_pdf_bytes(*pages))

    assert parsed.page_images == []
    assert len(parsed.segments) == 10
    assert child_starts == []


def test_only_the_figures_count_next_to_a_repeated_banner(isolated_state: Path) -> None:
    banner = b"q 612 0 0 95 0 697 cm /Banner Do Q "
    contents = [
        banner + b"q 612 0 0 300 0 0 cm /Fig%d Do Q " % n + b"BT /F1 12 Tf 72 600 Td (%s) Tj ET" % page_text(n).encode()
        for n in (1, 2, 3)
    ]
    contents.append(banner + b"BT /F1 12 Tf 72 600 Td (%s) Tj ET" % page_text(4).encode())
    objects = {100: gray_image(), 101: jpeg_xobject(), 102: jpeg_xobject(), 103: jpeg_xobject()}

    parsed = classify(
        isolated_state,
        raw_pdf_bytes(*contents, objects=objects, xobjects="/Banner 100 0 R /Fig1 101 0 R /Fig2 102 0 R /Fig3 103 0 R"),
    )

    assert modes(parsed) == [(1, "describe"), (2, "describe"), (3, "describe")]


# ---------------- Cap and priority ----------------


def test_scans_come_first_then_the_largest_figures(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "vision_max_pages", 5)
    pages = [("", 1.0), (page_text(2), 0.2), ("", 1.0), (page_text(4), 0.5), ("", 1.0), (page_text(6), 0.3)]
    pages.append((page_text(7), 0.4))

    with caplog.at_level(logging.INFO):
        parsed = classify(isolated_state, image_pdf_bytes(*pages, shared_image=False))

    assert modes(parsed) == [(1, "transcribe"), (3, "transcribe"), (4, "describe"), (5, "transcribe"), (7, "describe")]
    assert "classify.pdf: 2 page(s) not read with the vision model (VISION_MAX_PAGES=5)" in caplog.messages


def test_more_scanned_pages_than_the_cap_fail_before_anything_is_rendered(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, child_starts: list[int]
) -> None:
    monkeypatch.setattr(settings, "vision_max_pages", 2)

    with pytest.raises(DocumentParseError) as caught:
        classify(isolated_state, scanned_pdf_bytes("One", "Two", "Three"))

    assert str(caught.value) == (
        "This PDF has 3 scanned pages, but at most 2 pages per document can be read with the vision model "
        "(VISION_MAX_PAGES). Split it into smaller files and upload them separately."
    )
    assert child_starts == []


async def test_slides_over_the_cap_keep_their_titles(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "vision_max_pages", 50)
    slides = [(f"Slide {n}: regional sales", 0.6) for n in range(1, 61)]

    with caplog.at_level(logging.INFO):
        result = await ingest(alice, "deck.pdf", image_pdf_bytes(*slides, shared_image=False))

    assert result["status"] == "ready", result["error"]
    assert sorted(vision_pages(fake_llm)) == list(range(1, 51))
    texts = await page_texts(result["id"])
    assert [texts[page] for page in range(51, 61)] == [f"Slide {page}: regional sales" for page in range(51, 61)]
    assert texts[50] == scanned_text(50)
    assert result["vision_pages"] == 50
    assert any(  # parsing logs the stored file name
        message.endswith(".pdf: 10 page(s) not read with the vision model (VISION_MAX_PAGES=50)")
        for message in caplog.messages
    )


def test_a_figure_page_that_cannot_be_rendered_gives_its_slot_to_the_next(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "vision_max_pages", 1)
    monkeypatch.setattr(pdf_images, "_MAX_DO_VISITS", 3)
    text = b" BT /F1 12 Tf 72 700 Td (%s) Tj ET"
    complex_page = b"q 612 0 0 400 0 0 cm /A Do Q" + b" /Dot Do" * 4 + text % page_text(1).encode()
    simple_page = b"q 612 0 0 200 0 0 cm /B Do Q" + text % page_text(2).encode()
    objects = {100: jpeg_xobject(), 101: jpeg_xobject(), 102: gray_image(1, 1)}

    with caplog.at_level(logging.INFO):
        parsed = classify(
            isolated_state,
            raw_pdf_bytes(complex_page, simple_page, objects=objects, xobjects="/A 100 0 R /B 101 0 R /Dot 102 0 R"),
        )

    assert modes(parsed) == [(2, "describe")]
    assert any("page 1: image description skipped: Page 1 is too complex" in message for message in caplog.messages)


def test_a_scanned_page_that_cannot_be_rendered_fails_the_document(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, child_starts: list[int]
) -> None:
    monkeypatch.setattr(pdf_images, "_MAX_DO_VISITS", 3)
    huge = pdf_stream(b"", "/Type /XObject /Subtype /Image /Width 20000 /Height 20000 /ColorSpace /DeviceGray")
    content = b"q 612 0 0 792 0 0 cm /Im Do Q"

    with pytest.raises(DocumentParseError, match=r"^Page 1 is too complex to render for the vision model\.$"):
        classify(
            isolated_state,
            raw_pdf_bytes(content + b" /Im Do" * 4, objects={100: jpeg_xobject()}, xobjects="/Im 100 0 R"),
        )
    with pytest.raises(DocumentParseError, match=r"^Page 1 contains an image too large to process\.$"):
        classify(isolated_state, raw_pdf_bytes(content, objects={100: huge}, xobjects="/Im 100 0 R"))
    assert child_starts == []


def test_a_render_failure_fails_a_scanned_page_and_skips_a_figure(
    isolated_state: Path, render_child: Callable[..., Any]
) -> None:
    render_child(abort=[2])

    with pytest.raises(DocumentParseError, match=r"^Page 2 couldn't be rendered for the vision model\.$"):
        classify(isolated_state, scanned_pdf_bytes("One", "Two"))
    parsed = classify(isolated_state, image_pdf_bytes((page_text(1), 0.3), (page_text(2), 0.3), shared_image=False))
    assert modes(parsed) == [(1, "describe")]
    assert [segment.page for segment in parsed.segments] == [1, 2]


def test_cached_pages_are_not_rendered(isolated_state: Path, child_starts: list[int]) -> None:
    parsed = classify(isolated_state, scanned_pdf_bytes("One", "Two"), cached=frozenset({(1, "transcribe")}))

    assert [(image.page, image.image is None) for image in parsed.page_images] == [(1, True), (2, False)]
    assert child_starts == [1]


# ---------------- Ingestion and the LiteLLM call ----------------


async def test_a_scanned_pdf_is_read_with_the_vision_model(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    response = await upload(alice, "scan.pdf", scanned_pdf_bytes("Invoice", "Total"))
    assert response.status_code == 201
    doc_id = response.json()["id"]
    assert response.json()["vision_pages"] == 0
    assert (await item(alice, doc_id))["vision_pages"] == 0

    await process(doc_id)

    listed = await item(alice, doc_id)
    assert (listed["status"], listed["error"], listed["num_pages"], listed["vision_pages"]) == ("ready", None, 2, 2)
    assert (await item(admin_client, doc_id, "/api/admin/documents"))["vision_pages"] == 2
    assert await page_texts(doc_id) == {1: scanned_text(1), 2: scanned_text(2)}
    assert sorted(vision_page_and_mode(call) for call in fake_llm.vision_calls) == [
        (1, "transcribe"),
        (2, "transcribe"),
    ]
    assert fake_llm.chat_calls == []
    assert await cache_rows(doc_id) == [
        (doc_id, 1, "transcribe", scanned_text(1)),
        (doc_id, 2, "transcribe", scanned_text(2)),
    ]


async def test_the_vision_call_sends_the_page_image_and_keeps_thinking_low(alice: ApiClient, fake_llm: FakeLLM) -> None:
    await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    [call] = fake_llm.vision_calls
    assert call["model"] == SONNET
    system, user = call["messages"]
    assert system == {"role": "system", "content": vision.SYSTEM_PROMPT}
    assert user["role"] == "user"
    image, text = user["content"]
    assert image["type"] == "image_url" and set(image["image_url"]) == {"url"}
    url = image["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(url.removeprefix("data:image/jpeg;base64,")))) as page:
        assert page.format == "JPEG" and max(page.size) == 1568
    assert text == {"type": "text", "text": vision.instruction(1, "transcribe")}
    assert call["max_tokens"] == 8192
    assert call["reasoning_effort"] == "low"
    assert call["max_retries"] == 0  # the provider SDK must not retry: read_pages decides (see below)
    assert "temperature" not in call
    assert "stream" not in call


async def test_the_models_own_output_cap_and_temperature_are_sent(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    model = await vision_model()
    edited = await admin_client.patch(
        f"/api/admin/models/{model.id}", json={"max_output_tokens": 3000, "temperature": 0.2}
    )
    assert edited.status_code == 200

    await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    [call] = fake_llm.vision_calls
    assert (call["max_tokens"], call["temperature"]) == (3000, 0.2)


async def test_vision_usage_is_one_event_for_the_owner_outside_the_chat_budget(
    create_user: CreateUser, fake_llm: FakeLLM
) -> None:
    bob = await create_user("bob", monthly_token_limit=1000)

    result = await ingest(bob, "scan.pdf", scanned_pdf_bytes("Invoice", "Total"))

    assert result["status"] == "ready"
    [event] = await events("vision")
    model = await vision_model()
    assert (event.user_id, event.model_id, event.model_name) == (bob.user_id, model.id, SONNET)
    assert (event.prompt_tokens, event.completion_tokens, event.total_tokens) == (3000, 400, 3400)
    assert event.cost_usd == pytest.approx((3000 * 2.0 + 400 * 10.0) / 1_000_000)
    assert len(await events("embedding")) == 1
    me = (await bob.get("/api/auth/me")).json()
    assert (me["usage"]["vision_tokens"], me["usage"]["chat_tokens"]) == (3400, 0)
    assert me["usage"]["cost_usd"] == pytest.approx(0.01 + (await events("embedding"))[0].cost_usd, abs=1e-6)
    response, _ = await chat(bob, "What is the invoice total?")
    assert response.status_code == 200  # 3400 vision tokens, but a 1000-token chat budget


async def test_a_mixed_pdf_reads_text_scans_and_figures_and_cites_the_scanned_page(
    alice: ApiClient, fake_llm: FakeLLM
) -> None:
    data = image_pdf_bytes((page_text(1), 0.0), ("", 1.0), (page_text(3), 0.3), shared_image=False)

    result = await ingest(alice, "mixed.pdf", data)

    assert result["status"] == "ready", result["error"]
    assert await page_texts(result["id"]) == {
        1: page_text(1),
        2: scanned_text(2),
        3: f"{page_text(3)}\n\n{chart_text(3)}",
    }
    assert result["vision_pages"] == 2
    response, events_ = await chat(alice, "What is the invoice total?")
    assert response.status_code == 200
    sources = next(data for name, data in events_ if name == "sources")["sources"]
    assert any(source["page"] == 2 and "invoice total 1234" in source["snippet"] for source in sources)


# ---------------- The page cache ----------------


async def test_reprocessing_and_reindexing_reuse_the_cached_pages(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice", "Total"))
    assert len(fake_llm.vision_calls) == 2

    assert (await alice.post(f"/api/documents/{result['id']}/reprocess")).status_code == 200
    await process(result["id"])
    assert (await admin_client.post("/api/admin/reindex", json={"only_stale": False})).json() == {"queued": 1}
    await process(result["id"])

    assert len(fake_llm.vision_calls) == 2
    assert len(await events("vision")) == 1
    assert len(await events("embedding")) == 3
    assert (await item(alice, result["id"]))["vision_pages"] == 2
    assert await page_texts(result["id"]) == {1: scanned_text(1), 2: scanned_text(2)}


async def test_retry_after_a_failed_page_reads_only_that_page(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.vision_reply = raising(
        (2, litellm.AuthenticationError(message="bad key", llm_provider="anthropic", model="x"))
    )
    failed = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice", "Total"))
    assert failed["error"] == f"Page 2 couldn't be read with the vision model: {AUTH_FAILED}"

    assert (await alice.post(f"/api/documents/{failed['id']}/reprocess")).status_code == 200
    await process(failed["id"])

    assert sorted(vision_pages(fake_llm)) == [1, 2, 2]
    assert (await item(alice, failed["id"]))["status"] == "ready"


async def test_a_new_vision_model_or_prompt_reads_the_pages_again(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))
    model = await vision_model()

    edited = await admin_client.patch(
        f"/api/admin/models/{model.id}", json={"litellm_model": "anthropic/claude-sonnet-5"}
    )
    assert edited.status_code == 200
    assert (await alice.post(f"/api/documents/{result['id']}/reprocess")).status_code == 200
    await process(result["id"])
    monkeypatch.setattr(vision, "PROMPT_VERSION", vision.PROMPT_VERSION + 1)
    assert (await alice.post(f"/api/documents/{result['id']}/reprocess")).status_code == 200
    await process(result["id"])

    assert [call["model"] for call in fake_llm.vision_calls] == [
        SONNET,
        "anthropic/claude-sonnet-5",
        "anthropic/claude-sonnet-5",
    ]
    assert len(await cache_rows(result["id"])) == 1  # the run keeps only the rows it used


async def test_cache_rows_belong_to_one_document(alice: ApiClient, create_user: CreateUser, fake_llm: FakeLLM) -> None:
    bob = await create_user("bob")
    data = scanned_pdf_bytes("Invoice")
    first = await ingest(alice, "scan.pdf", data)
    second = await ingest(bob, "scan.pdf", data)
    assert len(fake_llm.vision_calls) == 2  # an identical file of another user is read again

    assert (await alice.delete(f"/api/documents/{first['id']}")).status_code == 204

    assert [row[0] for row in await cache_rows()] == [second["id"]]


async def test_pages_no_longer_read_are_pruned(alice: ApiClient, fake_llm: FakeLLM) -> None:
    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))
    async with SessionLocal() as session:
        session.add(VisionPage(document_id=result["id"], page=7, mode="describe", fingerprint="old", text="stale"))
        await session.commit()

    await alice.post(f"/api/documents/{result['id']}/reprocess")
    await process(result["id"])

    assert [(row[1], row[2]) for row in await cache_rows(result["id"])] == [(1, "transcribe")]


# ---------------- Output ----------------


@pytest.mark.parametrize("reply", ["NO_CONTENT", "no_content.", "NO_CONTENT - blank", "NO_CONTENT\n(no figures)"])
async def test_no_content_adds_nothing_to_a_figure_page(alice: ApiClient, fake_llm: FakeLLM, reply: str) -> None:
    fake_llm.vision_reply = reply

    result = await ingest(alice, "figure.pdf", image_pdf_bytes((TEXT, 0.3)))

    assert result["status"] == "ready"
    assert await page_texts(result["id"]) == {1: TEXT}
    assert await cache_rows(result["id"]) == [(result["id"], 1, "describe", "")]


async def test_a_figure_description_follows_the_page_text(alice: ApiClient, fake_llm: FakeLLM) -> None:
    result = await ingest(alice, "figure.pdf", image_pdf_bytes((TEXT, 0.3)))

    assert await page_texts(result["id"]) == {1: f"{TEXT}\n\n{chart_text(1)}"}
    assert vision_pages(fake_llm, "describe") == [1]


@pytest.mark.parametrize(
    ("reply", "cleaned"),
    [
        ("Here is the transcription:\nInvoice 1234\nTotal due", "Invoice 1234\nTotal due"),
        ("Sure! Here's the text of page 1:\n\nInvoice", "Invoice"),
        ("```\nInvoice 1234\n```", "Invoice 1234"),
        ("```markdown\n| Item | Total |\n| --- | --- |\n| A | 1 |\n```", "| Item | Total |\n| --- | --- |\n| A | 1 |"),
        ("Line\x00 one\x07\r\nTwo\x0bThree", "Line one\nTwo\nThree"),
        ("Here is a table that matters: it has columns.\nRow", "Here is a table that matters: it has columns.\nRow"),
        ("No content was found apart from this sentence.", "No content was found apart from this sentence."),
        ("  NO_CONTENT  ", ""),
        (None, ""),
    ],
)
def test_clean_output(reply: str | None, cleaned: str) -> None:
    assert vision.clean_output(reply) == cleaned


def test_clean_output_caps_a_page_at_20000_characters() -> None:
    assert vision.clean_output("x" * 50_000) == "x" * 20_000


async def test_the_text_limit_applies_after_the_merge(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_extracted_chars", 150)
    fake_llm.vision_reply = "[Chart] " + "A long description. " * 10

    result = await ingest(alice, "figure.pdf", image_pdf_bytes((TEXT, 0.3)))

    assert (result["status"], result["error"]) == ("failed", "The document has too much text (over 150 characters).")


async def test_a_truncated_reply_is_marked(
    alice: ApiClient, fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    fake_llm.vision_finish_reason = "length"

    with caplog.at_level(logging.WARNING):
        result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    assert await page_texts(result["id"]) == {
        1: f"{scanned_text(1)}\n\n[Truncated: the vision model reached its output limit on this page.]"
    }
    assert any("reached its output limit" in message for message in caplog.messages)


async def test_usage_is_estimated_without_reported_tokens(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.vision_usage = None

    await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    [event] = await events("vision")
    assert event.prompt_tokens > 1212 * 1568 // 784  # the image's patches plus the prompt
    assert 0 < event.completion_tokens < 100


# ---------------- Limits ----------------


async def test_calls_per_document_are_limited(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "vision_concurrency", 3)
    fake_llm.vision_delay = 0.05

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes(*(f"Page {n}" for n in range(1, 8))))

    assert result["status"] == "ready"
    assert len(fake_llm.vision_calls) == 7
    assert fake_llm.vision_max_in_flight == 3


async def test_calls_in_the_whole_worker_are_limited(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "vision_max_in_flight", 2)
    fake_llm.vision_delay = 0.05
    first = await uploaded_id(alice, "first.pdf", scanned_pdf_bytes("A1", "A2", "A3"))
    second = await uploaded_id(alice, "second.pdf", scanned_pdf_bytes("B1", "B2", "B3"))

    await asyncio.gather(process(first), process(second))

    assert len(fake_llm.vision_calls) == 6
    assert fake_llm.vision_max_in_flight == 2


async def test_the_document_deadline_stops_new_calls_and_keeps_what_was_read(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, one_call_at_a_time: None
) -> None:
    monkeypatch.setattr(settings, "vision_timeout_seconds", 0.4)
    fake_llm.vision_delay = 0.25

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("One", "Two", "Three"))

    assert result["status"] == "failed"
    assert result["error"] == (
        "Reading this PDF with the vision model took longer than 1 minute. "
        "Retry continues with the pages that weren't read yet."
    )
    assert vision_pages(fake_llm) == [1, 2]
    assert [row[1] for row in await cache_rows(result["id"])] == [1, 2]
    [event] = await events("vision")
    assert event.total_tokens == 2 * 1700


# ---------------- Failures ----------------


async def test_a_failed_scanned_page_fails_the_document_and_keeps_the_usage(
    alice: ApiClient, fake_llm: FakeLLM, one_call_at_a_time: None
) -> None:
    fake_llm.vision_reply = raising(
        (2, litellm.AuthenticationError(message="bad key", llm_provider="anthropic", model="x"))
    )

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("One", "Two", "Three"))

    assert (result["status"], result["error"]) == (
        "failed",
        f"Page 2 couldn't be read with the vision model: {AUTH_FAILED}",
    )
    assert vision_pages(fake_llm) == [1, 2]
    [event] = await events("vision")
    assert event.total_tokens == 1700
    assert await payloads(result["id"]) == []
    assert await events("embedding") == []

    fake_llm.vision_reply = scanned_text(0)  # any answer
    await alice.post(f"/api/documents/{result['id']}/reprocess")
    await process(result["id"])
    assert (await item(alice, result["id"]))["status"] == "ready"
    assert vision_pages(fake_llm) == [1, 2, 2, 3]


async def test_calls_in_flight_finish_after_a_failure_and_no_new_call_starts(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "vision_concurrency", 3)
    fake_llm.vision_delay = 0.05
    fake_llm.vision_reply = raising(
        (1, litellm.AuthenticationError(message="bad key", llm_provider="anthropic", model="x"))
    )

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("One", "Two", "Three", "Four"))

    assert result["error"] == f"Page 1 couldn't be read with the vision model: {AUTH_FAILED}"
    assert sorted(vision_pages(fake_llm)) == [1, 2, 3]
    [event] = await events("vision")
    assert event.total_tokens == 2 * 1700
    assert [row[1] for row in await cache_rows(result["id"])] == [2, 3]


async def test_a_failed_description_keeps_the_page_text(
    alice: ApiClient, fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    fake_llm.vision_reply = raising((1, litellm.BadRequestError(message="no", llm_provider="anthropic", model="x")))

    with caplog.at_level(logging.WARNING):
        result = await ingest(alice, "figure.pdf", image_pdf_bytes((TEXT, 0.3)))

    assert result["status"] == "ready"
    assert await page_texts(result["id"]) == {1: TEXT}
    assert result["vision_pages"] == 0
    assert any("figure.pdf page 1: image description skipped: " in message for message in caplog.messages)


async def test_a_fatal_error_on_a_description_stops_further_calls(
    alice: ApiClient, fake_llm: FakeLLM, one_call_at_a_time: None
) -> None:
    fake_llm.vision_reply = raising((1, litellm.NotFoundError(message="no model", llm_provider="anthropic", model="x")))
    pages = [(page_text(n), 0.3) for n in (1, 2, 3)]

    result = await ingest(alice, "figures.pdf", image_pdf_bytes(*pages, shared_image=False))

    assert result["status"] == "ready"
    assert vision_pages(fake_llm) == [1]
    assert await page_texts(result["id"]) == {n: page_text(n) for n in (1, 2, 3)}


async def test_a_fatal_error_on_a_description_fails_the_scanned_pages_with_its_reason(
    alice: ApiClient, fake_llm: FakeLLM, one_call_at_a_time: None
) -> None:
    fake_llm.vision_reply = raising(
        (1, litellm.AuthenticationError(message="bad key", llm_provider="anthropic", model="x"))
    )
    figure_then_scan = image_pdf_bytes((page_text(1), 0.3), ("", 1.0), shared_image=False)

    result = await ingest(alice, "mixed.pdf", figure_then_scan)

    # Not the deadline message: the run stopped on the rejected key, long before VISION_TIMEOUT_SECONDS.
    assert (result["status"], result["error"]) == (
        "failed",
        f"Page 2 couldn't be read with the vision model: {AUTH_FAILED}",
    )
    assert vision_pages(fake_llm) == [1]

    await alice.post(f"/api/documents/{result['id']}/reprocess")
    await process(result["id"])
    assert (await item(alice, result["id"]))["status"] == "ready"
    assert vision_pages(fake_llm) == [1, 1, 2]


async def test_a_description_reply_without_the_image_fails_the_scanned_pages(
    alice: ApiClient, fake_llm: FakeLLM, one_call_at_a_time: None
) -> None:
    fake_llm.vision_reply = "NO_IMAGE"

    result = await ingest(alice, "mixed.pdf", image_pdf_bytes((page_text(1), 0.3), ("", 1.0), shared_image=False))

    assert result["error"] == (
        "Page 2 couldn't be read with the vision model: "
        "The model didn't receive the page image; check that it supports images."
    )
    assert vision_pages(fake_llm) == [1]


async def test_a_missing_key_on_descriptions_names_the_key_for_a_later_scanned_page(
    isolated_state: Path, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three description units fill VISION_CONCURRENCY (3); the scanned page waits and is never called."""
    monkeypatch.setattr(settings, "vision_concurrency", 3)
    monkeypatch.delenv("MISSING_VISION_API_KEY", raising=False)
    image = isolated_state / "page.jpg"
    Image.new("RGB", (10, 10), "white").save(image, "JPEG")
    parsed = ParsedDocument(
        segments=[Segment(page_text(n), n) for n in (1, 2, 3)],
        num_pages=4,
        page_images=[
            *(PageImage(n, (n,), "describe", image, (10, 10)) for n in (1, 2, 3)),
            PageImage(4, (4,), "transcribe", image, (10, 10)),
        ],
    )

    async def nothing() -> None:
        return None

    run = vision.VisionRun(
        doc_id=1,
        model=LLMModel(litellm_model=SONNET, kind="vision", api_key_env="MISSING_VISION_API_KEY"),
        fingerprint="f",
        cached={},
        usage=llm.Usage(),
        checkpoint=nothing,
        deadline=time.monotonic() + 60,
    )

    with pytest.raises(DocumentParseError) as raised:
        await vision.read_pages(run, parsed, "doc.pdf")

    assert str(raised.value) == (
        "Page 4 couldn't be read with the vision model: This model's API key (MISSING_VISION_API_KEY) is not set on "
        "the server. Add it to .env and run docker compose up -d."
    )
    assert fake_llm.vision_calls == []


async def test_a_rate_limit_is_retried_after_the_providers_delay(
    alice: ApiClient, fake_llm: FakeLLM, vision_sleeps: list[float]
) -> None:
    limited = litellm.RateLimitError(
        message="slow down",
        llm_provider="anthropic",
        model="claude-sonnet-5-5",
        response=httpx.Response(429, headers={"retry-after": "7"}, request=httpx.Request("POST", "https://x")),
    )
    fake_llm.vision_reply = raising((1, limited))

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    assert result["status"] == "ready"
    assert vision_sleeps == [7.0]
    assert vision_pages(fake_llm) == [1, 1]


@pytest.mark.parametrize(("header", "expected"), [("0.2", 1.0), ("3600", 60.0)])
async def test_retry_after_is_clamped(
    alice: ApiClient, fake_llm: FakeLLM, vision_sleeps: list[float], header: str, expected: float
) -> None:
    response = httpx.Response(429, headers={"retry-after": header}, request=httpx.Request("POST", "https://x"))
    fake_llm.vision_reply = raising(
        (1, litellm.RateLimitError(message="slow", llm_provider="anthropic", model="x", response=response))
    )

    await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    assert vision_sleeps == [expected]


async def test_overloaded_providers_are_retried_with_backoff_then_fail(
    alice: ApiClient, fake_llm: FakeLLM, vision_sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vision, "_RETRY_DELAYS", (2.0, 5.0, 15.0, 40.0))

    def overloaded(page: int, mode: str) -> str:
        raise litellm.InternalServerError(message="Overloaded", llm_provider="anthropic", model="x")

    fake_llm.vision_reply = overloaded

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    assert vision_sleeps == [2.0, 5.0, 15.0, 40.0]
    assert len(fake_llm.vision_calls) == 5
    assert result["error"].startswith("Page 1 couldn't be read with the vision model: ")


async def test_a_timeout_is_not_retried(alice: ApiClient, fake_llm: FakeLLM, vision_sleeps: list[float]) -> None:
    fake_llm.vision_reply = raising((1, litellm.Timeout(message="timed out", model="x", llm_provider="anthropic")))

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    assert result["error"] == "Page 1 couldn't be read with the vision model: The model provider timed out."
    assert (len(fake_llm.vision_calls), vision_sleeps) == (1, [])


async def test_a_reply_without_the_image_fails_the_page(alice: ApiClient, fake_llm: FakeLLM) -> None:
    fake_llm.vision_reply = "NO_IMAGE"

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    assert result["error"] == (
        "Page 1 couldn't be read with the vision model: "
        "The model didn't receive the page image; check that it supports images."
    )


async def test_an_unexpected_error_in_one_page_is_a_clean_failure(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    read_image = llm.read_image

    async def broken(model: LLMModel, jpeg: bytes, **kwargs: Any) -> llm.VisionReply:
        if "Page 2" in kwargs["instruction"]:
            raise ValueError("a bug")
        return await read_image(model, jpeg, **kwargs)

    monkeypatch.setattr(llm, "read_image", broken)

    with caplog.at_level(logging.ERROR):
        result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("One", "Two"))

    assert result["error"] == "Page 2 couldn't be read with the vision model: an unexpected error occurred"
    assert "sub-exception" not in result["error"]
    assert any("Reading page 2 of scan.pdf with the vision model failed" in message for message in caplog.messages)


async def test_a_missing_model_key_fails_the_document(
    alice: ApiClient, admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    model = await vision_model()
    await admin_client.patch(f"/api/admin/models/{model.id}", json={"api_key_env": "MISSING_VISION_API_KEY"})

    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))

    assert result["error"] == (
        "Page 1 couldn't be read with the vision model: This model's API key (MISSING_VISION_API_KEY) is not set on "
        "the server. Add it to .env and run docker compose up -d."
    )
    assert fake_llm.vision_calls == []


# ---------------- Stop checks ----------------


async def test_deleting_the_document_stops_the_run_quietly(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, one_call_at_a_time: None
) -> None:
    doc_id = await uploaded_id(alice, "scan.pdf", scanned_pdf_bytes("One", "Two", "Three"))

    async def delete_after_the_first_call(call: int) -> None:
        if call == 1:
            assert (await alice.delete(f"/api/documents/{doc_id}")).status_code == 204

    wrap_read_image(monkeypatch, delete_after_the_first_call, after=True)

    assert await process(doc_id) is None
    assert len(fake_llm.vision_calls) == 1
    [event] = await events("vision")
    assert (event.user_id, event.total_tokens) == (alice.user_id, 1700)
    assert await cache_rows() == []


async def test_reprocessing_during_the_vision_phase_stops_the_run_and_resumes_from_the_cache(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, one_call_at_a_time: None
) -> None:
    doc_id = await uploaded_id(alice, "scan.pdf", scanned_pdf_bytes("One", "Two", "Three", "Four"))

    async def reprocess_after_the_second_call(call: int) -> None:
        if call == 2:
            assert (await alice.post(f"/api/documents/{doc_id}/reprocess")).status_code == 200

    wrap_read_image(monkeypatch, reprocess_after_the_second_call, after=True)

    stopped = await process(doc_id)
    resumed = await process(doc_id)

    assert stopped is not None and (stopped.status, stopped.error) == ("pending", None)
    assert resumed is not None and resumed.status == "ready"
    assert vision_pages(fake_llm) == [1, 2, 3, 4]
    assert [event.total_tokens for event in await events("vision")] == [3400, 3400]


async def test_disabling_the_vision_model_mid_run_queues_the_document_again(
    alice: ApiClient,
    admin_client: ApiClient,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    one_call_at_a_time: None,
) -> None:
    model = await vision_model()
    doc_id = await uploaded_id(alice, "scan.pdf", scanned_pdf_bytes("One", "Two"))

    async def disable(call: int) -> None:
        if call == 1:
            response = await admin_client.patch(f"/api/admin/models/{model.id}", json={"enabled": False})
            assert response.status_code == 200

    wrap_read_image(monkeypatch, disable)
    requeued = await process(doc_id)
    queue = await get_redis().lrange(INGEST_QUEUE, 0, -1)
    final = await process(doc_id)

    assert requeued is not None and requeued.status == "pending"
    assert queue == [str(doc_id)]
    assert final is not None and (final.status, final.error) == ("failed", NO_VISION)
    assert len(fake_llm.vision_calls) == 1
    [event] = await events("vision")
    assert event.model_id == model.id


async def test_deleting_the_vision_model_mid_run_records_its_usage_by_name(
    alice: ApiClient,
    admin_client: ApiClient,
    fake_llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    one_call_at_a_time: None,
) -> None:
    model = await vision_model()
    doc_id = await uploaded_id(alice, "scan.pdf", scanned_pdf_bytes("One", "Two"))

    async def delete_model(call: int) -> None:
        if call == 1:
            assert (await admin_client.delete(f"/api/admin/models/{model.id}")).status_code == 204

    wrap_read_image(monkeypatch, delete_model)

    requeued = await process(doc_id)

    assert requeued is not None and requeued.status == "pending"
    assert await get_redis().lrange(INGEST_QUEUE, 0, -1) == [str(doc_id)]
    [event] = await events("vision")
    assert (event.model_id, event.model_name, event.total_tokens) == (None, SONNET, 1700)


async def test_cancelling_mid_vision_records_the_usage_and_resets_the_attempts(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, one_call_at_a_time: None
) -> None:
    doc_id = await uploaded_id(alice, "scan.pdf", scanned_pdf_bytes("One", "Two"))
    second_call = asyncio.Event()

    async def hang_on_the_second_call(call: int) -> None:
        if call == 2:
            second_call.set()
            await asyncio.Event().wait()

    wrap_read_image(monkeypatch, hang_on_the_second_call)
    await get_redis().lrem(INGEST_QUEUE, 0, str(doc_id))
    job = asyncio.create_task(ingestion.process_document(doc_id))
    await asyncio.wait_for(second_call.wait(), GUARD_SECONDS)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    [event] = await events("vision")
    assert event.total_tokens == 1700
    row = await document(doc_id)
    assert row is not None and row.status == "processing"
    assert await get_redis().exists(ingest_attempts_key(doc_id)) == 0
    assert [entry[1] for entry in await cache_rows(doc_id)] == [1]


async def test_cancelling_during_the_parse_still_counts_the_attempt(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = await uploaded_id(alice, "scan.pdf", scanned_pdf_bytes("One"))
    parsing, release = threading.Event(), threading.Event()
    parse = ingestion._parse

    def slow_parse(stored_name: str, request: VisionRequest | None) -> ParsedDocument:
        parsing.set()
        release.wait(GUARD_SECONDS)
        return parse(stored_name, request)

    monkeypatch.setattr(ingestion, "_parse", slow_parse)
    await get_redis().lrem(INGEST_QUEUE, 0, str(doc_id))
    job = asyncio.create_task(ingestion.process_document(doc_id))
    await wait_for(lambda: asyncio.sleep(0, parsing.is_set()))
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    release.set()

    assert await get_redis().get(ingest_attempts_key(doc_id)) == "1"


# ---------------- The vision allowance (per period; an open-ended plan: per UTC month) ----------------


async def add_vision_usage(user_id: int, tokens: int) -> None:
    async with SessionLocal() as session:
        session.add(
            UsageEvent(user_id=user_id, model_name=SONNET, kind="vision", prompt_tokens=tokens, total_tokens=tokens)
        )
        await session.commit()


async def test_a_used_up_allowance_fails_uncached_scans_without_any_call(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, child_starts: list[int]
) -> None:
    await vision_allowance(alice.user_id, 5000)  # type: ignore[arg-type]
    await add_vision_usage(alice.user_id, 5000)  # type: ignore[arg-type]

    scan = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))
    figure = await ingest(alice, "figure.pdf", image_pdf_bytes((TEXT, 0.3)))

    assert (scan["status"], scan["error"]) == (
        "failed",
        "This PDF has scanned pages, but this period's allowance for the vision model is used up (5,000 of 5,000 "
        "tokens). " + restarts(),
    )
    assert (figure["status"], await page_texts(figure["id"])) == ("ready", {1: TEXT})
    assert (fake_llm.vision_calls, child_starts) == ([], [])


async def test_cached_pages_are_used_over_the_allowance(
    alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))
    await vision_allowance(alice.user_id, 1700)  # type: ignore[arg-type]

    await alice.post(f"/api/documents/{result['id']}/reprocess")
    await process(result["id"])

    assert (await item(alice, result["id"]))["status"] == "ready"
    assert len(fake_llm.vision_calls) == 1


async def test_the_admin_is_exempt_and_zero_means_unlimited(
    admin_client: ApiClient, alice: ApiClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    await vision_allowance(alice.user_id, 100)  # type: ignore[arg-type]
    await add_vision_usage(admin_client.user_id, 10_000)  # type: ignore[arg-type]
    shared = await ingest(admin_client, "shared-scan.pdf", scanned_pdf_bytes("Shared"), "shared")
    await vision_allowance(alice.user_id, 0)  # type: ignore[arg-type]
    await add_vision_usage(alice.user_id, 10_000_000)  # type: ignore[arg-type]
    private = await ingest(alice, "scan.pdf", scanned_pdf_bytes("Private"))

    assert (shared["status"], private["status"]) == ("ready", "ready")
    assert len(fake_llm.vision_calls) == 2


# ---------------- Worker ----------------


async def test_a_document_reading_pages_gives_its_worker_slot_to_the_next(
    alice: ApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "_POLL_SECONDS", 0.05)
    release = asyncio.Event()

    async def wait_for_release(call: int) -> None:
        await release.wait()

    wrap_read_image(monkeypatch, wait_for_release)
    scan_id = await uploaded_id(alice, "scan.pdf", scanned_pdf_bytes("Invoice"))
    text_id = await uploaded_id(alice, "notes.txt", txt_bytes(TEXT))
    instance = worker.Worker(1)
    task = asyncio.create_task(instance.run())
    try:

        async def text_ready() -> bool:
            row = await document(text_id)
            return row is not None and row.status == "ready"

        await wait_for(text_ready)
        scan = await document(scan_id)
        assert scan is not None and scan.status == "processing"
        release.set()

        async def scan_ready() -> bool:
            row = await document(scan_id)
            return row is not None and row.status == "ready"

        await wait_for(scan_ready)
    finally:
        release.set()
        instance.stop()
        await asyncio.wait_for(task, GUARD_SECONDS)
    assert ingestion._vision_phase_listener is None


# ---------------- Admin ----------------


async def test_vision_models_are_never_chat_models(alice: ApiClient, admin_client: ApiClient) -> None:
    created = await admin_client.post(
        "/api/admin/models",
        json={"display_name": "Gemini vision", "litellm_model": "gemini/gemini-2.5-flash", "kind": "vision"},
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    assert created.json()["kind"] == "vision"

    listed = (await alice.get("/api/models")).json()
    refused = await admin_client.patch(
        f"/api/admin/users/{alice.user_id}", json={"restrict_models": True, "allowed_model_ids": [model_id]}
    )
    response, _ = await chat(alice, "Hello", model_id=model_id)

    assert model_id not in {model["id"] for model in listed}
    assert all(model["display_name"] != "Claude Sonnet 5.5 (vision)" for model in listed)
    assert refused.status_code == 422
    assert (response.status_code, response.json()) == (400, {"detail": "This model is not available."})


async def test_vision_defaults_are_per_kind_and_the_active_model_has_no_protection(
    admin_client: ApiClient,
) -> None:
    seeded = await vision_model()
    created = await admin_client.post(
        "/api/admin/models",
        json={
            "display_name": "Mini vision",
            "litellm_model": "openai/gpt-5.4-mini",
            "kind": "vision",
            "is_default": True,
        },
    )
    assert created.status_code == 201
    models = {model["display_name"]: model for model in (await admin_client.get("/api/admin/models")).json()}
    assert models["Mini vision"]["is_default"] is True
    assert models[seeded.display_name]["is_default"] is False
    assert models["Claude Opus 5.5"]["is_default"] is True
    stats = (await admin_client.get("/api/admin/stats")).json()
    assert stats["active_vision_model"] == {"id": created.json()["id"], "display_name": "Mini vision"}

    model_id = created.json()["id"]
    undefaulted = await admin_client.patch(f"/api/admin/models/{model_id}", json={"is_default": False})
    redefaulted = await admin_client.patch(f"/api/admin/models/{model_id}", json={"is_default": True})
    disabled = await admin_client.patch(f"/api/admin/models/{model_id}", json={"enabled": False})
    stats_after = (await admin_client.get("/api/admin/stats")).json()
    deleted = await admin_client.delete(f"/api/admin/models/{model_id}")

    assert [r.status_code for r in (undefaulted, redefaulted, disabled, deleted)] == [200, 200, 200, 204]
    assert stats_after["active_vision_model"] is None


async def test_the_vision_test_reads_the_test_image(admin_client: ApiClient, fake_llm: FakeLLM) -> None:
    model = await vision_model()

    response = await admin_client.post(f"/api/admin/models/{model.id}/test")

    assert response.status_code == 200
    result = response.json()
    assert result == {
        "ok": True,
        "message": "OK: read the test image as 'TEST 4712'",
        "latency_ms": result["latency_ms"],
        "embedding_dim": None,
        "image_data_url": None,
    }
    [call] = fake_llm.vision_calls
    assert vision_page_and_mode(call) == (0, "test")
    assert call["messages"][0] == {"role": "system", "content": vision.SYSTEM_PROMPT}
    url = vision_part(call, "image_url")["image_url"]["url"]
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        assert (image.format, image.size) == ("JPEG", (480, 160))
    assert (call["max_tokens"], call["reasoning_effort"], call["max_retries"]) == (8192, "low", 0)
    assert fake_llm.chat_calls == []


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("OK", "The model replied 'OK' but didn't read the test image."),
        ("NO_IMAGE", "The model replied 'NO_IMAGE' but didn't read the test image."),
    ],
)
async def test_the_vision_test_fails_when_the_image_is_not_read(
    admin_client: ApiClient, fake_llm: FakeLLM, reply: str, message: str
) -> None:
    fake_llm.vision_reply = reply
    model = await vision_model()

    result = (await admin_client.post(f"/api/admin/models/{model.id}/test")).json()

    assert (result["ok"], result["message"]) == (False, message)


async def test_the_vision_test_reports_provider_errors_and_unlisted_models(
    admin_client: ApiClient, fake_llm: FakeLLM
) -> None:
    created = await admin_client.post(
        "/api/admin/models",
        json={"display_name": "Text only", "litellm_model": "openai/some-text-only-model", "kind": "vision"},
    )
    fake_llm.vision_reply = raising(
        (0, litellm.AuthenticationError(message="bad key", llm_provider="openai", model="x"))
    )

    failed = (await admin_client.post(f"/api/admin/models/{created.json()['id']}/test")).json()
    fake_llm.vision_reply = "OK"
    unread = (await admin_client.post(f"/api/admin/models/{created.json()['id']}/test")).json()

    note = " LiteLLM doesn't list this model as supporting images."
    assert (failed["ok"], failed["message"]) == (False, AUTH_FAILED + note)
    assert (unread["ok"], unread["message"]) == (False, "The model replied 'OK' but didn't read the test image." + note)


@pytest.mark.filterwarnings("ignore:Item 'summary' on TypedDict:UserWarning")  # from LiteLLM's response types
async def test_reasoning_effort_reaches_each_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiteLLM's own request transformation, with the HTTP layer replaced (no network)."""
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

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(litellm, "acompletion", litellm.main.acompletion)
    monkeypatch.setattr(AsyncHTTPHandler, "post", post)
    monkeypatch.setattr(OpenAIChatCompletion, "make_openai_chat_completion_request", openai_request)
    jpeg = vision._test_image()

    for name in (SONNET, "openai/gpt-5.4-mini", "gemini/gemini-2.5-flash"):
        model = LLMModel(display_name=name, litellm_model=name, kind="vision", api_key_env=None, max_output_tokens=None)
        with pytest.raises(llm.VisionCallError):
            await llm.read_image(model, jpeg, system="System.", instruction="Read it.", image_size=(480, 160))

    anthropic = next(body for url, body in bodies.items() if "anthropic" in url)
    gemini = next(body for url, body in bodies.items() if "googleapis" in url)
    assert anthropic["output_config"] == {"effort": "low"}
    assert anthropic["thinking"]["type"] == "adaptive"
    assert anthropic["max_tokens"] == 8192
    assert [part["type"] for part in anthropic["messages"][0]["content"]] == ["image", "text"]
    assert (bodies["openai"]["reasoning_effort"], bodies["openai"]["max_completion_tokens"]) == ("low", 8192)
    assert 0 < gemini["generationConfig"]["thinkingConfig"]["thinkingBudget"] <= 2048


@contextlib.contextmanager
def local_provider(status: int | None) -> Iterator[tuple[str, list[float]]]:
    """An OpenAI-compatible endpoint on 127.0.0.1 that answers every request with an error `status`, or never
    answers (None) until the block ends. Yields its api_base and the times of the requests it got."""
    requests: list[float] = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            requests.append(time.monotonic())
            self.rfile.read(int(self.headers.get("content-length") or 0))
            if status is None:
                release.wait(GUARD_SECONDS)
                return
            body = b'{"error": {"message": "provider error", "type": "server_error"}}'
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()


@pytest.mark.filterwarnings("ignore:Item 'summary' on TypedDict:UserWarning")  # from LiteLLM's response types
@pytest.mark.parametrize(
    ("status", "retryable", "message"),
    [
        (500, True, "Model call failed: "),
        (429, True, "The model provider is rate limiting requests. Please try again shortly."),
        (None, False, "The model provider timed out."),
    ],
)
async def test_one_vision_call_is_one_provider_request(
    monkeypatch: pytest.MonkeyPatch, status: int | None, retryable: bool, message: str
) -> None:
    """The OpenAI SDK (openai/* and OpenAI-compatible hosts) retries 429s, 5xx and timeouts twice inside one call
    unless told not to; read_pages alone decides retries, delays and Retry-After."""
    monkeypatch.setattr(litellm, "acompletion", litellm.main.acompletion)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 1)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with local_provider(status) as (api_base, requests):
        model = LLMModel(
            display_name="Local vision",
            litellm_model="openai/gpt-5.4-mini",
            kind="vision",
            api_base=api_base,
            api_key_env=None,
            max_output_tokens=None,
        )
        with pytest.raises(llm.VisionCallError) as raised:
            await llm.read_image(
                model, vision._test_image(), system="System.", instruction="Read it.", image_size=(480, 160)
            )

    assert len(requests) == 1
    assert raised.value.retryable is retryable
    assert str(raised.value).startswith(message)


# ---------------- Bootstrap ----------------


async def run_bootstrap() -> None:
    async with SessionLocal() as session:
        await bootstrap(session)


async def vision_rows() -> list[tuple[str, str, bool, bool]]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(LLMModel.display_name, LLMModel.litellm_model, LLMModel.enabled, LLMModel.is_default)
            .where(LLMModel.kind == "vision")
            .order_by(LLMModel.id)
        )
        return list(rows.tuples())


async def other_models() -> list[tuple[Any, ...]]:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(LLMModel.id, LLMModel.display_name, LLMModel.enabled, LLMModel.is_default, LLMModel.updated_at)
            .where(LLMModel.kind != "vision")
            .order_by(LLMModel.id)
        )
        return list(rows.tuples())


async def marker() -> str | None:
    async with SessionLocal() as session:
        row = await session.get(AppState, "vision_seeded")
    return row.value if row is not None else None


async def test_an_existing_database_gets_the_vision_model_once(app: object) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(LLMModel).where(LLMModel.kind == "vision"))
        await session.execute(delete(AppState))
        await session.commit()
    before = await other_models()

    await run_bootstrap()
    await run_bootstrap()

    assert await vision_rows() == [("Claude Sonnet 5.5 (vision)", SONNET, True, True)]
    assert await other_models() == before
    assert await marker() == SONNET


async def test_an_existing_vision_model_is_kept_and_recorded(app: object) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AppState))
        await session.execute(update(LLMModel).where(LLMModel.kind == "vision").values(display_name="Our vision"))
        await session.commit()

    await run_bootstrap()

    assert await vision_rows() == [("Our vision", SONNET, True, True)]
    assert await marker() == "existing"


async def test_a_deleted_vision_model_is_not_added_again(app: object, admin_client: ApiClient) -> None:
    model = await vision_model()
    assert (await admin_client.delete(f"/api/admin/models/{model.id}")).status_code == 204

    await run_bootstrap()

    assert await vision_rows() == []
    assert (await admin_client.get("/api/admin/stats")).json()["active_vision_model"] is None


async def test_two_backends_seeding_at_once_add_one_vision_model(app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(LLMModel).where(LLMModel.kind == "vision"))
        await session.execute(delete(AppState))
        await session.commit()
    commit = AsyncSession.commit
    raced: list[bool] = []

    async def racing_commit(self: AsyncSession) -> None:
        if not raced and any(isinstance(row, AppState) for row in self.new):
            raced.append(True)
            async with SessionLocal() as other:  # the other backend commits first
                other.add(LLMModel(display_name="Other", litellm_model=SONNET, kind="vision", is_default=True))
                other.add(AppState(key="vision_seeded", value=SONNET))
                await commit(other)
        await commit(self)

    monkeypatch.setattr(AsyncSession, "commit", racing_commit)

    await run_bootstrap()

    assert raced == [True]
    assert [row[0] for row in await vision_rows()] == ["Other"]
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1  # the admin sync was kept


# ---------------- Config ----------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vision_max_pages", 0),
        ("vision_max_pages", 201),
        ("vision_concurrency", 0),
        ("vision_concurrency", 17),
        ("vision_max_in_flight", 65),
        ("vision_timeout_seconds", 59),
        ("vision_min_text_chars", 10_001),
        ("vision_min_image_ratio", 0),
        ("vision_min_image_ratio", 1.5),
        ("vision_max_image_px", 255),
        ("vision_max_image_px", 2577),
        ("vision_max_output_tokens", 128_001),
    ],
)
def test_vision_settings_are_bounded(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})  # type: ignore[call-arg]


def test_vision_settings_defaults() -> None:
    defaults = Settings()  # type: ignore[call-arg]

    assert (
        defaults.vision_max_pages,
        defaults.vision_concurrency,
        defaults.vision_max_in_flight,
        defaults.vision_timeout_seconds,
        defaults.vision_min_text_chars,
        defaults.vision_min_image_ratio,
        defaults.vision_max_image_px,
        defaults.vision_max_output_tokens,
    ) == (50, 3, 4, 1800, 40, 0.1, 1568, 8192)


# ---------------- read_pages directly ----------------


async def test_read_pages_merges_units_onto_all_their_pages(
    isolated_state: Path, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = isolated_state / "page.jpg"
    Image.new("RGB", (10, 10), "white").save(image, "JPEG")
    parsed = ParsedDocument(
        segments=[],
        num_pages=3,
        page_images=[PageImage(1, (1, 3), "transcribe", image, (10, 10))],
    )

    async def nothing() -> None:
        return None

    run = vision.VisionRun(
        doc_id=1,
        model=LLMModel(litellm_model=SONNET, kind="vision", input_cost_per_mtok=2.0, output_cost_per_mtok=10.0),
        fingerprint="f",
        cached={},
        usage=llm.Usage(),
        checkpoint=nothing,
        deadline=time.monotonic() + 60,
    )

    async def no_database(*args: Any) -> bool:
        return True

    monkeypatch.setattr(vision, "_store", no_database)

    segments = await vision.read_pages(run, parsed, "doc.pdf")

    assert [(segment.page, segment.text) for segment in segments] == [(1, scanned_text(1)), (3, scanned_text(1))]
    assert run.used == {(1, "transcribe")}
    assert (run.usage.prompt_tokens, run.usage.completion_tokens) == (1500, 200)
    assert run.usage.cost_usd == pytest.approx(0.005)
    assert len(fake_llm.vision_calls) == 1
