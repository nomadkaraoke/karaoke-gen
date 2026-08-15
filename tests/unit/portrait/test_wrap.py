"""Tests for portrait balanced line-wrapping."""
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.portrait.wrap import balance_segments


def _seg(seg_id, text, start, end):
    # One synthetic word spanning the segment is enough for these tests; balance_segments
    # only concatenates word lists, it does not inspect per-word timing.
    return LyricsSegment(
        id=seg_id,
        text=text,
        words=[Word(id=f"w{seg_id}", text=text, start_time=start, end_time=end)],
        start_time=start,
        end_time=end,
    )


def test_orphan_merges_forward_when_it_fits():
    segs = [
        _seg("1", "No,", 10.0, 10.3),
        _seg("2", "he only wants", 10.4, 12.0),
    ]
    out = balance_segments(segs, max_line_length=19)
    assert len(out) == 1
    assert out[0].text == "No, he only wants"
    # Words preserved and combined in order.
    assert [w.text for w in out[0].words] == ["No,", "he only wants"]
    assert out[0].start_time == 10.0
    assert out[0].end_time == 12.0


def test_no_merge_when_result_would_overflow():
    segs = [
        _seg("1", "No,", 10.0, 10.3),
        _seg("2", "he only wants the naughty shit", 10.4, 12.0),
    ]
    out = balance_segments(segs, max_line_length=19)
    # Merged line (34 chars) exceeds the width budget → leave both as-is.
    assert [s.text for s in out] == ["No,", "he only wants the naughty shit"]


def test_no_merge_across_large_time_gap():
    segs = [
        _seg("1", "Oh,", 10.0, 10.3),
        _seg("2", "later line", 30.0, 31.0),  # 20s gap → different phrase
    ]
    out = balance_segments(segs, max_line_length=19, max_gap_seconds=1.5)
    assert [s.text for s in out] == ["Oh,", "later line"]


def test_prefers_smaller_gap_neighbour():
    segs = [
        _seg("1", "keep me here", 9.0, 9.9),   # gap to orphan = 0.05
        _seg("2", "hi", 9.95, 10.1),           # orphan
        _seg("3", "and далеко", 11.5, 12.0),   # gap from orphan = 1.4
    ]
    out = balance_segments(segs, max_line_length=20)
    # Orphan "hi" should merge backward (smaller gap) into "keep me here".
    assert out[0].text == "keep me here hi"
    assert len(out) == 2


def test_long_run_of_fragments_collapses():
    segs = [
        _seg("1", "a", 1.0, 1.1),
        _seg("2", "b", 1.2, 1.3),
        _seg("3", "c", 1.4, 1.5),
        _seg("4", "d", 1.6, 1.7),
    ]
    out = balance_segments(segs, max_line_length=12)
    # All fit within 12 chars → collapse to a single filled line.
    assert len(out) == 1
    assert out[0].text == "a b c d"


def test_empty_input():
    assert balance_segments([], max_line_length=19) == []
