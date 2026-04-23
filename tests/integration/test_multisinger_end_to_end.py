"""End-to-end integration tests for multi-singer rendering."""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "duet_small.json"


@pytest.fixture
def duet_segments():
    data = json.loads(FIXTURE_PATH.read_text())
    return [LyricsSegment.from_dict(s) for s in data["corrected_segments"]]


def _run_ass(segments, is_duet: bool, tmp_path: Path) -> str:
    gen = SubtitlesGenerator(
        output_dir=str(tmp_path),
        video_resolution=(1920, 1080),
        font_size=100,
        line_height=60,
        styles={"karaoke": DEFAULT_KARAOKE_STYLE},
        subtitle_offset_ms=0,
        logger=MagicMock(),
        is_duet=is_duet,
    )
    gen._get_audio_duration = MagicMock(return_value=30.0)
    return gen.generate_ass(segments, output_prefix="duet_test", audio_filepath="/fake/a.mp3")


class TestDuetEndToEnd:
    def test_duet_ass_has_three_named_styles(self, duet_segments, tmp_path):
        out = _run_ass(duet_segments, is_duet=True, tmp_path=tmp_path)
        content = Path(out).read_text()
        style_names = re.findall(r"^Style:\s*([^,]+),", content, re.MULTILINE)
        assert "Karaoke.Singer1" in style_names
        assert "Karaoke.Singer2" in style_names
        assert "Karaoke.Both" in style_names

    def test_duet_ass_tags_lines_with_correct_style(self, duet_segments, tmp_path):
        out = _run_ass(duet_segments, is_duet=True, tmp_path=tmp_path)
        content = Path(out).read_text()
        # The three singers should each appear as a style on at least one Dialogue line
        dialogue_lines = [l for l in content.split("\n") if l.startswith("Dialogue:")]
        # Simpler: just search for the named styles appearing in Dialogue lines
        assert any("Karaoke.Singer1" in l for l in dialogue_lines)
        assert any("Karaoke.Singer2" in l for l in dialogue_lines)
        assert any("Karaoke.Both" in l for l in dialogue_lines)

    def test_duet_word_override_produces_inline_color_tag(self, duet_segments, tmp_path):
        out = _run_ass(duet_segments, is_duet=True, tmp_path=tmp_path)
        content = Path(out).read_text()
        # In s2 (Singer 2), word "talk" has singer=1 override. The word should
        # get Singer 1's primary, secondary and outline colors inlined so it is
        # fully themed to Singer 1 throughout the karaoke sweep.
        # Singer 1 (post color-flip): primary=(230,230,255), secondary=(112,112,247),
        # outline=(26,58,235). ASS uses BGR.
        assert "\\1c&HFFE6E6&" in content  # primary (post-sung tint)
        assert "\\2c&HF77070&" in content  # secondary (pre-sung signature blue)
        assert "\\3c&HEB3A1A&" in content  # outline

    def test_solo_ass_has_one_default_style(self, duet_segments, tmp_path):
        # Force solo even though fixture has singer fields — is_duet=False ignores them
        out = _run_ass(duet_segments, is_duet=False, tmp_path=tmp_path)
        content = Path(out).read_text()
        style_names = re.findall(r"^Style:\s*([^,]+),", content, re.MULTILINE)
        assert style_names == [DEFAULT_KARAOKE_STYLE["ass_name"]]
        assert "Karaoke.Singer" not in content
        # No inline color tags
        assert "\\c&H" not in content


class TestCdgEndToEnd:
    def test_duet_cdg_lyrics_have_correct_singer_indices(self, duet_segments):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        result = build_cdg_lyrics(duet_segments, is_duet=True, line_tile_height=4, lines_per_page=3)
        # Three segments: singer=1, singer=2, singer=0 (Both → 3)
        assert [r.singer for r in result] == [1, 2, 3]

    def test_solo_cdg_lyrics_all_singer_1(self, duet_segments):
        from karaoke_gen.lyrics_transcriber.output.cdg import build_cdg_lyrics
        result = build_cdg_lyrics(duet_segments, is_duet=False, line_tile_height=4, lines_per_page=3)
        assert all(r.singer == 1 for r in result)

    def test_cdg_duet_toml_round_trips_through_composer(self, duet_segments, tmp_path):
        """Generate duet TOML, parse it via KaraokeComposer's structurer, and
        verify the resulting Settings has the 3-singer palette and singer-
        tagged lyric lines. Stops short of running compose() (needs audio).
        """
        import os
        import tomllib
        from unittest.mock import MagicMock
        from cattrs.preconf.json import make_converter
        from cattrs import Converter
        from karaoke_gen.lyrics_transcriber.output.cdg import CDGGenerator
        from karaoke_gen.lyrics_transcriber.output.cdgmaker.config import Settings
        import karaoke_gen.lyrics_transcriber.output as output_mod

        font_path = os.path.join(os.path.dirname(output_mod.__file__), "fonts", "arial.ttf")
        assert os.path.isfile(font_path)

        # A minimal but complete cdg_styles dict
        styles = {
            "title_color": "white", "artist_color": "white",
            "background_color": "blue", "border_color": "black",
            "font_path": font_path, "font_size": 20,
            "stroke_width": 1, "stroke_style": "octagon",
            "active_fill": "yellow", "active_stroke": "black",
            "inactive_fill": "white", "inactive_stroke": "black",
            "title_screen_background": str(font_path),
            "instrumental_background": str(font_path),
            "instrumental_transition": "fade",
            "instrumental_font_color": "gray",
            "title_screen_transition": "fade",
            "row": 1, "line_tile_height": 24, "lines_per_page": 4,
            "clear_mode": "page", "sync_offset": 0,
            "instrumental_gap_threshold": 500,
            "instrumental_text": "INSTRUMENTAL",
            "lead_in_threshold": 9999999,
            "lead_in_symbols": ["*"], "lead_in_duration": 50, "lead_in_total": 150,
            "title_artist_gap": 10, "title_top_padding": 0,
            "intro_duration_seconds": 5, "first_syllable_buffer_seconds": 0.1,
            "outro_background": str(font_path), "outro_transition": "fade",
            "outro_text_line1": "End", "outro_text_line2": "www.example.com",
            "outro_line1_color": "white", "outro_line2_color": "gray",
            "outro_line1_line2_gap": 5,
        }

        gen = CDGGenerator(output_dir=str(tmp_path), logger=MagicMock(), is_duet=True)
        lyrics_data = gen._convert_segments_to_lyrics_data(duet_segments, is_duet=True)
        toml_file = tmp_path / "duet.toml"
        gen.generate_toml(
            audio_file="/fake/audio.mp3",
            title="Duet Test",
            artist="Artist",
            lyrics_data=lyrics_data,
            output_file=str(toml_file),
            cdg_styles=styles,
        )

        with open(toml_file, "rb") as f:
            raw = tomllib.load(f)

        converter = Converter(prefer_attrib_converters=True)
        settings = converter.structure(raw, Settings)

        # 1. Palette has exactly 3 singers with the duet colors. After the
        #    color-flip, signature colors live on inactive_fill (pre-sung) so
        #    performers can read ahead; active_fill is white (sweep highlight).
        assert len(settings.singers) == 3
        assert settings.singers[0].inactive_fill == (154, 168, 255)   # Singer 1 sky-blue pre-sung
        assert settings.singers[0].active_fill == (255, 255, 255)     # white highlight
        assert settings.singers[1].inactive_fill == (247, 112, 180)   # Singer 2 pink pre-sung
        assert settings.singers[1].active_fill == (255, 255, 255)
        assert settings.singers[2].inactive_fill == (252, 211, 77)    # Both yellow pre-sung
        assert settings.singers[2].active_fill == (255, 255, 255)

        # 2. Lyrics text contains per-line N|singer prefixes (composer splits
        #    on \n+ and reads the prefix to choose singer per line).
        lyric_text = settings.lyrics[0].text
        non_empty = [line for line in lyric_text.split("\n") if line.strip()]
        assert non_empty, "Expected at least one non-empty lyric line"
        prefixes = set()
        for line in non_empty:
            assert "|" in line, f"Expected every duet line to be singer-tagged, got: {line!r}"
            prefix = line.split("|", 1)[0]
            assert prefix.isdigit(), f"Expected integer singer prefix, got: {prefix!r}"
            prefixes.add(prefix)
        # The fixture has singers 1, 2, and Both (→3); all three should appear
        assert {"1", "2", "3"}.issubset(prefixes)
