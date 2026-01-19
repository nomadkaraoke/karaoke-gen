"""Handles adding countdown intro to songs that start too quickly for karaoke singers."""

import logging
import os
import subprocess
from typing import List, Optional, Tuple
from copy import deepcopy

from karaoke_gen.lyrics_transcriber.types import CorrectionResult, LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


class CountdownProcessor:
    """
    Processes corrected lyrics and audio to add countdown intro for songs that start too quickly.
    
    For songs where vocals start within the first 3 seconds, this processor:
    - Adds 3 seconds of silence to the start of the audio file
    - Shifts all timestamps in corrected lyrics by 3 seconds
    - Adds a countdown segment "3... 2... 1..." spanning 0.1s to 2.9s
    """

    # Configuration constants
    COUNTDOWN_THRESHOLD_SECONDS = 3.0  # Trigger countdown if first word is within this time
    COUNTDOWN_PADDING_SECONDS = 3.0    # Amount of silence to add
    COUNTDOWN_START_TIME = 0.1         # When countdown text starts
    COUNTDOWN_END_TIME = 2.9           # When countdown text ends
    COUNTDOWN_TEXT = "3... 2... 1..."  # The countdown text to display

    def __init__(
        self,
        cache_dir: str,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize CountdownProcessor.

        Args:
            cache_dir: Directory for temporary files (padded audio)
            logger: Optional logger instance
        """
        self.cache_dir = cache_dir
        self.logger = logger or logging.getLogger(__name__)

        # Ensure cache directory exists
        os.makedirs(self.cache_dir, exist_ok=True)

    def process(
        self,
        correction_result: CorrectionResult,
        audio_filepath: str,
    ) -> Tuple[CorrectionResult, str, bool, float]:
        """
        Process correction result and audio file, adding countdown if needed.

        Args:
            correction_result: The CorrectionResult to potentially modify
            audio_filepath: Path to the original audio file

        Returns:
            Tuple of:
            - potentially modified CorrectionResult
            - potentially padded audio filepath
            - whether padding was added (bool)
            - amount of padding in seconds (float)
        """
        # Check if countdown is needed
        if not self._needs_countdown(correction_result):
            self.logger.info(
                f"First word starts after {self.COUNTDOWN_THRESHOLD_SECONDS}s - "
                "no countdown needed"
            )
            return correction_result, audio_filepath, False, 0.0

        self.logger.info(
            f"First word starts within {self.COUNTDOWN_THRESHOLD_SECONDS}s - "
            "adding countdown intro"
        )

        # Create padded audio file
        padded_audio_path = self._create_padded_audio(audio_filepath)

        # Create modified correction result with adjusted timestamps
        modified_result = self._add_countdown_to_result(correction_result)

        self.logger.info(
            f"Countdown intro added successfully. "
            f"Padded audio: {os.path.basename(padded_audio_path)}"
        )

        return modified_result, padded_audio_path, True, self.COUNTDOWN_PADDING_SECONDS

    def _needs_countdown(self, correction_result: CorrectionResult) -> bool:
        """
        Check if the song needs a countdown intro.

        Args:
            correction_result: The correction result to check

        Returns:
            True if first word starts within threshold, False otherwise
        """
        if not correction_result.corrected_segments:
            return False

        # Find the first segment with words
        for segment in correction_result.corrected_segments:
            if segment.words:
                first_word_start = segment.words[0].start_time
                return first_word_start < self.COUNTDOWN_THRESHOLD_SECONDS

        return False

    def _create_padded_audio(self, audio_filepath: str) -> str:
        """
        Create a new audio file with silence prepended.

        Args:
            audio_filepath: Path to original audio file

        Returns:
            Path to padded audio file

        Raises:
            FileNotFoundError: If input audio file doesn't exist
            RuntimeError: If ffmpeg command fails
        """
        if not os.path.isfile(audio_filepath):
            raise FileNotFoundError(f"Audio file not found: {audio_filepath}")

        # Create output path in cache directory
        # Always use .flac extension since we encode with FLAC codec for quality
        basename = os.path.basename(audio_filepath)
        name, _ = os.path.splitext(basename)
        padded_filename = f"{name}_padded.flac"
        padded_filepath = os.path.join(self.cache_dir, padded_filename)

        self.logger.info(f"Creating padded audio file: {padded_filename}")

        # Build ffmpeg command to prepend silence
        # We use the anullsrc filter to generate silence and concat it with the original audio
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output file if it exists
            "-hide_banner",
            "-loglevel", "error",
            "-f", "lavfi",
            "-t", str(self.COUNTDOWN_PADDING_SECONDS),
            "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-i", audio_filepath,
            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
            "-map", "[out]",
            "-c:a", "flac",  # Use FLAC to preserve quality
            padded_filepath,
        ]

        try:
            self.logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")
            output = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            self.logger.debug(f"ffmpeg output: {output}")

            if not os.path.isfile(padded_filepath):
                raise RuntimeError(
                    f"ffmpeg command succeeded but output file not created: {padded_filepath}"
                )

            return padded_filepath

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to create padded audio: {e.output}")
            raise RuntimeError(f"ffmpeg command failed: {e.output}")

    def _add_countdown_to_result(self, correction_result: CorrectionResult) -> CorrectionResult:
        """
        Create a new CorrectionResult with countdown segment and adjusted timestamps.

        Args:
            correction_result: The original correction result

        Returns:
            A new CorrectionResult with countdown and shifted timestamps
        """
        # Deep copy the result to avoid modifying the original
        modified_result = deepcopy(correction_result)

        # Shift all timestamps in corrected_segments
        self._shift_segments_timestamps(
            modified_result.corrected_segments,
            self.COUNTDOWN_PADDING_SECONDS
        )

        # Shift timestamps in resized_segments if they exist
        if modified_result.resized_segments:
            self._shift_segments_timestamps(
                modified_result.resized_segments,
                self.COUNTDOWN_PADDING_SECONDS
            )

        # Create and prepend countdown segment
        countdown_segment = self._create_countdown_segment()
        modified_result.corrected_segments.insert(0, countdown_segment)

        # Also add to resized_segments if present
        if modified_result.resized_segments:
            modified_result.resized_segments.insert(0, countdown_segment)

        self.logger.debug(
            f"Added countdown segment and shifted {len(modified_result.corrected_segments)} segments "
            f"by {self.COUNTDOWN_PADDING_SECONDS}s"
        )

        return modified_result

    def _shift_segments_timestamps(
        self,
        segments: List[LyricsSegment],
        offset_seconds: float
    ) -> None:
        """
        Shift all timestamps in segments by the given offset (in-place).

        Args:
            segments: List of segments to modify
            offset_seconds: Amount to shift timestamps (in seconds)
        """
        for segment in segments:
            # Shift segment timestamps
            segment.start_time += offset_seconds
            segment.end_time += offset_seconds

            # Shift all word timestamps
            for word in segment.words:
                word.start_time += offset_seconds
                word.end_time += offset_seconds

    def _create_countdown_segment(self) -> LyricsSegment:
        """
        Create a countdown segment with the countdown text.

        Returns:
            A LyricsSegment containing the countdown
        """
        # Create a single word for the countdown text
        countdown_word = Word(
            id=WordUtils.generate_id(),
            text=self.COUNTDOWN_TEXT,
            start_time=self.COUNTDOWN_START_TIME,
            end_time=self.COUNTDOWN_END_TIME,
            confidence=1.0,
            created_during_correction=True,
        )

        # Create the segment
        countdown_segment = LyricsSegment(
            id=WordUtils.generate_id(),
            text=self.COUNTDOWN_TEXT,
            words=[countdown_word],
            start_time=self.COUNTDOWN_START_TIME,
            end_time=self.COUNTDOWN_END_TIME,
        )

        return countdown_segment

    def has_countdown(self, correction_result: CorrectionResult) -> bool:
        """
        Check if a CorrectionResult already has a countdown segment.
        
        This is used to detect if countdown padding was applied to corrections
        that were loaded from a saved JSON file (where the padding state is not
        explicitly stored).

        Args:
            correction_result: The correction result to check

        Returns:
            True if the first segment is a countdown, False otherwise
        """
        if not correction_result.corrected_segments:
            return False

        first_segment = correction_result.corrected_segments[0]
        return first_segment.text == self.COUNTDOWN_TEXT

    def create_padded_audio_only(self, audio_filepath: str) -> str:
        """
        Create a padded audio file without modifying the correction result.
        
        This is used when loading existing corrections that already have countdown
        timestamps, but we need to create the padded audio file for video rendering.

        Args:
            audio_filepath: Path to original audio file

        Returns:
            Path to padded audio file

        Raises:
            FileNotFoundError: If input audio file doesn't exist
            RuntimeError: If ffmpeg command fails
        """
        return self._create_padded_audio(audio_filepath)

