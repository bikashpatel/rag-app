"""The Markdown subset every writer renders: markdown-it tokens to a small block tree.

An image reference ![caption](image:<id>) to one of the request's images becomes a Figure when it stands in a
paragraph; every other image becomes text and nothing is ever loaded from a URL. Raw HTML stays literal text, and
only http, https and mailto links are kept. Footnotes, math and task-list boxes aren't parsed (they stay literal
text).
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from functools import cache
from pathlib import PurePath
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

from . import MAX_IMAGES_PER_FILE, ImageRef, RenderError

_SAFE_LINK = re.compile(r"(?:https?://|mailto:)", re.IGNORECASE)
_TABLE_CAPS = (
    "Tables in pdf and docx files can have at most 20 columns, 500 rows and 2,000 characters per cell "
    "(10,000 cells per file); use xlsx for large tables."
)
MAX_TABLE_COLUMNS = 20
MAX_TABLE_ROWS = 500
MAX_TABLE_CELLS = 10_000
MAX_CELL_CHARS = 2_000
MAX_LIST_LEVELS = 4  # deeper levels are drawn at this level
_IMAGE_SRC = re.compile(r"image:([0-9]{1,10})")


@dataclass(frozen=True)
class Span:
    """Inline text with one style. A hard line break is a span whose text is "\\n"."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    link: str | None = None  # http, https or mailto only
    image: int | None = None  # an embedded image (text is its caption); only while a paragraph is split


@dataclass(frozen=True)
class Heading:
    level: int
    spans: list[Span]


@dataclass(frozen=True)
class Paragraph:
    spans: list[Span]


@dataclass(frozen=True)
class ListBlock:
    ordered: bool
    start: int
    items: list[list["Block"]]


@dataclass(frozen=True)
class Quote:
    blocks: list["Block"]  # never another Quote: nested quotes are flattened


@dataclass(frozen=True)
class Code:
    text: str


@dataclass(frozen=True)
class Rule:
    pass


@dataclass(frozen=True)
class Table:
    header: list[list[Span]]
    rows: list[list[list[Span]]]
    aligns: list[str]  # "left" | "center" | "right", one per column


@dataclass(frozen=True)
class Figure:
    """An image the file embeds (pdf, docx) or refers to by name (xlsx, md)."""

    image_id: int
    caption: str
    ref: ImageRef


Block = Heading | Paragraph | ListBlock | Quote | Code | Rule | Table | Figure


@cache
def _parser() -> MarkdownIt:
    # maxNesting is 20; link validation already turns javascript: and other unsafe targets into text.
    return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def parse(text: str, images: Mapping[int, ImageRef] | None = None) -> list[Block]:
    """The block tree. A paragraph's references to `images` split it into the text before, a Figure and the text
    after; in headings and table cells they're text, as are all other images."""
    return _Parser(images or {}).blocks(_parser().parse(text), 0, None)[0]


def parse_inline(text: str) -> list[Span]:
    tokens = _parser().parseInline(text)
    return _Parser({}).spans(tokens[0]) if tokens else []


def image_ids(text: str) -> list[int]:
    """The ids of image:<id> references standing in paragraphs (the ones a file can embed), in order, each once."""
    tokens = _parser().parse(text)
    ids: dict[int, None] = {}
    for previous, token in zip(tokens, tokens[1:], strict=False):
        if token.type == "inline" and previous.type == "paragraph_open":
            for child in token.children or []:
                if child.type == "image" and (found := _image_id(child)) is not None:
                    ids.setdefault(found)
    return list(ids)


def _image_id(token: Token) -> int | None:
    found = _IMAGE_SRC.fullmatch(str(token.attrs.get("src", "")))
    return int(found.group(1)) if found else None


class _Parser:
    def __init__(self, images: Mapping[int, ImageRef]) -> None:
        self.images = images

    def blocks(self, tokens: list[Token], index: int, close: str | None) -> tuple[list[Block], int]:
        return _blocks(self, tokens, index, close)

    def spans(self, inline: Token, *, figures: bool = False) -> list[Span]:
        return _spans(inline, self.images, figures)


def _blocks(parser: _Parser, tokens: list[Token], index: int, close: str | None) -> tuple[list[Block], int]:
    blocks: list[Block] = []
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind == close:
            return blocks, index + 1
        if kind == "heading_open":
            blocks.append(Heading(int(token.tag[1]), parser.spans(tokens[index + 1])))
            index += 3
        elif kind == "paragraph_open":
            blocks.extend(_split(parser.spans(tokens[index + 1], figures=True), parser.images))
            index += 3
        elif kind in ("bullet_list_open", "ordered_list_open"):
            ordered = kind == "ordered_list_open"
            start = int(token.attrs.get("start", 1) or 1) if ordered else 1
            end = kind.replace("_open", "_close")
            items: list[list[Block]] = []
            index += 1
            while index < len(tokens) and tokens[index].type != end:
                if tokens[index].type == "list_item_open":
                    item, index = parser.blocks(tokens, index + 1, "list_item_close")
                    items.append(item)
                else:
                    index += 1
            blocks.append(ListBlock(ordered, start, items))
            index += 1
        elif kind == "blockquote_open":
            inner, index = parser.blocks(tokens, index + 1, "blockquote_close")
            flat = [nested for block in inner for nested in (block.blocks if isinstance(block, Quote) else [block])]
            if flat:
                blocks.append(Quote(flat))
        elif kind in ("fence", "code_block"):
            text = token.content[:-1] if token.content.endswith("\n") else token.content
            blocks.append(Code(text))
            index += 1
        elif kind == "hr":
            blocks.append(Rule())
            index += 1
        elif kind == "table_open":
            table, index = _table(parser, tokens, index + 1)
            blocks.append(table)
        else:
            index += 1
    return blocks, index


def _table(parser: _Parser, tokens: list[Token], index: int) -> tuple[Table, int]:
    header: list[list[Span]] = []
    rows: list[list[list[Span]]] = []
    aligns: list[str] = []
    row: list[list[Span]] = []
    in_head = False
    while index < len(tokens) and tokens[index].type != "table_close":
        token = tokens[index]
        if token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif token.type in ("th_open", "td_open"):
            if in_head:
                style = str(token.attrs.get("style", ""))
                aligns.append("right" if "right" in style else "center" if "center" in style else "left")
            row.append(parser.spans(tokens[index + 1]))
        elif token.type == "tr_close":
            if in_head:
                header = row
            else:
                rows.append(row)
            row = []
        index += 1
    columns = len(header)
    rows = [(cells + [[] for _ in range(columns)])[:columns] for cells in rows]
    return Table(header, rows, aligns), index + 1


def _spans(inline: Token, images: Mapping[int, ImageRef], figures: bool) -> list[Span]:
    """The inline runs. With `figures`, a reference to one of `images` is a span of its own with `image` set."""
    spans: list[Span] = []
    bold = italic = strike = 0
    link: str | None = None

    def add(text: str, code: bool = False) -> None:
        if not text:
            return
        span = Span(text, bold > 0, italic > 0, strike > 0, code, link)
        previous = spans[-1] if spans else None
        if (
            previous is not None
            and previous.image is None
            and replace(previous, text="") == replace(span, text="")
            and "\n" not in (text, previous.text)
        ):
            spans[-1] = replace(span, text=previous.text + text)
        else:
            spans.append(span)

    for token in inline.children or []:
        kind = token.type
        if kind == "code_inline":
            add(token.content, code=True)
        elif kind == "softbreak":
            add(" ")
        elif kind == "hardbreak":
            add("\n")
        elif kind in ("strong_open", "strong_close"):
            bold += 1 if kind == "strong_open" else -1
        elif kind in ("em_open", "em_close"):
            italic += 1 if kind == "em_open" else -1
        elif kind in ("s_open", "s_close"):
            strike += 1 if kind == "s_open" else -1
        elif kind == "link_open":
            href = str(token.attrs.get("href", ""))
            link = href if _SAFE_LINK.match(href) else None
        elif kind == "link_close":
            link = None
        elif kind == "image":
            image_id = _image_id(token)
            ref = images.get(image_id) if image_id is not None else None
            if ref is None:
                add(f"[Image: {token.content}]" if token.content else "[Image]")
            elif figures:
                spans.append(Span(_caption(token, ref), image=ref.id))
            else:
                add(f"[Image: {_caption(token, ref)}]")
        else:
            add(token.content)
    while spans and spans[-1].text == "\n":
        spans.pop()
    return spans


def _caption(image: Token, ref: ImageRef) -> str:
    """The reference's text on one line, or the image's title (its file name without the extension)."""
    return " ".join(_alt(image.children).split()) or PurePath(ref.filename).stem or ref.filename


def _alt(tokens: list[Token] | None) -> str:
    """An image's description as plain text, without its Markdown (escapes, emphasis) and line breaks."""
    out: list[str] = []
    for token in tokens or []:
        if token.type in ("text", "text_special", "code_inline"):
            out.append(token.content)
        elif token.type == "image":
            out.append(_alt(token.children))
        elif token.type in ("softbreak", "hardbreak"):
            out.append(" ")
    return "".join(out)


def _split(spans: list[Span], images: Mapping[int, ImageRef]) -> list[Block]:
    """A paragraph's spans as Paragraph, Figure, Paragraph, …; parts with only whitespace are dropped."""
    if all(span.image is None for span in spans):
        return [Paragraph(spans)] if spans else []
    blocks: list[Block] = []
    part: list[Span] = []
    for span in [*spans, None]:
        if span is not None and span.image is None:
            part.append(span)
            continue
        if text := _trimmed(part):
            blocks.append(Paragraph(text))
        part = []
        if span is not None and span.image is not None:
            blocks.append(Figure(span.image, span.text, images[span.image]))
    return blocks


def _trimmed(spans: list[Span]) -> list[Span]:
    """Without hard breaks, spaces and tabs at both ends (code keeps its spaces); [] when nothing else is left."""
    spans = list(spans)
    while spans and (spans[0].text == "\n" or (not spans[0].code and not spans[0].text.strip(" \t"))):
        spans.pop(0)
    while spans and (spans[-1].text == "\n" or (not spans[-1].code and not spans[-1].text.strip(" \t"))):
        spans.pop()
    if spans and not spans[0].code:
        spans[0] = replace(spans[0], text=spans[0].text.lstrip(" \t"))
    if spans and not spans[-1].code:
        spans[-1] = replace(spans[-1], text=spans[-1].text.rstrip(" \t"))
    return spans


def plain(spans: list[Span]) -> str:
    return "".join(" " if span.text == "\n" else span.text for span in spans)


def link_label(span_text: str, url: str) -> str | None:
    """What follows a link whose text isn't its URL: the host (lowercased, without "www."), or the address of a
    mailto link. None when the visible text already is the URL."""
    if _bare(span_text) == _bare(url):
        return None
    if url.lower().startswith("mailto:"):
        return url[len("mailto:") :].split("?", 1)[0]
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.") or None


def _bare(url: str) -> str:
    text = url.strip().lower()
    text = re.sub(r"^(?:mailto:|[a-z][a-z0-9+.-]*://)", "", text)
    return text.rstrip("/")


def walk(blocks: list[Block]) -> Iterator[Block]:
    """Every block, nested ones included, in document order."""
    for block in blocks:
        yield block
        if isinstance(block, ListBlock):
            for item in block.items:
                yield from walk(item)
        elif isinstance(block, Quote):
            yield from walk(block.blocks)


def texts(blocks: list[Block]) -> Iterator[tuple[str, bool]]:
    """(text, is code) of every span and code block, table cells and figure captions included."""
    for block in walk(blocks):
        if isinstance(block, Heading | Paragraph):
            yield from ((span.text, span.code) for span in block.spans)
        elif isinstance(block, Code):
            yield block.text, True
        elif isinstance(block, Figure):
            yield block.caption, False
        elif isinstance(block, Table):
            for cells in (block.header, *block.rows):
                for cell in cells:
                    yield from ((span.text, span.code) for span in cell)


def check_table_caps(blocks: list[Block]) -> None:
    """PDF and DOCX tables are laid out cell by cell; xlsx is the format for large tables."""
    cells = 0
    for block in walk(blocks):
        if not isinstance(block, Table):
            continue
        if len(block.header) > MAX_TABLE_COLUMNS or len(block.rows) > MAX_TABLE_ROWS:
            raise RenderError(_TABLE_CAPS)
        cells += len(block.header) * len(block.rows)  # body cells
        if cells > MAX_TABLE_CELLS:
            raise RenderError(_TABLE_CAPS)
        for row in (block.header, *block.rows):
            if any(len(plain(cell)) > MAX_CELL_CHARS for cell in row):
                raise RenderError(_TABLE_CAPS)


def text_reference(figure: Figure) -> str:
    """How a file that doesn't embed the image names it: "[Image: caption] (file name)"."""
    return f"[Image: {figure.caption}] ({figure.ref.filename})"


def has_figures(blocks: list[Block]) -> bool:
    return any(isinstance(block, Figure) for block in walk(blocks))


def check_image_caps(blocks: list[Block]) -> None:
    if sum(isinstance(block, Figure) for block in walk(blocks)) > MAX_IMAGES_PER_FILE:
        raise RenderError(f"A file can embed at most {MAX_IMAGES_PER_FILE} images.")
