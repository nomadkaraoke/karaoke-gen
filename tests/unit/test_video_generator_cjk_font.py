"""Unit tests for VideoGenerator CJK font fallback.

Tests the fix for job f8a36b60 where Chinese characters in the title card
rendered as question marks because Montserrat-Bold.ttf lacks CJK glyphs.
"""

import pytest
from unittest.mock import MagicMock, patch
from karaoke_gen.video_generator import VideoGenerator, _text_needs_cjk_font, _find_cjk_font


class TestTextNeedsCjkFont:
    """Test the _text_needs_cjk_font helper function."""

    def test_ascii_text(self):
        assert _text_needs_cjk_font("Hello World") is False

    def test_empty_string(self):
        assert _text_needs_cjk_font("") is False

    def test_none(self):
        assert _text_needs_cjk_font(None) is False

    def test_chinese_simplified(self):
        assert _text_needs_cjk_font("青花瓷") is True

    def test_chinese_traditional(self):
        assert _text_needs_cjk_font("周杰倫") is True

    def test_japanese_kanji(self):
        assert _text_needs_cjk_font("宇多田ヒカル") is True

    def test_japanese_hiragana(self):
        assert _text_needs_cjk_font("あいうえお") is True

    def test_japanese_katakana(self):
        assert _text_needs_cjk_font("カラオケ") is True

    def test_korean(self):
        assert _text_needs_cjk_font("방탄소년단") is True

    def test_mixed_ascii_and_cjk(self):
        assert _text_needs_cjk_font("Artist - 青花瓷") is True

    def test_accented_latin(self):
        assert _text_needs_cjk_font("Café del Mar") is False

    def test_cyrillic(self):
        assert _text_needs_cjk_font("Тату") is False

    def test_arabic(self):
        assert _text_needs_cjk_font("فيروز") is False

    def test_emoji(self):
        assert _text_needs_cjk_font("🎵 Music") is False


class TestGetFontPathForText:
    """Test the CJK font fallback logic in VideoGenerator."""

    @pytest.fixture
    def generator(self):
        return VideoGenerator(
            logger=MagicMock(),
            ffmpeg_base_command="ffmpeg",
            render_bounding_boxes=False,
            output_png=True,
            output_jpg=True,
        )

    def test_ascii_text_keeps_original_font(self, generator):
        result = generator._get_font_path_for_text("/path/to/Montserrat-Bold.ttf", "Hello World")
        assert result == "/path/to/Montserrat-Bold.ttf"

    def test_none_text_keeps_original_font(self, generator):
        result = generator._get_font_path_for_text("/path/to/Montserrat-Bold.ttf", None)
        assert result == "/path/to/Montserrat-Bold.ttf"

    @patch("karaoke_gen.video_generator._find_cjk_font", return_value="/usr/share/fonts/noto/NotoSansCJK-Bold.ttc")
    def test_cjk_text_uses_fallback_font(self, mock_find, generator):
        result = generator._get_font_path_for_text("/path/to/Montserrat-Bold.ttf", "周杰倫")
        assert result == "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc"

    @patch("karaoke_gen.video_generator._find_cjk_font", return_value="")
    def test_cjk_text_falls_back_to_original_when_no_cjk_font(self, mock_find, generator):
        result = generator._get_font_path_for_text("/path/to/Montserrat-Bold.ttf", "周杰倫")
        assert result == "/path/to/Montserrat-Bold.ttf"

    def test_noto_font_not_overridden(self, generator):
        """If the configured font is already a Noto/CJK font, don't override."""
        result = generator._get_font_path_for_text(
            "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc", "周杰倫"
        )
        assert result == "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc"

    @patch("karaoke_gen.video_generator._find_cjk_font", return_value="/usr/share/fonts/noto/NotoSansCJK-Bold.ttc")
    def test_cjk_font_cached_across_calls(self, mock_find, generator):
        generator._get_font_path_for_text("/path/to/font.ttf", "周杰倫")
        generator._get_font_path_for_text("/path/to/font.ttf", "方大同")
        # _find_cjk_font should only be called once due to caching
        mock_find.assert_called_once()


class TestRenderAllTextFontFallback:
    """Test that _render_all_text uses CJK font fallback per text element."""

    @pytest.fixture
    def generator(self):
        gen = VideoGenerator(
            logger=MagicMock(),
            ffmpeg_base_command="ffmpeg",
            render_bounding_boxes=False,
            output_png=True,
            output_jpg=True,
        )
        gen._cjk_font_path = "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc"
        return gen

    @patch.object(VideoGenerator, "_render_text_in_region")
    def test_cjk_title_gets_cjk_font_while_ascii_artist_keeps_original(self, mock_render, generator):
        """When title is CJK but artist is ASCII, each gets the right font."""
        draw = MagicMock()
        format_config = {
            "font": "Montserrat-Bold.ttf",
            "title_region": "370, 200, 3100, 480",
            "title_color": "#ffffff",
            "title_gradient": None,
            "artist_region": "370, 700, 3100, 480",
            "artist_color": "#ffdf6b",
            "artist_gradient": None,
            "extra_text": None,
        }

        generator._render_all_text(
            draw, "/path/Montserrat-Bold.ttf", "青花瓷", "Jay Chou", format_config, False
        )

        # Should have 2 calls: title and artist
        assert mock_render.call_count == 2
        title_call = mock_render.call_args_list[0]
        artist_call = mock_render.call_args_list[1]

        # Title (CJK) should use CJK font
        assert title_call[0][2] == "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc"
        # Artist (ASCII) should use original font
        assert artist_call[0][2] == "/path/Montserrat-Bold.ttf"

    @patch.object(VideoGenerator, "_render_text_in_region")
    def test_both_cjk_both_get_cjk_font(self, mock_render, generator):
        """When both title and artist are CJK, both get CJK font."""
        draw = MagicMock()
        format_config = {
            "title_region": "370, 200, 3100, 480",
            "title_color": "#ffffff",
            "title_gradient": None,
            "artist_region": "370, 700, 3100, 480",
            "artist_color": "#ffdf6b",
            "artist_gradient": None,
            "extra_text": None,
        }

        generator._render_all_text(
            draw, "/path/Montserrat-Bold.ttf", "青花瓷", "周杰倫", format_config, False
        )

        assert mock_render.call_count == 2
        for call in mock_render.call_args_list:
            assert call[0][2] == "/usr/share/fonts/noto/NotoSansCJK-Bold.ttc"
