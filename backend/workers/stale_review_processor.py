"""
Stale review processor.

Detects jobs stuck in review (awaiting_review / in_review) or awaiting duration
confirmation (awaiting_duration_confirm) and takes action:
- 24h: sends a gentle reminder email with expiry warning
- 48h: auto-cancels the job and refunds the user's credits

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

# Statuses treated as "review" states (1-credit refund via cancel_job)
_REVIEW_STATUSES = {JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW}

# Statuses treated as "duration confirm" states (credits_charged refund)
_DURATION_CONFIRM_STATUSES = {JobStatus.AWAITING_DURATION_CONFIRM}


async def process_stale_reviews() -> Dict[str, Any]:
    """
    Query for stale review / duration-confirm jobs and take action.

    - Jobs in these states for >= 48h are auto-cancelled with credit refund
    - Jobs in these states for >= 24h (but < 48h) get a reminder email

    Review states (AWAITING_REVIEW, IN_REVIEW):
        - 24h reminder: send_review_reminder
        - 48h expiry: cancel_job (auto-refunds 1 credit) + send_review_expired

    Duration-confirm state (AWAITING_DURATION_CONFIRM):
        - 24h reminder: send_duration_confirm_reminder
        - 48h expiry: add_credits(credits_charged) + cancel_job + send_duration_confirm_expired
          The full credits_charged amount (not flat 1) is refunded.

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

    # Query for jobs in all blocking states
    stale_jobs = []
    for status in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW, JobStatus.AWAITING_DURATION_CONFIRM]:
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

            # Skip requests-board community picks — their ownership handoff worker
            # (community_handoff) owns their review lifecycle; auto-cancel+refund
            # here would kill a track voters are still waiting on.
            if state_data.get('community_request_id'):
                continue
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

            is_duration_confirm = job.status in _DURATION_CONFIRM_STATUSES

            if hours_elapsed >= REVIEW_EXPIRY_HOURS:
                if is_duration_confirm:
                    # Duration-confirm expiry: refund full credits_charged, then cancel.
                    # Check idempotency flag first.
                    if state_data.get('duration_confirm_expiry_processed'):
                        continue

                    logger.info(
                        f"Job {job.job_id}: duration-confirm stale for {hours_elapsed:.1f}h "
                        f"(>={REVIEW_EXPIRY_HOURS}h), auto-expiring with credits_charged refund"
                    )

                    credits_charged = state_data.get('credits_charged', 0) or 0
                    credits_refunded = 0

                    # Refund the full charged amount (not flat 1) BEFORE cancelling.
                    # We also mark credit_refunded=True on the job first so that
                    # cancel_job's built-in _refund_credit_for_job skips its 1-credit
                    # refund and we don't double-charge the user.
                    if credits_charged > 0 and job.user_email:
                        try:
                            from backend.services.user_service import get_user_service
                            user_service = get_user_service()
                            ok, _, _ = user_service.add_credits(
                                job.user_email,
                                amount=credits_charged,
                                reason="duration_confirm_expired",
                                job_id=job.job_id,
                            )
                            if ok:
                                credits_refunded = credits_charged
                                # Prevent cancel_job's internal 1-credit refund
                                firestore.update_job(job.job_id, {'credit_refunded': True})
                        except Exception as refund_err:
                            logger.error(
                                f"Job {job.job_id}: failed to refund {credits_charged} credits: {refund_err}"
                            )

                    cancelled = job_manager.cancel_job(
                        job.job_id,
                        reason="Duration confirmation not completed within 48 hours",
                    )
                    if cancelled:
                        jobs_expired += 1

                        # Set idempotency flag AFTER a successful cancel so that a
                        # cancel_job failure leaves the job un-flagged and re-processable
                        # on the next run (the refund is idempotent via add_credits).
                        try:
                            firestore.update_job(job.job_id, {
                                'state_data.duration_confirm_expiry_processed': True,
                                'state_data.duration_confirm_expiry_processed_at': datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception as flag_err:
                            logger.error(
                                f"Job {job.job_id}: failed to set duration_confirm_expiry_processed: {flag_err}"
                            )

                        if job.user_email:
                            try:
                                email_service.send_duration_confirm_expired(
                                    to_email=job.user_email,
                                    artist=job.artist,
                                    title=job.title,
                                    credits_refunded=credits_refunded,
                                    locale="en",
                                )
                            except Exception as email_err:
                                logger.error(
                                    f"Job {job.job_id}: failed to send duration_confirm_expired email: {email_err}"
                                )
                    else:
                        logger.warning(f"Job {job.job_id}: cancel_job returned False")

                else:
                    # Review-state expiry: cancel job (auto-refunds 1 credit via cancel_job)
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
                    f"Job {job.job_id}: stale for {hours_elapsed:.1f}h (>={REVIEW_REMINDER_HOURS}h), "
                    f"sending reminder"
                )

                # Send reminder email (different email per status)
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

                        if is_duration_confirm:
                            email_service.send_duration_confirm_reminder(
                                to_email=job.user_email,
                                job_id=job.job_id,
                                artist=job.artist,
                                title=job.title,
                                locale=user_locale,
                            )
                        else:
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
