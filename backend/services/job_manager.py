"""
Job management and queue operations.

This module handles the complete job lifecycle including:
- Job creation and initialization
- State transitions and validation
- Worker coordination (parallel audio + lyrics processing)
- Progress tracking and timeline events
- Error handling and retries
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from backend.config import settings
from backend.exceptions import InvalidStateTransitionError, InsufficientCreditsError
from backend.models.job import Job, JobStatus, JobCreate, STATE_TRANSITIONS
from backend.models.worker_log import WorkerLogEntry
from backend.services.firestore_service import FirestoreService
from backend.services.storage_service import StorageService


logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """Mask email for logging to protect PII. Shows first char + domain."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


class JobManager:
    """Manager for job lifecycle and state."""
    
    def __init__(self):
        """Initialize job manager with required services."""
        self.firestore = FirestoreService()
        self.storage = StorageService()
    
    def create_job(self, job_create: JobCreate, is_admin: bool = False) -> Job:
        """
        Create a new job with initial state PENDING.

        Jobs start in PENDING state and transition to DOWNLOADING
        when a worker picks them up.

        Args:
            job_create: Job creation parameters
            is_admin: Whether the requesting user is an admin (bypasses credit checks)

        Raises:
            ValueError: If theme_id is not provided (all jobs require a theme)
        """
        # Check credits (skip for admins)
        if job_create.user_email and not is_admin:
            from backend.services.user_service import get_user_service
            user_service = get_user_service()
            if not user_service.has_credits(job_create.user_email):
                credits_available = user_service.check_credits(job_create.user_email)
                raise InsufficientCreditsError(
                    message="You're out of credits. Buy more to continue creating karaoke videos.",
                    credits_available=credits_available,
                    credits_required=1,
                )

        # Enforce theme requirement - all jobs must have a theme
        # This prevents unstyled videos from ever being generated
        if not job_create.theme_id:
            raise ValueError(
                "theme_id is required for all jobs. "
                "Use get_theme_service().get_default_theme_id() to get the default theme."
            )

        job_id = str(uuid.uuid4())[:8]
        
        now = datetime.utcnow()
        job = Job(
            job_id=job_id,
            status=JobStatus.PENDING,  # New state machine starts with PENDING
            progress=0,
            created_at=now,
            updated_at=now,
            url=str(job_create.url) if job_create.url else None,
            artist=job_create.artist,
            title=job_create.title,
            # User preferences
            enable_cdg=job_create.enable_cdg,
            enable_txt=job_create.enable_txt,
            enable_youtube_upload=job_create.enable_youtube_upload,
            youtube_description=job_create.youtube_description,
            youtube_description_template=job_create.youtube_description_template,  # video_worker reads this
            webhook_url=job_create.webhook_url,
            user_email=job_create.user_email,
            # Distribution settings
            brand_prefix=job_create.brand_prefix,
            discord_webhook_url=job_create.discord_webhook_url,
            dropbox_path=job_create.dropbox_path,
            gdrive_folder_id=job_create.gdrive_folder_id,
            # Theme configuration
            theme_id=job_create.theme_id,
            color_overrides=job_create.color_overrides,
            style_params_gcs_path=job_create.style_params_gcs_path,
            style_assets=job_create.style_assets,
            # Two-phase workflow (Batch 6)
            prep_only=job_create.prep_only,
            finalise_only=job_create.finalise_only,
            keep_brand_code=job_create.keep_brand_code,
            # Request metadata (for tracking and filtering)
            request_metadata=job_create.request_metadata,
            # Tenant scoping
            tenant_id=job_create.tenant_id,
            # Made-for-you order fields
            made_for_you=job_create.made_for_you,
            customer_email=job_create.customer_email,
            customer_notes=job_create.customer_notes,
            # Private (non-published) track mode
            is_private=job_create.is_private,
            # Capture creation-time decision params for observability
            creation_params={
                "is_private": job_create.is_private,
                "is_admin": is_admin,
                "created_from": job_create.request_metadata.get("created_from", "unknown"),
            },
        )

        self.firestore.create_job(job)
        logger.info(f"Created new job {job_id} with status PENDING")

        # Deduct credit atomically (after job is persisted so we have job_id for transaction record)
        if job_create.user_email and not is_admin:
            from backend.services.user_service import get_user_service
            user_service = get_user_service()
            success, _remaining, deduct_msg = user_service.deduct_credit(
                job_create.user_email, job_id, reason="job_creation"
            )
            if not success:
                # Deduction failed (e.g., race condition) - delete the job and raise error
                logger.error(f"Credit deduction failed for job {job_id}: {deduct_msg}")
                try:
                    self.firestore.delete_job(job_id)
                except Exception:
                    logger.exception(f"Failed to delete job {job_id} after credit deduction failure")
                raise InsufficientCreditsError(
                    message="Failed to deduct credit. Please try again.",
                    credits_available=0,
                    credits_required=1,
                )

        return job
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        return self.firestore.get_job(job_id)
    
    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        **kwargs
    ) -> None:
        """Update job status with timeline tracking."""
        self.firestore.update_job_status(
            job_id=job_id,
            status=status,
            progress=progress,
            message=message,
            **kwargs
        )
    
    def update_job(self, job_id: str, updates: Dict[str, Any]) -> None:
        """Update job with arbitrary fields."""
        self.firestore.update_job(job_id, updates)
    
    def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        environment: Optional[str] = None,
        client_id: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        user_email: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Job]:
        """
        List jobs with optional filtering.

        Args:
            status: Filter by job status
            environment: Filter by request_metadata.environment (test/production/development)
            client_id: Filter by request_metadata.client_id (customer identifier)
            created_after: Filter jobs created after this datetime
            created_before: Filter jobs created before this datetime
            user_email: Filter by user_email (job owner)
            tenant_id: Filter by tenant_id (white-label portal scoping)
            limit: Maximum number of jobs to return

        Returns:
            List of Job objects matching filters
        """
        return self.firestore.list_jobs(
            status=status,
            environment=environment,
            client_id=client_id,
            created_after=created_after,
            created_before=created_before,
            user_email=user_email,
            tenant_id=tenant_id,
            limit=limit
        )
    
    def list_jobs_summary(
        self,
        status: Optional[JobStatus] = None,
        exclude_statuses: Optional[List[str]] = None,
        environment: Optional[str] = None,
        client_id: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        user_email: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List jobs with field projection (summary mode)."""
        return self.firestore.list_jobs_summary(
            status=status,
            exclude_statuses=exclude_statuses,
            environment=environment,
            client_id=client_id,
            created_after=created_after,
            created_before=created_before,
            user_email=user_email,
            tenant_id=tenant_id,
            limit=limit,
        )

    def delete_jobs_by_filter(
        self,
        environment: Optional[str] = None,
        client_id: Optional[str] = None,
        status: Optional[JobStatus] = None,
        created_before: Optional[datetime] = None,
        delete_files: bool = True
    ) -> Dict[str, Any]:
        """
        Delete multiple jobs matching filter criteria.
        
        CAUTION: This is a destructive operation. Use carefully.
        
        Args:
            environment: Delete jobs with this environment (e.g., "test")
            client_id: Delete jobs from this client
            status: Delete jobs with this status
            created_before: Delete jobs created before this datetime
            delete_files: Also delete GCS files (default True)
            
        Returns:
            Dict with deletion statistics
        """
        # First get matching jobs to delete their files
        if delete_files:
            jobs = self.firestore.list_jobs(
                status=status,
                environment=environment,
                client_id=client_id,
                created_before=created_before,
                limit=10000  # High limit for deletion
            )
            
            files_deleted = 0
            for job in jobs:
                # Delete files from various locations
                try:
                    # Delete uploads folder
                    self.storage.delete_folder(f"uploads/{job.job_id}/")
                    # Delete jobs folder
                    self.storage.delete_folder(f"jobs/{job.job_id}/")
                    files_deleted += 1
                except Exception as e:
                    logger.warning(f"Error deleting files for job {job.job_id}: {e}")
        
        # Delete the jobs from Firestore
        deleted_count = self.firestore.delete_jobs_by_filter(
            environment=environment,
            client_id=client_id,
            status=status,
            created_before=created_before
        )
        
        return {
            'jobs_deleted': deleted_count,
            'files_deleted': files_deleted if delete_files else 0
        }
    
    def mark_job_error(self, job_id: str, error_message: str) -> None:
        """Mark a job as errored."""
        self.firestore.update_job_status(
            job_id=job_id,
            status=JobStatus.ERROR,
            progress=0,
            message=error_message,
            error_message=error_message
        )
        logger.error(f"Job {job_id} marked as error: {error_message}")
    
    def get_output_urls(self, job_id: str) -> Dict[str, str]:
        """Generate signed URLs for job output files."""
        job = self.get_job(job_id)
        if not job or not job.output_files:
            return {}
        
        urls = {}
        for file_type, gcs_path in job.output_files.items():
            try:
                urls[file_type] = self.storage.generate_signed_url(gcs_path, expiration_minutes=120)
            except Exception as e:
                logger.error(f"Error generating URL for {file_type}: {e}")
        
        return urls
    
    def delete_job(self, job_id: str, delete_files: bool = True) -> None:
        """Delete a job, its files, and its logs subcollection."""
        job = self.get_job(job_id)

        # Refund credit if job never completed (and wasn't already refunded via fail/cancel)
        if job:
            no_refund_statuses = [JobStatus.COMPLETE, JobStatus.PREP_COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED]
            if job.status not in no_refund_statuses:
                self._refund_credit_for_job(job_id, job, reason="job_deleted")

        if delete_files and job and job.output_files:
            for gcs_path in job.output_files.values():
                try:
                    self.storage.delete_file(gcs_path)
                except Exception as e:
                    logger.error(f"Error deleting file {gcs_path}: {e}")

        # Delete logs subcollection first (must be done before deleting parent doc)
        try:
            deleted_logs = self.firestore.delete_logs_subcollection(job_id)
            if deleted_logs > 0:
                logger.info(f"Deleted {deleted_logs} log entries for job {job_id}")
        except Exception as e:
            logger.warning(f"Error deleting logs subcollection for job {job_id}: {e}")

        # Delete audio edit sessions subcollection
        try:
            deleted_sessions = self.firestore.delete_audio_edit_sessions_subcollection(job_id)
            if deleted_sessions > 0:
                logger.info(f"Deleted {deleted_sessions} audio edit sessions for job {job_id}")
        except Exception as e:
            logger.warning(f"Error deleting audio edit sessions for job {job_id}: {e}")

        self.firestore.delete_job(job_id)
        logger.info(f"Deleted job {job_id}")
    
    def validate_state_transition(
        self,
        job_id: str,
        new_status: JobStatus,
        raise_on_invalid: bool = True
    ) -> bool:
        """
        Validate that a state transition is legal.

        Args:
            job_id: Job ID
            new_status: Target state
            raise_on_invalid: If True, raise InvalidStateTransitionError on invalid
                              transitions. If False, return False (legacy behavior).

        Returns:
            True if transition is valid

        Raises:
            InvalidStateTransitionError: If transition is invalid and raise_on_invalid=True
            ValueError: If job not found and raise_on_invalid=True
        """
        job = self.get_job(job_id)
        if not job:
            error_msg = f"Job {job_id} not found"
            logger.error(error_msg)
            if raise_on_invalid:
                raise ValueError(error_msg)
            return False

        current_status = job.status
        valid_transitions = STATE_TRANSITIONS.get(current_status, [])

        if new_status not in valid_transitions:
            error_msg = (
                f"Invalid state transition for job {job_id}: "
                f"{current_status} -> {new_status}. "
                f"Valid transitions: {[s.value for s in valid_transitions]}"
            )
            logger.error(error_msg)

            if raise_on_invalid:
                raise InvalidStateTransitionError(
                    message=error_msg,
                    job_id=job_id,
                    from_status=current_status,
                    to_status=new_status.value if hasattr(new_status, 'value') else str(new_status),
                    valid_transitions=[s.value for s in valid_transitions]
                )
            return False

        return True

    def transition_to_state(
        self,
        job_id: str,
        new_status: JobStatus,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        state_data_updates: Optional[Dict[str, Any]] = None,
        raise_on_invalid: bool = True,
        timeline_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Transition job to new state with validation.

        Args:
            job_id: Job ID
            new_status: Target state
            progress: Progress percentage (0-100)
            message: Timeline message
            state_data_updates: Updates to state_data field
            raise_on_invalid: If True (default), raise InvalidStateTransitionError
                              on invalid transitions. If False, return False silently.
            timeline_metadata: Optional structured metadata to attach to the timeline event

        Returns:
            True if transition succeeded, False if failed (only when raise_on_invalid=False)

        Raises:
            InvalidStateTransitionError: If transition is invalid and raise_on_invalid=True
        """
        if not self.validate_state_transition(job_id, new_status, raise_on_invalid=raise_on_invalid):
            return False

        updates = {
            'status': new_status,
            'updated_at': datetime.utcnow()
        }

        if progress is not None:
            updates['progress'] = progress

        # Generate review token when entering blocking states that need user action
        # Tokens don't expire - they're job-scoped so low risk, and natural expiry happens when job completes
        if new_status in (JobStatus.AWAITING_REVIEW, JobStatus.AWAITING_AUDIO_EDIT):
            from backend.api.dependencies import generate_review_token
            review_token = generate_review_token()
            updates['review_token'] = review_token
            updates['review_token_expires_at'] = None  # No expiry - token is job-scoped
            logger.info(f"Generated review token for job {job_id} (no expiry)")

        # If we have state_data_updates, merge them with existing state_data
        merged_state_data = None
        if state_data_updates:
            job = self.get_job(job_id)
            if job:
                merged_state_data = {**job.state_data, **state_data_updates}

        # Update job status (includes timeline event), passing state_data if present
        if merged_state_data is not None:
            self.update_job_status(
                job_id=job_id,
                status=new_status,
                progress=progress,
                message=message,
                timeline_metadata=timeline_metadata,
                state_data=merged_state_data
            )
        else:
            self.update_job_status(
                job_id=job_id,
                status=new_status,
                progress=progress,
                message=message,
                timeline_metadata=timeline_metadata,
            )
        
        # Apply review token update separately if generated
        if new_status == JobStatus.AWAITING_REVIEW and 'review_token' in updates:
            self.firestore.update_job(job_id, {
                'review_token': updates['review_token'],
                'review_token_expires_at': updates['review_token_expires_at']
            })

        logger.info(f"Job {job_id} transitioned to {new_status}")

        # Increment user's completed jobs counter on terminal success states
        if new_status in (JobStatus.COMPLETE, JobStatus.PREP_COMPLETE):
            try:
                job = self.get_job(job_id)
                if job and job.user_email:
                    from backend.services.user_service import get_user_service
                    user_service = get_user_service()
                    user_service.increment_jobs_completed(job.user_email)
            except Exception:
                logger.exception(f"Failed to increment jobs_completed for job {job_id}")

        # Trigger notifications asynchronously (fire-and-forget)
        self._trigger_state_notifications(job_id, new_status)

        return True

    def _trigger_state_notifications(self, job_id: str, new_status: JobStatus) -> None:
        """
        Trigger email and push notifications based on state transitions.

        This is fire-and-forget - notification failures don't affect job processing.

        Args:
            job_id: Job ID
            new_status: New job status
        """
        import asyncio

        try:
            # Get the job to access user info
            job = self.get_job(job_id)
            if not job or not job.user_email:
                logger.debug(f"No user email for job {job_id}, skipping notifications")
                return

            # Job completion notification
            if new_status == JobStatus.COMPLETE:
                self._schedule_completion_email(job)
                self._send_push_notification(job, "complete")

            # Idle reminder scheduling for blocking states
            elif new_status in [JobStatus.AWAITING_REVIEW, JobStatus.AWAITING_INSTRUMENTAL_SELECTION, JobStatus.AWAITING_AUDIO_EDIT]:
                self._schedule_idle_reminder(job, new_status)
                # Send push notification for blocking states
                if new_status == JobStatus.AWAITING_AUDIO_EDIT:
                    action_type = "audio_edit"
                elif new_status == JobStatus.AWAITING_REVIEW:
                    action_type = "lyrics"
                else:
                    action_type = "instrumental"
                self._send_push_notification(job, action_type)

        except Exception as e:
            # Never let notification failures affect job processing
            logger.error(f"Error triggering notifications for job {job_id}: {e}")

    def _schedule_completion_email(self, job: Job) -> None:
        """
        Schedule sending a job completion email.

        For made-for-you orders, also transfers ownership from admin to customer.
        Uses asyncio to fire-and-forget the email sending.
        """
        import asyncio
        import threading

        try:
            from backend.services.job_notification_service import get_job_notification_service

            notification_service = get_job_notification_service()

            # Get youtube, dropbox URLs, and brand_code from state_data (may be None)
            state_data = job.state_data or {}
            youtube_url = state_data.get('youtube_url')
            dropbox_url = state_data.get('dropbox_link')
            brand_code = state_data.get('brand_code')
            youtube_queued = state_data.get('youtube_upload_queued', False)

            # For made-for-you orders, send to customer and transfer ownership
            recipient_email = job.user_email
            if job.made_for_you and job.customer_email:
                recipient_email = job.customer_email
                # Transfer ownership from admin to customer (non-blocking - email still goes out if this fails)
                try:
                    self.update_job(job.job_id, {'user_email': job.customer_email})
                    logger.info(f"Transferred ownership of made-for-you job {job.job_id} to {_mask_email(job.customer_email)}")
                except Exception as e:
                    logger.error(f"Failed to transfer ownership for job {job.job_id}: {e}")

            # Create async task (fire-and-forget)
            async def send_email():
                await notification_service.send_job_completion_email(
                    job_id=job.job_id,
                    user_email=recipient_email,
                    user_name=None,  # Could fetch from user service if needed
                    artist=job.artist,
                    title=job.title,
                    youtube_url=youtube_url,
                    dropbox_url=dropbox_url,
                    brand_code=brand_code,
                    is_private=getattr(job, 'is_private', False),
                    youtube_queued=youtube_queued,
                )

            # Try to get existing event loop, create new one if none exists
            try:
                loop = asyncio.get_running_loop()
                # If we're in an async context, create a task
                loop.create_task(send_email())
            except RuntimeError:
                # No event loop - we're likely in a sync context
                # Use daemon thread to avoid blocking job completion
                def run_in_thread():
                    asyncio.run(send_email())
                thread = threading.Thread(target=run_in_thread, daemon=True)
                thread.start()

            logger.info(f"Scheduled completion email for job {job.job_id}")

        except Exception as e:
            logger.error(f"Failed to schedule completion email for job {job.job_id}: {e}")

    def _schedule_idle_reminder(self, job: Job, new_status: JobStatus) -> None:
        """
        Schedule an idle reminder for a blocking state.

        Records the timestamp when the blocking state was entered and
        schedules a Cloud Tasks task for 5 minutes later.
        """
        import asyncio
        import threading

        try:
            # Record when we entered blocking state (for idle detection)
            blocking_entered_at = datetime.utcnow().isoformat()

            if new_status == JobStatus.AWAITING_AUDIO_EDIT:
                action_type = "audio_edit"
            elif new_status == JobStatus.AWAITING_REVIEW:
                action_type = "lyrics"
            else:
                action_type = "instrumental"

            # Update state_data with blocking state info (handle None state_data)
            existing_state_data = job.state_data or {}
            state_data_update = {
                'blocking_state_entered_at': blocking_entered_at,
                'blocking_action_type': action_type,
                'reminder_sent': False,  # Will be set to True after reminder is sent
            }

            self.firestore.update_job(job.job_id, {
                'state_data': {**existing_state_data, **state_data_update}
            })

            # Schedule the idle reminder check via worker service (5 min delay)
            from backend.services.worker_service import get_worker_service

            async def schedule_reminder():
                worker_service = get_worker_service()
                await worker_service.schedule_idle_reminder(job.job_id)

            # Try to get existing event loop, create new one if none exists
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(schedule_reminder())
            except RuntimeError:
                # No event loop - we're in a sync context
                # Use daemon thread to avoid blocking job processing
                def run_in_thread():
                    asyncio.run(schedule_reminder())
                thread = threading.Thread(target=run_in_thread, daemon=True)
                thread.start()

            logger.info(f"Scheduled idle reminder for job {job.job_id} ({action_type})")

        except Exception as e:
            logger.error(f"Failed to schedule idle reminder for job {job.job_id}: {e}")

    def _send_push_notification(self, job: Job, action_type: str) -> None:
        """
        Send a push notification for job state changes.

        Fire-and-forget - failures don't affect job processing.

        Args:
            job: Job object
            action_type: Type of notification ("lyrics", "instrumental", or "complete")
        """
        import asyncio
        import threading

        try:
            from backend.services.push_notification_service import get_push_notification_service

            push_service = get_push_notification_service()

            # Skip if push notifications not enabled
            if not push_service.is_enabled():
                logger.debug("Push notifications not enabled, skipping")
                return

            # Build job dict for notification service
            job_dict = {
                "job_id": job.job_id,
                "user_email": job.user_email,
                "artist": job.artist,
                "title": job.title,
                "tenant_id": job.tenant_id or None,
            }

            async def send_notification():
                if action_type == "complete":
                    await push_service.send_completion_notification(job_dict)
                else:
                    await push_service.send_blocking_notification(job_dict, action_type)

            # Try to get existing event loop, create new one if none exists
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(send_notification())
            except RuntimeError:
                # No event loop - we're in a sync context
                def run_in_thread():
                    asyncio.run(send_notification())
                thread = threading.Thread(target=run_in_thread, daemon=True)
                thread.start()

            logger.debug(f"Scheduled push notification for job {job.job_id} ({action_type})")

        except Exception as e:
            logger.error(f"Failed to send push notification for job {job.job_id}: {e}")

    def update_state_data(self, job_id: str, key: str, value: Any) -> None:
        """
        Update a specific key in the job's state_data field.

        This is used by workers to store stage-specific metadata.
        """
        job = self.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        state_data = job.state_data.copy()
        state_data[key] = value

        self.update_job(job_id, {'state_data': state_data})
        logger.debug(f"Job {job_id} state_data updated: {key} = {value}")

    def delete_state_data_key(self, job_id: str, key: str) -> bool:
        """
        Delete a specific key from the job's state_data field.

        Uses Firestore DELETE_FIELD to atomically remove the key.
        This is used to clear worker progress markers, allowing workers
        to re-run without skipping due to idempotency checks.

        Args:
            job_id: Job ID
            key: The state_data key to delete (e.g., "render_progress")

        Returns:
            True if successful, False otherwise
        """
        from google.cloud.firestore_v1 import DELETE_FIELD

        try:
            job_ref = self.firestore.db.collection("jobs").document(job_id)
            job_ref.update({f"state_data.{key}": DELETE_FIELD})
            logger.debug(f"Job {job_id}: Deleted state_data key '{key}'")
            return True
        except Exception as e:
            logger.error(f"Job {job_id}: Failed to delete state_data key '{key}': {e}")
            return False

    def delete_state_data_keys(self, job_id: str, keys: List[str]) -> List[str]:
        """
        Delete multiple keys from the job's state_data field in a single operation.

        Uses Firestore DELETE_FIELD to atomically remove the keys.

        Args:
            job_id: Job ID
            keys: List of state_data keys to delete

        Returns:
            List of keys that were successfully deleted
        """
        from google.cloud.firestore_v1 import DELETE_FIELD

        if not keys:
            return []

        try:
            job_ref = self.firestore.db.collection("jobs").document(job_id)
            update_payload = {f"state_data.{key}": DELETE_FIELD for key in keys}
            job_ref.update(update_payload)
            logger.info(f"Job {job_id}: Deleted state_data keys: {keys}")
            return keys
        except Exception as e:
            logger.error(f"Job {job_id}: Failed to delete state_data keys {keys}: {e}")
            return []
    
    def _refund_credit_for_job(self, job_id: str, job: Job, reason: str) -> bool:
        """
        Refund one credit for a job if eligible.

        Skips refund if: already refunded, no user email, or admin-owned job.
        Never raises — refund failure must not block the calling operation.

        Returns:
            True if credit was refunded, False otherwise.
        """
        try:
            if job.credit_refunded:
                logger.info(f"Credit already refunded for job {job_id}, skipping")
                return False

            if not job.user_email:
                return False

            from backend.services.auth_service import is_admin_email
            from backend.services.user_service import get_user_service

            if is_admin_email(job.user_email):
                return False

            user_service = get_user_service()
            refund_ok, new_balance, refund_msg = user_service.refund_credit(
                job.user_email, job_id, reason=reason
            )
            if refund_ok:
                # Mark job as refunded to prevent double-refund
                self.update_job(job_id, {'credit_refunded': True})
                logger.info(f"Refunded credit for {reason} job {job_id} to {_mask_email(job.user_email)} (balance: {new_balance})")
                return True
            else:
                logger.warning(f"Credit refund failed for job {job_id}: {refund_msg}")
                return False
        except Exception as e:
            logger.warning(f"Error refunding credit for job {job_id}: {e}")
            return False

    def fail_job(self, job_id: str, error_message: str, error_details: Optional[Dict[str, Any]] = None) -> bool:
        """
        Mark a job as failed with error information.
        
        Args:
            job_id: Job ID
            error_message: Human-readable error message
            error_details: Optional structured error details
            
        Returns:
            True if successful
        """
        try:
            # Update error fields
            self.update_job(job_id, {
                'error_message': error_message,
                'error_details': error_details or {}
            })
            
            # Use update_job_status which handles timeline
            self.update_job_status(
                job_id=job_id,
                status=JobStatus.FAILED,
                message=error_message
            )
            
            logger.error(f"Job {job_id} failed: {error_message}")

            # Auto-refund credit on failure
            job = self.get_job(job_id)
            if job:
                self._refund_credit_for_job(job_id, job, reason="job_failed")

            return True
        except Exception as e:
            logger.error(f"Error marking job {job_id} as failed: {e}")
            return False

    def update_file_url(self, job_id: str, category: str, file_type: str, url: str) -> None:
        """
        Update a file URL in the job's file_urls structure.
        
        Args:
            job_id: Job ID
            category: Category (e.g., "stems", "lyrics", "finals")
            file_type: File type within category (e.g., "clean", "lrc", "lossless_4k_mp4")
            url: GCS URL
        """
        job = self.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return
        
        file_urls = job.file_urls.copy()
        if category not in file_urls:
            file_urls[category] = {}
        
        file_urls[category][file_type] = url
        
        self.update_job(job_id, {'file_urls': file_urls})
        logger.debug(f"Job {job_id} file URL updated: {category}.{file_type}")
    
    def check_parallel_processing_complete(self, job_id: str) -> bool:
        """
        Check if both parallel tracks (audio + lyrics) are complete.
        
        This is called after audio_complete or lyrics_complete to determine
        if we can proceed to screen generation.
        
        Returns:
            True if both tracks complete, False otherwise
        """
        job = self.get_job(job_id)
        if not job:
            return False
        
        audio_complete = job.state_data.get('audio_complete', False)
        lyrics_complete = job.state_data.get('lyrics_complete', False)
        
        return audio_complete and lyrics_complete

    async def start_job_processing(
        self,
        job_id: str,
        progress: int = 15,
        message: str = "Starting audio and lyrics processing"
    ) -> None:
        """
        Transition job to DOWNLOADING and trigger audio + lyrics workers.

        This is the single entry point for starting job processing after audio
        is available. All handlers (file_upload, webhook, audio_search) should
        use this method to ensure consistent state transitions.

        The method:
        1. Validates job exists and has input_media_gcs_path
        2. Transitions to DOWNLOADING status (raises if invalid)
        3. Triggers audio and lyrics workers in parallel

        Args:
            job_id: Job ID to start processing
            progress: Progress percentage to set (default 15)
            message: Timeline message (default "Starting audio and lyrics processing")

        Raises:
            InvalidStateTransitionError: If job can't transition to DOWNLOADING
            ValueError: If job not found or missing input_media_gcs_path

        Example:
            # After downloading audio to GCS:
            job_manager.update_job(job_id, {'input_media_gcs_path': audio_path})
            await job_manager.start_job_processing(job_id)
        """
        import asyncio
        from backend.services.worker_service import get_worker_service

        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if not job.input_media_gcs_path:
            raise ValueError(
                f"Job {job_id} missing input_media_gcs_path. "
                "Audio must be downloaded to GCS before starting processing."
            )

        # Transition to DOWNLOADING - this will raise if invalid
        self.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.DOWNLOADING,
            progress=progress,
            message=message
        )

        # Check if audio separation can be skipped
        # Jobs with an existing instrumental (e.g. tenant uploads) don't need
        # GPU-powered audio separation — saves 5-10 minutes of processing time
        worker_service = get_worker_service()

        if job.existing_instrumental_gcs_path:
            logger.info(f"Job {job_id}: Skipping audio separation (existing instrumental provided)")
            # Mark audio as immediately complete and only trigger lyrics worker
            self.update_state_data(job_id, 'audio_complete', True)
            await worker_service.trigger_lyrics_worker(job_id)
            logger.info(f"Job {job_id}: Started processing (lyrics worker only, audio separation skipped)")
        else:
            # Trigger both workers in parallel
            await asyncio.gather(
                worker_service.trigger_audio_worker(job_id),
                worker_service.trigger_lyrics_worker(job_id)
            )
            logger.info(f"Job {job_id}: Started processing (audio + lyrics workers triggered)")

    def mark_audio_complete(self, job_id: str) -> None:
        """
        Mark audio processing as complete and check if can proceed.
        
        If lyrics are also complete, automatically triggers screens worker.
        """
        self.update_state_data(job_id, 'audio_complete', True)
        
        if self.check_parallel_processing_complete(job_id):
            logger.info(f"Job {job_id}: Both audio and lyrics complete, triggering screens worker")
            # Transition happens in screens worker
            # We just trigger it here
            self._trigger_screens_worker(job_id)
    
    def mark_lyrics_complete(self, job_id: str) -> None:
        """
        Mark lyrics processing as complete and check if can proceed.
        
        If audio is also complete, automatically triggers screens worker.
        """
        self.update_state_data(job_id, 'lyrics_complete', True)
        
        if self.check_parallel_processing_complete(job_id):
            logger.info(f"Job {job_id}: Both audio and lyrics complete, triggering screens worker")
            # Transition happens in screens worker
            # We just trigger it here
            self._trigger_screens_worker(job_id)
    
    def _trigger_screens_worker(self, job_id: str) -> None:
        """
        Trigger screens generation worker.
        
        Uses WorkerService to make HTTP call to internal API.
        This must be async, so we use asyncio to create a task.
        """
        import asyncio
        from backend.services.worker_service import get_worker_service
        
        logger.info(f"Job {job_id}: Triggering screens worker")
        
        # Create async task to trigger worker
        # This allows us to call async code from sync context
        async def _trigger():
            worker_service = get_worker_service()
            await worker_service.trigger_screens_worker(job_id)
        
        # Create task in event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running, create task
                asyncio.create_task(_trigger())
            else:
                # If no loop, run directly
                asyncio.run(_trigger())
        except RuntimeError:
            # Fallback: just log
            logger.warning(f"Job {job_id}: Could not trigger screens worker (no event loop)")
            # TODO: In production, use message queue instead
    
    def cancel_job(self, job_id: str, reason: Optional[str] = None) -> bool:
        """
        Cancel a job.
        
        Only jobs in non-terminal states can be cancelled.
        """
        job = self.get_job(job_id)
        if not job:
            return False
        
        terminal_states = [JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED]
        if job.status in terminal_states:
            logger.warning(f"Cannot cancel job {job_id} in terminal state {job.status}")
            return False
        
        message = f"Job cancelled{f': {reason}' if reason else ''}"
        
        self.update_job_status(
            job_id=job_id,
            status=JobStatus.CANCELLED,
            message=message
        )
        
        logger.info(f"Job {job_id} cancelled")

        # Refund credit for cancelled job
        self._refund_credit_for_job(job_id, job, reason="job_cancelled")

        return True

    def mark_job_failed(
        self,
        job_id: str,
        error_message: str,
        error_details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Mark a job as failed with error details.
        
        This replaces mark_job_error() with better error tracking.
        """
        updates = {
            'error_message': error_message
        }
        
        if error_details:
            updates['error_details'] = error_details
        
        self.firestore.update_job_status(
            job_id=job_id,
            status=JobStatus.FAILED,
            progress=0,
            message=f"Failed: {error_message}",
            **updates
        )
        logger.error(f"Job {job_id} failed: {error_message}")
    
    def increment_retry_count(self, job_id: str) -> int:
        """
        Increment retry count for a job.
        
        Returns:
            New retry count
        """
        job = self.get_job(job_id)
        if not job:
            return 0
        
        new_count = job.retry_count + 1
        self.update_job(job_id, {'retry_count': new_count})
        return new_count
    
    def append_worker_log(
        self,
        job_id: str,
        worker: str,
        level: str,
        message: str,
        max_logs: int = 500,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Append a log entry to the job's logs.

        By default (USE_LOG_SUBCOLLECTION=true), logs are stored in a Firestore
        subcollection (jobs/{job_id}/logs) to avoid the 1MB document size limit.

        For backward compatibility (USE_LOG_SUBCOLLECTION=false), logs are stored
        in the embedded worker_logs array using atomic ArrayUnion.

        Args:
            job_id: Job ID
            worker: Worker name (audio, lyrics, screens, video, render, distribution)
            level: Log level (DEBUG, INFO, WARNING, ERROR)
            message: Log message
            max_logs: Not used (kept for API compatibility)
            metadata: Optional additional metadata dict
        """
        if settings.use_log_subcollection:
            # New subcollection approach - avoids 1MB limit
            log_entry = WorkerLogEntry.create(
                job_id=job_id,
                worker=worker,
                level=level,
                message=message,
                metadata=metadata
            )
            self.firestore.append_log_to_subcollection(job_id, log_entry)
        else:
            # Legacy embedded array approach
            log_entry = {
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'level': level,
                'worker': worker,
                'message': message[:1000]  # Truncate long messages
            }
            self.firestore.append_worker_log(job_id, log_entry)
    
    def get_worker_logs(
        self,
        job_id: str,
        since_index: int = 0,
        worker: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get worker logs for a job, optionally filtered by worker and index.

        By default (USE_LOG_SUBCOLLECTION=true), logs are read from the
        subcollection. Falls back to embedded array for older jobs.

        Args:
            job_id: Job ID
            since_index: Return only logs after this index (for pagination)
            worker: Filter by worker name (optional)

        Returns:
            List of log entries as dicts (in legacy format for API compatibility)
        """
        if settings.use_log_subcollection:
            # Try subcollection first
            subcollection_logs = self.firestore.get_logs_from_subcollection(
                job_id=job_id,
                offset=since_index,
                worker=worker,
                limit=500
            )
            if subcollection_logs:
                # Convert to legacy format for API compatibility
                return [log.to_legacy_dict() for log in subcollection_logs]
            # Fall through to check embedded array for older jobs

        # Embedded array approach (legacy jobs or fallback)
        job = self.get_job(job_id)
        if not job or not job.worker_logs:
            return []

        logs = [log.dict() if hasattr(log, 'dict') else log for log in job.worker_logs]

        # Filter by index
        if since_index > 0:
            logs = logs[since_index:]

        # Filter by worker
        if worker:
            logs = [log for log in logs if log.get('worker') == worker]

        return logs

    def get_worker_logs_count(self, job_id: str) -> int:
        """
        Get the total count of worker logs for a job.

        Args:
            job_id: Job ID

        Returns:
            Total count of logs
        """
        if settings.use_log_subcollection:
            # Try subcollection first
            count = self.firestore.get_logs_count_from_subcollection(job_id)
            if count > 0:
                return count
            # Fall through to check embedded array

        # Embedded array (legacy jobs or fallback)
        job = self.get_job(job_id)
        if not job or not job.worker_logs:
            return 0
        return len(job.worker_logs)

