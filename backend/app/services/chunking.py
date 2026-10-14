"""Parsed segments -> overlapping chunks for embedding."""

import re
from dataclasses import dataclass

from app.services.parsing import Segment

# Tried in order; text with none of them is cut at fixed positions.
_SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")
_MIN_CONTENT_CHARS = 20
_WHITESPACE = re.compile(r"\s")


@dataclass
class Chunk:
    index: int
    text: str
    page: int | None


def chunk_segments(segments: list[Segment], chunk_size: int, overlap: int) -> list[Chunk]:
    """Chunks of at most `chunk_size` characters that never cross a segment (page) boundary.

    Consecutive chunks of a segment share up to `overlap` characters (at most half a chunk). Chunks with
    fewer than 20 non-whitespace characters are dropped unless that would leave nothing.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    overlap = max(0, min(overlap, chunk_size // 2))
    pieces = [(text, segment.page) for segment in segments for text in _chunk_text(segment.text, chunk_size, overlap)]
    substantial = [piece for piece in pieces if _content_chars(piece[0]) >= _MIN_CONTENT_CHARS]
    return [Chunk(index=index, text=text, page=page) for index, (text, page) in enumerate(substantial or pieces)]


def _content_chars(text: str) -> int:
    return len(text) - len(_WHITESPACE.findall(text))


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Greedily pack the split pieces; each new chunk starts with the tail of the previous one."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    fresh = False  # whether `current` holds anything beyond the carried-over tail
    for piece in _split(text, chunk_size, chunk_size - overlap, 0):
        if length and length + len(piece) > chunk_size:
            packed = "".join(current)
            if fresh:
                chunks.append(packed.strip())
            tail = _overlap_tail(packed, min(overlap, chunk_size - len(piece)))
            current, length, fresh = [tail], len(tail), False
        if not length:  # a chunk never starts with whitespace, so the next one can't contain all of it
            piece = piece.lstrip()
            if not piece:
                continue
        current.append(piece)
        length += len(piece)
        fresh = fresh or not piece.isspace()
    if fresh:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _split(text: str, limit: int, step: int, level: int) -> list[str]:
    """Pieces of at most `limit` characters that concatenate back to `text`.

    Uses the first separator (from `level` on) that occurs in the text and recurses into oversized parts
    with the next ones. Text without any separator is cut every `step` characters, which leaves room for
    the overlap. Recursion depth is bounded by the number of separators.
    """
    if len(text) <= limit:
        return [text]
    for position in range(level, len(_SEPARATORS)):
        separator = _SEPARATORS[position]
        if separator in text:
            parts = text.split(separator)
            pieces = [part + separator for part in parts[:-1]]
            if parts[-1]:
                pieces.append(parts[-1])
            return [small for piece in pieces for small in _split(piece, limit, step, position + 1)]
    return [text[start : start + step] for start in range(0, len(text), step)]


def _overlap_tail(text: str, size: int) -> str:
    """The last `size` characters of `text`, starting at a word boundary when the cut lands mid-word."""
    if size <= 0:
        return ""
    tail = text[-size:]
    if len(text) > size and not text[-size - 1].isspace() and (match := _WHITESPACE.search(tail)):
        tail = tail[match.start() :]
    return tail.lstrip()
