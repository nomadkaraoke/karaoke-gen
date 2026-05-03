"""Tests for eval scorer."""
from __future__ import annotations

import pytest

from backend.eval.custom_lyrics.scorer import (
    PerLineScore,
    FixtureScore,
    score_per_line,
    aggregate_fixture,
)
from backend.services.custom_lyrics.validator import LineValidation, Severity


def _line_val(idx: int, target: list[int], cand: list[int], passes: bool, severity: Severity, delta: int) -> LineValidation:
    return LineValidation(
        line_index=idx,
        target_text=f"t{idx}",
        candidate_text=f"c{idx}",
        target_syllables=target,
        candidate_syllables=cand,
        min_delta=delta,
        passes=passes,
        severity=severity,
        time_budget_seconds=1.0,
    )


def test_score_per_line_pass_fail_thresholds() -> None:
    val = _line_val(0, [5, 5, 5, 5], [6, 6, 6, 6], passes=False, severity=Severity.MINOR, delta=1)
    s = score_per_line(val)
    assert s.pass_at_0 is False
    assert s.pass_at_1 is True
    assert s.pass_at_2 is True
    assert s.pass_at_4 is True
    assert s.min_delta == 1


def test_score_per_line_major_violation() -> None:
    val = _line_val(0, [5, 5, 5, 5], [12, 12, 12, 12], passes=False, severity=Severity.MAJOR, delta=7)
    s = score_per_line(val)
    assert s.pass_at_0 is False
    assert s.pass_at_4 is False
    assert s.severity == "major"


def test_aggregate_fixture_basic() -> None:
    metadata = [
        _line_val(0, [5]*4, [5]*4, passes=True, severity=Severity.OK, delta=0),
        _line_val(1, [5]*4, [6]*4, passes=False, severity=Severity.MINOR, delta=1),
        _line_val(2, [5]*4, [9]*4, passes=False, severity=Severity.MAJOR, delta=4),
    ]
    fixture = aggregate_fixture(
        fixture_id="x",
        settings_name="default",
        metadata=metadata,
        iterations_used=2,
        stop_reason="max_iters_reached",
        duration_ms=1500,
        gemini_calls=3,
        line_count_match=True,
    )
    assert fixture.pct_pass_at_2 == pytest.approx(2 / 3)
    assert fixture.pct_pass_at_0 == pytest.approx(1 / 3)
    assert fixture.mean_delta == pytest.approx((0 + 1 + 4) / 3)
    assert fixture.max_delta == 4
