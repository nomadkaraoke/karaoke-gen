"""Sanitize submitted review corrections so word timings stay within their segment.

Mirrors the frontend sanitizeWordTimings.ts and the render-time
SegmentResizer._sanitize_segment_timings. Operates on the raw corrections dict the
frontend POSTs (Dict[str, Any]); clamps any word whose start/end falls outside its
segment window or is inverted. Segments whose own bounds are non-finite are left
untouched (the editor derives bounds from words elsewhere). Mutates and returns the
same dict for caller convenience, plus the number of fields clamped."""
from __future__ import annotations

import math
from typing import Any, Dict, Tuple


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def sanitize_corrections(corrections: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    segments = corrections.get("corrected_segments")
    if not isinstance(segments, list):
        return corrections, 0

    changes = 0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_start = seg.get("start_time")
        seg_end = seg.get("end_time")
        # If the segment's own bounds aren't finite, don't touch its words.
        if not _finite(seg_start) or not _finite(seg_end):
            continue
        seg_end = max(seg_end, seg_start)

        prev_end = seg_start
        for w in seg.get("words", []):
            if not isinstance(w, dict):
                continue
            start = w.get("start_time")
            end = w.get("end_time")

            # Invalid start = missing/non-finite OR outside the segment window.
            if _finite(start) and seg_start <= start <= seg_end:
                new_start = start
            else:
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
            prev_end = new_end

    return corrections, changes
