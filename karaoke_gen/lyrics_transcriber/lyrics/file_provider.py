from pathlib import Path
import logging
from typing import Optional, Dict, Any
from .base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsMetadata
from karaoke_lyrics_processor import KaraokeLyricsProcessor


class FileProvider(BaseLyricsProvider):
    """Provider that loads lyrics from a local file."""

    def __init__(self, config: LyricsProviderConfig, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.config = config  # Store the config for use in other methods
        self.logger.debug(f"FileProvider initialized with config: {config}")
        self.title = None  # Initialize title
        self.artist = None  # Initialize artist

    def get_lyrics(self, artist: str, title: str) -> Optional[LyricsData]:
        """Get lyrics for the specified artist and title."""
        self.title = title  # Store title for use in other methods
        self.artist = artist  # Store artist for use in other methods
        return super().fetch_lyrics(artist, title)

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Load lyrics from the specified file."""
        self.logger.info(f"Attempting to fetch lyrics from file for {artist} - {title}")

        if not self.config.lyrics_file:
            self.logger.warning("No lyrics file specified in config")
            return None

        lyrics_file = Path(self.config.lyrics_file)
        self.logger.debug(f"Looking for lyrics file at: {lyrics_file} (absolute: {lyrics_file.absolute()})")

        if not lyrics_file.exists():
            self.logger.error(f"Lyrics file not found: {lyrics_file}")
            return None

        self.logger.info(f"Found lyrics file: {lyrics_file}")
        self.logger.debug(f"File size: {lyrics_file.stat().st_size} bytes")

        try:
            # Get formatter safely
            formatter = None
            if self.logger.handlers and len(self.logger.handlers) > 0 and hasattr(self.logger.handlers[0], 'formatter'):
                formatter = self.logger.handlers[0].formatter
            
            processor = KaraokeLyricsProcessor(
                log_level=self.logger.getEffectiveLevel(),
                log_formatter=formatter,
                input_filename=str(lyrics_file),
                max_line_length=self.max_line_length,
            )

            self.logger.debug("Created KaraokeLyricsProcessor instance")
            processed_text = processor.process()
            self.logger.debug(f"Processed text length: {len(processed_text)} characters")
            self.logger.debug(f"First 100 characters of processed text: {processed_text[:100]}...")

            result = {"text": processed_text, "source": "file", "filepath": str(lyrics_file)}
            self.logger.info("Successfully processed lyrics file")
            self.logger.debug(f"Returning result dictionary: {result}")
            return result

        except Exception as e:
            self.logger.error(f"Error processing lyrics file: {str(e)}", exc_info=True)
            return None

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert the raw file data to LyricsData format."""
        self.logger.debug(f"Converting raw data to LyricsData format: {raw_data}")

        try:
            # Create metadata object
            metadata = LyricsMetadata(
                source="file",
                track_name=self.title,
                artist_names=self.artist,
                lyrics_provider="file",
                lyrics_provider_id=raw_data["filepath"],
                is_synced=False,
                provider_metadata={"filepath": raw_data["filepath"]},
            )

            # Create segments with words from the processed text
            segments = self._create_segments_with_words(raw_data["text"], is_synced=False)

            lyrics_data = LyricsData(source="file", segments=segments, metadata=metadata)
            self.logger.debug(f"Created LyricsData object with {len(segments)} segments")
            return lyrics_data

        except Exception as e:
            self.logger.error(f"Error converting result format: {str(e)}", exc_info=True)
            raise
