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
