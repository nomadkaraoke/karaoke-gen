"""Proportional timing redistribution for variable line count."""
from __future__ import annotations

from typing import Protocol


class _CounterProtocol(Protocol):
    def count_per_line(self, line: str) -> list[int]: ...


def _median_count(counts: list[int]) -> int:
    if not counts:
        return 0
    sorted_c = sorted(counts)
    n = len(sorted_c)
    if n % 2 == 1:
        return sorted_c[n // 2]
    # Even-length: integer-rounded average of the two middle values
    return (sorted_c[n // 2 - 1] + sorted_c[n // 2]) // 2


def redistribute_timing_proportional(
    *,
    new_lines: list[str],
    total_window: tuple[float, float],
    counter: _CounterProtocol,
) -> list[tuple[float, float]]:
    """Distribute `total_window` across `new_lines` proportional to per-line syllable count.

    Falls back to even distribution if all lines have 0 syllables.
    """
    if not new_lines:
        raise ValueError("new_lines must not be empty")
    start, end = total_window
    if end <= start:
        raise ValueError(f"window invalid: ({start}, {end})")

    syllables = [_median_count(counter.count_per_line(line)) for line in new_lines]
    total_syl = sum(syllables)

    if total_syl == 0:
        # Even split fallback
        per_line = (end - start) / len(new_lines)
        return [
            (start + i * per_line, start + (i + 1) * per_line)
            for i in range(len(new_lines))
        ]

    # Treat zero-syllable lines as weight-1 so they get a non-zero slice.
    # Otherwise an empty/whitespace line in a mostly-non-empty set yields a
    # zero-length segment that the timing pipeline downstream may reject.
    weights = [s if s > 0 else 1 for s in syllables]
    total_weight = sum(weights)

    out: list[tuple[float, float]] = []
    cursor = start
    duration = end - start
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            out.append((cursor, end))  # last slice goes to exact end (avoid float drift)
        else:
            slice_dur = duration * w / total_weight
            out.append((cursor, cursor + slice_dur))
            cursor += slice_dur
    return out
