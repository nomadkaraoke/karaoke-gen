"""
Unit tests for YouTubeQuotaService.

Tests GCP Cloud Monitoring integration, pending buffer, quota availability
checks, and cache behavior for YouTube Data API v3 quota tracking.
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())
sys.modules.setdefault('google.cloud.monitoring_v3', MagicMock())


PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


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

    @pytest.fixture(autouse=True)
    def reset_gcp_cache(self):
        """Reset the GCP cache before each test."""
        import backend.services.youtube_quota_service as mod
        mod._gcp_cache["value"] = None
        mod._gcp_cache["timestamp"] = 0.0
        yield

    # =========================================================================
    # check_quota_available Tests
    # =========================================================================

    def test_quota_available_when_no_usage(self, quota_service, mock_db, mock_settings):
        """Quota should be available when GCP reports 0 and no pending."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch.object(quota_service, '_get_gcp_usage', return_value=0):
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is True
        assert remaining == 9500  # 10000 - 500 safety margin
        assert "remaining" in message.lower()

    def test_quota_available_with_partial_gcp_usage(self, quota_service, mock_db, mock_settings):
        """Quota should be available when GCP shows partial usage."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch.object(quota_service, '_get_gcp_usage', return_value=3000):
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is True
        assert remaining == 6500  # 9500 - 3000

    def test_quota_uses_gcp_plus_pending(self, quota_service, mock_db, mock_settings):
        """Quota check should combine GCP usage and pending buffer."""
        # Pending doc with recent entry
        now = datetime.now(PACIFIC_TZ)
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "pending_uploads": [
                {"job_id": "job-1", "units": 300, "recorded_at": now},
            ]
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch.object(quota_service, '_get_gcp_usage', return_value=9000):
            # GCP 9000 + pending 300 = 9300. Need 300 more = 9600 > 9500
            allowed, remaining, message = quota_service.check_quota_available()

        assert allowed is False
        assert remaining == 200  # 9500 - 9300
        assert "insufficient" in message.lower()

    def test_quota_denied_at_effective_limit(self, quota_service, mock_db, mock_settings):
        """Quota should be denied when exactly at effective limit."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch.object(quota_service, '_get_gcp_usage', return_value=9500):
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
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch.object(quota_service, '_get_gcp_usage', return_value=9400):
            # 50 units should fit (9400 + 50 = 9450 < 9500)
            allowed, remaining, _ = quota_service.check_quota_available(estimated_units=50)
            assert allowed is True

            # 200 units should not fit (9400 + 200 = 9600 > 9500)
            allowed, remaining, _ = quota_service.check_quota_available(estimated_units=200)
            assert allowed is False

    # =========================================================================
    # record_upload Tests
    # =========================================================================

    def test_record_upload_new_document(self, quota_service, mock_db, mock_settings):
        """Recording an upload should create pending document if none exists."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            quota_service.record_upload(job_id="test-job-123")

        mock_db.transaction.assert_called_once()

    def test_record_upload_uses_default_cost(self, quota_service, mock_db, mock_settings):
        """Record upload should use settings.youtube_quota_upload_cost by default."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            quota_service.record_upload(job_id="test-job-123")

        # Verify it wrote to the pending collection
        mock_db.collection.assert_called_with("youtube_quota_pending")

    def test_record_upload_custom_units(self, quota_service, mock_db, mock_settings):
        """Record upload should accept custom units."""
        import google.cloud.firestore as firestore_module
        firestore_module.transactional = lambda f: f

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.transaction.return_value = Mock()

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            quota_service.record_upload(job_id="test-job-123", units=150)

        mock_db.transaction.assert_called_once()

    # =========================================================================
    # _get_pending_units Tests
    # =========================================================================

    def test_pending_units_no_document(self, quota_service, mock_db, mock_settings):
        """Should return 0 when no pending document exists."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            pending = quota_service._get_pending_units()

        assert pending == 0

    def test_pending_units_counts_recent_entries(self, quota_service, mock_db, mock_settings):
        """Should count only entries less than 10 min old."""
        now = datetime.now(PACIFIC_TZ)
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "pending_uploads": [
                {"job_id": "job-1", "units": 300, "recorded_at": now},
                {"job_id": "job-2", "units": 300, "recorded_at": now - timedelta(minutes=5)},
            ]
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            pending = quota_service._get_pending_units()

        assert pending == 600  # Both entries are within 10 min

    def test_pending_units_ignores_expired_entries(self, quota_service, mock_db, mock_settings):
        """Should ignore entries older than PENDING_EXPIRY_MINUTES."""
        now = datetime.now(PACIFIC_TZ)
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "pending_uploads": [
                {"job_id": "job-1", "units": 300, "recorded_at": now},
                {"job_id": "job-2", "units": 300, "recorded_at": now - timedelta(minutes=15)},
            ]
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            pending = quota_service._get_pending_units()

        assert pending == 300  # Only the recent entry

    # =========================================================================
    # _get_gcp_usage Tests (cache behavior)
    # =========================================================================

    def test_gcp_usage_cache_hit(self, quota_service, mock_settings):
        """Should return cached value when cache is fresh."""
        import backend.services.youtube_quota_service as mod
        mod._gcp_cache["value"] = 5000
        mod._gcp_cache["timestamp"] = time.time()  # Fresh

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            result = quota_service._get_gcp_usage()

        assert result == 5000

    def test_gcp_usage_cache_miss_queries_gcp(self, quota_service, mock_settings):
        """Should query GCP when cache is stale."""
        import backend.services.youtube_quota_service as mod
        mod._gcp_cache["value"] = 1000
        mod._gcp_cache["timestamp"] = time.time() - 120  # Stale (>60s)

        mock_point = Mock()
        mock_point.value.int64_value = 2500
        mock_ts = Mock()
        mock_ts.points = [mock_point]

        mock_client = MagicMock()
        mock_client.list_time_series.return_value = [mock_ts]

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch('google.cloud.monitoring_v3.MetricServiceClient', return_value=mock_client):
            result = quota_service._get_gcp_usage()

        assert result == 2500

    def test_gcp_usage_fallback_to_stale_cache(self, quota_service, mock_settings):
        """Should fall back to stale cache on GCP error."""
        import backend.services.youtube_quota_service as mod
        mod._gcp_cache["value"] = 3000
        mod._gcp_cache["timestamp"] = time.time() - 120  # Stale

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch('google.cloud.monitoring_v3.MetricServiceClient', side_effect=Exception("API Error")):
            result = quota_service._get_gcp_usage()

        assert result == 3000  # Stale cache value

    def test_gcp_usage_returns_zero_on_error_no_cache(self, quota_service, mock_settings):
        """Should return 0 when GCP fails and no cache exists."""
        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch('google.cloud.monitoring_v3.MetricServiceClient', side_effect=Exception("API Error")):
            result = quota_service._get_gcp_usage()

        assert result == 0

    # =========================================================================
    # get_quota_stats Tests
    # =========================================================================

    def test_get_quota_stats_no_usage(self, quota_service, mock_db, mock_settings):
        """Stats should show zero usage when GCP is 0 and no pending."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch.object(quota_service, '_get_gcp_usage', return_value=0):
            stats = quota_service.get_quota_stats()

        assert stats["units_consumed"] == 0
        assert stats["gcp_usage"] == 0
        assert stats["pending_units"] == 0
        assert stats["units_limit"] == 10000
        assert stats["effective_limit"] == 9500
        assert stats["units_remaining"] == 9500
        assert stats["upload_cost"] == 300
        assert stats["estimated_uploads_remaining"] == 31  # 9500 // 300
        assert stats["upload_count"] == 0
        assert "seconds_until_reset" in stats

    def test_get_quota_stats_with_gcp_and_pending(self, quota_service, mock_db, mock_settings):
        """Stats should reflect GCP usage + pending buffer."""
        now = datetime.now(PACIFIC_TZ)
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "pending_uploads": [
                {"job_id": "job-1", "units": 300, "recorded_at": now},
                {"job_id": "job-2", "units": 300, "recorded_at": now},
            ]
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.youtube_quota_service.settings', mock_settings), \
             patch.object(quota_service, '_get_gcp_usage', return_value=2100):
            stats = quota_service.get_quota_stats()

        assert stats["gcp_usage"] == 2100
        assert stats["pending_units"] == 600
        assert stats["units_consumed"] == 2700  # 2100 + 600
        assert stats["units_remaining"] == 6800  # 9500 - 2700
        assert stats["estimated_uploads_remaining"] == 22  # 6800 // 300
        assert stats["upload_count"] == 2


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
