"""Tests for proportional timing redistribution (variable line count)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.custom_lyrics.timing import redistribute_timing_proportional


@pytest.fixture
def stub_counter() -> MagicMock:
    c = MagicMock()
    c.count_per_line = MagicMock(
        side_effect=lambda line: [len(line.split())] * 4
    )
    return c


def test_redistribute_two_equal_lines(stub_counter: MagicMock) -> None:
    new_lines = ["one two", "three four"]
    result = redistribute_timing_proportional(
        new_lines=new_lines, total_window=(0.0, 4.0), counter=stub_counter,
    )
    assert len(result) == 2
    # Equal syllable counts → equal slices
    assert result[0] == pytest.approx((0.0, 2.0))
    assert result[1] == pytest.approx((2.0, 4.0))


def test_redistribute_proportional_to_syllables(stub_counter: MagicMock) -> None:
    new_lines = ["a", "b c d"]  # 1 vs 3 syllables
    result = redistribute_timing_proportional(
        new_lines=new_lines, total_window=(0.0, 4.0), counter=stub_counter,
    )
    # 1/(1+3) = 0.25 → first slice 1.0s, second slice 3.0s
    assert result[0] == pytest.approx((0.0, 1.0))
    assert result[1] == pytest.approx((1.0, 4.0))


def test_redistribute_zero_syllable_lines_falls_back_to_even(stub_counter: MagicMock) -> None:
    stub_counter.count_per_line = MagicMock(return_value=[0, 0, 0, 0])
    new_lines = ["", ""]
    result = redistribute_timing_proportional(
        new_lines=new_lines, total_window=(0.0, 2.0), counter=stub_counter,
    )
    assert result[0] == pytest.approx((0.0, 1.0))
    assert result[1] == pytest.approx((1.0, 2.0))


def test_redistribute_single_line_spans_window(stub_counter: MagicMock) -> None:
    result = redistribute_timing_proportional(
        new_lines=["only"], total_window=(2.0, 5.0), counter=stub_counter,
    )
    assert result == [(2.0, 5.0)]


def test_redistribute_empty_lines_raises(stub_counter: MagicMock) -> None:
    with pytest.raises(ValueError, match="empty"):
        redistribute_timing_proportional(
            new_lines=[], total_window=(0.0, 1.0), counter=stub_counter,
        )


def test_redistribute_invalid_window_raises(stub_counter: MagicMock) -> None:
    with pytest.raises(ValueError, match="window"):
        redistribute_timing_proportional(
            new_lines=["a"], total_window=(5.0, 2.0), counter=stub_counter,
        )
