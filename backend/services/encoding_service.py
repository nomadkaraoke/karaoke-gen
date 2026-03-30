"""
GCE Encoding Worker Service.

This service dispatches video encoding jobs to a dedicated high-performance
GCE instance (C4-standard with Intel Granite Rapids CPU) for faster encoding.

The GCE worker provides:
- 3.9 GHz all-core frequency (vs 3.7 GHz on Cloud Run)
- Dedicated vCPUs (no contention)
- 2-3x faster FFmpeg libx264 encoding

Usage:
    encoding_service = get_encoding_service()
    if encoding_service.is_configured:
        result = await encoding_service.encode_videos(job_id, input_gcs_path, config)
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any

import aiohttp

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Retry configuration for handling transient failures (e.g., worker restarts during deployments).
#
# During a CI deployment, the encoding worker is restarted via systemctl. The restart
# involves downloading a new wheel from GCS, installing it, and starting uvicorn —
# which can take 30-90 seconds. The retry window must exceed this to avoid failing
# jobs that happen to hit the encoding stage during a deploy.
#
# With 7 retries and exponential backoff (5s, 10s, 15s, 15s, 15s, 15s, 15s),
# total retry window is ~90s, sufficient to survive a full worker restart.
MAX_RETRIES = 7
INITIAL_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 15.0

# Poll failure tolerance: number of consecutive poll failures allowed before giving up.
# This prevents a single transient network blip during status polling from killing a
# long-running encoding job. Similar pattern to flacfetch status polling (PR #446).
MAX_CONSECUTIVE_POLL_FAILURES = 5


class EncodingService:
    """Service for dispatching encoding jobs to GCE worker."""

    def __init__(self):
        self.settings = get_settings()
        self._url = None
        self._api_key = None
        self._initialized = False
        self._worker_manager = None
        self._cached_url = None
        self._url_cached_at = 0
        self._URL_CACHE_TTL = 30  # seconds

    def set_worker_manager(self, manager):
        """Set the worker manager for dynamic URL resolution from Firestore."""
        self._worker_manager = manager

    def _get_worker_url(self) -> str:
        """Get the current primary worker URL from Firestore (with TTL cache).

        Falls back to static URL from config if worker_manager is not set.
        """
        now = time.time()
        if self._cached_url and (now - self._url_cached_at) < self._URL_CACHE_TTL:
            return self._cached_url

        if self._worker_manager:
            config = self._worker_manager.get_config()
            self._cached_url = config.primary_url
            self._url_cached_at = now
            return self._cached_url

        # Fallback to static URL
        if not self._initialized:
            self._load_credentials()
        return self._url

    def _load_credentials(self):
        """Load encoding worker URL and API key from config/secrets."""
        if self._initialized:
            return

        # Try environment variables first, then Secret Manager
        self._url = self.settings.encoding_worker_url
        self._api_key = self.settings.encoding_worker_api_key

        # Fall back to Secret Manager
        if not self._url:
            self._url = self.settings.get_secret("encoding-worker-url")
        if not self._api_key:
            self._api_key = self.settings.get_secret("encoding-worker-api-key")

        self._initialized = True

    @property
    def is_configured(self) -> bool:
        """Check if encoding service is configured with URL and API key."""
        self._load_credentials()
        return bool(self._url and self._api_key)

    @property
    def is_enabled(self) -> bool:
        """Check if GCE encoding is enabled and configured."""
        return self.settings.use_gce_encoding and self.is_configured

    @property
    def is_preview_enabled(self) -> bool:
        """Check if GCE preview encoding is enabled and configured."""
        return self.settings.use_gce_preview_encoding and self.is_configured

    def _warmup_encoding_worker_fallback(self, job_id: str) -> None:
        """Safety net: try to start the encoding worker VM on first connection failure.

        If the worker_manager is available, calls ensure_primary_running() so the VM
        starts booting during the retry window. If worker_manager is not set (e.g. dev
        mode with static URL), this is a no-op.
        """
        if not self._worker_manager:
            return
        try:
            result = self._worker_manager.ensure_primary_running()
            if result["started"]:
                logger.warning(
                    f"[job:{job_id}] Encoding worker unreachable — started VM {result['vm_name']} as fallback"
                )
            else:
                logger.info(
                    f"[job:{job_id}] Encoding worker unreachable — VM {result['vm_name']} already running/starting"
                )
        except Exception as e:
            logger.warning(f"[job:{job_id}] Encoding worker warmup fallback failed (non-fatal): {e}")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        json_payload: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
        job_id: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Make an HTTP request with retry logic for transient failures.

        This handles connection errors that occur when the GCE worker is
        restarting (e.g., during deployments) by retrying with exponential backoff.

        Args:
            method: HTTP method (GET, POST)
            url: Request URL
            headers: Request headers
            json_payload: JSON body for POST requests
            timeout: Request timeout in seconds
            job_id: Job ID for logging

        Returns:
            Dict with keys:
                - status (int): HTTP status code
                - json (Any): Parsed JSON response body (if status 200, else None)
                - text (str): Raw response text (if status != 200, else None)

        Raises:
            aiohttp.ClientConnectorError: If all retries fail due to connection errors
            aiohttp.ServerDisconnectedError: If all retries fail due to server disconnect
            asyncio.TimeoutError: If all retries fail due to timeout
        """
        last_exception = None
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    if method.upper() == "POST":
                        async with session.post(
                            url, json=json_payload, headers=headers, timeout=timeout
                        ) as resp:
                            # Return a copy of the response data since we exit the context
                            return {
                                "status": resp.status,
                                "json": await resp.json() if resp.status == 200 else None,
                                "text": await resp.text() if resp.status != 200 else None,
                            }
                    else:  # GET
                        async with session.get(
                            url, headers=headers, timeout=timeout
                        ) as resp:
                            return {
                                "status": resp.status,
                                "json": await resp.json() if resp.status == 200 else None,
                                "text": await resp.text() if resp.status != 200 else None,
                            }
            except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as e:
                last_exception = e
                if attempt == 0:
                    self._warmup_encoding_worker_fallback(job_id)
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"[job:{job_id}] GCE worker connection failed (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}. "
                        f"Retrying in {backoff:.1f}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                else:
                    logger.error(
                        f"[job:{job_id}] GCE worker connection failed after {MAX_RETRIES + 1} attempts: {e}"
                    )

        raise last_exception

    async def submit_encoding_job(
        self,
        job_id: str,
        input_gcs_path: str,
        output_gcs_path: str,
        encoding_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Submit an encoding job to the GCE worker.

        Args:
            job_id: Unique job identifier
            input_gcs_path: GCS path to input files (gs://bucket/path/)
            output_gcs_path: GCS path for output files (gs://bucket/path/)
            encoding_config: Configuration for encoding (formats, quality, etc.)

        Returns:
            Response from the encoding worker

        Raises:
            Exception: If submission fails
        """
        self._load_credentials()

        if not self.is_configured:
            raise RuntimeError("Encoding service not configured")

        url = f"{self._get_worker_url()}/encode"
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}
        payload = {
            "job_id": job_id,
            "input_gcs_path": input_gcs_path,
            "output_gcs_path": output_gcs_path,
            "encoding_config": encoding_config,
        }

        logger.info(f"[job:{job_id}] Submitting encoding job to GCE worker: {url}")

        resp = await self._request_with_retry(
            method="POST",
            url=url,
            headers=headers,
            json_payload=payload,
            timeout=30.0,
            job_id=job_id,
        )

        if resp["status"] == 401:
            raise RuntimeError("Invalid API key for encoding worker")
        if resp["status"] == 409:
            # Job already exists on worker — check if it already completed
            logger.warning(f"[job:{job_id}] GCE worker returned 409, checking job status")
            try:
                status = await self.get_job_status(job_id)
                job_status = status.get("status", "unknown")
                if job_status == "complete":
                    logger.info(f"[job:{job_id}] Job already complete on GCE worker, returning cached result")
                    return {"status": "cached", "job_id": job_id, "output_files": status.get("output_files")}
                elif job_status in ("pending", "running"):
                    logger.info(f"[job:{job_id}] Job still in progress on GCE worker")
                    return {"status": "in_progress", "job_id": job_id}
                else:
                    raise RuntimeError(f"Encoding job {job_id} already exists with status: {job_status}")
            except RuntimeError as e:
                if "not found" in str(e).lower():
                    # Job was in worker memory but got cleared (restart) — safe to raise original error
                    raise RuntimeError(f"Encoding job {job_id} conflict: 409 but job not found on status check")
                raise
        if resp["status"] != 200:
            raise RuntimeError(f"Failed to submit encoding job: {resp['status']} - {resp['text']}")

        return resp["json"]

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status of an encoding job.

        Args:
            job_id: Job identifier

        Returns:
            Job status including: status, progress, error, output_files
        """
        self._load_credentials()

        if not self.is_configured:
            raise RuntimeError("Encoding service not configured")

        url = f"{self._get_worker_url()}/status/{job_id}"
        headers = {"X-API-Key": self._api_key}

        resp = await self._request_with_retry(
            method="GET",
            url=url,
            headers=headers,
            timeout=30.0,
            job_id=job_id,
        )

        if resp["status"] == 401:
            raise RuntimeError("Invalid API key for encoding worker")
        if resp["status"] == 404:
            raise RuntimeError(f"Encoding job {job_id} not found")
        if resp["status"] != 200:
            raise RuntimeError(f"Failed to get job status: {resp['status']} - {resp['text']}")

        return resp["json"]

    async def wait_for_completion(
        self,
        job_id: str,
        poll_interval: float = 10.0,
        timeout: float = 3600.0,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Poll for encoding job completion with tolerance for transient failures.

        During a deployment, the encoding worker may restart while we're polling.
        Rather than failing the entire job on a single poll error, we tolerate up to
        MAX_CONSECUTIVE_POLL_FAILURES consecutive failures before giving up. This is
        the same pattern used in flacfetch status polling (PR #446).

        Args:
            job_id: Job identifier
            poll_interval: Seconds between status checks
            timeout: Maximum time to wait (default 1 hour)
            progress_callback: Optional callback(progress: int) for progress updates

        Returns:
            Final job status with output files

        Raises:
            TimeoutError: If job doesn't complete within timeout
            RuntimeError: If job fails or too many consecutive poll failures
        """
        logger.info(f"[job:{job_id}] Waiting for GCE encoding to complete...")

        start_time = asyncio.get_event_loop().time()
        last_progress = 0
        consecutive_failures = 0

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Encoding job {job_id} timed out after {timeout}s")

            try:
                status = await self.get_job_status(job_id)
                # Reset failure counter on successful poll
                consecutive_failures = 0
            except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError,
                    asyncio.TimeoutError, RuntimeError) as e:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                    logger.error(
                        f"[job:{job_id}] Status polling failed {consecutive_failures} consecutive times, giving up: {e}"
                    )
                    raise RuntimeError(
                        f"Encoding job {job_id} lost contact with worker after "
                        f"{consecutive_failures} consecutive poll failures: {e}"
                    )
                logger.warning(
                    f"[job:{job_id}] Status poll failed ({consecutive_failures}/{MAX_CONSECUTIVE_POLL_FAILURES}): {e}. "
                    f"Worker may be restarting, will retry..."
                )
                await asyncio.sleep(poll_interval)
                continue

            # Handle case where GCE worker returns a list instead of dict
            if isinstance(status, list):
                logger.warning(f"[job:{job_id}] GCE returned list instead of dict: {status}")
                status = status[0] if status and isinstance(status[0], dict) else {}
            if not isinstance(status, dict):
                logger.error(f"[job:{job_id}] Unexpected status type: {type(status)}")
                status = {}

            job_status = status.get("status", "unknown")
            progress = status.get("progress", 0)

            # Report progress
            if progress != last_progress:
                logger.info(f"[job:{job_id}] Encoding progress: {progress}%")
                last_progress = progress
                if progress_callback:
                    try:
                        progress_callback(progress)
                    except Exception as e:
                        logger.warning(f"Progress callback failed: {e}")

            if job_status == "complete":
                logger.info(f"[job:{job_id}] GCE encoding complete in {elapsed:.1f}s")
                return status

            if job_status == "failed":
                error = status.get("error", "Unknown error")
                raise RuntimeError(f"Encoding job {job_id} failed: {error}")

            await asyncio.sleep(poll_interval)

    async def encode_videos(
        self,
        job_id: str,
        input_gcs_path: str,
        output_gcs_path: str,
        encoding_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Submit encoding job and wait for completion.

        This is a convenience method that combines submit + wait.

        Args:
            job_id: Unique job identifier
            input_gcs_path: GCS path to input files
            output_gcs_path: GCS path for output files
            encoding_config: Optional encoding configuration
            progress_callback: Optional callback for progress updates

        Returns:
            Final job status with output files
        """
        config = encoding_config or {"formats": ["mp4_4k", "mp4_720p"]}

        # Submit the job
        submit_result = await self.submit_encoding_job(job_id, input_gcs_path, output_gcs_path, config)

        # If cached, return immediately — encoding already done
        submit_status = submit_result.get("status")
        if submit_status == "cached":
            logger.info(f"[job:{job_id}] Encoding already cached, returning immediately")
            return {"status": "complete", "output_files": submit_result.get("output_files")}

        # If in_progress, another request is encoding it — just wait for that
        if submit_status == "in_progress":
            logger.info(f"[job:{job_id}] Encoding already in progress, joining poll")

        # Wait for completion
        return await self.wait_for_completion(
            job_id, progress_callback=progress_callback
        )

    async def submit_preview_encoding_job(
        self,
        job_id: str,
        ass_gcs_path: str,
        audio_gcs_path: str,
        output_gcs_path: str,
        background_color: str = "black",
        background_image_gcs_path: Optional[str] = None,
        font_gcs_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit a preview video encoding job to the GCE worker.

        Args:
            job_id: Unique job identifier
            ass_gcs_path: GCS path to ASS subtitles file (gs://bucket/path/file.ass)
            audio_gcs_path: GCS path to audio file
            output_gcs_path: GCS path for output video
            background_color: Background color (default: black)
            background_image_gcs_path: Optional GCS path to background image
            font_gcs_path: Optional GCS path to custom font file

        Returns:
            Response from the encoding worker

        Raises:
            Exception: If submission fails
        """
        self._load_credentials()

        if not self.is_configured:
            raise RuntimeError("Encoding service not configured")

        url = f"{self._get_worker_url()}/encode-preview"
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}
        payload = {
            "job_id": job_id,
            "ass_gcs_path": ass_gcs_path,
            "audio_gcs_path": audio_gcs_path,
            "output_gcs_path": output_gcs_path,
            "background_color": background_color,
        }
        if background_image_gcs_path:
            payload["background_image_gcs_path"] = background_image_gcs_path
        if font_gcs_path:
            payload["font_gcs_path"] = font_gcs_path

        logger.info(f"[job:{job_id}] Submitting preview encoding job to GCE worker: {url}")

        resp = await self._request_with_retry(
            method="POST",
            url=url,
            headers=headers,
            json_payload=payload,
            timeout=30.0,
            job_id=job_id,
        )

        if resp["status"] == 401:
            raise RuntimeError("Invalid API key for encoding worker")
        if resp["status"] == 409:
            raise RuntimeError(f"Preview encoding job {job_id} already exists")
        if resp["status"] != 200:
            raise RuntimeError(f"Failed to submit preview encoding job: {resp['status']} - {resp['text']}")

        return resp["json"]

    async def encode_preview_video(
        self,
        job_id: str,
        ass_gcs_path: str,
        audio_gcs_path: str,
        output_gcs_path: str,
        background_color: str = "black",
        background_image_gcs_path: Optional[str] = None,
        font_gcs_path: Optional[str] = None,
        timeout: float = 90.0,
        poll_interval: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Submit preview encoding job and wait for completion.

        This is a convenience method that combines submit + wait with shorter
        timeouts suitable for preview videos.

        Args:
            job_id: Unique job identifier
            ass_gcs_path: GCS path to ASS subtitles file
            audio_gcs_path: GCS path to audio file
            output_gcs_path: GCS path for output video
            background_color: Background color (default: black)
            background_image_gcs_path: Optional GCS path to background image
            font_gcs_path: Optional GCS path to custom font file
            timeout: Maximum time to wait (default 90s for preview)
            poll_interval: Seconds between status checks (default 2s)

        Returns:
            Final job status with output files
        """
        # Submit the job
        submit_result = await self.submit_preview_encoding_job(
            job_id=job_id,
            ass_gcs_path=ass_gcs_path,
            audio_gcs_path=audio_gcs_path,
            output_gcs_path=output_gcs_path,
            background_color=background_color,
            background_image_gcs_path=background_image_gcs_path,
            font_gcs_path=font_gcs_path,
        )

        # If cached, return immediately - video already exists in GCS
        submit_status = submit_result.get("status")
        if submit_status == "cached":
            logger.info(f"[job:{job_id}] Preview already cached, returning immediately")
            return {"status": "complete", "output_path": submit_result.get("output_path")}

        # If in_progress, another request is encoding it - just wait for that
        if submit_status == "in_progress":
            logger.info(f"[job:{job_id}] Preview encoding already in progress, waiting")

        # Wait for completion with shorter timeout
        return await self.wait_for_completion(
            job_id=job_id,
            poll_interval=poll_interval,
            timeout=timeout,
        )

    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the encoding worker.

        Returns:
            Health status including active jobs and FFmpeg version
        """
        self._load_credentials()

        if not self.is_configured:
            return {"status": "not_configured"}

        url = f"{self._get_worker_url()}/health"
        headers = {"X-API-Key": self._api_key}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return {"status": "error", "code": resp.status}
        except Exception as e:
            return {"status": "error", "error": str(e)}


# Singleton instance
_encoding_service: Optional[EncodingService] = None


def get_encoding_service() -> EncodingService:
    """Get the singleton encoding service instance."""
    global _encoding_service
    if _encoding_service is None:
        _encoding_service = EncodingService()
        # Wire up worker manager for dynamic URL resolution
        try:
            from backend.services.encoding_worker_manager import EncodingWorkerManager
            from google.cloud import compute_v1, firestore
            settings = get_settings()
            db = firestore.Client(project=settings.google_cloud_project)
            compute_client = compute_v1.InstancesClient()
            manager = EncodingWorkerManager(
                db=db,
                compute_client=compute_client,
                project_id=settings.google_cloud_project,
            )
            _encoding_service.set_worker_manager(manager)
        except Exception:
            # Fallback to static URL if worker manager setup fails
            pass
    return _encoding_service
