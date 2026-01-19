import logging
import os
from typing import List, Optional

from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsSegment
from karaoke_gen.lyrics_transcriber.correction.corrector import CorrectionResult


class PlainTextGenerator:
    """Handles generation of plain text output files for lyrics and transcriptions."""

    def __init__(self, output_dir: str, logger: Optional[logging.Logger] = None):
        """Initialize PlainTextGenerator.

        Args:
            output_dir: Directory where output files will be written
            logger: Optional logger instance
        """
        self.output_dir = output_dir
        self.logger = logger or logging.getLogger(__name__)

    def _get_output_path(self, output_prefix: str, extension: str) -> str:
        """Generate full output path for a file."""
        return os.path.join(self.output_dir, f"{output_prefix}.{extension}")

    def write_lyrics(self, lyrics_data: LyricsData, output_prefix: str) -> str:
        """Write plain text lyrics file from provider data.

        Args:
            lyrics_data: LyricsData from a lyrics provider
            output_prefix: Prefix for output filename

        Returns:
            Path to generated file
        """
        self.logger.info("Writing plain lyrics file")
        provider_name = lyrics_data.metadata.source.title()
        output_path = self._get_output_path(f"{output_prefix} (Lyrics {provider_name})", "txt")

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                # Join segment texts with newlines
                lyrics_text = "\n".join(segment.text for segment in lyrics_data.segments)
                f.write(lyrics_text)
            self.logger.info(f"Plain lyrics file generated: {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Failed to write plain lyrics file: {str(e)}")
            raise

    def write_corrected_lyrics(self, segments: List[LyricsSegment], output_prefix: str) -> str:
        """Write corrected lyrics as plain text file.

        Args:
            segments: List of corrected LyricsSegment objects
            output_prefix: Prefix for output filename

        Returns:
            Path to generated file
        """
        self.logger.info("Writing corrected lyrics file")
        output_path = self._get_output_path(f"{output_prefix} (Lyrics Corrected)", "txt")

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                for segment in segments:
                    f.write(f"{segment.text}\n")
            self.logger.info(f"Corrected lyrics file generated: {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Failed to write corrected lyrics file: {str(e)}")
            raise

    def write_original_transcription(self, correction_result: CorrectionResult, output_prefix: str) -> str:
        """Write original (uncorrected) transcription as plain text.

        Args:
            correction_result: CorrectionResult containing original transcription
            output_prefix: Prefix for output filename

        Returns:
            Path to generated file
        """
        self.logger.info("Writing original transcription file")
        output_path = self._get_output_path(f"{output_prefix} (Lyrics Uncorrected)", "txt")

        transcribed_text = " ".join(" ".join(w.text for w in segment.words) for segment in correction_result.original_segments)

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(transcribed_text)
            self.logger.info(f"Original transcription file generated: {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Failed to write original transcription file: {str(e)}")
            raise
