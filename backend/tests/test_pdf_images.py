"""PDF image measurement (pypdf, PageMeasurer) and page rendering in the child process (render_pages)."""

import io
import logging
import os
import time
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import (
    RenderChild,
    image_pdf_bytes,
    pdf_bytes,
    pdf_stream,
    raw_pdf_bytes,
    scanned_pdf_bytes,
)
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject

from app.config import settings
from app.services import pdf_images
from app.services.parsing import DocumentParseError, VisionRequest, _TextBudget, parse_file
from app.services.pdf_images import PageMeasure, PageMeasurer, Rendered, RenderJob, render_pages, union_share

LETTER = (0.0, 0.0, 612.0, 792.0)
TEXT = "The quarterly revenue report for the northern region shows growth."


def gray_image(width: int, height: int) -> bytes:
    return pdf_stream(
        b"\x80" * (width * height),
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray /BitsPerComponent 8",
    )


def flate_stream(data: bytes, entries: str = "") -> bytes:
    return pdf_stream(zlib.compress(data, 9), f"/Filter /FlateDecode {entries}")


def measure_all(data: bytes) -> list[PageMeasure | None]:
    reader = PdfReader(io.BytesIO(data))
    measurer = PageMeasurer(reader)
    return [measurer.measure(page) for page in reader.pages]


def measure(data: bytes) -> PageMeasure:
    [result] = measure_all(data)
    assert result is not None
    return result


def cover(result: PageMeasure) -> float:
    return union_share((image.rect for image in result.images), LETTER)


def strips(count: int) -> tuple[bytes, dict[int, bytes], str]:
    """A scan stored as `count` horizontal strips covering the page: content, objects, XObject resources."""
    height = 792 / count
    content = b"".join(b"q 612 0 0 %.3f 0 %.3f cm /S%d Do Q\n" % (height, height * k, k) for k in range(count))
    objects = {100 + k: gray_image(100, 12) for k in range(count)}
    return content, objects, " ".join(f"/S{k} {100 + k} 0 R" for k in range(count))


def nested_forms(depth: int, *, self_drawing: bool = False) -> bytes:
    """Forms nested `depth` deep, the innermost drawing a full-page image; or one form that draws itself."""
    if self_drawing:
        objects = {
            100: gray_image(10, 10),
            101: pdf_stream(
                b"q /Fm Do Q q 612 0 0 792 0 0 cm /Im Do Q",
                "/Type /XObject /Subtype /Form /BBox [0 0 612 792] "
                "/Resources << /XObject << /Fm 101 0 R /Im 100 0 R >> >>",
            ),
        }
        return raw_pdf_bytes(b"/Fm Do", objects=objects, xobjects="/Fm 101 0 R")
    return raw_pdf_bytes(b"/Fm Do", objects=nested_forms_objects(depth), xobjects="/Fm 101 0 R")


def nested_forms_objects(depth: int = 1) -> dict[int, bytes]:
    """Form 101 drawing form 102 ... down to `depth` forms, the innermost drawing image 100 over the page."""
    objects = {100: gray_image(10, 10)}
    for level in range(depth):
        number = 101 + level
        if level == depth - 1:
            content, resources = b"q 612 0 0 792 0 0 cm /Im Do Q", "/Im 100 0 R"
        else:
            content, resources = b"/Fm Do", f"/Fm {number + 1} 0 R"
        objects[number] = pdf_stream(
            content, f"/Type /XObject /Subtype /Form /BBox [0 0 612 792] /Resources << /XObject << {resources} >> >>"
        )
    return objects


def form_bomb(operators: int = 20_000, uses: int = 300) -> bytes:
    """The reviewer's bomb: one form with `operators` path operators drawn `uses` times, next to a small image."""
    objects = {
        100: flate_stream(b"0 0 m 1 1 l S\n" * operators, "/Type /XObject /Subtype /Form /BBox [0 0 612 792]"),
        101: gray_image(10, 10),
    }
    content = b"q 612 0 0 792 0 0 cm /Im Do Q\n" + b"q /F1 Do Q\n" * uses
    return raw_pdf_bytes(content, objects=objects, xobjects="/F1 100 0 R /Im 101 0 R")


def stream_bomb(megabytes: int = 14) -> bytes:
    """A page whose content decodes to `megabytes` MB of path operators, with a page-covering image."""
    operators = b"0 0 m 1 1 l S\n" * (megabytes * 1024 * 1024 // 14)
    return raw_pdf_bytes(
        b"q 612 0 0 792 0 0 cm /Im Do Q\n" + operators,
        objects={100: gray_image(10, 10)},
        xobjects="/Im 100 0 R",
        compress=True,
    )


def write_pdf(directory: Path, data: bytes, name: str = "doc.pdf") -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


def letter_jobs(*pages: int, rotate: int = 0) -> list[RenderJob]:
    return [RenderJob(page=page, width=612.0, height=792.0, rotate=rotate) for page in pages]


# ---------------- Walker ----------------


def test_one_image_covers_its_share_of_the_page() -> None:
    result = measure(image_pdf_bytes((TEXT, 0.3)))

    [image] = result.images
    assert image.share == pytest.approx(0.3)
    assert image.rect == pytest.approx((0.0, 0.0, 612.0, 792 * 0.3))
    assert image.pixels == 400 * 250
    assert image.key is not None
    assert cover(result) == pytest.approx(0.3)
    assert result.renderable is True


def test_a_scan_stored_as_strips_covers_the_whole_page() -> None:
    content, objects, xobjects = strips(8)

    result = measure(raw_pdf_bytes(content, objects=objects, xobjects=xobjects))

    assert len(result.images) == 8
    assert all(image.share == pytest.approx(1 / 8) for image in result.images)
    assert cover(result) == pytest.approx(1.0)


def test_overlapping_images_are_counted_once() -> None:
    content = b"q 612 0 0 475.2 0 0 cm /A Do Q q 612 0 0 475.2 0 316.8 cm /B Do Q q 612 0 0 475.2 0 0 cm /A Do Q"
    data = raw_pdf_bytes(
        content, objects={100: gray_image(4, 4), 101: gray_image(4, 4)}, xobjects="/A 100 0 R /B 101 0 R"
    )

    result = measure(data)

    assert [image.share for image in result.images] == pytest.approx([0.6, 0.6, 0.6])
    assert cover(result) == pytest.approx(1.0)
    assert union_share([(0, 0, 306, 396), (0, 0, 306, 396)], LETTER) == pytest.approx(0.25)


def test_an_image_in_a_form_counts_with_its_transformed_area() -> None:
    plain = measure(image_pdf_bytes((TEXT, 0.4)))
    in_form = measure(image_pdf_bytes((TEXT, 0.4), in_form=True))

    assert in_form.images[0].share == pytest.approx(0.4)
    assert in_form.images[0].rect == pytest.approx(plain.images[0].rect)


def test_an_inline_image_is_measured_without_a_key() -> None:
    content = b"q 306 0 0 396 0 0 cm BI /W 4 /H 4 /BPC 8 /CS /G ID " + b"\x80" * 16 + b"\nEI Q"

    result = measure(raw_pdf_bytes(content))

    [image] = result.images
    assert (image.key, image.pixels) == (None, 16)
    assert image.share == pytest.approx(0.25)


def test_a_rotated_page_keeps_its_share() -> None:
    assert measure(image_pdf_bytes((TEXT, 0.3), rotate=90)).images[0].share == pytest.approx(0.3)


def test_visible_and_invisible_text_bytes() -> None:
    content = (
        b"BT /F1 12 Tf 72 700 Td (Hello) Tj [(Wor) -20 (ld)] TJ (Next line) ' 1 2 (Spaced) \" "
        b"3 Tr (Hidden) Tj [(OCR) (layer)] TJ 7 Tr (Clip) Tj 0 Tr (Shown) Tj ET"
    )

    result = measure(raw_pdf_bytes(content))

    assert result.visible_bytes == len("HelloWorldNext lineSpacedShown")
    assert result.invisible_bytes == len("HiddenOCRlayerClip")
    assert result.images == ()


def test_q_and_Q_restore_the_transformation_and_the_text_render_mode() -> None:  # noqa: N802
    content = b"q 0.5 0 0 0.5 0 0 cm 3 Tr Q q 612 0 0 792 0 0 cm /Im Do Q BT /F1 12 Tf (Visible) Tj ET"

    result = measure(raw_pdf_bytes(content, objects={100: gray_image(4, 4)}, xobjects="/Im 100 0 R"))

    assert result.images[0].share == pytest.approx(1.0)
    assert (result.visible_bytes, result.invisible_bytes) == (len("Visible"), 0)


def test_an_image_outside_the_crop_box_covers_nothing() -> None:
    content = b"q 100 0 0 100 700 900 cm /Im Do Q q 1000 0 0 1000 -200 -100 cm /Im Do Q"

    result = measure(raw_pdf_bytes(content, objects={100: gray_image(4, 4)}, xobjects="/Im 100 0 R"))

    assert [image.share for image in result.images] == pytest.approx([0.0, 1.0])


# ---------------- Caps ----------------


def test_a_huge_content_stream_is_not_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed: list[int] = []
    operations = pdf_images.ContentStream.operations

    def spy(self: pdf_images.ContentStream) -> object:
        parsed.append(1)
        return operations.fget(self)  # type: ignore[attr-defined]

    monkeypatch.setattr(pdf_images.ContentStream, "operations", property(spy))
    started = time.monotonic()

    assert measure_all(stream_bomb(14)) == [None]
    assert parsed == []
    assert time.monotonic() - started < 5


def test_the_form_reuse_bomb_is_not_renderable_and_measured_quickly() -> None:
    started = time.monotonic()

    result = measure(form_bomb())

    assert time.monotonic() - started < 1
    assert result.renderable is False
    assert result.images[0].share == pytest.approx(1.0)  # the images seen before it stopped are kept


def test_too_many_draw_operations_are_not_renderable() -> None:
    content = b"q 612 0 0 792 0 0 cm /Im Do Q\n" + b"/Im Do\n" * 10_001

    result = measure(raw_pdf_bytes(content, objects={100: gray_image(4, 4)}, xobjects="/Im 100 0 R"))

    assert result.renderable is False
    assert len(result.images) == pdf_images._MAX_IMAGES_PER_PAGE


def test_forms_nested_past_depth_eight_and_a_form_drawing_itself_stop() -> None:
    assert measure(nested_forms(8)).images[0].share == pytest.approx(1.0)
    assert measure(nested_forms(9)).images == ()
    looped = measure(nested_forms(1, self_drawing=True))
    assert looped.renderable is True
    assert len(looped.images) == 1


def test_forms_are_parsed_once_per_document(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed: list[int] = []
    form = pdf_images.PageMeasurer._form

    def counted(self: PageMeasurer, key: tuple[int, int], form_object: object) -> object:
        if key not in self._forms:
            parsed.append(key[0])
        return form(self, key, form_object)  # type: ignore[arg-type]

    monkeypatch.setattr(pdf_images.PageMeasurer, "_form", counted)
    data = raw_pdf_bytes(
        b"/Fm Do", b"/Fm Do BT /F1 12 Tf (Second page) Tj ET", objects=nested_forms_objects(), xobjects="/Fm 101 0 R"
    )

    measures = measure_all(data)

    assert all(result is not None and result.images for result in measures)
    assert parsed == [101]  # drawn on both pages, parsed once


# ---------------- Candidates ----------------


def test_a_large_text_only_pdf_is_never_measured_or_rendered(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch, child_starts: list[int]
) -> None:
    measured: list[int] = []
    original = PageMeasurer.measure

    def spy(self: PageMeasurer, page: object) -> object:
        measured.append(1)
        return original(self, page)  # type: ignore[arg-type]

    monkeypatch.setattr(PageMeasurer, "measure", spy)
    path = write_pdf(isolated_state, pdf_bytes(*(f"Page {n}: {TEXT}" for n in range(300))))
    out_dir = isolated_state / "out"
    out_dir.mkdir()

    parsed = parse_file(path, ".pdf", VisionRequest(out_dir, frozenset()))

    assert len(parsed.segments) == 300
    assert parsed.page_images == []
    assert measured == []
    assert child_starts == []


def test_an_inline_image_makes_a_page_a_candidate() -> None:
    reader = PdfReader(io.BytesIO(raw_pdf_bytes(b"q 612 0 0 792 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x80\nEI Q")))

    assert PageMeasurer(reader).is_candidate(reader.pages[0]) is True


def test_an_image_inside_a_form_makes_a_page_a_candidate() -> None:
    reader = PdfReader(io.BytesIO(nested_forms(3)))

    assert PageMeasurer(reader).is_candidate(reader.pages[0]) is True


# ---------------- Rendering (the real child) ----------------


def test_a_letter_page_renders_with_the_configured_long_edge(isolated_state: Path) -> None:
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5)))

    [result] = render_pages(
        pdf, letter_jobs(1), 1, isolated_state, settings.vision_max_image_px, _TextBudget()
    ).values()

    assert isinstance(result, Rendered)
    with Image.open(result.path) as image:
        assert image.format == "JPEG"
        assert image.size == result.size == (1212, 1568)
        image.load()


def test_a_smaller_long_edge_setting_shrinks_the_image(isolated_state: Path) -> None:
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5)))

    [result] = render_pages(pdf, letter_jobs(1), 1, isolated_state, 800, _TextBudget()).values()

    assert isinstance(result, Rendered) and max(result.size) == 800


def test_a_small_page_is_rendered_at_most_at_200_dpi(isolated_state: Path) -> None:
    pdf = write_pdf(isolated_state, raw_pdf_bytes(b"", media_box="0 0 144 72"))

    [result] = render_pages(
        pdf, [RenderJob(page=1, width=144, height=72, rotate=0)], 1, isolated_state, 1568, _TextBudget()
    ).values()

    assert isinstance(result, Rendered) and result.size == (400, 200)  # 2 x 1 inches at 200 DPI


def test_a_rotated_page_renders_landscape(isolated_state: Path) -> None:
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5), rotate=90))

    [result] = render_pages(pdf, letter_jobs(1, rotate=90), 1, isolated_state, 1568, _TextBudget()).values()

    assert isinstance(result, Rendered) and result.size == (1568, 1212)


def test_grayscale_scans_survive_encoding(isolated_state: Path) -> None:
    pdf = write_pdf(isolated_state, scanned_pdf_bytes("Dark ink on a gray scan", mode="L"))
    width, height = (float(value) for value in PdfReader(pdf).pages[0].cropbox.upper_right)

    [result] = render_pages(
        pdf, [RenderJob(page=1, width=width, height=height, rotate=0)], 1, isolated_state, 1568, _TextBudget()
    ).values()

    assert isinstance(result, Rendered)
    with Image.open(result.path) as image:
        low, high = image.convert("L").getextrema()
    assert low < 64 and high > 192  # the text and the paper are both there


def test_a_page_size_mismatch_fails_that_page_only(isolated_state: Path) -> None:
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5), (TEXT, 0.5)))
    jobs = [RenderJob(page=1, width=600, height=792, rotate=0), *letter_jobs(2)]

    results = render_pages(pdf, jobs, 2, isolated_state, 1568, _TextBudget())

    assert results[1] == "mismatch"
    assert isinstance(results[2], Rendered)


def test_a_rotation_mismatch_or_a_page_count_difference_fails(isolated_state: Path) -> None:
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5)))

    assert render_pages(pdf, letter_jobs(1, rotate=90), 1, isolated_state, 1568, _TextBudget()) == {1: "mismatch"}
    assert render_pages(pdf, letter_jobs(1), 2, isolated_state, 1568, _TextBudget()) == {1: "page_count"}


def with_page_boxes(
    data: bytes, *, media: list[float] | None = None, crop: list[float] | None = None, rotate: int | None = None
) -> bytes:
    """`data` with every page's /MediaBox, /CropBox or /Rotate set to these raw values."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    for page in writer.pages:
        for key, values in (("/MediaBox", media), ("/CropBox", crop)):
            if values is not None:
                page[NameObject(key)] = ArrayObject(FloatObject(value) for value in values)
        if rotate is not None:
            page[NameObject("/Rotate")] = NumberObject(rotate)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("media", "crop", "size"),  # size: PDFium's get_size() for the same page
    [
        ([0, 0, 612, 792], [-10, -10, 622, 802], (612, 792)),  # a crop box larger than the media box
        ([0, 0, 612, 792], [50, 50, 662, 842], (562, 742)),  # shifted past it
        ([0, 0, 595.28, 841.89], [0, 0, 612, 792], (595.28, 792)),  # A4 media box, Letter crop box
        ([612, 792, 0, 0], None, (612, 792)),  # corners swapped
        ([0, 0, 0, 0], None, (612, 792)),  # an empty media box is US Letter
        ([0, 0, 0, 0], [0, 0, 300, 300], (300, 300)),
        ([0, 0, 612, 792], [100, 100, 100, 200], (612, 792)),  # an empty crop box is the media box
        ([0, 0, 612, 792], [700, 900, 800, 1000], (0, 0)),  # outside the media box: nothing is visible
    ],
)
def test_the_visible_box_is_the_area_pdfium_renders(
    media: list[float], crop: list[float] | None, size: tuple[float, float]
) -> None:
    [page] = PdfReader(io.BytesIO(with_page_boxes(raw_pdf_bytes(b""), media=media, crop=crop))).pages

    x0, y0, x1, y1 = pdf_images.visible_box(page)

    assert (x1 - x0, y1 - y0) == pytest.approx(size)


@pytest.mark.parametrize(
    ("rotate", "applied"),
    [(0, 0), (45, 0), (-45, 0), (89, 0), (90, 90), (135, 90), (-90, 270), (-135, 270), (270, 270), (450, 90)],
)
def test_the_rotation_is_counted_in_whole_quarter_turns_like_pdfium(rotate: int, applied: int) -> None:
    [page] = PdfReader(io.BytesIO(with_page_boxes(raw_pdf_bytes(b""), rotate=rotate))).pages

    assert pdf_images.page_rotation(page) == applied


def test_scans_with_odd_crop_boxes_or_rotations_are_rendered(isolated_state: Path) -> None:
    """The render child compares the job with PDFium's page size and rotation: jobs from the raw crop box or
    /Rotate made it refuse these valid pages as a "mismatch", which failed the document."""
    scan = image_pdf_bytes(("", 1.0))
    cases = {
        "oversized": (with_page_boxes(scan, crop=[-10, -10, 622, 802]), (1212, 1568)),
        "a4-media": (with_page_boxes(scan, media=[0, 0, 595.28, 841.89], crop=[0, 0, 612, 792]), (1179, 1568)),
        "rotate-45": (with_page_boxes(scan, rotate=45), (1212, 1568)),
        "rotate-minus-45": (with_page_boxes(scan, rotate=-45), (1212, 1568)),
        "rotate-minus-90": (with_page_boxes(scan, rotate=-90), (1568, 1212)),
        "rotate-minus-135": (with_page_boxes(scan, rotate=-135), (1568, 1212)),
    }
    for name, (data, size) in cases.items():
        out_dir = isolated_state / name
        out_dir.mkdir()

        parsed = parse_file(write_pdf(isolated_state, data, f"{name}.pdf"), ".pdf", VisionRequest(out_dir, frozenset()))

        [image] = parsed.page_images
        assert (image.mode, image.size) == ("transcribe", size), name


def test_a_scan_under_a_shifted_crop_box_still_covers_the_visible_page() -> None:
    [result] = measure_all(with_page_boxes(image_pdf_bytes(("", 1.0)), crop=[50, 50, 662, 842]))

    assert result is not None and result.images[0].share == pytest.approx(1.0)


def test_relative_paths_reach_the_child_as_absolute_ones(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child runs in the output directory, so a relative UPLOAD_DIR made every page "crash"."""
    monkeypatch.chdir(isolated_state)
    write_pdf(isolated_state, scanned_pdf_bytes("Relative"), "scan.pdf")
    Path("rendered").mkdir()

    parsed = parse_file(Path("scan.pdf"), ".pdf", VisionRequest(Path("rendered"), frozenset()))

    [image] = parsed.page_images
    assert image.mode == "transcribe" and image.image is not None
    assert image.image.is_absolute() and image.image.stat().st_size > 0


def test_the_child_gets_no_app_environment(
    isolated_state: Path, render_child: Callable[..., RenderChild], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECRET_KEY", "must-not-reach-the-child")
    child = render_child()
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5)))

    results = render_pages(pdf, letter_jobs(1), 1, isolated_state, 1568, _TextBudget())

    assert isinstance(results[1], Rendered)
    assert set(child.environment) <= {"PATH", "LANG", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    assert {"SECRET_KEY", "ADMIN_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}.isdisjoint(child.environment)


def test_the_real_child_command_is_isolated() -> None:
    command = pdf_images._child_command()

    assert command[1] == "-I"
    assert Path(command[2]).name == "pdf_render_child.py"


# ---------------- Crashes and timeouts (fake child) ----------------


def test_a_crash_fails_its_page_and_a_new_child_renders_the_rest(
    isolated_state: Path, render_child: Callable[..., RenderChild], caplog: pytest.LogCaptureFixture
) -> None:
    child = render_child(abort=[2])
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5), (TEXT + " Two.", 0.5), (TEXT + " Three.", 0.5)))

    with caplog.at_level(logging.WARNING):
        results = render_pages(pdf, letter_jobs(1, 2, 3), 3, isolated_state, 1568, _TextBudget())

    assert isinstance(results[1], Rendered) and isinstance(results[3], Rendered)
    assert results[2] == "crashed"
    assert len(child.pids) == 2
    assert any("stopped on page 2 (crashed" in message for message in caplog.messages)


def test_a_reported_error_fails_only_that_page(isolated_state: Path, render_child: Callable[..., RenderChild]) -> None:
    child = render_child(fail=[1])
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5), (TEXT + " Two.", 0.5)))

    results = render_pages(pdf, letter_jobs(1, 2), 2, isolated_state, 1568, _TextBudget())

    assert results[1] == "InjectedError"
    assert isinstance(results[2], Rendered)
    assert len(child.pids) == 1


def test_a_stuck_page_times_out_and_its_child_is_killed(
    isolated_state: Path, render_child: Callable[..., RenderChild], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pdf_images, "_RENDER_PAGE_SECONDS", 1)
    child = render_child(hang=[1])
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5), (TEXT + " Two.", 0.5)))
    started = time.monotonic()

    results = render_pages(pdf, letter_jobs(1, 2), 2, isolated_state, 1568, _TextBudget())

    assert time.monotonic() - started < 15
    assert results[1] == "timeout"
    assert isinstance(results[2], Rendered)
    first = child.pids[0]
    with pytest.raises(ProcessLookupError):
        os.kill(first, 0)


def test_after_three_restarts_the_remaining_pages_fail(
    isolated_state: Path, render_child: Callable[..., RenderChild]
) -> None:
    child = render_child(abort=[1, 2, 3, 4])
    pages = [(f"{TEXT} Page {n}.", 0.5) for n in range(1, 7)]
    pdf = write_pdf(isolated_state, image_pdf_bytes(*pages))

    results = render_pages(pdf, letter_jobs(*range(1, 7)), 6, isolated_state, 1568, _TextBudget())

    assert [results[page] for page in range(1, 5)] == ["crashed"] * 4
    assert [results[page] for page in (5, 6)] == ["restarts", "restarts"]
    assert len(child.pids) == 4


def test_the_parse_deadline_stops_rendering(isolated_state: Path, render_child: Callable[..., RenderChild]) -> None:
    child = render_child(hang=[1])
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5)))
    budget = _TextBudget()
    budget.deadline = time.monotonic() + 1

    with pytest.raises(DocumentParseError, match="too large or complex to process"):
        render_pages(pdf, letter_jobs(1), 1, isolated_state, 1568, budget)

    with pytest.raises(ProcessLookupError):
        os.kill(child.pids[0], 0)


def test_a_child_that_cannot_start_fails_the_pages(isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_images, "_child_command", lambda: [str(isolated_state / "missing-python")])
    pdf = write_pdf(isolated_state, image_pdf_bytes((TEXT, 0.5)))

    assert render_pages(pdf, letter_jobs(1), 1, isolated_state, 1568, _TextBudget()) == {1: "not started"}
