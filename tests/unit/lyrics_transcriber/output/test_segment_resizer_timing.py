"""Regression tests: a resized (split) segment must never inherit a word
timestamp that falls outside its parent segment's [start_time, end_time].

Bug (job 17f7c313, "Atmosphere - Scalp"): the transcription emitted the segment

    A whiskey and a beer, let's forget where we are   (start=15.18, end=18.04)

but the four leading words ("A", "whiskey", "and", "a") had invalid timestamps
(start_time=0.0, end_time=-0.005) — only "beer," onward carried real timing.
``_create_segment_from_words`` sets a split line's ``start_time`` to
``words[0].start_time``, so the line "A whiskey and a beer," inherited
start_time=0.0.

That zero start_time corrupts screen ordering downstream: a LyricsScreen sorts by
``min(line.segment.start_time)``, so the screen jumped ahead of the INTRO section
screen. The INTRO then wiped the active-line bookkeeping and the next screen
faded in (post-instrumental) over the still-displayed previous screen — two full
screens of lyrics rendered on top of each other for 7-12 seconds.

The resizer must guarantee valid, in-bounds word timing so a split segment can
never start before its parent segment does.
"""
import logging

from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word


def _scalp_segment() -> LyricsSegment:
    """The exact shape of the offending segment from job 17f7c313."""
    words = [
        Word(id="w0", text="A", start_time=0.0, end_time=-0.005),
        Word(id="w1", text="whiskey", start_time=0.0, end_time=-0.005),
        Word(id="w2", text="and", start_time=0.0, end_time=0.0),
        Word(id="w3", text="a", start_time=0.0, end_time=0.0),
        Word(id="w4", text="beer,", start_time=16.10, end_time=16.42),
        Word(id="w5", text="let's", start_time=16.46, end_time=16.64),
        Word(id="w6", text="forget", start_time=16.68, end_time=17.14),
        Word(id="w7", text="where", start_time=17.20, end_time=17.44),
        Word(id="w8", text="we", start_time=17.46, end_time=17.62),
        Word(id="w9", text="are", start_time=17.66, end_time=18.04),
    ]
    return LyricsSegment(
        id="s",
        text="A whiskey and a beer, let's forget where we are",
        words=words,
        start_time=15.18,
        end_time=18.04,
    )


def test_split_segment_start_time_not_before_parent():
    """A split sub-segment must not start before its parent segment's start_time."""
    resizer = SegmentResizer(max_line_length=36, logger=logging.getLogger("test"))

    result = resizer.resize_segments([_scalp_segment()])

    # The segment is over 36 chars, so it splits into multiple lines.
    assert len(result) > 1, "expected the segment to be split"
    for s in result:
        assert s.start_time >= 15.18, f"sub-segment '{s.text}' starts at {s.start_time}, before parent 15.18"


def test_resized_words_stay_within_parent_bounds():
    """Every word in the output must have valid, in-bounds, non-inverted timing."""
    resizer = SegmentResizer(max_line_length=36, logger=logging.getLogger("test"))

    result = resizer.resize_segments([_scalp_segment()])

    for s in result:
        for w in s.words:
            assert w.start_time >= 15.18, f"word '{w.text}' start {w.start_time} < parent start 15.18"
            assert w.end_time >= w.start_time, f"word '{w.text}' end {w.end_time} < start {w.start_time}"
            assert w.end_time <= 18.04 + 1e-6, f"word '{w.text}' end {w.end_time} > parent end 18.04"


def test_valid_timings_are_left_untouched():
    """Sanitation must not perturb already-valid word timings."""
    resizer = SegmentResizer(max_line_length=36, logger=logging.getLogger("test"))
    words = [
        Word(id="w0", text="Filling", start_time=21.40, end_time=21.82),
        Word(id="w1", text="up", start_time=21.86, end_time=22.10),
        Word(id="w2", text="that", start_time=22.14, end_time=22.42),
        Word(id="w3", text="cup", start_time=22.46, end_time=22.78),
    ]
    seg = LyricsSegment(id="s", text="Filling up that cup", words=words, start_time=21.40, end_time=22.78)

    result = resizer.resize_segments([seg])

    assert len(result) == 1
    out = result[0]
    assert out.start_time == 21.40
    assert [(w.start_time, w.end_time) for w in out.words] == [
        (21.40, 21.82),
        (21.86, 22.10),
        (22.14, 22.42),
        (22.46, 22.78),
    ]
