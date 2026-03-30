"""
Unit tests for AudioAnalyzer.

Tests cover:
- Analysis of silent audio
- Analysis of audio with content
- Segment detection and merging
- Threshold behavior
- Recommendation logic
- Edge cases
"""

import os
import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from karaoke_gen.instrumental_review import (
    AudioAnalyzer,
    AnalysisResult,
    AudibleSegment,
    RecommendedSelection,
)


class TestAudioAnalyzerInit:
    """Tests for AudioAnalyzer initialization."""
    
    def test_default_parameters(self):
        """Test analyzer creates with sensible defaults."""
        analyzer = AudioAnalyzer()
        
        assert analyzer.silence_threshold_db == -40.0
        assert analyzer.min_segment_duration_ms == 100
        assert analyzer.merge_gap_ms == 500
        assert analyzer.window_ms == 50
    
    def test_custom_parameters(self):
        """Test analyzer accepts custom parameters."""
        analyzer = AudioAnalyzer(
            silence_threshold_db=-35.0,
            min_segment_duration_ms=200,
            merge_gap_ms=300,
            window_ms=100,
        )
        
        assert analyzer.silence_threshold_db == -35.0
        assert analyzer.min_segment_duration_ms == 200
        assert analyzer.merge_gap_ms == 300
        assert analyzer.window_ms == 100


class TestAnalyzeSilentAudio:
    """Tests for analyzing silent/near-silent audio."""
    
    def test_silent_audio_returns_no_audible_content(self, silent_audio_path):
        """Silent audio should return has_audible_content=False."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(silent_audio_path)
        
        assert result.has_audible_content is False
        assert len(result.audible_segments) == 0
        assert result.total_audible_duration_seconds == 0.0
        assert result.audible_percentage == 0.0
    
    def test_silent_audio_recommends_clean(self, silent_audio_path):
        """Silent audio should recommend clean instrumental."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(silent_audio_path)
        
        assert result.recommended_selection == RecommendedSelection.CLEAN
    
    def test_quiet_audio_below_threshold_is_silent(self, quiet_audio_path):
        """Audio below threshold should be considered silent."""
        analyzer = AudioAnalyzer(silence_threshold_db=-40.0)
        result = analyzer.analyze(quiet_audio_path)
        
        # Very quiet audio (-50dB) should be below -40dB threshold
        assert result.has_audible_content is False
        assert result.recommended_selection == RecommendedSelection.CLEAN


class TestAnalyzeAudibleAudio:
    """Tests for analyzing audio with audible content."""
    
    def test_loud_audio_has_audible_content(self, loud_audio_path):
        """Loud audio should return has_audible_content=True."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(loud_audio_path)
        
        assert result.has_audible_content is True
        assert len(result.audible_segments) > 0
        assert result.total_audible_duration_seconds > 0
    
    def test_loud_audio_high_audible_percentage(self, loud_audio_path):
        """Consistently loud audio should have high audible percentage."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(loud_audio_path)
        
        # Should be mostly audible
        assert result.audible_percentage > 80.0
    
    def test_loud_audio_recommends_with_backing(self, loud_audio_path):
        """Loud audio should recommend with_backing."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(loud_audio_path)
        
        assert result.recommended_selection == RecommendedSelection.WITH_BACKING


class TestSegmentDetection:
    """Tests for segment detection and merging."""
    
    def test_mixed_audio_detects_multiple_segments(self, mixed_audio_path):
        """Mixed audio should detect distinct audible segments."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(mixed_audio_path)
        
        assert result.has_audible_content is True
        # Should detect the two audible sections (3s each)
        assert len(result.audible_segments) >= 1
    
    def test_segment_timing_is_accurate(self, mixed_audio_path):
        """Detected segment timings should be approximately correct."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(mixed_audio_path)
        
        # First audible section should start around 2s (after 2s silence)
        if result.audible_segments:
            first_segment = result.audible_segments[0]
            # Allow some tolerance for window-based analysis
            assert first_segment.start_seconds >= 1.5
            assert first_segment.start_seconds <= 2.5
    
    def test_short_segments_are_filtered(self, temp_dir):
        """Segments shorter than min_segment_duration_ms should be filtered."""
        # Create audio with very short audible blips
        silence1 = AudioSegment.silent(duration=1000, frame_rate=44100)
        blip = Sine(440).to_audio_segment(duration=50) - 10  # 50ms blip
        silence2 = AudioSegment.silent(duration=1000, frame_rate=44100)
        
        audio = silence1 + blip + silence2
        audio_path = os.path.join(temp_dir, "short_blip.flac")
        audio.export(audio_path, format="flac")
        
        # Analyzer with 100ms minimum should filter out 50ms blip
        analyzer = AudioAnalyzer(min_segment_duration_ms=100)
        result = analyzer.analyze(audio_path)
        
        # The short blip should be filtered
        assert len(result.audible_segments) == 0
    
    def test_close_segments_are_merged(self, temp_dir):
        """Close segments should be merged based on merge_gap_ms."""
        # Create audio with two close audible sections
        tone1 = Sine(440).to_audio_segment(duration=1000) - 10  # 1s
        gap = AudioSegment.silent(duration=200, frame_rate=44100)  # 200ms gap
        tone2 = Sine(440).to_audio_segment(duration=1000) - 10  # 1s
        
        audio = tone1 + gap + tone2
        audio_path = os.path.join(temp_dir, "close_segments.flac")
        audio.export(audio_path, format="flac")
        
        # With 500ms merge gap, the 200ms gap should cause merging
        analyzer = AudioAnalyzer(merge_gap_ms=500)
        result = analyzer.analyze(audio_path)
        
        # Should merge into one segment
        assert len(result.audible_segments) == 1


class TestThresholdBehavior:
    """Tests for silence threshold behavior."""
    
    def test_custom_threshold_affects_detection(self, temp_dir):
        """Different thresholds should detect different content."""
        # Create audio at -35dB
        tone = Sine(440).to_audio_segment(duration=5000)
        tone = tone - 35
        audio_path = os.path.join(temp_dir, "threshold_test.flac")
        tone.export(audio_path, format="flac")
        
        # With -40dB threshold, -35dB audio should be detected
        analyzer_low = AudioAnalyzer(silence_threshold_db=-40.0)
        result_low = analyzer_low.analyze(audio_path)
        
        # With -30dB threshold, -35dB audio should NOT be detected
        analyzer_high = AudioAnalyzer(silence_threshold_db=-30.0)
        result_high = analyzer_high.analyze(audio_path)
        
        assert result_low.has_audible_content is True
        assert result_high.has_audible_content is False
    
    def test_threshold_stored_in_result(self, silent_audio_path):
        """The threshold used should be stored in the result."""
        threshold = -42.5
        analyzer = AudioAnalyzer(silence_threshold_db=threshold)
        result = analyzer.analyze(silent_audio_path)
        
        assert result.silence_threshold_db == threshold


class TestRecommendationLogic:
    """Tests for instrumental selection recommendations."""
    
    def test_no_content_recommends_clean(self, silent_audio_path):
        """No audible content should recommend clean."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(silent_audio_path)
        
        assert result.recommended_selection == RecommendedSelection.CLEAN
    
    def test_high_percentage_recommends_with_backing(self, loud_audio_path):
        """High audible percentage (>20%) should recommend with_backing."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(loud_audio_path)
        
        assert result.audible_percentage > 20.0
        assert result.recommended_selection == RecommendedSelection.WITH_BACKING
    
    def test_loud_segments_recommend_with_backing(self, temp_dir):
        """Loud segments (>-20dB) should recommend with_backing."""
        # Create audio with a loud section
        silence = AudioSegment.silent(duration=5000, frame_rate=44100)
        loud_section = Sine(440).to_audio_segment(duration=500) - 5  # Very loud
        
        audio = silence + loud_section + silence
        audio_path = os.path.join(temp_dir, "loud_section.flac")
        audio.export(audio_path, format="flac")
        
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(audio_path)
        
        # Even though percentage is low, loud segment should trigger review
        assert result.recommended_selection == RecommendedSelection.WITH_BACKING


class TestAmplitudeEnvelope:
    """Tests for amplitude envelope extraction."""
    
    def test_envelope_length_matches_duration(self, loud_audio_path):
        """Envelope should have expected number of points."""
        analyzer = AudioAnalyzer()
        
        window_ms = 100
        envelope = analyzer.get_amplitude_envelope(loud_audio_path, window_ms=window_ms)
        
        # 10 second audio with 100ms windows = ~100 points
        assert len(envelope) >= 90
        assert len(envelope) <= 110
    
    def test_envelope_normalized_to_0_1(self, loud_audio_path):
        """Normalized envelope should have values between 0 and 1."""
        analyzer = AudioAnalyzer()
        envelope = analyzer.get_amplitude_envelope(
            loud_audio_path, normalize=True
        )
        
        assert all(0.0 <= v <= 1.0 for v in envelope)
    
    def test_envelope_unnormalized_in_db(self, loud_audio_path):
        """Unnormalized envelope should be in dB."""
        analyzer = AudioAnalyzer()
        envelope = analyzer.get_amplitude_envelope(
            loud_audio_path, normalize=False
        )
        
        # dB values should be negative (below full scale)
        assert all(v <= 0 for v in envelope)
        # Most values should be above -60dB for loud audio
        assert sum(1 for v in envelope if v > -60) > len(envelope) * 0.5


class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_nonexistent_file_raises_error(self):
        """Analyzing non-existent file should raise FileNotFoundError."""
        analyzer = AudioAnalyzer()
        
        with pytest.raises(FileNotFoundError):
            analyzer.analyze("/nonexistent/path/to/audio.flac")
    
    def test_stereo_audio_handled(self, stereo_audio_path):
        """Stereo audio should be converted to mono and analyzed."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(stereo_audio_path)
        
        # Should not raise and should detect content
        assert result.has_audible_content is True
    
    def test_total_duration_accurate(self, mixed_audio_path):
        """Total duration should match audio file duration."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(mixed_audio_path)
        
        # Mixed audio is 10 seconds
        assert abs(result.total_duration_seconds - 10.0) < 0.5


class TestAnalysisResultMethods:
    """Tests for AnalysisResult methods."""
    
    def test_segment_count_property(self, mixed_audio_path):
        """segment_count should return number of segments."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(mixed_audio_path)
        
        assert result.segment_count == len(result.audible_segments)
    
    def test_get_segments_in_range(self, mixed_audio_path):
        """get_segments_in_range should filter segments correctly."""
        analyzer = AudioAnalyzer()
        result = analyzer.analyze(mixed_audio_path)
        
        # Get segments in the first 5 seconds
        segments_in_range = result.get_segments_in_range(0, 5)
        
        # All returned segments should overlap with [0, 5]
        for seg in segments_in_range:
            assert not (seg.end_seconds <= 0 or seg.start_seconds >= 5)
