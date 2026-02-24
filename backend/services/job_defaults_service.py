"""
Centralized job defaults service.

This module provides consistent handling of job creation defaults across all
endpoints (file_upload, audio_search, made-for-you webhook, etc.).

Centralizing these defaults prevents divergence between code paths and ensures
all jobs receive the same default configuration.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from backend.config import get_settings


@dataclass
class EffectiveDistributionSettings:
    """Distribution settings with defaults applied from environment variables."""
    dropbox_path: Optional[str]
    gdrive_folder_id: Optional[str]
    discord_webhook_url: Optional[str]
    brand_prefix: Optional[str]
    enable_youtube_upload: bool
    youtube_description: Optional[str]


def get_effective_distribution_settings(
    dropbox_path: Optional[str] = None,
    gdrive_folder_id: Optional[str] = None,
    discord_webhook_url: Optional[str] = None,
    brand_prefix: Optional[str] = None,
    enable_youtube_upload: Optional[bool] = None,
    youtube_description: Optional[str] = None,
) -> EffectiveDistributionSettings:
    """
    Get distribution settings with defaults applied from environment variables.

    This ensures consistent handling of defaults across all job creation endpoints.
    Each parameter, if not provided (None), falls back to the corresponding
    environment variable configured in settings.

    Args:
        dropbox_path: Explicit Dropbox path or None for default
        gdrive_folder_id: Explicit Google Drive folder ID or None for default
        discord_webhook_url: Explicit Discord webhook URL or None for default
        brand_prefix: Explicit brand prefix or None for default
        enable_youtube_upload: Explicit flag or None for default
        youtube_description: Explicit description or None for default

    Returns:
        EffectiveDistributionSettings with defaults applied
    """
    settings = get_settings()
    return EffectiveDistributionSettings(
        dropbox_path=dropbox_path or settings.default_dropbox_path,
        gdrive_folder_id=gdrive_folder_id or settings.default_gdrive_folder_id,
        discord_webhook_url=discord_webhook_url or settings.default_discord_webhook_url,
        brand_prefix=brand_prefix or settings.default_brand_prefix,
        enable_youtube_upload=enable_youtube_upload if enable_youtube_upload is not None else settings.default_enable_youtube_upload,
        youtube_description=youtube_description or settings.default_youtube_description,
    )


def get_effective_distribution_for_job(job) -> EffectiveDistributionSettings:
    """
    Get effective distribution settings for a job, applying private overrides if is_private=True.

    For private (non-published) jobs, this overrides:
    - dropbox_path → private Dropbox path (Tracks-NonPublished)
    - brand_prefix → private brand prefix (NOMADNP)
    - enable_youtube_upload → False (no YouTube for private)
    - gdrive_folder_id → None (no Google Drive for private)

    For non-private jobs, returns the job's own distribution settings unchanged.

    Args:
        job: Job object (or any object with distribution fields)

    Returns:
        EffectiveDistributionSettings with private overrides applied if needed
    """
    if getattr(job, 'is_private', False):
        settings = get_settings()
        return EffectiveDistributionSettings(
            dropbox_path=settings.default_private_dropbox_path,
            brand_prefix=settings.default_private_brand_prefix,
            enable_youtube_upload=False,
            gdrive_folder_id=None,
            discord_webhook_url=getattr(job, 'discord_webhook_url', None),
            youtube_description=None,
        )
    # Non-private: return job's own settings
    return EffectiveDistributionSettings(
        dropbox_path=getattr(job, 'dropbox_path', None),
        brand_prefix=getattr(job, 'brand_prefix', None),
        enable_youtube_upload=getattr(job, 'enable_youtube_upload', False),
        gdrive_folder_id=getattr(job, 'gdrive_folder_id', None),
        discord_webhook_url=getattr(job, 'discord_webhook_url', None),
        youtube_description=getattr(job, 'youtube_description_template', None),
    )


def resolve_cdg_txt_defaults(
    theme_id: Optional[str],
    enable_cdg: Optional[bool] = None,
    enable_txt: Optional[bool] = None,
) -> Tuple[bool, bool]:
    """
    Resolve CDG/TXT settings based on theme and explicit settings.

    The resolution logic is:
    1. If explicit True/False is provided, use that value
    2. Otherwise, if a theme is set, use the server defaults (settings.default_enable_cdg/txt)
    3. If no theme is set, default to False (CDG/TXT require style configuration)

    This ensures CDG/TXT are only enabled when:
    - A theme is configured (provides necessary style params), AND
    - The server defaults allow it (DEFAULT_ENABLE_CDG=true by default)

    Args:
        theme_id: Theme identifier (if any)
        enable_cdg: Explicit CDG setting (None means use default)
        enable_txt: Explicit TXT setting (None means use default)

    Returns:
        Tuple of (resolved_enable_cdg, resolved_enable_txt)
    """
    settings = get_settings()

    # Default based on whether theme is set AND server defaults
    # Theme is required because CDG/TXT need style configuration
    theme_is_set = theme_id is not None
    default_cdg = theme_is_set and settings.default_enable_cdg
    default_txt = theme_is_set and settings.default_enable_txt

    # Explicit values override defaults, None uses computed default
    resolved_cdg = enable_cdg if enable_cdg is not None else default_cdg
    resolved_txt = enable_txt if enable_txt is not None else default_txt

    return resolved_cdg, resolved_txt


# Singleton instance (optional, for convenience)
_service_instance = None


def get_job_defaults_service():
    """Get the job defaults service (module-level functions work fine, this is for consistency)."""
    return {
        'get_effective_distribution_settings': get_effective_distribution_settings,
        'resolve_cdg_txt_defaults': resolve_cdg_txt_defaults,
    }
