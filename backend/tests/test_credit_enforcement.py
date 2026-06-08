"""
Tests for credit enforcement in job creation, failure, cancellation, and deletion flows.

Verifies that:
- Job creation requires credits (unless admin)
- Credits are deducted on job creation
- Credits are refunded on job failure, cancellation, and deletion
- No double-refund when a job is cancelled/failed then deleted
- New users get 2 welcome credits
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

# Mock Firestore before importing modules that use it
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())

from backend.services.job_manager import JobManager
from backend.models.job import Job, JobCreate, JobStatus
from backend.exceptions import InsufficientCreditsError


@pytest.fixture
def mock_firestore_service():
    """Mock FirestoreService."""
    with patch('backend.services.job_manager.FirestoreService') as mock:
        service = Mock()
        mock.return_value = service
        yield service


@pytest.fixture
def mock_storage_service():
    """Mock StorageService."""
    with patch('backend.services.job_manager.StorageService') as mock:
        service = Mock()
        mock.return_value = service
        yield service


@pytest.fixture
def job_manager(mock_firestore_service, mock_storage_service):
    """Create JobManager with mocked dependencies."""
    return JobManager()


@pytest.fixture
def mock_user_service():
    """Mock UserService via get_user_service (patched at source module)."""
    service = Mock()
    with patch('backend.services.user_service.get_user_service', return_value=service):
        yield service


class TestCreditCheckOnJobCreation:
    """Test credit checks during job creation."""

    def test_job_creation_succeeds_with_credits(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Job creation succeeds when user has credits, and credit is deducted."""
        mock_user_service.check_credits.return_value = 5  # has enough credits
        mock_user_service.deduct_credits.return_value = (True, 4, "Credit deducted. 4 remaining")

        job = job_manager.create_job(
            JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="user@test.com"),
            is_admin=False,
        )

        assert job.status == JobStatus.PENDING
        mock_user_service.check_credits.assert_called_once_with("user@test.com")
        mock_user_service.deduct_credits.assert_called_once()
        # Verify deduct_credits was called with user email
        call_args = mock_user_service.deduct_credits.call_args
        assert call_args[0][0] == "user@test.com"

    def test_job_creation_fails_without_credits(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Job creation raises InsufficientCreditsError when user has no credits."""
        mock_user_service.has_credits.return_value = False
        mock_user_service.check_credits.return_value = 0

        with pytest.raises(InsufficientCreditsError) as exc_info:
            job_manager.create_job(
                JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="broke@test.com"),
                is_admin=False,
            )

        assert exc_info.value.credits_available == 0
        assert exc_info.value.credits_required == 1
        # Verify Firestore create_job was NOT called (fail-fast before job creation)
        mock_firestore_service.create_job.assert_not_called()

    def test_admin_bypasses_credit_check(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Admin users bypass credit check entirely."""
        job = job_manager.create_job(
            JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="admin@nomadkaraoke.com"),
            is_admin=True,
        )

        assert job.status == JobStatus.PENDING
        mock_user_service.check_credits.assert_not_called()
        mock_user_service.deduct_credits.assert_not_called()

    def test_job_without_user_email_skips_credit_check(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Jobs without user_email (e.g., API token auth) skip credit check."""
        job = job_manager.create_job(
            JobCreate(artist="Test", title="Song", theme_id="nomad"),
            is_admin=False,
        )

        assert job.status == JobStatus.PENDING
        mock_user_service.check_credits.assert_not_called()

    def test_deduction_failure_deletes_job_and_raises(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """If credit deduction fails after job creation, the job is deleted."""
        mock_user_service.check_credits.return_value = 5  # passes the gate
        mock_user_service.deduct_credits.return_value = (False, 0, "Race condition: insufficient credits")

        with pytest.raises(InsufficientCreditsError):
            job_manager.create_job(
                JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="user@test.com"),
                is_admin=False,
            )

        # Job was created then deleted
        mock_firestore_service.create_job.assert_called_once()
        mock_firestore_service.delete_job.assert_called_once()


class TestCreditRefundOnJobFailure:
    """Test credit refund when jobs fail.

    _refund_credit_for_job now calls user_service.add_credits directly
    (rather than refund_credit) so it can refund the exact credits_charged
    amount instead of a hard-coded 1.
    """

    def _mock_job(self, credits_charged=1, credit_refunded=False, user_email="user@test.com"):
        """Helper: build a minimal mock Job with state_data.credits_charged."""
        job = Mock(spec=Job)
        job.user_email = user_email
        job.credit_refunded = credit_refunded
        job.state_data = {"credits_charged": credits_charged}
        return job

    def test_credit_refunded_on_job_failure(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Credit is refunded when a non-admin job fails (1-credit legacy job)."""
        mock_job = self._mock_job(credits_charged=1)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.return_value = (True, 1, "Refunded")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.add_credits.assert_called_once_with(
            "user@test.com", amount=1, reason="job_failed", job_id="job123"
        )

    def test_multi_credit_job_refunds_full_amount_on_failure(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """A 4-credit long-duration job must refund 4 credits on failure, not 1."""
        mock_job = self._mock_job(credits_charged=4)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.return_value = (True, 6, "Refunded 4")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.add_credits.assert_called_once_with(
            "user@test.com", amount=4, reason="job_failed", job_id="job123"
        )

    def test_payment_bypassed_job_skips_refund_on_failure(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Admin / payment_bypassed jobs have credits_charged=0 — nothing to refund."""
        mock_job = self._mock_job(credits_charged=0)
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.add_credits.assert_not_called()

    def test_credit_refund_skipped_for_admin_jobs(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Credit refund is skipped for admin-owned jobs."""
        mock_job = self._mock_job(credits_charged=1, user_email="admin@nomadkaraoke.com")
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=True):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.add_credits.assert_not_called()

    def test_refund_failure_does_not_fail_job_marking(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """If refund fails, the job is still marked as failed."""
        mock_job = self._mock_job(credits_charged=1)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.side_effect = Exception("Firestore error")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True  # Job still marked as failed

    def test_no_refund_when_job_has_no_user_email(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund attempted when job has no user_email."""
        mock_job = self._mock_job(credits_charged=1, user_email=None)
        mock_firestore_service.get_job.return_value = mock_job

        result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.add_credits.assert_not_called()

    def test_no_double_refund_when_already_refunded(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund if credit_refunded is already True."""
        mock_job = self._mock_job(credits_charged=1, credit_refunded=True)
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.add_credits.assert_not_called()


class TestCreditRefundOnJobCancellation:
    """Test credit refund when jobs are cancelled.

    _refund_credit_for_job now calls user_service.add_credits directly
    (rather than refund_credit) so it can refund the exact credits_charged
    amount instead of a hard-coded 1.
    """

    def _mock_job(self, credits_charged=1, credit_refunded=False,
                  user_email="user@test.com", status=None):
        """Helper: build a minimal mock Job with state_data.credits_charged."""
        job = Mock(spec=Job)
        job.user_email = user_email
        job.credit_refunded = credit_refunded
        job.state_data = {"credits_charged": credits_charged}
        job.status = status or JobStatus.PENDING
        return job

    def test_credit_refunded_on_cancellation(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Credit is refunded when a non-admin job is cancelled (1-credit job)."""
        mock_job = self._mock_job(credits_charged=1)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.return_value = (True, 1, "Refunded")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.cancel_job("job123", reason="User cancelled")

        assert result is True
        mock_user_service.add_credits.assert_called_once_with(
            "user@test.com", amount=1, reason="job_cancelled", job_id="job123"
        )

    def test_multi_credit_cancel_refunds_full_amount(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """A 4-credit long job cancellation must refund 4 credits, not 1."""
        mock_job = self._mock_job(credits_charged=4)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.return_value = (True, 6, "Refunded 4")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.cancel_job("job123", reason="User cancelled")

        assert result is True
        mock_user_service.add_credits.assert_called_once_with(
            "user@test.com", amount=4, reason="job_cancelled", job_id="job123"
        )

    def test_cancel_guard_prevents_double_refund_when_credit_refunded(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """If credit_refunded is already True, cancel must NOT refund again."""
        mock_job = self._mock_job(credits_charged=4, credit_refunded=True)
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.cancel_job("job123")

        assert result is True
        mock_user_service.add_credits.assert_not_called()

    def test_payment_bypassed_cancel_refunds_nothing(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Admin / payment_bypassed jobs (credits_charged=0) must not be refunded on cancel."""
        mock_job = self._mock_job(credits_charged=0)
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.cancel_job("job123")

        assert result is True
        mock_user_service.add_credits.assert_not_called()

    def test_cancel_skips_refund_for_admin_jobs(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Credit refund is skipped when admin cancels their own job."""
        mock_job = self._mock_job(credits_charged=1, user_email="admin@nomadkaraoke.com")
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=True):
            result = job_manager.cancel_job("job123")

        assert result is True
        mock_user_service.add_credits.assert_not_called()

    def test_cancel_refund_failure_does_not_block_cancellation(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """If refund fails, the job is still cancelled."""
        mock_job = self._mock_job(credits_charged=1)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.side_effect = Exception("Firestore error")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.cancel_job("job123")

        assert result is True

    def test_cannot_cancel_terminal_job(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Cannot cancel a job in a terminal state."""
        mock_job = self._mock_job(credits_charged=1, status=JobStatus.COMPLETE)
        mock_firestore_service.get_job.return_value = mock_job

        result = job_manager.cancel_job("job123")

        assert result is False
        mock_user_service.add_credits.assert_not_called()


class TestCreditRefundOnJobDeletion:
    """Test credit refund when jobs are deleted.

    _refund_credit_for_job now calls user_service.add_credits directly
    (rather than refund_credit) so it can refund the exact credits_charged amount.
    """

    def _mock_job(self, credits_charged=1, credit_refunded=False,
                  user_email="user@test.com", status=None):
        job = Mock(spec=Job)
        job.user_email = user_email
        job.credit_refunded = credit_refunded
        job.state_data = {"credits_charged": credits_charged}
        job.status = status or JobStatus.PENDING
        job.output_files = {}
        return job

    def test_credit_refunded_on_delete_pending_job(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Credit is refunded when a pending job is deleted (1-credit job)."""
        mock_job = self._mock_job(credits_charged=1)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.return_value = (True, 1, "Refunded")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            job_manager.delete_job("job123")

        mock_user_service.add_credits.assert_called_once_with(
            "user@test.com", amount=1, reason="job_deleted", job_id="job123"
        )

    def test_no_refund_on_delete_completed_job(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund when deleting a completed job (user got their video)."""
        mock_job = self._mock_job(credits_charged=1, status=JobStatus.COMPLETE)
        mock_firestore_service.get_job.return_value = mock_job

        job_manager.delete_job("job123")

        mock_user_service.add_credits.assert_not_called()

    def test_no_refund_on_delete_prep_complete_job(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund when deleting a prep-complete job."""
        mock_job = self._mock_job(credits_charged=1, status=JobStatus.PREP_COMPLETE)
        mock_firestore_service.get_job.return_value = mock_job

        job_manager.delete_job("job123")

        mock_user_service.add_credits.assert_not_called()

    def test_no_double_refund_on_delete_failed_job(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund when deleting a failed job (already refunded on failure)."""
        mock_job = self._mock_job(credits_charged=1, credit_refunded=True, status=JobStatus.FAILED)
        mock_firestore_service.get_job.return_value = mock_job

        job_manager.delete_job("job123")

        mock_user_service.add_credits.assert_not_called()

    def test_no_double_refund_on_delete_cancelled_job(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund when deleting a cancelled job (already refunded on cancel)."""
        mock_job = self._mock_job(credits_charged=1, credit_refunded=True, status=JobStatus.CANCELLED)
        mock_firestore_service.get_job.return_value = mock_job

        job_manager.delete_job("job123")

        mock_user_service.add_credits.assert_not_called()

    def test_delete_refund_failure_does_not_block_deletion(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """If refund fails, the job is still deleted."""
        mock_job = self._mock_job(credits_charged=1)
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.add_credits.side_effect = Exception("Firestore error")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            job_manager.delete_job("job123")

        # Job should still be deleted despite refund failure
        mock_firestore_service.delete_job.assert_called_once_with("job123")

    def test_delete_skips_refund_for_admin_jobs(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund when admin deletes their own job."""
        mock_job = self._mock_job(credits_charged=1, user_email="admin@nomadkaraoke.com")
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=True):
            job_manager.delete_job("job123")

        mock_user_service.add_credits.assert_not_called()


class TestWelcomeCredits:
    """Test new user welcome credits."""

    def test_new_user_gets_1_welcome_credit(self):
        """Verify NEW_USER_FREE_CREDITS is set to 1."""
        from backend.services.user_service import UserService
        assert UserService.NEW_USER_FREE_CREDITS == 1


class TestInsufficientCreditsError:
    """Test the InsufficientCreditsError exception."""

    def test_exception_attributes(self):
        """Verify exception stores all attributes."""
        err = InsufficientCreditsError(
            message="No credits",
            credits_available=0,
            credits_required=1,
        )
        assert err.message == "No credits"
        assert err.credits_available == 0
        assert err.credits_required == 1
        assert str(err) == "No credits"

    def test_exception_defaults(self):
        """Verify default values."""
        err = InsufficientCreditsError(message="Out of credits")
        assert err.credits_available == 0
        assert err.credits_required == 1
