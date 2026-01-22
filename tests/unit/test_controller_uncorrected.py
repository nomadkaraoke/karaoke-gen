"""Tests for LyricsTranscriber._create_uncorrected_result() method.

These tests verify that anchor sequences and gap sequences are properly calculated
even when auto-correction is disabled (SKIP_CORRECTION=true).
"""

import logging
import pytest
from unittest.mock import MagicMock, patch
import tempfile
import os

from karaoke_gen.lyrics_transcriber.core.controller import LyricsTranscriber, LyricsControllerResult
from karaoke_gen.lyrics_transcriber.core.config import TranscriberConfig, LyricsConfig, OutputConfig
from karaoke_gen.lyrics_transcriber.types import (
    Word,
    LyricsSegment,
    LyricsData,
    LyricsMetadata,
    TranscriptionData,
    TranscriptionResult,
    AnchorSequence,
    GapSequence,
)


@pytest.fixture
def mock_logger():
    """Return a mock logger for testing."""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def sample_transcription_result():
    """Create a sample transcription result with segments and words."""
    words = [
        Word(id="w1", text="Hello", start_time=0.0, end_time=0.5),
        Word(id="w2", text="world", start_time=0.5, end_time=1.0),
        Word(id="w3", text="how", start_time=1.5, end_time=2.0),
        Word(id="w4", text="are", start_time=2.0, end_time=2.5),
        Word(id="w5", text="you", start_time=2.5, end_time=3.0),
    ]
    segments = [
        LyricsSegment(
            id="s1",
            text="Hello world",
            words=words[:2],
            start_time=0.0,
            end_time=1.0,
        ),
        LyricsSegment(
            id="s2",
            text="how are you",
            words=words[2:],
            start_time=1.5,
            end_time=3.0,
        ),
    ]
    transcription_data = TranscriptionData(
        segments=segments,
        words=words,
        text="Hello world how are you",
        source="audioshake",
    )
    return TranscriptionResult(
        name="audioshake",
        priority=1,
        result=transcription_data,
    )


@pytest.fixture
def sample_lyrics_results():
    """Create sample reference lyrics data from multiple sources."""
    # Genius lyrics - matches transcription
    genius_metadata = LyricsMetadata(
        source="genius",
        track_name="Test Song",
        artist_names="Test Artist",
    )
    genius_segments = [
        LyricsSegment(
            id="g1",
            text="Hello world",
            words=[
                Word(id="gw1", text="Hello", start_time=0.0, end_time=0.5),
                Word(id="gw2", text="world", start_time=0.5, end_time=1.0),
            ],
            start_time=0.0,
            end_time=1.0,
        ),
        LyricsSegment(
            id="g2",
            text="how are you",
            words=[
                Word(id="gw3", text="how", start_time=1.5, end_time=2.0),
                Word(id="gw4", text="are", start_time=2.0, end_time=2.5),
                Word(id="gw5", text="you", start_time=2.5, end_time=3.0),
            ],
            start_time=1.5,
            end_time=3.0,
        ),
    ]
    genius_data = LyricsData(
        segments=genius_segments,
        metadata=genius_metadata,
        source="genius",
    )

    # Spotify lyrics - also matches transcription
    spotify_metadata = LyricsMetadata(
        source="spotify",
        track_name="Test Song",
        artist_names="Test Artist",
    )
    spotify_segments = [
        LyricsSegment(
            id="sp1",
            text="Hello world how are you",
            words=[
                Word(id="sw1", text="Hello", start_time=0.0, end_time=0.5),
                Word(id="sw2", text="world", start_time=0.5, end_time=1.0),
                Word(id="sw3", text="how", start_time=1.5, end_time=2.0),
                Word(id="sw4", text="are", start_time=2.0, end_time=2.5),
                Word(id="sw5", text="you", start_time=2.5, end_time=3.0),
            ],
            start_time=0.0,
            end_time=3.0,
        ),
    ]
    spotify_data = LyricsData(
        segments=spotify_segments,
        metadata=spotify_metadata,
        source="spotify",
    )

    return {
        "genius": genius_data,
        "spotify": spotify_data,
    }


@pytest.fixture
def mock_anchor_finder():
    """Create a mock anchor finder that returns sample anchors and gaps."""
    finder = MagicMock()

    # Return sample anchor sequences
    finder.find_anchors.return_value = [
        AnchorSequence(
            words=["Hello", "world"],
            transcription_position=0,
            reference_positions={"genius": 0, "spotify": 0},
            confidence=1.0,
        ),
    ]

    # Return sample gap sequences using keyword arguments for new API
    finder.find_gaps.return_value = [
        GapSequence(
            id="gap1",
            transcribed_word_ids=["w3", "w4", "w5"],
            transcription_position=2,
            preceding_anchor_id=None,
            following_anchor_id=None,
            reference_word_ids={"genius": ["gw3", "gw4", "gw5"], "spotify": ["sw3", "sw4", "sw5"]},
        ),
    ]

    return finder


class TestCreateUncorrectedResult:
    """Tests for _create_uncorrected_result method."""

    def test_creates_result_with_anchors_when_lyrics_available(
        self, mock_logger, temp_dir, sample_transcription_result, sample_lyrics_results, mock_anchor_finder
    ):
        """Verify anchor sequences are calculated when reference lyrics are available."""
        # Create controller with mocked corrector
        with patch.object(LyricsTranscriber, '_initialize_transcribers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_lyrics_providers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_output_generator', return_value=MagicMock()):

            controller = LyricsTranscriber(
                audio_filepath=os.path.join(temp_dir, "test.mp3"),
                artist="Test Artist",
                title="Test Song",
                output_config=OutputConfig(
                    output_styles_json="",
                    output_dir=temp_dir,
                    cache_dir=temp_dir,
                    run_correction=False,
                ),
                logger=mock_logger,
            )

            # Inject mocks - set _anchor_finder directly since the property is read-only
            # but checks _anchor_finder first before creating a new instance
            controller.results.transcription_results = [sample_transcription_result]
            controller.results.lyrics_results = sample_lyrics_results
            controller.corrector._anchor_finder = mock_anchor_finder

            # Call the method under test
            controller._create_uncorrected_result()

            # Verify anchor sequences were calculated
            result = controller.results.transcription_corrected
            assert result is not None
            assert len(result.anchor_sequences) > 0, "Expected anchor sequences to be populated"
            assert len(result.gap_sequences) > 0, "Expected gap sequences to be populated"

            # Verify metadata reflects actual counts
            assert result.metadata["anchor_sequences_count"] == len(result.anchor_sequences)
            assert result.metadata["gap_sequences_count"] == len(result.gap_sequences)

            # Verify no corrections were made (correction disabled)
            assert result.corrections == []
            assert result.corrections_made == 0
            assert result.corrected_segments == result.original_segments

            # Verify anchor finder was called
            mock_anchor_finder.find_anchors.assert_called_once()
            mock_anchor_finder.find_gaps.assert_called_once()

    def test_creates_result_without_anchors_when_no_lyrics(
        self, mock_logger, temp_dir, sample_transcription_result
    ):
        """Verify result is created with empty anchors when no reference lyrics."""
        with patch.object(LyricsTranscriber, '_initialize_transcribers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_lyrics_providers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_output_generator', return_value=MagicMock()):

            controller = LyricsTranscriber(
                audio_filepath=os.path.join(temp_dir, "test.mp3"),
                artist="Test Artist",
                title="Test Song",
                output_config=OutputConfig(
                    output_styles_json="",
                    output_dir=temp_dir,
                    cache_dir=temp_dir,
                    run_correction=False,
                ),
                logger=mock_logger,
            )

            # Set transcription but no lyrics
            controller.results.transcription_results = [sample_transcription_result]
            controller.results.lyrics_results = {}  # Empty - no reference lyrics

            # Call the method under test
            controller._create_uncorrected_result()

            # Verify result is created with empty anchors
            result = controller.results.transcription_corrected
            assert result is not None
            assert result.anchor_sequences == [], "Expected empty anchors when no reference lyrics"
            assert result.gap_sequences == [], "Expected empty gaps when no reference lyrics"

            # Verify metadata reflects zero counts
            assert result.metadata["anchor_sequences_count"] == 0
            assert result.metadata["gap_sequences_count"] == 0

            # Verify correction fields
            assert result.corrections == []
            assert result.metadata["correction_type"] == "none"
            assert result.metadata["reason"] == "correction_disabled"

    def test_returns_early_when_no_transcription(self, mock_logger, temp_dir):
        """Verify method returns early without error when no transcription."""
        with patch.object(LyricsTranscriber, '_initialize_transcribers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_lyrics_providers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_output_generator', return_value=MagicMock()):

            controller = LyricsTranscriber(
                audio_filepath=os.path.join(temp_dir, "test.mp3"),
                artist="Test Artist",
                title="Test Song",
                output_config=OutputConfig(
                    output_styles_json="",
                    output_dir=temp_dir,
                    cache_dir=temp_dir,
                    run_correction=False,
                ),
                logger=mock_logger,
            )

            # No transcription results
            controller.results.transcription_results = []

            # Call the method under test - should not raise
            controller._create_uncorrected_result()

            # Verify no result was created
            assert controller.results.transcription_corrected is None

            # Verify warning was logged
            mock_logger.warning.assert_called()

    def test_preserves_original_segments(
        self, mock_logger, temp_dir, sample_transcription_result, sample_lyrics_results, mock_anchor_finder
    ):
        """Verify original segments are preserved (not modified) in result."""
        with patch.object(LyricsTranscriber, '_initialize_transcribers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_lyrics_providers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_output_generator', return_value=MagicMock()):

            controller = LyricsTranscriber(
                audio_filepath=os.path.join(temp_dir, "test.mp3"),
                artist="Test Artist",
                title="Test Song",
                output_config=OutputConfig(
                    output_styles_json="",
                    output_dir=temp_dir,
                    cache_dir=temp_dir,
                    run_correction=False,
                ),
                logger=mock_logger,
            )

            # Set _anchor_finder directly (read-only property checks it first)
            controller.results.transcription_results = [sample_transcription_result]
            controller.results.lyrics_results = sample_lyrics_results
            controller.corrector._anchor_finder = mock_anchor_finder

            controller._create_uncorrected_result()

            result = controller.results.transcription_corrected

            # Verify segments are exactly the same (no modifications)
            assert result.original_segments is result.corrected_segments
            assert result.original_segments == sample_transcription_result.result.segments

    def test_includes_reference_lyrics_in_result(
        self, mock_logger, temp_dir, sample_transcription_result, sample_lyrics_results, mock_anchor_finder
    ):
        """Verify reference lyrics are included in result for review UI."""
        with patch.object(LyricsTranscriber, '_initialize_transcribers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_lyrics_providers', return_value=[]), \
             patch.object(LyricsTranscriber, '_initialize_output_generator', return_value=MagicMock()):

            controller = LyricsTranscriber(
                audio_filepath=os.path.join(temp_dir, "test.mp3"),
                artist="Test Artist",
                title="Test Song",
                output_config=OutputConfig(
                    output_styles_json="",
                    output_dir=temp_dir,
                    cache_dir=temp_dir,
                    run_correction=False,
                ),
                logger=mock_logger,
            )

            # Set _anchor_finder directly (read-only property checks it first)
            controller.results.transcription_results = [sample_transcription_result]
            controller.results.lyrics_results = sample_lyrics_results
            controller.corrector._anchor_finder = mock_anchor_finder

            controller._create_uncorrected_result()

            result = controller.results.transcription_corrected

            # Verify reference lyrics are included
            assert result.reference_lyrics is sample_lyrics_results
            assert "genius" in result.reference_lyrics
            assert "spotify" in result.reference_lyrics
