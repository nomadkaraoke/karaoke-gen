"""Balanced line-wrapping for portrait karaoke lyrics.

The landscape pipeline wraps lyric lines with ``SegmentResizer``, which splits at
natural break points (sentence ends, commas, conjunctions) calibrated for the wide
16:9 frame. In a narrow 9:16 frame the same splits — combined with a much smaller
``max_line_length`` — can leave orphan lines: a stray one- or two-word segment such
as ``"No,"`` sitting alone on a row. This module merges those orphans back into an
adjacent segment when the merged line still fits the portrait width and the two
segments are close together in time (i.e. part of the same sung phrase).

It operates on already-resized segments and only ever *merges* — it never re-splits —
so per-word karaoke timing is preserved exactly.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.segment_resizer import display_width

# A segment whose rendered width is below this (in half-width char units) is an
# "orphan" candidate — short enough to look stranded on its own row.
DEFAULT_ORPHAN_WIDTH = 8.0
# Only merge segments separated by at most this gap (seconds); larger gaps mean the
# lines belong to different phrases and should stay on separate rows.
DEFAULT_MAX_GAP_SECONDS = 1.5


def _merge(a: LyricsSegment, b: LyricsSegment) -> LyricsSegment:
    """Merge two temporally-adjacent segments (``a`` before ``b``) into one."""
    words = a.words + b.words
    text = f"{a.text} {b.text}".strip()
    return LyricsSegment(
        id=a.id,
        text=text,
        words=words,
        start_time=min(a.start_time, b.start_time),
        end_time=max(a.end_time, b.end_time),
        singer=a.singer if a.singer == b.singer else None,
    )


def _gap(a: LyricsSegment, b: LyricsSegment) -> float:
    """Non-negative time gap between the end of ``a`` and the start of ``b``."""
    return max(0.0, b.start_time - a.end_time)


def balance_segments(
    segments: List[LyricsSegment],
    max_line_length: int,
    *,
    orphan_width: float = DEFAULT_ORPHAN_WIDTH,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    logger: Optional[logging.Logger] = None,
) -> List[LyricsSegment]:
    """Merge orphan (very short) segments into a neighbour when it fits the frame.

    A segment is merged when:
      * its rendered ``display_width`` is below ``orphan_width``, and
      * a neighbour exists within ``max_gap_seconds``, and
      * the merged line's ``display_width`` is ``<= max_line_length``.

    When both neighbours qualify, the one with the smaller time gap wins. The pass
    repeats until no further merge is possible, so a run of short fragments collapses
    into as few well-filled lines as the width budget allows. Timing is preserved
    because segments are only concatenated, never re-split.
    """
    if not segments:
        return []

    result = list(segments)
    changed = True
    while changed:
        changed = False
        for i, seg in enumerate(result):
            if display_width(seg.text) >= orphan_width:
                continue

            # Candidate merges: (merged_segment, replace_slice, gap)
            candidates = []
            if i + 1 < len(result):
                nxt = result[i + 1]
                if _gap(seg, nxt) <= max_gap_seconds and display_width(
                    f"{seg.text} {nxt.text}"
                ) <= max_line_length:
                    candidates.append((_merge(seg, nxt), (i, i + 2), _gap(seg, nxt)))
            if i - 1 >= 0:
                prev = result[i - 1]
                if _gap(prev, seg) <= max_gap_seconds and display_width(
                    f"{prev.text} {seg.text}"
                ) <= max_line_length:
                    candidates.append((_merge(prev, seg), (i - 1, i + 1), _gap(prev, seg)))

            if not candidates:
                continue

            merged, (lo, hi), _ = min(candidates, key=lambda c: c[2])
            result[lo:hi] = [merged]
            if logger:
                logger.debug(f"Portrait balance: merged orphan into '{merged.text}'")
            changed = True
            break  # indices shifted; restart the scan

    return result
