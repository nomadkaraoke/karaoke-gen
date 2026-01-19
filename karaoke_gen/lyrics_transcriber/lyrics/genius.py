import logging
import re
from typing import Optional, Dict, Any
import requests
import lyricsgenius
from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsMetadata
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig


class GeniusProvider(BaseLyricsProvider):
    """Handles fetching lyrics from Genius."""

    def __init__(self, config: LyricsProviderConfig, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.api_token = config.genius_api_token
        self.rapidapi_key = config.rapidapi_key
        self.client = None
        # Only initialize lyricsgenius client if rapidapi_key is not set
        if self.api_token and not self.rapidapi_key:
            self.client = lyricsgenius.Genius(
                self.api_token,
                verbose=(logger.getEffectiveLevel() == logging.DEBUG if logger else False),
                remove_section_headers=True,  # Remove [Chorus], [Verse], etc.
                skip_non_songs=True,  # Skip track listings and other non-song results
                timeout=10,  # Reasonable timeout for requests
                retries=3,  # Number of retries for failed requests
                sleep_time=1,  # Small delay between requests to be nice to the API
            )

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch raw song data from Genius API or RapidAPI."""
        # Try RapidAPI first if available
        if self.rapidapi_key:
            self.logger.info(f"Trying RapidAPI for {artist} - {title}")
            result = self._fetch_from_rapidapi(artist, title)
            if result:
                return result
                
        # Fall back to direct Genius API
        if not self.client:
            self.logger.warning("No Genius API token provided and RapidAPI failed")
            return None

        self.logger.info(f"Searching Genius for {artist} - {title}")
        try:
            song = self.client.search_song(title, artist)
            if song:
                self.logger.info("Found lyrics on Genius")
                return song.to_dict()
        except Exception as e:
            self.logger.error(f"Error fetching from Genius: {str(e)}")
        return None

    def _fetch_from_rapidapi(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch song data using RapidAPI."""
        try:
            # Step 1: Search for the song
            search_url = "https://genius-song-lyrics1.p.rapidapi.com/search/"
            search_params = {
                "q": f"{artist} {title}",
                "per_page": "10",
                "page": "1"
            }
            
            headers = {
                "x-rapidapi-key": self.rapidapi_key,
                "x-rapidapi-host": "genius-song-lyrics1.p.rapidapi.com"
            }
            
            self.logger.debug(f"Making RapidAPI search request for '{artist} {title}'")
            search_response = requests.get(search_url, headers=headers, params=search_params, timeout=10)
            search_response.raise_for_status()
            
            search_data = search_response.json()
            
            # Find the best match from search results
            if not search_data.get("hits"):
                self.logger.warning("No search results from RapidAPI")
                return None
                
            best_match = None
            for hit in search_data["hits"]:
                result = hit.get("result", {})
                if result.get("id"):
                    best_match = result
                    break
            
            if not best_match:
                self.logger.warning("No valid song ID found in RapidAPI search results")
                return None
                
            song_id = best_match["id"]
            self.logger.debug(f"Found song ID: {song_id}")
            
            # Step 2: Fetch lyrics using the song ID
            lyrics_url = "https://genius-song-lyrics1.p.rapidapi.com/song/lyrics/"
            lyrics_params = {"id": str(song_id)}
            
            self.logger.debug(f"Making RapidAPI lyrics request for song ID {song_id}")
            lyrics_response = requests.get(lyrics_url, headers=headers, params=lyrics_params, timeout=10)
            lyrics_response.raise_for_status()
            
            lyrics_data = lyrics_response.json()
            
            # Extract lyrics from the nested response structure
            lyrics_text = self._extract_lyrics_from_rapidapi_response(lyrics_data)
            if not lyrics_text:
                self.logger.warning("No lyrics found in RapidAPI response")
                return None
                
            # Create a clean RapidAPI-only response structure
            # Don't mix search metadata (which contains Genius fields) with our clean structure
            rapidapi_response = {
                "title": best_match.get("title", ""),
                "primary_artist": best_match.get("primary_artist", {}),
                "lyrics": lyrics_text,
                "id": song_id,
                "url": best_match.get("url", ""),
                "release_date_for_display": best_match.get("release_date_for_display", ""),
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

    def _extract_lyrics_from_rapidapi_response(self, lyrics_data: Dict[str, Any]) -> Optional[str]:
        """Extract lyrics text from RapidAPI response structure."""
        try:
            # Log the actual response structure for debugging
            self.logger.debug(f"RapidAPI response structure: {lyrics_data}")
            
            # Try different possible response structures
            
            # Structure 1: lyrics.lyrics.body.html (the actual RapidAPI structure)
            nested_lyrics = lyrics_data.get("lyrics", {}).get("lyrics", {})
            if isinstance(nested_lyrics, dict):
                html_content = nested_lyrics.get("body", {}).get("html")
                if html_content:
                    return self._clean_html_lyrics(html_content)
            
            # Structure 2: lyrics.lyrics (simple string)
            if isinstance(lyrics_data.get("lyrics", {}).get("lyrics"), str):
                return lyrics_data["lyrics"]["lyrics"]
            
            # Structure 3: lyrics.body.html (HTML content)
            html_content = lyrics_data.get("lyrics", {}).get("body", {}).get("html")
            if html_content:
                return self._clean_html_lyrics(html_content)
            
            # Structure 4: Direct lyrics field
            if isinstance(lyrics_data.get("lyrics"), str):
                return lyrics_data["lyrics"]
                
            # Structure 5: body.html at top level
            if lyrics_data.get("body", {}).get("html"):
                return self._clean_html_lyrics(lyrics_data["body"]["html"])
            
            # Structure 6: Check if lyrics is a dict with other possible keys
            lyrics_obj = lyrics_data.get("lyrics", {})
            if isinstance(lyrics_obj, dict):
                # Try common alternative keys
                for key in ["text", "content", "plain", "body"]:
                    if key in lyrics_obj:
                        content = lyrics_obj[key]
                        if isinstance(content, str):
                            return content
                        elif isinstance(content, dict) and "html" in content:
                            return self._clean_html_lyrics(content["html"])
                        elif isinstance(content, dict) and "text" in content:
                            return content["text"]
            
            self.logger.warning(f"Unknown RapidAPI response structure: {list(lyrics_data.keys())}")
            if "lyrics" in lyrics_data:
                self.logger.warning(f"Lyrics object structure: {lyrics_data['lyrics']}")
            return None
            
        except Exception as e:
            self.logger.error(f"Error extracting lyrics from RapidAPI response: {str(e)}")
            return None

    def _clean_html_lyrics(self, html_content: str) -> str:
        """Clean HTML content to extract plain text lyrics."""
        import re
        
        if not html_content:
            return ""
        
        # Remove HTML tags while preserving line breaks
        text = re.sub(r'<br\s*/?>', '\n', html_content)  # Convert <br> to newlines
        text = re.sub(r'<[^>]+>', '', text)  # Remove all other HTML tags
        
        # Decode HTML entities
        text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        text = text.replace('&quot;', '"').replace('&#x27;', "'").replace('&nbsp;', ' ')
        
        # Remove section markers but keep the lyrics content
        # Instead of removing entire lines, just remove the square bracket markers
        text = re.sub(r'\[Verse \d+\]', '', text)
        text = re.sub(r'\[Pre-Chorus\]', '', text)
        text = re.sub(r'\[Chorus\]', '', text)
        text = re.sub(r'\[Refrain\]', '', text)
        text = re.sub(r'\[Outro\]', '', text)
        text = re.sub(r'\[Bridge\]', '', text)
        text = re.sub(r'\[Intro\]', '', text)
        
        # Clean up multiple consecutive newlines
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        
        # Clean up leading/trailing whitespace
        text = text.strip()
        
        return text

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert Genius's raw API response to standardized format."""
        # Use our explicit source marker for detection
        is_rapidapi = raw_data.get("_rapidapi_source", False)
        
        if is_rapidapi:
            return self._convert_rapidapi_format(raw_data)
        else:
            return self._convert_lyricsgenius_format(raw_data)

    def _convert_lyricsgenius_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert lyricsgenius format to standardized format."""
        # Clean the lyrics before processing
        lyrics = self._clean_lyrics(raw_data.get("lyrics", ""))

        # Extract release date components if available
        release_date = None
        if release_components := raw_data.get("release_date_components"):
            year = release_components.get("year")
            month = release_components.get("month")
            day = release_components.get("day")
            if all(x is not None for x in (year, month, day)):
                release_date = f"{year}-{month:02d}-{day:02d}"

        # Create metadata object
        metadata = LyricsMetadata(
            source="genius",
            track_name=raw_data.get("title", ""),
            artist_names=raw_data.get("artist_names", ""),
            album_name=raw_data.get("album", {}).get("name"),
            lyrics_provider="genius",
            lyrics_provider_id=str(raw_data.get("id")),
            is_synced=False,  # Genius doesn't provide synced lyrics
            provider_metadata={
                "genius_id": raw_data.get("id"),
                "release_date": release_date,
                "page_url": raw_data.get("url"),
                "annotation_count": raw_data.get("annotation_count"),
                "lyrics_state": raw_data.get("lyrics_state"),
                "lyrics_owner_id": raw_data.get("lyrics_owner_id"),
                "pyongs_count": raw_data.get("pyongs_count"),
                "verified_annotations": len(raw_data.get("verified_annotations_by", [])),
                "verified_contributors": len(raw_data.get("verified_contributors", [])),
                "external_urls": {"genius": raw_data.get("url")},
                "api_source": "lyricsgenius",
            },
        )

        # Create segments with words from cleaned lyrics
        segments = self._create_segments_with_words(lyrics, is_synced=False)

        # Create result object with segments
        return LyricsData(source="genius", segments=segments, metadata=metadata)

    def _convert_rapidapi_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert RapidAPI format to standardized format."""
        # Clean the lyrics before processing
        lyrics = self._clean_lyrics(raw_data.get("lyrics", ""))

        # Extract artist name from primary_artist
        primary_artist = raw_data.get("primary_artist", {})
        artist_name = primary_artist.get("name", "")

        # Extract release date from release_date_for_display
        release_date = raw_data.get("release_date_for_display")

        # Create metadata object
        metadata = LyricsMetadata(
            source="genius",
            track_name=raw_data.get("title", ""),
            artist_names=artist_name,
            album_name=raw_data.get("album", {}).get("name") if raw_data.get("album") else None,
            lyrics_provider="genius",
            lyrics_provider_id=str(raw_data.get("id")),
            is_synced=False,  # Genius doesn't provide synced lyrics
            provider_metadata={
                "genius_id": raw_data.get("id"),
                "release_date": release_date,
                "page_url": raw_data.get("url"),
                "annotation_count": raw_data.get("annotation_count"),
                "lyrics_state": raw_data.get("lyrics_state"),
                "pyongs_count": raw_data.get("pyongs_count"),
                "external_urls": {"genius": raw_data.get("url")},
                "api_source": "rapidapi",
            },
        )

        # Create segments with words from cleaned lyrics
        segments = self._create_segments_with_words(lyrics, is_synced=False)

        # Create result object with segments
        return LyricsData(source="genius", segments=segments, metadata=metadata)

    def _clean_lyrics(self, lyrics: str) -> str:
        """Clean and process lyrics from Genius to remove unwanted content."""
        self.logger.debug("Starting lyrics cleaning process")
        
        # Handle unexpected input types
        if not isinstance(lyrics, str):
            self.logger.warning(f"Expected string for lyrics, got {type(lyrics)}: {repr(lyrics)}")
            if lyrics is None:
                return ""
            # Try to convert to string
            try:
                lyrics = str(lyrics)
            except Exception as e:
                self.logger.error(f"Failed to convert lyrics to string: {e}")
                return ""
        
        original = lyrics

        lyrics = lyrics.replace("\\n", "\n")
        lyrics = re.sub(r"You might also like", "", lyrics)
        if original != lyrics:
            self.logger.debug("Removed 'You might also like' text")

        original = lyrics
        lyrics = re.sub(r".*?Lyrics([A-Z])", r"\1", lyrics)
        if original != lyrics:
            self.logger.debug("Removed song name and 'Lyrics' prefix")

        original = lyrics
        lyrics = re.sub(r"^[0-9]* Contributors.*Lyrics", "", lyrics)
        if original != lyrics:
            self.logger.debug("Removed contributors count and 'Lyrics' text")

        original = lyrics
        lyrics = re.sub(r"See.*Live.*Get tickets as low as \$[0-9]+", "", lyrics)
        if original != lyrics:
            self.logger.debug("Removed ticket sales text")

        original = lyrics
        lyrics = re.sub(r"[0-9]+Embed$", "", lyrics)
        if original != lyrics:
            self.logger.debug("Removed numbered embed marker")

        original = lyrics
        lyrics = re.sub(r"(\S)Embed$", r"\1", lyrics)
        if original != lyrics:
            self.logger.debug("Removed 'Embed' suffix from word")

        original = lyrics
        lyrics = re.sub(r"^Embed$", r"", lyrics)
        if original != lyrics:
            self.logger.debug("Removed standalone 'Embed' text")

        # Remove section markers but keep the lyrics content (for non-HTML lyrics)
        # Instead of removing entire lines, just remove the square bracket markers
        original = lyrics
        lyrics = re.sub(r'\[Verse \d+\]', '', lyrics)
        lyrics = re.sub(r'\[Pre-Chorus\]', '', lyrics)
        lyrics = re.sub(r'\[Chorus\]', '', lyrics)
        lyrics = re.sub(r'\[Refrain\]', '', lyrics)
        lyrics = re.sub(r'\[Outro\]', '', lyrics)
        lyrics = re.sub(r'\[Bridge\]', '', lyrics)
        lyrics = re.sub(r'\[Intro\]', '', lyrics)
        if original != lyrics:
            self.logger.debug("Removed section markers while preserving lyrics content")

        # Remove common LyricsGenius page elements

        self.logger.debug("Completed lyrics cleaning process")
        return lyrics
