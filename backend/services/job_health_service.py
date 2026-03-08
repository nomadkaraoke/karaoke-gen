"""
Job health and consistency checking service.

Provides functions to detect state inconsistencies in jobs that might indicate
bugs in the state machine or trigger logic.

Created as part of state machine robustness improvements (2026-02-02).
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from backend.models.job import Job, JobStatus


logger = logging.getLogger(__name__)


# Jobs in these statuses should NOT have audio_complete flag set
# (unless they're actively processing or beyond)
STATUSES_BEFORE_AUDIO_START = {
    JobStatus.PENDING,
    JobStatus.SEARCHING_AUDIO,
    JobStatus.AWAITING_AUDIO_SELECTION,
    JobStatus.AWAITING_AUDIO_EDIT,
    JobStatus.IN_AUDIO_EDIT,
    JobStatus.AUDIO_EDIT_COMPLETE,
}

# Jobs in these statuses should NOT have lyrics_complete flag set
STATUSES_BEFORE_LYRICS_START = {
    JobStatus.PENDING,
    JobStatus.SEARCHING_AUDIO,
    JobStatus.AWAITING_AUDIO_SELECTION,
    JobStatus.AWAITING_AUDIO_EDIT,
    JobStatus.IN_AUDIO_EDIT,
    JobStatus.AUDIO_EDIT_COMPLETE,
}

# Statuses where screens_progress.stage='complete' is valid
STATUSES_AFTER_SCREENS_COMPLETE = {
    JobStatus.AWAITING_REVIEW,
    JobStatus.IN_REVIEW,
    JobStatus.REVIEW_COMPLETE,
    JobStatus.RENDERING_VIDEO,
    JobStatus.AWAITING_INSTRUMENTAL_SELECTION,  # Legacy
    JobStatus.INSTRUMENTAL_SELECTED,
    JobStatus.GENERATING_VIDEO,
    JobStatus.ENCODING,
    JobStatus.PACKAGING,
    JobStatus.UPLOADING,
    JobStatus.NOTIFYING,
    JobStatus.COMPLETE,
    JobStatus.PREP_COMPLETE,
}


def check_job_consistency(job: Job) -> List[str]:
    """
    Check for state inconsistencies in a job.

    This function detects situations where state_data flags indicate
    progress that doesn't match the job status - which usually indicates
    a bug in the state machine or trigger logic.

    Args:
        job: Job object to check

    Returns:
        List of issue descriptions found. Empty list means no issues.
    """
    issues = []
    state_data = job.state_data or {}

    # Normalize status to JobStatus enum if it's a string
    status = job.status
    if isinstance(status, str):
        try:
            status = JobStatus(status)
        except ValueError:
            issues.append(f"invalid_status: '{job.status}' is not a valid JobStatus")
            return issues

    # Check: audio_complete=True but status still in pre-audio states
    if state_data.get('audio_complete') and status in STATUSES_BEFORE_AUDIO_START:
        issues.append(
            f"audio_complete_status_mismatch: audio_complete=True but status={status.value}"
        )

    # Check: lyrics_complete=True but status still in pre-lyrics states
    if state_data.get('lyrics_complete') and status in STATUSES_BEFORE_LYRICS_START:
        issues.append(
            f"lyrics_complete_status_mismatch: lyrics_complete=True but status={status.value}"
        )

    # Check: screens_progress.stage='complete' but status not in post-screens states
    screens_progress = state_data.get('screens_progress', {})
    if isinstance(screens_progress, dict) and screens_progress.get('stage') == 'complete':
        if status not in STATUSES_AFTER_SCREENS_COMPLETE:
            issues.append(
                f"screens_complete_status_mismatch: screens_progress.stage=complete but status={status.value}"
            )

    # Check: job has input_media_gcs_path but is stuck at pending
    # This was the exact bug that got jobs 06cfea29 and 984da08b stuck
    if job.input_media_gcs_path and status == JobStatus.PENDING:
        # This could be legitimate if job was just created and workers haven't started
        # But if it's been more than a few minutes, it's likely stuck
        # We flag it as a warning, not an error
        issues.append(
            f"pending_with_input_media: has input_media_gcs_path but status=pending (may be stuck)"
        )

    # Check: job stuck in encoding status beyond 50 minutes
    # Encoding timeout is 1 hour; if updated_at hasn't advanced in 50 min,
    # the poller likely died (e.g., Cloud Run deployment killed the instance)
    if status == JobStatus.ENCODING and job.updated_at:
        now = datetime.now(timezone.utc)
        updated_at = job.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        encoding_age = now - updated_at
        if encoding_age > timedelta(minutes=50):
            minutes = int(encoding_age.total_seconds() / 60)
            issues.append(
                f"encoding_stuck: status=encoding for {minutes} min without update (updated_at={updated_at.isoformat()})"
            )

    # Check: job stuck in downloading_audio status beyond 10 minutes
    # Audio downloads typically complete in 30s-5min. If stuck longer,
    # the Cloud Run Job likely failed silently.
    if status == JobStatus.DOWNLOADING_AUDIO and job.updated_at:
        now = datetime.now(timezone.utc)
        updated_at = job.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        download_age = now - updated_at
        if download_age > timedelta(minutes=10):
            minutes = int(download_age.total_seconds() / 60)
            issues.append(
                f"downloading_audio_stuck: status=downloading_audio for {minutes} min without update (updated_at={updated_at.isoformat()})"
            )

    return issues


def check_job_consistency_detailed(job: Job) -> Dict[str, Any]:
    """
    Check job consistency with detailed information for debugging.

    Returns a dictionary with:
    - job_id: The job ID
    - status: Current job status
    - issues: List of issue descriptions
    - state_data_summary: Summary of relevant state_data fields
    - is_healthy: Boolean indicating if no issues were found

    Args:
        job: Job object to check

    Returns:
        Dictionary with detailed consistency information
    """
    issues = check_job_consistency(job)
    state_data = job.state_data or {}

    return {
        "job_id": job.job_id,
        "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
        "issues": issues,
        "state_data_summary": {
            "audio_complete": state_data.get('audio_complete', False),
            "lyrics_complete": state_data.get('lyrics_complete', False),
            "screens_progress_stage": state_data.get('screens_progress', {}).get('stage'),
            "has_input_media_gcs_path": bool(job.input_media_gcs_path),
        },
        "is_healthy": len(issues) == 0,
    }


def get_worker_valid_statuses() -> Dict[str, List[JobStatus]]:
    """
    Get mapping of worker names to their valid job statuses.

    This is used by workers to validate they should run for a given job.

    Returns:
        Dictionary mapping worker name to list of valid JobStatus values
    """
    return {
        "audio_worker": [
            JobStatus.DOWNLOADING,
            JobStatus.DOWNLOADING_AUDIO,
            JobStatus.SEPARATING_STAGE1,
            JobStatus.SEPARATING_STAGE2,
        ],
        "lyrics_worker": [
            JobStatus.DOWNLOADING,
            JobStatus.TRANSCRIBING,
            JobStatus.CORRECTING,
        ],
        "screens_worker": [
            # Screens worker can be triggered when both audio+lyrics complete
            # which can happen in any of these states
            JobStatus.DOWNLOADING,
            JobStatus.AUDIO_COMPLETE,
            JobStatus.LYRICS_COMPLETE,
            # Also valid if re-running after a failure
            JobStatus.GENERATING_SCREENS,
        ],
        "video_worker": [
            JobStatus.INSTRUMENTAL_SELECTED,
            JobStatus.REVIEW_COMPLETE,
            JobStatus.RENDERING_VIDEO,
            JobStatus.GENERATING_VIDEO,
        ],
        "render_video_worker": [
            JobStatus.REVIEW_COMPLETE,
            JobStatus.RENDERING_VIDEO,
        ],
    }


def validate_worker_can_run(worker_name: str, job: Job) -> Optional[str]:
    """
    Validate that a worker should run for the given job.

    Args:
        worker_name: Name of the worker (e.g., "screens_worker")
        job: Job object to validate

    Returns:
        None if valid, or an error message string if invalid
    """
    valid_statuses_map = get_worker_valid_statuses()

    if worker_name not in valid_statuses_map:
        # Unknown worker, can't validate
        return None

    valid_statuses = valid_statuses_map[worker_name]

    # Normalize status
    status = job.status
    if isinstance(status, str):
        try:
            status = JobStatus(status)
        except ValueError:
            return f"Invalid job status: {job.status}"

    if status not in valid_statuses:
        valid_names = [s.value for s in valid_statuses]
        return (
            f"{worker_name} called but job status is {status.value}, "
            f"expected one of {valid_names}. This may indicate a bug in the trigger logic."
        )

    return None
