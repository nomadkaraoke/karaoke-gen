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
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional, Dict, Any, AsyncIterator

import aiohttp

from backend.config import get_settings
from backend.services.encoding_errors import (
    ENCODING_RESTART_FAILURE_CODE,
    EncodingJobLostError,
    EncodingJobNotFoundError,
    EncodingWorkerCapacityError,
    EncodingWorkerStartError,
)

logger = logging.getLogger(__name__)

# Per-URL submission throttle. The encoding worker has a ThreadPoolExecutor
# of 4 workers (gce_encoding/main.py). Submitting more concurrent jobs than
# that to a single VM caused `Connection reset by peer` errors on the 5th+
# job during the May 6 fallback storm — 7 jobs simultaneously hammered
# fallback-a after the primary failed.
#
# Limit to 3 concurrent renders per URL per Cloud Run instance, leaving 1
# worker thread for status polls / preview / health checks. With multiple
# Cloud Run instances this is not a global cap (would require a distributed
# lock) but it caps each instance's contribution to the storm — empirically
# enough to prevent the failure mode.
_DEFAULT_SUBMISSION_CONCURRENCY = int(
    os.environ.get("ENCODING_SUBMISSION_CONCURRENCY", "3")
)

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

_DEFAULT_QUEUE_TIMEOUT_SECONDS = float(4 * 3600)


def _parse_queue_timeout() -> float:
    """How long a job may sit *queued* (status "pending") before we give up.

    Parsed defensively: a malformed / non-finite / non-positive value must not
    crash the module at import nor make pending jobs fail immediately — fall back
    to the generous default. Separate from the per-run `timeout`, which only
    starts once the job is actually "running".
    """
    raw = os.environ.get("ENCODING_QUEUE_TIMEOUT")
    if raw is None:
        return _DEFAULT_QUEUE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid ENCODING_QUEUE_TIMEOUT=%r; using default", raw)
        return _DEFAULT_QUEUE_TIMEOUT_SECONDS
    if not (value > 0) or value == float("inf"):
        logger.warning("ENCODING_QUEUE_TIMEOUT=%r not a positive finite number; using default", raw)
        return _DEFAULT_QUEUE_TIMEOUT_SECONDS
    return value


def _parse_resubmit_max() -> int:
    """Bounded automatic resubmits when the worker loses a job mid-run.

    Each resubmit is a fresh job id; see `run_with_lost_job_resubmit`. A bad
    value falls back to 2; negatives clamp to 0 (no resubmits).
    """
    raw = os.environ.get("ENCODING_RESUBMIT_MAX", "2")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        logger.warning("Invalid ENCODING_RESUBMIT_MAX=%r; using 2", raw)
        return 2


QUEUE_TIMEOUT_SECONDS = _parse_queue_timeout()
ENCODING_RESUBMIT_MAX = _parse_resubmit_max()


async def run_with_lost_job_resubmit(
    operation: Callable[[str], Awaitable[Any]],
    base_job_id: str,
    *,
    log: logging.Logger = logger,
    max_resubmits: int = ENCODING_RESUBMIT_MAX,
) -> Any:
    """Run a submit+wait `operation(job_id)`, resubmitting if the worker loses it.

    The encoding worker keeps job state in memory; an OOM/deploy restart wipes it
    and the in-flight ffmpeg, so the original job id can never complete. When
    `wait_for_completion` detects that (via `EncodingJobLostError`), we resubmit
    the same work under a fresh `<base>_retry_<hex8>` id (the worker treats a new
    id as a brand-new job). Bounded by `max_resubmits`.

    `operation` must be an async callable taking the job id to use and performing
    the whole submit-and-wait, returning the encode result. It should raise
    `EncodingJobLostError` (propagated from `wait_for_completion`) when the job is
    lost; any other exception aborts immediately.

    Idempotency: only the *worker-side* job id changes between attempts — output
    GCS paths are keyed by the real job (fixed input/output prefixes), so a
    resubmit re-encodes and *overwrites* the same objects rather than producing
    duplicates. Downstream side effects (uploads, distribution) run in the
    orchestrator only after a successful encode, so a resubmit before "complete"
    cannot double them.
    """
    attempt = 0
    while True:
        job_id = base_job_id if attempt == 0 else f"{base_job_id}_retry_{uuid.uuid4().hex[:8]}"
        try:
            return await operation(job_id)
        except EncodingJobLostError as e:
            attempt += 1
            if attempt > max_resubmits:
                log.error(
                    f"[job:{base_job_id}] Encoding worker lost the job {attempt} time(s); "
                    f"giving up after {max_resubmits} resubmit(s): {e}"
                )
                raise
            log.warning(
                f"[job:{base_job_id}] Encoding worker lost the job (restart/OOM); "
                f"resubmitting as a fresh job (attempt {attempt + 1}/{max_resubmits + 1})"
            )


def _format_exception(e: BaseException) -> str:
    """Render an exception with type info.

    Some aiohttp connection errors have empty str(e), which made the original
    "GCE worker connection failed after 8 attempts: " log line useless during
    the 2026-04-24 cold-start incident (job 2c577535). Always include the
    type name so operators can tell what failure class hit them.
    """
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


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

        # Per-URL submission semaphores. Lazily created on first use so we
        # don't bind to the wrong event loop at import time. Bound at
        # _DEFAULT_SUBMISSION_CONCURRENCY to keep concurrent renders per
        # worker VM under the worker's own ThreadPool capacity.
        self._url_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._url_semaphores_lock: Optional[asyncio.Lock] = None
        self._submission_concurrency = _DEFAULT_SUBMISSION_CONCURRENCY

    def set_worker_manager(self, manager):
        """Set the worker manager for dynamic URL resolution from Firestore."""
        self._worker_manager = manager

    def _get_worker_url(self) -> str:
        """Get the current worker URL from Firestore (with TTL cache).

        Returns config.active_url, which is normally the primary URL but
        switches to the capacity-fallback override when a primary-zone
        outage caused us to start a fallback VM in another zone.

        Falls back to static URL from config if worker_manager is not set.
        """
        now = time.time()
        if self._cached_url and (now - self._url_cached_at) < self._URL_CACHE_TTL:
            return self._cached_url

        if self._worker_manager:
            config = self._worker_manager.get_config()
            self._cached_url = config.active_url
            self._url_cached_at = now
            return self._cached_url

        # Fallback to static URL
        if not self._initialized:
            self._load_credentials()
        return self._url

    def _invalidate_cached_url(self) -> None:
        """Force the next _get_worker_url() to re-read from Firestore.

        Called after the worker manager updates active_override (e.g. on
        capacity fallback) so the in-flight request loop picks up the new URL.
        """
        self._cached_url = None
        self._url_cached_at = 0.0

    @asynccontextmanager
    async def _submission_slot(self, url: str, job_id: str) -> AsyncIterator[None]:
        """Bound the number of concurrent renders/encodes per worker URL.

        The encoding worker has 4 thread-pool workers; submitting 7+ at once
        causes `Connection reset by peer` mid-encode (observed during the
        May 6 fallback storm). This semaphore caps each Cloud Run instance's
        concurrent submissions per URL at `_submission_concurrency`,
        leaving headroom for status polls and previews.

        Captures the URL at entry — if the worker manager swaps active_url
        mid-operation (capacity fallback) we keep the original slot. The
        slot count for the new URL will be slightly understated until the
        next submission, which is acceptable: the fallback path is rare,
        and the goal is preventing the orchestrator-side thundering herd.
        """
        # Lazily create the per-loop lock so we bind to the right event loop
        if self._url_semaphores_lock is None:
            self._url_semaphores_lock = asyncio.Lock()

        async with self._url_semaphores_lock:
            sem = self._url_semaphores.get(url)
            if sem is None:
                sem = asyncio.Semaphore(self._submission_concurrency)
                self._url_semaphores[url] = sem

        # Brief log only when we actually have to wait — keeps the happy
        # path quiet but surfaces contention during a fallback storm.
        if sem.locked():
            logger.info(
                f"[job:{job_id}] Waiting for encoding submission slot "
                f"(url={url}, in_flight>={self._submission_concurrency})"
            )
        async with sem:
            yield

    def _build_worker_candidates(self) -> list:
        """Build the ranked candidate list for ensure_any_running.

        Reads optional fallback VMs from settings (env var
        ENCODING_WORKER_FALLBACK_VMS, JSON list of {vm, zone, ip, machine_type?}).
        The primary + all fallbacks are ranked by the shared preference logic
        (fastest-first, demoting recently-stocked-out types) so runtime selection
        stays in lock-step with the deploy green selection. Returns an empty list
        when no worker manager / no configured fallbacks — caller falls back to
        the single-VM ensure_primary_running path.
        """
        if not self._worker_manager:
            return []
        try:
            config = self._worker_manager.get_config()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not read worker config for candidates: {_format_exception(e)}")
            return []

        from backend.services.encoding_worker_manager import EncodingWorkerCandidate
        from backend.services.encoding_worker_preference import (
            PRIMARY_MACHINE_TYPE,
            ordered_candidates,
        )

        # Build a dict pool (primary + fallbacks), each tagged with machine_type so
        # the shared preference logic can rank them. The primary/secondary pair is
        # always c4d; the config may override via primary_machine_type.
        primary_mt = getattr(config, "primary_machine_type", None) or PRIMARY_MACHINE_TYPE
        pool = [{
            "vm": config.primary_vm,
            "zone": self._worker_manager._zone,
            "ip": config.primary_ip,
            "machine_type": primary_mt,
            "kind": "primary",
            "is_primary": True,
        }]

        # Optional capacity-fallback VMs in alternate zones/families, configured via
        # env var. Schema: '[{"vm":"encoding-worker-fallback-c4a","zone":"us-central1-a",
        # "ip":"34.x.x.x","machine_type":"c4-highcpu-32"}, ...]'. machine_type is
        # optional (inferred from the VM name for legacy entries). Empty by default;
        # populated after `pulumi up` provisions the fallback VMs.
        import json as _json
        raw = self.settings.encoding_worker_fallback_vms
        if raw:
            try:
                parsed = _json.loads(raw)
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid ENCODING_WORKER_FALLBACK_VMS JSON: {e}")
                parsed = []
            # A non-list root (null, dict, scalar) parses fine but would blow up
            # `for item in parsed` and escape this method during connection
            # recovery — degrade to no fallbacks instead.
            if not isinstance(parsed, list):
                logger.warning("ENCODING_WORKER_FALLBACK_VMS must be a JSON list; ignoring")
                parsed = []
            for item in parsed:
                try:
                    pool.append({
                        "vm": item["vm"],
                        "zone": item["zone"],
                        "ip": item["ip"],
                        "machine_type": item.get("machine_type"),
                        "kind": "fallback",
                    })
                except (KeyError, TypeError) as e:
                    logger.warning(f"Skipping malformed fallback candidate {item}: {e}")
                    continue

        # Rank fastest-first, demoting types that recently stocked out. Keeps the
        # primary (c4d) at the top whenever it has capacity.
        ranked = ordered_candidates(pool, capacity_state=config.capacity_state)
        return [
            EncodingWorkerCandidate(
                vm_name=c["vm"],
                zone=c["zone"],
                ip=c["ip"],
                machine_type=c.get("machine_type"),
                is_primary=c.get("is_primary", False),
            )
            for c in ranked
        ]

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

    async def _warmup_encoding_worker_fallback(self, job_id: str) -> None:
        """Safety net: try to start the encoding worker VM on first connection failure.

        Two paths:
          - VM already RUNNING/STAGING (deploy restart): return after the start
            attempt; the existing 90s retry loop covers the systemctl restart window.
          - VM was TERMINATED (cold start): block on wait_for_worker_ready until
            the VM is up and /health returns 200. The next retry attempt will
            then succeed immediately. If the readiness wait times out, log and
            return — the retry loop will surface the failure with a clear error.

        If the worker_manager is not set (e.g. dev mode with static URL), this
        is a no-op.

        Raises:
            EncodingWorkerCapacityError: GCE could not allocate capacity (e.g.
                ZONE_RESOURCE_POOL_EXHAUSTED). Caller should bail out of any
                retry loop — no point hammering an HTTP endpoint that will
                never come up — and surface the error to the job.
        """
        if not self._worker_manager:
            return

        candidates = self._build_worker_candidates()
        try:
            if candidates and len(candidates) > 1:
                # Multi-zone path: try primary, then fallbacks in alt zones.
                result = self._worker_manager.ensure_any_running(candidates)
                # If we fell back, the worker manager just persisted the
                # active_override URL — invalidate our cache so the next
                # request hits the right VM.
                if result.get("fell_back"):
                    self._invalidate_cached_url()
            else:
                result = self._worker_manager.ensure_primary_running()
        except EncodingWorkerStartError:
            # Every candidate exhausted (or single-zone primary failed) — both
            # capacity errors and other start failures (e.g. 503
            # SERVICE_UNAVAILABLE from the GCE backend) are transient and the
            # render worker should park the job for retry rather than hard-fail
            # with a misleading "connection timeout" message. EncodingWorker
            # CapacityError subclasses StartError, so this catches both.
            raise
        except Exception as e:
            logger.warning(
                f"[job:{job_id}] Encoding worker warmup fallback failed (non-fatal): "
                f"{_format_exception(e)}"
            )
            return

        if result["started"]:
            logger.warning(
                f"[job:{job_id}] Encoding worker unreachable — started VM {result['vm_name']} as fallback"
            )
            try:
                await self._worker_manager.wait_for_worker_ready(
                    result["vm_name"],
                    f"{result['primary_url']}/health",
                    # Pass the candidate's zone — without this, multi-zone
                    # fallback successfully starts a VM in (e.g.) us-central1-a
                    # but readiness wait looks for it in the manager's default
                    # zone (us-central1-c), gets 404, gives up, and the next
                    # request hits a still-booting VM and times out.
                    zone=result.get("zone"),
                )
                logger.info(
                    f"[job:{job_id}] Cold-started VM {result['vm_name']} is now ready"
                )
            except TimeoutError as e:
                logger.warning(
                    f"[job:{job_id}] Cold-start readiness wait timed out: {e}"
                )
            except Exception as e:
                logger.warning(
                    f"[job:{job_id}] Cold-start readiness wait failed (non-fatal): "
                    f"{_format_exception(e)}"
                )
        else:
            logger.info(
                f"[job:{job_id}] Encoding worker unreachable — VM {result['vm_name']} already running/starting"
            )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        json_payload: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
        job_id: str = "unknown",
        path: Optional[str] = None,
        allow_failover: bool = True,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request with retry logic for transient failures.

        This handles connection errors that occur when the GCE worker is
        restarting (e.g., during deployments) by retrying with exponential backoff.

        Args:
            method: HTTP method (GET, POST)
            url: Initial request URL (used for the first attempt)
            headers: Request headers
            json_payload: JSON body for POST requests
            timeout: Request timeout in seconds
            job_id: Job ID for logging
            path: Endpoint path (e.g. "/render-video"). When set, retry attempts
                AFTER the warmup runs re-resolve the URL via _get_worker_url() +
                path. This is what lets multi-zone fallback take effect mid-loop:
                the warmup may set a new active_override (e.g. switching to a
                fallback VM in another zone) and invalidate the URL cache; the
                next retry then targets the freshly-routed URL instead of the
                original primary which is dead.
            allow_failover: When True (default), a connection error fires the
                fallback-VM warmup and lets retries re-resolve the URL. Set False
                for requests pinned to a specific worker (in-flight status polls):
                a pinned poll must never re-route to active_url nor spin up a
                fallback VM, because a different/fresh VM cannot have the job — it
                would only return "not found" and orphan a render that is actually
                succeeding on the pinned worker (incident 2026-06-16, job d3af33ae).

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
        warmup_ran = False

        for attempt in range(MAX_RETRIES + 1):
            # Re-resolve URL on retries after the warmup has fired, so that any
            # active_override change made by the warmup actually takes effect.
            # Without this, all 8 retry attempts hammer the original (dead)
            # URL even though the warmup successfully routed to a fallback VM.
            if allow_failover and path and warmup_ran and self._worker_manager is not None:
                resolved = f"{self._get_worker_url()}{path}"
                if resolved != url:
                    logger.info(
                        f"[job:{job_id}] Re-resolved encoding URL after warmup: "
                        f"{url} -> {resolved}"
                    )
                    url = resolved

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
                if allow_failover and attempt == 0:
                    try:
                        await self._warmup_encoding_worker_fallback(job_id)
                        warmup_ran = True
                    except EncodingWorkerStartError as start_err:
                        # No VM could be started — capacity exhaustion or
                        # transient backend errors (e.g. 503). Hammering the
                        # HTTP endpoint 7 more times won't help; surface the
                        # typed error so the render worker can park the job
                        # for auto-retry rather than fail hard.
                        logger.error(
                            f"[job:{job_id}] Encoding worker start failed, "
                            f"aborting retries: {start_err}"
                        )
                        raise start_err from e
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"[job:{job_id}] GCE worker connection failed "
                        f"(attempt {attempt + 1}/{MAX_RETRIES + 1}): {_format_exception(e)}. "
                        f"Retrying in {backoff:.1f}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                else:
                    logger.error(
                        f"[job:{job_id}] GCE worker connection failed "
                        f"after {MAX_RETRIES + 1} attempts: {_format_exception(e)}"
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

        path = "/encode"
        url = f"{self._get_worker_url()}{path}"
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
            path=path,
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

    async def get_job_status(
        self, job_id: str, worker_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get the status of an encoding job.

        Args:
            job_id: Job identifier
            worker_url: When set, pin the request to this worker base URL instead
                of resolving the current active_url. In-flight polls must stay on
                the worker that accepted the job: a blue-green deploy can swap the
                active_url primary pointer mid-render, and an unpinned poll would
                migrate to the new primary — which never received the job and 404s
                "not found", orphaning a render that is succeeding on the original
                worker (incident 2026-06-16, job d3af33ae). A pinned poll also
                disables failover (allow_failover=False) so it never re-routes nor
                starts a fallback VM.

        Returns:
            Job status including: status, progress, error, output_files
        """
        self._load_credentials()

        if not self.is_configured:
            raise RuntimeError("Encoding service not configured")

        path = f"/status/{job_id}"
        if worker_url:
            url = f"{worker_url}{path}"
        else:
            url = f"{self._get_worker_url()}{path}"
        headers = {"X-API-Key": self._api_key}

        resp = await self._request_with_retry(
            method="GET",
            url=url,
            headers=headers,
            timeout=30.0,
            job_id=job_id,
            # When pinned, never re-resolve or fail over — the job lives on this
            # exact worker; do not pass `path` (which drives re-resolution).
            path=None if worker_url else path,
            allow_failover=not worker_url,
        )

        if resp["status"] == 401:
            raise RuntimeError("Invalid API key for encoding worker")
        if resp["status"] == 404:
            raise EncodingJobNotFoundError(f"Encoding job {job_id} not found")
        if resp["status"] != 200:
            raise RuntimeError(f"Failed to get job status: {resp['status']} - {resp['text']}")

        return resp["json"]

    async def wait_for_completion(
        self,
        job_id: str,
        poll_interval: float = 10.0,
        timeout: float = 3600.0,
        progress_callback=None,
        worker_url: Optional[str] = None,
        queue_timeout: float = QUEUE_TIMEOUT_SECONDS,
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
            worker_url: Worker base URL the job was submitted to. When set, every
                poll is pinned to this worker (see get_job_status) so a mid-render
                blue-green primary swap can't migrate the poll to a worker that
                never received the job. When None, polls resolve active_url (legacy
                behaviour, e.g. direct callers/tests).

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
        # Set to True after the first successful poll. Once we've seen the job,
        # a later 404 means the worker LOST it (restart wiped in-memory state),
        # not that it never existed — that is unrecoverable for this job id, so
        # we resubmit rather than burn the transient-blip tolerance.
        job_seen = False
        # Set once the job is actually "running". The per-run `timeout` only
        # counts from here; time spent "pending" in the worker's serialized
        # heavy queue counts against `queue_timeout` instead.
        run_started_at: Optional[float] = None

        while True:
            now = asyncio.get_event_loop().time()
            if run_started_at is not None:
                if now - run_started_at > timeout:
                    raise TimeoutError(f"Encoding job {job_id} timed out after {timeout}s")
            elif now - start_time > queue_timeout:
                raise TimeoutError(
                    f"Encoding job {job_id} stuck in worker queue longer than {queue_timeout}s"
                )

            try:
                status = await self.get_job_status(job_id, worker_url=worker_url)
                # Reset failure counter on successful poll
                consecutive_failures = 0
            except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError,
                    asyncio.TimeoutError, RuntimeError) as e:
                # A 404 *after* we've already seen the job = the worker lost it
                # (OOM/deploy restart wiped its in-memory registry). Resubmitting
                # the same id can never recover it, so surface a distinct signal
                # immediately instead of polling 4 more times and mislabelling it
                # "lost contact" (which callers treat as unrecoverable).
                if job_seen and isinstance(e, EncodingJobNotFoundError):
                    logger.warning(
                        f"[job:{job_id}] Worker no longer has this job after previously "
                        f"reporting it — treating as lost (worker restart): {e}"
                    )
                    raise EncodingJobLostError(
                        f"Encoding job {job_id} was lost by the worker (restarted mid-run)",
                        job_id=job_id,
                    ) from e
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

            job_seen = True
            job_status = status.get("status", "unknown")
            progress = status.get("progress", 0)
            if run_started_at is None and (job_status == "running" or progress):
                run_started_at = now

            # Report progress
            if progress != last_progress:
                queue_position = status.get("queue_position")
                if job_status == "pending" and queue_position:
                    logger.info(f"[job:{job_id}] Queued on worker (position {queue_position})")
                else:
                    logger.info(f"[job:{job_id}] Encoding progress: {progress}%")
                last_progress = progress
                if progress_callback:
                    try:
                        progress_callback(progress)
                    except Exception as e:
                        logger.warning(f"Progress callback failed: {e}")

            if job_status == "complete":
                logger.info(f"[job:{job_id}] GCE encoding complete in {now - start_time:.1f}s")
                return status

            if job_status == "failed":
                error = status.get("error", "Unknown error")
                # A restart-marked failure is recoverable by resubmission, same
                # as a mid-run vanish — surface the typed signal so callers retry.
                if status.get("restart_failure_code") == ENCODING_RESTART_FAILURE_CODE:
                    logger.warning(
                        f"[job:{job_id}] Worker marked job failed after a restart — treating as lost: {error}"
                    )
                    raise EncodingJobLostError(
                        f"Encoding job {job_id} was lost by the worker (restarted mid-run)",
                        job_id=job_id,
                    )
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

        This is a convenience method that combines submit + wait. The
        submission is bounded by a per-URL semaphore so concurrent renders
        from the same Cloud Run instance can't overwhelm a single worker
        VM (see `_submission_slot`).

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

        # Resolve URL up front so the semaphore is keyed by the actual
        # target VM (matters when capacity fallback is active — see
        # _submission_slot for the URL-change semantics).
        target_url = self._get_worker_url()

        async with self._submission_slot(target_url, job_id):
            # Submit the job
            submit_result = await self.submit_encoding_job(
                job_id, input_gcs_path, output_gcs_path, config,
            )

            # If cached, return immediately — encoding already done
            submit_status = submit_result.get("status")
            if submit_status == "cached":
                logger.info(f"[job:{job_id}] Encoding already cached, returning immediately")
                return {"status": "complete", "output_files": submit_result.get("output_files")}

            # If in_progress, another request is encoding it — just wait for that
            if submit_status == "in_progress":
                logger.info(f"[job:{job_id}] Encoding already in progress, joining poll")

            # Pin polls to the worker the job landed on (see render_video_on_gce).
            pinned_url = self._get_worker_url()

            # Wait for completion (still holding the slot — the worker is
            # actually doing CPU work for `job_id` until poll returns
            # complete/failed).
            return await self.wait_for_completion(
                job_id, progress_callback=progress_callback, worker_url=pinned_url
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

        path = "/encode-preview"
        url = f"{self._get_worker_url()}{path}"
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
            path=path,
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

        # Pin polls to the worker the job landed on (see render_video_on_gce).
        pinned_url = self._get_worker_url()

        # Wait for completion with a short timeout. Previews are interactive
        # (a user is waiting in the review UI), so cap the *total* wait at the
        # same short bound — don't let a queued preview sit for the long default
        # queue_timeout before its per-run timeout even starts.
        return await self.wait_for_completion(
            job_id=job_id,
            poll_interval=poll_interval,
            timeout=timeout,
            worker_url=pinned_url,
            queue_timeout=timeout,
        )

    async def submit_render_video_job(
        self,
        job_id: str,
        render_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Submit a render-video job to the GCE worker.

        Args:
            job_id: Unique job identifier
            render_config: Configuration dict containing original_corrections_gcs_path,
                audio_gcs_path, output_gcs_prefix, artist, title, and any other render params.

        Returns:
            Response from the encoding worker

        Raises:
            RuntimeError: If submission fails
        """
        self._load_credentials()

        if not self.is_configured:
            raise RuntimeError("Encoding service not configured")

        path = "/render-video"
        url = f"{self._get_worker_url()}{path}"
        headers = {"X-API-Key": self._api_key, "Content-Type": "application/json"}
        payload = {"job_id": job_id, **render_config}

        logger.info(f"[job:{job_id}] Submitting render-video job to GCE worker: {url}")

        resp = await self._request_with_retry(
            method="POST",
            url=url,
            headers=headers,
            json_payload=payload,
            timeout=30.0,
            job_id=job_id,
            path=path,
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
                    logger.info(f"[job:{job_id}] Render-video job already complete on GCE worker, returning cached result")
                    return {
                        "status": "cached",
                        "job_id": job_id,
                        "output_files": status.get("output_files"),
                        "metadata": status.get("metadata"),
                    }
                elif job_status in ("pending", "running"):
                    logger.info(f"[job:{job_id}] Render-video job still in progress on GCE worker")
                    return {"status": "in_progress", "job_id": job_id}
                else:
                    raise RuntimeError(f"Render-video job {job_id} already exists with status: {job_status}")
            except RuntimeError as e:
                if "not found" in str(e).lower():
                    raise RuntimeError(f"Render-video job {job_id} conflict: 409 but job not found on status check")
                raise
        if resp["status"] != 200:
            raise RuntimeError(f"Failed to submit render-video job: {resp['status']} - {resp['text']}")

        return resp["json"]

    async def render_video_on_gce(
        self,
        job_id: str,
        render_config: Dict[str, Any],
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Submit render-video job and wait for completion.

        This is a convenience method that combines submit + wait. The
        submission is bounded by a per-URL semaphore so concurrent renders
        from the same Cloud Run instance can't overwhelm a single worker
        VM (see `_submission_slot`).

        Args:
            job_id: Unique job identifier
            render_config: Configuration dict for the render-video worker endpoint
            progress_callback: Optional callback for progress updates

        Returns:
            Final job status with output files and metadata
        """
        # Resolve URL up front so the semaphore is keyed by the actual
        # target VM. If capacity fallback later swaps active_url, the slot
        # accounting is slightly off for that operation but the new
        # submissions land on the right counter.
        target_url = self._get_worker_url()

        async with self._submission_slot(target_url, job_id):
            # Submit the job
            submit_result = await self.submit_render_video_job(job_id, render_config)

            # If cached, return immediately — rendering already done
            submit_status = submit_result.get("status")
            if submit_status == "cached":
                logger.info(f"[job:{job_id}] Render-video already cached, returning immediately")
                return {
                    "status": "complete",
                    "output_files": submit_result.get("output_files"),
                    "metadata": submit_result.get("metadata"),
                }

            # If in_progress, another request is rendering it — just wait for that
            if submit_status == "in_progress":
                logger.info(f"[job:{job_id}] Render-video already in progress, joining poll")

            # Pin status polls to the worker the job actually landed on. Resolving
            # AFTER submit captures a capacity-fallback re-route (warmup invalidates
            # the URL cache) while being BEFORE any later blue-green deploy swap —
            # so the poll follows the job, not the floating active_url primary
            # pointer (incident 2026-06-16, job d3af33ae).
            pinned_url = self._get_worker_url()

            # Wait for completion (still holding the slot — the worker is
            # actively rendering for `job_id` until poll returns
            # complete/failed).
            return await self.wait_for_completion(
                job_id, progress_callback=progress_callback, worker_url=pinned_url
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
