"""
Audio search service for finding and downloading audio files.

This service integrates with karaoke_gen.audio_fetcher (which wraps flacfetch)
to search for audio from various sources (YouTube, music trackers, etc.)
and download the selected audio file.

This is a thin wrapper that adds backend-specific functionality:
- Caching raw results for API-based selection flow
- Firestore-compatible serialization
- Singleton pattern for service lifecycle
- Remote flacfetch service integration for torrent downloads

When FLACFETCH_API_URL is configured, this service uses the remote flacfetch
HTTP API for search and download operations. This is required for torrent
downloads since Cloud Run doesn't support BitTorrent peer connections.
"""
import asyncio
import logging
import os
from typing import List, Optional

import nest_asyncio

# Import shared classes from karaoke_gen.audio_fetcher - single source of truth
# Note: Import directly from audio_fetcher module, not karaoke_gen.__init__
# to avoid pulling in heavier dependencies like lyrics_transcriber
from karaoke_gen.audio_fetcher import (
    FlacFetcher,
    AudioSearchResult,
    AudioFetchResult,
    AudioFetcherError,
    NoResultsError,
    DownloadError,
)

from .flacfetch_client import get_flacfetch_client, FlacfetchServiceError

logger = logging.getLogger(__name__)


# Re-export exception under backend's naming convention for compatibility
AudioSearchError = AudioFetcherError


# Also alias AudioFetchResult as AudioDownloadResult for backwards compatibility
AudioDownloadResult = AudioFetchResult


class AudioSearchService:
    """
    Service for searching and downloading audio files.
    
    This is a thin wrapper around karaoke_gen.audio_fetcher.FlacFetcher that adds
    backend-specific functionality:
    - Caches raw results for API-based selection (client picks by index)
    - Provides Firestore-compatible serialization
    - Manages service lifecycle as singleton
    - Remote flacfetch service integration for torrent downloads
    
    When FLACFETCH_API_URL is configured, this service uses the remote flacfetch
    HTTP API for search and download operations. This enables torrent downloads
    which are not possible in Cloud Run due to network restrictions.
    
    The actual flacfetch integration is in karaoke_gen.audio_fetcher.FlacFetcher,
    which is shared between local CLI and cloud backend.
    """
    
    # Sentinel value to indicate "use environment variable"
    _USE_ENV = object()
    
    def __init__(
        self,
        red_api_key: Optional[str] = _USE_ENV,
        red_api_url: Optional[str] = _USE_ENV,
        ops_api_key: Optional[str] = _USE_ENV,
        ops_api_url: Optional[str] = _USE_ENV,
    ):
        """
        Initialize the audio search service.
        
        Args:
            red_api_key: API key for RED tracker (optional, uses env if not provided)
            red_api_url: Base URL for RED tracker API (optional, uses env if not provided)
            ops_api_key: API key for OPS tracker (optional, uses env if not provided)
            ops_api_url: Base URL for OPS tracker API (optional, uses env if not provided)
        """
        # Use environment variables if not explicitly provided
        if red_api_key is self._USE_ENV:
            red_api_key = os.environ.get("RED_API_KEY")
        if red_api_url is self._USE_ENV:
            red_api_url = os.environ.get("RED_API_URL")
            
        if ops_api_key is self._USE_ENV:
            ops_api_key = os.environ.get("OPS_API_KEY")
        if ops_api_url is self._USE_ENV:
            ops_api_url = os.environ.get("OPS_API_URL")
        
        # Check for remote flacfetch client
        self._remote_client = get_flacfetch_client()
        
        # Log which mode we're using
        if self._remote_client:
            logger.info("AudioSearchService using REMOTE flacfetch service (torrent downloads enabled)")
        else:
            logger.info("AudioSearchService using LOCAL flacfetch (YouTube only in Cloud Run)")
        
        # Delegate to shared FlacFetcher implementation (for local mode or fallback)
        self._fetcher = FlacFetcher(
            red_api_key=red_api_key,
            red_api_url=red_api_url,
            ops_api_key=ops_api_key,
            ops_api_url=ops_api_url,
        )
        
        # Cache search results for API-based selection flow
        # Key: index, Value: AudioSearchResult (with raw_result)
        self._cached_results: List[AudioSearchResult] = []
        
        # Cache for remote search results (search_id -> results)
        self._remote_search_id: Optional[str] = None
    
    def search(self, artist: str, title: str) -> List[AudioSearchResult]:
        """
        Search for audio matching the given artist and title.
        
        Results are cached internally for later download via download().
        
        If a remote flacfetch service is configured (FLACFETCH_API_URL), uses that
        for better torrent download support. Otherwise falls back to local flacfetch.
        
        Args:
            artist: The artist name to search for
            title: The track title to search for
            
        Returns:
            List of AudioSearchResult objects
            
        Raises:
            NoResultsError: If no results are found
            AudioSearchError: For other errors (e.g., flacfetch not installed)
        """
        try:
            # Try remote flacfetch service first if configured
            if self._remote_client:
                return self._search_remote(artist, title)
            
            # Fallback to local flacfetch
            results = self._fetcher.search(artist, title)
            
            # Cache results for later download
            self._cached_results = results
            self._remote_search_id = None
            
            logger.info(f"Found {len(results)} results for: {artist} - {title}")
            return results
            
        except (NoResultsError, AudioFetcherError):
            # Re-raise these as-is
            raise
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise AudioSearchError(f"Search failed: {e}") from e
    
    def _search_remote(self, artist: str, title: str) -> List[AudioSearchResult]:
        """
        Search using the remote flacfetch service.
        
        Runs async code in a sync context for compatibility with existing API.
        """
        try:
            # Enable nested event loops (needed when called from FastAPI async context)
            # Apply to the current loop if one exists, otherwise it's a no-op
            try:
                loop = asyncio.get_running_loop()
                nest_asyncio.apply(loop)
            except RuntimeError:
                pass  # No running loop, nest_asyncio not needed
            
            # Run async search in sync context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(
                    self._remote_client.search(artist, title)
                )
            finally:
                loop.close()
            
            # Check for empty results
            if not response.get("results"):
                raise NoResultsError(f"No results found for: {artist} - {title}")
            
            # Store search_id for download
            self._remote_search_id = response.get("search_id")
            
            # Convert remote results to AudioSearchResult objects
            results = []
            for item in response.get("results", []):
                result = AudioSearchResult(
                    title=item.get("title", "Unknown"),
                    artist=item.get("artist", "Unknown"),
                    url=item.get("download_url", ""),  # May be empty for remote
                    provider=item.get("provider", "Unknown"),
                    duration=item.get("duration_seconds"),
                    quality=item.get("quality", ""),
                    # source_id: use explicit source_id field first (YouTube video ID, Spotify track ID),
                    # fall back to info_hash for torrent sources (RED/OPS)
                    source_id=item.get("source_id") or item.get("info_hash"),
                    index=item.get("index", 0),
                    seeders=item.get("seeders"),
                    target_file=item.get("target_file"),
                    # Store full remote data in raw_result for rich display
                    raw_result=item,
                )
                results.append(result)
            
            # Cache results
            self._cached_results = results
            
            logger.info(f"Found {len(results)} results from remote flacfetch for: {artist} - {title}")
            return results
            
        except FlacfetchServiceError as e:
            logger.warning(f"Remote flacfetch search failed, falling back to local: {e}")
            # Fallback to local search
            self._remote_search_id = None
            results = self._fetcher.search(artist, title)
            self._cached_results = results
            return results
    
    def select_best(self, results: List[AudioSearchResult]) -> int:
        """
        Select the best result from a list of search results.
        
        Uses flacfetch's built-in quality ranking to select the best source.
        
        Args:
            results: List of search results
            
        Returns:
            Index of the best result
        """
        return self._fetcher.select_best(results)
    
    def download(
        self,
        result_index: int,
        output_dir: str,
        output_filename: Optional[str] = None,
        gcs_path: Optional[str] = None,
        remote_search_id: Optional[str] = None,
    ) -> AudioDownloadResult:
        """
        Download audio from a cached search result.

        This method uses the cached results from the last search() call.
        The API flow is:
        1. Client calls search() -> gets list of results
        2. Client picks an index
        3. Client calls download(index) -> gets downloaded file

        If a remote flacfetch service is configured and the search was performed
        remotely (for torrent sources), uses the remote service for download.

        Args:
            result_index: Index of the result to download (from search results)
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            gcs_path: Optional GCS path for remote uploads (e.g., "uploads/job123/audio/")
            remote_search_id: Optional search_id for remote downloads. If provided,
                uses this instead of the cached _remote_search_id. This is important
                for concurrent requests where the service-level cache may be stale.

        Returns:
            AudioDownloadResult with the downloaded file path

        Raises:
            DownloadError: If download fails or no cached result for index
        """
        # Use provided search_id or fall back to service-level cache
        effective_search_id = remote_search_id or self._remote_search_id

        # Check if we have local cached results
        has_local_cache = result_index >= 0 and result_index < len(self._cached_results)

        # If no local cache but we have remote search_id, use remote download
        # This handles multi-instance deployments where the cache doesn't persist
        if not has_local_cache:
            if effective_search_id and self._remote_client:
                logger.info(f"No local cache, using remote download with search_id={effective_search_id}, index={result_index}")
                return self._download_remote(result_index, output_dir, output_filename, gcs_path, effective_search_id)
            else:
                # Provide clear error message based on whether cache is empty or index out of bounds
                if len(self._cached_results) == 0:
                    raise DownloadError(
                        f"No cached result for index {result_index}. "
                        "No cached results available. Run search() first."
                    )
                raise DownloadError(
                    f"No cached result for index {result_index}. "
                    f"Available indices: 0-{len(self._cached_results) - 1}. "
                    "Run search() first."
                )

        result = self._cached_results[result_index]

        logger.info(f"Downloading: {result.artist} - {result.title} from {result.provider}")

        # Check if this was a remote search (torrent sources need remote download)
        if effective_search_id and self._remote_client:
            # Check if this is a torrent source that needs remote handling
            is_torrent = result.provider in ["RED", "OPS"]

            if is_torrent:
                return self._download_remote(result_index, output_dir, output_filename, gcs_path, effective_search_id)
        
        # Delegate to shared FlacFetcher (local download)
        fetch_result = self._fetcher.download(result, output_dir, output_filename)
        
        logger.info(f"Downloaded to: {fetch_result.filepath}")
        
        return fetch_result
    
    def download_by_id(
        self,
        source_name: str,
        source_id: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        target_file: Optional[str] = None,
        download_url: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> AudioDownloadResult:
        """
        Download audio directly by source ID (no prior search required).

        This is the preferred method when you have stored the source_id from a
        previous search and want to download later without re-searching. This
        avoids unnecessary API calls to private trackers.

        Args:
            source_name: Provider name (RED, OPS, YouTube, Spotify)
            source_id: Source-specific ID (torrent ID, video ID, track ID)
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            target_file: For torrents, specific file to extract from the torrent
            download_url: For YouTube/Spotify, direct URL (optional)
            gcs_path: Optional GCS path for remote uploads

        Returns:
            AudioDownloadResult with the downloaded file path

        Raises:
            DownloadError: If download fails
        """
        logger.info(f"Download by ID: {source_name} ID={source_id}")

        # For torrent sources, must use remote client
        if source_name in ["RED", "OPS"]:
            if not self._remote_client:
                raise DownloadError(
                    f"Cannot download from {source_name} without remote flacfetch service"
                )
            return self._download_by_id_remote(
                source_name=source_name,
                source_id=source_id,
                output_dir=output_dir,
                output_filename=output_filename,
                target_file=target_file,
                download_url=download_url,
                gcs_path=gcs_path,
            )

        # For YouTube/Spotify, can use local flacfetch via FetchManager.download_by_id
        # But we currently only have remote support, so use remote if available
        if self._remote_client:
            return self._download_by_id_remote(
                source_name=source_name,
                source_id=source_id,
                output_dir=output_dir,
                output_filename=output_filename,
                target_file=target_file,
                download_url=download_url,
                gcs_path=gcs_path,
            )

        # Local download for YouTube/Spotify (fallback)
        # Use the local fetcher's download_by_id if available
        raise DownloadError(
            f"Local download_by_id not yet implemented for {source_name}. "
            "Configure FLACFETCH_API_URL for remote downloads."
        )

    def _download_by_id_remote(
        self,
        source_name: str,
        source_id: str,
        output_dir: str,  # Not used for remote downloads (files go to GCS or remote server)
        output_filename: Optional[str] = None,
        target_file: Optional[str] = None,
        download_url: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> AudioDownloadResult:
        """
        Download by ID using the remote flacfetch service.

        Note: output_dir is accepted for API compatibility but ignored for remote
        downloads. Remote downloads either go to GCS (if gcs_path is set) or to
        the remote server's download directory.
        """
        # output_dir is intentionally unused - remote downloads don't use local paths
        _ = output_dir
        try:
            # Enable nested event loops
            try:
                running_loop = asyncio.get_running_loop()
                nest_asyncio.apply(running_loop)
            except RuntimeError:
                pass

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Start download
                download_id = loop.run_until_complete(
                    self._remote_client.download_by_id(
                        source_name=source_name,
                        source_id=source_id,
                        output_filename=output_filename,
                        target_file=target_file,
                        download_url=download_url,
                        gcs_path=gcs_path,
                    )
                )

                logger.info(f"Remote download by ID started: {download_id}")

                # Wait for completion
                def log_progress(status):
                    progress = status.get("progress", 0)
                    speed = status.get("download_speed_kbps", 0)
                    logger.debug(f"Download progress: {progress:.1f}% ({speed:.1f} KB/s)")

                final_status = loop.run_until_complete(
                    self._remote_client.wait_for_download(
                        download_id,
                        timeout=600,
                        progress_callback=log_progress,
                    )
                )
            finally:
                loop.close()

            # Determine file path
            filepath = final_status.get("gcs_path") or final_status.get("output_path")

            if not filepath:
                raise DownloadError("Remote download completed but no file path returned")

            logger.info(f"Remote download by ID complete: {filepath}")

            return AudioDownloadResult(
                filepath=filepath,
                artist="",  # Not available without search
                title="",   # Not available without search
                provider=source_name,
                quality="",  # Not available without search
            )

        except FlacfetchServiceError as e:
            raise DownloadError(f"Remote download by ID failed: {e}") from e
        except Exception as e:
            logger.error(f"Remote download by ID error: {e}")
            raise DownloadError(f"Remote download by ID failed: {e}") from e

    def _download_remote(
        self,
        result_index: int,
        output_dir: str,
        output_filename: Optional[str] = None,
        gcs_path: Optional[str] = None,
        search_id: Optional[str] = None,
    ) -> AudioDownloadResult:
        """
        Download using the remote flacfetch service.

        The remote service downloads via torrent and optionally uploads to GCS.

        Args:
            result_index: Index of the result to download
            output_dir: Directory to save downloaded file
            output_filename: Optional filename
            gcs_path: Optional GCS path for remote uploads
            search_id: Remote search ID to use (overrides self._remote_search_id)
        """
        effective_search_id = search_id or self._remote_search_id
        if not effective_search_id:
            raise DownloadError("No remote search ID - run search() first")

        # Try to get local result for metadata, but it's optional
        # The remote service maintains its own cache by search_id
        result = None
        if result_index >= 0 and result_index < len(self._cached_results):
            result = self._cached_results[result_index]

        try:
            # Enable nested event loops (needed when called from FastAPI async context)
            try:
                running_loop = asyncio.get_running_loop()
                nest_asyncio.apply(running_loop)
            except RuntimeError:
                pass  # No running loop, nest_asyncio not needed
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Start download
                download_id = loop.run_until_complete(
                    self._remote_client.download(
                        search_id=effective_search_id,
                        result_index=result_index,
                        output_filename=output_filename,
                        gcs_path=gcs_path,
                    )
                )
                
                logger.info(f"Remote download started: {download_id}")
                
                # Wait for completion with progress logging
                def log_progress(status):
                    progress = status.get("progress", 0)
                    speed = status.get("download_speed_kbps", 0)
                    logger.debug(f"Download progress: {progress:.1f}% ({speed:.1f} KB/s)")
                
                final_status = loop.run_until_complete(
                    self._remote_client.wait_for_download(
                        download_id,
                        timeout=600,  # 10 minute timeout
                        progress_callback=log_progress,
                    )
                )
            finally:
                loop.close()
            
            # Determine file path
            filepath = final_status.get("gcs_path") or final_status.get("output_path")
            
            if not filepath:
                raise DownloadError("Remote download completed but no file path returned")
            
            logger.info(f"Remote download complete: {filepath}")

            # Use local result metadata if available, otherwise use empty strings
            # (the actual download is handled by the remote service using search_id)
            return AudioDownloadResult(
                filepath=filepath,
                artist=result.artist if result else "",
                title=result.title if result else "",
                provider=result.provider if result else "remote",
                quality=result.quality if result else "",
            )
            
        except FlacfetchServiceError as e:
            raise DownloadError(f"Remote download failed: {e}") from e
        except Exception as e:
            logger.error(f"Remote download error: {e}")
            raise DownloadError(f"Remote download failed: {e}") from e
    
    def search_and_download_auto(
        self,
        artist: str,
        title: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> AudioDownloadResult:
        """
        Search for audio and automatically download the best result.
        
        This is a convenience method that combines search(), select_best(),
        and download() for automated/non-interactive usage.
        
        Args:
            artist: The artist name to search for
            title: The track title to search for
            output_dir: Directory to save the downloaded file
            output_filename: Optional filename (without extension)
            gcs_path: Optional GCS path for remote uploads
            
        Returns:
            AudioDownloadResult with the downloaded file path
            
        Raises:
            NoResultsError: If no results are found
            DownloadError: If download fails
        """
        # Search
        results = self.search(artist, title)
        
        # Select best
        best_index = self.select_best(results)
        best_result = results[best_index]
        logger.info(
            f"Auto-selected result {best_index}: "
            f"{best_result.artist} - {best_result.title} ({best_result.provider})"
        )
        
        # Download
        return self.download(best_index, output_dir, output_filename, gcs_path)
    
    async def search_async(self, artist: str, title: str) -> List[AudioSearchResult]:
        """
        Async version of search for use in async routes.
        
        Note: Currently wraps sync search in executor. Future optimization
        could make this fully async.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.search, artist, title)
    
    async def download_async(
        self,
        result_index: int,
        output_dir: str,
        output_filename: Optional[str] = None,
        gcs_path: Optional[str] = None,
        remote_search_id: Optional[str] = None,
    ) -> AudioDownloadResult:
        """
        Async version of download for use in async routes.

        Note: Currently wraps sync download in executor. Future optimization
        could make this fully async.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.download(result_index, output_dir, output_filename, gcs_path, remote_search_id)
        )

    async def download_by_id_async(
        self,
        source_name: str,
        source_id: str,
        output_dir: str,
        output_filename: Optional[str] = None,
        target_file: Optional[str] = None,
        download_url: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> AudioDownloadResult:
        """
        Async version of download_by_id for use in async routes.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.download_by_id(
                source_name, source_id, output_dir, output_filename,
                target_file, download_url, gcs_path
            )
        )
    
    def is_remote_enabled(self) -> bool:
        """Check if remote flacfetch service is configured."""
        return self._remote_client is not None

    @property
    def last_remote_search_id(self) -> Optional[str]:
        """Get the search_id from the last remote search.

        This should be stored in job state_data after search and passed
        back to download() to ensure correct remote download handling.
        """
        return self._remote_search_id
    
    async def check_remote_health(self) -> Optional[dict]:
        """
        Check health of remote flacfetch service.
        
        Returns:
            Health status dict if remote service is configured and healthy,
            None if not configured or unhealthy.
        """
        if not self._remote_client:
            return None
        
        try:
            return await self._remote_client.health_check()
        except Exception as e:
            logger.warning(f"Remote flacfetch health check failed: {e}")
            return None


# Singleton instance
_audio_search_service: Optional[AudioSearchService] = None


def get_audio_search_service() -> AudioSearchService:
    """Get the singleton AudioSearchService instance."""
    global _audio_search_service
    if _audio_search_service is None:
        _audio_search_service = AudioSearchService()
    return _audio_search_service
