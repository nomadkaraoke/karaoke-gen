import logging
from typing import Optional, Dict, Any
import syrics.api
import time
import requests

from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsMetadata, LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


class SpotifyProvider(BaseLyricsProvider):
    """Handles fetching lyrics from Spotify."""

    def __init__(self, config: LyricsProviderConfig, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.cookie = config.spotify_cookie
        self.rapidapi_key = config.rapidapi_key
        self.client = None

        # Only initialize syrics client if rapidapi_key is not set
        if self.cookie and not self.rapidapi_key:
            max_retries = 5
            retry_delay = 5  # seconds

            for attempt in range(max_retries):
                try:
                    self.client = syrics.api.Spotify(self.cookie)
                    break  # Successfully initialized
                except Exception as e:
                    if attempt == max_retries - 1:  # Last attempt
                        self.logger.error(f"Failed to initialize Spotify client after {max_retries} attempts: {str(e)}")
                        break
                    self.logger.warning(f"Attempt {attempt + 1}/{max_retries} failed, retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch raw data from Spotify APIs using RapidAPI or syrics library."""
        # Try RapidAPI first if available
        if self.rapidapi_key:
            self.logger.info(f"Trying RapidAPI for {artist} - {title}")
            result = self._fetch_from_rapidapi(artist, title)
            if result:
                return result
                
        # Fall back to syrics library
        if not self.client:
            self.logger.warning("No Spotify cookie provided and RapidAPI failed")
            return None

        try:
            # Search for track
            search_query = f"{title} - {artist}"
            search_results = self.client.search(search_query, type="track", limit=1)

            track_data = search_results["tracks"]["items"][0]
            self.logger.debug(
                f"Found track: {track_data['artists'][0]['name']} - {track_data['name']} " f"({track_data['external_urls']['spotify']})"
            )

            # Get lyrics data
            lyrics_data = self.client.get_lyrics(track_data["id"])
            if not lyrics_data:
                return None

            return {"track_data": track_data, "lyrics_data": lyrics_data}
        except Exception as e:
            self.logger.error(f"Error fetching from Spotify: {str(e)}")
            return None

    def _fetch_from_rapidapi(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch song data using RapidAPI."""
        try:
            # Step 1: Search for the track
            search_url = "https://spotify-scraper.p.rapidapi.com/v1/track/search"
            search_params = {
                "name": f"{title} {artist}"
            }
            
            headers = {
                "x-rapidapi-key": self.rapidapi_key,
                "x-rapidapi-host": "spotify-scraper.p.rapidapi.com"
            }
            
            self.logger.debug(f"Making RapidAPI search request for '{artist} {title}'")
            search_response = requests.get(search_url, headers=headers, params=search_params, timeout=10)
            search_response.raise_for_status()
            
            search_data = search_response.json()
            
            # Check if search was successful
            if not search_data.get("status") or search_data.get("errorId") != "Success":
                self.logger.warning("RapidAPI search failed")
                return None
                
            track_id = search_data.get("id")
            if not track_id:
                self.logger.warning("No track ID found in RapidAPI search results")
                return None
                
            self.logger.debug(f"Found track ID: {track_id}")
            
            # Step 2: Fetch lyrics using the track ID
            lyrics_url = "https://spotify-scraper.p.rapidapi.com/v1/track/lyrics"
            lyrics_params = {
                "trackId": track_id,
                "format": "json",
                "removeNote": "true"
            }
            
            self.logger.debug(f"Making RapidAPI lyrics request for track ID {track_id}")
            lyrics_response = requests.get(lyrics_url, headers=headers, params=lyrics_params, timeout=10)
            lyrics_response.raise_for_status()
            
            lyrics_data = lyrics_response.json()
            
            # Create a clean RapidAPI response structure
            rapidapi_response = {
                "track_data": search_data,
                "lyrics_data": lyrics_data,
                # Mark this as RapidAPI source
                "_rapidapi_source": True
            }
            
            self.logger.info("Successfully fetched lyrics from RapidAPI")
            return rapidapi_response
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"RapidAPI request failed: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching from RapidAPI: {str(e)}")
            return None

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert Spotify's raw API response to standardized format."""
        # Use our explicit source marker for detection
        is_rapidapi = raw_data.get("_rapidapi_source", False)
        
        if is_rapidapi:
            return self._convert_rapidapi_format(raw_data)
        else:
            return self._convert_syrics_format(raw_data)

    def _convert_syrics_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert syrics format to standardized format."""
        track_data = raw_data["track_data"]
        lyrics_data = raw_data["lyrics_data"]["lyrics"]

        # Create metadata object
        metadata = LyricsMetadata(
            source="spotify",
            track_name=track_data.get("name"),
            artist_names=", ".join(artist.get("name", "") for artist in track_data.get("artists", [])),
            album_name=track_data.get("album", {}).get("name"),
            duration_ms=track_data.get("duration_ms"),
            explicit=track_data.get("explicit"),
            language=lyrics_data.get("language"),
            is_synced=lyrics_data.get("syncType") == "LINE_SYNCED",
            lyrics_provider=lyrics_data.get("provider"),
            lyrics_provider_id=lyrics_data.get("providerLyricsId"),
            provider_metadata={
                "spotify_id": track_data.get("id"),
                "preview_url": track_data.get("preview_url"),
                "external_urls": track_data.get("external_urls"),
                "sync_type": lyrics_data.get("syncType"),
                "api_source": "syrics",
            },
        )

        # Create segments with timing information
        segments = []
        for line in lyrics_data.get("lines", []):
            if not line.get("words"):
                continue

            # Skip lines that are just musical notes
            if not self._clean_lyrics(line["words"]):
                continue

            # Split line into words
            word_texts = line["words"].strip().split()
            if not word_texts:
                continue

            # Calculate approximate timing for each word
            start_time = float(line["startTimeMs"]) / 1000 if line["startTimeMs"] != "0" else 0.0
            end_time = float(line["endTimeMs"]) / 1000 if line["endTimeMs"] != "0" else 0.0
            duration = end_time - start_time
            word_duration = duration / len(word_texts)

            words = []
            for i, word_text in enumerate(word_texts):
                word = Word(
                    id=WordUtils.generate_id(),
                    text=word_text,
                    start_time=start_time + (i * word_duration),
                    end_time=start_time + ((i + 1) * word_duration),
                    confidence=1.0,
                    created_during_correction=False,
                )
                words.append(word)

            segment = LyricsSegment(
                id=WordUtils.generate_id(), text=line["words"].strip(), words=words, start_time=start_time, end_time=end_time
            )
            segments.append(segment)

        return LyricsData(source="spotify", segments=segments, metadata=metadata)

    def _convert_rapidapi_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert RapidAPI format to standardized format."""
        track_data = raw_data["track_data"]
        lyrics_data = raw_data["lyrics_data"]

        # Extract artist names from RapidAPI format
        artist_names = []
        if "artists" in track_data:
            artist_names = [artist.get("name", "") for artist in track_data["artists"]]
        
        # Create metadata object
        metadata = LyricsMetadata(
            source="spotify",
            track_name=track_data.get("name"),
            artist_names=", ".join(artist_names),
            album_name=track_data.get("album", {}).get("name"),
            duration_ms=track_data.get("durationMs"),
            explicit=track_data.get("explicit"),
            is_synced=True,  # RapidAPI format includes timing information
            lyrics_provider="spotify",
            lyrics_provider_id=track_data.get("id"),
            provider_metadata={
                "spotify_id": track_data.get("id"),
                "share_url": track_data.get("shareUrl"),
                "duration_text": track_data.get("durationText"),
                "album_cover": track_data.get("album", {}).get("cover"),
                "api_source": "rapidapi",
            },
        )

        # Create segments with timing information from RapidAPI format
        segments = []
        for line in lyrics_data:
            if not line.get("text"):
                continue

            # Skip lines that are just musical notes
            if not self._clean_lyrics(line["text"]):
                continue

            # Split line into words
            word_texts = line["text"].strip().split()
            if not word_texts:
                continue

            # Calculate timing for each word
            start_time = float(line["startMs"]) / 1000 if line.get("startMs") else 0.0
            duration = float(line["durMs"]) / 1000 if line.get("durMs") else 0.0
            end_time = start_time + duration
            word_duration = duration / len(word_texts)

            words = []
            for i, word_text in enumerate(word_texts):
                word = Word(
                    id=WordUtils.generate_id(),
                    text=word_text,
                    start_time=start_time + (i * word_duration),
                    end_time=start_time + ((i + 1) * word_duration),
                    confidence=1.0,
                    created_during_correction=False,
                )
                words.append(word)

            segment = LyricsSegment(
                id=WordUtils.generate_id(),
                text=line["text"].strip(),
                words=words,
                start_time=start_time,
                end_time=end_time
            )
            segments.append(segment)

        return LyricsData(source="spotify", segments=segments, metadata=metadata)

    def _clean_lyrics(self, lyrics: str) -> str:
        """Clean and process lyrics from Spotify to remove unwanted content."""
        # Remove lines that contain only musical note symbols
        if lyrics.strip() == "♪":
            return ""
        return lyrics
