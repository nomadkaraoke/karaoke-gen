"""
Stale review processor.

Detects jobs stuck in review (awaiting_review / in_review) and takes action:
- 24h: sends a gentle reminder email with expiry warning
- 48h: auto-cancels the job and refunds the user's credit

Called by Cloud Scheduler via an internal endpoint (hourly).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from backend.models.job import JobStatus
from backend.services.email_service import get_email_service
from backend.services.firestore_service import FirestoreService
from backend.services.job_manager import JobManager


logger = logging.getLogger(__name__)

# Configurable thresholds (hours since blocking_state_entered_at)
REVIEW_REMINDER_HOURS = 24
REVIEW_EXPIRY_HOURS = 48


async def process_stale_reviews() -> Dict[str, Any]:
    """
    Query for stale review jobs and take action.

    - Jobs in review for >= 48h are auto-cancelled with credit refund
    - Jobs in review for >= 24h (but < 48h) get a reminder email

    Excludes made-for-you and tenant jobs.

    Returns:
        Summary dict with counts and any errors encountered.
    """
    job_manager = JobManager()
    firestore = FirestoreService()
    email_service = get_email_service()

    reminders_sent = 0
    jobs_expired = 0
    errors = []

    # Query for jobs in both blocking review states
    stale_jobs = []
    for status in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        try:
            jobs = firestore.list_jobs(status=status, limit=500)
            stale_jobs.extend(jobs)
        except Exception as e:
            error_msg = f"Failed to query jobs with status {status.value}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    if not stale_jobs:
        logger.info("No stale review jobs found")
        return {
            "status": "completed",
            "reminders_sent": 0,
            "jobs_expired": 0,
            "errors": errors,
        }

    logger.info(f"Found {len(stale_jobs)} jobs in review states, checking for stale ones")

    now = datetime.now(timezone.utc)

    for job in stale_jobs:
        try:
            # Skip made-for-you jobs (admin-controlled)
            if getattr(job, 'made_for_you', False):
                continue

            # Skip tenant/white-label jobs (B2B partners)
            if getattr(job, 'tenant_id', '') and job.tenant_id:
                continue

            # Get blocking state entry time from state_data
            state_data = job.state_data or {}
            blocking_entered_at_str = state_data.get('blocking_state_entered_at')
            if not blocking_entered_at_str:
                continue

            # Parse the naive UTC ISO string and make it timezone-aware
            try:
                blocking_entered_at = datetime.fromisoformat(blocking_entered_at_str)
                if blocking_entered_at.tzinfo is None:
                    blocking_entered_at = blocking_entered_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                logger.warning(
                    f"Job {job.job_id}: invalid blocking_state_entered_at: {blocking_entered_at_str}"
                )
                continue

            hours_elapsed = (now - blocking_entered_at).total_seconds() / 3600

            if hours_elapsed >= REVIEW_EXPIRY_HOURS:
                # Auto-expire: cancel job (which refunds credit)
                logger.info(
                    f"Job {job.job_id}: review stale for {hours_elapsed:.1f}h (>={REVIEW_EXPIRY_HOURS}h), "
                    f"auto-expiring"
                )
                cancelled = job_manager.cancel_job(
                    job.job_id,
                    reason="Review not completed within 48 hours"
                )
                if cancelled:
                    jobs_expired += 1

                    # Send expiry notification email
                    if job.user_email:
                        try:
                            from backend.services.user_service import get_user_service
                            user_service = get_user_service()
                            credits_balance = user_service.check_credits(job.user_email)

                            # Look up user locale for email
                            user_locale = "en"
                            try:
                                user = user_service.get_user(job.user_email)
                                if user and user.locale:
                                    user_locale = user.locale
                            except Exception:
                                pass

                            email_service.send_review_expired(
                                to_email=job.user_email,
                                artist=job.artist,
                                title=job.title,
                                credits_balance=credits_balance,
                                locale=user_locale,
                            )
                        except Exception as email_err:
                            logger.error(
                                f"Job {job.job_id}: failed to send expiry email: {email_err}"
                            )
                else:
                    logger.warning(f"Job {job.job_id}: cancel_job returned False")

            elif hours_elapsed >= REVIEW_REMINDER_HOURS:
                # Check if expiry reminder was already sent
                if state_data.get('expiry_reminder_sent'):
                    continue

                logger.info(
                    f"Job {job.job_id}: review stale for {hours_elapsed:.1f}h (>={REVIEW_REMINDER_HOURS}h), "
                    f"sending reminder"
                )

                # Send reminder email
                if job.user_email:
                    try:
                        # Look up user locale for email
                        user_locale = "en"
                        try:
                            from backend.services.user_service import get_user_service
                            user_service = get_user_service()
                            user = user_service.get_user(job.user_email)
                            if user and user.locale:
                                user_locale = user.locale
                        except Exception:
                            pass

                        email_service.send_review_reminder(
                            to_email=job.user_email,
                            artist=job.artist,
                            title=job.title,
                            job_id=job.job_id,
                            locale=user_locale,
                        )
                    except Exception as email_err:
                        logger.error(
                            f"Job {job.job_id}: failed to send reminder email: {email_err}"
                        )

                # Update state_data to mark reminder as sent (even if email failed,
                # to avoid retrying every hour)
                try:
                    firestore.update_job(job.job_id, {
                        'state_data.expiry_reminder_sent': True,
                        'state_data.expiry_reminder_sent_at': datetime.now(timezone.utc).isoformat(),
                    })
                except Exception as update_err:
                    logger.error(
                        f"Job {job.job_id}: failed to update expiry_reminder_sent: {update_err}"
                    )

                reminders_sent += 1

        except Exception as e:
            error_msg = f"Job {job.job_id}: unexpected error: {e}"
            logger.exception(error_msg)
            errors.append(error_msg)

    logger.info(
        f"Stale review processing complete: "
        f"{reminders_sent} reminders sent, {jobs_expired} jobs expired, "
        f"{len(errors)} errors"
    )

    return {
        "status": "completed",
        "reminders_sent": reminders_sent,
        "jobs_expired": jobs_expired,
        "errors": errors,
    }
