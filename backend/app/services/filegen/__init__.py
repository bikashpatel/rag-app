"""The files the chat assistant creates: Markdown (and sheets for xlsx) to PDF, DOCX, XLSX or MD bytes, with the
images generated in the conversation embedded in PDF and DOCX files.

Pure functions: no database, no network and no imports from `app` (relative imports only), so child.py can run
them in a process without the app's settings or secrets. The renderers are imported only when needed.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import date
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
CREATOR = "AskNuvexusDoc"
MAX_IMAGES_PER_FILE = 10

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
# What a caption must escape to stay one image's text (mdwriter escapes the same), and the & of an entity.
_CAPTION_ESCAPES = re.compile(r"[\\`*_\[\]<>|~]")
_ENTITY = re.compile(r"&(?:#[0-9]{1,7};|#[xX][0-9a-fA-F]{1,6};|[A-Za-z][A-Za-z0-9]{1,31};)")
_TEXT_REFERENCES = "Images can only be embedded in pdf and docx files; this file has text references to them."


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
class ImageRef:
    """An image the content may embed as ![caption](image:<id>), resolved by the server."""

    id: int
    path: str  # a bare file name in the render work directory ("img-812.png"); "" for xlsx and md
    filename: str  # the image document's file name, for text references
    alt_text: str  # the PDF's /Alt and the DOCX picture's descr
    width: int
    height: int


@dataclass(frozen=True)
class RenderRequest:
    title: str
    format: FileFormat
    content: str  # Markdown ("" for xlsx without notes)
    sheets: list[SheetData]
    sources: list[SourceRef]  # one per (filename, page), in order of their first number
    images: list[ImageRef] = field(default_factory=list)

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
            images=[ImageRef(**image) for image in data.get("images", [])],
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
        images=[
            replace(ref, filename=xml_safe(ref.filename), alt_text=xml_safe(ref.alt_text)) for ref in request.images
        ],
    )


def render(request: RenderRequest) -> Rendered:
    """The file's bytes. Raises RenderError for input that can't be rendered (the message says why). Images are
    read from the working directory (ImageRef.path) for pdf and docx only."""
    from .markdown import has_figures, parse

    request = _xml_safe_request(request)
    if request.format == "xlsx":
        from .excel import render_xlsx

        notes_sheet = parse(request.content, _images_by_id(request)) if request.content.strip() else []
        data = render_xlsx(request, notes_sheet)
        return Rendered(data, None, None, [_TEXT_REFERENCES] if has_figures(notes_sheet) else [])
    blocks = document_tree(request)
    if request.format == "pdf":
        from .mdwriter import write
        from .pdf import render_pdf

        data, pages, pdf_notes = render_pdf(blocks, request.title)
        return Rendered(data, pages, write(blocks), pdf_notes)
    if request.format == "docx":
        from .word import render_docx

        data, docx_notes = render_docx(blocks, request.title)
        return Rendered(data, None, None, docx_notes)
    from .mdwriter import write

    return Rendered(write(blocks).encode(), None, None, [_TEXT_REFERENCES] if has_figures(blocks) else [])


def document_tree(request: RenderRequest) -> "list[Block]":
    """The content's blocks, with a title heading unless the content starts with one, then the Sources section."""
    from .markdown import Heading, ListBlock, Paragraph, Span, parse

    blocks = parse(request.content, _images_by_id(request))
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


def _images_by_id(request: RenderRequest) -> dict[int, ImageRef]:
    return {ref.id: ref for ref in request.images}


def image_refs(content: str) -> list[int]:
    """The ids of the ![caption](image:<id>) references in `content` that a file can embed (those standing in
    paragraphs, not in headings or table cells), in order, each once."""
    from .markdown import image_ids

    return image_ids(content)


def image_markdown(caption: str, image_id: int) -> str:
    """The reference that embeds an image: "![<caption>](image:<id>)", the caption on one line with every character
    that would end it or style it escaped, so it parses back as this image with this caption."""
    text = " ".join(xml_safe(caption).split())
    text = _CAPTION_ESCAPES.sub(lambda found: "\\" + found.group(), text)
    text = _ENTITY.sub(lambda found: "\\" + found.group(), text)
    return f"![{text}](image:{image_id})"


def image_source_markdown(
    *, title: str, alt_text: str, prompt: str, revised_prompt: str | None, model: str, created: date
) -> str:
    """The indexed text of a generated image (its .source.md): title, what it is, description, prompt and the
    model's revised prompt, written as escaped Markdown."""
    from .markdown import Block, Heading, Paragraph, Span
    from .mdwriter import write

    made_with = f" with {name}" if (name := _one_line(model)) else ""
    blocks: list[Block] = [
        Heading(1, [Span(_one_line(title) or "Image")]),
        Paragraph([Span(f"AI-generated image, made in chat{made_with} on {created.isoformat()}.")]),
    ]
    if description := _one_line(alt_text):
        blocks.append(Paragraph([Span(description)]))
    blocks.extend(_labelled("Prompt", prompt))
    if revised_prompt:
        blocks.extend(_labelled("Revised prompt", revised_prompt))
    return write(blocks)


def _one_line(text: str) -> str:
    return " ".join(xml_safe(text).split())


def _labelled(label: str, text: str) -> "list[Block]":
    """`text` as paragraphs (split at blank lines, lines joined by hard breaks), the first one starting with the
    label."""
    from .markdown import Paragraph, Span

    paragraphs: list[Block] = []
    for part in re.split(r"\n[ \t]*\n", xml_safe(text).replace("\r\n", "\n").replace("\r", "\n")):
        lines = [" ".join(line.split()) for line in part.split("\n")]
        lines = [line for line in lines if line]
        if not lines:
            continue
        if not paragraphs:
            lines[0] = f"{label}: {lines[0]}"
        spans: list[Span] = []
        for line in lines:
            if spans:
                spans.append(Span("\n"))
            spans.append(Span(line))
        paragraphs.append(Paragraph(spans))
    return paragraphs


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
