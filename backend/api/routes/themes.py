"""
Theme API routes for listing and retrieving theme information.

Themes are pre-created style configurations that users can select when creating jobs.
These endpoints are public (no authentication required) to allow theme selection
before job creation.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from backend.models.theme import ThemeDetailResponse, ThemesListResponse
from backend.services.theme_service import get_theme_service
from backend.i18n import t, get_locale_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/themes", tags=["themes"])


@router.get("", response_model=ThemesListResponse)
async def list_themes(request: Request) -> ThemesListResponse:
    """
    List all available themes.

    Returns a list of themes with their metadata and preview image URLs.
    Preview URLs are signed and valid for 60 minutes.

    This endpoint is public and does not require authentication.
    """
    locale = get_locale_from_request(request)
    try:
        theme_service = get_theme_service()
        themes = theme_service.list_themes()
        return ThemesListResponse(themes=themes)
    except Exception as e:
        logger.error(f"Error listing themes: {e}")
        raise HTTPException(
            status_code=500,
            detail=t(locale, "themes.themesLoadError")
        )


@router.get("/{theme_id}", response_model=ThemeDetailResponse)
async def get_theme(theme_id: str, request: Request) -> ThemeDetailResponse:
    """
    Get detailed information about a specific theme.

    Returns the full theme details including style parameters.
    This can be used to preview theme settings or inspect configuration.

    Args:
        theme_id: The unique identifier of the theme

    Returns:
        ThemeDetailResponse with full theme details

    Raises:
        404: Theme not found
    """
    locale = get_locale_from_request(request)
    try:
        theme_service = get_theme_service()
        theme = theme_service.get_theme(theme_id)

        if theme is None:
            raise HTTPException(
                status_code=404,
                detail=t(locale, "themes.themeNotFound", theme_id=theme_id)
            )

        return ThemeDetailResponse(theme=theme)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting theme {theme_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=t(locale, "themes.themeLoadError")
        )


@router.get("/{theme_id}/preview")
async def get_theme_preview(theme_id: str, request: Request) -> dict:
    """
    Get a signed URL for the theme's preview image.

    This endpoint is useful when you need just the preview URL
    without loading the full theme details.

    Args:
        theme_id: The unique identifier of the theme

    Returns:
        Object with preview_url field

    Raises:
        404: Theme not found or no preview available
    """
    locale = get_locale_from_request(request)
    try:
        theme_service = get_theme_service()

        if not theme_service.theme_exists(theme_id):
            raise HTTPException(
                status_code=404,
                detail=t(locale, "themes.themeNotFound", theme_id=theme_id)
            )

        theme = theme_service.get_theme(theme_id)
        if theme is None or theme.preview_url is None:
            raise HTTPException(
                status_code=404,
                detail=t(locale, "themes.previewNotAvailable", theme_id=theme_id)
            )

        return {"preview_url": theme.preview_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting preview for theme {theme_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=t(locale, "themes.previewLoadError")
        )


@router.get("/{theme_id}/youtube-description")
async def get_theme_youtube_description(theme_id: str, request: Request) -> dict:
    """
    Get the YouTube description template for a theme.

    Some themes include a default YouTube video description template
    that can be used when uploading videos.

    Args:
        theme_id: The unique identifier of the theme

    Returns:
        Object with description field containing the template text,
        or null if no template is available

    Raises:
        404: Theme not found
    """
    locale = get_locale_from_request(request)
    try:
        theme_service = get_theme_service()

        if not theme_service.theme_exists(theme_id):
            raise HTTPException(
                status_code=404,
                detail=t(locale, "themes.youtubeDescriptionNotFound", theme_id=theme_id)
            )

        description = theme_service.get_youtube_description(theme_id)
        return {"description": description}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting YouTube description for theme {theme_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=t(locale, "themes.youtubeDescriptionLoadError")
        )
