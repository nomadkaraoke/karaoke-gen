import logging
import re
from typing import Optional, Dict, Any, List
import requests
from karaoke_gen.lyrics_transcriber.types import LyricsData, LyricsMetadata, LyricsSegment, Word
from karaoke_gen.lyrics_transcriber.lyrics.base_lyrics_provider import BaseLyricsProvider, LyricsProviderConfig
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


class LRCLIBProvider(BaseLyricsProvider):
    """Handles fetching lyrics from LRCLIB."""

    BASE_URL = "https://lrclib.net"
    USER_AGENT = "lyrics-transcriber (https://github.com/nomadkaraoke/python-lyrics-transcriber)"

    def __init__(self, config: LyricsProviderConfig, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.duration = None  # Will be set when fetching lyrics

    def _fetch_data_from_source(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch raw song data from LRCLIB API."""
        self.logger.info(f"Searching LRCLIB for {artist} - {title}")
        
        # Try to get duration from audio file if available
        duration = self._get_track_duration()
        
        if duration:
            # Try exact match with duration first
            result = self._fetch_with_duration(artist, title, "", duration)
            if result:
                return result
        
        # Fall back to search API if exact match fails or duration unavailable
        result = self._fetch_from_search(artist, title)
        if result:
            return result
            
        self.logger.warning(f"No lyrics found on LRCLIB for {artist} - {title}")
        return None

    def _get_track_duration(self) -> Optional[int]:
        """Get track duration in seconds from audio file."""
        if not self.audio_filepath:
            return None
            
        try:
            import mutagen
            audio = mutagen.File(self.audio_filepath)
            if audio and audio.info:
                duration = int(audio.info.length)
                self.logger.debug(f"Track duration: {duration} seconds")
                return duration
        except Exception as e:
            self.logger.warning(f"Could not determine track duration: {str(e)}")
        
        return None

    def _fetch_with_duration(self, artist: str, title: str, album: str, duration: int) -> Optional[Dict[str, Any]]:
        """Fetch lyrics using the exact signature endpoint."""
        try:
            url = f"{self.BASE_URL}/api/get"
            params = {
                "artist_name": artist,
                "track_name": title,
                "album_name": album,
                "duration": duration
            }
            
            headers = {
                "User-Agent": self.USER_AGENT
            }
            
            self.logger.debug(f"Making LRCLIB request with duration {duration}s")
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            if response.status_code == 404:
                self.logger.debug("Track not found with exact duration")
                return None
                
            response.raise_for_status()
            data = response.json()
            
            self.logger.info("Successfully fetched lyrics from LRCLIB")
            return data
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"LRCLIB request failed: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching from LRCLIB: {str(e)}")
            return None

    def _fetch_from_search(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Fetch lyrics using the search endpoint."""
        try:
            url = f"{self.BASE_URL}/api/search"
            params = {
                "track_name": title,
                "artist_name": artist
            }
            
            headers = {
                "User-Agent": self.USER_AGENT
            }
            
            self.logger.debug(f"Making LRCLIB search request")
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            
            results = response.json()
            
            if not results or len(results) == 0:
                self.logger.debug("No search results from LRCLIB")
                return None
            
            # Return the first (best) match
            best_match = results[0]
            self.logger.info(f"Found lyrics via LRCLIB search: {best_match.get('trackName')} by {best_match.get('artistName')}")
            return best_match
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"LRCLIB search request failed: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Error searching LRCLIB: {str(e)}")
            return None

    def _convert_result_format(self, raw_data: Dict[str, Any]) -> LyricsData:
        """Convert LRCLIB's raw API response to standardized format."""
        # Check if track is instrumental
        is_instrumental = raw_data.get("instrumental", False)
        
        # Determine if we have synced lyrics
        synced_lyrics = raw_data.get("syncedLyrics", "")
        plain_lyrics = raw_data.get("plainLyrics", "")
        has_synced = bool(synced_lyrics and synced_lyrics.strip())
        
        # Create metadata object
        metadata = LyricsMetadata(
            source="lrclib",
            track_name=raw_data.get("trackName", ""),
            artist_names=raw_data.get("artistName", ""),
            album_name=raw_data.get("albumName"),
            duration_ms=raw_data.get("duration", 0) * 1000 if raw_data.get("duration") else None,
            is_synced=has_synced,
            lyrics_provider="lrclib",
            lyrics_provider_id=str(raw_data.get("id")) if raw_data.get("id") else None,
            provider_metadata={
                "lrclib_id": raw_data.get("id"),
                "duration": raw_data.get("duration"),
                "instrumental": is_instrumental,
                "has_synced_lyrics": has_synced,
                "has_plain_lyrics": bool(plain_lyrics and plain_lyrics.strip()),
            },
        )
        
        # Create segments based on whether we have synced or plain lyrics
        if has_synced:
            segments = self._parse_synced_lyrics(synced_lyrics)
        elif plain_lyrics:
            segments = self._create_segments_with_words(plain_lyrics, is_synced=False)
        else:
            # Empty segments for instrumental tracks
            segments = []
        
        return LyricsData(source="lrclib", segments=segments, metadata=metadata)

    def _parse_synced_lyrics(self, synced_lyrics: str) -> List[LyricsSegment]:
        """Parse LRC format synced lyrics into segments with timing."""
        segments = []
        
        # LRC format: [mm:ss.xx] lyrics text
        # Pattern matches timestamps like [00:17.12] or [03:20.31]
        lrc_pattern = re.compile(r'\[(\d+):(\d+)\.(\d+)\]\s*(.+)')
        
        lines = synced_lyrics.strip().split('\n')
        
        for i, line in enumerate(lines):
            match = lrc_pattern.match(line.strip())
            if not match:
                continue
            
            minutes, seconds, centiseconds, text = match.groups()
            
            # Calculate start time in seconds
            start_time = int(minutes) * 60 + int(seconds) + int(centiseconds) / 100
            
            # Estimate end time (use next line's start time or add 3 seconds for last line)
            end_time = start_time + 3.0  # Default duration
            if i + 1 < len(lines):
                next_match = lrc_pattern.match(lines[i + 1].strip())
                if next_match:
                    next_minutes, next_seconds, next_centiseconds, _ = next_match.groups()
                    end_time = int(next_minutes) * 60 + int(next_seconds) + int(next_centiseconds) / 100
            
            # Skip empty lines
            if not text.strip():
                continue
            
            # Split line into words
            word_texts = text.strip().split()
            if not word_texts:
                continue
            
            # Calculate timing for each word
            duration = end_time - start_time
            word_duration = duration / len(word_texts) if len(word_texts) > 0 else duration
            
            words = []
            for j, word_text in enumerate(word_texts):
                word = Word(
                    id=WordUtils.generate_id(),
                    text=word_text,
                    start_time=start_time + (j * word_duration),
                    end_time=start_time + ((j + 1) * word_duration),
                    confidence=1.0,
                    created_during_correction=False,
                )
                words.append(word)
            
            segment = LyricsSegment(
                id=WordUtils.generate_id(),
                text=text.strip(),
                words=words,
                start_time=start_time,
                end_time=end_time
            )
            segments.append(segment)
        
        return segments

