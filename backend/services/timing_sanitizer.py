"""Sanitize submitted review corrections so word timings stay within their segment.

Mirrors the frontend sanitizeWordTimings.ts and the render-time
SegmentResizer._sanitize_segment_timings. Operates on the raw corrections dict the
frontend POSTs (Dict[str, Any]); clamps any word whose start/end falls outside its
segment window or is inverted, or that starts before the previous word in its line
started (out of order: a Tap To Sync pass that stopped mid-line can leave a stale
untapped word behind the last tapped one). A segment whose own bounds are missing/non-finite
(JSON.stringify drops `undefined`, so an in-browser edit can arrive without them)
gets them from its words' timings, else from its neighbours; one with no usable
timing anywhere is left untouched (see find_missing_timings). Mutates and returns
the same dict for caller convenience, plus the number of fields changed."""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fill_segment_bounds(segments: List[Any]) -> int:
    """Give segments with missing/non-finite bounds ones derived from their words,
    else from the neighbouring segments. Returns the number of fields filled."""
    changes = 0
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        start, end = seg.get("start_time"), seg.get("end_time")
        if _finite(start) and _finite(end):
            continue
        words = [w for w in seg.get("words", []) if isinstance(w, dict)]
        starts = [w["start_time"] for w in words if _finite(w.get("start_time"))]
        ends = [w["end_time"] for w in words if _finite(w.get("end_time"))]
        prev_end = next((s.get("end_time") for s in reversed(segments[:i])
                         if isinstance(s, dict) and _finite(s.get("end_time"))), None)
        next_start = next((s.get("start_time") for s in segments[i + 1:]
                           if isinstance(s, dict) and _finite(s.get("start_time"))), None)

        new_start = start if _finite(start) else (min(starts + ends) if starts or ends else prev_end)
        new_end = end if _finite(end) else (max(starts + ends) if starts or ends else next_start)
        if new_start is None and new_end is None:
            continue  # no timing anywhere near it: leave it for find_missing_timings
        if new_start is None:
            new_start = new_end
        if new_end is None or new_end < new_start:
            new_end = new_start
        if new_start != start:
            seg["start_time"] = new_start
            changes += 1
        if new_end != end:
            seg["end_time"] = new_end
            changes += 1
    return changes


def sanitize_corrections(corrections: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    segments = corrections.get("corrected_segments")
    if not isinstance(segments, list):
        return corrections, 0

    changes = _fill_segment_bounds(segments)
    out_of_order: List[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_start = seg.get("start_time")
        seg_end = seg.get("end_time")
        # If the segment's own bounds aren't finite, don't touch its words.
        if not _finite(seg_start) or not _finite(seg_end):
            continue
        seg_end = max(seg_end, seg_start)

        prev_start = seg_start
        prev_end = seg_start
        for w in seg.get("words", []):
            if not isinstance(w, dict):
                continue
            start = w.get("start_time")
            end = w.get("end_time")

            # Invalid start = missing/non-finite, outside the segment window, OR before the
            # previous word's START (out of order). A start between the previous word's start and
            # end is a small overlap, common in AI transcriptions, and is left alone. Invalid
            # starts are all repaired the same way: placed at the previous word's end. Moving rather than clearing, because a cleared word
            # would be refilled at prev_end on the next sanitize anyway, and nulls crash the
            # render (find_missing_timings only guards the preview).
            in_window = _finite(start) and seg_start <= start <= seg_end
            if in_window and start >= prev_start:
                new_start = start
            else:
                if in_window:
                    out_of_order.append(str(w.get("text", "")))
                new_start = min(max(prev_end, seg_start), seg_end)

            # Invalid end = missing/non-finite, before start, or past segment end.
            if _finite(end) and new_start <= end <= seg_end:
                new_end = end
            else:
                new_end = min(max(new_start, end if _finite(end) else new_start), seg_end)
                if new_end < new_start:
                    new_end = new_start

            if new_start != start:
                w["start_time"] = new_start
                changes += 1
            if new_end != end:
                w["end_time"] = new_end
                changes += 1
            prev_start = new_start
            prev_end = new_end

    if out_of_order:
        # Expected after a partial Tap To Sync pass, and repaired here: WARNING, not ERROR.
        # One aggregated line per submission (alert rules: one root cause, one message).
        logger.warning(
            "timing_sanitizer: moved %d out-of-order word(s) to the previous word's end: %s",
            len(out_of_order), ", ".join(repr(t) for t in out_of_order[:10]),
        )
    return corrections, changes


def find_missing_timings(corrections: Dict[str, Any]) -> Optional[str]:
    """After sanitize_corrections: the text of the first line that still has a word
    or segment without a finite start/end time, or None if every timing is usable."""
    for seg in corrections.get("corrected_segments") or []:
        if not isinstance(seg, dict):
            continue
        words = [w for w in seg.get("words", []) if isinstance(w, dict)]
        fields = [seg] + words
        if any(not _finite(f.get("start_time")) or not _finite(f.get("end_time")) for f in fields):
            return str(seg.get("text") or " ".join(str(w.get("text", "")) for w in words)).strip()
    return None
