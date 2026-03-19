"""
Unit tests for stale review processor.

Tests the logic for detecting stale review jobs, sending reminder emails,
and auto-expiring jobs that exceed the 48h threshold.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, patch

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())

from backend.models.job import JobStatus


def _make_job(
    job_id="job-123",
    status=JobStatus.AWAITING_REVIEW,
    hours_ago=30,
    user_email="user@test.com",
    artist="Test Artist",
    title="Test Song",
    made_for_you=False,
    tenant_id="",
    expiry_reminder_sent=False,
    has_blocking_state=True,
):
    """Helper to create a mock job with configurable state_data."""
    job = Mock()
    job.job_id = job_id
    job.status = status
    job.user_email = user_email
    job.artist = artist
    job.title = title
    job.made_for_you = made_for_you
    job.tenant_id = tenant_id

    if has_blocking_state:
        entered_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        job.state_data = {
            'blocking_state_entered_at': entered_at,
            'blocking_action_type': 'lyrics',
            'reminder_sent': True,  # Existing idle reminder (always True for real jobs)
        }
        if expiry_reminder_sent:
            job.state_data['expiry_reminder_sent'] = True
    else:
        job.state_data = {}

    return job


@pytest.mark.asyncio
class TestProcessStaleReviews:
    """Test the main stale review processing function."""

    @pytest.fixture
    def mock_firestore(self):
        service = Mock()
        service.get_jobs.return_value = []
        service.update_job = Mock()
        return service

    @pytest.fixture
    def mock_job_manager(self):
        manager = Mock()
        manager.cancel_job.return_value = True
        return manager

    @pytest.fixture
    def mock_email_service(self):
        service = Mock()
        service.send_review_reminder.return_value = True
        service.send_review_expired.return_value = True
        return service

    @pytest.fixture
    def mock_user_service(self):
        service = Mock()
        service.check_credits.return_value = 2
        return service

    def _patch_all(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Return a context manager that patches all dependencies."""
        from contextlib import contextmanager

        @contextmanager
        def patches():
            with patch('backend.workers.stale_review_processor.FirestoreService', return_value=mock_firestore), \
                 patch('backend.workers.stale_review_processor.JobManager', return_value=mock_job_manager), \
                 patch('backend.workers.stale_review_processor.get_email_service', return_value=mock_email_service), \
                 patch('backend.services.user_service.get_user_service', return_value=mock_user_service):
                yield
        return patches()

    async def test_no_stale_jobs(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should return zero counts when no jobs are in review."""
        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 0
        assert result["jobs_expired"] == 0
        assert result["status"] == "completed"

    async def test_expires_job_over_48h(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should expire a job that's been in review for over 48 hours."""
        job = _make_job(hours_ago=50)
        mock_firestore.get_jobs.side_effect = [[job], []]  # awaiting_review query, in_review query

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1
        mock_job_manager.cancel_job.assert_called_once_with(
            "job-123", reason="Review not completed within 48 hours"
        )
        mock_email_service.send_review_expired.assert_called_once_with(
            to_email="user@test.com",
            artist="Test Artist",
            title="Test Song",
            credits_balance=2,
        )

    async def test_expires_job_over_48h_with_reminder_already_sent(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should expire job >48h even if reminder was already sent (no duplicate reminder)."""
        job = _make_job(hours_ago=50, expiry_reminder_sent=True)
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1
        # Reminder should NOT be sent again
        mock_email_service.send_review_reminder.assert_not_called()
        # But expiry email should still be sent
        mock_email_service.send_review_expired.assert_called_once()

    async def test_sends_reminder_24_to_48h(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should send reminder for job in 24-48h range."""
        job = _make_job(hours_ago=30)
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 1
        assert result["jobs_expired"] == 0
        mock_email_service.send_review_reminder.assert_called_once_with(
            to_email="user@test.com",
            artist="Test Artist",
            title="Test Song",
            job_id="job-123",
        )
        # Should update state_data with expiry_reminder_sent flag
        mock_firestore.update_job.assert_called_once()
        call_args = mock_firestore.update_job.call_args
        assert call_args[0][0] == "job-123"
        assert call_args[0][1]['state_data.expiry_reminder_sent'] is True
        assert 'state_data.expiry_reminder_sent_at' in call_args[0][1]

    async def test_skips_if_expiry_reminder_already_sent(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should skip reminder if expiry_reminder_sent is already True."""
        job = _make_job(hours_ago=30, expiry_reminder_sent=True)
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 0
        assert result["jobs_expired"] == 0
        mock_email_service.send_review_reminder.assert_not_called()

    async def test_idle_reminder_sent_does_not_block_expiry_reminder(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Existing idle reminder_sent=True should not prevent 24h expiry reminder."""
        job = _make_job(hours_ago=30)
        # reminder_sent is True (set by idle reminder system) but expiry_reminder_sent is not set
        assert job.state_data['reminder_sent'] is True
        assert 'expiry_reminder_sent' not in job.state_data
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 1
        mock_email_service.send_review_reminder.assert_called_once()

    async def test_no_action_under_24h(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should take no action for jobs under 24 hours old."""
        job = _make_job(hours_ago=12)
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 0
        assert result["jobs_expired"] == 0
        mock_email_service.send_review_reminder.assert_not_called()
        mock_job_manager.cancel_job.assert_not_called()

    async def test_skips_made_for_you_jobs(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should skip made-for-you jobs regardless of age."""
        job = _make_job(hours_ago=100, made_for_you=True)
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 0
        assert result["reminders_sent"] == 0
        mock_job_manager.cancel_job.assert_not_called()

    async def test_skips_tenant_jobs(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should skip tenant/white-label jobs regardless of age."""
        job = _make_job(hours_ago=100, tenant_id="vocalstar")
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 0
        assert result["reminders_sent"] == 0
        mock_job_manager.cancel_job.assert_not_called()

    async def test_skips_job_without_blocking_state(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should skip jobs that have no blocking_state_entered_at."""
        job = _make_job(hours_ago=50, has_blocking_state=False)
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 0
        assert result["reminders_sent"] == 0

    async def test_expires_job_without_email(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should still expire jobs with no user_email (just skip the email)."""
        job = _make_job(hours_ago=50, user_email=None)
        mock_firestore.get_jobs.side_effect = [[job], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1
        mock_job_manager.cancel_job.assert_called_once()
        # No email sent because no user_email
        mock_email_service.send_review_expired.assert_not_called()

    async def test_error_in_one_job_doesnt_block_others(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """An error processing one job should not prevent processing others."""
        job1 = _make_job(job_id="job-bad", hours_ago=50)
        job2 = _make_job(job_id="job-good", hours_ago=50)
        mock_firestore.get_jobs.side_effect = [[job1, job2], []]

        # First cancel raises, second succeeds
        mock_job_manager.cancel_job.side_effect = [Exception("DB error"), True]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1  # Only job-good counted
        assert len(result["errors"]) == 1
        assert "job-bad" in result["errors"][0]

    async def test_return_counts_correct(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Return value should have correct counts for mixed batch."""
        job_expire = _make_job(job_id="expire-1", hours_ago=50)
        job_remind = _make_job(job_id="remind-1", hours_ago=30)
        job_fresh = _make_job(job_id="fresh-1", hours_ago=12)
        job_skip = _make_job(job_id="skip-1", hours_ago=50, made_for_you=True)
        mock_firestore.get_jobs.side_effect = [[job_expire, job_remind, job_fresh, job_skip], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1
        assert result["reminders_sent"] == 1
        assert len(result["errors"]) == 0

    async def test_handles_in_review_status(self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
        """Should process jobs in IN_REVIEW status (user opened but abandoned)."""
        job = _make_job(job_id="in-review-1", status=JobStatus.IN_REVIEW, hours_ago=50)
        mock_firestore.get_jobs.side_effect = [[], [job]]  # Empty awaiting, one in_review

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1
        mock_job_manager.cancel_job.assert_called_once()
