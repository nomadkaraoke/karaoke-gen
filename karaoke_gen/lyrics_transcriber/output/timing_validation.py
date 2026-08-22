"""Pre-render validation that every lyric segment/word carries real timing.

Untimed lyrics (``start_time``/``end_time`` == ``None``) are a legitimate
*intermediate* state in the review UI — the LyricsSynchronizer creates segments
with null timing that the user is expected to resolve by tap-syncing. But they
must never reach the output pipeline: ASS generation, segment resizing and the
GCE encoder all do arithmetic/comparisons on timing values and crash with cryptic
errors (``unsupported format string passed to NoneType.__format__`` or
``'<' not supported between instances of 'NoneType' and 'float'``) when a value
is ``None``.

This module provides a single, early gate that fails loudly with an actionable,
user-facing message instead of crashing deep inside rendering.
"""
from __future__ import annotations

from typing import List

from karaoke_gen.lyrics_transcriber.types import LyricsSegment


class LyricsTimingError(ValueError):
    """Raised when lyrics reach the output pipeline without complete timing.

    Carries structured detail so callers (API endpoints, workers) can surface a
    clear message and log useful breadcrumbs.
    """

    def __init__(self, message: str, *, untimed_words: int, untimed_segments: int, first_line: str) -> None:
        super().__init__(message)
        self.untimed_words = untimed_words
        self.untimed_segments = untimed_segments
        self.first_line = first_line


def validate_segment_timing(segments: List[LyricsSegment]) -> None:
    """Ensure every segment (and each of its words) has non-None start/end timing.

    A segment with no words is skipped (there is nothing to time). Any word with a
    ``None`` ``start_time``/``end_time``, or any word-bearing segment whose own
    ``start_time``/``end_time`` is ``None``, is a violation.

    Raises:
        LyricsTimingError: if any violation is found. The message names the count
            of untimed words and the first offending line so the user knows what
            to fix (synchronize their lyrics before generating the video).
    """
    untimed_words = 0
    untimed_segments = 0
    first_line = ""

    for segment in segments:
        words = segment.words or []
        seg_has_untimed = False

        if words and (segment.start_time is None or segment.end_time is None):
            seg_has_untimed = True

        for word in words:
            if word.start_time is None or word.end_time is None:
                untimed_words += 1
                seg_has_untimed = True

        if seg_has_untimed:
            untimed_segments += 1
            if not first_line:
                first_line = (segment.text or "").strip()

    if untimed_segments == 0:
        return

    message = (
        f"Lyrics are missing timing: {untimed_words} word(s) across "
        f"{untimed_segments} line(s) have no start/end time. "
        f"Please synchronize your lyrics (tap each line to the beat) before "
        f"generating the video. First affected line: {first_line!r}"
    )
    raise LyricsTimingError(
        message,
        untimed_words=untimed_words,
        untimed_segments=untimed_segments,
        first_line=first_line,
    )
