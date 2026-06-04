"""Regression tests: SegmentResizer must never emit a line longer than
``max_line_length``.

Bug (job 5c631108): the segment

    Well the word got around; they said "Steph, she's the real one,"

was split correctly by ``_process_segment_text`` into three short lines, but
``_create_segments_from_lines`` re-assigns Word objects to those lines via
substring matching. The break point for ``;`` was placed *before* the
semicolon, so the line ended with ``...around`` while the word token was
``around;``. The substring match (``"around;" in "...around"``) failed, the
matcher gave up, and every remaining word fell into the unbounded catch-all
branch — producing a single 46-char line. At 4K that line is too wide, so
libass wraps it onto a second physical row that overlaps the slot below
(two lines of lyrics rendered on top of each other).
"""
import logging

from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word


def _segment_from_text(text: str, words_text=None) -> LyricsSegment:
    words_text = words_text or text.split()
    words = []
    t = 0.0
    for i, w in enumerate(words_text):
        words.append(Word(id=f"w{i}", text=w, start_time=t, end_time=t + 0.3))
        t += 0.35
    return LyricsSegment(id="s", text=text, words=words, start_time=0.0, end_time=t)


def test_semicolon_segment_never_exceeds_max_line_length():
    resizer = SegmentResizer(max_line_length=36, logger=logging.getLogger("test"))
    seg = _segment_from_text(
        'Well the word got around; they said "Steph, she\'s the real one,"',
        words_text=["Well", "the", "word", "got", "around;", "they", "said", '"Steph,', "she's", "the", "real", "one,\""],
    )

    result = resizer.resize_segments([seg])

    # A single word longer than the limit is allowed on its own line (it cannot be
    # split further); only multi-word lines must respect max_line_length.
    too_long = [s.text for s in result if len(s.text) > resizer.max_line_length and len(s.words) > 1]
    assert not too_long, f"Lines exceeding {resizer.max_line_length} chars: {too_long}"


def test_dash_segment_never_exceeds_max_line_length():
    """The word-assignment desync is a class of bug, not specific to ';'. A
    space-dash-space clause break attaches the dash to the preceding word token
    ('around -'), so any character-position split orphans it and dumps the rest
    into the catch-all. The catch-all must still respect max_line_length."""
    resizer = SegmentResizer(max_line_length=36, logger=logging.getLogger("test"))
    seg = _segment_from_text(
        'Well the word got around - they said "Steph, she\'s the real one,"',
        words_text=["Well", "the", "word", "got", "around -", "they", "said", '"Steph,', "she's", "the", "real", "one,\""],
    )

    result = resizer.resize_segments([seg])

    # A single word longer than the limit is allowed on its own line (it cannot be
    # split further); only multi-word lines must respect max_line_length.
    too_long = [s.text for s in result if len(s.text) > resizer.max_line_length and len(s.words) > 1]
    assert not too_long, f"Lines exceeding {resizer.max_line_length} chars: {too_long}"


def test_single_word_longer_than_limit_is_emitted_alone():
    """A word that is itself longer than max_line_length cannot be split, so it is
    emitted on its own line. This is the one allowed way to exceed the limit."""
    resizer = SegmentResizer(max_line_length=10, logger=logging.getLogger("test"))
    seg = _segment_from_text(
        "supercalifragilisticexpialidocious is long",
        words_text=["supercalifragilisticexpialidocious", "is", "long"],
    )

    result = resizer.resize_segments([seg])

    over = [s for s in result if len(s.text) > resizer.max_line_length]
    assert len(over) == 1
    assert over[0].text == "supercalifragilisticexpialidocious"
    assert len(over[0].words) == 1


def test_split_preserves_all_words_in_order():
    resizer = SegmentResizer(max_line_length=36, logger=logging.getLogger("test"))
    original_words = ["Well", "the", "word", "got", "around;", "they", "said", '"Steph,', "she's", "the", "real", "one,\""]
    seg = _segment_from_text(
        'Well the word got around; they said "Steph, she\'s the real one,"',
        words_text=original_words,
    )

    result = resizer.resize_segments([seg])

    out_words = [w.text for s in result for w in s.words]
    assert out_words == original_words, f"Words lost/reordered: {out_words}"
