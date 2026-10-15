"""The files the chat assistant creates: Markdown (and sheets for xlsx) to PDF, DOCX, XLSX or MD bytes.

Pure functions: no database, no network and no imports from `app` (relative imports only), so child.py can run
them in a process without the app's settings or secrets. The renderers are imported only when needed.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal, get_args

if TYPE_CHECKING:
    from .markdown import Block

FileFormat = Literal["pdf", "docx", "xlsx", "md"]
FORMATS: tuple[FileFormat, ...] = get_args(FileFormat)
EXTENSIONS: dict[FileFormat, str] = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx", "md": ".md"}
CONTENT_TYPES: dict[FileFormat, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
}
CREATOR = "RAG Assistant"

_MAX_STEM_CHARS = 120
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
# What XML 1.0 can't hold (python-docx and openpyxl refuse it, through lxml): C0 controls other than tab, line feed and
# carriage return, U+FFFE, U+FFFF and lone surrogates. DEL goes too, as in parsing. Vertical tab and form feed are
# line breaks.
_XML_INVALID = {code: None for code in (*range(0x00, 0x09), *range(0x0E, 0x20), 0x7F, 0xFFFE, 0xFFFF)} | {
    0x0B: "\n",
    0x0C: "\n",
}
_SURROGATES = re.compile("[\ud800-\udfff]")
_RESERVED_CHARACTERS = re.compile(r'[<>:"/\\|?*]')
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
)


class RenderError(Exception):
    """The file can't be created from this input. str() is safe to return to the model."""


@dataclass(frozen=True)
class SourceRef:
    numbers: list[int]  # the [n] numbers that point at this document and page, ascending
    filename: str
    page: int | None


@dataclass(frozen=True)
class SheetData:
    name: str
    columns: list[str]
    rows: list[list[str | int | float | bool | None]]


@dataclass(frozen=True)
class RenderRequest:
    title: str
    format: FileFormat
    content: str  # Markdown ("" for xlsx without notes)
    sheets: list[SheetData]
    sources: list[SourceRef]  # one per (filename, page), in order of their first number

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RenderRequest":
        return cls(
            title=data["title"],
            format=data["format"],
            content=data["content"],
            sheets=[SheetData(**sheet) for sheet in data["sheets"]],
            sources=[SourceRef(**source) for source in data["sources"]],
        )


@dataclass(frozen=True)
class Rendered:
    data: bytes
    pages: int | None  # PDFs
    source_markdown: str | None  # PDFs: the Markdown that was rendered (indexed instead of the PDF's text)
    notes: list[str]  # for the tool result, e.g. characters drawn as a placeholder


def xml_safe(text: str) -> str:
    """`text` without the characters XML 1.0 can't hold; vertical tab and form feed become line feeds."""
    return _SURROGATES.sub("", text.translate(_XML_INVALID))


def _xml_safe_request(request: RenderRequest) -> RenderRequest:
    """Every text of the request made XML-safe. document_tool cleans the tool's arguments already; this is the
    renderers' own guard, for titles, sheets and Sources that reach them another way."""
    return RenderRequest(
        title=xml_safe(request.title),
        format=request.format,
        content=xml_safe(request.content),
        sheets=[
            SheetData(
                xml_safe(sheet.name),
                [xml_safe(column) for column in sheet.columns],
                [[xml_safe(value) if isinstance(value, str) else value for value in row] for row in sheet.rows],
            )
            for sheet in request.sheets
        ],
        sources=[SourceRef(source.numbers, xml_safe(source.filename), source.page) for source in request.sources],
    )


def render(request: RenderRequest) -> Rendered:
    """The file's bytes. Raises RenderError for input that can't be rendered (the message says why)."""
    request = _xml_safe_request(request)
    if request.format == "xlsx":
        from .excel import render_xlsx

        return Rendered(render_xlsx(request), None, None, [])
    blocks = document_tree(request)
    if request.format == "pdf":
        from .mdwriter import write
        from .pdf import render_pdf

        data, pages, notes = render_pdf(blocks, request.title)
        return Rendered(data, pages, write(blocks), notes)
    if request.format == "docx":
        from .word import render_docx

        return Rendered(render_docx(blocks, request.title), None, None, [])
    from .mdwriter import write

    return Rendered(write(blocks).encode(), None, None, [])


def document_tree(request: RenderRequest) -> "list[Block]":
    """The content's blocks, with a title heading unless the content starts with one, then the Sources section."""
    from .markdown import Heading, ListBlock, Paragraph, Span, parse

    blocks = parse(request.content)
    if not blocks or not (isinstance(blocks[0], Heading) and blocks[0].level == 1):
        blocks.insert(0, Heading(1, [Span(request.title)]))
    if request.sources:
        blocks.append(Heading(2, [Span("Sources")]))
        blocks.append(ListBlock(False, 1, [[Paragraph([Span(source_line(source))])] for source in request.sources]))
    return blocks


def document_markdown(request: RenderRequest) -> str:
    """The normalized Markdown of a pdf, docx or md file: title, content and Sources, written from the parsed tree."""
    from .mdwriter import write

    return write(document_tree(_xml_safe_request(request)))


def source_line(source: SourceRef) -> str:
    refs = ", ".join(f"[{number}]" for number in source.numbers)
    page = f", page {source.page}" if source.page is not None else ""
    return f"{refs} {source.filename}{page}"


def file_name(title: str, extension: str) -> str:
    """A safe display name: no control or reserved characters, no Windows device name, a stem of at most 120
    characters ("document" when nothing is left), then the extension."""
    name = unicodedata.normalize("NFC", xml_safe(title))
    name = "".join(ch for ch in name if ch.isspace() or unicodedata.category(ch) not in _HIDDEN_CATEGORIES)
    name = " ".join(_RESERVED_CHARACTERS.sub("-", name).split()).strip(". ")
    name = name[:_MAX_STEM_CHARS].rstrip(". ")
    head, dot, rest = name.partition(".")
    if head.rstrip().upper() in _DEVICE_NAMES:
        name = f"{head}_{dot}{rest}"[:_MAX_STEM_CHARS]
    return (name or "document") + extension
