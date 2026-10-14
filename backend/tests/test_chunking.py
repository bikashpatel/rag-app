"""Unit tests for chunk_segments: sizes, overlap, page boundaries, filtering, determinism and termination."""

import random
import re
from itertools import pairwise

import pytest

from app.services.chunking import Chunk, chunk_segments
from app.services.parsing import Segment

# Every separator the chunker splits on, so words are never cut while they fit in a chunk.
_SEPARATORS = (" ", "\n", "\n\n", ". ", "? ", "! ", "; ", ", ")
_WORD = re.compile(r"word\d{4}x+")


def words(count: int, start: int = 0) -> list[str]:
    """Unique 24-character words: every chunk holding one passes the 20-character minimum."""
    return [f"word{number:04d}" + "x" * 16 for number in range(start, start + count)]


def join(items: list[str], separators: tuple[str, ...] = (" ",)) -> str:
    return "".join(word + separators[position % len(separators)] for position, word in enumerate(items)).strip()


def words_in(chunks: list[Chunk]) -> list[str]:
    """Words in reading order, each counted once even when an overlap repeats it."""
    seen: list[str] = []
    for chunk in chunks:
        for word in _WORD.findall(chunk.text):
            if word not in seen:
                seen.append(word)
    return seen


def shared_text(previous: str, following: str) -> str:
    """The longest start of `following` that `previous` ends with."""
    for size in range(min(len(previous), len(following)), 0, -1):
        if previous.endswith(following[:size]):
            return following[:size]
    return ""


def test_short_segment_is_one_stripped_chunk() -> None:
    chunks = chunk_segments([Segment("  The orchid greenhouse opens at nine.  \n", 3)], 1800, 250)

    assert chunks == [Chunk(index=0, text="The orchid greenhouse opens at nine.", page=3)]


@pytest.mark.parametrize(("chunk_size", "overlap"), [(60, 0), (60, 20), (100, 40), (250, 60), (500, 250), (1800, 250)])
def test_chunks_respect_the_size_and_keep_every_word_in_order(chunk_size: int, overlap: int) -> None:
    source = words(300)
    text = join(source, _SEPARATORS)

    chunks = chunk_segments([Segment(text, None)], chunk_size, overlap)

    assert len(chunks) > 1
    assert all(0 < len(chunk.text) <= chunk_size for chunk in chunks)
    assert all(chunk.text == chunk.text.strip() and chunk.text in text for chunk in chunks)
    assert words_in(chunks) == source


def test_consecutive_chunks_share_whole_words_up_to_the_overlap() -> None:
    text = join(words(120))

    chunks = chunk_segments([Segment(text, None)], 200, 60)

    assert len(chunks) > 5
    for previous, following in pairwise(chunks):
        shared = shared_text(previous.text, following.text)
        assert 0 < len(shared) <= 60
        assert _WORD.fullmatch(shared.split(" ")[0]), "the overlap starts at a word boundary"
        assert following.text != shared, "every chunk adds new text"


def test_overlap_is_clamped_to_half_a_chunk() -> None:
    text = join(words(100))

    chunks = chunk_segments([Segment(text, None)], 100, 10_000)

    assert all(len(chunk.text) <= 100 for chunk in chunks)
    assert all(len(shared_text(a.text, b.text)) <= 50 for a, b in pairwise(chunks))
    assert words_in(chunks) == words(100)
    assert len(chunks) <= 100


def test_zero_overlap_partitions_the_text() -> None:
    source = words(80)

    chunks = chunk_segments([Segment(join(source, _SEPARATORS), None)], 150, 0)

    found = [word for chunk in chunks for word in _WORD.findall(chunk.text)]
    assert found == source


def test_text_without_separators_is_cut_into_overlapping_windows() -> None:
    text = "".join(chr(0x4E00 + offset) for offset in range(100))

    chunks = chunk_segments([Segment(text, None)], 30, 10)

    assert all(len(chunk.text) <= 30 for chunk in chunks)
    assert all(len(shared_text(a.text, b.text)) == 10 for a, b in pairwise(chunks))
    assert chunks[0].text == text[: len(chunks[0].text)]
    assert chunks[-1].text == text[-len(chunks[-1].text) :]
    assert set("".join(chunk.text for chunk in chunks)) == set(text)


def test_chunks_never_cross_segment_boundaries_and_indices_run_across_pages() -> None:
    first, second, third = words(40), words(40, start=40), words(40, start=80)
    segments = [Segment(join(first), 1), Segment(join(second), 2), Segment(join(third), None)]

    chunks = chunk_segments(segments, 300, 80)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    pages = [chunk.page for chunk in chunks]
    assert pages == sorted(pages, key=lambda page: 3 if page is None else page)
    assert {1, 2, None} == set(pages)
    for chunk in chunks:
        expected = {1: first, 2: second, None: third}[chunk.page]
        assert set(_WORD.findall(chunk.text)) <= set(expected)
    assert words_in(chunks) == first + second + third


def test_chunks_with_fewer_than_20_visible_characters_are_dropped() -> None:
    segments = [
        Segment("abcdefghij klmnopqrs", 1),  # 19 non-whitespace characters
        Segment("abcdefghij klmnopqrst", 2),  # 20
        Segment("Contents", 3),
        Segment("A full sentence that is clearly long enough to keep.", 4),
    ]

    chunks = chunk_segments(segments, 1800, 250)

    assert chunks == [
        Chunk(index=0, text="abcdefghij klmnopqrst", page=2),
        Chunk(index=1, text="A full sentence that is clearly long enough to keep.", page=4),
    ]


def test_small_chunks_are_kept_when_nothing_else_remains() -> None:
    chunks = chunk_segments([Segment("Hi.", 1), Segment("  Ok  ", 2)], 1800, 250)

    assert chunks == [Chunk(index=0, text="Hi.", page=1), Chunk(index=1, text="Ok", page=2)]


def test_blank_input_produces_no_chunks() -> None:
    assert chunk_segments([], 1800, 250) == []
    assert chunk_segments([Segment(" \n\t \n\n ", 1)], 1800, 250) == []


@pytest.mark.parametrize("chunk_size", [0, -5])
def test_chunk_size_must_be_positive(chunk_size: int) -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        chunk_segments([Segment("Some text that would be chunked.", None)], chunk_size, 0)


def test_chunking_is_deterministic() -> None:
    text = join(words(200), _SEPARATORS) + "\n\n" + "z" * 5000
    segments = [Segment(text, 1), Segment(join(words(50)), 2)]

    first = chunk_segments(segments, 400, 120)
    second = chunk_segments([Segment(segment.text, segment.page) for segment in segments], 400, 120)

    assert first == second


@pytest.mark.parametrize(
    ("text", "chunk_size", "overlap"),
    [
        ("x" * 50_000, 1800, 250),
        ("x" * 5_000, 1, 0),
        ("x" * 5_000, 2, 5),
        (" " * 50_000 + "a trailing sentence long enough", 1800, 250),
        ("\n" * 20_000 + "after many newlines comes text", 100, 50),
        (". " * 20_000, 50, 25),
        ("ab. " * 5_000, 1, 0),
        ("a" + " " * 3 + "b", 1, 1),
        ("\u00e9\u0301" * 10_000, 7, 3),
        ("\n\n".join(["Short.", "x" * 4000, "y " * 2000]), 64, 64),
        ("word " * 20_000, 10, 9),
    ],
    ids=[
        "long-word",
        "size-1",
        "size-2-overlap-above-size",
        "leading-spaces",
        "newlines",
        "only-separators",
        "tiny-sentences-size-1",
        "spaces-size-1",
        "combining-marks",
        "mixed",
        "overlap-near-size",
    ],
)
def test_adversarial_input_terminates_within_limits(text: str, chunk_size: int, overlap: int) -> None:
    chunks = chunk_segments([Segment(text, 7)], chunk_size, overlap)

    assert chunks
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert 0 < len(chunk.text) <= chunk_size
        assert chunk.text == chunk.text.strip()
        assert chunk.text in text
        assert chunk.page == 7
    assert len(chunks) <= len(text)


def test_random_inputs_keep_every_invariant() -> None:
    rng = random.Random(20261014)
    alphabet = ["a", "bc", "Delta", " ", "  ", "\n", "\n\n", ". ", "? ", "! ", "; ", ", ", "\t", "\u00e9", "x" * 30]
    for _ in range(400):
        segments = [
            Segment("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120))), rng.choice([None, 1, 2, 3]))
            for _ in range(rng.randint(1, 4))
        ]
        chunk_size, overlap = rng.randint(1, 120), rng.randint(0, 200)

        chunks = chunk_segments(segments, chunk_size, overlap)

        assert chunks == chunk_segments(segments, chunk_size, overlap)
        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
        owners = iter(segments)
        owner = next(owners)
        for chunk in chunks:
            assert 0 < len(chunk.text) <= chunk_size
            assert chunk.text == chunk.text.strip()
            while not (chunk.page == owner.page and chunk.text in owner.text):
                owner = next(owners)  # chunks follow segment order and never span two segments
