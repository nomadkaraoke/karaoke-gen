"""Tests for the custom-lyrics validator."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.custom_lyrics.validator import (
    LineValidation,
    Severity,
    validate,
)


@pytest.fixture
def stub_counter() -> MagicMock:
    """Returns a SyllableCounter-shaped stub returning fixed counts per call."""
    c = MagicMock()
    c.count_per_line = MagicMock(side_effect=lambda line: _stub_counts(line))
    c.any_method_within = MagicMock(
        side_effect=lambda cand, tgt, tol: min(abs(a - b) for a in cand for b in tgt) <= tol
    )
    c.min_delta = MagicMock(
        side_effect=lambda cand, tgt: min(abs(a - b) for a in cand for b in tgt)
    )
    return c


def _stub_counts(line: str) -> list[int]:
    """Stub: every word = 1 syllable; punctuation ignored. Returns [n, n, n, n]."""
    n = len([w for w in line.split() if any(ch.isalpha() for ch in w)])
    return [n, n, n, n]


def test_validate_all_pass(stub_counter: MagicMock) -> None:
    candidates = ["one two three", "four five"]
    targets = ["aa bb cc", "dd ee"]
    segments = [_segment(0.0, 1.0), _segment(1.0, 2.0)]
    result = validate(candidates, targets, segments, stub_counter, tolerance=0)
    assert len(result) == 2
    assert all(r.passes for r in result)
    assert all(r.severity is Severity.OK for r in result)


def test_validate_one_fails(stub_counter: MagicMock) -> None:
    candidates = ["one two three four five six seven eight"]
    targets = ["aa bb cc"]
    segments = [_segment(0.0, 1.0)]
    result = validate(candidates, targets, segments, stub_counter, tolerance=0)
    assert len(result) == 1
    assert result[0].passes is False
    assert result[0].min_delta == 5
    assert result[0].severity is Severity.MAJOR


def test_validate_severity_minor(stub_counter: MagicMock) -> None:
    candidates = ["a b c d"]      # 4 words
    targets = ["x y z"]           # 3 words
    segments = [_segment(0.0, 1.0)]
    # tolerance=0 → delta=1 → fails; minor severity (delta <= tolerance + 2)
    result = validate(candidates, targets, segments, stub_counter, tolerance=0)
    assert result[0].passes is False
    assert result[0].severity is Severity.MINOR


def test_validate_length_mismatch_raises(stub_counter: MagicMock) -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        validate(["one"], ["aa", "bb"], [_segment(0.0, 1.0)], stub_counter, tolerance=0)


def test_validate_includes_time_budget(stub_counter: MagicMock) -> None:
    segments = [_segment(2.0, 5.5)]
    result = validate(["x"], ["y"], segments, stub_counter, tolerance=0)
    assert result[0].time_budget_seconds == pytest.approx(3.5)


def _segment(start: float, end: float) -> dict:
    return {"start_time": start, "end_time": end}
