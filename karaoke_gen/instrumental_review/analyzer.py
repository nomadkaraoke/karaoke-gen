"""
Audio analyzer for detecting audible content in backing vocals.

This module provides the AudioAnalyzer class which analyzes audio files
to detect segments of audible content above a silence threshold. It's used
to help determine whether backing vocals should be included in the final
karaoke instrumental.
"""

import logging
import math
from pathlib import Path
from typing import List, Optional, Tuple

from pydub import AudioSegment

from .models import AnalysisResult, AudibleSegment, RecommendedSelection


logger = logging.getLogger(__name__)


class AudioAnalyzer:
    """
    Analyzes audio files for backing vocals content.
    
    This class is pure Python with no cloud dependencies. It works with
    local file paths and uses pydub for audio loading and analysis.
    
    The analyzer detects segments of audible content (above a silence threshold)
    and provides recommendations for instrumental selection based on the
    analysis results.
    
    Attributes:
        silence_threshold_db: Amplitude threshold below which audio is
            considered silent (default: -40.0 dB)
        min_segment_duration_ms: Minimum duration for a segment to be
            considered audible (default: 100ms)
        merge_gap_ms: Maximum gap between segments to merge them
            (default: 500ms)
        window_ms: Analysis window size in milliseconds (default: 50ms)
    
    Example:
        >>> analyzer = AudioAnalyzer(silence_threshold_db=-40.0)
        >>> result = analyzer.analyze("/path/to/backing_vocals.flac")
        >>> if result.has_audible_content:
        ...     print(f"Found {result.segment_count} audible segments")
        ...     for seg in result.audible_segments:
        ...         print(f"  {seg.start_seconds:.1f}s - {seg.end_seconds:.1f}s")
    """
    
    def __init__(
        self,
        silence_threshold_db: float = -40.0,
        min_segment_duration_ms: int = 100,
        merge_gap_ms: int = 500,
        window_ms: int = 50,
    ):
        """
        Initialize the audio analyzer.
        
        Args:
            silence_threshold_db: Amplitude threshold below which audio is
                considered silent. Default is -40.0 dB.
            min_segment_duration_ms: Minimum duration for a segment to be
                reported as audible. Segments shorter than this are ignored.
                Default is 100ms.
            merge_gap_ms: If two audible segments are separated by a gap
                shorter than this, they are merged into one segment.
                Default is 500ms.
            window_ms: Size of the analysis window in milliseconds.
                Smaller windows give more precise timing but slower analysis.
                Default is 50ms.
        """
        self.silence_threshold_db = silence_threshold_db
        self.min_segment_duration_ms = min_segment_duration_ms
        self.merge_gap_ms = merge_gap_ms
        self.window_ms = window_ms
    
    def analyze(self, audio_path: str) -> AnalysisResult:
        """
        Analyze an audio file for audible content.
        
        This method loads the audio file, calculates amplitude levels across
        the duration, and identifies segments where the amplitude exceeds
        the silence threshold.
        
        Args:
            audio_path: Path to the audio file to analyze. Supports formats
                that pydub/ffmpeg can read (FLAC, WAV, MP3, etc.)
        
        Returns:
            AnalysisResult containing:
                - has_audible_content: Whether any audible content was found
                - total_duration_seconds: Total duration of the audio
                - audible_segments: List of detected audible segments
                - recommended_selection: Recommendation for which instrumental
                - Various statistics about the audible content
        
        Raises:
            FileNotFoundError: If the audio file doesn't exist
            Exception: If the audio file cannot be loaded
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        logger.info(f"Analyzing audio file: {audio_path}")
        
        # Load audio file
        audio = AudioSegment.from_file(audio_path)
        total_duration_ms = len(audio)
        total_duration_seconds = total_duration_ms / 1000.0
        
        logger.debug(f"Audio duration: {total_duration_seconds:.2f}s, "
                    f"channels: {audio.channels}, "
                    f"sample_rate: {audio.frame_rate}")
        
        # Convert to mono for consistent analysis
        if audio.channels > 1:
            audio = audio.set_channels(1)
        
        # Analyze amplitude in windows
        audible_windows = self._find_audible_windows(audio)
        
        # Merge adjacent windows into segments
        raw_segments = self._windows_to_segments(audible_windows, audio)
        
        # Merge close segments and filter short ones
        segments = self._merge_and_filter_segments(raw_segments)
        
        # Calculate statistics
        total_audible_ms = sum(
            seg.duration_seconds * 1000 for seg in segments
        )
        total_audible_seconds = total_audible_ms / 1000.0
        audible_percentage = (
            (total_audible_seconds / total_duration_seconds * 100)
            if total_duration_seconds > 0 else 0.0
        )
        
        has_audible_content = len(segments) > 0
        
        # Determine recommendation
        recommended_selection = self._get_recommendation(
            has_audible_content,
            segments,
            audible_percentage
        )
        
        logger.info(
            f"Analysis complete: {len(segments)} segments, "
            f"{audible_percentage:.1f}% audible, "
            f"recommendation: {recommended_selection.value}"
        )
        
        return AnalysisResult(
            has_audible_content=has_audible_content,
            total_duration_seconds=total_duration_seconds,
            audible_segments=segments,
            recommended_selection=recommended_selection,
            silence_threshold_db=self.silence_threshold_db,
            total_audible_duration_seconds=total_audible_seconds,
            audible_percentage=audible_percentage,
        )
    
    def get_amplitude_envelope(
        self,
        audio_path: str,
        window_ms: int = 100,
        normalize: bool = True,
    ) -> List[float]:
        """
        Get the amplitude envelope for waveform visualization.
        
        This method returns a list of amplitude values suitable for
        rendering a waveform display. Each value represents the RMS
        amplitude of a window of audio.
        
        Args:
            audio_path: Path to the audio file
            window_ms: Size of each window in milliseconds. Smaller values
                give more detail but larger data. Default is 100ms.
            normalize: If True, normalize amplitudes to 0.0-1.0 range.
                Default is True.
        
        Returns:
            List of amplitude values (floats). If normalize=True, values
            are in the range [0.0, 1.0]. Otherwise, values are in dBFS.
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        audio = AudioSegment.from_file(audio_path)
        
        # Convert to mono
        if audio.channels > 1:
            audio = audio.set_channels(1)
        
        amplitudes = []
        duration_ms = len(audio)
        
        for start_ms in range(0, duration_ms, window_ms):
            end_ms = min(start_ms + window_ms, duration_ms)
            window = audio[start_ms:end_ms]
            
            # Get RMS amplitude in dBFS
            if window.rms > 0:
                db = 20 * math.log10(window.rms / window.max_possible_amplitude)
            else:
                db = -100.0  # Effectively silent
            
            amplitudes.append(db)
        
        if normalize and amplitudes:
            # Normalize to 0.0 - 1.0 range
            # Map from [silence_threshold, 0] to [0, 1]
            min_db = self.silence_threshold_db
            max_db = 0.0
            amplitudes = [
                max(0.0, min(1.0, (db - min_db) / (max_db - min_db)))
                for db in amplitudes
            ]
        
        return amplitudes
    
    def _find_audible_windows(
        self,
        audio: AudioSegment
    ) -> List[Tuple[int, float, float]]:
        """
        Find windows with amplitude above the silence threshold.
        
        Returns a list of tuples: (start_ms, avg_db, peak_db)
        """
        audible_windows = []
        duration_ms = len(audio)
        
        for start_ms in range(0, duration_ms, self.window_ms):
            end_ms = min(start_ms + self.window_ms, duration_ms)
            window = audio[start_ms:end_ms]
            
            # Calculate RMS amplitude in dB
            if window.rms > 0:
                avg_db = 20 * math.log10(window.rms / window.max_possible_amplitude)
                # Peak is approximated as max sample value
                peak_db = window.dBFS if hasattr(window, 'dBFS') else avg_db
            else:
                avg_db = -100.0
                peak_db = -100.0
            
            if avg_db > self.silence_threshold_db:
                audible_windows.append((start_ms, avg_db, peak_db))
        
        return audible_windows
    
    def _windows_to_segments(
        self,
        audible_windows: List[Tuple[int, float, float]],
        audio: AudioSegment
    ) -> List[AudibleSegment]:
        """
        Convert list of audible windows into contiguous segments.
        """
        if not audible_windows:
            return []
        
        segments = []
        segment_start_ms = audible_windows[0][0]
        segment_dbs = [audible_windows[0][1]]
        segment_peaks = [audible_windows[0][2]]
        last_end_ms = audible_windows[0][0] + self.window_ms
        
        for i in range(1, len(audible_windows)):
            start_ms, avg_db, peak_db = audible_windows[i]
            
            # Check if this window is contiguous with the previous
            gap_ms = start_ms - last_end_ms
            
            if gap_ms <= self.window_ms:
                # Extend current segment
                segment_dbs.append(avg_db)
                segment_peaks.append(peak_db)
                last_end_ms = start_ms + self.window_ms
            else:
                # Save current segment and start a new one
                segments.append(self._create_segment(
                    segment_start_ms, last_end_ms, segment_dbs, segment_peaks
                ))
                
                segment_start_ms = start_ms
                segment_dbs = [avg_db]
                segment_peaks = [peak_db]
                last_end_ms = start_ms + self.window_ms
        
        # Don't forget the last segment
        segments.append(self._create_segment(
            segment_start_ms, last_end_ms, segment_dbs, segment_peaks
        ))
        
        return segments
    
    def _create_segment(
        self,
        start_ms: int,
        end_ms: int,
        dbs: List[float],
        peaks: List[float]
    ) -> AudibleSegment:
        """Create an AudibleSegment from window data."""
        return AudibleSegment(
            start_seconds=start_ms / 1000.0,
            end_seconds=end_ms / 1000.0,
            duration_seconds=(end_ms - start_ms) / 1000.0,
            avg_amplitude_db=sum(dbs) / len(dbs) if dbs else -100.0,
            peak_amplitude_db=max(peaks) if peaks else -100.0,
        )
    
    def _merge_and_filter_segments(
        self,
        segments: List[AudibleSegment]
    ) -> List[AudibleSegment]:
        """
        Merge segments that are close together and filter out short ones.
        """
        if not segments:
            return []
        
        # Sort by start time
        segments = sorted(segments, key=lambda s: s.start_seconds)
        
        # Merge segments with small gaps
        merged = []
        current = segments[0]
        
        for next_seg in segments[1:]:
            gap_ms = (next_seg.start_seconds - current.end_seconds) * 1000
            
            if gap_ms <= self.merge_gap_ms:
                # Merge segments
                combined_duration = (
                    next_seg.end_seconds - current.start_seconds
                )
                # Weight average amplitude by duration
                total_duration = (
                    current.duration_seconds + next_seg.duration_seconds
                )
                weighted_avg_db = (
                    (current.avg_amplitude_db * current.duration_seconds +
                     next_seg.avg_amplitude_db * next_seg.duration_seconds)
                    / total_duration
                ) if total_duration > 0 else -100.0
                
                current = AudibleSegment(
                    start_seconds=current.start_seconds,
                    end_seconds=next_seg.end_seconds,
                    duration_seconds=combined_duration,
                    avg_amplitude_db=weighted_avg_db,
                    peak_amplitude_db=max(
                        current.peak_amplitude_db,
                        next_seg.peak_amplitude_db
                    ),
                )
            else:
                merged.append(current)
                current = next_seg
        
        merged.append(current)
        
        # Filter out segments shorter than minimum duration
        min_duration_seconds = self.min_segment_duration_ms / 1000.0
        filtered = [
            seg for seg in merged
            if seg.duration_seconds >= min_duration_seconds
        ]
        
        return filtered
    
    def _get_recommendation(
        self,
        has_audible_content: bool,
        segments: List[AudibleSegment],
        audible_percentage: float
    ) -> RecommendedSelection:
        """
        Determine the recommended instrumental selection.
        
        Logic:
        - If no audible content: recommend clean instrumental
        - If audible content covers > 20% of the audio: likely has
          meaningful backing vocals, recommend with_backing
        - If loud segments detected: recommend with_backing
        - Otherwise: minimal content, recommend clean
        """
        if not has_audible_content:
            return RecommendedSelection.CLEAN

        # If there's significant audible content, recommend with backing vocals
        if audible_percentage > 20.0:
            return RecommendedSelection.WITH_BACKING

        # If there are loud segments, recommend with backing vocals
        loud_segments = [seg for seg in segments if seg.is_loud]
        if loud_segments:
            return RecommendedSelection.WITH_BACKING

        # Minimal content - recommend clean
        return RecommendedSelection.CLEAN
