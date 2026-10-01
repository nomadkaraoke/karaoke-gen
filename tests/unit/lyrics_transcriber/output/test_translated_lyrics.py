"""Translated lyrics: a smaller static translation row beneath each lyric line."""
import os
import re

import pytest

from karaoke_gen.lyrics_transcriber.output.ass.config import translation_layout
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_line import LyricsLine
from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer, split_text_proportionally
from karaoke_gen.lyrics_transcriber.output.subtitles import TRANSLATION_OPACITY, SubtitlesGenerator
from karaoke_gen.lyrics_transcriber.output.translations import apply_translations, load_and_apply_translations
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.portrait.renderer import PortraitLayout, _computed_top_padding, build_portrait_ass
from karaoke_gen.portrait.wrap import _merge
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE

FONT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "karaoke_gen", "resources", "AvenirNext-Bold.ttf"
))
W, H = 1920, 1080
FONT = 120


def seg(text, start, translation=None, seg_id=None, step=0.5):
    words = [
        Word(id=f"{seg_id or start}-{i}", text=w, start_time=start + i * step, end_time=start + (i + 1) * step - 0.05)
        for i, w in enumerate(text.split())
    ]
    return LyricsSegment(
        id=seg_id or f"s{start}", text=text, words=words, start_time=words[0].start_time,
        end_time=words[-1].end_time, translation=translation,
    )


def generate(tmp_path, segments, karaoke_overrides=None):
    karaoke = dict(DEFAULT_KARAOKE_STYLE)
    karaoke["font_path"] = FONT_PATH  # real metrics, so width fitting is measured
    karaoke.update(karaoke_overrides or {})
    gen = SubtitlesGenerator(
        output_dir=str(tmp_path), video_resolution=(W, H), font_size=FONT, line_height=FONT,
        styles={"karaoke": karaoke},
    )
    path = gen.generate_ass(segments, "test", None)
    with open(path, encoding="utf-8") as f:
        return f.read()


def dialogues(ass_text):
    return [line for line in ass_text.splitlines() if line.startswith("Dialogue:")]


def positions(lines):
    return [tuple(int(v) for v in re.search(r"\\pos\((\d+),(\d+)\)", d).groups()) for d in lines]


# ---- data model ----


def test_segment_translation_round_trips_through_dict():
    s = seg("hello world", 1.0, translation="hola mundo")
    d = s.to_dict()
    assert d["translation"] == "hola mundo"
    assert LyricsSegment.from_dict(d).translation == "hola mundo"


def test_segment_without_translation_omits_key():
    assert "translation" not in seg("hello world", 1.0).to_dict()


def test_apply_translations_matches_by_id_then_text():
    a, b, c = seg("first line", 1, seg_id="a"), seg("Second  Line", 3, seg_id="b"), seg("third", 5, seg_id="c")
    applied = apply_translations([a, b, c], {"lines": [
        {"segment_id": "a", "text": "first line", "translation": "primera"},
        {"segment_id": "zzz", "text": "second line", "translation": "segunda"},
        {"segment_id": "c", "text": "third", "translation": "  "},
    ]})
    assert applied == 2
    assert (a.translation, b.translation, c.translation) == ("primera", "segunda", None)


def test_load_and_apply_missing_file_is_noop(tmp_path):
    s = seg("hello", 1)
    assert load_and_apply_translations([s], str(tmp_path / "nope.json")) == 0
    assert s.translation is None


# ---- resizer ----


@pytest.mark.parametrize("text,weights,expected", [
    ("uno dos tres cuatro", [1, 1], ["uno dos", "tres cuatro"]),
    ("我爱你我爱你", [1, 1], ["我爱你", "我爱你"]),
    ("solo", [3, 1], ["solo", ""]),
    ("a b c", [1], ["a b c"]),
])
def test_split_text_proportionally(text, weights, expected):
    assert split_text_proportionally(text, weights) == expected


def test_split_text_proportionally_keeps_all_text():
    text = "Las luces se apagan y aquí está mi canción, la noche es larga y sigo aquí"
    parts = split_text_proportionally(text, [29, 32, 10])
    assert " ".join(p for p in parts if p) == text


def test_resizer_keeps_translation_on_short_line():
    out = SegmentResizer(max_line_length=40).resize_segments([seg("short line", 1, translation="línea corta")])
    assert [s.translation for s in out] == ["línea corta"]


def test_resizer_shares_translation_across_split_pieces():
    long = seg("Lights go down and here's my song, the night is long and I'm still here", 1,
               translation="Las luces se apagan y aquí está mi canción, la noche es larga y sigo aquí")
    out = SegmentResizer(max_line_length=40).resize_segments([long])
    assert len(out) == 2
    assert all(s.translation for s in out)
    assert " ".join(s.translation for s in out) == long.translation


def test_portrait_merge_joins_translations():
    merged = _merge(seg("No,", 1, translation="No,"), seg("not now", 2, translation="ahora no"))
    assert merged.translation == "No, ahora no"
    assert _merge(seg("a", 1), seg("b", 2)).translation is None


# ---- ASS layout ----


def test_no_translations_keeps_classic_layout(tmp_path):
    ass = generate(tmp_path, [seg(f"line number {i}", 1 + 3 * i) for i in range(4)])
    assert "Karaoke.Translation" not in ass
    first_screen = dialogues(ass)[1:5]  # after the lead-in rectangle
    assert len(first_screen) == 4


def test_translation_rows_render_beneath_each_line(tmp_path):
    segs = [seg(f"line number {i}", 1 + 3 * i, translation=f"línea número {i}") for i in range(3)]
    ass = generate(tmp_path, segs)
    assert "Style: Karaoke.Translation" in ass
    events = dialogues(ass)
    lyric = [d for d in events if "\\kf" in d and "\\p1" not in d]
    translated = [d for d in events if "Karaoke.Translation" in d]
    assert len(lyric) == 3 and len(translated) == 3
    layout = translation_layout(FONT, FONT, DEFAULT_KARAOKE_STYLE)
    for (lx, ly), (tx, ty), d in zip(positions(lyric), positions(translated), translated):
        assert tx == lx == W // 2
        assert ty == ly + FONT + layout.gap
        assert "\\k" not in d  # static text, no karaoke wipe
    # Slots are taller to fit the row
    ys = [y for _, y in positions(lyric)]
    assert ys[1] - ys[0] == layout.slot_height
    assert translated[0].endswith("línea número 0")


def test_translation_layout_shows_three_lines_per_screen(tmp_path):
    segs = [seg(f"line number {i}", 1 + 3 * i, translation=f"línea {i}") for i in range(4)]
    ass = generate(tmp_path, segs)
    lyric = [d for d in dialogues(ass) if "\\kf" in d and "\\p1" not in d]
    ys = [y for _, y in positions(lyric)]
    assert ys[3] == ys[0]  # 4th line reuses the first slot: 3 slots per screen


def _ass_alpha(colour: str) -> int:
    """Opacity (255 = opaque) of an ASS ``&HAABBGGRR`` colour."""
    return 255 - int(colour.strip()[2:4], 16)


def test_translation_style_uses_faded_unsung_colour_and_smaller_font(tmp_path):
    ass = generate(tmp_path, [seg("hello there", 1, translation="hola")])
    style = next(line for line in ass.splitlines() if line.startswith("Style: Karaoke.Translation"))
    fields = style.split(",")
    assert int(fields[3]) == translation_layout(FONT, FONT).translation_font_size
    lyric = next(line for line in ass.splitlines() if line.startswith("Style: ") and "Translation" not in line).split(",")
    # Translation colour = the lyric's unsung (secondary) colour, same RGB...
    assert fields[4].strip()[4:] == lyric[5].strip()[4:]
    # ...at 70% of its opacity, so it reads as secondary to the sung line. Outline and
    # shadow fade by the same factor (no dark halo around faded text).
    for ours, theirs in ((fields[4], lyric[5]), (fields[6], lyric[6]), (fields[7], lyric[7])):
        assert _ass_alpha(ours) == round(_ass_alpha(theirs) * TRANSLATION_OPACITY)
    assert _ass_alpha(fields[4]) < 255


def test_theme_can_override_translation_colour_and_size(tmp_path):
    ass = generate(tmp_path, [seg("hello there", 1, translation="hola")],
                   {"translation_color": "255, 0, 0, 255", "translation_font_size": 40})
    style = next(line for line in ass.splitlines() if line.startswith("Style: Karaoke.Translation"))
    assert style.split(",")[3] == "40"
    assert "&H000000FF" in style.split(",")[4]


def test_lines_without_translation_get_no_row(tmp_path):
    ass = generate(tmp_path, [seg("hello there", 1, translation="hola"), seg("oh oh", 4)])
    assert len([d for d in dialogues(ass) if "Karaoke.Translation" in d]) == 1


def test_overlong_translation_is_scaled_to_fit(tmp_path):
    long = " ".join(["palabra"] * 40)
    ass = generate(tmp_path, [seg("hello there", 1, translation=long)])
    translated = next(d for d in dialogues(ass) if "Karaoke.Translation" in d)
    scale = int(re.search(r"\\fscx(\d+)", translated).group(1))
    assert 1 <= scale < 100


def test_translation_text_cannot_inject_ass_tags(tmp_path):
    ass = generate(tmp_path, [seg("hello there", 1, translation="hola {\\b1} \\N mundo\nfin")])
    translated = next(d for d in dialogues(ass) if "Karaoke.Translation" in d)
    text = translated.split("}")[-1]
    assert "{" not in text and "\\" not in text
    assert text == "hola ( b1) N mundo fin"


def test_rtl_translation_is_plain_text(tmp_path):
    ass = generate(tmp_path, [seg("hello there", 1, translation="שלום לך")])
    translated = next(d for d in dialogues(ass) if "Karaoke.Translation" in d)
    assert translated.endswith("שלום לך")
    assert "\\frz" not in translated


def test_case_transform_applies_to_translation(tmp_path):
    ass = generate(tmp_path, [seg("hello there", 1, translation="hola amigo")], {"text_case_transform": "uppercase"})
    assert next(d for d in dialogues(ass) if "Karaoke.Translation" in d).endswith("HOLA AMIGO")


def test_split_rows_balances_words():
    assert LyricsLine._split_rows("one two three four", 2) == ["one two", "three four"]
    assert LyricsLine._split_rows("我爱你我爱你", 2) == ["我爱你", "我爱你"]
    assert LyricsLine._split_rows("solo", 2) == ["solo"]


def test_two_row_layout_wraps_long_translation(tmp_path):
    long = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis"
    ass = generate(tmp_path, [seg("hello there", 1, translation=long)], {"translation_max_rows": 2})
    translated = next(d for d in dialogues(ass) if "Karaoke.Translation" in d)
    assert "\\N" in translated


# ---- portrait ----


def test_portrait_centres_taller_translation_block():
    layout = PortraitLayout()
    tl = translation_layout(layout.font_size, layout.line_height)
    padded = _computed_top_padding(layout, tl.slot_height, 3)
    assert padded != _computed_top_padding(layout)
    # First line lands so the 3-slot block is centred at block_center_frac
    first = padded + (layout.height - 3 * tl.slot_height - padded) // 4
    assert abs(first + 3 * tl.slot_height / 2 - layout.height * layout.block_center_frac) < 3


def test_portrait_ass_includes_translations(tmp_path):
    class CR:
        corrected_segments = [seg("I will always love you", 1, translation="Siempre te amaré")]

    path = build_portrait_ass(CR(), {}, None, str(tmp_path), "p", PortraitLayout())
    with open(path, encoding="utf-8") as f:
        ass = f.read()
    rows = [d for d in dialogues(ass) if "Karaoke.Translation" in d]
    assert rows and " ".join(r.split("}")[-1] for r in rows) == "Siempre te amaré"


def test_hebrew_lyric_with_english_translation_mixes_directions(tmp_path):
    """RTL lyric keeps its RTL fill tags; the LTR translation beneath gets none."""
    ass = generate(tmp_path, [seg("כמו איזה שני משוגעים", 1, translation="Like two crazy people")])
    events = dialogues(ass)
    lyric = next(d for d in events if "\\kf" in d and "\\p1" not in d)
    translated = next(d for d in events if "Karaoke.Translation" in d)
    assert "\\frz" in lyric
    assert "\\frz" not in translated and translated.endswith("Like two crazy people")
    style = next(line for line in ass.splitlines() if line.startswith("Style: Karaoke.Translation"))
    assert style.rstrip().endswith(",-1")  # Encoding=-1: libass picks direction per line


def test_split_hebrew_lyric_shares_english_translation_in_reading_order():
    long = seg("כמו איזה שני משוגעים בחוף הים בלילה אנחנו רוקדים עד הבוקר", 1,
               translation="Like two crazy people on the beach at night we dance until the morning")
    out = SegmentResizer(max_line_length=30).resize_segments([long])
    assert len(out) >= 2
    assert " ".join(s.translation for s in out if s.translation) == long.translation
    assert out[0].translation.startswith("Like")


def test_thai_split_never_strands_a_combining_mark():
    import unicodedata

    text = "ฉันรักเธอมากที่สุดในโลกนี้"
    for n in (2, 3, 4):
        parts = split_text_proportionally(text, [1] * n)
        assert "".join(parts) == text
        for part in parts:
            assert part and not unicodedata.category(part[0]).startswith("M")


def test_missing_font_estimates_width_so_long_translation_still_shrinks(tmp_path):
    karaoke = dict(DEFAULT_KARAOKE_STYLE)
    karaoke["font_path"] = ""  # unresolved: PIL's tiny default font would under-measure
    gen = SubtitlesGenerator(output_dir=str(tmp_path), video_resolution=(1080, 1920), font_size=88,
                             line_height=118, styles={"karaoke": karaoke})
    with open(gen.generate_ass([seg("hello there", 1, translation=" ".join(["palabra"] * 15))], "t", None),
              encoding="utf-8") as f:
        translated = next(d for d in dialogues(f.read()) if "Karaoke.Translation" in d)
    assert re.search(r"\\fscx(\d+)", translated)


def test_instrumental_card_position_unchanged_by_translations(tmp_path):
    def instrumental_y(segments):
        ass = generate(tmp_path, segments)
        card = next(d for d in dialogues(ass) if "INSTRUMENTAL" in d)
        return positions([card])[0][1] if "\\pos" in card else card

    plain = [seg("first line", 1), seg("after the break", 40)]
    translated = [seg("first line", 1, translation="primera"), seg("after the break", 40, translation="después")]
    assert instrumental_y(plain) == instrumental_y(translated)


def test_japanese_split_prefers_punctuation_over_mid_word():
    # Seen in prod (job ca2e881a): 無駄 ("waste") was cut in half across two rows
    parts = split_text_proportionally("ねえ、おかしいでしょ、無駄な時間を省いてあげる", [22, 32])
    assert parts == ["ねえ、おかしいでしょ、", "無駄な時間を省いてあげる"]


def test_japanese_split_falls_back_to_a_phrase_boundary():
    parts = split_text_proportionally("自分の生活をしようとしてるのに", [1, 1])
    assert parts == ["自分の生活を", "しようとしてるのに"]  # after the particle を, not inside しよう
