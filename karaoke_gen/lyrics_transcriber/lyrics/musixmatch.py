import logging
from typing import Optional, Dict, Any
import requests
from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsMetadata
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig


class MusixmatchProvider(BaseLyricsProvider):
    """Handles fetching lyrics from Musixmatch via RapidAPI."""

    def __init__(self, config: LyricsProviderConfig, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.rapidapi_key = config.rapidapi_key

    API_HOST = "musixmatch-song-lyrics-api.p.rapidapi.com"

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch raw song data from Musixmatch via RapidAPI.

        The API moved to ``GET /lyrics?artist=&title=`` (the old
        ``/lyrics/{artist}/{title}/`` path 404s for every song since ~2026-09).
        It reports a miss as HTTP 200 with ``data.error`` set, not as a 404.
        """
        if not self.rapidapi_key:
            self.logger.warning("No RapidAPI key provided for Musixmatch")
            return None

        self.logger.info(f"Fetching lyrics from Musixmatch for {artist} - {title}")

        try:
            response = requests.get(
                f"https://{self.API_HOST}/lyrics",
                params={"artist": artist, "title": title},
                headers={"x-rapidapi-key": self.rapidapi_key, "x-rapidapi-host": self.API_HOST},
                timeout=10,
            )
            response.raise_for_status()
            data = (response.json() or {}).get("data") or {}

            if data.get("error") or not str(data.get("lyrics") or "").strip():
                self.logger.info(f"No lyrics found on Musixmatch for {artist} - {title}")
                return None

            self.logger.info("Successfully fetched lyrics from Musixmatch")
            return data

        except requests.exceptions.RequestException as e:
            self._log_request_exception(e, "Musixmatch API")
            return None
        except ValueError as e:  # non-JSON body
            self.logger.warning(f"Unreadable Musixmatch response: {e}")
            return None

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert Musixmatch's ``data`` object (trackName, artistName, trackId, lyrics) to LyricsData."""
        if "message" in raw_data:  # raw response cached from the old macro_calls API
            calls = raw_data["message"].get("body", {}).get("macro_calls", {})
            track = calls.get("matcher.track.get", {}).get("message", {}).get("body", {}).get("track", {})
            lyrics = calls.get("track.lyrics.get", {}).get("message", {}).get("body", {}).get("lyrics", {})
            raw_data = {"trackName": track.get("track_name", ""), "artistName": track.get("artist_name", ""),
                        "trackId": track.get("track_id"), "lyrics": lyrics.get("lyrics_body", "")}
        lyrics_text = self._clean_lyrics(raw_data.get("lyrics", ""))
        metadata = LyricsMetadata(
            source="musixmatch",
            track_name=raw_data.get("trackName", ""),
            artist_names=raw_data.get("artistName", ""),
            lyrics_provider="musixmatch",
            lyrics_provider_id=str(raw_data.get("trackId", "")),
            is_synced=False,
            provider_metadata={
                "musixmatch_track_id": raw_data.get("trackId"),
                "artwork_url": raw_data.get("artworkUrl"),
                "api_source": "rapidapi_musixmatch",
            },
        )
        segments = self._create_segments_with_words(lyrics_text, is_synced=False)
        return LyricsData(source="musixmatch", segments=segments, metadata=metadata)

    def _clean_lyrics(self, lyrics: str) -> str:
        """Clean and process lyrics from Musixmatch to remove unwanted content."""
        if not isinstance(lyrics, str):
            self.logger.warning(f"Expected string for lyrics, got {type(lyrics)}: {repr(lyrics)}")
            if lyrics is None:
                return ""
            try:
                lyrics = str(lyrics)
            except Exception as e:
                self.logger.error(f"Failed to convert lyrics to string: {e}")
                return ""
        
        # Replace escaped newlines with actual newlines, handling whitespace
        import re
        lyrics = re.sub(r'\s*\\n\s*', '\n', lyrics)
        
        # Remove any HTML tags that might be present
        lyrics = re.sub(r'<[^>]+>', '', lyrics)
        
        # Clean up multiple consecutive newlines
        lyrics = re.sub(r'\n\s*\n\s*\n+', '\n\n', lyrics)
        
        # Clean up leading/trailing whitespace
        lyrics = lyrics.strip()
        
        self.logger.debug("Completed Musixmatch lyrics cleaning process")
        return lyrics 