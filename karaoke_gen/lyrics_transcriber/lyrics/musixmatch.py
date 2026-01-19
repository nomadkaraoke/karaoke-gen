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

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch raw song data from Musixmatch via RapidAPI."""
        if not self.rapidapi_key:
            self.logger.warning("No RapidAPI key provided for Musixmatch")
            return None

        self.logger.info(f"Fetching lyrics from Musixmatch for {artist} - {title}")
        
        try:
            # Construct the API URL with artist and title
            url = f"https://musixmatch-song-lyrics-api.p.rapidapi.com/lyrics/{artist}/{title}/"
            
            headers = {
                "x-rapidapi-key": self.rapidapi_key,
                "x-rapidapi-host": "musixmatch-song-lyrics-api.p.rapidapi.com"
            }
            
            self.logger.debug(f"Making Musixmatch API request to: {url}")
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Check if we got a valid response
            if not data.get("message", {}).get("body", {}).get("macro_calls"):
                self.logger.warning("Invalid response structure from Musixmatch API")
                return None
                
            # Check if lyrics are available
            lyrics_data = data.get("message", {}).get("body", {}).get("macro_calls", {}).get("track.lyrics.get", {})
            if not lyrics_data.get("message", {}).get("body", {}).get("lyrics"):
                self.logger.warning("No lyrics found in Musixmatch response")
                return None
                
            self.logger.info("Successfully fetched lyrics from Musixmatch")
            return data
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Musixmatch API request failed: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching from Musixmatch: {str(e)}")
            return None

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert Musixmatch's raw API response to standardized format."""
        try:
            # Extract macro calls from the nested response
            macro_calls = raw_data.get("message", {}).get("body", {}).get("macro_calls", {})
            
            # Extract track information
            track_data = macro_calls.get("matcher.track.get", {}).get("message", {}).get("body", {}).get("track", {})
            
            # Extract lyrics information
            lyrics_data = macro_calls.get("track.lyrics.get", {}).get("message", {}).get("body", {}).get("lyrics", {})
            
            # Get the actual lyrics text
            lyrics_text = lyrics_data.get("lyrics_body", "")
            
            # Clean the lyrics
            lyrics_text = self._clean_lyrics(lyrics_text)
            
            # Create metadata object
            metadata = LyricsMetadata(
                source="musixmatch",
                track_name=track_data.get("track_name", ""),
                artist_names=track_data.get("artist_name", ""),
                album_name=track_data.get("album_name", ""),
                duration_ms=track_data.get("track_length", 0) * 1000 if track_data.get("track_length") else None,
                explicit=bool(track_data.get("explicit", 0)),
                language=lyrics_data.get("lyrics_language", ""),
                is_synced=False,  # Musixmatch API doesn't provide sync data in this format
                lyrics_provider="musixmatch",
                lyrics_provider_id=str(lyrics_data.get("lyrics_id", "")),
                provider_metadata={
                    "musixmatch_track_id": track_data.get("track_id"),
                    "musixmatch_lyrics_id": lyrics_data.get("lyrics_id"),
                    "album_id": track_data.get("album_id"),
                    "artist_id": track_data.get("artist_id"),
                    "track_share_url": track_data.get("track_share_url"),
                    "track_edit_url": track_data.get("track_edit_url"),
                    "lyrics_language": lyrics_data.get("lyrics_language"),
                    "lyrics_language_description": lyrics_data.get("lyrics_language_description"),
                    "lyrics_copyright": lyrics_data.get("lyrics_copyright"),
                    "track_rating": track_data.get("track_rating"),
                    "num_favourite": track_data.get("num_favourite"),
                    "first_release_date": track_data.get("first_release_date"),
                    "spotify_id": track_data.get("track_spotify_id"),
                    "isrc": track_data.get("track_isrc"),
                    "api_source": "rapidapi_musixmatch",
                },
            )

            # Create segments with words from lyrics
            segments = self._create_segments_with_words(lyrics_text, is_synced=False)

            # Create result object with segments
            return LyricsData(source="musixmatch", segments=segments, metadata=metadata)
            
        except Exception as e:
            self.logger.error(f"Error converting Musixmatch response format: {str(e)}")
            # Return empty lyrics data if conversion fails
            return LyricsData(
                source="musixmatch",
                segments=[],
                metadata=LyricsMetadata(
                    source="musixmatch",
                    track_name="",
                    artist_names="",
                    lyrics_provider="musixmatch",
                    is_synced=False,
                    provider_metadata={"api_source": "rapidapi_musixmatch", "conversion_error": str(e)},
                )
            )

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