"""
Instrumental Review Module - Audio analysis and editing for karaoke generation.

This module provides audio analysis and editing functionality for instrumental
selection in karaoke generation. It's designed to be:
- Pure Python with no cloud dependencies (GCS, etc.)
- Reusable by both local CLI (karaoke-gen) and remote backend (Cloud Run)
- Easy to test without mocking cloud services

Classes:
    AudioAnalyzer: Analyzes backing vocals audio for audible content
    AudioEditor: Creates custom instrumentals by muting regions
    WaveformGenerator: Generates waveform visualization images

Models:
    AnalysisResult: Result of audio analysis
    AudibleSegment: A detected segment of audible content
    MuteRegion: A region to mute in the backing vocals
    RecommendedSelection: Enum of selection recommendations

Note: The standalone InstrumentalReviewServer has been removed. Instrumental
review is now integrated into the combined review flow via ReviewServer
(karaoke_gen.lyrics_transcriber.review.server.ReviewServer).
"""

from .models import (
    AnalysisResult,
    AudibleSegment,
    MuteRegion,
    RecommendedSelection,
)
from .analyzer import AudioAnalyzer
from .editor import AudioEditor
from .waveform import WaveformGenerator

__all__ = [
    # Models
    "AnalysisResult",
    "AudibleSegment",
    "MuteRegion",
    "RecommendedSelection",
    # Classes
    "AudioAnalyzer",
    "AudioEditor",
    "WaveformGenerator",
]
