"""Tests for multi-singer ASS rendering."""
import pytest

from karaoke_gen.lyrics_transcriber.output.ass.style import Style, build_karaoke_styles
from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_line import LyricsLine
from karaoke_gen.lyrics_transcriber.output.ass.config import ScreenConfig, LineTimingInfo, LineState
from karaoke_gen.lyrics_transcriber.output.ass.lyrics_screen import LyricsScreen


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
        # Theme override wins for primary
        assert styles[0].PrimaryColour == (247, 112, 180, 255)
        # Fields not overridden by the theme fall back to the built-in default
        # duet palette (Singer 2 secondary = 247, 112, 180 — pink signature).
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        expected = tuple(
            int(x.strip()) for x in DEFAULT_KARAOKE_STYLE["singers"]["2"]["secondary_color"].split(",")
        )
        assert styles[0].SecondaryColour == expected

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
        # Full override now emits primary (\1c), secondary (\2c), outline (\3c).
        assert "\\1c&HB470F7&" in text
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
        assert "\\1c&H" not in text
        assert "\\2c&H" not in text
        assert "\\3c&H" not in text
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

        # Singer 2 override was applied to "there" — full override emits all three color tags
        assert "\\1c&HB470F7&" in text
        # Reset tag must appear before the third word (w2 fallback to segment singer 1)
        # so the text between the two color-related markers shouldn't end with stale singer 2 color
        # A simple structural check: the \r must precede "friend"
        r_idx = text.find("{\\r}")
        friend_idx = text.find("friend")
        assert r_idx > -1 and friend_idx > -1 and r_idx < friend_idx, (
            f"Expected {{\\r}} before 'friend' to reset the missing-singer override; got: {text}"
        )


class TestLyricsScreenPassesStylesMap:
    def test_screen_threads_styles_by_singer_to_lines(self, karaoke_style_dict):
        styles = build_karaoke_styles(karaoke_style_dict, singers=[1, 2, 0])
        by_name = {s.Name: s for s in styles}
        styles_map = {1: by_name["Karaoke.Singer1"], 2: by_name["Karaoke.Singer2"], 0: by_name["Karaoke.Both"]}

        screen = LyricsScreen(
            video_size=(1920, 1080),
            line_height=60,
            config=_screen_config(),
        )
        screen.lines.append(_make_line(singer=2))

        events, _ = screen.as_ass_events(style=by_name["Karaoke.Singer1"], styles_by_singer=styles_map)
        # The one line's event should use Singer2's style
        dialogue_events = [e for e in events if getattr(e, "type", None) == "Dialogue"]
        assert any(e.Style is by_name["Karaoke.Singer2"] for e in dialogue_events)


from unittest.mock import MagicMock


def _segment(seg_id, start, end, words, singer=None):
    return LyricsSegment(
        id=seg_id, text=" ".join(w.text for w in words),
        words=words, start_time=start, end_time=end, singer=singer,
    )


class TestSubtitlesGeneratorSingerDetection:
    def test_solo_default_returns_singer_1_only(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment("s1", 0.0, 1.0, [Word(id="w1", text="hi", start_time=0.0, end_time=1.0)], singer=None),
        ]
        assert gen._detect_singers_in_use(segments, is_duet=False) == [1]

    def test_duet_on_but_all_singer_1_still_returns_just_1(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment("s1", 0.0, 1.0, [Word(id="w1", text="hi", start_time=0.0, end_time=1.0)], singer=1),
        ]
        assert gen._detect_singers_in_use(segments, is_duet=True) == [1]

    def test_duet_with_mixed_singers(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment("s1", 0.0, 1.0, [Word(id="w1", text="hi", start_time=0.0, end_time=1.0)], singer=1),
            _segment("s2", 1.0, 2.0, [Word(id="w2", text="bye", start_time=1.0, end_time=2.0)], singer=2),
            _segment("s3", 2.0, 3.0, [Word(id="w3", text="hey", start_time=2.0, end_time=3.0)], singer=0),
        ]
        # Always sorted with 1 first
        singers = gen._detect_singers_in_use(segments, is_duet=True)
        assert 1 in singers and 2 in singers and 0 in singers

    def test_duet_picks_up_word_level_override(self, karaoke_style_dict):
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=100, line_height=60, styles={"karaoke": karaoke_style_dict},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        segments = [
            _segment(
                "s1", 0.0, 1.0,
                [Word(id="w1", text="hi", start_time=0.0, end_time=0.5, singer=2),
                 Word(id="w2", text="bye", start_time=0.5, end_time=1.0)],
                singer=1,
            ),
        ]
        singers = gen._detect_singers_in_use(segments, is_duet=True)
        assert 2 in singers

    def test_fontsize_param_overrides_style_dict(self, karaoke_style_dict):
        """Regression: the fontsize parameter to _create_styled_ass_instance must be
        the source of truth for Style.Fontsize, not karaoke_style["font_size"]. This
        preserves pre-T7 behavior and lets the caller scale font size for preview mode."""
        from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
        # Override dict has font_size=999 but param passes 42
        d = dict(karaoke_style_dict)
        d["font_size"] = 999
        gen = SubtitlesGenerator(
            output_dir="/tmp", video_resolution=(1920, 1080),
            font_size=42, line_height=60, styles={"karaoke": d},
            subtitle_offset_ms=0, logger=MagicMock(),
        )
        ass, primary, _ = gen._create_styled_ass_instance((1920, 1080), 42, segments=[])
        assert primary.Fontsize == 42
