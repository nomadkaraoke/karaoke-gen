"""
Unit tests for YouTubeQuotaService.

Tests quota tracking, availability checks, and operation recording
against the YouTube Data API v3's 10,000 units/day quota.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())


class TestYouTubeQuotaService:
    """Test YouTubeQuotaService functionality."""

    @pytest.fixture
    def mock_db(self):
        mock = MagicMock()
        return mock

    @pytest.fixture
    def mock_settings(self):
        settings = Mock()
        settings.google_cloud_project = "test-project"
        settings.youtube_quota_daily_limit = 10000
        settings.youtube_quota_upload_cost = 300
        settings.youtube_quota_safety_margin = 500
        return settings

    @pytest.fixture
    def quota_service(self, mock_db, mock_settings):
        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            from backend.services.youtube_quota_service import YouTubeQuotaService
            service = YouTubeQuotaService(db=mock_db)
            return service

    # =========================================================================
    # check_quota_available Tests
    # =========================================================================

    def test_quota_available_when_no_usage(self, quota_service, mock_db, mock_settings):
        """Quota should be available when nothing consumed today."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is True
        assert remaining == 9500  # 10000 - 500 safety margin
        assert "remaining" in message.lower()

    def test_quota_available_with_partial_usage(self, quota_service, mock_db, mock_settings):
        """Quota should be available when partial usage (under effective limit)."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 3000}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is True
        assert remaining == 6500  # 9500 - 3000

    def test_quota_denied_when_insufficient(self, quota_service, mock_db, mock_settings):
        """Quota should be denied when remaining units less than estimated cost."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 9300}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is False
        assert remaining == 200  # 9500 - 9300
        assert "insufficient" in message.lower()

    def test_quota_denied_at_effective_limit(self, quota_service, mock_db, mock_settings):
        """Quota should be denied when exactly at effective limit."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 9500}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is False
        assert remaining == 0

    def test_quota_unlimited_when_limit_zero(self, quota_service, mock_db, mock_settings):
        """Quota should be unlimited when daily limit is 0."""
        mock_settings.youtube_quota_daily_limit = 0

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is True
        assert remaining == -1
        assert "no youtube quota limit" in message.lower()

    def test_quota_check_with_custom_estimated_units(self, quota_service, mock_db, mock_settings):
        """Check quota with a custom estimated_units value."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 9400}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            # 50 units should fit (9400 + 50 = 9450 < 9500)
            allowed, remaining, _ = quota_service.check_quota_available(estimated_units=50)
            assert allowed is True

            # 200 units should not fit (9400 + 200 = 9600 > 9500)
            allowed, remaining, _ = quota_service.check_quota_available(estimated_units=200)
            assert allowed is False

    # =========================================================================
    # record_operation Tests
    # =========================================================================

    def test_record_operation_new_document(self, quota_service, mock_db, mock_settings):
        """Recording an operation should create document if none exists."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            quota_service.record_operation(
                job_id="test-job-123",
                user_email="user@example.com",
                operation="videos.insert",
                units=100,
            )

        # Verify transaction was used
        mock_db.transaction.assert_called_once()

    def test_record_operation_uses_known_costs(self, quota_service, mock_db, mock_settings):
        """When units not specified, should look up from QUOTA_COSTS."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_transaction = Mock()
        mock_db.transaction.return_value = mock_transaction

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            quota_service.record_operation(
                job_id="test-job-123",
                user_email="user@example.com",
                operation="thumbnails.set",
                # units not specified - should use QUOTA_COSTS["thumbnails.set"] = 50
            )

        mock_db.transaction.assert_called_once()

    # =========================================================================
    # get_quota_stats Tests
    # =========================================================================

    def test_get_quota_stats_no_usage(self, quota_service, mock_db, mock_settings):
        """Stats should show zero usage when no document exists."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            stats = quota_service.get_quota_stats()

        assert stats["units_consumed"] == 0
        assert stats["units_limit"] == 10000
        assert stats["effective_limit"] == 9500
        assert stats["units_remaining"] == 9500
        assert stats["upload_cost"] == 300
        assert stats["estimated_uploads_remaining"] == 31  # 9500 // 300
        assert stats["operations_count"] == 0
        assert "seconds_until_reset" in stats

    def test_get_quota_stats_with_usage(self, quota_service, mock_db, mock_settings):
        """Stats should reflect current usage."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "units_consumed": 2700,
            "operations": [
                {"operation": "videos.insert", "units": 100},
                {"operation": "search.list", "units": 100},
                {"operation": "thumbnails.set", "units": 50},
            ] * 3 + [
                {"operation": "search.list", "units": 100},
                {"operation": "videos.insert", "units": 100},
                {"operation": "thumbnails.set", "units": 50},
            ] * 3 + [
                {"operation": "search.list", "units": 100},
                {"operation": "videos.insert", "units": 100},
                {"operation": "thumbnails.set", "units": 50},
            ] * 3,
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            stats = quota_service.get_quota_stats()

        assert stats["units_consumed"] == 2700
        assert stats["units_remaining"] == 6800  # 9500 - 2700
        assert stats["estimated_uploads_remaining"] == 22  # 6800 // 300

    # =========================================================================
    # _get_units_consumed_today Tests
    # =========================================================================

    def test_get_units_consumed_no_document(self, quota_service, mock_db, mock_settings):
        """Should return 0 when no tracking document exists."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            consumed = quota_service._get_units_consumed_today()

        assert consumed == 0

    def test_get_units_consumed_with_document(self, quota_service, mock_db, mock_settings):
        """Should return units_consumed from document."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 4500}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            consumed = quota_service._get_units_consumed_today()

        assert consumed == 4500


class TestQuotaCosts:
    """Test that known API operation costs are correctly defined."""

    def test_known_costs(self):
        from backend.services.youtube_quota_service import QUOTA_COSTS

        assert QUOTA_COSTS["videos.insert"] == 100
        assert QUOTA_COSTS["search.list"] == 100
        assert QUOTA_COSTS["thumbnails.set"] == 50
        assert QUOTA_COSTS["videos.delete"] == 50
        assert QUOTA_COSTS["channels.list"] == 1


class TestHelperFunctions:
    """Test module-level helper functions."""

    def test_get_today_date_pt_format(self):
        from backend.services.youtube_quota_service import _get_today_date_pt
        date_str = _get_today_date_pt()
        # Should be YYYY-MM-DD format
        assert len(date_str) == 10
        assert date_str[4] == "-"
        assert date_str[7] == "-"

    def test_seconds_until_midnight_pt_positive(self):
        from backend.services.youtube_quota_service import _seconds_until_midnight_pt
        seconds = _seconds_until_midnight_pt()
        assert 0 < seconds <= 86400  # Between 0 and 24 hours
