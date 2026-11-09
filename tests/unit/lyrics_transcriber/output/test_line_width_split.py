"""Lines too wide for the frame must be split before libass wraps them.

Bug (NOMAD-1779, job 58555663): the character-count limit (``max_line_length``) let
through lines like "Mm- mm- mm, mm- call me when you're ..." that are short in
characters but made of wide glyphs. At 4K they measured ~3890px on a 3840px frame,
so libass wrapped each one onto a second physical line, one row lower, on top of the
next lyric line: two lines of lyrics drawn over each other.

``SubtitlesGenerator`` now measures every line in the real style font (scaled as libass
scales it) and splits any line wider than ``MAX_LINE_WIDTH_FRACTION`` of the frame.
"""
import logging
import re

import pytest

from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer
from karaoke_gen.lyrics_transcriber.output.subtitles import MAX_LINE_WIDTH_FRACTION
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word

from .ass_render_harness import (
    FONT_SIZE,
    WIDTH,
    ffmpeg_has_libass,
    generate_ass,
    lyric_lines,
    make_segment,
    render_frames,
)


def _seg(words, translation=None):
    ws = [Word(id=f"w{i}", text=t, start_time=i * 1.0, end_time=i * 1.0 + 0.8) for i, t in enumerate(words)]
    return LyricsSegment(
        id="s", text=" ".join(words), words=ws, start_time=0.0, end_time=ws[-1].end_time, translation=translation
    )


def _measure(text: str) -> float:
    """10px per character: easy to reason about."""
    return len(text) * 10.0


@pytest.fixture
def resizer():
    return SegmentResizer(logger=logging.getLogger("test"))


class TestSplitToWidth:
    def test_line_that_fits_is_unchanged(self, resizer):
        seg = _seg(["short", "line"])
        assert resizer.split_to_width([seg], _measure, 200) == [seg]

    def test_too_wide_line_is_split_and_every_piece_fits(self, resizer):
        seg = _seg(["mm-"] * 12)  # 47 chars = 470px
        out = resizer.split_to_width([seg], _measure, 200)
        assert len(out) > 1
        assert all(_measure(s.text) <= 200 for s in out)

    def test_words_and_timing_preserved_in_order(self, resizer):
        seg = _seg([f"word{i}" for i in range(10)])
        out = resizer.split_to_width([seg], _measure, 250)
        assert [w for s in out for w in s.words] == seg.words
        for s in out:
            assert s.start_time == s.words[0].start_time
            assert s.end_time == s.words[-1].end_time
            assert s.text == " ".join(w.text for w in s.words)

    def test_prefers_breaking_after_a_comma(self, resizer):
        # Balanced split would be after word 3; the comma after word 2 still fits.
        seg = _seg(["aaaa", "bbbb,", "cccc", "dddd", "eeee", "ffff"])
        out = resizer.split_to_width([seg], _measure, 200)
        assert [s.text for s in out] == ["aaaa bbbb,", "cccc dddd eeee ffff"]

    def test_balanced_split_without_punctuation(self, resizer):
        seg = _seg(["aaaa", "bbbb", "cccc", "dddd"])  # 19 chars
        out = resizer.split_to_width([seg], _measure, 150)
        assert [s.text for s in out] == ["aaaa bbbb", "cccc dddd"]

    def test_single_word_wider_than_frame_is_kept(self, resizer):
        seg = _seg(["supercalifragilistic"])
        assert resizer.split_to_width([seg], _measure, 50) == [seg]

    def test_translation_is_shared_between_pieces(self, resizer):
        seg = _seg(["aaaa", "bbbb", "cccc", "dddd"], translation="uno dos tres cuatro")
        out = resizer.split_to_width([seg], _measure, 150)
        assert len(out) == 2
        assert " ".join(s.translation for s in out) == "uno dos tres cuatro"

    def test_singer_carried_to_pieces(self, resizer):
        seg = _seg(["aaaa", "bbbb", "cccc", "dddd"])
        seg.singer = 2
        assert {s.singer for s in resizer.split_to_width([seg], _measure, 150)} == {2}


# A hum line made of wide glyphs, ~1475px on the 1280px test frame: well clear of the split
# threshold on every platform (font layout differs slightly between macOS and Linux).
WIDE_HUM = ["Mm-", "mm-", "mm,"] * 4


def _lyric_events(ass_path):
    with open(ass_path, encoding="utf-8-sig") as f:
        return [l for l in f if l.startswith("Dialogue") and "\\pos(" in l]


def test_generated_ass_splits_wide_hum_line(tmp_path):
    ass = generate_ass(tmp_path, [make_segment(WIDE_HUM, start=4.0)])
    events = _lyric_events(ass)
    assert len(events) >= 2
    texts = [re.sub(r"\{[^}]*\}", "", e.split(",", 5)[5]).strip() for e in events]
    assert " ".join(texts) == " ".join(WIDE_HUM)


def test_generated_ass_leaves_normal_lines_alone(tmp_path):
    ass = generate_ass(tmp_path, [make_segment(["it", "is", "in", "a", "line"], start=4.0)])
    assert len(_lyric_events(ass)) == 1


@pytest.mark.skipif(not ffmpeg_has_libass(), reason="ffmpeg with libass not available")
def test_every_line_renders_as_one_physical_line(tmp_path):
    """Rendered with real libass: a line wider than the frame must not wrap into a second row.

    Unsplit, this line is ~1475px on the 1280px test frame and libass draws its one
    event as two rows (the second one lands where the next lyric line goes).
    """
    ass = generate_ass(tmp_path, [make_segment(WIDE_HUM, start=4.0)])
    events = _lyric_events(ass)
    frame = render_frames(ass, [5.0]).frames[5.0]
    lines = lyric_lines(frame)
    assert len(lines) == len(events), f"{len(events)} lyric events rendered as {len(lines)} rows"
    for line in lines:
        assert line.bottom - line.top < FONT_SIZE * 1.5  # one row of text, not two
        assert line.right - line.left <= WIDTH * MAX_LINE_WIDTH_FRACTION
