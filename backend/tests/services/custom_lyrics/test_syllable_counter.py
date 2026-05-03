"""Tests for the shared SyllableCounter utility."""
from __future__ import annotations

import pytest

from karaoke_gen.lyrics_transcriber.utils.syllable_counter import SyllableCounter


@pytest.fixture(scope="module")
def counter() -> SyllableCounter:
    return SyllableCounter()


def test_instantiates_without_error(counter: SyllableCounter) -> None:
    assert counter is not None


def test_count_per_word_returns_four_method_counts(counter: SyllableCounter) -> None:
    counts = counter.count_per_word(["hello"])
    assert isinstance(counts, list)
    assert len(counts) == 4
    assert all(isinstance(c, int) and c > 0 for c in counts)


def test_count_per_word_empty_input(counter: SyllableCounter) -> None:
    counts = counter.count_per_word([])
    assert counts == [0, 0, 0, 0]


def test_count_per_line_tokenises_then_counts(counter: SyllableCounter) -> None:
    line_counts = counter.count_per_line("hello world")
    word_counts = counter.count_per_word(["hello", "world"])
    assert line_counts == word_counts


def test_count_per_line_handles_punctuation(counter: SyllableCounter) -> None:
    counts = counter.count_per_line("Hello, world!")
    assert all(c >= 2 for c in counts)
