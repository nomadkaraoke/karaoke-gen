"""Backward-compat shim. Real implementation lives in backend.services.custom_lyrics."""
from backend.services.custom_lyrics.service import (
    CustomLyricsService,
    CustomLyricsServiceError,
    get_custom_lyrics_service,
)
from backend.services.custom_lyrics.result import CustomLyricsResult, StopReason
from backend.services.custom_lyrics.settings import GenerationSettings, StrictnessLevel

__all__ = [
    "CustomLyricsService",
    "CustomLyricsServiceError",
    "CustomLyricsResult",
    "GenerationSettings",
    "StopReason",
    "StrictnessLevel",
    "get_custom_lyrics_service",
]
