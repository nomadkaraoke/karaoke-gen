"""Tests for the pre-render timing validation gate.

Regression for job 231806a4 ("Nelly Furtado - Maneater" with Danish custom
parody lyrics): the review UI saved 50 segments / 364 words with all
start_time/end_time == None (user replaced lyrics via the synchronizer's
"Edit Lyrics" modal but never tap-synced). The render + preview pipeline then
crashed with cryptic NoneType errors. The gate must reject this loudly with an
actionable message.
"""
import pytest

from karaoke_gen.lyrics_transcriber.output.timing_validation import (
    LyricsTimingError,
    validate_segment_timing,
)
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word


def _timed_segment() -> LyricsSegment:
    return LyricsSegment(
        id="s0",
        text="Hello world",
        words=[
            Word(id="w0", text="Hello", start_time=0.0, end_time=0.5),
            Word(id="w1", text="world", start_time=0.5, end_time=1.0),
        ],
        start_time=0.0,
        end_time=1.0,
    )


def _untimed_segment(idx: int = 0) -> LyricsSegment:
    """Shape of a segment created by LyricsSynchronizer 'Edit Lyrics' (all null)."""
    return LyricsSegment(
        id=f"segment-{idx}-1782928225732",
        text="Vor's veninde er blevet gift",
        words=[
            Word(id="w0", text="Vor's", start_time=None, end_time=None),
            Word(id="w1", text="veninde", start_time=None, end_time=None),
        ],
        start_time=None,
        end_time=None,
    )


def test_valid_timing_passes():
    # Should not raise.
    validate_segment_timing([_timed_segment(), _timed_segment()])


def test_empty_list_passes():
    validate_segment_timing([])


def test_all_untimed_raises_with_counts():
    segments = [_untimed_segment(i) for i in range(3)]
    with pytest.raises(LyricsTimingError) as exc:
        validate_segment_timing(segments)
    err = exc.value
    assert err.untimed_segments == 3
    assert err.untimed_words == 6
    assert err.first_line == "Vor's veninde er blevet gift"
    # Message is actionable for the user.
    assert "synchronize" in str(err).lower()


def test_single_untimed_word_raises():
    seg = _timed_segment()
    seg.words[1].start_time = None  # one bad word
    with pytest.raises(LyricsTimingError) as exc:
        validate_segment_timing([seg])
    assert exc.value.untimed_words == 1
    assert exc.value.untimed_segments == 1


def test_untimed_segment_bounds_with_timed_words_raises():
    seg = _timed_segment()
    seg.start_time = None  # segment bound null even though words are timed
    with pytest.raises(LyricsTimingError):
        validate_segment_timing([seg])


def test_segment_with_no_words_is_skipped():
    # A word-less segment has nothing to time; must not raise.
    seg = LyricsSegment(id="s", text="", words=[], start_time=None, end_time=None)
    validate_segment_timing([seg])


def test_mixed_timed_and_untimed_reports_first_untimed_line():
    segments = [_timed_segment(), _untimed_segment(5)]
    with pytest.raises(LyricsTimingError) as exc:
        validate_segment_timing(segments)
    assert exc.value.first_line == "Vor's veninde er blevet gift"
    assert exc.value.untimed_segments == 1
