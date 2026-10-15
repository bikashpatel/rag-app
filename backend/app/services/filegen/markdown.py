"""The Markdown subset every writer renders: markdown-it tokens to a small block tree.

Images become text and are never loaded, raw HTML stays literal text, and only http, https and mailto links are
kept. Footnotes, math and task-list boxes aren't parsed (they stay literal text).
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from functools import cache
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

from . import RenderError

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


@dataclass(frozen=True)
class Span:
    """Inline text with one style. A hard line break is a span whose text is "\\n"."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    link: str | None = None  # http, https or mailto only


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


Block = Heading | Paragraph | ListBlock | Quote | Code | Rule | Table


@cache
def _parser() -> MarkdownIt:
    # maxNesting is 20; link validation already turns javascript: and other unsafe targets into text.
    return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def parse(text: str) -> list[Block]:
    return _blocks(_parser().parse(text), 0, None)[0]


def parse_inline(text: str) -> list[Span]:
    tokens = _parser().parseInline(text)
    return _spans(tokens[0]) if tokens else []


def _blocks(tokens: list[Token], index: int, close: str | None) -> tuple[list[Block], int]:
    blocks: list[Block] = []
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind == close:
            return blocks, index + 1
        if kind == "heading_open":
            blocks.append(Heading(int(token.tag[1]), _spans(tokens[index + 1])))
            index += 3
        elif kind == "paragraph_open":
            if spans := _spans(tokens[index + 1]):
                blocks.append(Paragraph(spans))
            index += 3
        elif kind in ("bullet_list_open", "ordered_list_open"):
            ordered = kind == "ordered_list_open"
            start = int(token.attrs.get("start", 1) or 1) if ordered else 1
            end = kind.replace("_open", "_close")
            items: list[list[Block]] = []
            index += 1
            while index < len(tokens) and tokens[index].type != end:
                if tokens[index].type == "list_item_open":
                    item, index = _blocks(tokens, index + 1, "list_item_close")
                    items.append(item)
                else:
                    index += 1
            blocks.append(ListBlock(ordered, start, items))
            index += 1
        elif kind == "blockquote_open":
            inner, index = _blocks(tokens, index + 1, "blockquote_close")
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
            table, index = _table(tokens, index + 1)
            blocks.append(table)
        else:
            index += 1
    return blocks, index


def _table(tokens: list[Token], index: int) -> tuple[Table, int]:
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
            row.append(_spans(tokens[index + 1]))
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


def _spans(inline: Token) -> list[Span]:
    spans: list[Span] = []
    bold = italic = strike = 0
    link: str | None = None

    def add(text: str, code: bool = False) -> None:
        if not text:
            return
        span = Span(text, bold > 0, italic > 0, strike > 0, code, link)
        if spans and replace(spans[-1], text="") == replace(span, text="") and "\n" not in (text, spans[-1].text):
            spans[-1] = replace(span, text=spans[-1].text + text)
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
            add(f"[Image: {token.content}]" if token.content else "[Image]")
        else:
            add(token.content)
    while spans and spans[-1].text == "\n":
        spans.pop()
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
    """(text, is code) of every span and code block, table cells included."""
    for block in walk(blocks):
        if isinstance(block, Heading | Paragraph):
            yield from ((span.text, span.code) for span in block.spans)
        elif isinstance(block, Code):
            yield block.text, True
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
