"""Render translated-lyrics karaoke with real ffmpeg/libass: the translation row must
render (every glyph found, any script) as a smaller centred band beneath its line.

Translation rows use the unsung colour, so they classify as "unsung" pixels.
Run in Linux like prod with scripts/run-render-tests-linux.sh.
"""
import os
import re

import pytest

from .ass_render_harness import (
    WIDTH,
    ffmpeg_has_libass,
    generate_ass,
    lyric_lines,
    make_segment,
    render_frames,
    word_mid,
)

IN_CI = os.environ.get("CI", "").lower() == "true"

if not ffmpeg_has_libass():
    if IN_CI:
        raise RuntimeError("ffmpeg with the libass 'ass' filter is required for render tests in CI")
    pytest.skip("ffmpeg with libass not available", allow_module_level=True)

ENGLISH = ("every", "night", "I", "sing", "along")
HEBREW = ("כמו", "איזה", "שני", "משוגעים", "בחוף")
ARABIC = ("كيف", "حالك", "يا", "صديقي", "العزيز")

# name -> (lyric words, lyric is RTL, translation). Mixed directions on purpose: a
# Hebrew song with an English translation must lay the lyric out (and highlight it)
# right-to-left while the translation beneath reads left-to-right, and vice versa.
CASES = {
    "spanish": (ENGLISH, False, "Cada noche canto contigo"),
    "hebrew": (ENGLISH, False, "כל לילה אני שר איתך"),
    "arabic": (ENGLISH, False, "كل ليلة أغني معك"),
    "japanese": (ENGLISH, False, "毎晩 一緒に歌う"),
    "chinese": (ENGLISH, False, "每天晚上我都跟着唱"),
    "korean": (ENGLISH, False, "매일 밤 함께 노래해"),
    "hebrew_lyric_english": (HEBREW, True, "Like two crazy people on the beach"),
    "hebrew_lyric_arabic": (HEBREW, True, "مثل مجنونين على الشاطئ"),
    "arabic_lyric_english": (ARABIC, True, "How are you, my dear friend"),
}
TRANSLATIONS = CASES
NEEDS_FONTCONFIG_FALLBACK = {"japanese", "chinese", "korean"}
CENTRE_TOL = 12
T = word_mid(1)


def _provider(log: str) -> str:
    m = re.search(r"Using font provider (\w+)", log)
    return m.group(1) if m else "unknown"


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    out = {}
    for name, (words, _rtl, translation) in CASES.items():
        seg = make_segment(words)
        seg.translation = translation
        ass = generate_ass(tmp_path_factory.mktemp(name), [seg])
        out[name] = render_frames(ass, [T])
    return out


@pytest.mark.parametrize("name", list(CASES))
def test_translation_row_renders_beneath_line(renders, name):
    result = renders[name]
    if name in NEEDS_FONTCONFIG_FALLBACK and _provider(result.log) != "fontconfig":
        if IN_CI:
            pytest.fail(f"{name} needs the libass fontconfig provider")
        pytest.skip(f"{name} glyph fallback needs libass fontconfig provider")
    assert result.missing_glyphs == [], f"libass found no glyph for {result.missing_glyphs}"

    bands = lyric_lines(result.frames[T])
    assert len(bands) == 2, f"expected lyric line + translation row, got {len(bands)} bands"
    lyric, translation = bands
    assert len(lyric.sung_cols) > 0  # the lyric line is part-sung
    assert len(translation.sung_cols) == 0  # the translation is never highlighted
    assert translation.top > lyric.bottom
    assert (translation.bottom - translation.top) < (lyric.bottom - lyric.top)
    assert abs(translation.center_x - WIDTH / 2) < CENTRE_TOL


@pytest.mark.parametrize("name", [n for n, (_w, rtl, _t) in CASES.items() if rtl])
def test_rtl_lyric_still_highlights_from_the_right(renders, name):
    """The translation row must not disturb the RTL lyric's right-to-left fill."""
    lyric = lyric_lines(renders[name].frames[T])[0]
    assert len(lyric.sung_cols) > 0 and len(lyric.unsung_cols) > 0
    # Sung part sits on the right (reading start) of an RTL line
    assert lyric.sung_cols.mean() > lyric.unsung_cols.mean()
