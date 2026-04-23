"""Tests for the style_loader module."""
import json
import os
import pytest
import tempfile
from unittest.mock import MagicMock, patch, mock_open

from karaoke_gen.style_loader import (
    DEFAULT_INTRO_STYLE,
    DEFAULT_END_STYLE,
    DEFAULT_KARAOKE_STYLE,
    DEFAULT_CDG_STYLE,
    DEFAULT_STYLE_PARAMS,
    ASSET_KEY_MAPPINGS,
    load_style_params_from_file,
    apply_style_overrides,
    update_asset_paths,
    save_style_params,
    load_styles_from_gcs,
    get_default_style_params,
    get_minimal_karaoke_styles,
    get_intro_format,
    get_end_format,
    get_karaoke_format,
    get_cdg_format,
    get_video_durations,
    get_existing_images,
)


class TestDefaultStyles:
    """Tests for default style configurations."""

    def test_default_intro_style_has_required_keys(self):
        """Test that DEFAULT_INTRO_STYLE has all required keys."""
        required_keys = [
            "video_duration", "existing_image", "background_color",
            "background_image", "font", "artist_color", "title_color",
        ]
        for key in required_keys:
            assert key in DEFAULT_INTRO_STYLE

    def test_default_end_style_has_required_keys(self):
        """Test that DEFAULT_END_STYLE has all required keys."""
        required_keys = [
            "video_duration", "background_color", "font",
            "extra_text", "extra_text_color",
        ]
        for key in required_keys:
            assert key in DEFAULT_END_STYLE

    def test_default_karaoke_style_has_required_keys(self):
        """Test that DEFAULT_KARAOKE_STYLE has all required keys."""
        required_keys = [
            "background_color", "font", "font_path", "primary_color",
            "secondary_color", "outline_color", "back_color",
            "font_size", "max_line_length",
        ]
        for key in required_keys:
            assert key in DEFAULT_KARAOKE_STYLE

    def test_default_cdg_style_has_required_keys(self):
        """Test that DEFAULT_CDG_STYLE has all required keys."""
        required_keys = [
            "font_path", "instrumental_background",
            "title_screen_background", "outro_background",
        ]
        for key in required_keys:
            assert key in DEFAULT_CDG_STYLE

    def test_default_style_params_has_all_sections(self):
        """Test that DEFAULT_STYLE_PARAMS has all sections."""
        assert "intro" in DEFAULT_STYLE_PARAMS
        assert "end" in DEFAULT_STYLE_PARAMS
        assert "karaoke" in DEFAULT_STYLE_PARAMS
        assert "cdg" in DEFAULT_STYLE_PARAMS


class TestAssetKeyMappings:
    """Tests for asset key mappings."""

    def test_background_mappings_exist(self):
        """Test that background image mappings exist."""
        assert "intro_background" in ASSET_KEY_MAPPINGS
        assert "karaoke_background" in ASSET_KEY_MAPPINGS
        assert "end_background" in ASSET_KEY_MAPPINGS

    def test_font_mapping_is_list(self):
        """Test that font mapping is a list (maps to multiple sections)."""
        assert isinstance(ASSET_KEY_MAPPINGS["font"], list)
        assert len(ASSET_KEY_MAPPINGS["font"]) == 4  # intro, karaoke, end, cdg

    def test_cdg_mappings_exist(self):
        """Test that CDG-specific mappings exist."""
        assert "cdg_instrumental_background" in ASSET_KEY_MAPPINGS
        assert "cdg_title_background" in ASSET_KEY_MAPPINGS
        assert "cdg_outro_background" in ASSET_KEY_MAPPINGS


class TestLoadStyleParamsFromFile:
    """Tests for load_style_params_from_file function."""

    def test_returns_defaults_when_no_path(self):
        """Test that defaults are returned when no path is provided."""
        result = load_style_params_from_file(None)
        assert result == get_default_style_params()

    def test_loads_valid_json_file(self, temp_dir):
        """Test loading a valid JSON file."""
        style_data = {"intro": {"video_duration": 10}}
        style_path = os.path.join(temp_dir, "styles.json")
        
        with open(style_path, "w") as f:
            json.dump(style_data, f)
        
        result = load_style_params_from_file(style_path)
        assert result == style_data

    def test_raises_on_file_not_found(self, temp_dir):
        """Test that FileNotFoundError is raised when file doesn't exist."""
        fake_path = os.path.join(temp_dir, "nonexistent.json")
        
        with pytest.raises(FileNotFoundError):
            load_style_params_from_file(fake_path, exit_on_error=False)

    def test_raises_on_invalid_json(self, temp_dir):
        """Test that JSONDecodeError is raised for invalid JSON."""
        invalid_path = os.path.join(temp_dir, "invalid.json")
        
        with open(invalid_path, "w") as f:
            f.write("not valid json {{{")
        
        with pytest.raises(json.JSONDecodeError):
            load_style_params_from_file(invalid_path, exit_on_error=False)

    def test_raises_on_generic_error(self, temp_dir):
        """Test that generic exceptions are raised."""
        style_path = os.path.join(temp_dir, "styles.json")
        
        # Create a file that will cause a read error by mocking open
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                load_style_params_from_file(style_path, exit_on_error=False)

    def test_exit_on_error_file_not_found(self, temp_dir):
        """Test that sys.exit is called when exit_on_error=True and file not found."""
        fake_path = os.path.join(temp_dir, "nonexistent.json")
        
        with patch("sys.exit") as mock_exit:
            # sys.exit is mocked so exception still propagates after sys.exit call
            with pytest.raises(FileNotFoundError):
                load_style_params_from_file(fake_path, exit_on_error=True)
            mock_exit.assert_called_once_with(1)

    def test_exit_on_error_invalid_json(self, temp_dir):
        """Test that sys.exit is called when exit_on_error=True and invalid JSON."""
        invalid_path = os.path.join(temp_dir, "invalid.json")
        
        with open(invalid_path, "w") as f:
            f.write("not valid json")
        
        with patch("sys.exit") as mock_exit:
            # sys.exit is mocked so exception still propagates after sys.exit call
            with pytest.raises(json.JSONDecodeError):
                load_style_params_from_file(invalid_path, exit_on_error=True)
            mock_exit.assert_called_once_with(1)


class TestApplyStyleOverrides:
    """Tests for apply_style_overrides function."""

    def test_applies_simple_override(self):
        """Test applying a simple override."""
        style_params = {"intro": {"video_duration": 5}}
        overrides = {"intro.video_duration": "10"}
        
        apply_style_overrides(style_params, overrides)
        
        assert style_params["intro"]["video_duration"] == 10

    def test_applies_string_override(self):
        """Test applying a string override."""
        style_params = {"intro": {"font": "Arial"}}
        overrides = {"intro.font": "Helvetica"}
        
        apply_style_overrides(style_params, overrides)
        
        assert style_params["intro"]["font"] == "Helvetica"

    def test_applies_bool_override_true(self):
        """Test applying a boolean true override."""
        style_params = {"karaoke": {"bold": False}}
        overrides = {"karaoke.bold": "true"}
        
        apply_style_overrides(style_params, overrides)
        
        assert style_params["karaoke"]["bold"] is True

    def test_applies_bool_override_false(self):
        """Test applying a boolean false override."""
        style_params = {"karaoke": {"bold": True}}
        overrides = {"karaoke.bold": "false"}
        
        apply_style_overrides(style_params, overrides)
        
        assert style_params["karaoke"]["bold"] is False

    def test_applies_bool_override_yes(self):
        """Test applying a boolean 'yes' override."""
        style_params = {"karaoke": {"bold": False}}
        overrides = {"karaoke.bold": "yes"}
        
        apply_style_overrides(style_params, overrides)
        
        assert style_params["karaoke"]["bold"] is True

    def test_warns_on_missing_key(self, mock_logger):
        """Test that a warning is logged for missing keys."""
        style_params = {"intro": {"video_duration": 5}}
        overrides = {"intro.nonexistent": "value"}
        
        apply_style_overrides(style_params, overrides, logger=mock_logger)
        
        mock_logger.warning.assert_called()

    def test_warns_on_missing_section(self, mock_logger):
        """Test that a warning is logged for missing sections."""
        style_params = {"intro": {"video_duration": 5}}
        overrides = {"nonexistent.key": "value"}
        
        apply_style_overrides(style_params, overrides, logger=mock_logger)
        
        mock_logger.warning.assert_called()

    def test_handles_type_conversion_error(self, mock_logger):
        """Test handling of type conversion errors."""
        style_params = {"intro": {"video_duration": 5}}
        # This will try to convert "not_a_number" to int and fail gracefully
        overrides = {"intro.video_duration": "not_a_number"}
        
        apply_style_overrides(style_params, overrides, logger=mock_logger)
        
        # Should use string value as fallback
        mock_logger.warning.assert_called()

    def test_handles_none_original_value(self):
        """Test handling when original value is None."""
        style_params = {"intro": {"background_image": None}}
        overrides = {"intro.background_image": "/path/to/image.png"}
        
        apply_style_overrides(style_params, overrides)
        
        assert style_params["intro"]["background_image"] == "/path/to/image.png"


class TestUpdateAssetPaths:
    """Tests for update_asset_paths function."""

    def test_updates_single_mapping(self):
        """Test updating a single asset path."""
        style_data = {"intro": {"background_image": None}}
        local_assets = {"intro_background": "/local/path/background.png"}
        
        result = update_asset_paths(style_data, local_assets)
        
        assert result is True
        assert style_data["intro"]["background_image"] == "/local/path/background.png"

    def test_updates_multiple_mappings(self):
        """Test updating multiple asset paths from font mapping."""
        style_data = {
            "intro": {"font": None},
            "karaoke": {"font_path": None},
            "end": {"font": None},
            "cdg": {"font_path": None},
        }
        local_assets = {"font": "/local/path/font.ttf"}
        
        result = update_asset_paths(style_data, local_assets)
        
        assert result is True
        assert style_data["intro"]["font"] == "/local/path/font.ttf"
        assert style_data["karaoke"]["font_path"] == "/local/path/font.ttf"
        assert style_data["end"]["font"] == "/local/path/font.ttf"
        assert style_data["cdg"]["font_path"] == "/local/path/font.ttf"

    def test_skips_unknown_asset_keys(self):
        """Test that unknown asset keys are skipped."""
        style_data = {"intro": {"background_image": None}}
        local_assets = {"unknown_key": "/local/path/file.png"}
        
        result = update_asset_paths(style_data, local_assets)
        
        assert result is False
        assert style_data["intro"]["background_image"] is None

    def test_skips_missing_sections(self):
        """Test that missing sections don't cause errors."""
        style_data = {}  # No sections
        local_assets = {"intro_background": "/local/path/background.png"}
        
        result = update_asset_paths(style_data, local_assets)
        
        assert result is False

    def test_returns_false_when_no_updates(self):
        """Test that False is returned when no updates are made."""
        style_data = {"intro": {"background_image": None}}
        local_assets = {}
        
        result = update_asset_paths(style_data, local_assets)
        
        assert result is False


class TestSaveStyleParams:
    """Tests for save_style_params function."""

    def test_saves_to_file(self, temp_dir):
        """Test saving style params to a file."""
        style_data = {"intro": {"video_duration": 10}}
        output_path = os.path.join(temp_dir, "output.json")
        
        result = save_style_params(style_data, output_path)
        
        assert result == output_path
        assert os.path.exists(output_path)
        
        with open(output_path) as f:
            loaded = json.load(f)
        assert loaded == style_data

    def test_formats_json_with_indent(self, temp_dir):
        """Test that JSON is formatted with indentation."""
        style_data = {"intro": {"video_duration": 10}}
        output_path = os.path.join(temp_dir, "output.json")
        
        save_style_params(style_data, output_path)
        
        with open(output_path) as f:
            content = f.read()
        # Check for indentation (2 spaces)
        assert "  " in content


class TestLoadStylesFromGcs:
    """Tests for load_styles_from_gcs function."""

    def test_raises_error_when_no_gcs_path(self, temp_dir):
        """Test that error is raised when no GCS path provided (Phase 2)."""
        mock_download = MagicMock()

        with pytest.raises(ValueError, match="style_params_gcs_path is required"):
            load_styles_from_gcs(
                style_params_gcs_path=None,
                style_assets=None,
                temp_dir=temp_dir,
                download_func=mock_download,
            )

        mock_download.assert_not_called()

    def test_downloads_and_loads_custom_styles(self, temp_dir):
        """Test downloading and loading custom styles from GCS."""
        custom_styles = {"karaoke": {"font_size": 120}}
        
        def mock_download(gcs_path, local_path):
            if "styles.json" in local_path:
                with open(local_path, "w") as f:
                    json.dump(custom_styles, f)
        
        styles_path, style_data = load_styles_from_gcs(
            style_params_gcs_path="gs://bucket/styles.json",
            style_assets=None,
            temp_dir=temp_dir,
            download_func=mock_download,
        )
        
        assert style_data["karaoke"]["font_size"] == 120

    def test_downloads_style_assets(self, temp_dir):
        """Test downloading style assets."""
        custom_styles = {"karaoke": {"background_image": None}}
        download_calls = []
        
        def mock_download(gcs_path, local_path):
            download_calls.append((gcs_path, local_path))
            if "styles.json" in local_path:
                with open(local_path, "w") as f:
                    json.dump(custom_styles, f)
            else:
                # Create a dummy asset file
                with open(local_path, "w") as f:
                    f.write("asset content")
        
        style_assets = {
            "style_params": "gs://bucket/styles.json",
            "karaoke_background": "gs://bucket/background.png",
        }
        
        styles_path, style_data = load_styles_from_gcs(
            style_params_gcs_path="gs://bucket/styles.json",
            style_assets=style_assets,
            temp_dir=temp_dir,
            download_func=mock_download,
        )
        
        # Should have downloaded both styles.json and the background
        assert len(download_calls) >= 2

    def test_skips_style_params_asset(self, temp_dir):
        """Test that style_params asset key is skipped (already downloaded)."""
        custom_styles = {"karaoke": {"background_image": None}}
        download_calls = []
        
        def mock_download(gcs_path, local_path):
            download_calls.append((gcs_path, local_path))
            if "styles.json" in local_path:
                with open(local_path, "w") as f:
                    json.dump(custom_styles, f)
        
        style_assets = {"style_params": "gs://bucket/styles.json"}
        
        load_styles_from_gcs(
            style_params_gcs_path="gs://bucket/styles.json",
            style_assets=style_assets,
            temp_dir=temp_dir,
            download_func=mock_download,
        )
        
        # Should only download styles.json once (not from style_assets)
        assert len(download_calls) == 1

    def test_handles_asset_download_failure(self, temp_dir, mock_logger):
        """Test handling of asset download failures."""
        custom_styles = {"karaoke": {"background_image": None}}
        
        def mock_download(gcs_path, local_path):
            if "background" in gcs_path:
                raise Exception("Download failed")
            with open(local_path, "w") as f:
                json.dump(custom_styles, f)
        
        style_assets = {"karaoke_background": "gs://bucket/background.png"}
        
        # Should not raise, just log warning
        styles_path, style_data = load_styles_from_gcs(
            style_params_gcs_path="gs://bucket/styles.json",
            style_assets=style_assets,
            temp_dir=temp_dir,
            download_func=mock_download,
            logger=mock_logger,
        )
        
        mock_logger.warning.assert_called()

    def test_raises_error_on_download_failure(self, temp_dir, mock_logger):
        """Test that error is raised when download fails (Phase 2)."""
        def mock_download(gcs_path, local_path):
            raise Exception("Download failed")

        with pytest.raises(ValueError, match="Failed to download theme from GCS"):
            load_styles_from_gcs(
                style_params_gcs_path="gs://bucket/styles.json",
                style_assets=None,
                temp_dir=temp_dir,
                download_func=mock_download,
                logger=mock_logger,
            )

        mock_logger.error.assert_called()


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_default_style_params(self):
        """Test get_default_style_params returns fresh copy."""
        params1 = get_default_style_params()
        params2 = get_default_style_params()
        
        # Modify params1
        params1["intro"]["video_duration"] = 999
        
        # params2 should be unaffected
        assert params2["intro"]["video_duration"] != 999

    def test_get_minimal_karaoke_styles(self):
        """Test get_minimal_karaoke_styles returns minimal set."""
        styles = get_minimal_karaoke_styles()
        
        assert "karaoke" in styles
        assert "cdg" in styles
        assert "intro" not in styles
        assert "end" not in styles

    def test_get_intro_format_raises_on_missing_section(self):
        """Test get_intro_format raises error when section missing (Phase 2)."""
        with pytest.raises(ValueError, match="Missing 'intro' section"):
            get_intro_format({})

    def test_get_intro_format_raises_on_incomplete(self):
        """Test get_intro_format raises error on incomplete theme (Phase 2)."""
        style_params = {"intro": {"video_duration": 10}}

        with pytest.raises(ValueError, match="Incomplete 'intro' section"):
            get_intro_format(style_params)

    def test_get_intro_format_works_with_complete_theme(self):
        """Test get_intro_format works with complete theme."""
        style_params = {"intro": DEFAULT_INTRO_STYLE.copy()}
        style_params["intro"]["video_duration"] = 10

        result = get_intro_format(style_params)

        assert result["video_duration"] == 10
        assert result["font"] == DEFAULT_INTRO_STYLE["font"]

    def test_get_end_format_raises_on_missing_section(self):
        """Test get_end_format raises error when section missing (Phase 2)."""
        with pytest.raises(ValueError, match="Missing 'end' section"):
            get_end_format({})

    def test_get_end_format_raises_on_incomplete(self):
        """Test get_end_format raises error on incomplete theme (Phase 2)."""
        style_params = {"end": {"video_duration": 8}}

        with pytest.raises(ValueError, match="Incomplete 'end' section"):
            get_end_format(style_params)

    def test_get_end_format_works_with_complete_theme(self):
        """Test get_end_format works with complete theme."""
        style_params = {"end": DEFAULT_END_STYLE.copy()}
        style_params["end"]["video_duration"] = 8

        result = get_end_format(style_params)

        assert result["video_duration"] == 8

    def test_get_karaoke_format_raises_on_missing_section(self):
        """Test get_karaoke_format raises error when section missing (Phase 2)."""
        with pytest.raises(ValueError, match="Missing 'karaoke' section"):
            get_karaoke_format({})

    def test_get_karaoke_format_raises_on_incomplete(self):
        """Test get_karaoke_format raises error on incomplete theme (Phase 2)."""
        style_params = {"karaoke": {"font_size": 120}}

        with pytest.raises(ValueError, match="Incomplete 'karaoke' section"):
            get_karaoke_format(style_params)

    def test_get_karaoke_format_works_with_complete_theme(self):
        """Test get_karaoke_format works with complete theme."""
        style_params = {"karaoke": DEFAULT_KARAOKE_STYLE.copy()}
        style_params["karaoke"]["font_size"] = 120

        result = get_karaoke_format(style_params)

        assert result["font_size"] == 120

    def test_get_cdg_format_returns_none_when_missing(self):
        """Test get_cdg_format returns None when section is missing."""
        result = get_cdg_format({})

        assert result is None

    def test_get_cdg_format_raises_on_incomplete(self):
        """Test get_cdg_format raises error on incomplete theme (Phase 2)."""
        style_params = {"cdg": {"font_path": "/custom/font.ttf"}}

        with pytest.raises(ValueError, match="Incomplete 'cdg' section"):
            get_cdg_format(style_params)

    def test_get_cdg_format_works_with_complete_theme(self):
        """Test get_cdg_format works with complete theme."""
        style_params = {"cdg": DEFAULT_CDG_STYLE.copy()}
        style_params["cdg"]["font_path"] = "/custom/font.ttf"

        result = get_cdg_format(style_params)

        assert result["font_path"] == "/custom/font.ttf"

    def test_get_video_durations_defaults(self):
        """Test get_video_durations with defaults."""
        intro_dur, end_dur = get_video_durations({})
        
        assert intro_dur == DEFAULT_INTRO_STYLE["video_duration"]
        assert end_dur == DEFAULT_END_STYLE["video_duration"]

    def test_get_video_durations_custom(self):
        """Test get_video_durations with custom values."""
        style_params = {
            "intro": {"video_duration": 10},
            "end": {"video_duration": 8},
        }
        intro_dur, end_dur = get_video_durations(style_params)
        
        assert intro_dur == 10
        assert end_dur == 8

    def test_get_existing_images_defaults(self):
        """Test get_existing_images with no existing images."""
        title_img, end_img = get_existing_images({})
        
        assert title_img is None
        assert end_img is None

    def test_get_existing_images_custom(self):
        """Test get_existing_images with custom images."""
        style_params = {
            "intro": {"existing_image": "/path/to/title.png"},
            "end": {"existing_image": "/path/to/end.png"},
        }
        title_img, end_img = get_existing_images(style_params)

        assert title_img == "/path/to/title.png"
        assert end_img == "/path/to/end.png"


class TestKaraokeSingersBlock:
    """Tests for the optional per-singer colors block under karaoke style."""

    def test_default_karaoke_style_has_singers_block(self):
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        assert "singers" in DEFAULT_KARAOKE_STYLE
        # The nomad defaults ship with blue / pink / yellow presets
        singers = DEFAULT_KARAOKE_STYLE["singers"]
        assert set(singers.keys()) == {"1", "2", "both"}

    def test_singer2_has_pink_unhighlighted(self):
        """Duet model: secondary (pre-sung) is the singer's signature color."""
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        assert DEFAULT_KARAOKE_STYLE["singers"]["2"]["secondary_color"] == "247, 112, 180, 255"

    def test_both_has_yellow_unhighlighted(self):
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        assert DEFAULT_KARAOKE_STYLE["singers"]["both"]["secondary_color"] == "252, 211, 77, 255"

    def test_highlighted_primaries_are_near_white(self):
        """Duet model: primary (post-sung) is near-white with a subtle hue tint."""
        from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE
        for key in ("1", "2", "both"):
            primary = DEFAULT_KARAOKE_STYLE["singers"][key]["primary_color"]
            r, g, b, _ = [int(x.strip()) for x in primary.split(",")]
            # Every channel should be ≥ 230 (near-white) but at least one should
            # differ from pure white to preserve the singer's hue hint.
            assert r >= 230 and g >= 230 and b >= 230, f"Singer {key} primary not near-white: {primary}"
            assert (r, g, b) != (255, 255, 255), f"Singer {key} primary is pure white, should have tint"


class TestResolveSingerColors:
    """Tests for resolve_singer_colors — per-singer color resolution."""

    def test_resolve_singer1_falls_back_to_default_palette_when_theme_has_no_override(self):
        """Theme with empty singers block → fall back to DEFAULT_KARAOKE_STYLE's
        built-in blue/pink/yellow palette so duets still look distinct even
        when the theme doesn't declare per-singer colors."""
        from karaoke_gen.style_loader import resolve_singer_colors, DEFAULT_KARAOKE_STYLE
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            "singers": {},
        }
        colors = resolve_singer_colors(karaoke, 1)
        default_singer1 = DEFAULT_KARAOKE_STYLE["singers"]["1"]
        assert colors["primary_color"] == default_singer1["primary_color"]
        assert colors["outline_color"] == default_singer1["outline_color"]

    def test_resolve_singer2_theme_override_wins_over_defaults(self):
        """Theme-supplied per-singer override takes precedence over the
        built-in default palette; non-overridden fields fall back to the
        default palette (not the flat theme colors)."""
        from karaoke_gen.style_loader import resolve_singer_colors, DEFAULT_KARAOKE_STYLE
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            "singers": {"2": {"primary_color": "9, 9, 9, 255"}},
        }
        colors = resolve_singer_colors(karaoke, 2)
        default_singer2 = DEFAULT_KARAOKE_STYLE["singers"]["2"]
        assert colors["primary_color"] == "9, 9, 9, 255"
        # Fields not overridden fall back to the default duet palette
        assert colors["secondary_color"] == default_singer2["secondary_color"]
        assert colors["outline_color"] == default_singer2["outline_color"]

    def test_resolve_both_uses_both_key_not_numeric(self):
        from karaoke_gen.style_loader import resolve_singer_colors
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            "singers": {"both": {"primary_color": "7, 7, 7, 255"}},
        }
        colors = resolve_singer_colors(karaoke, 0)
        assert colors["primary_color"] == "7, 7, 7, 255"

    def test_resolve_handles_missing_singers_block(self):
        """Theme with no "singers" key at all falls back to DEFAULT_KARAOKE_STYLE's
        built-in palette for per-singer colors."""
        from karaoke_gen.style_loader import resolve_singer_colors, DEFAULT_KARAOKE_STYLE
        karaoke = {
            "primary_color": "1, 1, 1, 255",
            "secondary_color": "2, 2, 2, 255",
            "outline_color": "3, 3, 3, 255",
            "back_color": "0, 0, 0, 0",
            # no "singers" key
        }
        colors = resolve_singer_colors(karaoke, 2)
        # Primary color comes from the built-in Singer 2 default (pink signature)
        assert colors["primary_color"] == DEFAULT_KARAOKE_STYLE["singers"]["2"]["primary_color"]


class TestCdgDuetSingers:
    def test_cdg_duet_singers_has_three_entries(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        assert len(CDG_DUET_SINGERS) == 3

    def test_cdg_duet_singer_colors_are_rgb_tuples(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        for s in CDG_DUET_SINGERS:
            assert len(s.active_fill) == 3
            assert all(isinstance(c, int) for c in s.active_fill)

    def test_cdg_duet_singer_1_is_blue(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        # Color model (post-flip): inactive = signature color (pre-sung),
        # active = white (sweep highlight). Singer 1 signature = sky blue
        # (brightened from #7070F7 to #9AA8FF for contrast on dark backgrounds).
        assert CDG_DUET_SINGERS[0].inactive_fill == (154, 168, 255)
        assert CDG_DUET_SINGERS[0].active_fill == (255, 255, 255)

    def test_cdg_duet_singer_2_is_pink(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        # Singer 2 signature = pink
        assert CDG_DUET_SINGERS[1].inactive_fill == (247, 112, 180)
        assert CDG_DUET_SINGERS[1].active_fill == (255, 255, 255)

    def test_cdg_duet_singer_both_is_yellow(self):
        from karaoke_gen.style_loader import CDG_DUET_SINGERS
        # Both (SingerId 0 → CDG singer 3) signature = yellow
        assert CDG_DUET_SINGERS[2].inactive_fill == (252, 211, 77)
        assert CDG_DUET_SINGERS[2].active_fill == (255, 255, 255)
