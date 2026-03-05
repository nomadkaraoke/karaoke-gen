"""
Client for the remote flacfetch HTTP API service.

This client communicates with a dedicated flacfetch VM that handles:
- BitTorrent downloads from private trackers (RED, OPS)
- YouTube downloads
- GCS uploads of downloaded files

The flacfetch service provides:
- Full peer connectivity for torrents (not possible in Cloud Run)
- Indefinite seeding of completed torrents
- Automatic disk cleanup

Usage:
    client = get_flacfetch_client()
    if client:
        # Search for audio
        search_result = await client.search("ABBA", "Waterloo")
        
        # Download the best result
        download_id = await client.download(
            search_id=search_result["search_id"],
            result_index=0,
            gcs_path="uploads/job123/audio/",
        )
        
        # Wait for completion
        result = await client.wait_for_download(download_id)
        print(f"Downloaded to: {result['gcs_path']}")
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)


class FlacfetchServiceError(Exception):
    """Error communicating with or returned from flacfetch service."""
    pass


def _format_httpx_error(e: Exception) -> str:
    """
    Format an httpx exception into a descriptive error string.

    httpx.RequestError subclasses (ConnectError, ReadError, etc.) often have
    empty str() representations, which produces useless messages like
    "Status request failed: ". This helper always returns something meaningful.
    """
    error_str = str(e)
    error_type = type(e).__name__

    if error_str:
        return f"{error_type}: {error_str}"

    # str(e) is empty - try to include request URL for context
    if isinstance(e, httpx.RequestError):
        try:
            request = e.request
            if request:
                return f"{error_type} for {request.method} {request.url}"
        except RuntimeError:
            pass  # .request not set
    return error_type


def should_retry_flacfetch_error(exception: Exception) -> bool:
    """
    Determine if a flacfetch exception should be retried.

    Retry on:
    - httpx.RequestError (network failures: timeout, connection refused, etc.)
    - httpx.HTTPStatusError with 5xx status (server errors)

    Don't retry on:
    - httpx.HTTPStatusError with 4xx status (client errors: bad request, auth, not found)
    """
    if isinstance(exception, httpx.RequestError):
        # Network-level failures - always retry
        return True

    if isinstance(exception, httpx.HTTPStatusError):
        # HTTP errors - only retry on 5xx (server errors)
        return exception.response.status_code >= 500

    # Don't retry on other exceptions (e.g., FlacfetchServiceError after wrapping)
    return False


async def with_retry(func, *args, **kwargs):
    """
    Execute an async function with retry logic using exponential backoff.

    This implementation uses manual retry logic instead of tenacity decorators
    for better testability and runtime configuration.
    """
    settings = get_settings()
    max_attempts = settings.flacfetch_retry_max_attempts
    min_wait = settings.flacfetch_retry_min_wait
    max_wait = settings.flacfetch_retry_max_wait

    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e

            # Check if we should retry this exception
            if not should_retry_flacfetch_error(e):
                # Don't retry - raise immediately
                raise

            # Check if we have more attempts left
            if attempt >= max_attempts:
                # No more attempts - raise the last exception
                logger.warning(
                    f"Flacfetch request failed after {max_attempts} attempts: "
                    f"{_format_httpx_error(e) if isinstance(e, httpx.RequestError) else e}"
                )
                raise

            # Calculate wait time with exponential backoff
            wait_time = min(min_wait * (2 ** (attempt - 1)), max_wait)

            logger.warning(
                f"Flacfetch request failed (attempt {attempt}/{max_attempts}): "
                f"{_format_httpx_error(e) if isinstance(e, httpx.RequestError) else e}. "
                f"Retrying in {wait_time:.1f}s..."
            )

            # Wait before next attempt
            await asyncio.sleep(wait_time)

    # Should never reach here, but just in case
    raise last_exception


class FlacfetchClient:
    """
    Client for remote flacfetch HTTP API.
    
    All endpoints require authentication via X-API-Key header.
    """
    
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 60,
    ):
        """
        Initialize flacfetch client.
        
        Args:
            base_url: Base URL of flacfetch service (e.g., http://10.0.0.5:8080)
            api_key: API key for authentication
            timeout: Default request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        logger.info(f"FlacfetchClient initialized with base_url={self.base_url}")
    
    def _headers(self) -> Dict[str, str]:
        """Get request headers with authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Check if flacfetch service is healthy.
        
        Returns:
            Health status dict with transmission, disk, and provider info
            
        Raises:
            FlacfetchServiceError: If service is unhealthy or unreachable
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/health",
                    headers=self._headers(),
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                
                if data.get("status") not in ["healthy", "degraded"]:
                    raise FlacfetchServiceError(f"Service unhealthy: {data}")
                
                return data
        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Cannot reach flacfetch service: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(f"Health check failed: {e.response.status_code}")
    
    async def search(
        self,
        artist: str,
        title: str,
    ) -> Dict[str, Any]:
        """
        Search for audio matching artist and title.
        
        Args:
            artist: Artist name
            title: Track title
            
        Returns:
            Search response dict with search_id and results list
            
        Raises:
            FlacfetchServiceError: On search failure
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/search",
                    headers=self._headers(),
                    json={"artist": artist, "title": title},
                    timeout=self.timeout,
                )
                
                if resp.status_code == 404:
                    # No results found - return empty results
                    return {
                        "search_id": None,
                        "artist": artist,
                        "title": title,
                        "results": [],
                        "results_count": 0,
                    }
                
                resp.raise_for_status()
                return resp.json()
                
        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Search request failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(f"Search failed: {e.response.status_code} - {e.response.text}")

    async def download(
        self,
        search_id: str,
        result_index: int,
        output_filename: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> str:
        """
        Start downloading an audio file.

        Retries automatically on network errors and server errors (5xx) using
        exponential backoff. Configured via FLACFETCH_RETRY_* environment variables.

        Args:
            search_id: Search ID from previous search
            result_index: Index of result to download
            output_filename: Optional custom filename (without extension)
            gcs_path: GCS path for upload (e.g., "uploads/job123/audio/")

        Returns:
            Download ID for tracking progress

        Raises:
            FlacfetchServiceError: On download start failure (after all retries exhausted)
        """
        try:
            return await with_retry(self._download_impl, search_id, result_index, output_filename, gcs_path)
        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Download request failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(f"Download start failed: {e.response.status_code} - {e.response.text}")

    async def _download_impl(
        self,
        search_id: str,
        result_index: int,
        output_filename: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> str:
        """Implementation of download without error wrapping - lets httpx exceptions bubble up."""
        async with httpx.AsyncClient() as client:
            payload = {
                "search_id": search_id,
                "result_index": result_index,
            }
            if output_filename:
                payload["output_filename"] = output_filename
            if gcs_path:
                payload["upload_to_gcs"] = True
                payload["gcs_path"] = gcs_path

            resp = await client.post(
                f"{self.base_url}/download",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()

            data = resp.json()
            return data["download_id"]

    async def download_by_id(
        self,
        source_name: str,
        source_id: str,
        output_filename: Optional[str] = None,
        target_file: Optional[str] = None,
        download_url: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> str:
        """
        Start downloading directly by source ID (no prior search required).

        This is useful when you have stored the source_id from a previous search
        and want to download later without re-searching.

        Retries automatically on network errors and server errors (5xx) using
        exponential backoff. Configured via FLACFETCH_RETRY_* environment variables.

        Args:
            source_name: Provider name (RED, OPS, YouTube, Spotify)
            source_id: Source-specific ID (torrent ID, video ID, track ID)
            output_filename: Optional custom filename (without extension)
            target_file: For torrents, specific file to extract
            download_url: For YouTube/Spotify, direct URL (optional)
            gcs_path: GCS path for upload (e.g., "uploads/job123/audio/")

        Returns:
            Download ID for tracking progress

        Raises:
            FlacfetchServiceError: On download start failure (after all retries exhausted)
        """
        try:
            return await with_retry(
                self._download_by_id_impl,
                source_name, source_id, output_filename, target_file, download_url, gcs_path
            )
        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Download by ID request failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(f"Download by ID failed: {e.response.status_code} - {e.response.text}")

    async def _download_by_id_impl(
        self,
        source_name: str,
        source_id: str,
        output_filename: Optional[str] = None,
        target_file: Optional[str] = None,
        download_url: Optional[str] = None,
        gcs_path: Optional[str] = None,
    ) -> str:
        """Implementation of download_by_id without error wrapping - lets httpx exceptions bubble up."""
        async with httpx.AsyncClient() as client:
            payload = {
                "source_name": source_name,
                "source_id": source_id,
            }
            if output_filename:
                payload["output_filename"] = output_filename
            if target_file:
                payload["target_file"] = target_file
            if download_url:
                payload["download_url"] = download_url
            if gcs_path:
                payload["upload_to_gcs"] = True
                payload["gcs_path"] = gcs_path

            resp = await client.post(
                f"{self.base_url}/download-by-id",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()

            data = resp.json()
            return data["download_id"]

    async def get_download_status(self, download_id: str) -> Dict[str, Any]:
        """
        Get the current status of a download.

        Args:
            download_id: Download ID from start_download

        Returns:
            Status dict with progress, speed, path info

        Raises:
            httpx.RequestError: On network-level failures (connection refused, timeout, etc.)
                These are NOT wrapped so wait_for_download can handle retries.
            FlacfetchServiceError: On application-level failures (404, other HTTP errors)
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/download/{download_id}/status",
                    headers=self._headers(),
                    timeout=10,
                )
                resp.raise_for_status()
                return resp.json()

        except httpx.RequestError:
            # Let network errors bubble up so wait_for_download can handle retries
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise FlacfetchServiceError(f"Download not found: {download_id}")
            raise FlacfetchServiceError(f"Status check failed: {e.response.status_code}")
    
    async def wait_for_download(
        self,
        download_id: str,
        timeout: int = 600,
        poll_interval: float = 2.0,
        progress_callback: Optional[callable] = None,
        max_consecutive_errors: int = 3,
    ) -> Dict[str, Any]:
        """
        Wait for a download to complete.

        Tolerates transient network errors during status polling. A single
        connection hiccup won't fail the entire download -- only N consecutive
        poll failures will.

        Args:
            download_id: Download ID to wait for
            timeout: Maximum wait time in seconds
            poll_interval: Time between status checks
            progress_callback: Optional callback(status_dict) for progress updates
            max_consecutive_errors: Number of consecutive poll failures before giving up

        Returns:
            Final status dict with output_path or gcs_path

        Raises:
            FlacfetchServiceError: On download failure, timeout, or too many consecutive poll errors
        """
        elapsed = 0.0
        consecutive_errors = 0

        while elapsed < timeout:
            try:
                status = await self.get_download_status(download_id)
                consecutive_errors = 0  # Reset on success
            except httpx.RequestError as e:
                consecutive_errors += 1
                error_desc = _format_httpx_error(e)
                logger.warning(
                    f"Status poll failed for download {download_id} "
                    f"({consecutive_errors}/{max_consecutive_errors}): {error_desc}"
                )
                if consecutive_errors >= max_consecutive_errors:
                    raise FlacfetchServiceError(
                        f"Status polling failed after {consecutive_errors} consecutive "
                        f"network errors (last: {error_desc})"
                    )
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                continue

            if progress_callback:
                try:
                    progress_callback(status)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

            download_status = status.get("status")

            if download_status in ["complete", "seeding"]:
                logger.info(f"Download {download_id} complete: {status.get('gcs_path') or status.get('output_path')}")
                return status
            elif download_status == "failed":
                error = status.get("error", "Unknown error")
                raise FlacfetchServiceError(f"Download failed: {error}")
            elif download_status == "cancelled":
                raise FlacfetchServiceError("Download was cancelled")

            # Log progress
            progress = status.get("progress", 0)
            speed = status.get("download_speed_kbps", 0)
            peers = status.get("peers", 0)
            logger.debug(
                f"Download {download_id}: {progress:.1f}% "
                f"({speed:.1f} KB/s, {peers} peers)"
            )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise FlacfetchServiceError(f"Download timed out after {timeout}s")
    
    async def list_torrents(self) -> Dict[str, Any]:
        """
        List all torrents in Transmission.
        
        Returns:
            Torrent list response with torrent info and totals
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/torrents",
                    headers=self._headers(),
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()
                
        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"List torrents failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(f"List torrents failed: {e.response.status_code}")
    
    async def delete_torrent(
        self,
        torrent_id: int,
        delete_data: bool = True,
    ) -> Dict[str, Any]:
        """
        Delete a torrent from Transmission.
        
        Args:
            torrent_id: Transmission torrent ID
            delete_data: Also delete downloaded files
            
        Returns:
            Delete response
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{self.base_url}/torrents/{torrent_id}",
                    headers=self._headers(),
                    params={"delete_data": str(delete_data).lower()},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()
                
        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Delete torrent failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(f"Delete torrent failed: {e.response.status_code}")
    
    async def cleanup_torrents(
        self,
        strategy: str = "oldest",
        target_free_gb: float = 10.0,
    ) -> Dict[str, Any]:
        """
        Trigger disk cleanup.

        Args:
            strategy: Cleanup strategy (oldest, largest, lowest_ratio)
            target_free_gb: Target free space to achieve

        Returns:
            Cleanup response with removed count and freed space
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/torrents/cleanup",
                    headers=self._headers(),
                    json={
                        "strategy": strategy,
                        "target_free_gb": target_free_gb,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                return resp.json()

        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Cleanup failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(f"Cleanup failed: {e.response.status_code}")

    # =========================================================================
    # Cache Management
    # =========================================================================

    async def clear_search_cache(self, artist: str, title: str) -> bool:
        """
        Clear a specific cached search result by artist and title.

        Args:
            artist: Artist name
            title: Track title

        Returns:
            True if a cache entry was deleted, False if no entry existed

        Raises:
            FlacfetchServiceError: On request failure
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    "DELETE",
                    f"{self.base_url}/cache/search",
                    headers=self._headers(),
                    json={"artist": artist, "title": title},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                deleted = data.get("deleted", False)
                logger.info(
                    f"Cleared flacfetch cache for '{artist}' - '{title}': "
                    f"{'deleted' if deleted else 'no entry found'}"
                )
                return deleted

        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Clear search cache request failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(
                f"Clear search cache failed: {e.response.status_code} - {e.response.text}"
            )

    async def clear_all_cache(self) -> int:
        """
        Clear all cached search results.

        Returns:
            Number of cache entries deleted

        Raises:
            FlacfetchServiceError: On request failure
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{self.base_url}/cache",
                    headers=self._headers(),
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                deleted_count = data.get("deleted_count", 0)
                logger.info(f"Cleared all flacfetch cache: {deleted_count} entries deleted")
                return deleted_count

        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Clear all cache request failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(
                f"Clear all cache failed: {e.response.status_code} - {e.response.text}"
            )

    # =========================================================================
    # YouTube Availability Check
    # =========================================================================

    async def check_youtube(self, url: str) -> Dict[str, Any]:
        """
        Check if a YouTube video is available for download from this server's location.

        Calls the flacfetch /check-youtube endpoint which uses yt-dlp to detect
        geo-restrictions, private/removed videos, and other unavailability reasons.

        Args:
            url: YouTube video URL or video ID

        Returns:
            Dict with keys: available, video_id, title, error,
            is_geo_restricted, is_age_restricted, is_private, is_removed

        Raises:
            FlacfetchServiceError: On request failure
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/check-youtube",
                    headers=self._headers(),
                    json={"url": url},
                    timeout=15,
                )
                resp.raise_for_status()
                return resp.json()

        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"YouTube availability check failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(
                f"YouTube availability check failed: {e.response.status_code} - {e.response.text}"
            )

    # =========================================================================
    # Cache Management
    # =========================================================================

    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the cache.

        Returns:
            Dict with count, total_size_bytes, oldest_entry, newest_entry, configured

        Raises:
            FlacfetchServiceError: On request failure
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/cache/stats",
                    headers=self._headers(),
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()

        except httpx.RequestError as e:
            raise FlacfetchServiceError(f"Get cache stats request failed: {_format_httpx_error(e)}")
        except httpx.HTTPStatusError as e:
            raise FlacfetchServiceError(
                f"Get cache stats failed: {e.response.status_code} - {e.response.text}"
            )


# Singleton client instance
_client: Optional[FlacfetchClient] = None


def get_flacfetch_client() -> Optional[FlacfetchClient]:
    """
    Get the flacfetch client if configured.
    
    Returns:
        FlacfetchClient instance if FLACFETCH_API_URL and FLACFETCH_API_KEY are set,
        None otherwise (indicating local-only mode).
    """
    global _client
    
    if _client is None:
        from backend.config import get_settings
        settings = get_settings()
        
        if settings.flacfetch_api_url and settings.flacfetch_api_key:
            _client = FlacfetchClient(
                base_url=settings.flacfetch_api_url,
                api_key=settings.flacfetch_api_key,
            )
            logger.info("FlacfetchClient initialized for remote service")
        else:
            logger.debug("FlacfetchClient not configured (missing URL or API key)")
    
    return _client


def reset_flacfetch_client():
    """Reset the singleton client (for testing)."""
    global _client
    _client = None

