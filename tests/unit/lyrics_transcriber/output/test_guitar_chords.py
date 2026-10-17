"""Guitar chords: chord symbols above the words they land on + a now/next chord box."""
import os
import re

from PIL import ImageFont

from karaoke_gen.lyrics_transcriber.output.ass.chords import (
    ChordChange,
    assign_chords_to_segments,
    chord_at,
    clean_chords,
    place_chords,
    word_index_for_time,
)
from karaoke_gen.lyrics_transcriber.output.ass.config import chord_layout
from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE

FONT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "karaoke_gen", "resources", "AvenirNext-Bold.ttf"
))
W, H = 1920, 1080
FONT = 120


def seg(text, start, seg_id=None, step=0.5):
    words = [
        Word(id=f"{seg_id or start}-{i}", text=w, start_time=start + i * step, end_time=start + (i + 1) * step - 0.05)
        for i, w in enumerate(text.split())
    ]
    return LyricsSegment(id=seg_id or f"s{start}", text=text, words=words,
                         start_time=words[0].start_time, end_time=words[-1].end_time)


def generate(tmp_path, segments, chords=None, karaoke_overrides=None):
    karaoke = dict(DEFAULT_KARAOKE_STYLE)
    karaoke["font_path"] = FONT_PATH
    karaoke.update(karaoke_overrides or {})
    gen = SubtitlesGenerator(output_dir=str(tmp_path), video_resolution=(W, H), font_size=FONT,
                             line_height=FONT, styles={"karaoke": karaoke})
    path = gen.generate_ass(segments, "test", None, chords=chords)
    with open(path, encoding="utf-8") as f:
        return f.read()


def dialogues(ass_text, style=None):
    lines = [line for line in ass_text.splitlines() if line.startswith("Dialogue:")]
    return [d for d in lines if style is None or f",{style}," in d]


def pos(d):
    return tuple(int(v) for v in re.search(r"\\pos\((\d+),(\d+)\)", d).groups())


# ---- chord timeline helpers ----


def test_clean_chords_drops_no_chord_and_merges_repeats():
    chords = clean_chords([
        ChordChange(4, 6, "C"), ChordChange(0, 2, "G"), ChordChange(2, 4, "G"),
        ChordChange(6, 7, "N"), ChordChange(7, 9, "D7"),
    ])
    assert [(c.start, c.end, c.label) for c in chords] == [(0, 4, "G"), (4, 6, "C"), (7, 9, "D7")]


def test_word_index_snaps_a_pickup_strum_to_the_next_word():
    s = seg("one two three", 10.0)  # words at 10.0, 10.5, 11.0
    assert word_index_for_time(s, 10.3) == 1  # 0.2s before "two" -> over "two"
    assert word_index_for_time(s, 10.2) == 0
    assert word_index_for_time(s, 5.0) == 0  # before the line -> first word
    assert word_index_for_time(s, 20.0) == 2


def test_assign_puts_changes_on_the_line_being_sung():
    a, b = seg("first line here", 10.0, "a"), seg("second line", 20.0, "b")
    chords = [ChordChange(9.8, 10.9, "G"), ChordChange(10.9, 15.0, "C"),
              ChordChange(15.0, 20.4, "D"), ChordChange(20.4, 30, "G")]
    by_seg = assign_chords_to_segments([a, b], chords)
    assert [c.label for c in by_seg["a"]] == ["G", "C"]
    # D started between the lines and is still ringing when "second" is sung: repeated over it
    assert [c.label for c in by_seg["b"]] == ["D", "G"]


def test_assign_skips_the_ringing_chord_when_a_change_lands_on_the_first_word():
    a = seg("only line", 10.0, "a")
    by_seg = assign_chords_to_segments([a], [ChordChange(2, 10.1, "D"), ChordChange(10.1, 12, "G")])
    assert [c.label for c in by_seg["a"]] == ["G"]


def test_place_chords_never_overlaps_quick_changes():
    s = seg("hello world", 0.0)
    measure = lambda text: 10 * len(text)  # 10px per character
    placed = place_chords(s, [ChordChange(0.0, 0.1, "G"), ChordChange(0.1, 0.4, "Cmaj7"),
                              ChordChange(0.5, 1, "D")], ["hello", "world"], 100, measure, measure, 5)
    xs = [p.x for p in placed]
    assert xs[0] == 100  # over "hello"
    assert xs[1] == 100 + 10 + 5  # pushed right of "G" (same word)
    # "world" starts at 160, but Cmaj7 runs to 165 (+5 spacing): pushed clear of it
    assert [p.word_index for p in placed] == [0, 0, 1]
    assert xs[2] == xs[1] + measure("Cmaj7") + 5


def test_chord_at():
    chords = [ChordChange(0, 2, "G"), ChordChange(2, 4, "C")]
    assert chord_at(chords, 2.5).label == "C"
    assert chord_at(chords, 9) is None


def test_chord_layout_reserves_a_row_above_the_line():
    layout = chord_layout(120, 120)
    assert layout.chord_font_size == 66
    assert layout.lyric_y_offset == round(66 * 1.05)
    assert layout.slot_height == 120 + layout.lyric_y_offset
    assert chord_layout(120, 120, {"chord_font_size": 50, "chord_max_visible_lines": 3}).max_visible_lines == 3


# ---- rendered ASS ----


def test_no_chords_leaves_the_classic_layout_untouched(tmp_path):
    segments = [seg("hello there world", 5.0, "a")]
    assert generate(tmp_path, segments) == generate(tmp_path, segments, chords=[])


def test_chord_symbols_sit_above_their_words(tmp_path):
    segments = [seg("hello there world", 5.0, "a")]
    chords = [ChordChange(0, 5.5, "G"), ChordChange(5.5, 9, "C")]
    ass = generate(tmp_path, segments, chords)
    assert "Style: Karaoke.Chord," in ass and "Style: Karaoke.ChordBox," in ass
    lyric = next(d for d in dialogues(ass) if "\\kf" in d and ",Karaoke.Chord" not in d)
    chord_rows = dialogues(ass, "Karaoke.Chord")
    assert [re.sub(r"\{[^}]*\}", "", d.split(",", 5)[-1]) for d in chord_rows] == ["G", "C"]
    lyric_x, lyric_y = pos(lyric)
    (g_x, g_y), (c_x, c_y) = pos(chord_rows[0]), pos(chord_rows[1])
    assert g_y == c_y < lyric_y  # chord row above the line
    assert lyric_y - g_y == chord_layout(FONT, FONT).lyric_y_offset
    # G sits on the centred line's left edge, C where "there" starts. Compare as a
    # fraction of the line width: the renderer scales PIL metrics to match libass.
    rendered_width = 2 * (lyric_x - g_x)
    font = ImageFont.truetype(FONT_PATH, FONT)
    expected = font.getlength("hello ") / font.getlength("hello there world")
    assert abs((c_x - g_x) / rendered_width - expected) < 0.01


def test_chord_lights_up_when_played(tmp_path):
    segments = [seg("hello there world", 5.0, "a")]
    ass = generate(tmp_path, segments, [ChordChange(5.5, 9, "C")])
    row = dialogues(ass, "Karaoke.Chord")[0]
    start = row.split(",")[2]  # H:MM:SS.cc
    h, m, sec = start.split(":")
    start_s = int(h) * 3600 + int(m) * 60 + float(sec)
    wait = int(re.search(r"\\k(\d+)\}\{\\kf", row).group(1))
    assert abs(start_s + wait / 100 - 5.5) < 0.02


def test_chord_box_shows_now_and_next(tmp_path):
    segments = [seg("hello there world", 5.0, "a")]
    ass = generate(tmp_path, segments, [ChordChange(0, 5.5, "G"), ChordChange(5.5, 9, "C")])
    box = dialogues(ass, "Karaoke.ChordBox")
    assert len(box) == 2
    assert box[0].endswith("next: C") and "}G\\N" in box[0]
    assert "next:" not in box[1]  # last chord


def test_chord_colour_comes_from_theme(tmp_path):
    segments = [seg("hello there world", 5.0, "a")]
    ass = generate(tmp_path, segments, [ChordChange(5.5, 9, "C")], {"chord_color": "10, 20, 30"})
    style = next(line for line in ass.splitlines() if line.startswith("Style: Karaoke.Chord,"))
    assert "&H001E140A" in style  # secondary (upcoming) colour = theme chord_color (BGR)
