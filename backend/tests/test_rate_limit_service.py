"""
Unit tests for RateLimitService.

Tests rate limiting logic for job creation, user overrides, and stats.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

# Mock Google Cloud before imports
import sys
sys.modules['google.cloud.firestore'] = MagicMock()
sys.modules['google.cloud.storage'] = MagicMock()


class TestRateLimitService:
    """Test RateLimitService functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Firestore client."""
        mock = MagicMock()
        return mock

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = Mock()
        settings.enable_rate_limiting = True
        settings.rate_limit_jobs_per_day = 5
        return settings

    @pytest.fixture
    def rate_limit_service(self, mock_db, mock_settings):
        """Create RateLimitService instance with mocks."""
        with patch('backend.services.rate_limit_service.settings', mock_settings):
            from backend.services.rate_limit_service import RateLimitService
            service = RateLimitService(db=mock_db)
            return service

    # =========================================================================
    # User Job Rate Limiting Tests
    # =========================================================================

    def test_check_user_job_limit_under_limit(self, rate_limit_service, mock_db, mock_settings):
        """Test that user under limit is allowed."""
        # Mock: user has 2 jobs today
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"count": 2}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit("user@example.com")

        assert allowed is True
        assert remaining == 3  # 5 - 2
        assert "3 jobs remaining" in message

    def test_check_user_job_limit_at_limit(self, rate_limit_service, mock_db, mock_settings):
        """Test that user at limit is blocked."""
        # Mock: user has 5 jobs today (at limit)
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"count": 5}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit("user@example.com")

        assert allowed is False
        assert remaining == 0
        assert "Daily job limit reached" in message

    def test_check_user_job_limit_over_limit(self, rate_limit_service, mock_db, mock_settings):
        """Test that user over limit is blocked."""
        # Mock: user somehow has 7 jobs (over limit)
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"count": 7}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit("user@example.com")

        assert allowed is False
        assert remaining == 0

    def test_check_user_job_limit_no_jobs_yet(self, rate_limit_service, mock_db, mock_settings):
        """Test user with no jobs today is allowed."""
        # Mock: no document exists
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit("newuser@example.com")

        assert allowed is True
        assert remaining == 5

    def test_check_user_job_limit_with_admin_bypass(self, rate_limit_service, mock_db, mock_settings):
        """Test that admins bypass job limits."""
        # Mock: user at limit
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"count": 10}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit(
                "admin@example.com", is_admin=True
            )

        assert allowed is True
        assert remaining == -1  # Unlimited for admins

    def test_check_user_job_limit_with_override_bypass(self, rate_limit_service, mock_db, mock_settings):
        """Test that users with bypass override are allowed."""
        # Mock: user at limit
        mock_job_doc = Mock()
        mock_job_doc.exists = True
        mock_job_doc.to_dict.return_value = {"count": 10}

        # Mock: user has bypass override
        mock_override_doc = Mock()
        mock_override_doc.exists = True
        mock_override_doc.to_dict.return_value = {"bypass_job_limit": True}

        mock_db.collection.return_value.document.return_value.get.side_effect = [mock_override_doc, mock_job_doc]

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit("vip@example.com")

        assert allowed is True

    def test_check_user_job_limit_with_custom_limit(self, rate_limit_service, mock_db, mock_settings):
        """Test that custom limit from override is respected."""
        # Mock: user has 8 jobs
        mock_job_doc = Mock()
        mock_job_doc.exists = True
        mock_job_doc.to_dict.return_value = {"count": 8}

        # Mock: user has custom limit of 20
        mock_override_doc = Mock()
        mock_override_doc.exists = True
        mock_override_doc.to_dict.return_value = {"bypass_job_limit": False, "custom_daily_job_limit": 20}

        mock_db.collection.return_value.document.return_value.get.side_effect = [mock_override_doc, mock_job_doc]

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit("custom@example.com")

        assert allowed is True
        assert remaining == 12  # 20 - 8

    def test_check_user_job_limit_rate_limiting_disabled(self, rate_limit_service, mock_settings):
        """Test that rate limiting can be disabled."""
        mock_settings.enable_rate_limiting = False

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            allowed, remaining, message = rate_limit_service.check_user_job_limit("user@example.com")

        assert allowed is True
        assert remaining == -1
        assert "Rate limiting disabled" in message

    # =========================================================================
    # Recording Tests
    # =========================================================================

    def test_record_job_creation(self, rate_limit_service, mock_db, mock_settings):
        """Test recording a job creation."""
        mock_transaction = Mock()
        mock_db.transaction.return_value = mock_transaction

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            with patch('backend.services.rate_limit_service.firestore') as mock_firestore:
                # Mock the transactional decorator
                mock_firestore.transactional = lambda f: f
                rate_limit_service.record_job_creation("user@example.com", "job123")

        # Verify transaction was used
        mock_db.transaction.assert_called()

    def test_record_skipped_when_disabled(self, rate_limit_service, mock_db, mock_settings):
        """Test that recording is skipped when rate limiting is disabled."""
        mock_settings.enable_rate_limiting = False

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            rate_limit_service.record_job_creation("user@example.com", "job123")

        # Should not call Firestore
        mock_db.collection.assert_not_called()

    # =========================================================================
    # Override Management Tests
    # =========================================================================

    def test_get_user_override_exists(self, rate_limit_service, mock_db):
        """Test getting an existing user override."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "email": "vip@example.com",
            "bypass_job_limit": True,
            "reason": "VIP user"
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        override = rate_limit_service.get_user_override("vip@example.com")

        assert override is not None
        assert override["bypass_job_limit"] is True

    def test_get_user_override_not_exists(self, rate_limit_service, mock_db):
        """Test getting a non-existent user override."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        override = rate_limit_service.get_user_override("regular@example.com")

        assert override is None

    def test_set_user_override(self, rate_limit_service, mock_db):
        """Test setting a user override."""
        rate_limit_service.set_user_override(
            user_email="vip@example.com",
            bypass_job_limit=True,
            reason="Special access",
            admin_email="admin@example.com"
        )

        mock_db.collection.return_value.document.return_value.set.assert_called_once()

    def test_remove_user_override_exists(self, rate_limit_service, mock_db):
        """Test removing an existing user override."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = rate_limit_service.remove_user_override("vip@example.com", "admin@example.com")

        assert result is True
        mock_db.collection.return_value.document.return_value.delete.assert_called_once()

    def test_remove_user_override_not_exists(self, rate_limit_service, mock_db):
        """Test removing a non-existent user override."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = rate_limit_service.remove_user_override("regular@example.com", "admin@example.com")

        assert result is False

    # =========================================================================
    # Stats Tests
    # =========================================================================

    def test_get_rate_limit_stats(self, rate_limit_service, mock_db, mock_settings):
        """Test getting rate limit statistics."""
        # Mock Firestore stream returning one user's job doc
        mock_doc = Mock()
        mock_doc.to_dict.return_value = {
            "user_email": "user@example.com",
            "count": 3,
        }
        mock_db.collection.return_value.where.return_value.stream.return_value = [mock_doc]

        with patch('backend.services.rate_limit_service.settings', mock_settings):
            stats = rate_limit_service.get_rate_limit_stats()

        assert stats["rate_limiting_enabled"] is True
        assert stats["job_limit_per_user"] == 5
        assert stats["total_jobs_today"] == 3
        assert stats["users_with_jobs_today"] == 1
        assert "seconds_until_reset" in stats


class TestRateLimitExceededError:
    """Test RateLimitExceededError exception."""

    def test_exception_attributes(self):
        """Test exception has all expected attributes."""
        from backend.exceptions import RateLimitExceededError

        exc = RateLimitExceededError(
            message="Limit exceeded",
            limit_type="job",
            remaining_seconds=3600,
            current_count=5,
            limit_value=5
        )

        assert exc.message == "Limit exceeded"
        assert exc.limit_type == "job"
        assert exc.remaining_seconds == 3600
        assert exc.current_count == 5
        assert exc.limit_value == 5

    def test_exception_str(self):
        """Test exception string representation."""
        from backend.exceptions import RateLimitExceededError

        exc = RateLimitExceededError(message="Test message")
        assert str(exc) == "Test message"
