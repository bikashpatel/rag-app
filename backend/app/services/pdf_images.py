"""PDF pages for the vision model: where images are drawn (pypdf only, in the parse thread) and rendering selected
pages to JPEG in a child process (pdf_render_child.py), so PDFium never runs in the worker."""

import contextlib
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from pypdf import PageObject, PdfReader
from pypdf.generic import ContentStream, DictionaryObject, IndirectObject

if TYPE_CHECKING:
    from app.services.parsing import _TextBudget

logger = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1
Matrix = tuple[float, float, float, float, float, float]

# ContentStream costs about 230 bytes of Python memory per operator, so decoded content is capped per unit.
_MAX_MEASURE_BYTES = 2 * 1024 * 1024
# What PDFium would build when it loads the page (forms expanded per use): more is refused before rendering.
_MAX_RENDER_OPS = 300_000
_MAX_DO_VISITS = 10_000
_MAX_IMAGES_PER_PAGE = 500
_MAX_FORM_DEPTH = 8
_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_LETTER: Rect = (0.0, 0.0, 612.0, 792.0)  # PDFium's page for an empty media box
_TEXT_SHOWING = frozenset({b"Tj", b"'", b'"', b"TJ"})
_KEPT = frozenset({b"q", b"Q", b"cm", b"Tr", b"Do", b"INLINE IMAGE"}) | _TEXT_SHOWING
_INVISIBLE_MODES = frozenset({3, 7})
_INLINE_IMAGE = re.compile(rb"\bBI\s")

_RENDER_PAGE_SECONDS = 30
_MAX_RENDER_RESTARTS = 3
_MAX_DPI = 200


@dataclass(frozen=True)
class PlacedImage:
    key: tuple[int, int] | None  # the XObject reference; None for inline images
    share: float  # of the page's visible box (see visible_box)
    rect: Rect  # box ∩ visible box, page space
    pixels: int


@dataclass(frozen=True)
class PageMeasure:
    images: tuple[PlacedImage, ...]
    visible_bytes: int  # text shown with a visible render mode
    invisible_bytes: int  # render mode 3 or 7: an OCR layer
    renderable: bool  # False: too many operators to hand to PDFium


@dataclass(frozen=True)
class _Form:
    operations: list[tuple[Any, bytes]]  # the kept operators only
    total: int  # all operators
    size: int  # decoded bytes


class _TooLarge(Exception):
    pass


class _TooComplex(Exception):
    pass


@dataclass
class _Walk:
    box: Rect  # visible_box() of the page
    images: list[PlacedImage] = field(default_factory=list)
    visible: int = 0
    invisible: int = 0
    size: int = 0
    operators: int = 0
    do_visits: int = 0
    forms: set[tuple[int, int]] = field(default_factory=set)  # forms counted toward `size`


class PageMeasurer:
    """Measures image placement and text visibility of pages of one document. Forms and image dictionaries are
    memoized, since many pages usually share them."""

    def __init__(self, reader: PdfReader) -> None:
        self._reader = reader
        self._forms: dict[tuple[int, int], _Form | None] = {}  # None: over the size cap
        self._xobjects: dict[tuple[int, int], tuple[str, int]] = {}  # subtype, pixels
        self._has_images: dict[tuple[int, int], bool] = {}

    def is_candidate(self, page: PageObject) -> bool:
        """Whether the page may draw an image: an image XObject in its resources (also inside forms), or an inline
        image in its content. Anything else is text, without measuring."""
        if self._resources_have_images(_dictionary(page, "/Resources"), 0):
            return True
        contents = page.get_contents()
        return contents is not None and _INLINE_IMAGE.search(contents.get_data()) is not None

    def measure(self, page: PageObject) -> PageMeasure | None:
        """None when the page's content (with the forms it draws) is over _MAX_MEASURE_BYTES."""
        walk = _Walk(visible_box(page))
        contents = page.get_contents()
        if contents is None:
            return PageMeasure((), 0, 0, True)
        size = len(contents.get_data())
        if size > _MAX_MEASURE_BYTES:
            return None
        walk.size = size
        operations = contents.operations
        walk.operators = len(operations)
        kept = [(operands, operator) for operands, operator in operations if operator in _KEPT]
        del operations, contents  # only the kept operators stay in memory while forms are parsed
        renderable = walk.operators <= _MAX_RENDER_OPS
        try:
            self._walk(kept, _dictionary(page, "/Resources"), _IDENTITY, 0, walk, ())
        except _TooLarge:
            return None
        except _TooComplex:
            renderable = False
        return PageMeasure(tuple(walk.images), walk.visible, walk.invisible, renderable)

    def _walk(
        self,
        operations: list[tuple[Any, bytes]],
        resources: DictionaryObject | None,
        ctm: Matrix,
        depth: int,
        walk: _Walk,
        active: tuple[tuple[int, int], ...],
    ) -> None:
        xobjects = _dictionary(resources, "/XObject") if resources is not None else None
        stack: list[tuple[Matrix, int]] = []
        mode = 0
        for operands, operator in operations:
            if operator == b"q":
                stack.append((ctm, mode))
            elif operator == b"Q":
                if stack:
                    ctm, mode = stack.pop()
            elif operator == b"cm":
                if (matrix := _matrix(operands)) is not None:
                    ctm = _multiply(matrix, ctm)
            elif operator == b"Tr":
                if operands:
                    mode = _integer(operands[0])
            elif operator in _TEXT_SHOWING:
                shown = _text_bytes(operands, operator)
                if mode in _INVISIBLE_MODES:
                    walk.invisible += shown
                else:
                    walk.visible += shown
            elif operator == b"INLINE IMAGE":
                found = operands.get("settings") if isinstance(operands, dict) else None
                inline = found if isinstance(found, DictionaryObject) else DictionaryObject()
                width = _integer(_get(inline, "/W")) or _integer(_get(inline, "/Width"))
                height = _integer(_get(inline, "/H")) or _integer(_get(inline, "/Height"))
                self._place(walk, None, ctm, width * height)
            elif operator == b"Do" and operands:
                walk.do_visits += 1
                if walk.do_visits > _MAX_DO_VISITS:
                    raise _TooComplex
                if xobjects is not None:
                    self._draw(xobjects, operands[0], ctm, resources, depth, walk, active)

    def _draw(
        self,
        xobjects: DictionaryObject,
        name: Any,
        ctm: Matrix,
        resources: DictionaryObject | None,
        depth: int,
        walk: _Walk,
        active: tuple[tuple[int, int], ...],
    ) -> None:
        reference = xobjects.raw_get(name) if name in xobjects else None
        if not isinstance(reference, IndirectObject):  # streams are always indirect objects
            return
        key = (reference.idnum, reference.generation)
        subtype, pixels = self._xobject(key, reference)
        if subtype == "/Image":
            self._place(walk, key, ctm, pixels)
        elif subtype == "/Form" and depth < _MAX_FORM_DEPTH and key not in active:
            form_object = reference.get_object()
            if not isinstance(form_object, DictionaryObject):
                return
            form = self._form(key, form_object)
            if form is None:
                raise _TooLarge
            if key not in walk.forms:
                walk.forms.add(key)
                walk.size += form.size
                if walk.size > _MAX_MEASURE_BYTES:
                    raise _TooLarge
            walk.operators += form.total
            if walk.operators > _MAX_RENDER_OPS:
                raise _TooComplex
            matrix = _matrix(_get(form_object, "/Matrix"))
            form_ctm = _multiply(matrix, ctm) if matrix is not None else ctm
            form_resources = _dictionary(form_object, "/Resources") or resources
            self._walk(form.operations, form_resources, form_ctm, depth + 1, walk, (*active, key))

    def _xobject(self, key: tuple[int, int], reference: IndirectObject) -> tuple[str, int]:
        if key not in self._xobjects:
            xobject = reference.get_object()
            subtype, pixels = "", 0
            if isinstance(xobject, DictionaryObject):
                subtype = str(_get(xobject, "/Subtype") or "")
                if subtype == "/Image":
                    pixels = _integer(_get(xobject, "/Width")) * _integer(_get(xobject, "/Height"))
            self._xobjects[key] = (subtype, pixels)
        return self._xobjects[key]

    def _form(self, key: tuple[int, int], form_object: DictionaryObject) -> _Form | None:
        if key not in self._forms:
            stream = ContentStream(form_object, self._reader)
            size = len(stream.get_data())
            if size > _MAX_MEASURE_BYTES:
                self._forms[key] = None
            else:
                operations = stream.operations
                kept = [(operands, operator) for operands, operator in operations if operator in _KEPT]
                self._forms[key] = _Form(kept, len(operations), size)
        return self._forms[key]

    def _place(self, walk: _Walk, key: tuple[int, int] | None, ctm: Matrix, pixels: int) -> None:
        if len(walk.images) >= _MAX_IMAGES_PER_PAGE:
            return
        rect = _intersection(_unit_square(ctm), walk.box)
        area = _area(walk.box)
        share = _area(rect) / area if area > 0 else 0.0
        walk.images.append(PlacedImage(key=key, share=share, rect=rect, pixels=pixels))

    def _resources_have_images(self, resources: DictionaryObject | None, depth: int) -> bool:
        xobjects = _dictionary(resources, "/XObject") if resources is not None else None
        if xobjects is None:
            return False
        for name in list(xobjects.keys()):
            reference = xobjects.raw_get(name)
            if not isinstance(reference, IndirectObject):
                continue
            key = (reference.idnum, reference.generation)
            subtype, _ = self._xobject(key, reference)
            if subtype == "/Image":
                return True
            if subtype == "/Form" and depth < _MAX_FORM_DEPTH:
                if key not in self._has_images:
                    self._has_images[key] = False  # a form reaching itself is cut off here
                    form_object = reference.get_object()
                    self._has_images[key] = isinstance(form_object, DictionaryObject) and self._resources_have_images(
                        _dictionary(form_object, "/Resources"), depth + 1
                    )
                if self._has_images[key]:
                    return True
        return False


def visible_box(page: PageObject) -> Rect:
    """The page area PDFium renders, from the boxes pypdf reads: the crop box clipped to the media box. An empty media
    box is US Letter and an empty crop box is the media box; a crop box outside the media box leaves nothing."""
    media = _box(page.mediabox)
    if _area(media) <= 0:
        media = _LETTER
    crop = _box(page.cropbox)
    return media if _area(crop) <= 0 else _intersection(crop, media)


def page_rotation(page: PageObject) -> int:
    """/Rotate as PDFium applies it: whole quarter turns, counted toward zero (45 and -45 are 0), as 0, 90, 180 or
    270."""
    rotation = int(page.rotation)
    turns = abs(rotation) // 90
    return (turns if rotation >= 0 else -turns) % 4 * 90


def union_share(rects: Iterable[Rect], cropbox: Rect) -> float:
    """Share of the crop box covered by the union of the rectangles (a sweep over x slabs), so overlapping images
    count once and a scan stored as strips counts as one layer."""
    box = _normalized(cropbox)
    area = _area(box)
    if area <= 0:
        return 0.0
    clipped = [rect for rect in (_intersection(_normalized(r), box) for r in rects) if _area(rect) > 0]
    xs = sorted({rect[0] for rect in clipped} | {rect[2] for rect in clipped})
    covered = 0.0
    for left, right in zip(xs, xs[1:], strict=False):
        spans = sorted((rect[1], rect[3]) for rect in clipped if rect[0] <= left and rect[2] >= right)
        height, top = 0.0, float("-inf")
        for bottom, upper in spans:
            if upper > top:
                height += upper - max(bottom, top)
                top = upper
        covered += height * (right - left)
    return min(1.0, covered / area)


# ---------------- Rendering ----------------


@dataclass(frozen=True)
class RenderJob:
    page: int  # 1-based
    width: float  # visible_box() size, points
    height: float
    rotate: int  # page_rotation()


@dataclass(frozen=True)
class Rendered:
    path: Path
    size: tuple[int, int]


def _child_command() -> list[str]:
    """`-I`: the child ignores PYTHON* variables and the user site, and its directory isn't on sys.path."""
    return [sys.executable, "-I", str(Path(__file__).with_name("pdf_render_child.py"))]


def render_pages(
    pdf: Path,
    jobs: Sequence[RenderJob],
    page_count: int,
    out_dir: Path,
    max_px: int,
    budget: "_TextBudget",
) -> dict[int, Rendered | str]:
    """Render pages to `out_dir/page-N.jpg` in a child process. Returns page -> Rendered, or the reason it failed
    (for logs). A crashed or stuck child fails its page and is replaced, at most _MAX_RENDER_RESTARTS times."""
    pdf, out_dir = pdf.absolute(), out_dir.absolute()  # the child runs in out_dir, where relative paths would break
    results: dict[int, Rendered | str] = {}
    remaining = list(jobs)
    starts = 0
    while remaining:
        if starts > _MAX_RENDER_RESTARTS:
            for job in remaining:
                results[job.page] = "restarts"
            break
        budget.check_time()
        starts += 1
        _run_child(pdf, remaining, page_count, out_dir, max_px, budget, results)
        remaining = [job for job in remaining if job.page not in results]
    return results


def _run_child(
    pdf: Path,
    jobs: list[RenderJob],
    page_count: int,
    out_dir: Path,
    max_px: int,
    budget: "_TextBudget",
    results: dict[int, Rendered | str],
) -> None:
    """One child for `jobs`, until it finishes, crashes or times out. Fills `results` for the pages it got to."""
    request = {
        "pdf": str(pdf),
        "out_dir": str(out_dir),
        "max_px": max_px,
        "max_dpi": _MAX_DPI,
        "page_count": page_count,
        "pages": [{"page": j.page, "width": j.width, "height": j.height, "rotate": j.rotate} for j in jobs],
    }
    log_path = out_dir / "render.log"
    lines: queue.Queue[bytes | None] = queue.Queue()
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                _child_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                cwd=out_dir,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            )
    except OSError as exc:
        logger.error("Could not start the PDF render child: %s", exc)
        for job in jobs:
            results[job.page] = "not started"
        return
    assert process.stdin is not None and process.stdout is not None
    reader = threading.Thread(target=_read_lines, args=(process.stdout, lines), daemon=True)
    reader.start()
    try:
        try:
            process.stdin.write(json.dumps(request).encode())
            process.stdin.close()
        except OSError:
            pass  # the child is already gone: reported as a crash below
        for job in jobs:
            outcome = _await_result(lines, job.page, budget)
            if outcome == "page_count":
                for pending in jobs:
                    results.setdefault(pending.page, "page_count")
                return
            if isinstance(outcome, str):
                results[job.page] = outcome
                if outcome == "timeout":
                    process.kill()
                    budget.check_time()
                else:
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=5)
                    logger.warning(
                        "The PDF render child stopped on page %d (%s, exit code %s): %s",
                        job.page,
                        outcome,
                        process.returncode,
                        _log_tail(log_path),
                    )
                return
            results[job.page] = _rendered(out_dir, job.page, outcome) if outcome.get("ok") else str(outcome["error"])
    finally:
        process.kill()
        process.wait()
        reader.join(timeout=5)  # the child is gone, so its stdout has ended
        process.stdout.close()


def _await_result(lines: "queue.Queue[bytes | None]", page: int, budget: "_TextBudget") -> dict[str, Any] | str:
    """The child's result for `page` (with "ok" or "error"), or "timeout", "crashed" or "page_count". Every line from
    the child restarts the clock, so loading the document and each page get _RENDER_PAGE_SECONDS each."""
    while True:
        wait = min(_RENDER_PAGE_SECONDS, budget.deadline - time.monotonic())
        try:
            line = lines.get(timeout=max(wait, 0.0))
        except queue.Empty:
            return "timeout"
        if line is None:
            return "crashed"
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("error") == "page_count" and "page" not in event:
            return "page_count"
        if event.get("page") == page:
            return event if event.get("ok") else {"error": str(event.get("error") or "failed")}


def _rendered(out_dir: Path, page: int, event: dict[str, Any]) -> Rendered | str:
    path = out_dir / f"page-{page}.jpg"
    try:
        if path.stat().st_size == 0:
            return "empty"
        return Rendered(path=path, size=(int(event["width"]), int(event["height"])))
    except (OSError, KeyError, TypeError, ValueError):
        return "missing"


def _read_lines(stream: IO[bytes], lines: "queue.Queue[bytes | None]") -> None:
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


def _log_tail(path: Path) -> str:
    try:
        return path.read_bytes()[-300:].decode("utf-8", "replace").strip() or "(no output)"
    except OSError:
        return "(no log)"


# ---------------- Geometry ----------------


def _multiply(m: Matrix, n: Matrix) -> Matrix:
    """m × n for PDF's row-vector matrices: `cm` premultiplies the current transformation matrix."""
    a, b, c, d, e, f = m
    p, q, r, s, t, u = n
    return (a * p + b * r, a * q + b * s, c * p + d * r, c * q + d * s, e * p + f * r + t, e * q + f * s + u)


def _unit_square(ctm: Matrix) -> Rect:
    a, b, c, d, e, f = ctm
    xs = (e, a + e, c + e, a + c + e)
    ys = (f, b + f, d + f, b + d + f)
    return min(xs), min(ys), max(xs), max(ys)


def _box(rectangle: Any) -> Rect:
    """A pypdf RectangleObject, normalized."""
    return _normalized((float(rectangle.left), float(rectangle.bottom), float(rectangle.right), float(rectangle.top)))


def _normalized(rect: Rect) -> Rect:
    x0, y0, x1, y1 = rect
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _intersection(rect: Rect, box: Rect) -> Rect:
    x0, y0 = max(rect[0], box[0]), max(rect[1], box[1])
    x1, y1 = min(rect[2], box[2]), min(rect[3], box[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else (x0, y0, x0, y0)


def _area(rect: Rect) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _matrix(values: Any) -> Matrix | None:
    """Six finite numbers, else None (a malformed operator is skipped, as PDFium does)."""
    if not isinstance(values, list) or len(values) != 6:
        return None
    try:
        numbers = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    if not all(abs(number) < 1e9 for number in numbers):
        return None
    return numbers[0], numbers[1], numbers[2], numbers[3], numbers[4], numbers[5]


def _integer(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if 0 <= number < 10**9 else 0


def _text_bytes(operands: Any, operator: bytes) -> int:
    if not operands:
        return 0
    shown = operands[-1]
    if operator == b"TJ":
        return sum(_string_bytes(part) for part in shown) if isinstance(shown, list) else 0
    return _string_bytes(shown)


def _string_bytes(value: Any) -> int:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        original = getattr(value, "original_bytes", None)
        return len(original) if isinstance(original, bytes) else len(value)
    return 0


def _get(dictionary: DictionaryObject, key: str) -> Any:
    """dictionary[key] resolved, or None (DictionaryObject.get returns indirect references as they are)."""
    value = dictionary.get(key)
    return value.get_object() if isinstance(value, IndirectObject) else value


def _dictionary(dictionary: DictionaryObject, key: str) -> DictionaryObject | None:
    value = _get(dictionary, key)
    return value if isinstance(value, DictionaryObject) else None
