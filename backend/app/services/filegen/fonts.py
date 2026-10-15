"""Bundled fonts and the choice of a font for every character of a generated PDF.

The TTFs in app/fonts are the unhinted Noto Sans fonts from
https://github.com/notofonts/notofonts.github.io at commit 5c3913063bb189df4edc94b766584dfb4c04a187
(fonts/<Family>/unhinted/ttf/<file>), under the SIL Open Font License 1.1 (app/fonts/OFL.txt). To refresh them,
download the same files from a newer commit, check them visually with an Indian-script sample, and update the
sha256 table in docs/briefs/document-generation.md. CJK, Arabic, Hebrew, Thai and emoji fonts aren't bundled.
"""

import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from fontTools.ttLib import TTFont
from fpdf.unicode_script import UnicodeScript, get_unicode_script

FONT_DIR = Path(__file__).resolve().parents[2] / "fonts"

SANS = "NotoSans"
MONO = "NotoSansMono"
MATH = "NotoSansMath"
SYMBOLS2 = "NotoSansSymbols2"
SYMBOLS = "NotoSansSymbols"
INDIAN: dict[UnicodeScript, str] = {
    UnicodeScript.DEVANAGARI: "NotoSansDevanagari",
    UnicodeScript.BENGALI: "NotoSansBengali",
    UnicodeScript.GURMUKHI: "NotoSansGurmukhi",
    UnicodeScript.GUJARATI: "NotoSansGujarati",
    UnicodeScript.ORIYA: "NotoSansOriya",
    UnicodeScript.TAMIL: "NotoSansTamil",
    UnicodeScript.TELUGU: "NotoSansTelugu",
    UnicodeScript.KANNADA: "NotoSansKannada",
    UnicodeScript.MALAYALAM: "NotoSansMalayalam",
    UnicodeScript.SINHALA: "NotoSansSinhala",
}
INDIAN_FAMILIES = frozenset(INDIAN.values())
_SEARCH_ORDER = (SANS, MATH, SYMBOLS2, SYMBOLS)  # for a character the run's own font doesn't have
_NEUTRAL = (UnicodeScript.COMMON, UnicodeScript.INHERITED, UnicodeScript.UNKNOWN)
PLACEHOLDER = "□"  # drawn for a letter of a script no bundled font has
MAX_UNSUPPORTED_SHARE = 0.02
# Emoji are left out of PDFs (no bundled font draws them in colour): the pictographs outside the Basic
# Multilingual Plane always, and symbols that may be emoji (check marks, stars, arrows) only when no font has them.
_EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0xE0020, 0xE007F))
_SYMBOL_RANGES = ((0x2300, 0x23FF), (0x2600, 0x27BF), (0x2B00, 0x2BFF))
_ALWAYS_DROPPED = frozenset({0xFE0E, 0xFE0F, *range(0x1F3FB, 0x1F400)})
_ZWJ = 0x200D


def font_files(family: str) -> dict[str, Path]:
    """The file behind each style ("", "B", "I", "BI"), so any style works with any family."""
    regular = FONT_DIR / f"{family}-Regular.ttf"
    if family == SANS:
        names = {"": "Regular", "B": "Bold", "I": "Italic", "BI": "BoldItalic"}
        return {style: FONT_DIR / f"{SANS}-{name}.ttf" for style, name in names.items()}
    if family == MONO or family in INDIAN_FAMILIES:
        bold = FONT_DIR / f"{family}-Bold.ttf"
        return {"": regular, "B": bold, "I": regular, "BI": bold}
    return dict.fromkeys(("", "B", "I", "BI"), regular)


@cache
def cmap(family: str) -> frozenset[int]:
    """The code points every style of the family has a glyph for."""
    covered: frozenset[int] | None = None
    for path in set(font_files(family).values()):
        points = frozenset(TTFont(path, lazy=True).getBestCmap())
        covered = points if covered is None else covered & points
    return covered or frozenset()


def is_emoji(code: int) -> bool:
    return any(low <= code <= high for low, high in _EMOJI_RANGES)


def _maybe_emoji(code: int) -> bool:
    return is_emoji(code) or any(low <= code <= high for low, high in _SYMBOL_RANGES)


def script_name(ch: str) -> str:
    return get_unicode_script(ch).name.replace("_", " ").title()


@dataclass
class Report:
    """Characters no bundled font has: letters per script (drawn as the placeholder) and others (left out)."""

    letters: int = 0
    unsupported: Counter[str] = field(default_factory=Counter)  # script name -> letters

    @property
    def unsupported_letters(self) -> int:
        return sum(self.unsupported.values())


def scan(texts: list[str]) -> Report:
    """Count the letters, and those no bundled font has, per script."""
    report = Report()
    for text in texts:
        for ch in text:
            if not unicodedata.category(ch).startswith("L"):
                continue
            report.letters += 1
            if _find_family(ch, SANS, None) is None:
                report.unsupported[script_name(ch)] += 1
    return report


def _find_family(ch: str, base: str, current: str | None) -> str | None:
    code = ord(ch)
    script = get_unicode_script(ch)
    if (indian := INDIAN.get(script)) is not None and code in cmap(indian):
        return indian
    if script in _NEUTRAL and current is not None and code in cmap(current):
        return current
    if code in cmap(base):
        return base
    if script not in _NEUTRAL and unicodedata.category(ch)[0] in "LM":
        # Letters of a script only from a text font: Noto Sans Math has Arabic letters, but no shaping or direction.
        return SANS if code in cmap(SANS) else None
    return next((family for family in _SEARCH_ORDER if code in cmap(family)), None)


@dataclass
class Runs:
    """Splits text into runs of one font each, so every run's font has every character in it."""

    placeholders: Counter[str] = field(default_factory=Counter)  # script name -> letters drawn as the placeholder
    dropped: int = 0  # other characters no font has (emoji aren't counted)
    _cache: dict[tuple[str, str], list[tuple[str, str]]] = field(default_factory=dict)

    def __call__(self, text: str, base: str = SANS) -> list[tuple[str, str]]:
        key = (text, base)
        if (cached := self._cache.get(key)) is None:
            cached = self._cache[key] = self._split(text, base)
        return cached

    def _split(self, text: str, base: str) -> list[tuple[str, str]]:
        runs: list[tuple[str, list[str]]] = []
        current: str | None = None
        for position, ch in enumerate(text):
            code = ord(ch)
            if code in _ALWAYS_DROPPED or is_emoji(code) or (code == _ZWJ and _next_to_emoji(text, position)):
                continue
            if ch == "\n":
                family: str | None = current or base
            else:
                family = _find_family(ch, base, current)
            if family is None:
                category = unicodedata.category(ch)
                if _maybe_emoji(code) or category.startswith("M"):
                    continue
                if not category.startswith("L"):
                    self.dropped += 1
                    continue
                self.placeholders[script_name(ch)] += 1
                ch = PLACEHOLDER
                family = _find_family(ch, base, current)
                if family is None:
                    continue
            if runs and runs[-1][0] == family:
                runs[-1][1].append(ch)
            else:
                runs.append((family, [ch]))
            current = family
        return [(family, "".join(chars)) for family, chars in runs]


def _next_to_emoji(text: str, position: int) -> bool:
    before = ord(text[position - 1]) if position > 0 else 0
    after = ord(text[position + 1]) if position + 1 < len(text) else 0
    return _maybe_emoji(before) or _maybe_emoji(after)
