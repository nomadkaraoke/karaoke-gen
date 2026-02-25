"""
Tests for credit enforcement in job creation and failure flows.

Verifies that:
- Job creation requires credits (unless admin)
- Credits are deducted on job creation
- Credits are refunded on job failure
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


@pytest.fixture
def mock_rate_limit_service():
    """Mock rate limit service to always allow."""
    service = Mock()
    service.check_user_job_limit.return_value = (True, 5, "OK")
    service.record_job_creation.return_value = None
    with patch('backend.services.rate_limit_service.get_rate_limit_service', return_value=service):
        yield service


class TestCreditCheckOnJobCreation:
    """Test credit checks during job creation."""

    def test_job_creation_succeeds_with_credits(
        self, job_manager, mock_firestore_service, mock_user_service, mock_rate_limit_service
    ):
        """Job creation succeeds when user has credits, and credit is deducted."""
        mock_user_service.has_credits.return_value = True
        mock_user_service.deduct_credit.return_value = (True, 4, "Credit deducted. 4 remaining")

        job = job_manager.create_job(
            JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="user@test.com"),
            is_admin=False,
        )

        assert job.status == JobStatus.PENDING
        mock_user_service.has_credits.assert_called_once_with("user@test.com")
        mock_user_service.deduct_credit.assert_called_once()
        # Verify deduct_credit was called with user email, job_id, and reason
        call_args = mock_user_service.deduct_credit.call_args
        assert call_args[0][0] == "user@test.com"

    def test_job_creation_fails_without_credits(
        self, job_manager, mock_firestore_service, mock_user_service, mock_rate_limit_service
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
        self, job_manager, mock_firestore_service, mock_user_service, mock_rate_limit_service
    ):
        """Admin users bypass credit check entirely."""
        job = job_manager.create_job(
            JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="admin@nomadkaraoke.com"),
            is_admin=True,
        )

        assert job.status == JobStatus.PENDING
        mock_user_service.has_credits.assert_not_called()
        mock_user_service.deduct_credit.assert_not_called()

    def test_job_without_user_email_skips_credit_check(
        self, job_manager, mock_firestore_service, mock_user_service, mock_rate_limit_service
    ):
        """Jobs without user_email (e.g., API token auth) skip credit check."""
        job = job_manager.create_job(
            JobCreate(artist="Test", title="Song", theme_id="nomad"),
            is_admin=False,
        )

        assert job.status == JobStatus.PENDING
        mock_user_service.has_credits.assert_not_called()

    def test_deduction_failure_deletes_job_and_raises(
        self, job_manager, mock_firestore_service, mock_user_service, mock_rate_limit_service
    ):
        """If credit deduction fails after job creation, the job is deleted."""
        mock_user_service.has_credits.return_value = True
        mock_user_service.deduct_credit.return_value = (False, 0, "Insufficient credits")

        with pytest.raises(InsufficientCreditsError):
            job_manager.create_job(
                JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="user@test.com"),
                is_admin=False,
            )

        # Job was created then deleted
        mock_firestore_service.create_job.assert_called_once()
        mock_firestore_service.delete_job.assert_called_once()


class TestCreditRefundOnJobFailure:
    """Test credit refund when jobs fail."""

    def test_credit_refunded_on_job_failure(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Credit is refunded when a non-admin job fails."""
        mock_job = Mock(spec=Job)
        mock_job.user_email = "user@test.com"
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.refund_credit.return_value = (True, 1, "Refunded")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.refund_credit.assert_called_once_with(
            "user@test.com", "job123", reason="job_failed"
        )

    def test_credit_refund_skipped_for_admin_jobs(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """Credit refund is skipped for admin-owned jobs."""
        mock_job = Mock(spec=Job)
        mock_job.user_email = "admin@nomadkaraoke.com"
        mock_firestore_service.get_job.return_value = mock_job

        with patch('backend.services.auth_service.is_admin_email', return_value=True):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.refund_credit.assert_not_called()

    def test_refund_failure_does_not_fail_job_marking(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """If refund fails, the job is still marked as failed."""
        mock_job = Mock(spec=Job)
        mock_job.user_email = "user@test.com"
        mock_firestore_service.get_job.return_value = mock_job
        mock_user_service.refund_credit.side_effect = Exception("Firestore error")

        with patch('backend.services.auth_service.is_admin_email', return_value=False):
            result = job_manager.fail_job("job123", "Processing error")

        assert result is True  # Job still marked as failed

    def test_no_refund_when_job_has_no_user_email(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """No refund attempted when job has no user_email."""
        mock_job = Mock(spec=Job)
        mock_job.user_email = None
        mock_firestore_service.get_job.return_value = mock_job

        result = job_manager.fail_job("job123", "Processing error")

        assert result is True
        mock_user_service.refund_credit.assert_not_called()


class TestCreditAndRateLimitOrdering:
    """Test interaction between rate limit and credit checks."""

    def test_rate_limited_user_not_charged_credit(
        self, job_manager, mock_firestore_service, mock_user_service
    ):
        """User who is rate limited should NOT have a credit deducted."""
        mock_rate_limit_service = Mock()
        mock_rate_limit_service.check_user_job_limit.return_value = (False, 0, "Rate limit exceeded")
        mock_rate_limit_service.get_user_job_count_today.return_value = 5

        with patch('backend.services.rate_limit_service.get_rate_limit_service', return_value=mock_rate_limit_service):
            from backend.exceptions import RateLimitExceededError
            with pytest.raises(RateLimitExceededError):
                job_manager.create_job(
                    JobCreate(artist="Test", title="Song", theme_id="nomad", user_email="user@test.com"),
                    is_admin=False,
                )

        # Credit check should never be called because rate limit fails first
        mock_user_service.has_credits.assert_not_called()
        mock_user_service.deduct_credit.assert_not_called()


class TestWelcomeCredits:
    """Test new user welcome credits."""

    def test_new_user_gets_2_welcome_credits(self):
        """Verify NEW_USER_FREE_CREDITS is set to 2."""
        from backend.services.user_service import UserService
        assert UserService.NEW_USER_FREE_CREDITS == 2


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
