"""
Worker coordination service.

Handles triggering and coordinating background workers.
Supports two modes:
- Cloud Tasks (production): Guaranteed delivery, automatic retries, horizontal scaling
- Direct HTTP (development): Faster iteration, simpler debugging

SOLID Principles:
- Single Responsibility: Only handles worker coordination
- Dependency Inversion: Depends on HTTP abstraction, not implementation
- Open/Closed: Can add new workers without modifying existing code

Observability:
- Propagates trace context through Cloud Tasks for distributed tracing
- All worker invocations create spans linked to original request trace
"""
import logging
import os
import json
from typing import Optional
import httpx
from google.protobuf import duration_pb2

from backend.config import get_settings
from backend.services.tracing import inject_trace_context


logger = logging.getLogger(__name__)


# Mapping of worker types to their Cloud Tasks queue names
# These queues are created by Pulumi in infrastructure/__main__.py
WORKER_QUEUES = {
    "audio": "audio-worker-queue",
    "lyrics": "lyrics-worker-queue",
    "screens": "screens-worker-queue",
    "render-video": "render-worker-queue",
    "video": "video-worker-queue",
    "idle-reminder": "idle-reminder-queue",  # For delayed idle reminder checks
}

# Dispatch deadlines for each worker type (in seconds)
# Cloud Tasks max is 1800s (30 min). These set how long Cloud Tasks waits
# for the HTTP handler to respond before considering it failed.
# Must be >= actual worker timeout + buffer for startup/upload.
WORKER_DISPATCH_DEADLINES = {
    "audio": 1800,       # 30 min - Modal separation can take 15-20 min
    "lyrics": 1500,      # 25 min - TRANSCRIPTION_TIMEOUT_SECONDS is 1200s (20 min)
    "screens": 600,      # 10 min - Screen generation is fast
    "render-video": 1800,  # 30 min - Video encoding can be slow
    "video": 1800,       # 30 min - Video encoding can be slow
    "idle-reminder": 60, # 1 min - Quick check and potential email send
}

# Default delay for idle reminders (seconds)
IDLE_REMINDER_DELAY_SECONDS = 5 * 60  # 5 minutes


class WorkerService:
    """
    Service for coordinating background workers.
    
    Supports two execution modes controlled by ENABLE_CLOUD_TASKS env var:
    
    1. Cloud Tasks mode (production, ENABLE_CLOUD_TASKS=true):
       - Tasks enqueued to Cloud Tasks for guaranteed delivery
       - Automatic retries on failure
       - Each task runs in dedicated Cloud Run instance
       - Rate limiting to protect external APIs (Modal, AudioShake)
       
    2. Direct HTTP mode (development, ENABLE_CLOUD_TASKS=false):
       - Direct HTTP POST to internal worker endpoints
       - Worker runs as FastAPI BackgroundTask
       - Faster iteration for local development
       - No retry guarantees
    """
    
    def __init__(self):
        """Initialize worker service."""
        self.settings = get_settings()
        self._base_url = self._get_base_url()
        self._admin_token = self._get_admin_token()
        self._use_cloud_tasks = self._should_use_cloud_tasks()
        self._tasks_client = None
        
        if self._use_cloud_tasks:
            logger.info("WorkerService initialized with Cloud Tasks mode")
        else:
            logger.info("WorkerService initialized with direct HTTP mode")
    
    def _should_use_cloud_tasks(self) -> bool:
        """
        Check if Cloud Tasks should be used for worker coordination.
        
        Controlled by ENABLE_CLOUD_TASKS setting (from environment variable).
        Default is false (direct HTTP mode) for backward compatibility.
        
        Returns:
            True if Cloud Tasks should be used, False for direct HTTP
        """
        return self.settings.enable_cloud_tasks
    
    @property
    def tasks_client(self):
        """
        Lazy-initialize Cloud Tasks client.
        
        Only created when needed to avoid import overhead in development mode.
        """
        if self._tasks_client is None and self._use_cloud_tasks:
            try:
                from google.cloud import tasks_v2
                self._tasks_client = tasks_v2.CloudTasksClient()
            except ImportError:
                logger.error(
                    "google-cloud-tasks not installed. "
                    "Install with: pip install google-cloud-tasks"
                )
                raise
        return self._tasks_client
    
    def _get_admin_token(self) -> Optional[str]:
        """
        Get admin token for internal API authentication.
        
        Returns the first admin token from ADMIN_TOKENS env var.
        """
        admin_tokens_str = self.settings.admin_tokens or ""
        tokens = [t.strip() for t in admin_tokens_str.split(",") if t.strip()]
        if tokens:
            return tokens[0]
        return None
    
    def _get_base_url(self) -> str:
        """
        Get base URL for internal API calls.
        
        Priority:
        1. TEST_SERVER_URL env var (for tests)
        2. CLOUD_RUN_SERVICE_URL env var (for Cloud Tasks to call back)
        3. localhost with PORT env var (for development)
        
        Returns:
            Base URL for API calls
        """
        # Check for test environment override
        test_url = os.getenv("TEST_SERVER_URL")
        if test_url:
            return test_url
        
        # Production: Cloud Run service URL
        # This must be the publicly accessible URL for Cloud Tasks to call
        service_url = os.getenv("CLOUD_RUN_SERVICE_URL")
        if service_url:
            return service_url
        
        # Development mode: use localhost with PORT env var
        port = os.getenv("PORT", "8000")
        return f"http://localhost:{port}"
    
    async def trigger_worker(
        self,
        worker_type: str,
        job_id: str,
        timeout_seconds: int = 30
    ) -> bool:
        """
        Trigger a background worker for a job.
        
        In production (ENABLE_CLOUD_TASKS=true):
          - Enqueues task to Cloud Tasks queue
          - Cloud Tasks delivers HTTP request to internal endpoint
          - Automatic retries on failure with exponential backoff
          - Rate limiting protects external APIs
          
        In development (ENABLE_CLOUD_TASKS=false):
          - Direct HTTP POST to internal endpoint
          - Faster iteration, but no retry guarantees
          - Worker runs in same container as API
        
        Args:
            worker_type: Worker type ("audio", "lyrics", "screens", "render-video", "video")
            job_id: Job ID to process
            timeout_seconds: Request timeout (for direct HTTP mode)
            
        Returns:
            True if trigger successful (or task enqueued), False otherwise
        """
        if self._use_cloud_tasks:
            return await self._enqueue_cloud_task(worker_type, job_id)
        else:
            return await self._trigger_http(worker_type, job_id, timeout_seconds)
    
    async def _enqueue_cloud_task(self, worker_type: str, job_id: str) -> bool:
        """
        Enqueue task to Cloud Tasks for guaranteed delivery.
        
        The task will be delivered as an HTTP POST to the internal worker endpoint.
        Cloud Tasks handles:
        - Retry on failure (with exponential backoff)
        - Rate limiting (max dispatches per second)
        - Deduplication (via task name if needed)
        - OIDC authentication for Cloud Run
        
        Observability:
        - Injects trace context headers so worker spans link to parent trace
        
        Args:
            worker_type: Worker type
            job_id: Job ID to process
            
        Returns:
            True if task enqueued successfully, False otherwise
        """
        try:
            from google.cloud import tasks_v2
            
            queue_name = WORKER_QUEUES.get(worker_type)
            if not queue_name:
                logger.error(f"Unknown worker type: {worker_type}")
                return False
            
            project = self.settings.google_cloud_project
            if not project:
                logger.error("GOOGLE_CLOUD_PROJECT not set, cannot enqueue Cloud Task")
                return False
                
            location = self.settings.gcp_region  # Must match queue location
            
            # Build queue path
            parent = self.tasks_client.queue_path(project, location, queue_name)
            
            # Build base headers
            headers = {
                "Content-Type": "application/json",
            }
            
            # Add admin auth via custom header
            # NOTE: We use X-Admin-Token instead of Authorization because Cloud Tasks
            # OIDC token overwrites the Authorization header when oidc_token is specified.
            # The OIDC token handles Cloud Run authentication (allows Cloud Tasks to invoke
            # the service), while X-Admin-Token handles application-level authentication.
            if self._admin_token:
                headers["X-Admin-Token"] = self._admin_token
                logger.debug(
                    f"[job:{job_id}] Using admin token for Cloud Task auth via X-Admin-Token, "
                    f"token prefix: {self._admin_token[:8]}..., len={len(self._admin_token)}"
                )
            
            # Inject trace context for distributed tracing
            # This allows worker spans to link back to the original request trace
            headers = inject_trace_context(headers)
            
            # Get dispatch deadline for this worker type (how long Cloud Tasks waits for response)
            dispatch_deadline_seconds = WORKER_DISPATCH_DEADLINES.get(worker_type, 600)

            # Build task payload
            task = {
                "http_request": {
                    "http_method": tasks_v2.HttpMethod.POST,
                    "url": f"{self._base_url}/api/internal/workers/{worker_type}",
                    "headers": headers,
                    "body": json.dumps({"job_id": job_id}).encode(),
                    # Use OIDC token for Cloud Run authentication
                    # This allows Cloud Tasks to invoke the Cloud Run service
                    "oidc_token": {
                        "service_account_email": f"karaoke-backend@{project}.iam.gserviceaccount.com",
                    },
                },
                # Set dispatch_deadline - how long Cloud Tasks waits for the handler to respond
                # Default is 10 min, but audio separation can take 30+ min
                # Max is 1800s (30 min) for Cloud Tasks
                "dispatch_deadline": duration_pb2.Duration(seconds=dispatch_deadline_seconds),
            }
            
            # Create task
            # Note: We don't set a task name, allowing Cloud Tasks to generate one
            # This prevents duplicate task errors on retries
            response = self.tasks_client.create_task(parent=parent, task=task)
            logger.info(
                f"[job:{job_id}] Created Cloud Task for {worker_type} worker: {response.name} "
                f"(dispatch_deadline={dispatch_deadline_seconds}s)"
            )
            return True
            
        except Exception as e:
            logger.error(
                f"[job:{job_id}] Failed to enqueue Cloud Task for {worker_type}: {e}",
                exc_info=True
            )
            return False
    
    async def _trigger_http(
        self,
        worker_type: str,
        job_id: str,
        timeout_seconds: int = 30
    ) -> bool:
        """
        Trigger worker via direct HTTP call (development mode).
        
        This is the original implementation - direct HTTP POST to internal endpoint.
        The endpoint adds the worker function to FastAPI BackgroundTasks.
        
        Observability:
        - Injects trace context headers so worker spans link to parent trace
        
        Args:
            worker_type: Worker type
            job_id: Job ID to process
            timeout_seconds: Request timeout
            
        Returns:
            True if trigger successful, False otherwise
        """
        try:
            # Build headers with admin auth token
            headers = {}
            if self._admin_token:
                headers["Authorization"] = f"Bearer {self._admin_token}"
            
            # Inject trace context for distributed tracing
            headers = inject_trace_context(headers)
            
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                url = f"{self._base_url}/api/internal/workers/{worker_type}"
                
                response = await client.post(
                    url,
                    json={"job_id": job_id},
                    headers=headers
                )
                
                if response.status_code == 200:
                    logger.info(f"[job:{job_id}] Successfully triggered {worker_type} worker")
                    return True
                else:
                    logger.error(
                        f"[job:{job_id}] Failed to trigger {worker_type} worker: "
                        f"HTTP {response.status_code} - {response.text}"
                    )
                    return False
                    
        except httpx.TimeoutException:
            logger.error(f"[job:{job_id}] Timeout triggering {worker_type} worker")
            return False
            
        except Exception as e:
            logger.error(
                f"[job:{job_id}] Error triggering {worker_type} worker: {e}",
                exc_info=True
            )
            return False
    
    # Convenience methods for specific workers
    # These provide a cleaner API and better IDE autocomplete
    
    async def trigger_audio_worker(self, job_id: str) -> bool:
        """
        Trigger audio separation worker.

        Always uses Cloud Run Jobs to avoid instance termination during long-running
        Modal API calls. The BackgroundTasks approach caused Cloud Run to terminate
        instances mid-processing when no HTTP requests were active.
        """
        return await self._trigger_worker_cloud_run_job(
            job_id=job_id,
            cloud_run_job_name="audio-separation-job",
            worker_module="audio_worker",
        )

    async def trigger_lyrics_worker(self, job_id: str) -> bool:
        """
        Trigger lyrics transcription worker.

        Always uses Cloud Run Jobs to avoid instance termination during long-running
        AudioShake transcription. The BackgroundTasks approach caused Cloud Run to
        terminate instances mid-processing (e.g., job c94cc9d6) when no HTTP requests
        were active, even though the worker was still processing.
        """
        return await self._trigger_worker_cloud_run_job(
            job_id=job_id,
            cloud_run_job_name="lyrics-transcription-job",
            worker_module="lyrics_worker",
        )
    
    async def trigger_screens_worker(self, job_id: str) -> bool:
        """Trigger screen generation worker."""
        return await self.trigger_worker("screens", job_id)
    
    async def trigger_video_worker(self, job_id: str) -> bool:
        """
        Trigger video generation worker.
        
        When USE_CLOUD_RUN_JOBS_FOR_VIDEO=true and ENABLE_CLOUD_TASKS=true,
        uses Cloud Run Jobs for execution (supports >30 min encoding).
        Otherwise, uses Cloud Tasks or direct HTTP.
        """
        if self._use_cloud_tasks and self.settings.use_cloud_run_jobs_for_video:
            return await self._trigger_cloud_run_job(job_id)
        return await self.trigger_worker("video", job_id)
    
    async def _trigger_cloud_run_job(self, job_id: str) -> bool:
        """
        Trigger video encoding as a Cloud Run Job.

        Cloud Run Jobs support up to 24 hours of execution time,
        making them suitable for very long video encoding tasks.

        Args:
            job_id: Job ID to process

        Returns:
            True if job was triggered successfully, False otherwise
        """
        return await self._trigger_worker_cloud_run_job(
            job_id=job_id,
            cloud_run_job_name="video-encoding-job",
            worker_module="video_worker",
        )

    async def _trigger_worker_cloud_run_job(
        self,
        job_id: str,
        cloud_run_job_name: str,
        worker_module: str,
    ) -> bool:
        """
        Trigger a worker as a Cloud Run Job.

        Cloud Run Jobs run to completion without HTTP request lifecycle concerns,
        avoiding the instance termination issue where Cloud Run would shut down
        instances mid-processing when using BackgroundTasks.

        Args:
            job_id: Job ID to process
            cloud_run_job_name: Name of the Cloud Run Job (e.g., "lyrics-transcription-job")
            worker_module: Worker module name (e.g., "lyrics_worker")

        Returns:
            True if job was triggered successfully, False otherwise
        """
        try:
            from google.cloud import run_v2

            project = self.settings.google_cloud_project
            if not project:
                logger.error("GOOGLE_CLOUD_PROJECT not set, cannot trigger Cloud Run Job")
                return False

            location = self.settings.gcp_region
            job_name = f"projects/{project}/locations/{location}/jobs/{cloud_run_job_name}"

            # Create Cloud Run Jobs client
            client = run_v2.JobsClient()

            # Run the job with overrides for the specific job_id
            request = run_v2.RunJobRequest(
                name=job_name,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            args=[
                                "python", "-m", f"backend.workers.{worker_module}",
                                "--job-id", job_id,
                            ],
                        )
                    ]
                )
            )

            # Run the job (async operation)
            operation = client.run_job(request=request)
            logger.info(
                f"[job:{job_id}] Started Cloud Run Job {cloud_run_job_name}: "
                f"{operation.metadata}"
            )
            return True

        except Exception as e:
            logger.error(
                f"[job:{job_id}] Failed to trigger Cloud Run Job {cloud_run_job_name}: {e}",
                exc_info=True
            )
            return False
    
    async def trigger_render_video_worker(self, job_id: str) -> bool:
        """Trigger render video worker (post-review)."""
        return await self.trigger_worker("render-video", job_id)

    async def schedule_idle_reminder(
        self,
        job_id: str,
        delay_seconds: int = IDLE_REMINDER_DELAY_SECONDS
    ) -> bool:
        """
        Schedule an idle reminder check for a job.

        The reminder task will be delivered after the specified delay.
        When the task executes, it checks if the job is still in a blocking
        state and sends a reminder email if the user hasn't taken action.

        Args:
            job_id: Job ID to check
            delay_seconds: Delay before executing the check (default: 5 minutes)

        Returns:
            True if task was scheduled successfully, False otherwise
        """
        if not self._use_cloud_tasks:
            # In development mode, log and skip (no delayed execution support)
            logger.info(
                f"[job:{job_id}] Idle reminder not scheduled (Cloud Tasks disabled). "
                f"Would have fired in {delay_seconds}s."
            )
            return True

        try:
            from google.cloud import tasks_v2
            from google.protobuf import timestamp_pb2
            import time

            queue_name = WORKER_QUEUES.get("idle-reminder")
            if not queue_name:
                logger.error("Idle reminder queue not configured")
                return False

            project = self.settings.google_cloud_project
            if not project:
                logger.error("GOOGLE_CLOUD_PROJECT not set")
                return False

            location = self.settings.gcp_region

            # Build queue path
            parent = self.tasks_client.queue_path(project, location, queue_name)

            # Build headers
            headers = {
                "Content-Type": "application/json",
            }

            if self._admin_token:
                headers["X-Admin-Token"] = self._admin_token

            # Inject trace context
            headers = inject_trace_context(headers)

            # Calculate schedule time
            schedule_time = timestamp_pb2.Timestamp()
            schedule_time.FromSeconds(int(time.time()) + delay_seconds)

            dispatch_deadline_seconds = WORKER_DISPATCH_DEADLINES.get("idle-reminder", 60)

            # Build task payload
            task = {
                "http_request": {
                    "http_method": tasks_v2.HttpMethod.POST,
                    "url": f"{self._base_url}/api/internal/jobs/{job_id}/check-idle-reminder",
                    "headers": headers,
                    "body": json.dumps({"job_id": job_id}).encode(),
                    "oidc_token": {
                        "service_account_email": f"karaoke-backend@{project}.iam.gserviceaccount.com",
                    },
                },
                "schedule_time": schedule_time,
                "dispatch_deadline": duration_pb2.Duration(seconds=dispatch_deadline_seconds),
            }

            # Create task
            response = self.tasks_client.create_task(parent=parent, task=task)
            logger.info(
                f"[job:{job_id}] Scheduled idle reminder check in {delay_seconds}s: {response.name}"
            )
            return True

        except Exception as e:
            logger.error(
                f"[job:{job_id}] Failed to schedule idle reminder: {e}",
                exc_info=True
            )
            return False


# Global worker service instance
_worker_service: Optional[WorkerService] = None


def get_worker_service() -> WorkerService:
    """
    Get global worker service instance.
    
    Singleton pattern to reuse HTTP client pool.
    
    Returns:
        WorkerService instance
    """
    global _worker_service
    if _worker_service is None:
        _worker_service = WorkerService()
    return _worker_service


def reset_worker_service() -> None:
    """
    Reset the global worker service instance.
    
    Used in tests to ensure clean state between test cases.
    """
    global _worker_service
    _worker_service = None
