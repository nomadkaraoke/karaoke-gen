"""Pure scoring functions for custom-lyrics eval."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.services.custom_lyrics.validator import LineValidation


@dataclass
class PerLineScore:
    line_index: int
    min_delta: int
    pass_at_0: bool
    pass_at_1: bool
    pass_at_2: bool
    pass_at_4: bool
    severity: str


@dataclass
class FixtureScore:
    fixture_id: str
    settings_name: str
    line_count: int
    pct_pass_at_2: float
    pct_pass_at_1: float
    pct_pass_at_0: float
    pct_pass_at_4: float
    mean_delta: float
    median_delta: float
    max_delta: int
    iterations_used: int
    stop_reason: str
    duration_ms: int
    gemini_calls: int
    line_count_match: bool
    severity_breakdown: dict[str, int]


def score_per_line(val: LineValidation) -> PerLineScore:
    return PerLineScore(
        line_index=val.line_index,
        min_delta=val.min_delta,
        pass_at_0=val.min_delta <= 0,
        pass_at_1=val.min_delta <= 1,
        pass_at_2=val.min_delta <= 2,
        pass_at_4=val.min_delta <= 4,
        severity=val.severity.value,
    )


def aggregate_fixture(
    *,
    fixture_id: str,
    settings_name: str,
    metadata: list[LineValidation],
    iterations_used: int,
    stop_reason: str,
    duration_ms: int,
    gemini_calls: int,
    line_count_match: bool,
) -> FixtureScore:
    n = len(metadata)
    if n == 0:
        return FixtureScore(
            fixture_id=fixture_id, settings_name=settings_name,
            line_count=0,
            pct_pass_at_2=0, pct_pass_at_1=0, pct_pass_at_0=0, pct_pass_at_4=0,
            mean_delta=0, median_delta=0, max_delta=0,
            iterations_used=iterations_used, stop_reason=stop_reason,
            duration_ms=duration_ms, gemini_calls=gemini_calls,
            line_count_match=line_count_match,
            severity_breakdown={"ok": 0, "minor": 0, "major": 0},
        )
    deltas = sorted(v.min_delta for v in metadata)
    severity_breakdown = {"ok": 0, "minor": 0, "major": 0}
    for v in metadata:
        severity_breakdown[v.severity.value] += 1
    return FixtureScore(
        fixture_id=fixture_id,
        settings_name=settings_name,
        line_count=n,
        pct_pass_at_2=sum(1 for d in deltas if d <= 2) / n,
        pct_pass_at_1=sum(1 for d in deltas if d <= 1) / n,
        pct_pass_at_0=sum(1 for d in deltas if d <= 0) / n,
        pct_pass_at_4=sum(1 for d in deltas if d <= 4) / n,
        mean_delta=sum(deltas) / n,
        median_delta=deltas[n // 2],
        max_delta=deltas[-1],
        iterations_used=iterations_used,
        stop_reason=stop_reason,
        duration_ms=duration_ms,
        gemini_calls=gemini_calls,
        line_count_match=line_count_match,
        severity_breakdown=severity_breakdown,
    )


def aggregate_corpus(per_fixture: Iterable[FixtureScore]) -> dict:
    """Macro-averaged corpus aggregate."""
    fixtures = list(per_fixture)
    if not fixtures:
        return {}
    return {
        "fixture_count": len(fixtures),
        "macro_pct_pass_at_2": sum(f.pct_pass_at_2 for f in fixtures) / len(fixtures),
        "macro_pct_pass_at_1": sum(f.pct_pass_at_1 for f in fixtures) / len(fixtures),
        "macro_pct_pass_at_0": sum(f.pct_pass_at_0 for f in fixtures) / len(fixtures),
        "macro_pct_pass_at_4": sum(f.pct_pass_at_4 for f in fixtures) / len(fixtures),
        "macro_mean_delta": sum(f.mean_delta for f in fixtures) / len(fixtures),
        "total_gemini_calls": sum(f.gemini_calls for f in fixtures),
    }
