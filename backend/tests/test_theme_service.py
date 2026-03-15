"""
Tests for the theme service and API endpoints.

Tests theme listing, retrieval, color override application, and job style preparation.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, Mock
from datetime import datetime

from backend.models.theme import (
    ThemeSummary,
    ThemeDetail,
    ThemeMetadata,
    ThemeRegistry,
    ColorOverrides,
    hex_to_rgba,
    rgba_to_hex,
)
from backend.services.theme_service import ThemeService, get_theme_service


# =============================================================================
# Color Conversion Tests
# =============================================================================

class TestColorConversion:
    """Tests for hex/rgba color conversion utilities."""

    def test_hex_to_rgba_basic(self):
        """Test basic hex to RGBA conversion."""
        result = hex_to_rgba("#7070F7")
        assert result == "112, 112, 247, 255"

    def test_hex_to_rgba_with_custom_alpha(self):
        """Test hex to RGBA with custom alpha."""
        result = hex_to_rgba("#FF0000", alpha=128)
        assert result == "255, 0, 0, 128"

    def test_hex_to_rgba_lowercase(self):
        """Test hex with lowercase letters."""
        result = hex_to_rgba("#abcdef")
        assert result == "171, 205, 239, 255"

    def test_hex_to_rgba_without_hash(self):
        """Test hex without leading hash."""
        result = hex_to_rgba("FFFFFF")
        assert result == "255, 255, 255, 255"

    def test_rgba_to_hex_basic(self):
        """Test basic RGBA to hex conversion."""
        result = rgba_to_hex("112, 112, 247, 255")
        assert result == "#7070f7"

    def test_rgba_to_hex_ignores_alpha(self):
        """Test that alpha is ignored in conversion."""
        result = rgba_to_hex("255, 0, 0, 128")
        assert result == "#ff0000"


# =============================================================================
# ColorOverrides Model Tests
# =============================================================================

class TestColorOverrides:
    """Tests for ColorOverrides model."""

    def test_has_overrides_empty(self):
        """Test has_overrides with no overrides set."""
        overrides = ColorOverrides()
        assert overrides.has_overrides() is False

    def test_has_overrides_with_artist_color(self):
        """Test has_overrides with artist_color set."""
        overrides = ColorOverrides(artist_color="#FF0000")
        assert overrides.has_overrides() is True

    def test_has_overrides_with_all_colors(self):
        """Test has_overrides with all colors set."""
        overrides = ColorOverrides(
            artist_color="#FF0000",
            title_color="#00FF00",
            sung_lyrics_color="#0000FF",
            unsung_lyrics_color="#FFFF00",
        )
        assert overrides.has_overrides() is True

    def test_to_dict_excludes_none(self):
        """Test to_dict excludes None values."""
        overrides = ColorOverrides(artist_color="#FF0000")
        result = overrides.to_dict()
        assert result == {"artist_color": "#FF0000"}
        assert "title_color" not in result

    def test_color_validation_valid(self):
        """Test color validation accepts valid hex colors."""
        overrides = ColorOverrides(artist_color="#AbCdEf")
        assert overrides.artist_color == "#AbCdEf"

    def test_color_validation_invalid(self):
        """Test color validation rejects invalid colors."""
        with pytest.raises(ValueError):
            ColorOverrides(artist_color="red")

        with pytest.raises(ValueError):
            ColorOverrides(artist_color="#GGG")


# =============================================================================
# ThemeService Tests
# =============================================================================

class TestThemeService:
    """Tests for ThemeService."""

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage service."""
        storage = MagicMock()
        return storage

    @pytest.fixture
    def sample_metadata(self):
        """Sample theme metadata for testing."""
        return {
            "version": 1,
            "themes": [
                {
                    "id": "nomad",
                    "name": "Nomad Karaoke",
                    "description": "Golden artist text, professional look",
                    "is_default": True,
                },
                {
                    "id": "default",
                    "name": "Default",
                    "description": "Clean white text on black background",
                    "is_default": False,
                },
            ],
        }

    @pytest.fixture
    def sample_style_params(self):
        """Sample style params for testing."""
        return {
            "intro": {
                "artist_color": "#ffdf6b",
                "title_color": "#ffffff",
                "background_image": "intro_bg.png",
                "font": "Montserrat-Bold.ttf",
            },
            "end": {
                "artist_color": "#ffdf6b",
                "title_color": "#ffffff",
                "background_image": "end_bg.png",
                "font": "Montserrat-Bold.ttf",
            },
            "karaoke": {
                "primary_color": "112, 112, 247, 255",
                "secondary_color": "255, 255, 255, 255",
                "background_image": "karaoke_bg.png",
                "font_path": "Montserrat-Bold.ttf",
            },
            "cdg": {
                "active_fill": "#7070F7",
                "inactive_fill": "#FFFFFF",
                "font_path": "Montserrat-Bold.ttf",
            },
        }

    def test_list_themes_returns_summaries(self, mock_storage, sample_metadata):
        """Test list_themes returns ThemeSummary objects."""
        mock_storage.download_json.return_value = sample_metadata
        mock_storage.file_exists.return_value = True
        mock_storage.generate_signed_url.return_value = "https://signed-url.com/preview.png"

        service = ThemeService(storage=mock_storage)
        themes = service.list_themes()

        assert len(themes) == 2
        assert themes[0].id == "nomad"
        assert themes[0].name == "Nomad Karaoke"
        assert themes[0].is_default is True
        assert themes[0].preview_url is not None

    def test_list_themes_handles_missing_preview(self, mock_storage, sample_metadata):
        """Test list_themes handles missing preview images gracefully."""
        mock_storage.download_json.return_value = sample_metadata
        mock_storage.file_exists.return_value = False

        service = ThemeService(storage=mock_storage)
        themes = service.list_themes()

        assert len(themes) == 2
        assert themes[0].preview_url is None

    def test_get_theme_returns_detail(self, mock_storage, sample_metadata, sample_style_params):
        """Test get_theme returns ThemeDetail with style params."""
        mock_storage.download_json.side_effect = [sample_metadata, sample_style_params]
        mock_storage.file_exists.return_value = True
        mock_storage.generate_signed_url.return_value = "https://signed-url.com/preview.png"

        service = ThemeService(storage=mock_storage)
        theme = service.get_theme("nomad")

        assert theme is not None
        assert theme.id == "nomad"
        assert theme.name == "Nomad Karaoke"
        assert "intro" in theme.style_params
        assert theme.style_params["intro"]["artist_color"] == "#ffdf6b"

    def test_get_theme_returns_none_for_unknown(self, mock_storage, sample_metadata):
        """Test get_theme returns None for unknown theme ID."""
        mock_storage.download_json.return_value = sample_metadata

        service = ThemeService(storage=mock_storage)
        theme = service.get_theme("unknown-theme")

        assert theme is None

    def test_theme_exists_true(self, mock_storage, sample_metadata):
        """Test theme_exists returns True for existing theme."""
        mock_storage.download_json.return_value = sample_metadata

        service = ThemeService(storage=mock_storage)
        assert service.theme_exists("nomad") is True

    def test_theme_exists_false(self, mock_storage, sample_metadata):
        """Test theme_exists returns False for unknown theme."""
        mock_storage.download_json.return_value = sample_metadata

        service = ThemeService(storage=mock_storage)
        assert service.theme_exists("unknown") is False

    def test_get_default_theme_id(self, mock_storage, sample_metadata):
        """Test get_default_theme_id returns the default theme."""
        mock_storage.download_json.return_value = sample_metadata

        service = ThemeService(storage=mock_storage)
        assert service.get_default_theme_id() == "nomad"

    def test_get_default_theme_id_no_default(self, mock_storage):
        """Test get_default_theme_id returns None when no default."""
        mock_storage.download_json.return_value = {
            "version": 1,
            "themes": [{"id": "test", "name": "Test", "description": "Test", "is_default": False}],
        }

        service = ThemeService(storage=mock_storage)
        assert service.get_default_theme_id() is None

    def test_apply_color_overrides_no_changes(self, sample_style_params):
        """Test apply_color_overrides with empty overrides."""
        service = ThemeService()
        overrides = ColorOverrides()

        result = service.apply_color_overrides(sample_style_params, overrides)

        # Should return original (no deep copy needed when no changes)
        assert result == sample_style_params

    def test_apply_color_overrides_artist_color(self, sample_style_params):
        """Test apply_color_overrides applies artist_color."""
        service = ThemeService()
        overrides = ColorOverrides(artist_color="#FF0000")

        result = service.apply_color_overrides(sample_style_params, overrides)

        assert result["intro"]["artist_color"] == "#FF0000"
        assert result["end"]["artist_color"] == "#FF0000"
        assert result["cdg"]["artist_color"] == "#FF0000"
        # Original should be unchanged
        assert sample_style_params["intro"]["artist_color"] == "#ffdf6b"

    def test_apply_color_overrides_sung_lyrics_color(self, sample_style_params):
        """Test apply_color_overrides applies sung_lyrics_color with conversion."""
        service = ThemeService()
        overrides = ColorOverrides(sung_lyrics_color="#FF0000")

        result = service.apply_color_overrides(sample_style_params, overrides)

        # Karaoke uses RGBA format
        assert result["karaoke"]["primary_color"] == "255, 0, 0, 255"
        # CDG uses hex
        assert result["cdg"]["active_fill"] == "#FF0000"

    def test_apply_color_overrides_unsung_lyrics_color(self, sample_style_params):
        """Test apply_color_overrides applies unsung_lyrics_color."""
        service = ThemeService()
        overrides = ColorOverrides(unsung_lyrics_color="#00FF00")

        result = service.apply_color_overrides(sample_style_params, overrides)

        assert result["karaoke"]["secondary_color"] == "0, 255, 0, 255"
        assert result["cdg"]["inactive_fill"] == "#00FF00"

    def test_metadata_cache(self, mock_storage, sample_metadata):
        """Test metadata caching works."""
        mock_storage.download_json.return_value = sample_metadata

        service = ThemeService(storage=mock_storage)

        # First call loads from GCS
        service.list_themes()
        assert mock_storage.download_json.call_count == 1

        # Second call uses cache
        service.list_themes()
        assert mock_storage.download_json.call_count == 1

    def test_invalidate_cache(self, mock_storage, sample_metadata):
        """Test cache invalidation."""
        mock_storage.download_json.return_value = sample_metadata

        service = ThemeService(storage=mock_storage)

        # Load metadata
        service.list_themes()
        assert mock_storage.download_json.call_count == 1

        # Invalidate cache
        service.invalidate_cache()

        # Next call should reload
        service.list_themes()
        assert mock_storage.download_json.call_count == 2

    def test_prepare_job_style(self, mock_storage, sample_metadata, sample_style_params):
        """Test prepare_job_style creates job style from theme."""
        mock_storage.download_json.side_effect = [sample_metadata, sample_style_params]
        mock_storage.file_exists.return_value = True

        service = ThemeService(storage=mock_storage)
        style_path, style_assets = service.prepare_job_style("job123", "nomad")

        # Should upload modified style_params.json
        mock_storage.upload_json.assert_called_once()
        assert style_path == "jobs/job123/style/style_params.json"

    def test_prepare_job_style_with_overrides(self, mock_storage, sample_metadata, sample_style_params):
        """Test prepare_job_style applies color overrides."""
        # prepare_job_style calls get_theme_style_params which directly downloads style_params.json
        # No metadata lookup happens unless we've cached it previously
        mock_storage.download_json.return_value = sample_style_params
        mock_storage.file_exists.return_value = True

        service = ThemeService(storage=mock_storage)
        overrides = ColorOverrides(artist_color="#FF0000")

        style_path, style_assets = service.prepare_job_style("job123", "nomad", overrides)

        # Check that upload_json was called with modified style params
        call_args = mock_storage.upload_json.call_args
        uploaded_path = call_args[0][0]  # First positional arg is the path
        uploaded_style = call_args[0][1]  # Second positional arg is the data

        assert uploaded_path == "jobs/job123/style/style_params.json"
        # Check that artist_color override was applied
        assert uploaded_style["intro"]["artist_color"] == "#FF0000"
        assert uploaded_style["end"]["artist_color"] == "#FF0000"

    def test_prepare_job_style_unknown_theme_raises(self, mock_storage, sample_metadata):
        """Test prepare_job_style raises for unknown theme."""
        # First download_json returns metadata (no unknown theme)
        # Second download_json for style_params.json should fail
        def download_json_side_effect(path):
            if "_metadata.json" in path:
                return sample_metadata
            # Unknown theme path - raise exception
            raise Exception(f"File not found: {path}")

        mock_storage.download_json.side_effect = download_json_side_effect

        service = ThemeService(storage=mock_storage)

        with pytest.raises(ValueError, match="Theme not found"):
            service.prepare_job_style("job123", "unknown-theme")

    def test_get_youtube_description(self, mock_storage, sample_metadata):
        """Test get_youtube_description returns template text."""
        mock_storage.download_json.return_value = sample_metadata
        mock_storage.file_exists.return_value = True

        # Mock the download_file to write content to temp file
        def mock_download(gcs_path, local_path):
            with open(local_path, "w") as f:
                f.write("Thank you for watching!")

        mock_storage.download_file.side_effect = mock_download

        service = ThemeService(storage=mock_storage)
        desc = service.get_youtube_description("nomad")

        assert desc == "Thank you for watching!"

    def test_get_youtube_description_not_found(self, mock_storage, sample_metadata):
        """Test get_youtube_description returns None when no template."""
        mock_storage.download_json.return_value = sample_metadata
        mock_storage.file_exists.return_value = False

        service = ThemeService(storage=mock_storage)
        desc = service.get_youtube_description("nomad")

        assert desc is None


# =============================================================================
# API Endpoint Tests
# =============================================================================

class TestThemeAPI:
    """Tests for theme API endpoints."""

    @pytest.fixture
    def sample_metadata(self):
        """Sample theme metadata."""
        return {
            "version": 1,
            "themes": [
                {
                    "id": "nomad",
                    "name": "Nomad Karaoke",
                    "description": "Golden artist text",
                    "is_default": True,
                }
            ],
        }

    def test_list_themes_endpoint(self, test_client, sample_metadata):
        """Test GET /api/themes returns theme list."""
        with patch("backend.api.routes.themes.get_theme_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_themes.return_value = [
                ThemeSummary(
                    id="nomad",
                    name="Nomad Karaoke",
                    description="Golden artist text",
                    preview_url="https://example.com/preview.png",
                    is_default=True,
                )
            ]
            mock_get_service.return_value = mock_service

            response = test_client.get("/api/themes")

            assert response.status_code == 200
            data = response.json()
            assert len(data["themes"]) == 1
            assert data["themes"][0]["id"] == "nomad"
            assert data["themes"][0]["is_default"] is True

    def test_get_theme_endpoint(self, test_client):
        """Test GET /api/themes/{theme_id} returns theme detail."""
        with patch("backend.api.routes.themes.get_theme_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_theme.return_value = ThemeDetail(
                id="nomad",
                name="Nomad Karaoke",
                description="Golden artist text",
                is_default=True,
                style_params={"intro": {"artist_color": "#ffdf6b"}},
            )
            mock_get_service.return_value = mock_service

            response = test_client.get("/api/themes/nomad")

            assert response.status_code == 200
            data = response.json()
            assert data["theme"]["id"] == "nomad"
            assert "style_params" in data["theme"]

    def test_get_theme_not_found(self, test_client):
        """Test GET /api/themes/{theme_id} returns 404 for unknown theme."""
        with patch("backend.api.routes.themes.get_theme_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_theme.return_value = None
            mock_get_service.return_value = mock_service

            response = test_client.get("/api/themes/unknown")

            assert response.status_code == 404

    def test_get_theme_preview_endpoint(self, test_client):
        """Test GET /api/themes/{theme_id}/preview returns preview URL."""
        with patch("backend.api.routes.themes.get_theme_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.theme_exists.return_value = True
            mock_service.get_theme.return_value = ThemeDetail(
                id="nomad",
                name="Nomad Karaoke",
                description="Golden artist text",
                preview_url="https://example.com/preview.png",
                is_default=True,
            )
            mock_get_service.return_value = mock_service

            response = test_client.get("/api/themes/nomad/preview")

            assert response.status_code == 200
            data = response.json()
            assert "preview_url" in data

    def test_get_youtube_description_endpoint(self, test_client):
        """Test GET /api/themes/{theme_id}/youtube-description returns description."""
        with patch("backend.api.routes.themes.get_theme_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.theme_exists.return_value = True
            mock_service.get_youtube_description.return_value = "Thank you for watching!"
            mock_get_service.return_value = mock_service

            response = test_client.get("/api/themes/nomad/youtube-description")

            assert response.status_code == 200
            data = response.json()
            assert data["description"] == "Thank you for watching!"
