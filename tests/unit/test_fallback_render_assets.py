"""
Fallback audit (Theme 4): a *configured* branded render asset that is missing
must fail loudly rather than silently substituting a different font/background on
a paid deliverable. Mirrors the existing background-image check in
lyrics_transcriber/output/video.VideoGenerator.__init__ (which already raises).

Scope: only the cleanest "configured-but-missing → raise" cases were changed
(4.1 final-MP4 karaoke font dir, 4.3 themed title/end background). Font
*substitution* paths (title/end font, CDG arial, measurement font) were left as
graceful fallbacks because they overlap with legitimate CJK font handling.
"""
import pytest
from unittest.mock import MagicMock

from karaoke_gen.lyrics_transcriber.output.video import VideoGenerator as AssVideoGenerator
from karaoke_gen.video_generator import VideoGenerator as ScreenVideoGenerator


# ---- 4.1: final-MP4 karaoke font dir ----

def _make_ass_generator(styles):
    """Build just enough of the ASS VideoGenerator to exercise _build_ass_filter."""
    gen = AssVideoGenerator.__new__(AssVideoGenerator)
    gen.styles = styles
    gen.logger = MagicMock()
    return gen


def test_build_ass_filter_raises_when_configured_font_missing():
    """A theme font that's set but missing must raise, not silently drop fontsdir."""
    gen = _make_ass_generator({"karaoke": {"font_path": "/nonexistent/Branded.ttf"}})
    with pytest.raises(FileNotFoundError, match="Karaoke font not found"):
        gen._build_ass_filter("/tmp/lyrics.ass")


def test_build_ass_filter_ok_when_no_font_configured():
    """No font configured is legitimate — no fontsdir, no raise."""
    gen = _make_ass_generator({"karaoke": {}})
    result = gen._build_ass_filter("/tmp/lyrics.ass")
    assert "fontsdir" not in result
    assert result.startswith("ass=")


# ---- 4.3: themed title/end background image ----

def _make_screen_generator():
    gen = ScreenVideoGenerator.__new__(ScreenVideoGenerator)
    gen.logger = MagicMock()
    return gen


def test_create_background_raises_when_configured_image_missing():
    """A background image that's set but missing must raise, not silently use a flat color."""
    gen = _make_screen_generator()
    fmt = {"background_image": "/nonexistent/bg.png", "background_color": "black"}
    with pytest.raises(FileNotFoundError, match="Background image not found"):
        gen._create_background(fmt, (100, 100))


def test_create_background_uses_color_when_no_image_configured():
    """No background image configured is legitimate — flat color, no raise."""
    gen = _make_screen_generator()
    fmt = {"background_image": None, "background_color": "#000000"}
    bg = gen._create_background(fmt, (10, 10))
    assert bg.size == (10, 10)
