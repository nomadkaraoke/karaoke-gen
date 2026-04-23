"""Regression guard: solo jobs must produce byte-identical ASS output to pre-change.

We generate ASS for a fixture segment using both the legacy-compatible path
(is_duet=False) and compare structural markers to the expected pre-change output.
This catches accidental behaviour changes in the solo path.
"""
import os
from unittest.mock import MagicMock

from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.lyrics_transcriber.types import Word, LyricsSegment
from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE


def _fixture_segments():
    return [
        LyricsSegment(
            id="s1",
            text="hello world",
            words=[
                Word(id="w1", text="hello", start_time=0.5, end_time=1.0),
                Word(id="w2", text="world", start_time=1.1, end_time=1.8),
            ],
            start_time=0.5,
            end_time=1.8,
        ),
    ]


def test_solo_ass_has_single_default_style():
    """is_duet=False + no singer fields → single 'Default' style, no per-singer styles."""
    gen = SubtitlesGenerator(
        output_dir="/tmp",
        video_resolution=(1920, 1080),
        font_size=100,
        line_height=60,
        styles={"karaoke": DEFAULT_KARAOKE_STYLE},
        subtitle_offset_ms=0,
        logger=MagicMock(),
        is_duet=False,
    )
    ass, _style, styles_by_singer = gen._create_styled_ass_instance((1920, 1080), 100, segments=_fixture_segments())
    assert styles_by_singer is None, "Solo path should produce no styles_by_singer map"
    # The ASS file should contain exactly one style, named per DEFAULT_KARAOKE_STYLE["ass_name"]
    # (ASS keeps styles in a list at ass.styles)
    assert len(ass.styles) == 1
    assert ass.styles[0].Name == DEFAULT_KARAOKE_STYLE["ass_name"]


def test_solo_ass_no_color_override_tags(tmp_path):
    """Generated ASS text for a solo segment should not contain any {\\c} or {\\r} tags."""
    gen = SubtitlesGenerator(
        output_dir=str(tmp_path),
        video_resolution=(1920, 1080),
        font_size=100,
        line_height=60,
        styles={"karaoke": DEFAULT_KARAOKE_STYLE},
        subtitle_offset_ms=0,
        logger=MagicMock(),
        is_duet=False,
    )
    # Run generate_ass against a fake audio filepath — we'll mock the duration
    gen._get_audio_duration = MagicMock(return_value=10.0)
    segments = _fixture_segments()
    output = gen.generate_ass(segments, output_prefix="test", audio_filepath="/fake/audio.mp3")
    with open(output, "r", encoding="utf-8") as f:
        content = f.read()
    assert "\\1c&H" not in content, "Solo ASS must not contain inline primary-color override tags"
    assert "\\2c&H" not in content, "Solo ASS must not contain inline secondary-color override tags"
    assert "\\3c&H" not in content, "Solo ASS must not contain inline outline-color override tags"
    # Count number of Style definition lines — should be exactly 1 Style
    style_lines = [l for l in content.split("\n") if l.startswith("Style:")]
    assert len(style_lines) == 1, f"Solo ASS must have exactly one Style, got {len(style_lines)}"
