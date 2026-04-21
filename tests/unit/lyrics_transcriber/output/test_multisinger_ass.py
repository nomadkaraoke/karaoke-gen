"""Tests for multi-singer ASS rendering."""
import pytest

from karaoke_gen.lyrics_transcriber.output.ass.style import Style, build_karaoke_styles
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_line import LyricsLine
from karaoke_gen.lyrics_transcriber.output.ass.config import ScreenConfig, LineTimingInfo, LineState


def _screen_config():
    return ScreenConfig(
        line_height=60,
        video_width=1920,
        video_height=1080,
    )


def _line_state():
    return LineState(
        text="hello world",
        timing=LineTimingInfo(
            fade_in_time=0.0,
            end_time=2.0,
            fade_out_time=2.3,
            clear_time=2.3,
        ),
        y_position=100,
    )


def _make_line(singer=None):
    segment = LyricsSegment(
        id="s1",
        text="hello world",
        words=[
            Word(id="w1", text="hello", start_time=0.0, end_time=0.5),
            Word(id="w2", text="world", start_time=0.6, end_time=1.0),
        ],
        start_time=0.0,
        end_time=1.0,
        singer=singer,
    )
    return LyricsLine(segment=segment, screen_config=_screen_config())


@pytest.fixture
def karaoke_style_dict():
    # Minimal style dict used by SubtitlesGenerator
    return {
        "ass_name": "Default",
        "font": "Noto Sans",
        "font_path": "",
        "font_size": 100,
        "primary_color":   "112, 112, 247, 255",
        "secondary_color": "255, 255, 255, 255",
        "outline_color":   "26, 58, 235, 255",
        "back_color":      "0, 0, 0, 0",
        "bold": False,
        "italic": False,
        "underline": False,
        "strike_out": False,
        "scale_x": 100,
        "scale_y": 100,
        "spacing": 0,
        "angle": 0.0,
        "border_style": 1,
        "outline": 1,
        "shadow": 0,
        "margin_l": 0, "margin_r": 0, "margin_v": 0,
        "encoding": 0,
        "singers": {
            "1": {},
            "2": {"primary_color": "247, 112, 180, 255"},
            "both": {"primary_color": "252, 211, 77, 255"},
        },
    }


class TestBuildKaraokeStyles:
    def test_solo_returns_single_default_style(self, karaoke_style_dict):
        # Solo path: singers=[1] with the original ass_name
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1], solo=True)
        assert len(styles) == 1
        assert styles[0].Name == "Default"
        assert styles[0].PrimaryColour == (112, 112, 247, 255)

    def test_duet_returns_named_styles_per_singer(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        assert set(by_name) == {"Karaoke.Singer1", "Karaoke.Singer2", "Karaoke.Both"}

    def test_singer2_picks_up_overridden_primary(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[2])
        assert styles[0].Name == "Karaoke.Singer2"
        assert styles[0].PrimaryColour == (247, 112, 180, 255)
        # Non-overridden fields still come from flat theme
        assert styles[0].SecondaryColour == (255, 255, 255, 255)

    def test_both_is_yellow(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[0])
        assert styles[0].Name == "Karaoke.Both"
        assert styles[0].PrimaryColour == (252, 211, 77, 255)

    def test_font_settings_identical_across_singers(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        font_sizes = {s.Fontsize for s in styles}
        fontnames = {s.Fontname for s in styles}
        assert len(font_sizes) == 1
        assert len(fontnames) == 1


class TestLyricsLineStylePerSinger:
    def test_line_uses_fallback_style_when_no_styles_map(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1], solo=True)
        line = _make_line(singer=None)
        events = line.create_ass_events(
            state=_line_state(), style=styles[0], config=line.screen_config
        )
        assert len(events) >= 1
        assert events[-1].Style is styles[0]

    def test_line_picks_singer2_style_when_segment_singer_is_2(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _make_line(singer=2)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],  # fallback
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        assert events[-1].Style is by_name["Karaoke.Singer2"]

    def test_line_picks_both_style_when_segment_singer_is_0(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _make_line(singer=0)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        assert events[-1].Style is by_name["Karaoke.Both"]

    def test_line_defaults_to_singer1_style_when_segment_singer_none_with_map(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _make_line(singer=None)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        assert events[-1].Style is by_name["Karaoke.Singer1"]


def _line_with_override():
    segment = LyricsSegment(
        id="s1",
        text="hello world",
        words=[
            Word(id="w1", text="hello", start_time=0.0, end_time=0.5, singer=None),
            Word(id="w2", text="world", start_time=0.6, end_time=1.0, singer=2),
        ],
        start_time=0.0,
        end_time=1.0,
        singer=1,
    )
    return LyricsLine(segment=segment, screen_config=_screen_config())


class TestLyricsLineWordOverride:
    def test_word_override_emits_color_tag(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        line = _line_with_override()

        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        text = events[-1].Text
        # Singer 2 primary = 247, 112, 180 → BGR hex: B4 70 F7 (padded)
        # ASS color format: &HBBGGRR& i.e. B4 70 F7 → &HB470F7&
        assert "\\c&HB470F7&" in text
        # Reset tag after the overridden word
        assert "{\\r}" in text

    def test_no_override_when_word_singer_matches_segment(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        # All words' singer None (inherit from segment.singer=1) — no overrides
        line = _make_line(singer=1)
        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        text = events[-1].Text
        assert "\\c&H" not in text
        assert "{\\r}" not in text

    def test_override_resets_when_word_singer_not_in_map(self, karaoke_style_dict):
        """If a word's singer isn't present in styles_by_singer, emit {\\r} to fall back
        to the line's base style rather than leaving a stale override color in effect."""
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}

        # Segment singer 1, word0 override singer 2 (in map), word1 singer 99 (NOT in map)
        segment = LyricsSegment(
            id="s1",
            text="hi there friend",
            words=[
                Word(id="w0", text="hi",     start_time=0.0, end_time=0.3),
                Word(id="w1", text="there",  start_time=0.4, end_time=0.7, singer=2),
                Word(id="w2", text="friend", start_time=0.8, end_time=1.2, singer=99),
            ],
            start_time=0.0,
            end_time=1.2,
            singer=1,
        )
        line = LyricsLine(segment=segment, screen_config=_screen_config())

        events = line.create_ass_events(
            state=_line_state(),
            style=by_name["Karaoke.Singer1"],
            config=line.screen_config,
            styles_by_singer={1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]},
        )
        text = events[-1].Text

        # Singer 2 override was applied to "there"
        assert "\\c&HB470F7&" in text
        # Reset tag must appear before the third word (w2 fallback to segment singer 1)
        # so the text between the two color-related markers shouldn't end with stale singer 2 color
        # A simple structural check: the \r must precede "friend"
        r_idx = text.find("{\\r}")
        friend_idx = text.find("friend")
        assert r_idx > -1 and friend_idx > -1 and r_idx < friend_idx, (
            f"Expected {{\\r}} before 'friend' to reset the missing-singer override; got: {text}"
        )
