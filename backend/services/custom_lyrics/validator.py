"""Pure validator over LLM-generated candidate lines vs. target lines."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class Severity(str, Enum):
    OK = "ok"
    MINOR = "minor"
    MAJOR = "major"


@dataclass
class LineValidation:
    line_index: int
    target_text: str
    candidate_text: str
    target_syllables: list[int]
    candidate_syllables: list[int]
    min_delta: int
    passes: bool
    severity: Severity
    time_budget_seconds: float


class _CounterProtocol(Protocol):
    def count_per_line(self, line: str) -> list[int]: ...
    def any_method_within(
        self, candidate_counts: list[int], target_counts: list[int], tolerance: int
    ) -> bool: ...
    def min_delta(
        self, candidate_counts: list[int], target_counts: list[int]
    ) -> int: ...


def _segment_duration(seg: Any) -> float:
    """Accepts dict or object with start_time/end_time; returns end - start (>= 0)."""
    if isinstance(seg, dict):
        start = seg.get("start_time")
        end = seg.get("end_time")
    else:
        start = getattr(seg, "start_time", None)
        end = getattr(seg, "end_time", None)
    if start is None or end is None:
        return 0.0
    try:
        return max(0.0, float(end) - float(start))
    except (TypeError, ValueError):
        return 0.0


def validate(
    candidate_lines: list[str],
    target_lines: list[str],
    target_segments: list[Any],
    counter: _CounterProtocol,
    tolerance: int,
) -> list[LineValidation]:
    """Score each candidate line against its target. Pure; no I/O."""
    if not (len(candidate_lines) == len(target_lines) == len(target_segments)):
        raise ValueError(
            f"length mismatch: candidates={len(candidate_lines)} targets={len(target_lines)} "
            f"segments={len(target_segments)}"
        )

    out: list[LineValidation] = []
    for i, (cand, tgt, seg) in enumerate(zip(candidate_lines, target_lines, target_segments)):
        target_counts = counter.count_per_line(tgt)
        candidate_counts = counter.count_per_line(cand)
        delta = counter.min_delta(candidate_counts, target_counts)
        passes = counter.any_method_within(candidate_counts, target_counts, tolerance)
        if passes:
            severity = Severity.OK
        elif delta <= tolerance + 2:
            severity = Severity.MINOR
        else:
            severity = Severity.MAJOR
        out.append(
            LineValidation(
                line_index=i,
                target_text=tgt,
                candidate_text=cand,
                target_syllables=target_counts,
                candidate_syllables=candidate_counts,
                min_delta=delta,
                passes=passes,
                severity=severity,
                time_budget_seconds=_segment_duration(seg),
            )
        )
    return out
