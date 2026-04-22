"""Regression tests: SegmentResizer must preserve singer fields.

Integration bug: `OutputGenerator.generate_outputs` runs `SegmentResizer.resize_segments`
on its `corrected_segments` before handing them to `SubtitlesGenerator`. The resizer
rebuilds LyricsSegment / Word objects for cleaning and splitting — and all three
rebuilder helpers were dropping the `singer` field silently. Result: after the review
UI correctly attached segment-level and word-level singer data, the renderer still
saw `None` everywhere and rendered every line in Singer 1's default color.
"""
import logging

from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word


def _word(id_: str, text: str, start: float, end: float, singer=None) -> Word:
    return Word(id=id_, text=text, start_time=start, end_time=end, singer=singer)


def _short_segment(singer=None, word_singers=None) -> LyricsSegment:
    """Returns a short (under max_line_length) segment so it hits the cleaning path."""
    words = [
        _word("w1", "hi", 0.0, 0.3, singer=(word_singers[0] if word_singers else None)),
        _word("w2", "there", 0.4, 0.7, singer=(word_singers[1] if word_singers else None)),
    ]
    return LyricsSegment(id="s1", text="hi there", words=words, start_time=0.0, end_time=0.7, singer=singer)


def _long_segment(singer=None) -> LyricsSegment:
    """Returns a segment long enough to be split (over the default max_line_length)."""
    phrase = "the quick brown fox jumps over the lazy dog. "
    text = (phrase * 3).strip()
    words = []
    t = 0.0
    for i, w in enumerate(text.split()):
        words.append(_word(f"w{i}", w, t, t + 0.3))
        t += 0.35
    return LyricsSegment(id="slong", text=text, words=words, start_time=0.0, end_time=t, singer=singer)


class TestCleaningPathPreservesSinger:
    def test_short_segment_cleaned_keeps_singer(self):
        resizer = SegmentResizer(max_line_length=40, logger=logging.getLogger("test"))
        seg = _short_segment(singer=2)
        result = resizer.resize_segments([seg])
        assert len(result) == 1
        assert result[0].singer == 2

    def test_short_segment_keeps_word_level_overrides(self):
        resizer = SegmentResizer(max_line_length=40, logger=logging.getLogger("test"))
        seg = _short_segment(singer=1, word_singers=[None, 2])
        result = resizer.resize_segments([seg])
        assert result[0].words[1].singer == 2
        assert result[0].words[0].singer is None


class TestSplitPathPreservesSinger:
    def test_long_segment_split_children_inherit_singer(self):
        resizer = SegmentResizer(max_line_length=20, logger=logging.getLogger("test"))
        seg = _long_segment(singer=2)
        result = resizer.resize_segments([seg])
        # All split children should inherit singer=2 from the parent
        assert len(result) > 1, "expected the long segment to split"
        for child in result:
            assert child.singer == 2, f"child segment {child.id} lost singer"

    def test_long_segment_with_no_singer_splits_to_none(self):
        resizer = SegmentResizer(max_line_length=20, logger=logging.getLogger("test"))
        seg = _long_segment(singer=None)
        result = resizer.resize_segments([seg])
        for child in result:
            assert child.singer is None


class TestCleanedWordHelper:
    def test_cleaned_word_preserves_singer_and_correction_flag(self):
        resizer = SegmentResizer(max_line_length=40, logger=logging.getLogger("test"))
        w = Word(
            id="w1",
            text="hello\n",
            start_time=0.0,
            end_time=0.3,
            confidence=0.9,
            created_during_correction=True,
            singer=2,
        )
        cleaned = resizer._create_cleaned_word(w)
        assert cleaned.singer == 2
        assert cleaned.created_during_correction is True
        assert cleaned.text == "hello"
