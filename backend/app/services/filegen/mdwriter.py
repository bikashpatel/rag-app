"""The block tree written back as Markdown: the MD file, and the indexed source of a PDF.

Only what the other formats render survives: images are already text, an embedded image (a Figure) becomes the
text reference "[Image: caption] (file name)", raw HTML comes out escaped (so the file loads nothing when
previewed), and only http, https and mailto links are written. Every inline run is checked by parsing it back;
emphasis the Markdown rules can't express there is dropped rather than garbled, so writing a parsed file again
gives the same text.
"""

import re

from .markdown import (
    Block,
    Code,
    Figure,
    Heading,
    ListBlock,
    Paragraph,
    Quote,
    Rule,
    Span,
    Table,
    parse_inline,
    text_reference,
)

_ALWAYS_ESCAPED = frozenset("\\`*_[]<>|~")
_ENTITY = re.compile(r"&(?:#[0-9]{1,7};|#[xX][0-9a-fA-F]{1,6};|[A-Za-z][A-Za-z0-9]{1,31};)")
_LINE_START = re.compile(r"[#+\-=]|\d+[.)]")
_BACKTICKS = re.compile(r"`+")
_MARKERS = {"strike": "~~", "bold": "**", "italic": "*"}
_STYLES = tuple(_MARKERS)


def write(blocks: list[Block]) -> str:
    """UTF-8 text with LF line endings, ending with one newline."""
    return _join(blocks).strip("\n") + "\n"


def _join(blocks: list[Block], *, in_item: bool = False) -> str:
    parts: list[str] = []
    previous: Block | None = None
    alternate = False
    for block in blocks:
        # Two lists in a row would merge into one unless the second uses the other marker.
        alternate = (
            isinstance(previous, ListBlock)
            and isinstance(block, ListBlock)
            and previous.ordered == block.ordered
            and not alternate
        )
        if parts:
            parts.append("\n" if in_item and _interrupts(previous, block) else "\n\n")
        parts.append(_block(block, alternate=alternate, first_in_item=in_item and previous is None))
        previous = block
    return "".join(parts)


def _interrupts(previous: Block | None, block: Block) -> bool:
    """A list that may follow a paragraph line directly (CommonMark lets it interrupt the paragraph)."""
    return (
        isinstance(previous, Paragraph)
        and isinstance(block, ListBlock)
        and bool(block.items and block.items[0])
        and (not block.ordered or block.start == 1)
    )


def _block(block: Block, *, alternate: bool = False, first_in_item: bool = False) -> str:
    if isinstance(block, Heading):
        return "#" * block.level + (" " + text if (text := inline(block.spans, heading=True)) else "")
    if isinstance(block, Paragraph):
        return inline(block.spans)
    if isinstance(block, Figure):
        return inline([Span(text_reference(block))])
    if isinstance(block, ListBlock):
        return _list(block, alternate)
    if isinstance(block, Quote):
        return "\n".join(f"> {line}" if line else ">" for line in _join(block.blocks).split("\n"))
    if isinstance(block, Code):
        longest = max((len(run) for run in _BACKTICKS.findall(block.text)), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}\n{block.text}\n{fence}" if block.text else f"{fence}\n{fence}"
    if isinstance(block, Rule):
        return "***" if first_in_item else "---"  # "- ---" would be a rule, not a list item holding one
    return _table(block)


def _list(block: ListBlock, alternate: bool) -> str:
    lines: list[str] = []
    for number, item in enumerate(block.items, start=block.start):
        if block.ordered:
            marker = f"{number}{')' if alternate else '.'}"
        else:
            marker = "*" if alternate else "-"
        body = _join(item, in_item=True)
        if not body:
            lines.append(marker)
            continue
        first, *rest = body.split("\n")
        indent = " " * (len(marker) + 1)
        lines.append(f"{marker} {first}")
        lines.extend(indent + line if line else "" for line in rest)
    return "\n".join(lines)


def _table(table: Table) -> str:
    delimiter = {"left": "---", "center": ":---:", "right": "---:"}

    def row(cells: list[list[Span]]) -> str:
        return "| " + " | ".join(inline(cell, cell=True) for cell in cells) + " |"

    lines = [row(table.header), "| " + " | ".join(delimiter.get(align, "---") for align in table.aligns) + " |"]
    lines.extend(row(cells) for cells in table.rows)
    return "\n".join(lines)


# ---------------- Inline ----------------


def inline(spans: list[Span], *, heading: bool = False, cell: bool = False) -> str:
    """One line of inline Markdown (lines joined by hard breaks), verified by parsing it back."""
    spans = _normalized(spans, single_line=heading or cell)
    if not spans:
        return ""
    for keep in ("all", "links"):
        text = _inline(spans, keep=keep, heading=heading, cell=cell)
        if _parsed_keys(text, cell) == _keys(spans):
            return text
    return _inline([Span(span.text) for span in spans], keep="none", heading=heading, cell=cell)


def _normalized(spans: list[Span], *, single_line: bool) -> list[Span]:
    """Without the whitespace the parser drops: around hard breaks and at both ends."""
    out: list[Span] = []
    for span in spans:
        if span.text == "\n" and single_line:
            span = Span(" ", link=span.link)
        if span.text == "\n":
            _strip_end(out)
            if out and out[-1].text != "\n":
                out.append(span)
            continue
        if (not out or out[-1].text == "\n") and not span.code:
            span = _with_text(span, span.text.lstrip(" \t"))
        if span.text:
            out.append(span)
    while out and out[-1].text == "\n":
        out.pop()
    _strip_end(out)
    return out


def _strip_end(spans: list[Span]) -> None:
    while spans and not spans[-1].code and spans[-1].text != "\n":
        text = spans[-1].text.rstrip(" \t")
        if text:
            spans[-1] = _with_text(spans[-1], text)
            return
        spans.pop()


def _with_text(span: Span, text: str) -> Span:
    return Span(text, span.bold, span.italic, span.strike, span.code, span.link)


def _inline(spans: list[Span], *, keep: str, heading: bool, cell: bool) -> str:
    out: list[str] = []
    index = 0
    while index < len(spans):
        link = spans[index].link if keep != "none" else None
        end = index + 1
        while end < len(spans) and (spans[end].link if keep != "none" else None) == link:
            end += 1
        line_start = link is None and (not out or out[-1].endswith("\n"))
        content = _emphasized(spans[index:end], keep=keep, heading=heading, cell=cell, line_start=line_start)
        out.append(f"[{content}]({_url(link)})" if link is not None else content)
        index = end
    return "".join(out)


def _emphasized(spans: list[Span], *, keep: str, heading: bool, cell: bool, line_start: bool) -> str:
    out: list[str] = []
    stack: list[str] = []

    def close(keep_styles: tuple[str, ...] = ()) -> None:
        while any(style not in keep_styles for style in stack):
            out.append(_MARKERS[stack.pop()])

    def open_(styles: tuple[str, ...]) -> bool:
        opening = [style for style in styles if style not in stack]
        out.extend(_MARKERS[style] for style in opening)
        stack.extend(opening)
        return bool(opening)

    def styles(span: Span | None) -> tuple[str, ...]:
        if span is None or span.text == "\n" or keep != "all":
            return ()
        return tuple(style for style in _STYLES if getattr(span, style))

    for position, span in enumerate(spans):
        if span.text == "\n":
            close()
            out.append("\\\n")
            line_start = True
            continue
        wanted = styles(span)
        if span.code and keep != "none":
            close(wanted)
            open_(wanted)
            out.append(_code_span(span.text, cell))
            line_start = False
            continue
        core = span.text.strip(" \t")
        lead = span.text[: len(span.text) - len(span.text.lstrip(" \t"))]
        trail = span.text[len(lead) + len(core) :]
        if lead:  # markers stay open over whitespace only when the text on both sides has their style
            close(wanted)
            out.append(lead)
            line_start = False
        if core:
            close(wanted)
            opened = open_(wanted)
            out.append(_escape(core, line_start=line_start and not opened, heading=heading))
            line_start = False
        if trail:
            following = styles(spans[position + 1] if position + 1 < len(spans) else None)
            close(tuple(style for style in wanted if style in following))
            out.append(trail)
    close()
    return "".join(out)


def _escape(text: str, *, line_start: bool, heading: bool) -> str:
    out: list[str] = []
    for position, ch in enumerate(text):
        if ch in _ALWAYS_ESCAPED or (heading and ch == "#"):
            out.append("\\" + ch)
        elif ch == "&" and _ENTITY.match(text, position):
            out.append("\\&")
        elif ch == "!" and text.startswith("[", position + 1):
            out.append("\\!")
        else:
            out.append(ch)
    escaped = "".join(out)
    if line_start and (found := _LINE_START.match(escaped)):
        mark = found.end() - 1  # the character that makes a heading, a list item or an underline
        escaped = escaped[:mark] + "\\" + escaped[mark:]
    return escaped


def _code_span(text: str, cell: bool) -> str:
    if cell:
        text = text.replace("|", "\\|")  # the table splits cells before code spans are read
    longest = max((len(run) for run in _BACKTICKS.findall(text)), default=0)
    fence = "`" * (longest + 1)
    spaced = text.startswith(" ") and text.endswith(" ") and bool(text.strip())  # the parser strips one space each
    padded = text.startswith("`") or text.endswith("`") or spaced
    return f"{fence} {text} {fence}" if padded else f"{fence}{text}{fence}"


def _url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")


def _keys(spans: list[Span]) -> list[tuple[object, ...]]:
    """What must survive a round trip: every character, and the style of every one that isn't whitespace."""
    keys: list[tuple[object, ...]] = []
    for span in spans:
        for ch in span.text:
            if ch.isspace() and not span.code:
                keys.append((ch, span.link))
            else:
                keys.append((ch, span.bold, span.italic, span.strike, span.code, span.link))
    return keys


def _parsed_keys(text: str, cell: bool) -> list[tuple[object, ...]]:
    if cell:
        text = text.replace("\\|", "|")
    return _keys(_normalized(parse_inline(text), single_line=False))
