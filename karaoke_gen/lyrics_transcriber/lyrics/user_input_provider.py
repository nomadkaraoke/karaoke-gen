from typing import Optional, Dict, Any
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsMetadata


class UserInputProvider(BaseLyricsProvider):
    """Provider for manually input lyrics text."""

    def __init__(self, lyrics_text: str, source_name: str, metadata: Dict[str, Any], *args, **kwargs):
        """Initialize with the user's input text."""
        super().__init__(LyricsProviderConfig(), *args, **kwargs)
        self.lyrics_text = lyrics_text
        self.source_name = source_name
        self.input_metadata = metadata

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Return the user's input text as raw data."""
        return {"text": self.lyrics_text, "metadata": self.input_metadata}

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert the raw text into LyricsData format."""
        # Create segments with words from the text
        segments = self._create_segments_with_words(raw_data["text"])

        # Create metadata
        metadata = LyricsMetadata(
            source=self.source_name,
            track_name=raw_data["metadata"].get("title", ""),
            artist_names=raw_data["metadata"].get("artist", ""),
            is_synced=False,
            lyrics_provider="manual",
            lyrics_provider_id="",
            album_name=None,
            duration_ms=None,
            explicit=None,
            language=None,
            provider_metadata={},
        )

        return LyricsData(segments=segments, metadata=metadata, source=self.source_name)

    def get_name(self) -> str:
        """Return the provider name."""
        return "UserInput"
