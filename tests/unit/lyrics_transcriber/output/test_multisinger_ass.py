"""Tests for multi-singer ASS rendering."""
import pytest

from karaoke_gen.lyrics_transcriber.output.ass.style import Style, build_karaoke_styles


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
