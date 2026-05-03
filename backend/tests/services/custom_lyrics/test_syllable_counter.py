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


def test_any_method_within_all_match() -> None:
    assert SyllableCounter.any_method_within([5, 5, 5, 5], [5, 5, 5, 5], tolerance=0) is True


def test_any_method_within_one_pair_close() -> None:
    # spacy(candidate)=10, syllables(target)=8 → delta=2; passes at tol=2
    assert SyllableCounter.any_method_within([10, 11, 12, 13], [6, 7, 7, 8], tolerance=2) is True


def test_any_method_within_no_pair_close() -> None:
    assert SyllableCounter.any_method_within([10, 10, 10, 10], [5, 5, 5, 5], tolerance=2) is False


def test_any_method_within_empty_inputs_returns_false() -> None:
    assert SyllableCounter.any_method_within([], [5, 5, 5, 5], tolerance=10) is False
    assert SyllableCounter.any_method_within([5, 5, 5, 5], [], tolerance=10) is False


def test_min_delta() -> None:
    assert SyllableCounter.min_delta([10, 11, 12, 13], [6, 7, 7, 8]) == 2
    assert SyllableCounter.min_delta([5, 5, 5, 5], [5, 5, 5, 5]) == 0
    assert SyllableCounter.min_delta([], [5, 5, 5, 5]) == 0
