import logging
import os
from typing import List, Optional

from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word


class LyricsFileGenerator:
    """Handles generation of lyrics files in various formats (LRC, etc)."""

    def __init__(self, output_dir: str, logger: Optional[logging.Logger] = None):
        """Initialize LyricsFileGenerator.
        
        Args:
            output_dir: Directory where output files will be written
            logger: Optional logger instance
        """
        self.output_dir = output_dir
        self.logger = logger or logging.getLogger(__name__)

    def _get_output_path(self, output_prefix: str, extension: str) -> str:
        """Generate full output path for a file."""
        return os.path.join(self.output_dir, f"{output_prefix}.{extension}")

    def generate_lrc(self, segments: List[LyricsSegment], output_prefix: str) -> str:
        """Generate LRC format lyrics file.
        
        Args:
            segments: List of LyricsSegment objects containing word timing data
            output_prefix: Prefix for output filename
            
        Returns:
            Path to generated LRC file
        """
        self.logger.info("Generating LRC format lyrics")
        output_path = self._get_output_path(f"{output_prefix} (Karaoke)", "lrc")

        try:
            self._write_lrc_file(output_path, segments)
            self.logger.info(f"LRC file generated: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Failed to generate LRC file: {str(e)}")
            raise

    def _write_lrc_file(self, output_path: str, segments: List[LyricsSegment]) -> None:
        """Write LRC file content with MidiCo-compatible word-level timestamps.

        Args:
            output_path: Path to write the LRC file
            segments: List of LyricsSegment objects containing word timing data
        """
        with open(output_path, "w", encoding="utf-8") as f:
            # Write MidiCo header
            f.write("[re:MidiCo]\n")

            for segment in segments:
                for i, word in enumerate(segment.words):
                    start_time = self._format_lrc_timestamp(word.start_time)

                    # Add space after all words except last in segment
                    text = word.text
                    if i != len(segment.words) - 1:
                        text += " "

                    # Add "/" prefix for first word in segment
                    prefix = "/" if i == 0 else ""

                    # Write MidiCo formatted line
                    f.write(f"[{start_time}]1:{prefix}{text}\n")

    def _format_lrc_timestamp(self, seconds: float) -> str:
        """Format timestamp for MidiCo LRC format (MM:SS.mmm).
        
        Args:
            seconds: Time in seconds
            
        Returns:
            Formatted timestamp string in MM:SS.mmm format
        """
        minutes = int(seconds // 60)
        remaining_seconds = seconds % 60
        
        # Convert to milliseconds and round to nearest integer
        total_milliseconds = round(remaining_seconds * 1000)
        
        # Extract seconds and milliseconds
        seconds_part = total_milliseconds // 1000
        milliseconds = total_milliseconds % 1000
        
        # Handle rollover
        if seconds_part == 60:
            seconds_part = 0
            minutes += 1
            
        return f"{minutes:02d}:{seconds_part:02d}.{milliseconds:03d}"

    # Future methods for other lyrics file formats can be added here
    # def generate_txt(self, segments: List[LyricsSegment], output_prefix: str) -> str:
    #     """Generate Power Karaoke TXT format lyrics file."""
    #     pass 