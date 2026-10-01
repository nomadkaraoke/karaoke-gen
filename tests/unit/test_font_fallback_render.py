"""Title/end cards and portrait headers must render non-Latin titles with real glyphs.

Job 5710831e (Omer Adam – "שני משוגעים") shipped a title card of "?" boxes: the theme
font (Avenir Next) has no Hebrew glyphs and PIL, unlike libass, doesn't fall back.
These tests use real fonts (fonts-noto-core / fonts-noto-cjk in CI and prod) and check
rendered pixels, not just which path was chosen.
"""
import os
import shutil

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont, features
from unittest.mock import MagicMock

from karaoke_gen.portrait.background import PortraitBrandConfig, _load_font
from karaoke_gen.utils.font_fallback import missing_codepoints, resolve_font_for_text
from karaoke_gen.video_generator import VideoGenerator

IN_CI = os.environ.get("CI", "").lower() == "true"
THEME_FONT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "karaoke_gen", "resources", "AvenirNext-Bold.ttf"))

TITLES = {
    "hebrew": "שני משוגעים",
    "arabic": "كيف حالك",
    "chinese": "青花瓷",
    "japanese": "宇多田ヒカル",
    "korean": "방탄소년단",
    "mixed": "Omer Adam - שני משוגעים",
}


def skip_unless_ci(reason):
    if IN_CI:
        pytest.fail(f"precondition failed in CI: {reason}")
    pytest.skip(reason)


@pytest.fixture(autouse=True)
def _need_fontconfig():
    if not shutil.which("fc-match"):
        skip_unless_ci("fontconfig (fc-match) not installed")


def _notdef_mask(font: ImageFont.FreeTypeFont) -> bytes:
    # U+10FFFD is a private-use noncharacter no font maps -> renders .notdef
    return bytes(font.getmask("\U0010FFFD"))


def _assert_no_tofu(font_path: str, text: str):
    font = ImageFont.truetype(font_path, 80)
    notdef = _notdef_mask(font)
    tofu = [ch for ch in text if not ch.isspace() and bytes(font.getmask(ch)) == notdef]
    assert not tofu, f"{os.path.basename(font_path)} draws .notdef boxes for {tofu!r}"


@pytest.mark.parametrize("script", list(TITLES))
def test_fallback_font_covers_title(script):
    text = TITLES[script]
    assert missing_codepoints(THEME_FONT, text), "theme font should lack these glyphs (test premise)"
    chosen = resolve_font_for_text(THEME_FONT, text)
    if chosen == THEME_FONT:
        skip_unless_ci(f"no installed font covers {script}")
    assert not missing_codepoints(chosen, text)
    _assert_no_tofu(chosen, text)


def test_latin_title_keeps_theme_font():
    assert resolve_font_for_text(THEME_FONT, "Omer Adam") == THEME_FONT
    assert resolve_font_for_text(THEME_FONT, "Café del Mar") == THEME_FONT


def _generator():
    return VideoGenerator(logger=MagicMock(), ffmpeg_base_command="ffmpeg",
                          render_bounding_boxes=False, output_png=True, output_jpg=True)


def _render_title(title: str) -> np.ndarray:
    """Render ``title`` through the real title-card path; return the ink mask."""
    img = Image.new("RGB", (3840, 2160), "black")
    fmt = {
        "title_region": "370, 200, 3100, 480", "title_color": "#ffffff", "title_gradient": None,
        "artist_region": None, "extra_text": None,
    }
    gen = _generator()
    gen._render_all_text(ImageDraw.Draw(img), THEME_FONT, title, None, fmt, False)
    return np.asarray(img.convert("L")) > 128


@pytest.mark.parametrize("script", ["hebrew", "arabic", "korean"])
def test_title_card_uses_a_covering_font(script):
    gen = _generator()
    used_fonts = []
    real_render = gen._render_text_in_region

    def spy(draw, text, font_path, *args, **kwargs):
        used_fonts.append(font_path)
        return real_render(draw, text, font_path, *args, **kwargs)

    gen._render_text_in_region = spy
    img = Image.new("RGB", (3840, 2160), "black")
    fmt = {"title_region": "370, 200, 3100, 480", "title_color": "#ffffff", "title_gradient": None,
           "artist_region": None, "extra_text": None}
    gen._render_all_text(ImageDraw.Draw(img), THEME_FONT, TITLES[script], None, fmt, False)
    assert len(used_fonts) == 1
    if used_fonts[0] == THEME_FONT:
        skip_unless_ci(f"no installed font covers {script}")
    _assert_no_tofu(used_fonts[0], TITLES[script])


def _split_at_widest_gap(mask: np.ndarray):
    """Column extents of the two words either side of the widest ink gap (the space)."""
    cols = np.where(mask.any(axis=0))[0]
    gaps = np.diff(cols)
    i = int(np.argmax(gaps))
    return (int(cols[0]), int(cols[i])), (int(cols[i + 1]), int(cols[-1]))


def test_hebrew_title_card_reads_right_to_left():
    """"שני משוגעים": the first word (3 letters) must be drawn on the RIGHT of the second
    (7 letters). Needs Pillow's raqm layout (libraqm + fribidi), present in CI and prod."""
    if not features.check("raqm"):
        skip_unless_ci("Pillow without raqm cannot lay out RTL text")
    if resolve_font_for_text(THEME_FONT, TITLES["hebrew"]) == THEME_FONT:
        skip_unless_ci("no installed Hebrew font")
    (l0, l1), (r0, r1) = _split_at_widest_gap(_render_title(TITLES["hebrew"]))
    left_width, right_width = l1 - l0, r1 - r0
    assert right_width < left_width * 0.6, (
        f"first word שני should be the narrow one on the right; widths left={left_width} right={right_width}"
    )


@pytest.mark.parametrize("script", ["hebrew", "chinese"])
def test_portrait_header_font_covers_title(script):
    cfg = PortraitBrandConfig(font_path=THEME_FONT)
    font = _load_font(cfg, 60, TITLES[script])
    if font.path == THEME_FONT:
        skip_unless_ci(f"no installed font covers {script}")
    _assert_no_tofu(font.path, TITLES[script])


def test_latin_only_noto_font_is_not_kept_for_cjk_title(tmp_path):
    """A theme font named Noto*-Bold that is Latin-only must not pass for a CJK font."""
    latin_noto = tmp_path / "NotoSans-Bold.ttf"
    shutil.copy(THEME_FONT, latin_noto)  # Latin-only font under a "noto" name
    gen = _generator()
    gen._cjk_font_path = "/fonts/NotoSansCJK-Bold.ttc"
    assert gen._get_font_path_for_text(str(latin_noto), TITLES["chinese"]) == "/fonts/NotoSansCJK-Bold.ttc"
