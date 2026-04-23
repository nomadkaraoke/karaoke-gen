"""Tests for CDG duet TOML construction.

Covers the wire-up between CDGGenerator, CDG_DUET_SINGERS palette and the
segment-level singer tagging produced by build_cdg_lyrics — i.e. the
end-to-end TOML emitted when is_duet=True.
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import toml

from karaoke_gen.lyrics_transcriber.output.cdg import CDGGenerator
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word


def _fixture_cdg_styles(font_path: str) -> dict:
    return {
        "title_color": "white",
        "artist_color": "white",
        "background_color": "blue",
        "border_color": "black",
        "font_path": font_path,
        "font_size": 20,
        "stroke_width": 1,
        "stroke_style": "outline",
        "active_fill": "yellow",
        "active_stroke": "black",
        "inactive_fill": "white",
        "inactive_stroke": "black",
        "title_screen_background": "black",
        "instrumental_background": "black",
        "instrumental_transition": "fade",
        "instrumental_font_color": "gray",
        "title_screen_transition": "fade",
        "row": 1,
        "line_tile_height": 24,
        "lines_per_page": 4,
        "clear_mode": "page",
        "sync_offset": 0,
        "instrumental_gap_threshold": 500,
        "instrumental_text": "INSTRUMENTAL",
        "lead_in_threshold": 9999999,  # Disable lead-ins to keep tests readable
        "lead_in_symbols": ["*"],
        "lead_in_duration": 50,
        "lead_in_total": 150,
        "title_artist_gap": 10,
        "title_top_padding": 0,
        "intro_duration_seconds": 5,
        "first_syllable_buffer_seconds": 0.1,
        "outro_background": "black",
        "outro_transition": "fade",
        "outro_text_line1": "End",
        "outro_text_line2": "www.example.com",
        "outro_line1_color": "white",
        "outro_line2_color": "gray",
        "outro_line1_line2_gap": 5,
    }


def _segment(seg_id: str, start: float, text: str, singer=None):
    words = [
        Word(id=f"{seg_id}-w{i}", text=t, start_time=start + i * 0.1, end_time=start + (i + 1) * 0.1)
        for i, t in enumerate(text.split())
    ]
    return LyricsSegment(
        id=seg_id, text=text, words=words,
        start_time=start, end_time=start + len(words) * 0.1, singer=singer,
    )


@pytest.fixture
def real_font_path():
    """Use the bundled arial font so TOML generation doesn't crash on missing font."""
    import karaoke_gen.lyrics_transcriber.output as output_mod
    font = os.path.join(os.path.dirname(output_mod.__file__), "fonts", "arial.ttf")
    assert os.path.isfile(font), f"Expected bundled font at {font}"
    return font


def _generate_and_parse_toml(segments, is_duet: bool, cdg_styles: dict) -> dict:
    """Run the TOML-generation pipeline and return the parsed TOML dict."""
    with tempfile.TemporaryDirectory() as tmp:
        gen = CDGGenerator(output_dir=tmp, logger=MagicMock(), is_duet=is_duet)
        lyrics_data = gen._convert_segments_to_lyrics_data(segments, is_duet=is_duet)
        toml_path = os.path.join(tmp, "test.toml")
        gen.generate_toml(
            audio_file="/fake/audio.mp3",
            title="Test",
            artist="Test Artist",
            lyrics_data=lyrics_data,
            output_file=toml_path,
            cdg_styles=cdg_styles,
        )
        with open(toml_path, "rb") as f:
            return toml.loads(f.read().decode("utf-8"))


class TestDuetTomlSingersPalette:
    def test_duet_emits_three_singers(self, real_font_path):
        styles = _fixture_cdg_styles(real_font_path)
        segments = [
            _segment("s1", 0.0, "hello world", singer=1),
            _segment("s2", 2.0, "goodbye friend", singer=2),
            _segment("s3", 4.0, "together now", singer=0),
        ]
        data = _generate_and_parse_toml(segments, is_duet=True, cdg_styles=styles)
        assert len(data["singers"]) == 3
        # Post color-flip: inactive = signature (pre-sung, readable ahead),
        # active = white (sweep highlight). Matches the ASS/MP4 color model.
        # Singer 1: sky-blue inactive (#9AA8FF), white active
        assert data["singers"][0]["inactive_fill"].upper() == "#9AA8FF"
        assert data["singers"][0]["active_fill"].upper() == "#FFFFFF"
        # Singer 2: pink inactive (#F770B4), white active
        assert data["singers"][1]["inactive_fill"].upper() == "#F770B4"
        assert data["singers"][1]["active_fill"].upper() == "#FFFFFF"
        # Both: yellow inactive (#FCD34D), white active
        assert data["singers"][2]["inactive_fill"].upper() == "#FCD34D"
        assert data["singers"][2]["active_fill"].upper() == "#FFFFFF"

    def test_solo_keeps_one_singer_entry(self, real_font_path):
        styles = _fixture_cdg_styles(real_font_path)
        segments = [_segment("s1", 0.0, "hello world", singer=None)]
        data = _generate_and_parse_toml(segments, is_duet=False, cdg_styles=styles)
        assert len(data["singers"]) == 1
        # Solo fills match the cdg_styles flat colors
        assert data["singers"][0]["active_fill"] == "yellow"


class TestDuetTomlLyricTagging:
    def test_duet_prefixes_each_line_with_singer_index(self, real_font_path):
        styles = _fixture_cdg_styles(real_font_path)
        # 3 segments with distinct singers. With lead-in disabled, each segment
        # flushes as its own visual line (thanks to the '/' boundary marker).
        segments = [
            _segment("s1", 0.0, "alpha", singer=1),
            _segment("s2", 1.0, "beta", singer=2),
            _segment("s3", 2.0, "gamma", singer=0),
        ]
        data = _generate_and_parse_toml(segments, is_duet=True, cdg_styles=styles)
        lyric_text = data["lyrics"][0]["text"]
        lines = lyric_text.split("\n")
        # Strip padding '~' lines and find content lines
        singer_tagged_content = [l for l in lines if "|" in l and l.split("|", 1)[1].strip() not in ("", "~")]
        # Expect lines prefixed 1|, 2|, 3| in order (singer 0 → 3 for Both)
        prefixes = [l.split("|", 1)[0] for l in singer_tagged_content]
        assert "1" in prefixes
        assert "2" in prefixes
        assert "3" in prefixes  # SingerId 0 (Both) → CDG singer 3
        # All lines (content + padding) should be prefixed when is_duet=True
        non_empty_lines = [l for l in lines if l != ""]
        for l in non_empty_lines:
            assert "|" in l, f"Expected every line to be singer-tagged in duet mode, got: {l!r}"

    def test_solo_has_no_singer_prefixes(self, real_font_path):
        styles = _fixture_cdg_styles(real_font_path)
        segments = [_segment("s1", 0.0, "hello world", singer=None)]
        data = _generate_and_parse_toml(segments, is_duet=False, cdg_styles=styles)
        lyric_text = data["lyrics"][0]["text"]
        # No |-prefix in solo mode
        for line in lyric_text.split("\n"):
            if line:
                assert not (len(line) >= 2 and line[0].isdigit() and line[1] == "|"), (
                    f"Solo TOML must not have singer prefixes, got line: {line!r}"
                )


class TestCdgGeneratorIsDuetFlag:
    def test_is_duet_defaults_to_false(self):
        gen = CDGGenerator(output_dir="/tmp", logger=MagicMock())
        assert gen.is_duet is False

    def test_is_duet_explicit_true(self):
        gen = CDGGenerator(output_dir="/tmp", logger=MagicMock(), is_duet=True)
        assert gen.is_duet is True

    def test_convert_segments_no_singer_field_when_solo(self):
        gen = CDGGenerator(output_dir="/tmp", logger=MagicMock(), is_duet=False)
        segments = [_segment("s1", 0.0, "hi", singer=1)]
        result = gen._convert_segments_to_lyrics_data(segments, is_duet=False)
        # Solo path never sets 'singer' key (byte-identical to legacy)
        for entry in result:
            assert "singer" not in entry

    def test_convert_segments_includes_singer_field_when_duet(self):
        gen = CDGGenerator(output_dir="/tmp", logger=MagicMock(), is_duet=True)
        segments = [
            _segment("s1", 0.0, "hi", singer=1),
            _segment("s2", 1.0, "bye", singer=2),
        ]
        result = gen._convert_segments_to_lyrics_data(segments, is_duet=True)
        assert all("singer" in entry for entry in result)
        # First segment's word: singer=1; second segment's word: singer=2
        assert result[0]["singer"] == 1
        assert result[1]["singer"] == 2
        # The first word of s2 carries '/' marker so format_lyrics flushes
        # the previous line before accumulating singer-2 words.
        s2_first_entry = next(e for e in result if e["text"].startswith("/"))
        assert s2_first_entry["singer"] == 2
