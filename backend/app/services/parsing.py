"""Uploaded file -> plain-text segments. Synchronous and CPU-bound: call it through asyncio.to_thread."""

import codecs
import json
import logging
import re
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, CData, NavigableString, Tag
from docx import Document as open_docx
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from pptx import Presentation
from pptx.shapes.group import GroupShape
from pypdf import PageObject, PasswordType, PdfReader
from pypdf.errors import DependencyError
from pypdf.generic import ArrayObject, IndirectObject

from app.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown", ".txt", ".csv", ".json")
LEGACY_EXTENSIONS = {".doc": ".docx", ".ppt": ".pptx", ".xls": ".xlsx"}

_NO_PDF_TEXT = "No extractable text (scanned PDFs need OCR, which isn't supported yet)."
_NO_TEXT = "The document contains no text."
_TOO_COMPLEX = "The file is too large or complex to process."
_UNSUPPORTED_PDF_ENCRYPTION = (
    "This PDF uses encryption that isn't supported. Save an unencrypted copy and upload it again."
)
_ZIP_FORMATS = frozenset({".docx", ".pptx", ".xlsx"})
# python-docx and python-pptx load each XML part into a full DOM (about 25 bytes of memory per byte of XML).
_DOM_FORMATS = frozenset({".docx", ".pptx"})
# Plain text needs at least one byte per character and at most four, so a bigger file has too much text.
_MAX_BYTES_PER_CHAR = 4
_XLSX_DEADLINE_ROWS = 1000
# Excel's own row limit, and a bound on the cells a crafted sheet can make openpyxl generate.
_XLSX_MAX_ROWS = 1_048_576
_XLSX_MAX_CELLS = 50_000_000
_HTML_DROPPED = frozenset({"script", "style", "noscript", "template", "svg", "title"})
_HTML_BLOCKS = frozenset(
    "address article aside blockquote caption dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 "
    "header hr li main nav ol p pre section table tr ul".split()
)
_HTML_CELLS = frozenset({"td", "th"})
# The string types get_text() keeps: no comments, doctypes or <rt>/<rp> text.
_HTML_STRING_TYPES = (NavigableString, CData)
_NEWLINE, _CELL_SEPARATOR = object(), object()
# C0 controls except tab/newline are dropped; vertical tab, form feed and Unicode line breaks become newlines.
_CONTROL_CHARS = {code: None for code in (*range(0x00, 0x09), *range(0x0E, 0x20), 0x7F)} | {
    0x0B: "\n",
    0x0C: "\n",
    0x2028: "\n",
    0x2029: "\n",
}
_BLANK_LINES = re.compile(r"\n{3,}")


class DocumentParseError(Exception):
    """The file can't be turned into text. str(exc) is safe to show to end users."""


@dataclass
class Segment:
    text: str
    page: int | None


@dataclass
class ParsedDocument:
    segments: list[Segment]
    num_pages: int | None


class _TextBudget:
    """Running total of extracted characters that fails as soon as the cap is passed, plus the parse deadline."""

    def __init__(self) -> None:
        self.used = 0
        self.deadline = time.monotonic() + settings.parse_timeout_seconds

    def take(self, text: str) -> str:
        self.used += len(text)
        if self.used > settings.max_extracted_chars:
            raise _too_much_text()
        return text

    def check_time(self) -> None:
        """Called between pages, slides and rows: the parse runs in a thread, which can't be cancelled."""
        if time.monotonic() > self.deadline:
            raise DocumentParseError(
                f"The file is too large or complex to process (it took over {settings.parse_timeout_seconds} seconds)."
            )


_Reader = Callable[[Path, _TextBudget], tuple[list[Segment], int | None]]


def parse_file(path: Path, extension: str) -> ParsedDocument:
    """Extract normalized text segments (one per page, slide or sheet where the format has them)."""
    extension = extension.lower()
    reader = _READERS.get(extension)
    if reader is None:
        raise DocumentParseError(f"Unsupported file type: {extension or '(none)'}.")
    if extension in _ZIP_FORMATS:
        _check_zip(path, extension)
    try:
        raw_segments, num_pages = reader(path, _TextBudget())
    except (DocumentParseError, OSError):
        raise
    except Exception as exc:
        logger.warning("Could not parse %s: %r", path.name, exc)
        raise DocumentParseError(f"The file is corrupted or not a valid {extension} file.") from exc

    segments = [Segment(text=text, page=s.page) for s in raw_segments if (text := _normalize(s.text))]
    if not segments:
        raise DocumentParseError(_NO_PDF_TEXT if extension == ".pdf" else _NO_TEXT)
    if sum(len(s.text) for s in segments) > settings.max_extracted_chars:
        raise _too_much_text()
    return ParsedDocument(segments=segments, num_pages=num_pages)


def _too_much_text() -> DocumentParseError:
    return DocumentParseError(f"The document has too much text (over {settings.max_extracted_chars:,} characters).")


def _check_zip(path: Path, extension: str) -> None:
    """Refuse corrupt archives and zip bombs before any library inflates them.

    Python's zipfile never inflates an entry past its declared size, so the declared sizes are hard bounds.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, EOFError, ValueError) as exc:
        raise DocumentParseError(f"The file is corrupted or not a valid {extension} file.") from exc
    if sum(info.file_size for info in entries) > settings.max_uncompressed_mb * 1024 * 1024:
        raise DocumentParseError(
            f"The file expands to more than {settings.max_uncompressed_mb} MB when uncompressed, which isn't allowed."
        )
    if extension in _DOM_FORMATS and any(
        info.file_size > settings.max_xml_part_mb * 1024 * 1024
        for info in entries
        if info.filename.lower().endswith((".xml", ".rels"))
    ):
        raise DocumentParseError(_TOO_COMPLEX)


def _normalize(text: str) -> str:
    text = text.encode("utf-8", "replace").decode("utf-8")  # lone surrogates can't be stored or sent
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROL_CHARS)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _read_pdf(path: Path, budget: _TextBudget) -> tuple[list[Segment], int | None]:
    try:
        reader = PdfReader(path)  # checks the empty password of AES-256 files already
    except DependencyError as exc:
        raise DocumentParseError(_UNSUPPORTED_PDF_ENCRYPTION) from exc
    if reader.is_encrypted:
        _decrypt_pdf(reader)
    num_pages = len(reader.pages)
    if num_pages > settings.max_pdf_pages:
        raise DocumentParseError(f"The PDF has more than {settings.max_pdf_pages:,} pages, which isn't supported.")
    segments: list[Segment] = []
    # Text by content streams and resources: pages that draw the same ones (a crafted file can repeat one
    # costly stream on thousands of pages) are read once.
    seen: dict[tuple[object, ...], str | None] = {}
    for number, page in enumerate(reader.pages, start=1):
        budget.check_time()
        key = _pdf_content_key(page)
        if key is not None and key in seen:
            text = seen[key]
        else:
            try:
                text = page.extract_text() or ""
            except DependencyError as exc:
                raise DocumentParseError(_UNSUPPORTED_PDF_ENCRYPTION) from exc
            except Exception as exc:
                logger.warning("Skipping unreadable page %d of %s: %r", number, path.name, exc)
                text = None
            if key is not None:
                seen[key] = text
        if text is not None:
            segments.append(Segment(text=budget.take(text), page=number))
    return segments, num_pages


def _pdf_content_key(page: PageObject) -> tuple[object, ...] | None:
    """Identifies what a page draws: its content stream objects and its resources (the same stream can show
    different text with other fonts or forms). None when the streams aren't all objects of their own."""
    contents = page.raw_get("/Contents") if "/Contents" in page else None
    if isinstance(contents, IndirectObject) and isinstance(contents.get_object(), ArrayObject):
        contents = contents.get_object()
    streams = list(contents) if isinstance(contents, ArrayObject) else [contents]
    if not all(isinstance(stream, IndirectObject) for stream in streams):
        return None
    resources = page.raw_get("/Resources") if "/Resources" in page else None
    # A direct resources dictionary is one Python object for every page that inherits or repeats it.
    shared = (resources.idnum, resources.generation) if isinstance(resources, IndirectObject) else id(resources)
    return (shared, *((stream.idnum, stream.generation) for stream in streams))


def _decrypt_pdf(reader: PdfReader) -> None:
    """Open PDFs that are encrypted with an empty user password (permission-restricted files)."""
    try:
        result = reader.decrypt("")
    except DependencyError as exc:
        raise DocumentParseError(_UNSUPPORTED_PDF_ENCRYPTION) from exc
    except Exception:
        result = PasswordType.NOT_DECRYPTED
    if result == PasswordType.NOT_DECRYPTED:
        raise DocumentParseError("Password-protected PDFs aren't supported.")


def _read_docx(path: Path, budget: _TextBudget) -> tuple[list[Segment], int | None]:
    document = open_docx(str(path))
    lines = [budget.take(line) for line in _docx_lines(document)]
    return [Segment(text="\n".join(lines), page=None)], None


def _docx_lines(container: Any) -> Iterator[str]:
    """Paragraphs and table rows in body order (`container` is the document or a table cell)."""
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            yield block.text
        elif isinstance(block, DocxTable):
            yield from _docx_table_lines(block)


def _docx_table_lines(table: DocxTable) -> Iterator[str]:
    for row in table.rows:
        cells: list[str] = []
        previous = None
        for cell in row.cells:
            if cell is previous:  # a horizontally merged cell is repeated once per grid column
                continue
            previous = cell
            cells.append(" ".join(" ".join(_docx_lines(cell)).split()))
        if any(cells):
            yield " | ".join(cells)


def _read_pptx(path: Path, budget: _TextBudget) -> tuple[list[Segment], int | None]:
    presentation = Presentation(str(path))
    segments: list[Segment] = []
    for number, slide in enumerate(presentation.slides, start=1):
        budget.check_time()
        lines = list(_pptx_shape_lines(slide.shapes))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                lines.append(f"Notes: {notes.text}")
        segments.append(Segment(text=budget.take("\n".join(lines)), page=number))
    return segments, len(presentation.slides)


def _pptx_shape_lines(shapes: Iterable[Any]) -> Iterator[str]:
    for shape in shapes:
        if isinstance(shape, GroupShape):
            yield from _pptx_shape_lines(shape.shapes)
        elif shape.has_text_frame:
            yield shape.text_frame.text
        elif shape.has_table:
            for row in shape.table.rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells if not cell.is_spanned]
                if any(cells):
                    yield " | ".join(cells)


def _read_xlsx(path: Path, budget: _TextBudget) -> tuple[list[Segment], int | None]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        segments: list[Segment] = []
        cells_seen = 0
        for sheet in workbook.worksheets:
            # The stored dimensions are untrusted; without them openpyxl doesn't pad rows to a claimed width.
            sheet.reset_dimensions()
            lines: list[str] = []
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if row_number > _XLSX_MAX_ROWS:
                    break
                if row_number % _XLSX_DEADLINE_ROWS == 0:
                    budget.check_time()
                cells_seen += max(1, len(row))
                if cells_seen > _XLSX_MAX_CELLS:
                    raise DocumentParseError("The spreadsheet is too large to process.")
                if line := _xlsx_row_text(row):
                    lines.append(budget.take(line))
            if lines:
                segments.append(Segment(text="\n".join([f"Sheet: {sheet.title}", *lines]), page=None))
        return segments, None
    finally:
        workbook.close()


def _xlsx_row_text(values: tuple[Any, ...]) -> str:
    cells = [_xlsx_cell_text(value) for value in values]
    while cells and not cells[-1]:
        cells.pop()
    return " | ".join(cells)


def _xlsx_cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime) and not (value.hour or value.minute or value.second or value.microsecond):
        return value.date().isoformat()
    return " ".join(str(value).split())


def _read_html(path: Path, budget: _TextBudget) -> tuple[list[Segment], int | None]:
    # The tree takes many times the file's size in memory, and markup doesn't count toward the text cap.
    if path.stat().st_size > settings.max_extracted_chars * _MAX_BYTES_PER_CHAR:
        raise DocumentParseError(_TOO_COMPLEX)
    soup = BeautifulSoup(path.read_bytes(), "lxml")
    title = " ".join(soup.title.get_text(" ").split()) if soup.title else ""
    body = "\n".join(" ".join(line.split()) for line in _html_text(soup).splitlines())
    text = f"{title}\n\n{body}" if title else body
    return [Segment(text=budget.take(text), page=None)], None


def _html_text(root: Tag) -> str:
    """Text with blocks on lines of their own and table cells joined by " | ".

    One iterative pass that leaves the tree untouched: inserting into it costs time quadratic in the nesting depth.
    """
    parts: list[str] = []
    stack: list[Any] = list(reversed(root.contents))
    while stack:
        node = stack.pop()
        if node is _NEWLINE:
            parts.append("\n")
        elif node is _CELL_SEPARATOR:
            parts.append(" | ")
        elif isinstance(node, Tag):
            if node.name in _HTML_DROPPED:
                continue
            if node.name == "br":
                parts.append("\n")
                continue
            children: list[Any] = node.contents
            if node.name == "tr":  # " | " before every direct td/th except the first
                children, first_cell = [], True
                for child in node.contents:
                    if isinstance(child, Tag) and child.name in _HTML_CELLS:
                        if not first_cell:
                            children.append(_CELL_SEPARATOR)
                        first_cell = False
                    children.append(child)
            if node.name in _HTML_BLOCKS:
                parts.append("\n")
                stack.append(_NEWLINE)
            stack.extend(reversed(children))
        elif type(node) in _HTML_STRING_TYPES:
            parts.append(node)
    return "".join(parts)


def _read_text(path: Path, budget: _TextBudget) -> tuple[list[Segment], int | None]:
    _check_text_size(path)
    return [Segment(text=budget.take(_decode(path.read_bytes())), page=None)], None


def _read_json(path: Path, budget: _TextBudget) -> tuple[list[Segment], int | None]:
    _check_text_size(path)
    text = _decode(path.read_bytes())
    if len(text) > settings.max_extracted_chars:  # don't build the object only to refuse its text
        raise _too_much_text()
    try:
        pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (ValueError, RecursionError):
        pass
    else:
        if len(pretty) <= settings.max_extracted_chars:
            text = pretty
    return [Segment(text=budget.take(text), page=None)], None


def _check_text_size(path: Path) -> None:
    """Refuse a text file that is certain to exceed the text cap before reading it into memory."""
    if path.stat().st_size > settings.max_extracted_chars * _MAX_BYTES_PER_CHAR:
        raise _too_much_text()


def _decode(data: bytes) -> str:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


_READERS: dict[str, _Reader] = {
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".pptx": _read_pptx,
    ".xlsx": _read_xlsx,
    ".html": _read_html,
    ".htm": _read_html,
    ".md": _read_text,
    ".markdown": _read_text,
    ".txt": _read_text,
    ".csv": _read_text,
    ".json": _read_json,
}
