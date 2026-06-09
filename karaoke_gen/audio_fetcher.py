"""
Audio Fetcher module - abstraction layer for fetching audio files.

This module provides a clean interface for searching and downloading audio files
using flacfetch, replacing the previous direct yt-dlp usage.

Supports two modes:
1. Local mode: Uses flacfetch library directly (requires torrent client, etc.)
2. Remote mode: Uses a remote flacfetch HTTP API server when FLACFETCH_API_URL
   and FLACFETCH_API_KEY environment variables are set.
"""

import logging
import os
import signal
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any

# Optional import for remote fetcher
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Global flag to track if user requested cancellation via Ctrl+C
_interrupt_requested = False


@dataclass
class AudioSearchResult:
    """Represents a single search result for audio.
    
    Used by both local CLI and cloud backend. Supports serialization
    for Firestore storage via to_dict()/from_dict().
    
    For rich display, this class can serialize the full flacfetch Release
    data so remote CLIs can use flacfetch's shared display functions.
    """

    title: str
    artist: str
    url: str
    provider: str
    duration: Optional[int] = None  # Duration in seconds
    quality: Optional[str] = None  # e.g., "FLAC", "320kbps", etc.
    source_id: Optional[str] = None  # Unique ID from the source
    index: int = 0  # Index in the results list (for API selection)
    seeders: Optional[int] = None  # Number of seeders (for torrent sources)
    target_file: Optional[str] = None  # Target filename in the release
    # Raw result object from the provider (for download) - not serialized
    raw_result: Optional[object] = field(default=None, repr=False)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON/Firestore serialization.
        
        Includes full flacfetch Release data if available, enabling
        remote CLIs to use flacfetch's shared display functions.
        """
        result = {
            "title": self.title,
            "artist": self.artist,
            "url": self.url,
            "provider": self.provider,
            "duration": self.duration,
            "quality": self.quality,
            "source_id": self.source_id,
            "index": self.index,
            "seeders": self.seeders,
            "target_file": self.target_file,
        }
        
        # If we have a raw_result (flacfetch Release or dict), include its full data
        # This enables rich display on the remote CLI
        # raw_result can be either:
        # - A dict (from remote flacfetch API)
        # - A Release object (from local flacfetch)
        if self.raw_result:
            if isinstance(self.raw_result, dict):
                # Remote flacfetch API returns dicts directly
                release_dict = self.raw_result
            else:
                # Local flacfetch returns Release objects
                try:
                    release_dict = self.raw_result.to_dict()
                except AttributeError:
                    release_dict = {}  # raw_result doesn't have to_dict() method
            
            # Merge Release fields into result (they may override basic fields)
            for key in ['year', 'label', 'edition_info', 'release_type', 'channel',
                       'view_count', 'size_bytes', 'target_file_size', 'track_pattern',
                       'match_score', 'formatted_size', 'formatted_duration',
                       'formatted_views', 'is_lossless', 'quality_str']:
                if key in release_dict:
                    result[key] = release_dict[key]
            
            # Handle quality dict - remote API uses 'quality_data', local uses 'quality'
            if 'quality_data' in release_dict:
                result['quality_data'] = release_dict['quality_data']
            elif 'quality' in release_dict and isinstance(release_dict['quality'], dict):
                result['quality_data'] = release_dict['quality']
        
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioSearchResult":
        """Create from dict (e.g., from Firestore)."""
        return cls(
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            url=data.get("url", ""),
            provider=data.get("provider", "Unknown"),
            duration=data.get("duration"),
            quality=data.get("quality"),
            source_id=data.get("source_id"),
            index=data.get("index", 0),
            seeders=data.get("seeders"),
            target_file=data.get("target_file"),
            raw_result=None,  # Not stored in serialized form
        )


@dataclass
class AudioFetchResult:
    """Result of an audio fetch operation.
    
    Used by both local CLI and cloud backend. Supports serialization
    for Firestore storage via to_dict()/from_dict().
    """

    filepath: str
    artist: str
    title: str
    provider: str
    duration: Optional[int] = None
    quality: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON/Firestore serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioFetchResult":
        """Create from dict (e.g., from Firestore)."""
        return cls(
            filepath=data.get("filepath", ""),
            artist=data.get("artist", ""),
            title=data.get("title", ""),
            provider=data.get("provider", "Unknown"),
            duration=data.get("duration"),
            quality=data.get("quality"),
        )


class AudioFetcherError(Exception):
    """Base exception for audio fetcher errors."""

    pass


class NoResultsError(AudioFetcherError):
    """Raised when no search results are found."""

    pass


class DownloadError(AudioFetcherError):
    """Raised when download fails."""

    pass


class UserCancelledError(AudioFetcherError):
    """Raised when user explicitly cancels the operation (e.g., enters 0 or Ctrl+C)."""

    pass


def _check_interrupt():
    """Check if interrupt was requested and raise UserCancelledError if so."""
    global _interrupt_requested
    if _interrupt_requested:
        raise UserCancelledError("Operation cancelled by user")


class AudioFetcher(ABC):
    """Abstract base class for audio fetching implementations."""

    @abstractmethod
    def search(self, artist: str, title: str) -> List[AudioSearchResult]:
        """
        Search for audio matching the given artist and title.

        Args:
            artist: The artist name to search for
            title: The track title to search for

        Returns:
            List of AudioSearchResult objects

        Raises:
            NoResultsError: If no results are found
            AudioFetcherError: For other errors
        """
        pass

    @abstractmethod
    def download(
        self,
        result: AudioSearchResult,
        output_dir: str,
        output_filename: Optional[str] = None,
    ) -> AudioFetchResult:
        """
        Download audio from a search result.

        Args:
            result: The search result to download
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)

        Returns:
            AudioFetchResult with the downloaded file path

        Raises:
            DownloadError: If download fails
        """
        pass

    @abstractmethod
    def search_and_download(
        self,
        artist: str,
        title: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        auto_select: bool = False,
    ) -> AudioFetchResult:
        """
        Search for audio and download it in one operation.

        In interactive mode (auto_select=False), this will present options to the user.
        In auto mode (auto_select=True), this will automatically select the best result.

        Args:
            artist: The artist name to search for
            title: The track title to search for
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            auto_select: If True, automatically select the best result

        Returns:
            AudioFetchResult with the downloaded file path

        Raises:
            NoResultsError: If no results are found
            DownloadError: If download fails
        """
        pass

    @abstractmethod
    def download_from_url(
        self,
        url: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> AudioFetchResult:
        """
        Download audio directly from a URL (e.g., YouTube URL).

        This bypasses the search step and downloads directly from the provided URL.
        Useful when the user provides a specific YouTube URL rather than artist/title.

        Args:
            url: The URL to download from (e.g., YouTube video URL)
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            artist: Optional artist name for metadata
            title: Optional title for metadata

        Returns:
            AudioFetchResult with the downloaded file path

        Raises:
            DownloadError: If download fails
        """
        pass


class FlacFetchAudioFetcher(AudioFetcher):
    """
    Audio fetcher implementation using flacfetch library.

    This provides access to multiple audio sources including private music trackers
    and YouTube, with intelligent prioritization of high-quality sources.
    
    Also exported as FlacFetcher for shorter name.
    """

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        red_api_key: Optional[str] = None,
        red_api_url: Optional[str] = None,
        ops_api_key: Optional[str] = None,
        ops_api_url: Optional[str] = None,
        provider_priority: Optional[List[str]] = None,
    ):
        """
        Initialize the FlacFetch audio fetcher.

        Args:
            logger: Logger instance for output
            red_api_key: API key for RED tracker (optional)
            red_api_url: Base URL for RED tracker API (optional, required if using RED)
            ops_api_key: API key for OPS tracker (optional)
            ops_api_url: Base URL for OPS tracker API (optional, required if using OPS)
            provider_priority: Custom provider priority order (optional)
        """
        self.logger = logger or logging.getLogger(__name__)
        self._red_api_key = red_api_key or os.environ.get("RED_API_KEY")
        self._red_api_url = red_api_url or os.environ.get("RED_API_URL")
        self._ops_api_key = ops_api_key or os.environ.get("OPS_API_KEY")
        self._ops_api_url = ops_api_url or os.environ.get("OPS_API_URL")
        self._provider_priority = provider_priority
        self._manager = None
        self._transmission_available = None  # Cached result of Transmission check

    def _check_transmission_available(self) -> bool:
        """
        Check if Transmission daemon is available for torrent downloads.
        
        This prevents adding tracker providers (RED/OPS) when Transmission
        isn't running, which would result in search results that can't be downloaded.
        
        Returns:
            True if Transmission is available and responsive, False otherwise.
        """
        if self._transmission_available is not None:
            self.logger.info(f"[Transmission] Using cached status: available={self._transmission_available}")
            return self._transmission_available
        
        host = os.environ.get("TRANSMISSION_HOST", "localhost")
        port = int(os.environ.get("TRANSMISSION_PORT", "9091"))
        self.logger.info(f"[Transmission] Checking availability at {host}:{port}")
        
        try:
            import transmission_rpc
            self.logger.info(f"[Transmission] transmission_rpc imported successfully")
            
            client = transmission_rpc.Client(host=host, port=port, timeout=5)
            self.logger.info(f"[Transmission] Client created, calling session_stats()...")
            
            # Simple test to verify connection works
            stats = client.session_stats()
            self.logger.info(f"[Transmission] Connected! Download dir: {getattr(stats, 'download_dir', 'unknown')}")
            
            self._transmission_available = True
        except ImportError as e:
            self._transmission_available = False
            self.logger.warning(f"[Transmission] transmission_rpc not installed: {e}")
        except Exception as e:
            self._transmission_available = False
            self.logger.warning(f"[Transmission] Connection failed to {host}:{port}: {type(e).__name__}: {e}")
        
        self.logger.info(f"[Transmission] Final status: available={self._transmission_available}")
        return self._transmission_available

    def _get_manager(self):
        """Lazily initialize and return the FetchManager."""
        if self._manager is None:
            # Import flacfetch here to avoid import errors if not installed
            from flacfetch.core.manager import FetchManager
            from flacfetch.providers.youtube import YoutubeProvider
            from flacfetch.downloaders.youtube import YoutubeDownloader

            # Try to import TorrentDownloader (has optional dependencies)
            TorrentDownloader = None
            try:
                from flacfetch.downloaders.torrent import TorrentDownloader
            except ImportError:
                self.logger.debug("TorrentDownloader not available (missing dependencies)")

            self._manager = FetchManager()

            # Only add tracker providers if we can actually download from them
            # This requires both TorrentDownloader and a running Transmission daemon
            has_torrent_downloader = TorrentDownloader is not None
            transmission_available = self._check_transmission_available()
            can_use_trackers = has_torrent_downloader and transmission_available
            
            self.logger.info(
                f"[FlacFetcher] Provider setup: TorrentDownloader={has_torrent_downloader}, "
                f"Transmission={transmission_available}, can_use_trackers={can_use_trackers}"
            )
            
            if not can_use_trackers and (self._red_api_key or self._ops_api_key):
                self.logger.warning(
                    "[FlacFetcher] Tracker providers (RED/OPS) DISABLED: "
                    f"TorrentDownloader={has_torrent_downloader}, Transmission={transmission_available}. "
                    "Only YouTube sources will be used."
                )

            # Add providers and downloaders based on available API keys and URLs
            if self._red_api_key and self._red_api_url and can_use_trackers:
                from flacfetch.providers.red import REDProvider

                self._manager.add_provider(REDProvider(api_key=self._red_api_key, base_url=self._red_api_url))
                self._manager.register_downloader("RED", TorrentDownloader())
                self.logger.info("[FlacFetcher] Added RED provider with TorrentDownloader")
            elif self._red_api_key and not self._red_api_url:
                self.logger.warning("[FlacFetcher] RED_API_KEY set but RED_API_URL not set - RED provider disabled")

            if self._ops_api_key and self._ops_api_url and can_use_trackers:
                from flacfetch.providers.ops import OPSProvider

                self._manager.add_provider(OPSProvider(api_key=self._ops_api_key, base_url=self._ops_api_url))
                self._manager.register_downloader("OPS", TorrentDownloader())
                self.logger.info("[FlacFetcher] Added OPS provider with TorrentDownloader")
            elif self._ops_api_key and not self._ops_api_url:
                self.logger.warning("[FlacFetcher] OPS_API_KEY set but OPS_API_URL not set - OPS provider disabled")

            # Always add YouTube as a fallback provider with its downloader
            self._manager.add_provider(YoutubeProvider())
            self._manager.register_downloader("YouTube", YoutubeDownloader())
            self.logger.debug("Added YouTube provider")

        return self._manager

    def search(self, artist: str, title: str) -> List[AudioSearchResult]:
        """
        Search for audio matching the given artist and title.

        Args:
            artist: The artist name to search for
            title: The track title to search for

        Returns:
            List of AudioSearchResult objects

        Raises:
            NoResultsError: If no results are found
        """
        from flacfetch.core.models import TrackQuery

        manager = self._get_manager()
        query = TrackQuery(artist=artist, title=title)

        self.logger.info(f"Searching for: {artist} - {title}")
        results = manager.search(query)

        if not results:
            raise NoResultsError(f"No results found for: {artist} - {title}")

        # Convert to our AudioSearchResult format
        search_results = []
        for i, result in enumerate(results):
            # Get quality as string if it's a Quality object
            quality = getattr(result, "quality", None)
            quality_str = str(quality) if quality else None

            search_results.append(
                AudioSearchResult(
                    title=getattr(result, "title", title),
                    artist=getattr(result, "artist", artist),
                    url=getattr(result, "download_url", "") or "",
                    provider=getattr(result, "source_name", "Unknown"),
                    duration=getattr(result, "duration_seconds", None),
                    quality=quality_str,
                    source_id=getattr(result, "info_hash", None),
                    index=i,  # Set index for API selection
                    seeders=getattr(result, "seeders", None),
                    target_file=getattr(result, "target_file", None),
                    raw_result=result,
                )
            )

        self.logger.info(f"Found {len(search_results)} results")
        return search_results

    def download(
        self,
        result: AudioSearchResult,
        output_dir: str,
        output_filename: Optional[str] = None,
    ) -> AudioFetchResult:
        """
        Download audio from a search result.

        Args:
            result: The search result to download
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)

        Returns:
            AudioFetchResult with the downloaded file path

        Raises:
            DownloadError: If download fails
        """
        manager = self._get_manager()

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Generate filename if not provided
        if output_filename is None:
            output_filename = f"{result.artist} - {result.title}"

        self.logger.info(f"Downloading: {result.artist} - {result.title} from {result.provider or 'Unknown'}")

        try:
            # Use flacfetch to download
            filepath = manager.download(
                result.raw_result,
                output_path=output_dir,
                output_filename=output_filename,
            )

            if filepath is None:
                raise DownloadError(f"Download returned no file path for: {result.artist} - {result.title}")

            self.logger.info(f"Downloaded to: {filepath}")

            return AudioFetchResult(
                filepath=filepath,
                artist=result.artist,
                title=result.title,
                provider=result.provider,
                duration=result.duration,
                quality=result.quality,
            )

        except Exception as e:
            raise DownloadError(f"Failed to download {result.artist} - {result.title}: {e}") from e

    def select_best(self, results: List[AudioSearchResult]) -> int:
        """
        Select the best result from a list of search results.
        
        Uses flacfetch's built-in quality ranking to determine the best source.
        This is useful for automated/non-interactive usage.
        
        Args:
            results: List of AudioSearchResult objects from search()
            
        Returns:
            Index of the best result in the list
        """
        if not results:
            return 0
        
        manager = self._get_manager()
        
        # Get raw results that have raw_result set
        raw_results = [r.raw_result for r in results if r.raw_result is not None]
        
        if raw_results:
            try:
                best = manager.select_best(raw_results)
                # Find index of best result
                for i, r in enumerate(results):
                    if r.raw_result == best:
                        return i
            except Exception as e:
                self.logger.warning(f"select_best failed, using first result: {e}")
        
        # Fallback: return first result
        return 0

    def search_and_download(
        self,
        artist: str,
        title: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        auto_select: bool = False,
    ) -> AudioFetchResult:
        """
        Search for audio and download it in one operation.

        In interactive mode (auto_select=False), this will present options to the user.
        In auto mode (auto_select=True), this will automatically select the best result.

        Args:
            artist: The artist name to search for
            title: The track title to search for
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            auto_select: If True, automatically select the best result

        Returns:
            AudioFetchResult with the downloaded file path

        Raises:
            NoResultsError: If no results are found
            DownloadError: If download fails
            UserCancelledError: If user cancels (Ctrl+C or enters 0)
        """
        from flacfetch.core.models import TrackQuery

        manager = self._get_manager()
        query = TrackQuery(artist=artist, title=title)

        self.logger.info(f"Searching for: {artist} - {title}")
        
        # Run search in a thread so we can handle Ctrl+C
        results = self._interruptible_search(manager, query)

        if not results:
            raise NoResultsError(f"No results found for: {artist} - {title}")

        self.logger.info(f"Found {len(results)} results")

        if auto_select:
            # Auto mode: select best result based on flacfetch's ranking
            selected = manager.select_best(results)
            self.logger.info(f"Auto-selected: {getattr(selected, 'title', title)} from {getattr(selected, 'source_name', 'Unknown')}")
        else:
            # Interactive mode: present options to user
            selected = self._interactive_select(results, artist, title)

        # Note: _interactive_select now raises UserCancelledError instead of returning None
        # This check is kept as a safety net
        if selected is None:
            raise NoResultsError(f"No result selected for: {artist} - {title}")

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Generate filename if not provided
        if output_filename is None:
            output_filename = f"{artist} - {title}"

        self.logger.info(f"Downloading from {getattr(selected, 'source_name', 'Unknown')}...")

        try:
            # Use interruptible download so Ctrl+C works during torrent downloads
            filepath = self._interruptible_download(
                manager,
                selected,
                output_path=output_dir,
                output_filename=output_filename,
            )

            if not filepath:
                raise DownloadError(f"Download returned no file path for: {artist} - {title}")

            self.logger.info(f"Downloaded to: {filepath}")

            # Get quality as string if it's a Quality object
            quality = getattr(selected, "quality", None)
            quality_str = str(quality) if quality else None

            return AudioFetchResult(
                filepath=filepath,
                artist=artist,
                title=title,
                provider=getattr(selected, "source_name", "Unknown"),
                duration=getattr(selected, "duration_seconds", None),
                quality=quality_str,
            )

        except (UserCancelledError, KeyboardInterrupt):
            # Let cancellation exceptions propagate without wrapping
            raise
        except Exception as e:
            raise DownloadError(f"Failed to download {artist} - {title}: {e}") from e

    def download_from_url(
        self,
        url: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> AudioFetchResult:
        """
        Download audio directly from a URL (e.g., YouTube URL).

        Uses flacfetch's download_by_id() method which supports direct YouTube downloads.

        Args:
            url: The URL to download from (e.g., YouTube video URL)
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            artist: Optional artist name for metadata
            title: Optional title for metadata

        Returns:
            AudioFetchResult with the downloaded file path

        Raises:
            DownloadError: If download fails
        """
        import re

        manager = self._get_manager()

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Detect source type from URL
        source_name = "YouTube"  # Default to YouTube for now
        source_id = None

        # Extract YouTube video ID from URL
        youtube_patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
        ]
        for pattern in youtube_patterns:
            match = re.search(pattern, url)
            if match:
                source_id = match.group(1)
                break

        if not source_id:
            # For other URLs, use the full URL as the source_id
            source_id = url

        # Generate filename if not provided
        if output_filename is None:
            if artist and title:
                output_filename = f"{artist} - {title}"
            else:
                output_filename = source_id

        self.logger.info(f"Downloading from URL: {url}")

        try:
            filepath = manager.download_by_id(
                source_name=source_name,
                source_id=source_id,
                output_path=output_dir,
                output_filename=output_filename,
                download_url=url,  # Pass full URL for direct download
            )

            if not filepath:
                raise DownloadError(f"Download returned no file path for URL: {url}")

            self.logger.info(f"Downloaded to: {filepath}")

            return AudioFetchResult(
                filepath=filepath,
                artist=artist or "",
                title=title or "",
                provider=source_name,
                duration=None,  # Could extract from yt-dlp info if needed
                quality=None,
            )

        except Exception as e:
            raise DownloadError(f"Failed to download from URL {url}: {e}") from e

    def _interruptible_search(self, manager, query) -> list:
        """
        Run search in a way that can be interrupted by Ctrl+C.
        
        The flacfetch search is a blocking network operation that doesn't
        respond to SIGINT while running. This method runs it in a background
        thread and periodically checks for interrupts.
        
        Args:
            manager: The FetchManager instance
            query: The TrackQuery to search for
            
        Returns:
            List of search results
            
        Raises:
            UserCancelledError: If user presses Ctrl+C during search
        """
        global _interrupt_requested
        _interrupt_requested = False
        result_container = {"results": None, "error": None}
        
        def do_search():
            try:
                result_container["results"] = manager.search(query)
            except Exception as e:
                result_container["error"] = e
        
        # Set up signal handler for immediate response to Ctrl+C
        original_handler = signal.getsignal(signal.SIGINT)
        
        def interrupt_handler(signum, frame):
            global _interrupt_requested
            _interrupt_requested = True
            # Print immediately so user knows it was received
            print("\nCancelling... please wait", file=sys.stderr)
        
        signal.signal(signal.SIGINT, interrupt_handler)
        
        try:
            # Start search in background thread
            search_thread = threading.Thread(target=do_search, daemon=True)
            search_thread.start()
            
            # Wait for completion with periodic interrupt checks
            while search_thread.is_alive():
                search_thread.join(timeout=0.1)  # Check every 100ms
                if _interrupt_requested:
                    # Don't wait for thread - it's a daemon and will be killed
                    raise UserCancelledError("Search cancelled by user (Ctrl+C)")
            
            # Check for errors from the search
            if result_container["error"] is not None:
                raise result_container["error"]
                
            return result_container["results"]
            
        finally:
            # Restore original signal handler
            signal.signal(signal.SIGINT, original_handler)
            _interrupt_requested = False

    def _interruptible_download(self, manager, selected, output_path: str, output_filename: str) -> str:
        """
        Run download in a way that can be interrupted by Ctrl+C.
        
        The flacfetch/transmission download is a blocking operation that doesn't
        respond to SIGINT while running (especially for torrent downloads).
        This method runs it in a background thread and periodically checks for interrupts.
        
        Args:
            manager: The FetchManager instance
            selected: The selected result to download
            output_path: Directory to save the file
            output_filename: Filename to save as
            
        Returns:
            Path to the downloaded file
            
        Raises:
            UserCancelledError: If user presses Ctrl+C during download
            DownloadError: If download fails
        """
        global _interrupt_requested
        _interrupt_requested = False
        result_container = {"filepath": None, "error": None}
        was_cancelled = False
        
        def do_download():
            try:
                result_container["filepath"] = manager.download(
                    selected,
                    output_path=output_path,
                    output_filename=output_filename,
                )
            except Exception as e:
                result_container["error"] = e
        
        # Set up signal handler for immediate response to Ctrl+C
        original_handler = signal.getsignal(signal.SIGINT)
        
        def interrupt_handler(signum, frame):
            global _interrupt_requested
            _interrupt_requested = True
            # Print immediately so user knows it was received
            print("\nCancelling download... please wait (may take a few seconds)", file=sys.stderr)
        
        signal.signal(signal.SIGINT, interrupt_handler)
        
        try:
            # Start download in background thread
            download_thread = threading.Thread(target=do_download, daemon=True)
            download_thread.start()
            
            # Wait for completion with periodic interrupt checks
            while download_thread.is_alive():
                download_thread.join(timeout=0.2)  # Check every 200ms
                if _interrupt_requested:
                    was_cancelled = True
                    # Clean up any pending torrents before raising
                    self._cleanup_transmission_torrents(selected)
                    raise UserCancelledError("Download cancelled by user (Ctrl+C)")
            
            # Check for errors from the download
            if result_container["error"] is not None:
                raise result_container["error"]
                
            return result_container["filepath"]
            
        finally:
            # Restore original signal handler
            signal.signal(signal.SIGINT, original_handler)
            _interrupt_requested = False

    def _cleanup_transmission_torrents(self, selected) -> None:
        """
        Clean up any torrents in Transmission that were started for this download.
        
        Called when a download is cancelled to remove incomplete torrents and their data.
        
        Args:
            selected: The selected result that was being downloaded
        """
        try:
            import transmission_rpc
            host = os.environ.get("TRANSMISSION_HOST", "localhost")
            port = int(os.environ.get("TRANSMISSION_PORT", "9091"))
            client = transmission_rpc.Client(host=host, port=port, timeout=5)
            
            # Get the release name to match against torrents
            release_name = getattr(selected, 'name', None) or getattr(selected, 'title', None)
            if not release_name:
                self.logger.debug("[Transmission] No release name to match for cleanup")
                return
            
            # Find and remove matching incomplete torrents
            torrents = client.get_torrents()
            for torrent in torrents:
                # Match by name similarity and incomplete status
                if torrent.progress < 100 and release_name.lower() in torrent.name.lower():
                    self.logger.info(f"[Transmission] Removing cancelled torrent: {torrent.name}")
                    client.remove_torrent(torrent.id, delete_data=True)
                    
        except Exception as e:
            # Don't fail the cancellation if cleanup fails
            self.logger.debug(f"[Transmission] Cleanup failed (non-fatal): {e}")

    def _interactive_select(self, results: list, artist: str, title: str) -> object:
        """
        Present search results to the user for interactive selection.

        Uses flacfetch's built-in CLIHandler for rich, colorized output.

        Args:
            results: List of Release objects from flacfetch
            artist: The artist name being searched
            title: The track title being searched

        Returns:
            The selected Release object
            
        Raises:
            UserCancelledError: If user cancels selection
        """
        try:
            # Use flacfetch's built-in CLIHandler for rich display
            from flacfetch.interface.cli import CLIHandler

            handler = CLIHandler(target_artist=artist)
            result = handler.select_release(results)
            if result is None:
                # User selected 0 to cancel
                raise UserCancelledError("Selection cancelled by user")
            return result
        except ImportError:
            # Fallback to basic display if CLIHandler not available
            return self._basic_interactive_select(results, artist, title)
        except (KeyboardInterrupt, EOFError):
            raise UserCancelledError("Selection cancelled by user (Ctrl+C)")
        except (AttributeError, TypeError):
            # Fallback if results aren't proper Release objects (e.g., in tests)
            return self._basic_interactive_select(results, artist, title)

    def _basic_interactive_select(self, results: list, artist: str, title: str) -> object:
        """
        Basic fallback for interactive selection without rich formatting.

        Args:
            results: List of Release objects from flacfetch
            artist: The artist name being searched
            title: The track title being searched

        Returns:
            The selected Release object
            
        Raises:
            UserCancelledError: If user cancels selection
        """
        # Use flacfetch's shared display function
        from flacfetch import print_releases
        print_releases(results, target_artist=artist, use_colors=True)

        while True:
            try:
                choice = input("Enter your choice (1-{}, or 0 to cancel): ".format(len(results))).strip()

                if choice == "0":
                    self.logger.info("User cancelled selection")
                    raise UserCancelledError("Selection cancelled by user")

                choice_num = int(choice)
                if 1 <= choice_num <= len(results):
                    selected = results[choice_num - 1]
                    self.logger.info(f"User selected option {choice_num}")
                    return selected
                else:
                    print(f"Please enter a number between 0 and {len(results)}")

            except ValueError:
                print("Please enter a valid number")
            except KeyboardInterrupt:
                print("\nCancelled")
                raise UserCancelledError("Selection cancelled by user (Ctrl+C)")


# Alias for shorter name - used by backend and other consumers
FlacFetcher = FlacFetchAudioFetcher


class RemoteFlacFetchAudioFetcher(AudioFetcher):
    """
    Audio fetcher implementation using remote flacfetch HTTP API.
    
    This fetcher communicates with a dedicated flacfetch server that handles:
    - BitTorrent downloads from private trackers (RED, OPS)
    - YouTube downloads
    - File streaming back to the client
    
    Used when FLACFETCH_API_URL and FLACFETCH_API_KEY environment variables are set.
    """
    
    def __init__(
        self,
        api_url: str,
        api_key: str,
        logger: Optional[logging.Logger] = None,
        timeout: int = 60,
        download_timeout: int = 600,
    ):
        """
        Initialize the remote FlacFetch audio fetcher.
        
        Args:
            api_url: Base URL of flacfetch API server (e.g., http://10.0.0.5:8080)
            api_key: API key for authentication
            logger: Logger instance for output
            timeout: Request timeout in seconds for search/status calls
            download_timeout: Maximum wait time for downloads to complete
        """
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for remote flacfetch. Install with: pip install httpx")
        
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.logger = logger or logging.getLogger(__name__)
        self.timeout = timeout
        self.download_timeout = download_timeout
        self._last_search_id: Optional[str] = None
        self._last_search_results: List[Dict[str, Any]] = []
        
        self.logger.info(f"[RemoteFlacFetcher] Initialized with API URL: {self.api_url}")
    
    def _headers(self) -> Dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
    
    def _check_health(self) -> bool:
        """Check if the remote flacfetch service is healthy."""
        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{self.api_url}/health",
                    headers=self._headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "unknown")
                    self.logger.debug(f"[RemoteFlacFetcher] Health check: {status}")
                    return status in ["healthy", "degraded"]
                return False
        except Exception as e:
            self.logger.warning(f"[RemoteFlacFetcher] Health check failed: {e}")
            return False
    
    def search(self, artist: str, title: str) -> List[AudioSearchResult]:
        """
        Search for audio matching the given artist and title via remote API.
        
        Args:
            artist: The artist name to search for
            title: The track title to search for
            
        Returns:
            List of AudioSearchResult objects
            
        Raises:
            NoResultsError: If no results are found
            AudioFetcherError: For other errors
        """
        self.logger.info(f"[RemoteFlacFetcher] Searching for: {artist} - {title}")
        
        try:
            with httpx.Client() as client:
                resp = client.post(
                    f"{self.api_url}/search",
                    headers=self._headers(),
                    json={"artist": artist, "title": title},
                    timeout=self.timeout,
                )
                
                if resp.status_code == 404:
                    raise NoResultsError(f"No results found for: {artist} - {title}")
                
                resp.raise_for_status()
                data = resp.json()
                
                self._last_search_id = data.get("search_id")
                self._last_search_results = data.get("results", [])
                
                if not self._last_search_results:
                    raise NoResultsError(f"No results found for: {artist} - {title}")
                
                # Convert API results to AudioSearchResult objects
                search_results = []
                for i, result in enumerate(self._last_search_results):
                    search_results.append(
                        AudioSearchResult(
                            title=result.get("title", title),
                            artist=result.get("artist", artist),
                            url=result.get("download_url", "") or result.get("url", ""),
                            provider=result.get("provider", result.get("source_name", "Unknown")),
                            duration=result.get("duration_seconds", result.get("duration")),
                            quality=result.get("quality_str", result.get("quality")),
                            source_id=result.get("info_hash"),
                            index=i,
                            seeders=result.get("seeders"),
                            target_file=result.get("target_file"),
                            raw_result=result,  # Store the full API result
                        )
                    )
                
                self.logger.info(f"[RemoteFlacFetcher] Found {len(search_results)} results")
                return search_results
                
        except httpx.RequestError as e:
            raise AudioFetcherError(f"Search request failed: {e}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise NoResultsError(f"No results found for: {artist} - {title}") from e
            raise AudioFetcherError(f"Search failed: {e.response.status_code} - {e.response.text}") from e
    
    def download(
        self,
        result: AudioSearchResult,
        output_dir: str,
        output_filename: Optional[str] = None,
    ) -> AudioFetchResult:
        """
        Download audio from a search result via remote API.
        
        Args:
            result: The search result to download
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            
        Returns:
            AudioFetchResult with the downloaded file path
            
        Raises:
            DownloadError: If download fails
        """
        if not self._last_search_id:
            raise DownloadError("No search performed - call search() first")
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate filename if not provided
        if output_filename is None:
            output_filename = f"{result.artist} - {result.title}"
        
        self.logger.info(f"[RemoteFlacFetcher] Downloading: {result.artist} - {result.title} from {result.provider}")
        
        try:
            # Start the download
            with httpx.Client() as client:
                resp = client.post(
                    f"{self.api_url}/download",
                    headers=self._headers(),
                    json={
                        "search_id": self._last_search_id,
                        "result_index": result.index,
                        "output_filename": output_filename,
                        # Don't set upload_to_gcs - we want local download
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                download_id = data.get("download_id")
                
                if not download_id:
                    raise DownloadError("No download_id returned from API")
                
                self.logger.info(f"[RemoteFlacFetcher] Download started: {download_id}")
            
            # Wait for download to complete
            filepath = self._wait_and_stream_download(
                download_id=download_id,
                output_dir=output_dir,
                output_filename=output_filename,
            )
            
            self.logger.info(f"[RemoteFlacFetcher] Downloaded to: {filepath}")
            
            return AudioFetchResult(
                filepath=filepath,
                artist=result.artist,
                title=result.title,
                provider=result.provider,
                duration=result.duration,
                quality=result.quality,
            )
            
        except httpx.RequestError as e:
            raise DownloadError(f"Download request failed: {e}") from e
        except httpx.HTTPStatusError as e:
            raise DownloadError(f"Download failed: {e.response.status_code} - {e.response.text}") from e
    
    def _wait_and_stream_download(
        self,
        download_id: str,
        output_dir: str,
        output_filename: str,
        poll_interval: float = 2.0,
    ) -> str:
        """
        Wait for a remote download to complete, then stream the file locally.
        
        Args:
            download_id: Download ID from /download endpoint
            output_dir: Local directory to save file
            output_filename: Local filename (without extension)
            poll_interval: Seconds between status checks
            
        Returns:
            Path to the downloaded local file
            
        Raises:
            DownloadError: On download failure or timeout
            UserCancelledError: If user presses Ctrl+C
        """
        global _interrupt_requested
        _interrupt_requested = False
        
        # Set up signal handler for Ctrl+C
        original_handler = signal.getsignal(signal.SIGINT)
        
        def interrupt_handler(signum, frame):
            global _interrupt_requested
            _interrupt_requested = True
            print("\nCancelling download... please wait", file=sys.stderr)
        
        signal.signal(signal.SIGINT, interrupt_handler)
        
        try:
            elapsed = 0.0
            last_progress = -1
            
            while elapsed < self.download_timeout:
                # Check for interrupt
                if _interrupt_requested:
                    raise UserCancelledError("Download cancelled by user (Ctrl+C)")
                
                # Check status
                with httpx.Client() as client:
                    resp = client.get(
                        f"{self.api_url}/download/{download_id}/status",
                        headers=self._headers(),
                        timeout=10,
                    )
                    resp.raise_for_status()
                    status = resp.json()
                
                download_status = status.get("status")
                progress = status.get("progress", 0)
                speed = status.get("download_speed_kbps", 0)
                
                # Log progress updates
                if int(progress) != last_progress:
                    if download_status == "downloading":
                        self.logger.info(f"[RemoteFlacFetcher] Progress: {progress:.1f}% ({speed:.1f} KB/s)")
                    elif download_status in ["uploading", "processing"]:
                        self.logger.info(f"[RemoteFlacFetcher] {download_status.capitalize()}...")
                    last_progress = int(progress)
                
                if download_status in ["complete", "seeding"]:
                    # Download complete - now stream the file locally
                    self.logger.info(f"[RemoteFlacFetcher] Remote download complete, streaming to local...")
                    return self._stream_file_locally(download_id, output_dir, output_filename)
                
                elif download_status == "failed":
                    error = status.get("error", "Unknown error")
                    raise DownloadError(f"Remote download failed: {error}")
                
                elif download_status == "cancelled":
                    raise DownloadError("Download was cancelled on server")
                
                time.sleep(poll_interval)
                elapsed += poll_interval
            
            raise DownloadError(f"Download timed out after {self.download_timeout}s")
            
        finally:
            # Restore original signal handler
            signal.signal(signal.SIGINT, original_handler)
            _interrupt_requested = False
    
    def _stream_file_locally(
        self,
        download_id: str,
        output_dir: str,
        output_filename: str,
    ) -> str:
        """
        Stream a completed download from the remote server to local disk.
        
        Args:
            download_id: Download ID
            output_dir: Local directory to save file
            output_filename: Local filename (without extension)
            
        Returns:
            Path to the downloaded local file
            
        Raises:
            DownloadError: On streaming failure
        """
        try:
            # Stream the file from the remote server
            with httpx.Client() as client:
                with client.stream(
                    "GET",
                    f"{self.api_url}/download/{download_id}/file",
                    headers=self._headers(),
                    timeout=300,  # 5 minute timeout for file streaming
                ) as resp:
                    resp.raise_for_status()
                    
                    # Get content-disposition header for filename/extension
                    content_disp = resp.headers.get("content-disposition", "")
                    
                    # Try to extract extension from the server's filename
                    extension = ".flac"  # Default
                    if "filename=" in content_disp:
                        import re
                        match = re.search(r'filename="?([^";\s]+)"?', content_disp)
                        if match:
                            server_filename = match.group(1)
                            _, ext = os.path.splitext(server_filename)
                            if ext:
                                extension = ext
                    
                    # Also try content-type
                    content_type = resp.headers.get("content-type", "")
                    if "audio/mpeg" in content_type or "audio/mp3" in content_type:
                        extension = ".mp3"
                    elif "audio/wav" in content_type:
                        extension = ".wav"
                    elif "audio/x-flac" in content_type or "audio/flac" in content_type:
                        extension = ".flac"
                    elif "audio/mp4" in content_type or "audio/m4a" in content_type:
                        extension = ".m4a"
                    
                    # Build local filepath
                    local_filepath = os.path.join(output_dir, f"{output_filename}{extension}")
                    
                    # Stream to local file
                    total_bytes = 0
                    with open(local_filepath, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                            total_bytes += len(chunk)
                    
                    self.logger.info(f"[RemoteFlacFetcher] Streamed {total_bytes / 1024 / 1024:.1f} MB to {local_filepath}")
                    return local_filepath
                    
        except httpx.RequestError as e:
            raise DownloadError(f"Failed to stream file: {e}") from e
        except httpx.HTTPStatusError as e:
            raise DownloadError(f"Failed to stream file: {e.response.status_code}") from e
    
    def select_best(self, results: List[AudioSearchResult]) -> int:
        """
        Select the best result from a list of search results.
        
        For remote fetcher, we use simple heuristics since we don't have
        access to flacfetch's internal ranking. Prefers:
        1. Lossless sources (FLAC) over lossy
        2. Higher seeders for torrents
        3. First result otherwise (API typically returns sorted by quality)
        
        Args:
            results: List of AudioSearchResult objects from search()
            
        Returns:
            Index of the best result in the list
        """
        if not results:
            return 0
        
        # Score each result
        best_index = 0
        best_score = -1
        
        for i, result in enumerate(results):
            score = 0
            
            # Prefer lossless
            quality = (result.quality or "").lower()
            if "flac" in quality or "lossless" in quality:
                score += 1000
            elif "320" in quality:
                score += 500
            elif "256" in quality or "192" in quality:
                score += 200
            
            # Prefer higher seeders (for torrents)
            if result.seeders:
                score += min(result.seeders, 100)  # Cap at 100 points
            
            # Prefer non-YouTube sources (typically higher quality)
            provider = (result.provider or "").lower()
            if "youtube" not in provider:
                score += 50
            
            if score > best_score:
                best_score = score
                best_index = i
        
        return best_index
    
    def search_and_download(
        self,
        artist: str,
        title: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        auto_select: bool = False,
    ) -> AudioFetchResult:
        """
        Search for audio and download it in one operation via remote API.
        
        Args:
            artist: The artist name to search for
            title: The track title to search for
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            auto_select: If True, automatically select the best result
            
        Returns:
            AudioFetchResult with the downloaded file path
            
        Raises:
            NoResultsError: If no results are found
            DownloadError: If download fails
            UserCancelledError: If user cancels
        """
        # Search
        results = self.search(artist, title)
        
        if auto_select:
            # Auto mode: select best result
            best_index = self.select_best(results)
            selected = results[best_index]
            self.logger.info(f"[RemoteFlacFetcher] Auto-selected: {selected.title} from {selected.provider}")
        else:
            # Interactive mode: present options to user
            selected = self._interactive_select(results, artist, title)
        
        # Download
        return self.download(selected, output_dir, output_filename)
    
    def _convert_api_result_for_release(self, api_result: dict) -> dict:
        """
        Convert API SearchResultItem format to format expected by Release.from_dict().
        
        The flacfetch API returns:
        - provider: source name (RED, OPS, YouTube)
        - quality: display string (e.g., "FLAC 16bit CD")
        - quality_data: structured dict with format, bit_depth, media, etc.
        
        But Release.from_dict() expects:
        - source_name: provider name
        - quality: dict with format, bit_depth, media, etc.
        
        This mirrors the convert_api_result_to_display() function in flacfetch-remote CLI.
        """
        result = dict(api_result)  # Copy to avoid modifying original
        
        # Map provider to source_name
        result["source_name"] = api_result.get("provider", "Unknown")
        
        # Store original quality string as quality_str (used by display functions)
        result["quality_str"] = api_result.get("quality", "")
        
        # Map quality_data to quality (Release.from_dict expects quality to be a dict)
        quality_data = api_result.get("quality_data")
        if quality_data and isinstance(quality_data, dict):
            result["quality"] = quality_data
        else:
            # Fallback: parse quality string to determine format
            quality_str = api_result.get("quality", "").upper()
            format_name = "OTHER"
            media_name = "OTHER"
            
            if "FLAC" in quality_str:
                format_name = "FLAC"
            elif "MP3" in quality_str:
                format_name = "MP3"
            elif "WAV" in quality_str:
                format_name = "WAV"
            
            if "CD" in quality_str:
                media_name = "CD"
            elif "WEB" in quality_str:
                media_name = "WEB"
            elif "VINYL" in quality_str:
                media_name = "VINYL"
            
            result["quality"] = {"format": format_name, "media": media_name}
        
        # Copy is_lossless if available
        if "is_lossless" in api_result:
            result["is_lossless"] = api_result["is_lossless"]
        
        return result
    
    def _interactive_select(
        self,
        results: List[AudioSearchResult],
        artist: str,
        title: str,
    ) -> AudioSearchResult:
        """
        Present search results to the user for interactive selection.
        
        Uses flacfetch's built-in display functions if available, otherwise
        falls back to basic text display.
        
        Args:
            results: List of AudioSearchResult objects
            artist: The artist name being searched
            title: The track title being searched
            
        Returns:
            The selected AudioSearchResult
            
        Raises:
            UserCancelledError: If user cancels selection
        """
        # Try to use flacfetch's display functions with raw API results
        try:
            # Convert raw_result dicts back to Release objects for display
            from flacfetch.core.models import Release
            
            releases = []
            for r in results:
                if r.raw_result and isinstance(r.raw_result, dict):
                    # Convert API format to Release.from_dict() format
                    converted = self._convert_api_result_for_release(r.raw_result)
                    release = Release.from_dict(converted)
                    releases.append(release)
                elif r.raw_result and hasattr(r.raw_result, 'title'):
                    # It's already a Release object
                    releases.append(r.raw_result)
            
            if releases:
                from flacfetch.interface.cli import CLIHandler
                handler = CLIHandler(target_artist=artist)
                selected_release = handler.select_release(releases)
                
                if selected_release is None:
                    raise UserCancelledError("Selection cancelled by user")
                
                # Find the matching AudioSearchResult by index
                # CLIHandler returns the release at the selected index
                for i, release in enumerate(releases):
                    if release == selected_release:
                        return results[i]
                
                # Fallback: try matching by download_url
                for r in results:
                    if r.raw_result == selected_release or (
                        isinstance(r.raw_result, dict) and 
                        r.raw_result.get("download_url") == getattr(selected_release, "download_url", None)
                    ):
                        return r
                
        except (ImportError, AttributeError, TypeError) as e:
            self.logger.debug(f"[RemoteFlacFetcher] Falling back to basic display: {e}")
        
        # Fallback to basic display
        return self._basic_interactive_select(results, artist, title)
    
    def _basic_interactive_select(
        self,
        results: List[AudioSearchResult],
        artist: str,
        title: str,
    ) -> AudioSearchResult:
        """
        Basic fallback for interactive selection without rich formatting.
        
        Args:
            results: List of AudioSearchResult objects
            artist: The artist name being searched
            title: The track title being searched
            
        Returns:
            The selected AudioSearchResult
            
        Raises:
            UserCancelledError: If user cancels selection
        """
        print(f"\nFound {len(results)} releases:\n")
        
        for i, result in enumerate(results, 1):
            # Try to get lossless info from raw_result (API response)
            is_lossless = False
            if result.raw_result and isinstance(result.raw_result, dict):
                is_lossless = result.raw_result.get("is_lossless", False)
            elif result.quality:
                is_lossless = "flac" in result.quality.lower() or "lossless" in result.quality.lower()
            
            format_indicator = "[LOSSLESS]" if is_lossless else "[lossy]"
            quality = f"({result.quality})" if result.quality else ""
            provider = f"[{result.provider}]" if result.provider else ""
            seeders = f"Seeders: {result.seeders}" if result.seeders else ""
            duration = ""
            if result.duration:
                mins, secs = divmod(result.duration, 60)
                duration = f"[{int(mins)}:{int(secs):02d}]"
            
            print(f"{i}. {format_indicator} {provider} {result.artist}: {result.title} {quality} {duration} {seeders}")
        
        print()
        
        while True:
            try:
                choice = input(f"Select a release (1-{len(results)}, 0 to cancel): ").strip()
                
                if choice == "0":
                    raise UserCancelledError("Selection cancelled by user")
                
                choice_num = int(choice)
                if 1 <= choice_num <= len(results):
                    selected = results[choice_num - 1]
                    self.logger.info(f"[RemoteFlacFetcher] User selected option {choice_num}")
                    return selected
                else:
                    print(f"Please enter a number between 0 and {len(results)}")
                    
            except ValueError:
                print("Please enter a valid number")
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled")
                raise UserCancelledError("Selection cancelled by user (Ctrl+C)")

    def download_from_url(
        self,
        url: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> AudioFetchResult:
        """
        Download audio directly from a URL (e.g., YouTube URL).

        For YouTube URLs, this uses local flacfetch since YouTube downloads
        don't require the remote flacfetch infrastructure (no torrents).

        Args:
            url: The URL to download from (e.g., YouTube video URL)
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            artist: Optional artist name for metadata
            title: Optional title for metadata

        Returns:
            AudioFetchResult with the downloaded file path

        Raises:
            DownloadError: If download fails
        """
        import re

        self.logger.info(f"[RemoteFlacFetcher] Downloading from URL: {url}")
        self.logger.info("[RemoteFlacFetcher] Using local flacfetch for YouTube download (no remote API needed)")

        try:
            # Use local flacfetch for YouTube downloads - no need for remote API
            # This avoids needing yt-dlp directly in karaoke-gen
            from flacfetch.core.manager import FetchManager
            from flacfetch.providers.youtube import YoutubeProvider
            from flacfetch.downloaders.youtube import YoutubeDownloader

            # Create a minimal local manager for YouTube downloads
            manager = FetchManager()
            manager.add_provider(YoutubeProvider())
            manager.register_downloader("YouTube", YoutubeDownloader())

            # Ensure output directory exists
            os.makedirs(output_dir, exist_ok=True)

            # Extract video ID from URL
            source_id = None
            youtube_patterns = [
                r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
                r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
                r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
            ]
            for pattern in youtube_patterns:
                match = re.search(pattern, url)
                if match:
                    source_id = match.group(1)
                    break

            if not source_id:
                source_id = url

            # Generate filename if not provided
            if output_filename is None:
                if artist and title:
                    output_filename = f"{artist} - {title}"
                else:
                    output_filename = source_id

            # Use flacfetch's download_by_id for direct URL download
            filepath = manager.download_by_id(
                source_name="YouTube",
                source_id=source_id,
                output_path=output_dir,
                output_filename=output_filename,
                download_url=url,
            )

            if not filepath:
                raise DownloadError(f"Download returned no file path for URL: {url}")

            self.logger.info(f"[RemoteFlacFetcher] Downloaded to: {filepath}")

            return AudioFetchResult(
                filepath=filepath,
                artist=artist or "",
                title=title or "",
                provider="YouTube",
                duration=None,
                quality=None,
            )

        except ImportError as e:
            raise DownloadError(
                f"flacfetch is required for URL downloads but import failed: {e}"
            ) from e
        except Exception as e:
            raise DownloadError(f"Failed to download from URL {url}: {e}") from e


# Alias for shorter name
RemoteFlacFetcher = RemoteFlacFetchAudioFetcher


def create_audio_fetcher(
    logger: Optional[logging.Logger] = None,
    red_api_key: Optional[str] = None,
    red_api_url: Optional[str] = None,
    ops_api_key: Optional[str] = None,
    ops_api_url: Optional[str] = None,
    flacfetch_api_url: Optional[str] = None,
    flacfetch_api_key: Optional[str] = None,
) -> AudioFetcher:
    """
    Factory function to create an appropriate AudioFetcher instance.
    
    If FLACFETCH_API_URL and FLACFETCH_API_KEY environment variables are set
    (or passed as arguments), returns a RemoteFlacFetchAudioFetcher that uses
    the remote flacfetch HTTP API server.
    
    Otherwise, returns a local FlacFetchAudioFetcher that uses the flacfetch
    library directly.

    Args:
        logger: Logger instance for output
        red_api_key: API key for RED tracker (optional, for local mode)
        red_api_url: Base URL for RED tracker API (optional, for local mode)
        ops_api_key: API key for OPS tracker (optional, for local mode)
        ops_api_url: Base URL for OPS tracker API (optional, for local mode)
        flacfetch_api_url: URL of remote flacfetch API server (optional)
        flacfetch_api_key: API key for remote flacfetch server (optional)

    Returns:
        An AudioFetcher instance (remote or local depending on configuration)
    """
    # Check for remote flacfetch API configuration
    api_url = flacfetch_api_url or os.environ.get("FLACFETCH_API_URL")
    api_key = flacfetch_api_key or os.environ.get("FLACFETCH_API_KEY")
    
    if api_url and api_key:
        # Use remote flacfetch API
        if logger:
            logger.info(f"Using remote flacfetch API at: {api_url}")
        return RemoteFlacFetchAudioFetcher(
            api_url=api_url,
            api_key=api_key,
            logger=logger,
        )
    elif api_url and not api_key:
        # Half-configured remote flacfetch is almost always a deploy/secret typo.
        # Silently using local mode masks the misconfiguration — fail loudly so
        # the operator fixes it. (Fallback audit 2026-06-09, Theme 1.)
        raise AudioFetcherError(
            "FLACFETCH_API_URL is set but FLACFETCH_API_KEY is not. Remote flacfetch "
            "is half-configured — set both (or neither, for local mode). Refusing to "
            "silently fall back to local mode and mask the misconfiguration."
        )
    elif api_key and not api_url:
        raise AudioFetcherError(
            "FLACFETCH_API_KEY is set but FLACFETCH_API_URL is not. Remote flacfetch "
            "is half-configured — set both (or neither, for local mode). Refusing to "
            "silently fall back to local mode and mask the misconfiguration."
        )
    
    # Use local flacfetch library
    return FlacFetchAudioFetcher(
        logger=logger,
        red_api_key=red_api_key,
        red_api_url=red_api_url,
        ops_api_key=ops_api_key,
        ops_api_url=ops_api_url,
    )
