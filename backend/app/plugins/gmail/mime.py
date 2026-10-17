"""MIME for the Gmail plugin: Gmail's `format=full` resources read into text, and the emails it drafts and sends
(docs/briefs/plugins-gmail.md, section 9).

Parsing, HTML -> text, building and hashing are CPU-bound: callers run them through asyncio.to_thread.
"""

import base64
import binascii
import codecs
import functools
import hashlib
import html
import json
import mimetypes
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import errors as email_errors
from email import policy
from email.header import decode_header, make_header
from email.headerregistry import Address as HeaderAddress
from email.message import EmailMessage, Message
from email.utils import format_datetime, getaddresses, parsedate_to_datetime
from pathlib import PurePosixPath
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.plugins.gmail.schemas import EmailAddress
from app.services.parsing import LEGACY_EXTENSIONS, SUPPORTED_EXTENSIONS, html_to_text

MAX_PART_CHARS = 2_000_000  # a text/plain or text/html part is cut to this before parsing
MAX_MESSAGE_BYTES = 25 * 1024 * 1024  # Gmail's limit for a built message, encoding included
MAX_REPLY_SUBJECT = 1000
MAX_SUBJECT = 250
MAX_BODY = 50_000
MAX_RECIPIENTS = 20
MAX_REFERENCES = 4000
TOO_LARGE_PART = "[part too large to read]"
_MAX_NAME = 100
_MAX_DEPTH = 20
_MAX_PARTS = 500
_MAX_MESSAGE_ID = 900
_UNSUPPORTED = (
    "Only PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), HTML, Markdown, text, CSV and JSON files can be "
    "imported."
)
_TOO_LARGE = "This email is too large for Gmail (25 MB including encoding). Attach fewer or smaller files."
_LONG_REPLY_SUBJECT = (
    "The original email's subject is too long to reply in its conversation. Write a new email instead."
)
_ENCODED_SUBJECT = "The subject can't contain MIME encoded words (=?…?=). Write it as plain text."
_ENCODED_REPLY_SUBJECT = (
    "The original email's subject contains MIME encoded words (=?…?=), so it can't be replied to in its conversation. "
    "Write a new email instead."
)

# Controls except tab and newline, Unicode tag characters, bidi embeddings/overrides/isolates and zero-width
# characters that hide text (ZWJ and ZWNJ stay: Indian scripts need them).
_INVISIBLE: dict[int, str | None] = {
    code: None
    for code in (
        *range(0x00, 0x09),
        *range(0x0E, 0x20),
        0x7F,
        *range(0xE0000, 0xE0080),
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
        0x200B,
        0x2060,
        0xFEFF,
    )
} | {0x0B: "\n", 0x0C: "\n", 0x2028: "\n", 0x2029: "\n"}
# Outgoing text (outgoing_text) also loses the rest of what a reader can't see (Unicode's default-ignorable code
# points): format characters other than those below, the combining grapheme joiner, Khmer's invisible vowels, the
# variation selectors other than text/emoji presentation, and the unassigned ignorable ranges.
_KEPT_FORMAT = frozenset(
    {0x200C, 0x200D, 0x200E, 0x200F, 0x061C}  # joiners and directional marks: kept where they can matter (below)
    | {*range(0x0600, 0x0606), 0x06DD, 0x070F, 0x0890, 0x0891, 0x08E2, 0x110BD, 0x110CD}  # visible number marks
)
_IGNORABLE = frozenset({0x00AD, 0x034F, 0x17B4, 0x17B5, 0x2065, *range(0xFE00, 0xFE0E), *range(0xFFF0, 0xFFF9)})
_IGNORABLE_PLANE = range(0xE0000, 0xE1000)  # tags, supplementary variation selectors, unassigned
_BLANK_LETTERS = {code: " " for code in (0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800)}  # Hangul fillers, blank Braille
_JOINERS = frozenset("\u200c\u200d")
_MARKS = frozenset("\u200e\u200f\u061c")
_SELECTORS = frozenset("\ufe0e\ufe0f\u180b\u180c\u180d\u180f")  # presentation and Mongolian variation selectors
_ATTACHING = _JOINERS | _MARKS | _SELECTORS
_PRESENTATION = frozenset("\ufe0e\ufe0f")  # text and emoji presentation
_KEYCAP_BASES = frozenset("0123456789#*")
_RIGHT_TO_LEFT = frozenset({"R", "AL"})
_BLANK_LINES = re.compile(r"\n{3,}")
# Single-byte labels read as the Windows code page WHATWG maps them to (codecs.lookup names): bytes 0x80-0x9F are
# that code page's characters ("€", typographic quotes), invisible controls in the ISO charsets. A byte the code page
# leaves undefined reads as the labelled charset has it, else U+FFFD.
_WINDOWS_LABELS = {"iso8859-1": "cp1252", "ascii": "cp1252", "iso8859-9": "cp1254", "tis-620": "cp874"}
_AS_WINDOWS = {
    label: {
        code: bytes([code]).decode(codepage, "ignore")
        or bytes([code]).decode("latin-1" if label == "ascii" else label, "ignore")
        or "\ufffd"
        for code in range(0x80, 0x100)
    }
    for label, codepage in _WINDOWS_LABELS.items()
}
# Labels Python doesn't know, and the supersets mail clients send under the narrower label (WHATWG reads the label
# as the superset): GBK under "gb2312", Windows-949 under "ks_c_5601-1987", Windows-31J under "shift_jis", HKSCS
# under "big5".
_LABEL_ALIASES = {"x-gbk": "gbk", "x-sjis": "shift_jis", "x-euc-jp": "euc_jp", "windows-874": "cp874"}
_SUPERSETS = {"gb2312": "gb18030", "gbk": "gb18030", "euc_kr": "cp949", "shift_jis": "cp932", "big5": "big5hkscs"}
_ISO2022_JP_ESCAPE = re.compile(rb"(\x1b\(B|\x1b\(J|\x1b\(I|\x1b\$@|\x1b\$B)")
_ISO2022_JP_PAIR = re.compile(rb"[\x21-\x7e]{2}")
_JIS_ROMAN = {0x5C: "\u00a5", 0x7E: "\u203e"}  # ESC ( J: ASCII but for the yen sign and the overline
_PROMPT_TAGS = re.compile(r"<(\s*/?\s*(?:email|context))", re.IGNORECASE)
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)(?:display:none|visibility:hidden|mso-hide:all|(?:opacity|font-size|max-height):(?:0+(?:\.0*)?|\.0+)"
    r"(?:px|pt|em|rem|%)?)(?:!important)?(?=;|$)"
)
_DROPPED_TAGS = ["head", "script", "style", "noscript", "template", "svg", "title"]
_MENTIONED = re.compile(r"(?<![\w.%+-])[\w.%+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")
_LOCAL_PART = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}")
_ASCII_DOMAIN = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_REPLY_PREFIX = re.compile(r"re\s*:", re.IGNORECASE)
# An RFC 2047 encoded word. Python's header parser decodes these in the values it is given, so text containing one
# would be sent as something else than the card showed ("=?utf-8?q?bob=40company.com?=" becomes "bob@company.com").
_ENCODED_WORD = re.compile(r"=\?[^?\s]*\?[bq]\?[^?\s]*\?=", re.IGNORECASE)
_MIME_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}")  # the sender wrote it


class EmailInputError(Exception):
    """An email that can't be built as asked. str(exc) is for the model or the user."""


# ---------------- Text ----------------


def clean_text(text: str) -> str:
    """Control, invisible and bidi characters removed (tab and newline kept), lines right-trimmed, at most one blank
    line in a row."""
    text = text.encode("utf-8", "replace").decode("utf-8")  # lone surrogates
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def clean_line(text: str) -> str:
    return " ".join(clean_text(text).split())


def outgoing_text(text: str) -> str:
    """Text of an email the app drafts or sends, with nothing the card can't show: lone surrogates replaced, line
    endings normalized, clean_text's invisible characters and every other default-ignorable one removed, blank letters
    as spaces. A variation selector stays only right after a visible non-ASCII character (text/emoji presentation also
    after a keycap's 0-9, # or *), ZWJ and ZWNJ only between two non-ASCII characters, directional marks only in text
    with right-to-left letters, and none of them twice in a row: no run can carry hidden text."""
    text = _normalized(text).translate(_INVISIBLE)
    if text.isascii():  # no format characters, joiners, marks or selectors
        return text
    kept = [character for character in text.translate(_BLANK_LETTERS) if not _ignorable(character)]
    right_to_left = any(
        unicodedata.bidirectional(character) in _RIGHT_TO_LEFT for character in kept if character not in _MARKS
    )
    out: list[str] = []
    for index, character in enumerate(kept):
        before = out[-1] if out else "\n"
        if character in _SELECTORS:
            presentation = character in _PRESENTATION and before in _KEYCAP_BASES
            if _visible(before) and (not before.isascii() or presentation):
                out.append(character)
        elif character in _JOINERS:
            after = kept[index + 1] if index + 1 < len(kept) else "\n"
            joins = before in _SELECTORS or (_visible(before) and not before.isascii())
            if joins and _visible(after) and not after.isascii():
                out.append(character)
        elif character in _MARKS:
            if right_to_left and before not in _MARKS:
                out.append(character)
        else:
            out.append(character)
    return "".join(out)


def outgoing_line(text: str) -> str:
    return " ".join(outgoing_text(text).split())


def hides_text(text: str) -> bool:
    """Whether outgoing_text would remove or replace any character of `text` (line endings aside)."""
    return outgoing_text(text) != _normalized(text)


def _normalized(text: str) -> str:
    return text.encode("utf-8", "replace").decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _ignorable(character: str) -> bool:
    code = ord(character)
    if code in _IGNORABLE or code in _IGNORABLE_PLANE:
        return True
    return code not in _KEPT_FORMAT and unicodedata.category(character) == "Cf"


def _visible(character: str) -> bool:
    """A character a joiner or variation selector can attach to: not whitespace, a joiner, a mark or a selector."""
    return not character.isspace() and character not in _ATTACHING


def defuse(text: str) -> str:
    """Neutralize <email> and <context> tags in untrusted text, so it can't close or open a prompt block."""
    return _PROMPT_TAGS.sub(r"[\1", text)


def capped(text: str, limit: int) -> tuple[str, bool]:
    """At most `limit` characters, cut at a line or word boundary near the end, with "…" appended when cut."""
    if len(text) <= limit:
        return text, False
    if limit <= 1:
        return "…"[:limit], True
    cut = text[: limit - 1]
    floor = (limit - 1) * 4 // 5
    for separator in ("\n", " "):
        at = cut.rfind(separator)
        if at >= floor:
            cut = cut[:at]
            break
    return cut.rstrip() + "…", True


def decode_header_value(value: str) -> str:
    """A header value with RFC 2047 words decoded, on one line."""
    try:
        decoded = str(make_header(decode_header(value)))
    except Exception:
        decoded = value
    return clean_line(decoded)


def html_text(markup: str) -> str:
    """Visible text of an HTML body: hidden elements (hidden, aria-hidden, inline styles that hide) are dropped."""
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup.find_all(_DROPPED_TAGS):
        if not tag.decomposed:
            tag.decompose()
    for tag in soup.find_all(_is_hidden):
        if not tag.decomposed:
            tag.decompose()
    return clean_text(html_to_text(soup))


def _is_hidden(tag: Tag) -> bool:
    if tag.has_attr("hidden"):
        return True
    aria = tag.get("aria-hidden")
    if isinstance(aria, str) and aria.strip().lower() == "true":
        return True
    style = tag.get("style")
    return isinstance(style, str) and _HIDDEN_STYLE.search(re.sub(r"\s+", "", style.lower())) is not None


def decode_bytes(data: bytes, declared: str | None) -> str:
    """Text of a body part, read as mail clients and browsers (WHATWG) read it:
    - bytes declared as another charset that are valid UTF-8 with non-ASCII letters are UTF-8 (Gmail may already have
      converted them);
    - Latin-1 and ASCII labels read as Windows-1252, ISO-8859-9 as Windows-1254 and TIS-620 as Windows-874 ("€" and
      typographic quotes are 0x80-0x9F there, invisible controls in the ISO charsets);
    - the declared charset, then its superset (GBK and GB18030 under "gb2312", Windows-949 under "ks_c_5601-1987" and
      "euc-kr", Windows-31J under "shift_jis", HKSCS under "big5"); ISO-2022-JP with the NEC and IBM extensions (①,
      ㈱, 髙) through Windows-31J;
    - bytes the declared family can't read become U+FFFD when that leaves the rest readable (at most 1 in 20);
    - otherwise (no or an unknown label, or mostly unreadable) UTF-8 with replacements, then cp1252, then Latin-1."""
    charset = _codec(declared) or "utf-8"
    if charset != "utf-8" and data and max(data) >= 0x80:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    if charset in _AS_WINDOWS:
        return data.decode("latin-1").translate(_AS_WINDOWS[charset])
    superset = _SUPERSETS.get(charset)
    for codec in (charset, superset):
        if codec is not None:
            try:
                return data.decode(codec)
            except (UnicodeDecodeError, LookupError, ValueError):
                pass
    if charset == "iso2022_jp":  # 7-bit: the fallbacks below could only show its escape sequences
        return _iso2022_jp(data)
    if charset != "utf-8":
        try:
            text = data.decode(superset or charset, "replace")
        except (LookupError, ValueError):
            pass
        else:
            if text.count("�") * 20 <= len(text):
                return text
    text = data.decode("utf-8", "replace")
    if text.count("�") * 20 <= len(text):
        return text
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _codec(name: str | None) -> str | None:
    if not name:
        return None
    label = name.strip()
    try:
        return codecs.lookup(_LABEL_ALIASES.get(label.lower(), label)).name
    except (LookupError, ValueError):
        return None


def _iso2022_jp(data: bytes) -> str:
    """ISO-2022-JP as the WHATWG decoder reads it: JIS X 0208 pairs as Python's iso2022_jp maps them, the pairs it
    lacks (NEC row 13 and the IBM rows) through Windows-31J, half-width katakana (ESC ( I) too; anything else is
    U+FFFD."""
    out: list[str] = []
    mode = b"\x1b(B"
    for index, part in enumerate(_ISO2022_JP_ESCAPE.split(data)):
        if index % 2:
            mode = part
        elif mode in (b"\x1b$B", b"\x1b$@"):
            out.append(_ISO2022_JP_PAIR.sub(lambda pair: _jis_table()[pair.group()], part).decode("utf-8", "replace"))
        elif mode == b"\x1b(I":
            out.append("".join(_katakana(byte) for byte in part))
        elif mode == b"\x1b(J":
            out.append(part.decode("ascii", "replace").translate(_JIS_ROMAN))
        else:
            out.append(part.decode("ascii", "replace"))
    return "".join(out)


def _katakana(byte: int) -> str:
    """A byte of ISO-2022-JP's half-width katakana mode (ESC ( I)."""
    if 0x21 <= byte <= 0x5F:
        return chr(0xFF61 + byte - 0x21)
    return chr(byte) if byte < 0x80 else "\ufffd"


@functools.cache
def _jis_table() -> dict[bytes, bytes]:
    """Each JIS X 0208 byte pair -> its character in UTF-8."""
    table: dict[bytes, bytes] = {}
    for lead in range(0x21, 0x7F):
        for trail in range(0x21, 0x7F):
            try:
                character = bytes([lead | 0x80, trail | 0x80]).decode("euc_jp")
            except UnicodeDecodeError:
                sjis_lead = (lead + 1) // 2 + (0x70 if lead <= 0x5E else 0xB0)
                sjis_trail = trail + (0x7E if lead % 2 == 0 else 0x1F if trail < 0x60 else 0x20)
                character = bytes([sjis_lead, sjis_trail]).decode("cp932", "replace")
            table[bytes([lead, trail])] = character.encode("utf-8")
    return table


def b64url_decode(data: str) -> bytes:
    """Gmail's base64url, possibly without padding; b"" when it isn't base64."""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return b""


# ---------------- Addresses ----------------


@dataclass(frozen=True)
class Address:
    name: str | None  # display name; None when absent or dropped because it contained "@"
    address: str


def _display_name(name: str) -> str | None:
    """A display name without control or other invisible characters (outgoing_text), at most 100 characters; None when
    empty, when it contains "@" or a look-alike of it (only a spoof needs an address in the name), or when it contains
    an encoded word (it would be sent decoded, as something else than shown)."""
    cleaned = outgoing_line(name)[:_MAX_NAME].strip()
    if not cleaned or "@" in unicodedata.normalize("NFKC", cleaned) or _ENCODED_WORD.search(cleaned):
        return None
    return cleaned


def header_addresses(value: str | None) -> list[Address]:
    """The addresses of a received header (lenient: received mail isn't validated), names decoded."""
    if not value:
        return []
    pairs = getaddresses([value])
    if not any(address for _, address in pairs):
        pairs = getaddresses([value], strict=False)
    found = []
    for name, address in pairs:
        address = clean_line(address)
        if address:
            found.append(Address(_display_name(decode_header_value(name)) if name else None, address))
    return found


def format_address(address: Address) -> str:
    return f"{address.name} <{address.address}>" if address.name else address.address


def format_addresses(addresses: Iterable[Address]) -> str:
    return ", ".join(format_address(address) for address in addresses)


def _ascii_domain(domain: str) -> str | None:
    try:
        return domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def address_key(address: str) -> str:
    """The comparison key of an address: the case-folded local part and the IDNA-encoded, lowercased domain."""
    local, _, domain = address.rpartition("@")
    return f"{local.casefold()}@{_ascii_domain(domain) or domain.casefold()}"


def mentioned_addresses(texts: Iterable[str]) -> set[str]:
    """Keys of the addresses written in free text (word-bounded, Unicode domains included). Compare exact keys, never
    substrings: "alice@company.com" doesn't cover "alice@company.co"."""
    return {address_key(found.group().rstrip(".")) for text in texts for found in _MENTIONED.finditer(text)}


def unicode_domain(address: str) -> str | None:
    """The Unicode form of an internationalized ("xn--") domain, else None."""
    domain = address.rpartition("@")[2]
    if not any(label.startswith("xn--") for label in domain.lower().split(".")):
        return None
    try:
        return domain.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return None


def view_address(address: Address) -> EmailAddress:
    return EmailAddress(address=address.address, name=address.name, unicode_domain=unicode_domain(address.address))


def parse_addresses(values: Sequence[str]) -> list[Address]:
    """Addresses typed by the model or the user ("Name <address>" or "address", several per entry allowed), validated:
    ASCII local part, IDNA-encoded domain, at most 254 characters. Blank entries are ignored."""
    parsed: list[Address] = []
    for value in values:
        if not value.strip():
            continue
        shown = " ".join(value.split())[:80]
        invalid = EmailInputError(f"Not a valid email address: {shown}")
        if any(character in value for character in "\r\n\x00"):
            raise invalid
        pairs = getaddresses([value])
        if not pairs or any(not address for _, address in pairs):
            raise invalid
        for name, address in pairs:
            local, at, domain = address.strip().rpartition("@")
            ascii_domain = _ascii_domain(domain) if at else None
            if not _LOCAL_PART.fullmatch(local) or ascii_domain is None or not _ASCII_DOMAIN.fullmatch(ascii_domain):
                raise invalid
            normalized = f"{local}@{ascii_domain}"
            if len(normalized) > 254:
                raise invalid
            parsed.append(Address(_display_name(name), normalized))
    return parsed


def normalize_recipients(
    to: Sequence[Address], cc: Sequence[Address], bcc: Sequence[Address]
) -> tuple[list[Address], list[Address], list[Address]]:
    """Duplicates (by address_key) removed across To, Cc and Bcc, the first kept; at least one To; at most 20."""
    seen: set[str] = set()

    def unique(addresses: Sequence[Address]) -> list[Address]:
        kept = []
        for address in addresses:
            key = address_key(address.address)
            if key not in seen:
                seen.add(key)
                kept.append(address)
        return kept

    lists = unique(to), unique(cc), unique(bcc)
    if not lists[0]:
        raise EmailInputError("Add at least one To address.")
    if sum(len(addresses) for addresses in lists) > MAX_RECIPIENTS:
        raise EmailInputError(f"An email can have at most {MAX_RECIPIENTS} recipients.")
    return lists


# ---------------- Subject and body ----------------


def new_subject(subject: str | None) -> str:
    """The subject of a new email: required, one line, at most 250 characters."""
    value = (subject or "").strip()
    if not value:
        raise EmailInputError("A new email needs a subject.")
    if "\r" in value or "\n" in value:
        raise EmailInputError("The subject must be one line.")
    value = outgoing_line(value)
    if not value:
        raise EmailInputError("A new email needs a subject.")
    if len(value) > MAX_SUBJECT:
        raise EmailInputError(f"The subject is longer than {MAX_SUBJECT} characters.")
    if _ENCODED_WORD.search(value):
        raise EmailInputError(_ENCODED_SUBJECT)
    return value


def reply_subject(original: str) -> str:
    """"Re: " + the original's subject unless it already starts with "Re:". Never cut: Gmail threads by subject."""
    value = clean_line(original)
    subject = value if _REPLY_PREFIX.match(value) else f"Re: {value}".rstrip()
    if len(subject) > MAX_REPLY_SUBJECT:
        raise EmailInputError(_LONG_REPLY_SUBJECT)
    if _ENCODED_WORD.search(subject):
        raise EmailInputError(_ENCODED_REPLY_SUBJECT)
    return subject


def clean_body(body: str) -> str:
    """The plain-text body as the card shows it and Gmail sends it: outgoing_text, and at most one blank (or
    whitespace-only) line in a row, so no run of empty lines can push text below the card's preview. Other whitespace
    stays (indentation, the "-- " signature line); required, at most 50,000 characters."""
    value = "\n".join("" if line.isspace() else line for line in outgoing_text(body).split("\n"))
    value = _BLANK_LINES.sub("\n\n", value)
    if not value.strip():
        raise EmailInputError("The email needs a body.")
    if len(value) > MAX_BODY:
        raise EmailInputError(f"The body is longer than {MAX_BODY:,} characters.")
    return value


# ---------------- Reading Gmail resources ----------------


@dataclass(frozen=True)
class AttachmentInfo:
    part_id: str
    filename: str
    mime_type: str
    size_bytes: int
    importable: bool
    reason: str | None
    attachment_id: str | None = None  # valid for downloads, but Gmail gives a new one on every fetch
    data: bytes | None = None  # a small attachment Gmail sent inline


@dataclass(frozen=True)
class ParsedMessage:
    message_id: str
    thread_id: str
    labels: tuple[str, ...]
    sender: Address | None
    to: list[Address]
    cc: list[Address]
    reply_to: list[Address]
    subject: str
    date: datetime | None  # internalDate in UTC; the Date header only as a fallback
    snippet: str
    message_id_header: str | None
    references: str | None
    body: str  # cleaned text, not yet capped
    truncated: bool  # a part was cut before parsing
    attachments: list[AttachmentInfo]

    @property
    def sender_text(self) -> str:
        return format_address(self.sender) if self.sender else ""

    @property
    def participants(self) -> set[str]:
        """Keys of From, Reply-To, To and Cc: who a reply may address without being flagged."""
        people = [*([self.sender] if self.sender else []), *self.reply_to, *self.to, *self.cc]
        return {address_key(person.address) for person in people}


@dataclass(frozen=True)
class ParsedThread:
    thread_id: str
    messages: list[ParsedMessage]  # oldest first
    total: int  # messages in the thread (more than `messages` when only the newest were read)


@dataclass
class _Walk:
    large_bodies: Mapping[str, bytes]
    parts: int = 0
    truncated: bool = False


@dataclass
class _Count:
    parts: int = 0
    found: list[AttachmentInfo] = field(default_factory=list)


def parse_message(resource: Mapping[str, Any], large_bodies: Mapping[str, bytes] | None = None) -> ParsedMessage:
    """A Gmail message resource (`format=full` or `metadata`) as text. `large_bodies`: the bytes of body parts Gmail
    holds as attachments (by part id, see large_body_parts); without them such a part reads TOO_LARGE_PART."""
    payload = resource.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    headers = _headers(payload)
    walk = _Walk(large_bodies or {})
    body = clean_text("\n\n".join(text for text in _body(payload, 0, walk) if text))
    count = _Count()
    _attachments(payload, 0, count)
    senders = header_addresses(headers.get("from"))
    labels = resource.get("labelIds")
    return ParsedMessage(
        message_id=str(resource.get("id") or ""),
        thread_id=str(resource.get("threadId") or ""),
        labels=tuple(label for label in labels if isinstance(label, str)) if isinstance(labels, list) else (),
        sender=senders[0] if senders else None,
        to=header_addresses(headers.get("to")),
        cc=header_addresses(headers.get("cc")),
        reply_to=header_addresses(headers.get("reply-to")),
        subject=decode_header_value(headers.get("subject", "")),
        date=_internal_date(resource.get("internalDate")) or _header_date(headers.get("date")),
        snippet=clean_line(html.unescape(str(resource.get("snippet") or ""))),
        message_id_header=clean_line(headers.get("message-id", "")) or None,
        references=clean_line(headers.get("references", "")) or None,
        body=body,
        truncated=walk.truncated,
        attachments=count.found,
    )


def large_body_parts(resource: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(part id, attachment id) of the text body parts Gmail holds as attachments (large bodies), to fetch for an
    import."""
    found: list[tuple[str, str]] = []

    def visit(part: Mapping[str, Any], depth: int) -> None:
        if depth > _MAX_DEPTH or len(found) >= _MAX_PARTS:
            return
        body = _body_of(part)
        mime = _mime(part)
        if mime in ("text/plain", "text/html") and not _is_attachment(part, _headers(part)):
            if not body.get("data") and isinstance(body.get("attachmentId"), str):
                found.append((str(part.get("partId") or ""), body["attachmentId"]))
        for child in _children(part):
            visit(child, depth + 1)

    payload = resource.get("payload")
    if isinstance(payload, dict):
        visit(payload, 0)
    return found


def list_attachments(resource: Mapping[str, Any], *, listed_only: bool = True) -> list[AttachmentInfo]:
    """A message's attachments without reading its body."""
    payload = resource.get("payload")
    count = _Count()
    if isinstance(payload, dict):
        _attachments(payload, 0, count, listed_only=listed_only)
    return count.found


def find_part(resource: Mapping[str, Any], part_id: str) -> AttachmentInfo | None:
    """The attachment with this part id in a fresh `format=full` resource."""
    return next((info for info in list_attachments(resource, listed_only=False) if info.part_id == part_id), None)


def _children(part: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    parts = part.get("parts")
    return [child for child in parts if isinstance(child, dict)] if isinstance(parts, list) else []


def _headers(part: Mapping[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    headers = part.get("headers")
    for header in headers if isinstance(headers, list) else []:
        if isinstance(header, dict) and isinstance(header.get("name"), str) and isinstance(header.get("value"), str):
            found.setdefault(header["name"].lower(), header["value"])
    return found


def _body_of(part: Mapping[str, Any]) -> dict[str, Any]:
    body = part.get("body")
    return body if isinstance(body, dict) else {}


def _mime(part: Mapping[str, Any]) -> str:
    return str(part.get("mimeType") or "").strip().lower()


def _is_attachment(part: Mapping[str, Any], headers: Mapping[str, str]) -> bool:
    return bool(part.get("filename")) or headers.get("content-disposition", "").strip().lower().startswith("attachment")


def _body(part: Mapping[str, Any], depth: int, walk: _Walk) -> list[str]:
    if depth > _MAX_DEPTH or walk.parts >= _MAX_PARTS:
        return []
    walk.parts += 1
    mime = _mime(part)
    headers = _headers(part)
    if mime == "message/rfc822" and _children(part):
        return [_forwarded(part, depth, walk)]
    if _is_attachment(part, headers):
        return []
    if mime.startswith("multipart/"):
        children = _children(part)
        if mime == "multipart/alternative":
            return _alternative(children, depth, walk)
        return [text for child in children for text in _body(child, depth + 1, walk)]
    if mime in ("text/plain", "text/html"):
        text = _text(part, headers, mime, walk)
        return [text] if text else []
    return []


def _alternative(children: list[Mapping[str, Any]], depth: int, walk: _Walk) -> list[str]:
    """text/plain unless it is empty or under 20 visible characters while an HTML alternative exists."""
    plain = next((child for child in children if _mime(child) == "text/plain" and not child.get("filename")), None)
    richer = [child for child in children if child is not plain and _has_html(child, depth + 1)]
    if plain is not None:
        text = "\n\n".join(_body(plain, depth + 1, walk))
        if len("".join(text.split())) >= 20 or not richer:
            return [text] if text else []
    if richer:
        return _body(richer[-1], depth + 1, walk)
    for child in reversed(children):
        if texts := _body(child, depth + 1, walk):
            return texts
    return []


def _has_html(part: Mapping[str, Any], depth: int) -> bool:
    if depth > _MAX_DEPTH:
        return False
    return _mime(part) == "text/html" or any(_has_html(child, depth + 1) for child in _children(part))


def _text(part: Mapping[str, Any], headers: Mapping[str, str], mime: str, walk: _Walk) -> str:
    body = _body_of(part)
    if isinstance(body.get("data"), str) and body["data"]:
        raw = b64url_decode(body["data"])
    elif body.get("attachmentId"):
        fetched = walk.large_bodies.get(str(part.get("partId") or ""))
        if fetched is None:
            return TOO_LARGE_PART
        raw = fetched
    else:
        return ""
    if len(raw) > MAX_PART_CHARS * 4:  # no character takes more than 4 bytes
        raw = raw[: MAX_PART_CHARS * 4]
        walk.truncated = True
    text = decode_bytes(raw, _declared_charset(headers))
    if len(text) > MAX_PART_CHARS:
        text = text[:MAX_PART_CHARS]
        walk.truncated = True
    return html_text(text) if mime == "text/html" else clean_text(text)


def _declared_charset(headers: Mapping[str, str]) -> str | None:
    value = headers.get("content-type")
    if not value:
        return None
    message = Message()
    message["Content-Type"] = value
    return message.get_content_charset()


def _forwarded(part: Mapping[str, Any], depth: int, walk: _Walk) -> str:
    children = _children(part)
    inner = children[0] if len(children) == 1 else {"mimeType": "multipart/mixed", "parts": children}
    headers = {**_headers(part), **_headers(inner)}
    lines = ["---------- Forwarded message ----------"]
    if sender := format_addresses(header_addresses(headers.get("from"))):
        lines.append(f"From: {sender}")
    for name in ("Date", "Subject"):
        if value := decode_header_value(headers.get(name.lower(), "")):
            lines.append(f"{name}: {value}")
    body = "\n\n".join(text for text in _body(inner, depth + 1, walk) if text)
    return "\n".join(lines) + (f"\n\n{body}" if body else "")


def _attachments(part: Mapping[str, Any], depth: int, count: _Count, *, listed_only: bool = True) -> None:
    if depth > _MAX_DEPTH or count.parts >= _MAX_PARTS:
        return
    count.parts += 1
    headers = _headers(part)
    mime = _mime(part)
    if _is_attachment(part, headers) and not (mime == "message/rfc822" and _children(part)):
        info = _attachment_info(part, headers, mime)
        disposition = headers.get("content-disposition", "").strip().lower()
        inline_image = disposition.startswith("inline") and "content-id" in headers
        if not listed_only or not inline_image or info.importable:
            count.found.append(info)
        return
    for child in _children(part):
        _attachments(child, depth + 1, count, listed_only=listed_only)


def _attachment_info(part: Mapping[str, Any], headers: Mapping[str, str], mime: str) -> AttachmentInfo:
    body = _body_of(part)
    filename = decode_header_value(str(part.get("filename") or ""))
    extension = PurePosixPath(filename).suffix.lower() if filename else ""
    data = b64url_decode(body["data"]) if isinstance(body.get("data"), str) and body["data"] else None
    size = body.get("size")
    size_bytes = size if isinstance(size, int) and size >= 0 else len(data or b"")
    if not filename:
        reason: str | None = "It has no file name."
    elif extension in LEGACY_EXTENSIONS:
        reason = f"Legacy {extension} files can't be imported. Save it as {LEGACY_EXTENSIONS[extension]} first."
    elif extension not in SUPPORTED_EXTENSIONS:
        reason = _UNSUPPORTED
    elif size_bytes <= 0:
        reason = "The file is empty."
    else:
        reason = None
    attachment_id = body.get("attachmentId")
    return AttachmentInfo(
        part_id=str(part.get("partId") or ""),
        filename=filename,
        mime_type=mime if _MIME_TYPE.fullmatch(mime) else "application/octet-stream",
        size_bytes=size_bytes,
        importable=reason is None,
        reason=reason,
        attachment_id=attachment_id if isinstance(attachment_id, str) else None,
        data=data,
    )


def _internal_date(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _header_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def reply_headers(original: ParsedMessage) -> tuple[str | None, str | None]:
    """In-Reply-To and References of a reply to `original` (References at most 4,000 characters, the oldest ids
    dropped first)."""
    message_id = original.message_id_header
    if not message_id or len(message_id) > _MAX_MESSAGE_ID:
        return None, None
    ids = [*(original.references or "").split(), message_id]
    while len(ids) > 1 and len(" ".join(ids)) > MAX_REFERENCES:
        ids.pop(0)
    return message_id, " ".join(ids)


# ---------------- Building ----------------


@dataclass(frozen=True)
class OutgoingAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class OutgoingEmail:
    account_email: str
    to: list[Address]
    cc: list[Address]
    bcc: list[Address]
    subject: str
    body: str
    attachments: list[OutgoingAttachment]
    in_reply_to: str | None
    references: str | None


def content_type_for(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename, strict=False)
    return guessed or "application/octet-stream"


def _header_address(address: Address) -> HeaderAddress:
    return HeaderAddress(display_name=address.name or "", addr_spec=address.address)


def build_mime(email: OutgoingEmail, now: datetime) -> bytes:
    """The RFC 5322 bytes Gmail stores or sends: From/To/Cc/Bcc exactly as shown on the card (Gmail strips Bcc from
    what recipients get), a plain-text body, attachments. No Message-ID: Gmail adds it."""
    message = EmailMessage(policy=policy.SMTP)
    try:
        message["From"] = HeaderAddress(addr_spec=email.account_email)
        message["To"] = [_header_address(address) for address in email.to]
        if email.cc:
            message["Cc"] = [_header_address(address) for address in email.cc]
        if email.bcc:
            message["Bcc"] = [_header_address(address) for address in email.bcc]
        message["Subject"] = email.subject
        message["Date"] = format_datetime(now)
        if email.in_reply_to:
            message["In-Reply-To"] = email.in_reply_to
        if email.references:
            message["References"] = email.references
    except (ValueError, TypeError, IndexError, email_errors.MessageError) as exc:
        raise EmailInputError("The email's headers aren't valid.") from exc
    body = email.body.replace("\r\n", "\n").replace("\r", "\n")
    plain_ascii = body.isascii() and all(len(line) <= 998 for line in body.split("\n"))
    message.set_content(body, subtype="plain", charset="utf-8", cte="7bit" if plain_ascii else "quoted-printable")
    for attachment in email.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        message.add_attachment(
            attachment.data, maintype=maintype or "application", subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    data = message.as_bytes()
    if len(data) > MAX_MESSAGE_BYTES:
        raise EmailInputError(_TOO_LARGE)
    return data


_HASHED_ATTACHMENT_KEYS = ("document_id", "filename", "sha256", "size_bytes", "content_type")


def content_hash(
    *,
    account: str,
    to: Sequence[Address],
    cc: Sequence[Address],
    bcc: Sequence[Address],
    subject: str,
    body: str,
    thread_id: str | None,
    in_reply_to: str | None,
    references: str | None,
    reply_to: Mapping[str, Any] | None,
    attachments: Sequence[Mapping[str, Any]],
) -> str:
    """sha256 of everything an email sends and shows (not the unmentioned/unrequested flags, which only describe)."""
    payload = {
        "v": 1,
        "account": account,
        "to": [[address.name, address.address] for address in to],
        "cc": [[address.name, address.address] for address in cc],
        "bcc": [[address.name, address.address] for address in bcc],
        "subject": subject,
        "body": body,
        "thread_id": thread_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "reply_to": dict(reply_to) if reply_to is not None else None,
        "attachments": [{key: attachment.get(key) for key in _HASHED_ATTACHMENT_KEYS} for attachment in attachments],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
