"""
Audio editor for creating custom instrumentals with muted regions.

This module provides the AudioEditor class which creates custom instrumental
tracks by muting specified regions of backing vocals audio and combining
it with the clean instrumental.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

from pydub import AudioSegment

from karaoke_gen.mix_levels import BACKING_VOCALS_MIX_GAIN_DB

from .models import CustomInstrumentalResult, MuteRegion


logger = logging.getLogger(__name__)


class AudioEditor:
    """
    Creates custom instrumentals by muting regions of backing vocals.
    
    This class is pure Python with no cloud dependencies. It works with
    local file paths and uses pydub for audio manipulation.
    
    The editor takes a clean instrumental and backing vocals track,
    applies silence to specified regions of the backing vocals, and
    combines them to create a custom instrumental track.
    
    Example:
        >>> editor = AudioEditor()
        >>> mute_regions = [
        ...     MuteRegion(start_seconds=45.0, end_seconds=48.5),
        ...     MuteRegion(start_seconds=120.0, end_seconds=125.0),
        ... ]
        >>> result = editor.create_custom_instrumental(
        ...     clean_instrumental_path="/path/to/clean.flac",
        ...     backing_vocals_path="/path/to/backing.flac",
        ...     mute_regions=mute_regions,
        ...     output_path="/path/to/custom.flac"
        ... )
        >>> print(f"Created: {result.output_path}")
    """
    
    def __init__(self, output_format: str = "flac"):
        """
        Initialize the audio editor.
        
        Args:
            output_format: Output audio format. Default is "flac".
                Supported formats depend on ffmpeg installation.
        """
        self.output_format = output_format
    
    def create_custom_instrumental(
        self,
        clean_instrumental_path: str,
        backing_vocals_path: str,
        mute_regions: List[MuteRegion],
        output_path: str,
    ) -> CustomInstrumentalResult:
        """
        Create a custom instrumental by muting regions of backing vocals.
        
        This method:
        1. Loads the clean instrumental and backing vocals tracks
        2. Applies silence to the specified regions of the backing vocals
        3. Combines the clean instrumental with the edited backing vocals
        4. Exports the result to the specified output path
        
        Args:
            clean_instrumental_path: Path to the clean instrumental audio file
            backing_vocals_path: Path to the backing vocals audio file
            mute_regions: List of regions to mute in the backing vocals
            output_path: Path where the output file should be saved
        
        Returns:
            CustomInstrumentalResult containing the output path and statistics
        
        Raises:
            FileNotFoundError: If input files don't exist
            ValueError: If mute regions are invalid
        """
        # Validate inputs
        if not Path(clean_instrumental_path).exists():
            raise FileNotFoundError(
                f"Clean instrumental not found: {clean_instrumental_path}"
            )
        if not Path(backing_vocals_path).exists():
            raise FileNotFoundError(
                f"Backing vocals not found: {backing_vocals_path}"
            )
        
        # Normalize and validate mute regions
        normalized_regions = self._normalize_mute_regions(mute_regions)
        
        logger.info(
            f"Creating custom instrumental with {len(normalized_regions)} "
            f"mute regions"
        )
        
        # Load audio files
        logger.debug(f"Loading clean instrumental: {clean_instrumental_path}")
        clean_instrumental = AudioSegment.from_file(clean_instrumental_path)
        
        logger.debug(f"Loading backing vocals: {backing_vocals_path}")
        backing_vocals = AudioSegment.from_file(backing_vocals_path)
        
        # Ensure same duration (use shorter one)
        clean_duration_ms = len(clean_instrumental)
        backing_duration_ms = len(backing_vocals)
        
        if abs(clean_duration_ms - backing_duration_ms) > 1000:
            logger.warning(
                f"Duration mismatch: clean={clean_duration_ms}ms, "
                f"backing={backing_duration_ms}ms. Using shorter duration."
            )
        
        target_duration_ms = min(clean_duration_ms, backing_duration_ms)
        clean_instrumental = clean_instrumental[:target_duration_ms]
        backing_vocals = backing_vocals[:target_duration_ms]
        
        # Apply mute regions to backing vocals
        edited_backing = self._apply_mute_regions(
            backing_vocals, normalized_regions
        )
        
        # Combine: clean instrumental + edited backing vocals
        # The backing vocals are mixed on top of the clean instrumental,
        # attenuated so they sit behind the instrumental (same level as the
        # "Instrumental +BV" track built by AudioProcessor).
        combined = clean_instrumental.overlay(edited_backing.apply_gain(BACKING_VOCALS_MIX_GAIN_DB))
        
        # Ensure output directory exists
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Export
        logger.info(f"Exporting custom instrumental to: {output_path}")
        combined.export(output_path, format=self.output_format)
        
        # Calculate statistics with clamping to actual audio duration
        output_duration_seconds = len(combined) / 1000.0
        total_muted_ms = sum(
            (min(r.end_seconds, output_duration_seconds) - max(r.start_seconds, 0)) * 1000
            for r in normalized_regions
            if r.start_seconds < output_duration_seconds  # Skip regions entirely outside audio
        )
        
        return CustomInstrumentalResult(
            output_path=output_path,
            mute_regions_applied=normalized_regions,
            total_muted_duration_seconds=max(0, total_muted_ms / 1000.0),
            output_duration_seconds=output_duration_seconds,
        )
    
    def apply_mute_to_single_track(
        self,
        audio_path: str,
        mute_regions: List[MuteRegion],
        output_path: str,
    ) -> str:
        """
        Apply mute regions to a single audio track.
        
        This is useful for muting sections of backing vocals without
        combining with the clean instrumental.
        
        Args:
            audio_path: Path to the input audio file
            mute_regions: List of regions to mute
            output_path: Path where the output file should be saved
        
        Returns:
            Path to the output file
        """
        if not Path(audio_path).exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        normalized_regions = self._normalize_mute_regions(mute_regions)
        
        logger.info(f"Applying {len(normalized_regions)} mute regions to audio")
        
        audio = AudioSegment.from_file(audio_path)
        edited = self._apply_mute_regions(audio, normalized_regions)
        
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        
        edited.export(output_path, format=self.output_format)
        
        return output_path
    
    def _normalize_mute_regions(
        self,
        regions: List[MuteRegion]
    ) -> List[MuteRegion]:
        """
        Normalize mute regions: sort, validate, and merge overlapping ones.
        """
        if not regions:
            return []
        
        # Validate regions
        for region in regions:
            if region.start_seconds < 0:
                raise ValueError(
                    f"Mute region start cannot be negative: {region.start_seconds}"
                )
            if region.end_seconds <= region.start_seconds:
                raise ValueError(
                    f"Mute region end ({region.end_seconds}) must be after "
                    f"start ({region.start_seconds})"
                )
        
        # Sort by start time
        sorted_regions = sorted(regions, key=lambda r: r.start_seconds)
        
        # Merge overlapping regions
        merged = [sorted_regions[0]]
        
        for region in sorted_regions[1:]:
            last = merged[-1]
            
            # Check if overlapping or adjacent
            if region.start_seconds <= last.end_seconds:
                # Merge
                merged[-1] = MuteRegion(
                    start_seconds=last.start_seconds,
                    end_seconds=max(last.end_seconds, region.end_seconds)
                )
            else:
                merged.append(region)
        
        return merged
    
    def _apply_mute_regions(
        self,
        audio: AudioSegment,
        regions: List[MuteRegion]
    ) -> AudioSegment:
        """
        Apply silence to the specified regions of an audio segment.
        """
        if not regions:
            return audio
        
        duration_ms = len(audio)
        result = audio
        
        for region in regions:
            start_ms = int(region.start_seconds * 1000)
            end_ms = int(region.end_seconds * 1000)
            
            # Clamp to audio boundaries
            start_ms = max(0, start_ms)
            end_ms = min(duration_ms, end_ms)
            
            if start_ms >= end_ms:
                continue
            
            # Create silence segment
            silence_duration = end_ms - start_ms
            silence = AudioSegment.silent(
                duration=silence_duration,
                frame_rate=audio.frame_rate
            )
            
            # Replace the region with silence
            before = result[:start_ms]
            after = result[end_ms:]
            result = before + silence + after
            
            logger.debug(
                f"Muted region: {start_ms/1000:.2f}s - {end_ms/1000:.2f}s"
            )
        
        return result
    
    def preview_with_mutes(
        self,
        clean_instrumental_path: str,
        backing_vocals_path: str,
        mute_regions: List[MuteRegion],
        output_path: Optional[str] = None,
    ) -> AudioSegment:
        """
        Create a preview of the custom instrumental (in memory).
        
        This is useful for creating temporary previews without saving
        to disk. If output_path is provided, the preview is also saved.
        
        Args:
            clean_instrumental_path: Path to the clean instrumental
            backing_vocals_path: Path to the backing vocals
            mute_regions: Regions to mute
            output_path: Optional path to save the preview
        
        Returns:
            AudioSegment of the preview
        """
        clean_instrumental = AudioSegment.from_file(clean_instrumental_path)
        backing_vocals = AudioSegment.from_file(backing_vocals_path)
        
        # Match durations
        target_duration = min(len(clean_instrumental), len(backing_vocals))
        clean_instrumental = clean_instrumental[:target_duration]
        backing_vocals = backing_vocals[:target_duration]
        
        # Apply mutes
        normalized = self._normalize_mute_regions(mute_regions)
        edited_backing = self._apply_mute_regions(backing_vocals, normalized)
        
        # Combine
        combined = clean_instrumental.overlay(edited_backing)
        
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            combined.export(output_path, format=self.output_format)
        
        return combined
