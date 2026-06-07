"""
Unit tests for stale duration-confirm processor.

Tests the 24h reminder and 48h auto-cancel+refund logic for jobs stuck in
AWAITING_DURATION_CONFIRM state, within the shared stale review processor.

Money-critical: verifies the FULL credits_charged amount is refunded (not flat 1)
and that the idempotency flag prevents double-refunds.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, patch, call

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())

from backend.models.job import JobStatus


def _make_duration_confirm_job(
    job_id="dur-job-1",
    hours_ago=50,
    user_email="user@test.com",
    artist="Test Artist",
    title="Test Song",
    credits_charged=3,
    made_for_you=False,
    tenant_id="",
    expiry_reminder_sent=False,
    has_blocking_state=True,
):
    """Helper to create a mock AWAITING_DURATION_CONFIRM job."""
    job = Mock()
    job.job_id = job_id
    job.status = JobStatus.AWAITING_DURATION_CONFIRM
    job.user_email = user_email
    job.artist = artist
    job.title = title
    job.made_for_you = made_for_you
    job.tenant_id = tenant_id

    if has_blocking_state:
        entered_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        job.state_data = {
            'blocking_state_entered_at': entered_at,
            'credits_charged': credits_charged,
        }
        if expiry_reminder_sent:
            job.state_data['expiry_reminder_sent'] = True
    else:
        job.state_data = {}

    return job


def _make_review_job(
    job_id="rev-job-1",
    status=JobStatus.AWAITING_REVIEW,
    hours_ago=50,
    user_email="user@test.com",
    artist="Test Artist",
    title="Test Song",
    expiry_reminder_sent=False,
):
    """Helper to create a mock AWAITING_REVIEW job."""
    job = Mock()
    job.job_id = job_id
    job.status = status
    job.user_email = user_email
    job.artist = artist
    job.title = title
    job.made_for_you = False
    job.tenant_id = ""

    entered_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    job.state_data = {
        'blocking_state_entered_at': entered_at,
        'blocking_action_type': 'lyrics',
    }
    if expiry_reminder_sent:
        job.state_data['expiry_reminder_sent'] = True

    return job


@pytest.mark.asyncio
class TestStaleDurationConfirm:
    """Tests for AWAITING_DURATION_CONFIRM jobs in the stale review processor."""

    @pytest.fixture
    def mock_firestore(self):
        service = Mock()
        service.list_jobs.return_value = []
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
        service.send_duration_confirm_reminder.return_value = True
        service.send_duration_confirm_expired.return_value = True
        return service

    @pytest.fixture
    def mock_user_service(self):
        service = Mock()
        service.check_credits.return_value = 5
        service.add_credits.return_value = (True, 5, "Added 3 credits")
        user = Mock()
        user.locale = "en"
        service.get_user.return_value = user
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

    # ------------------------------------------------------------------
    # 48h expiry: full refund of credits_charged
    # ------------------------------------------------------------------

    async def test_48h_expires_with_full_credits_refund(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """
        A duration-confirm job at >=48h should be cancelled, refund ALL
        credits_charged (3 in this case, not flat 1), and send
        send_duration_confirm_expired.
        """
        job = _make_duration_confirm_job(hours_ago=50, credits_charged=3)
        # list_jobs called for: AWAITING_REVIEW, IN_REVIEW, AWAITING_DURATION_CONFIRM
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1

        # Must call add_credits with the full amount (3), not flat 1
        mock_user_service.add_credits.assert_called_once_with(
            job.user_email,
            amount=3,
            reason="duration_confirm_expired",
            job_id=job.job_id,
        )

        # Must cancel job with correct reason
        mock_job_manager.cancel_job.assert_called_once_with(
            job.job_id,
            reason="Duration confirmation not completed within 48 hours",
        )

        # Must send the duration-confirm expired email (NOT review expired)
        mock_email_service.send_duration_confirm_expired.assert_called_once()
        mock_email_service.send_review_expired.assert_not_called()

    async def test_48h_no_double_refund_from_cancel_job(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """
        cancel_job's built-in 1-credit refund must be suppressed so the user
        is not refunded twice. The processor must mark credit_refunded=True on
        the job (via firestore.update_job) BEFORE calling cancel_job.
        """
        job = _make_duration_confirm_job(hours_ago=50, credits_charged=3)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            await process_stale_reviews()

        # Collect all update_job calls
        update_calls = mock_firestore.update_job.call_args_list
        # One of the calls must set credit_refunded=True
        credit_refunded_call = [
            c for c in update_calls
            if isinstance(c.args[1] if c.args else c.kwargs.get('updates', {}), dict)
            and c.args[1].get('credit_refunded') is True
        ]
        assert credit_refunded_call, (
            "Expected firestore.update_job({'credit_refunded': True}) before cancel_job "
            f"to prevent double-refund. Actual calls: {update_calls}"
        )

    async def test_48h_credits_charged_zero_skips_add_credits(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """If credits_charged==0, add_credits should NOT be called (nothing to refund)."""
        job = _make_duration_confirm_job(hours_ago=50, credits_charged=0)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1
        mock_user_service.add_credits.assert_not_called()
        mock_job_manager.cancel_job.assert_called_once()

    async def test_48h_sets_expiry_idempotency_flag_after_cancel(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """
        The expiry idempotency flag must be set AFTER a successful cancel_job.

        Ordering requirement (Bug E fix): if cancel_job fails the flag must NOT be
        set — otherwise the job is stuck (unflagged refund but never cancelled).
        """
        job = _make_duration_confirm_job(hours_ago=50, credits_charged=2)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        call_order = []

        def record_cancel(*args, **kwargs):
            call_order.append("cancel_job")
            return True

        def record_update(job_id, updates):
            if isinstance(updates, dict) and updates.get('state_data.duration_confirm_expiry_processed') is True:
                call_order.append("flag_set")

        mock_job_manager.cancel_job.side_effect = record_cancel
        mock_firestore.update_job.side_effect = record_update

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            await process_stale_reviews()

        # Both events must have been recorded
        assert "cancel_job" in call_order, "cancel_job was never called"
        assert "flag_set" in call_order, (
            "Expected update_job with state_data.duration_confirm_expiry_processed=True "
            "to be called after cancel_job"
        )

        # Flag must come AFTER cancel
        cancel_idx = call_order.index("cancel_job")
        flag_idx = call_order.index("flag_set")
        assert flag_idx > cancel_idx, (
            f"Idempotency flag was set (pos {flag_idx}) BEFORE cancel_job (pos {cancel_idx}). "
            "It must be set only after a successful cancel."
        )

    async def test_48h_flag_not_set_when_cancel_fails(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """If cancel_job returns False, the idempotency flag must NOT be written."""
        job = _make_duration_confirm_job(hours_ago=50, credits_charged=2)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]
        mock_job_manager.cancel_job.return_value = False  # cancel fails

        # Reset side_effect so update_job works normally
        mock_firestore.update_job.side_effect = None

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            await process_stale_reviews()

        # Flag must NOT be set when cancel failed
        update_calls = mock_firestore.update_job.call_args_list
        expiry_flag_calls = [
            c for c in update_calls
            if isinstance(c.args[1] if len(c.args) > 1 else c.kwargs.get('updates', {}), dict)
            and (c.args[1] if len(c.args) > 1 else c.kwargs.get('updates', {})).get(
                'state_data.duration_confirm_expiry_processed'
            ) is True
        ]
        assert not expiry_flag_calls, (
            "Idempotency flag must NOT be written when cancel_job returns False. "
            f"Actual update_job calls: {update_calls}"
        )

    # ------------------------------------------------------------------
    # Idempotency: already-processed job is not refunded again
    # ------------------------------------------------------------------

    async def test_idempotency_already_processed_not_refunded_again(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """A job already flagged duration_confirm_expiry_processed must not be processed again."""
        job = _make_duration_confirm_job(hours_ago=50, credits_charged=3)
        job.state_data['duration_confirm_expiry_processed'] = True
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 0
        mock_user_service.add_credits.assert_not_called()
        mock_job_manager.cancel_job.assert_not_called()
        mock_email_service.send_duration_confirm_expired.assert_not_called()

    # ------------------------------------------------------------------
    # 24h reminder
    # ------------------------------------------------------------------

    async def test_24h_sends_duration_confirm_reminder(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """
        A duration-confirm job at 24–48h should send send_duration_confirm_reminder,
        not be cancelled, and not refund credits.
        """
        job = _make_duration_confirm_job(hours_ago=30, credits_charged=2)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 1
        assert result["jobs_expired"] == 0

        mock_email_service.send_duration_confirm_reminder.assert_called_once()
        call_kwargs = mock_email_service.send_duration_confirm_reminder.call_args.kwargs
        assert call_kwargs["to_email"] == job.user_email

        # Must NOT cancel or refund
        mock_job_manager.cancel_job.assert_not_called()
        mock_user_service.add_credits.assert_not_called()
        mock_email_service.send_review_reminder.assert_not_called()

    async def test_24h_reminder_sets_expiry_reminder_sent_flag(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """The expiry_reminder_sent flag must be set after sending reminder."""
        job = _make_duration_confirm_job(hours_ago=30, credits_charged=2)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            await process_stale_reviews()

        update_calls = mock_firestore.update_job.call_args_list
        reminder_flag_calls = [
            c for c in update_calls
            if isinstance(c.args[1] if c.args else c.kwargs.get('updates', {}), dict)
            and c.args[1].get('state_data.expiry_reminder_sent') is True
        ]
        assert reminder_flag_calls, (
            f"Expected expiry_reminder_sent=True update. Actual calls: {update_calls}"
        )

    async def test_24h_reminder_skipped_if_already_sent(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """If expiry_reminder_sent is already True, reminder must NOT be sent again."""
        job = _make_duration_confirm_job(hours_ago=30, credits_charged=2, expiry_reminder_sent=True)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 0
        mock_email_service.send_duration_confirm_reminder.assert_not_called()

    # ------------------------------------------------------------------
    # Under 24h: no action
    # ------------------------------------------------------------------

    async def test_under_24h_no_action(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """Jobs under 24h must be left alone."""
        job = _make_duration_confirm_job(hours_ago=12, credits_charged=2)
        mock_firestore.list_jobs.side_effect = [[], [], [job]]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 0
        assert result["jobs_expired"] == 0
        mock_email_service.send_duration_confirm_reminder.assert_not_called()
        mock_job_manager.cancel_job.assert_not_called()
        mock_user_service.add_credits.assert_not_called()

    # ------------------------------------------------------------------
    # Regression: review-state jobs still use old path
    # ------------------------------------------------------------------

    async def test_regression_review_job_still_uses_review_path(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """
        AWAITING_REVIEW jobs must still use the existing review expiry path:
        cancel_job + send_review_expired (with credits_balance), NOT
        add_credits + send_duration_confirm_expired.
        """
        review_job = _make_review_job(job_id="rev-1", hours_ago=50)
        mock_firestore.list_jobs.side_effect = [[review_job], [], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["jobs_expired"] == 1

        # Old path: cancel_job (includes 1-credit refund internally)
        mock_job_manager.cancel_job.assert_called_once_with(
            "rev-1", reason="Review not completed within 48 hours"
        )

        # Old email: send_review_expired with credits_balance
        mock_email_service.send_review_expired.assert_called_once()
        call_kwargs = mock_email_service.send_review_expired.call_args.kwargs
        assert call_kwargs["to_email"] == review_job.user_email
        assert "credits_balance" in call_kwargs

        # Must NOT use duration-confirm path
        mock_user_service.add_credits.assert_not_called()
        mock_email_service.send_duration_confirm_expired.assert_not_called()

    async def test_regression_review_job_24h_reminder_unchanged(
        self, mock_firestore, mock_job_manager, mock_email_service, mock_user_service
    ):
        """AWAITING_REVIEW jobs at 24-48h still use send_review_reminder (unchanged)."""
        review_job = _make_review_job(job_id="rev-2", hours_ago=30)
        mock_firestore.list_jobs.side_effect = [[review_job], [], []]

        with self._patch_all(mock_firestore, mock_job_manager, mock_email_service, mock_user_service):
            from backend.workers.stale_review_processor import process_stale_reviews
            result = await process_stale_reviews()

        assert result["reminders_sent"] == 1
        mock_email_service.send_review_reminder.assert_called_once()
        mock_email_service.send_duration_confirm_reminder.assert_not_called()
