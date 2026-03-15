"""
Theme service for managing pre-created style themes.

Themes are stored in GCS at themes/{theme_id}/ with:
- style_params.json: Complete style configuration
- preview.png: Preview image for theme selection UI
- youtube_description.txt: Optional YouTube description template
- assets/: Fonts, backgrounds, and other assets

The service provides:
- Theme listing with signed preview URLs
- Theme detail retrieval
- Color override application
- Job style preparation (copying theme to job folder)
"""

import copy
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.models.theme import (
    ColorOverrides,
    ThemeDetail,
    ThemeMetadata,
    ThemeRegistry,
    ThemeSummary,
    hex_to_rgba,
)
from backend.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# GCS paths for themes
THEMES_PREFIX = "themes"
METADATA_FILE = f"{THEMES_PREFIX}/_metadata.json"


class ThemeService:
    """Service for managing themes from GCS."""

    def __init__(self, storage: Optional[StorageService] = None):
        """
        Initialize the theme service.

        Args:
            storage: StorageService instance (creates new one if not provided)
        """
        self.storage = storage or StorageService()
        self._metadata_cache: Optional[ThemeRegistry] = None
        self._cache_time: Optional[datetime] = None
        self.CACHE_TTL_SECONDS = 300  # 5 minute cache for theme metadata

    def _get_theme_path(self, theme_id: str, filename: str = "") -> str:
        """Get the GCS path for a theme file."""
        if filename:
            return f"{THEMES_PREFIX}/{theme_id}/{filename}"
        return f"{THEMES_PREFIX}/{theme_id}"

    def _is_cache_valid(self) -> bool:
        """Check if the metadata cache is still valid."""
        if self._metadata_cache is None or self._cache_time is None:
            return False
        age = datetime.now() - self._cache_time
        return age.total_seconds() < self.CACHE_TTL_SECONDS

    def _load_metadata(self, force_refresh: bool = False) -> ThemeRegistry:
        """
        Load theme metadata from GCS with caching.

        Args:
            force_refresh: Force reload from GCS even if cache is valid

        Returns:
            ThemeRegistry containing all theme metadata
        """
        if not force_refresh and self._is_cache_valid():
            return self._metadata_cache  # type: ignore

        try:
            data = self.storage.download_json(METADATA_FILE)
            self._metadata_cache = ThemeRegistry(**data)
            self._cache_time = datetime.now()
            logger.info(f"Loaded theme metadata: {len(self._metadata_cache.themes)} themes")
            return self._metadata_cache
        except Exception as e:
            logger.error(f"Failed to load theme metadata: {e}")
            # Return empty registry on error
            return ThemeRegistry(version=1, themes=[])

    def list_themes(self) -> List[ThemeSummary]:
        """
        List all available themes with signed preview URLs.

        Returns:
            List of ThemeSummary objects with preview URLs
        """
        metadata = self._load_metadata()
        summaries = []

        for theme in metadata.themes:
            try:
                # Generate signed URLs for preview images
                preview_path = self._get_theme_path(theme.id, "preview.png")
                thumbnail_path = self._get_theme_path(theme.id, "preview_thumbnail.png")

                preview_url = None
                thumbnail_url = None

                if self.storage.file_exists(preview_path):
                    preview_url = self.storage.generate_signed_url(preview_path, expiration_minutes=60)

                # Try thumbnail, fall back to main preview
                if self.storage.file_exists(thumbnail_path):
                    thumbnail_url = self.storage.generate_signed_url(thumbnail_path, expiration_minutes=60)
                elif preview_url:
                    thumbnail_url = preview_url

                summaries.append(
                    ThemeSummary(
                        id=theme.id,
                        name=theme.name,
                        description=theme.description,
                        preview_url=preview_url,
                        thumbnail_url=thumbnail_url,
                        is_default=theme.is_default,
                    )
                )
            except Exception as e:
                logger.warning(f"Error processing theme {theme.id}: {e}")
                # Still include theme but without preview URLs
                summaries.append(
                    ThemeSummary(
                        id=theme.id,
                        name=theme.name,
                        description=theme.description,
                        is_default=theme.is_default,
                    )
                )

        return summaries

    def get_theme(self, theme_id: str) -> Optional[ThemeDetail]:
        """
        Get full theme details including style parameters.

        Args:
            theme_id: The theme identifier

        Returns:
            ThemeDetail if found, None otherwise
        """
        metadata = self._load_metadata()

        # Find theme in metadata
        theme_meta = next((t for t in metadata.themes if t.id == theme_id), None)
        if not theme_meta:
            logger.warning(f"Theme not found: {theme_id}")
            return None

        try:
            # Load style_params.json
            style_params_path = self._get_theme_path(theme_id, "style_params.json")
            style_params = self.storage.download_json(style_params_path)

            # Check for YouTube description
            youtube_desc_path = self._get_theme_path(theme_id, "youtube_description.txt")
            has_youtube_desc = self.storage.file_exists(youtube_desc_path)

            # Generate preview URL
            preview_path = self._get_theme_path(theme_id, "preview.png")
            preview_url = None
            if self.storage.file_exists(preview_path):
                preview_url = self.storage.generate_signed_url(preview_path, expiration_minutes=60)

            return ThemeDetail(
                id=theme_id,
                name=theme_meta.name,
                description=theme_meta.description,
                preview_url=preview_url,
                is_default=theme_meta.is_default,
                style_params=style_params,
                has_youtube_description=has_youtube_desc,
            )
        except Exception as e:
            logger.error(f"Error loading theme {theme_id}: {e}")
            return None

    def get_theme_style_params(self, theme_id: str) -> Optional[Dict[str, Any]]:
        """
        Get just the style_params.json for a theme.

        Args:
            theme_id: The theme identifier

        Returns:
            Style parameters dict if found, None otherwise
        """
        try:
            style_params_path = self._get_theme_path(theme_id, "style_params.json")
            return self.storage.download_json(style_params_path)
        except Exception as e:
            logger.error(f"Error loading style params for theme {theme_id}: {e}")
            return None

    def get_youtube_description(self, theme_id: str) -> Optional[str]:
        """
        Get the YouTube description template for a theme.

        Args:
            theme_id: The theme identifier

        Returns:
            YouTube description text if found, None otherwise
        """
        try:
            youtube_desc_path = self._get_theme_path(theme_id, "youtube_description.txt")
            if not self.storage.file_exists(youtube_desc_path):
                return None

            # Download to temp file and read
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
                tmp_path = tmp.name

            try:
                self.storage.download_file(youtube_desc_path, tmp_path)
                with open(tmp_path, "r") as f:
                    return f.read()
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Error loading YouTube description for theme {theme_id}: {e}")
            return None

    def apply_color_overrides(
        self, style_params: Dict[str, Any], overrides: ColorOverrides
    ) -> Dict[str, Any]:
        """
        Apply user color overrides to theme style parameters.

        Args:
            style_params: Base style parameters from theme
            overrides: User color overrides

        Returns:
            Modified style parameters with overrides applied
        """
        if not overrides.has_overrides():
            return style_params

        # Deep copy to avoid modifying original
        result = copy.deepcopy(style_params)

        # Apply artist_color to intro, end, and CDG sections
        if overrides.artist_color:
            if "intro" in result:
                result["intro"]["artist_color"] = overrides.artist_color
            if "end" in result:
                result["end"]["artist_color"] = overrides.artist_color
            if "cdg" in result:
                result["cdg"]["artist_color"] = overrides.artist_color

        # Apply title_color to intro, end, and CDG sections
        if overrides.title_color:
            if "intro" in result:
                result["intro"]["title_color"] = overrides.title_color
            if "end" in result:
                result["end"]["title_color"] = overrides.title_color
            if "cdg" in result:
                result["cdg"]["title_color"] = overrides.title_color

        # Apply sung_lyrics_color (karaoke uses RGBA, CDG uses hex)
        if overrides.sung_lyrics_color:
            if "karaoke" in result:
                result["karaoke"]["primary_color"] = hex_to_rgba(overrides.sung_lyrics_color)
            if "cdg" in result:
                result["cdg"]["active_fill"] = overrides.sung_lyrics_color

        # Apply unsung_lyrics_color (karaoke uses RGBA, CDG uses hex)
        if overrides.unsung_lyrics_color:
            if "karaoke" in result:
                result["karaoke"]["secondary_color"] = hex_to_rgba(overrides.unsung_lyrics_color)
            if "cdg" in result:
                result["cdg"]["inactive_fill"] = overrides.unsung_lyrics_color

        return result

    def prepare_job_style(
        self,
        job_id: str,
        theme_id: str,
        color_overrides: Optional[ColorOverrides] = None,
    ) -> Tuple[str, Dict[str, str]]:
        """
        Prepare style files for a job by copying theme to job folder.

        This method:
        1. Loads the theme's style_params.json
        2. Applies any color overrides
        3. Updates asset paths to point to theme assets (shared, not copied)
        4. Uploads modified style_params.json to job folder
        5. Returns the GCS path and style_assets mapping

        Args:
            job_id: The job ID
            theme_id: The theme to use
            color_overrides: Optional color overrides

        Returns:
            Tuple of (style_params_gcs_path, style_assets dict)

        Raises:
            ValueError: If theme not found
        """
        # Load theme style params
        style_params = self.get_theme_style_params(theme_id)
        if style_params is None:
            raise ValueError(f"Theme not found: {theme_id}")

        # Apply color overrides if provided
        if color_overrides:
            style_params = self.apply_color_overrides(style_params, color_overrides)

        # Update asset paths to point to theme's shared assets
        # Theme assets stay in themes/{theme_id}/assets/ - they're shared
        style_assets = self._build_style_assets_mapping(theme_id, style_params)

        # Update style_params to use theme asset paths (not job-specific)
        style_params = self._update_asset_paths_in_style(theme_id, style_params)

        # Upload modified style_params.json to job's style folder
        # Uses jobs/ prefix (not uploads/) to avoid GCS lifecycle deletion rule
        job_style_path = f"jobs/{job_id}/style/style_params.json"
        self.storage.upload_json(job_style_path, style_params)

        logger.info(f"Prepared job {job_id} style from theme {theme_id}")

        return job_style_path, style_assets

    def _build_style_assets_mapping(
        self, theme_id: str, style_params: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Build the style_assets mapping for a theme.

        Maps asset keys to their GCS paths in the theme folder.
        """
        style_assets = {}
        theme_assets_prefix = f"{THEMES_PREFIX}/{theme_id}/assets"

        # Check each potential asset type
        asset_checks = [
            ("intro_background", "intro", "background_image"),
            ("karaoke_background", "karaoke", "background_image"),
            ("end_background", "end", "background_image"),
            ("font", "intro", "font"),  # Font is typically shared across sections
            ("cdg_instrumental_background", "cdg", "instrumental_background"),
            ("cdg_title_background", "cdg", "title_screen_background"),
            ("cdg_outro_background", "cdg", "outro_background"),
        ]

        for asset_key, section, field in asset_checks:
            if section in style_params and field in style_params[section]:
                value = style_params[section][field]
                if value and isinstance(value, str):
                    # Check if it's a theme asset path or already has the full path
                    if value.startswith(f"{THEMES_PREFIX}/"):
                        # Already a full theme path
                        style_assets[asset_key] = value
                    elif not value.startswith("/") and not value.startswith("gs://"):
                        # Relative path - prepend theme assets prefix
                        asset_path = f"{theme_assets_prefix}/{os.path.basename(value)}"
                        if self.storage.file_exists(asset_path):
                            style_assets[asset_key] = asset_path

        return style_assets

    def _update_asset_paths_in_style(
        self, theme_id: str, style_params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update asset paths in style_params to use full GCS theme paths.

        This ensures the style_loader can find assets when processing.
        """
        result = copy.deepcopy(style_params)
        theme_assets_prefix = f"{THEMES_PREFIX}/{theme_id}/assets"

        # Fields that contain asset paths
        path_fields = {
            "intro": ["background_image", "font"],
            "end": ["background_image", "font"],
            "karaoke": ["background_image", "font_path"],
            "cdg": [
                "font_path",
                "instrumental_background",
                "title_screen_background",
                "outro_background",
            ],
        }

        for section, fields in path_fields.items():
            if section not in result:
                continue
            for field in fields:
                if field not in result[section]:
                    continue
                value = result[section][field]
                if value and isinstance(value, str):
                    # Skip if already a full path or URL
                    if value.startswith(f"{THEMES_PREFIX}/") or value.startswith("gs://"):
                        continue
                    # Skip absolute local paths (shouldn't happen but be safe)
                    if value.startswith("/"):
                        continue
                    # Update to full theme asset path
                    result[section][field] = f"{theme_assets_prefix}/{os.path.basename(value)}"

        return result

    def theme_exists(self, theme_id: str) -> bool:
        """
        Check if a theme exists.

        Args:
            theme_id: The theme identifier

        Returns:
            True if theme exists, False otherwise
        """
        metadata = self._load_metadata()
        return any(t.id == theme_id for t in metadata.themes)

    def get_default_theme_id(self) -> Optional[str]:
        """
        Get the ID of the default theme.

        Returns:
            Default theme ID if one exists, None otherwise
        """
        metadata = self._load_metadata()
        default_theme = next((t for t in metadata.themes if t.is_default), None)
        return default_theme.id if default_theme else None

    def invalidate_cache(self) -> None:
        """Force invalidation of the metadata cache."""
        self._metadata_cache = None
        self._cache_time = None
        logger.info("Theme metadata cache invalidated")


# Singleton instance with thread-safe initialization
_theme_service: Optional[ThemeService] = None
_theme_service_lock = threading.Lock()


def get_theme_service() -> ThemeService:
    """Get or create the singleton ThemeService instance (thread-safe)."""
    global _theme_service
    if _theme_service is None:
        with _theme_service_lock:
            # Double-check after acquiring lock
            if _theme_service is None:
                _theme_service = ThemeService()
    return _theme_service
