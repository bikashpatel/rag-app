"""PDF rendering with fpdf2: A4, Noto Sans fonts, real shaping for Indian scripts.

Every text run gets its font set explicitly (fonts.Runs), and fpdf2's fallback fonts are only a safety net:
fpdf2 2.8.8 and 2.8.9 lose the font when a line's first fragment uses a fallback font before the page's font is
set, so the following lines were drawn with the wrong font. Shaping is switched on only for Indian-script runs
(shaping everything made long English documents several times slower).
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from fpdf import FPDF, FontFace
from fpdf.enums import TableBordersLayout, WrapMode
from fpdf.output import OutputProducer
from fpdf.text_region import Paragraph as TextParagraph

from . import CREATOR, RenderError
from .fonts import (
    INDIAN_FAMILIES,
    MAX_UNSUPPORTED_SHARE,
    MONO,
    PLACEHOLDER,
    SANS,
    Runs,
    font_files,
    scan,
)
from .markdown import (
    MAX_LIST_LEVELS,
    Block,
    Code,
    Heading,
    ListBlock,
    Paragraph,
    Quote,
    Rule,
    Span,
    Table,
    check_table_caps,
    link_label,
    plain,
    texts,
)

Color = tuple[int, int, int]

BODY_PT = 10.5
BODY_LINE_HEIGHT = 1.35
HEADING_PT = {1: 20.0, 2: 15.5, 3: 13.0, 4: 11.5, 5: 10.5, 6: 10.5}
HEADING_LINE_HEIGHT = 1.25
CODE_PT = 8.8
CODE_LINE_HEIGHT = 1.3
INLINE_CODE_SCALE = 0.92
LIST_INDENT_MM = 7.0
QUOTE_INDENT_MM = 6.0
CODE_INDENT_MM = 4.0
PARAGRAPH_GAP_MM = 3.0
LIST_ITEM_GAP_MM = 1.5
BULLETS = ("•", "–", "‣", "–")  # by level
TEXT: Color = (17, 24, 39)
QUOTE_TEXT: Color = (75, 85, 99)
LINK: Color = (29, 78, 216)
CODE_RULE: Color = (203, 213, 225)
RULE: Color = (209, 213, 219)
FOOTER: Color = (120, 120, 120)
TABLE_HEADER_FILL: Color = (243, 244, 246)
_TOO_TALL = (
    "A table row is too tall to fit on one page of a PDF. Shorten the cell text, or create a docx or xlsx file instead."
)


@dataclass(frozen=True)
class _Context:
    indent: float = 0.0  # mm from the left margin
    level: int = 0  # list nesting
    italic: bool = False
    color: Color = TEXT


class _NoOpenAction(OutputProducer):
    """fpdf2 always opens the document with an /OpenAction (a zoom destination); generated files have no actions."""

    def _finalize_catalog(self, catalog_obj: Any, *args: Any, **kwargs: Any) -> None:
        super()._finalize_catalog(catalog_obj, *args, **kwargs)
        catalog_obj.open_action = None


class _Document(FPDF):
    def footer(self) -> None:
        shaping = self.text_shaping
        if shaping:
            self.set_text_shaping(False)  # the page-count alias isn't replaced in shaped text
        self.set_font(SANS, "", 8)
        self.set_text_color(*FOOTER)
        self.set_y(-12)
        self.cell(0, 5, f"{self.page_no()} / {{nb}}", align="C")
        if shaping:
            self.set_text_shaping(True)


def render_pdf(blocks: list[Block], title: str) -> tuple[bytes, int, list[str]]:
    """(the PDF, its page count, notes for the tool result)."""
    check_table_caps(blocks)
    report = scan([text for text, _ in texts(blocks)])
    if report.unsupported_letters and report.unsupported_letters >= MAX_UNSUPPORTED_SHARE * report.letters:
        script = report.unsupported.most_common(1)[0][0]
        raise RenderError(
            f"PDF files support Latin, Greek, Cyrillic and Indian scripts; this content has {script} text. "
            "Create a docx or md file instead."
        )
    writer = _Writer(blocks, title)
    try:
        data = writer.render()
    except ValueError as exc:
        if "too high" in str(exc):
            raise RenderError(_TOO_TALL) from exc
        raise
    notes = [
        f"{count} characters in {script} couldn't be shown in the PDF and appear as {PLACEHOLDER}. "
        "A docx or md file keeps them."
        for script, count in writer.runs.placeholders.items()
    ]
    if writer.runs.dropped:
        notes.append(f"{writer.runs.dropped} other characters couldn't be shown in the PDF and were left out.")
    return data, writer.pdf.page, notes


class _Writer:
    def __init__(self, blocks: list[Block], title: str) -> None:
        self.blocks = blocks
        self.runs = Runs()
        families = {SANS}
        for text, code in texts(blocks):
            families.update(family for family, _ in self.runs(_clean(text, code), MONO if code else SANS))
        pdf = self.pdf = _Document(format="A4")
        pdf.set_margins(20, 18, 20)
        pdf.set_auto_page_break(True, margin=18)
        for family in sorted(families, key=lambda name: name != SANS):
            for style, path in font_files(family).items():
                pdf.add_font(family, style=style, fname=path)
        if len(families) > 1:  # only a safety net: every run sets its own font
            pdf.set_fallback_fonts(sorted(families - {SANS}), exact_match=False)
        pdf.set_title(title)
        pdf.set_creator(CREATOR)
        pdf.set_creation_date(datetime.now(timezone.utc))

    def render(self) -> bytes:
        self.pdf.add_page()
        self.pdf.set_font(SANS, "", BODY_PT)
        self._blocks(self.blocks, _Context())
        return bytes(self.pdf.output(output_producer_class=_NoOpenAction))

    # ---------------- Blocks ----------------

    def _blocks(self, blocks: list[Block], context: _Context, bullet: str = "") -> None:
        for block in blocks:
            if isinstance(block, Heading):
                size = HEADING_PT.get(block.level, BODY_PT)
                spans = [replace(span, bold=True) for span in block.spans]
                self._paragraph(
                    spans,
                    context,
                    size=size,
                    line_height=HEADING_LINE_HEIGHT,
                    top=0.45 * size,
                    bottom=0.2 * size,
                    bullet=bullet,
                )
            elif isinstance(block, Paragraph):
                gap = LIST_ITEM_GAP_MM if context.level else PARAGRAPH_GAP_MM
                self._paragraph(block.spans, context, bullet=bullet, bottom=gap)
            elif isinstance(block, ListBlock):
                self._list(block, context, bullet)
            elif isinstance(block, Quote):
                quoted = replace(context, indent=context.indent + QUOTE_INDENT_MM, italic=True, color=QUOTE_TEXT)
                self._blocks(block.blocks, quoted, bullet)
            elif isinstance(block, Code):
                self._code(block.text, context, bullet)
            elif isinstance(block, Rule):
                self._rule(context, bullet)
            else:
                self._table(block, context, bullet)
            bullet = ""

    def _list(self, block: ListBlock, context: _Context, bullet: str) -> None:
        if bullet:  # a list as the first thing in a list item
            self._paragraph([], context, bullet=bullet, bottom=0)
        deeper = context.level < MAX_LIST_LEVELS  # deeper levels are drawn at the last one
        nested = replace(context, level=context.level + 1, indent=context.indent + (LIST_INDENT_MM if deeper else 0.0))
        for number, item in enumerate(block.items, start=block.start):
            marker = f"{number}." if block.ordered else BULLETS[min(nested.level, MAX_LIST_LEVELS) - 1]
            if item:
                self._blocks(item, nested, marker)
            else:
                self._paragraph([], nested, bullet=marker)
        if not context.level:
            self.pdf.ln(LIST_ITEM_GAP_MM)

    def _paragraph(
        self,
        spans: list[Span],
        context: _Context,
        *,
        size: float = BODY_PT,
        line_height: float = BODY_LINE_HEIGHT,
        top: float = 0.0,
        bottom: float = PARAGRAPH_GAP_MM,
        bullet: str = "",
    ) -> None:
        pdf = self.pdf
        self._font(SANS, "I" if context.italic else "", size)  # the bullet's font
        pdf.set_text_color(*context.color)
        with pdf.text_columns(line_height=line_height, l_margin=pdf.l_margin + context.indent) as columns:
            with columns.paragraph(
                top_margin=top, bottom_margin=bottom, bullet_string=bullet, bullet_r_margin=2 if bullet else None
            ) as paragraph:
                if not spans:
                    paragraph.write(" ")
                for position, span in enumerate(spans):
                    following = spans[position + 1] if position + 1 < len(spans) else None
                    self._span(paragraph, span, context, size)
                    if span.link and (following is None or following.link != span.link):
                        start = position
                        while start > 0 and spans[start - 1].link == span.link:
                            start -= 1
                        label = link_label("".join(item.text for item in spans[start : position + 1]), span.link)
                        if label:
                            self._span(paragraph, Span(f" ({label})", bold=span.bold), context, size, color=LINK)

    def _span(
        self, paragraph: TextParagraph, span: Span, context: _Context, size: float, color: Color | None = None
    ) -> None:
        style = "B" if span.bold else ""
        style += "I" if span.italic or context.italic else ""
        style += "S" if span.strike else ""
        if span.code:
            size *= INLINE_CODE_SCALE
        text_color = color or (LINK if span.link else context.color)
        for family, chunk in self.runs(_clean(span.text, span.code), MONO if span.code else SANS):
            self._font(family, style, size)
            self.pdf.set_text_color(*text_color)
            paragraph.write(chunk, link=span.link)

    def _font(self, family: str, style: str, size: float) -> None:
        shaping = family in INDIAN_FAMILIES
        if shaping != bool(self.pdf.text_shaping):
            self.pdf.set_text_shaping(shaping)
        self.pdf.set_font(family, style, size)

    def _code(self, text: str, context: _Context, bullet: str) -> None:
        if bullet:
            self._paragraph([], context, bullet=bullet, bottom=0)
        pdf = self.pdf
        x = pdf.l_margin + context.indent + 1
        start_page, start_y = pdf.page, pdf.get_y() + 1
        self._font(MONO, "", CODE_PT)
        pdf.set_text_color(*TEXT)
        left = pdf.l_margin + context.indent + CODE_INDENT_MM
        with pdf.text_columns(line_height=CODE_LINE_HEIGHT, l_margin=left, wrapmode=WrapMode.CHAR) as columns:
            with columns.paragraph(top_margin=1, bottom_margin=1) as paragraph:
                for line in _clean(text, True).split("\n"):
                    for family, chunk in self.runs(line, MONO):
                        self._font(family, "", CODE_PT)
                        pdf.set_text_color(*TEXT)
                        paragraph.write(chunk)
                    paragraph.ln()
        end_page, end_y = pdf.page, pdf.get_y()
        pdf.set_draw_color(*CODE_RULE)
        pdf.set_line_width(0.6)
        for page in range(start_page, end_page + 1):  # a long block continues on later pages
            pdf.page = page
            top = start_y if page == start_page else pdf.t_margin
            bottom = end_y - 1 if page == end_page else pdf.h - pdf.b_margin
            if bottom > top:
                pdf.line(x, top, x, bottom)
        pdf.page = end_page
        pdf.ln(PARAGRAPH_GAP_MM)

    def _rule(self, context: _Context, bullet: str) -> None:
        if bullet:
            self._paragraph([], context, bullet=bullet, bottom=0)
        pdf = self.pdf
        y = pdf.get_y() + 2
        if y > pdf.h - pdf.b_margin:
            return
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.3)
        pdf.line(pdf.l_margin + context.indent, y, pdf.w - pdf.r_margin, y)
        pdf.ln(5)

    def _table(self, table: Table, context: _Context, bullet: str) -> None:
        if bullet:
            self._paragraph([], context, bullet=bullet, bottom=0)
        pdf = self.pdf
        rows: list[list[tuple[str, str]]] = []  # (family of the first character, text)
        shaping = False
        for cells in (table.header, *table.rows):
            row: list[tuple[str, str]] = []
            for cell in cells:
                runs = self.runs(_clean(plain(cell), False), SANS)
                shaping = shaping or any(family in INDIAN_FAMILIES for family, _ in runs)
                row.append((runs[0][0] if runs else SANS, "".join(chunk for _, chunk in runs)))
            rows.append(row)
        columns = len(table.header)
        if not columns:
            return
        widths = [max(4, min(40, max(len(row[column][1]) for row in rows))) for column in range(columns)]
        size = 9.5 if columns <= 6 else 8.5 if columns <= 10 else 7.5
        self._font(SANS, "", size)
        if shaping:  # cells use fpdf2's fallback fonts after their first character, so shaping covers the table
            pdf.set_text_shaping(True)
        pdf.set_text_color(*TEXT)
        aligns = {"left": "L", "center": "C", "right": "R"}
        left_margin = pdf.l_margin
        pdf.set_left_margin(left_margin + context.indent)
        pdf.set_x(pdf.l_margin)
        try:
            with pdf.table(
                col_widths=widths,
                width=pdf.w - pdf.r_margin - pdf.l_margin,
                line_height=pdf.font_size * 1.6,
                padding=(1.2, 2),
                borders_layout=TableBordersLayout.HORIZONTAL_LINES,
                first_row_as_headings=True,
                headings_style=FontFace(emphasis="BOLD", fill_color=TABLE_HEADER_FILL),
                text_align="LEFT",
                markdown=False,
            ) as grid:
                for laid_out in rows:
                    row_cells = grid.row()
                    for column, (family, text) in enumerate(laid_out):
                        align = aligns.get(table.aligns[column] if column < len(table.aligns) else "left", "L")
                        row_cells.cell(text, align=align, style=FontFace(family=family))
        finally:
            pdf.set_left_margin(left_margin)
        pdf.ln(4)


def _clean(text: str, code: bool) -> str:
    """Tabs as spaces, and no other control characters except line breaks."""
    text = text.replace("\t", "    " if code else " ")
    return "".join(ch for ch in text if ch == "\n" or ch >= " " and ch != "\x7f")
