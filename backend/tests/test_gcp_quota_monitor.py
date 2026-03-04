"""
Unit tests for YouTubeQuotaService.get_gcp_quota_usage().

Tests GCP Cloud Monitoring integration for quota drift detection,
caching behavior, and graceful fallbacks.
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

# Mock Google Cloud before imports
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())


PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


class TestGCPQuotaMonitor:
    """Test get_gcp_quota_usage() on YouTubeQuotaService."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Firestore client."""
        mock = MagicMock()
        return mock

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = Mock()
        settings.google_cloud_project = "test-project"
        settings.youtube_quota_daily_limit = 10000
        settings.youtube_quota_upload_cost = 300
        settings.youtube_quota_safety_margin = 500
        return settings

    @pytest.fixture(autouse=True)
    def clear_gcp_cache(self):
        """Clear the module-level GCP quota cache before each test."""
        import backend.services.youtube_quota_service as mod
        mod._gcp_quota_cache = None
        mod._gcp_quota_cache_time = 0
        yield
        # Also clear after test to avoid leaking state
        mod._gcp_quota_cache = None
        mod._gcp_quota_cache_time = 0

    @pytest.fixture
    def quota_service(self, mock_db, mock_settings):
        """Create YouTubeQuotaService instance with mocks."""
        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            from backend.services.youtube_quota_service import YouTubeQuotaService
            service = YouTubeQuotaService(db=mock_db)
            return service

    def _make_mock_point(self, value, end_time):
        """Create a mock Cloud Monitoring data point."""
        point = Mock()
        point.value.int64_value = value
        point.interval.end_time = end_time
        return point

    def _make_mock_time_series(self, points):
        """Create a mock time series with given points."""
        ts = Mock()
        ts.points = points
        return ts

    # =========================================================================
    # Successful Query Tests
    # =========================================================================

    def test_successful_query_returns_units_consumed(self, quota_service, mock_db, mock_settings):
        """Test successful GCP monitoring query returns units and drift info."""
        # Mock Firestore for _get_units_consumed_today (for drift calc)
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 800}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock the Cloud Monitoring client
        now_pt = datetime.now(PACIFIC_TZ)
        mock_point = self._make_mock_point(850, now_pt)
        mock_ts = self._make_mock_time_series([mock_point])

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = [mock_ts]

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        # The method does `from google.cloud import monitoring_v3` locally,
        # so we inject the mock into sys.modules for that import to resolve.
        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                result = quota_service.get_gcp_quota_usage()
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result["available"] is True
        assert result["gcp_units_consumed"] == 850
        assert result["gcp_last_datapoint_time"] is not None
        # Drift = abs(850 - 800) = 50
        assert result["drift"] == 50
        assert result["drift_alert"] is False  # 50/800 = 6.25% < 10%

    def test_successful_query_multiple_time_series(self, quota_service, mock_db, mock_settings):
        """Test query with multiple time series sums all values."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 500}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        now_pt = datetime.now(PACIFIC_TZ)
        mock_point1 = self._make_mock_point(200, now_pt)
        mock_point2 = self._make_mock_point(300, now_pt)
        mock_ts1 = self._make_mock_time_series([mock_point1])
        mock_ts2 = self._make_mock_time_series([mock_point2])

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = [mock_ts1, mock_ts2]

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                result = quota_service.get_gcp_quota_usage()
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result["available"] is True
        assert result["gcp_units_consumed"] == 500  # 200 + 300

    def test_successful_query_no_data_points(self, quota_service, mock_db, mock_settings):
        """Test query that returns no data points (e.g., start of day)."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = []

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                result = quota_service.get_gcp_quota_usage()
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result["available"] is True
        assert result["gcp_units_consumed"] == 0
        assert result["gcp_last_datapoint_time"] is None
        assert result["drift"] is None  # No GCP data to compare

    # =========================================================================
    # Caching Tests
    # =========================================================================

    def test_caching_returns_cached_result(self, quota_service, mock_db, mock_settings):
        """Test that second call within 5 min returns cached result."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = []

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                # First call - should hit the monitoring API
                result1 = quota_service.get_gcp_quota_usage()
                assert mock_monitoring_client.list_time_series.call_count == 1

                # Second call - should use cache
                result2 = quota_service.get_gcp_quota_usage()
                assert mock_monitoring_client.list_time_series.call_count == 1  # Not called again
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result1 == result2

    def test_cache_expires_after_ttl(self, quota_service, mock_db, mock_settings):
        """Test that cache expires and re-queries after TTL."""
        import backend.services.youtube_quota_service as mod

        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = []

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                # First call
                quota_service.get_gcp_quota_usage()
                assert mock_monitoring_client.list_time_series.call_count == 1

                # Simulate cache expiry by backdating the cache time
                mod._gcp_quota_cache_time = time.time() - 301  # 301 seconds ago (TTL is 300)

                # Second call - should re-query
                quota_service.get_gcp_quota_usage()
                assert mock_monitoring_client.list_time_series.call_count == 2
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

    # =========================================================================
    # ImportError Fallback Tests
    # =========================================================================

    def test_import_error_fallback(self, quota_service, mock_settings):
        """Test graceful fallback when google-cloud-monitoring is not installed."""
        # Remove the module from sys.modules so the import fails
        saved = sys.modules.pop('google.cloud.monitoring_v3', None)

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            # Patch the import to raise ImportError
            original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

            def mock_import(name, *args, **kwargs):
                if 'monitoring_v3' in name:
                    raise ImportError("No module named 'google.cloud.monitoring_v3'")
                return original_import(name, *args, **kwargs)

            with patch('builtins.__import__', side_effect=mock_import):
                result = quota_service.get_gcp_quota_usage()

        assert result == {"available": False}

        # Restore if it was there
        if saved is not None:
            sys.modules['google.cloud.monitoring_v3'] = saved

    # =========================================================================
    # General Exception Fallback Tests
    # =========================================================================

    def test_general_exception_fallback(self, quota_service, mock_settings):
        """Test graceful fallback when Cloud Monitoring query raises an error."""
        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.side_effect = Exception("API quota exceeded")

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                result = quota_service.get_gcp_quota_usage()
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result == {"available": False}

    # =========================================================================
    # Drift Alert Tests
    # =========================================================================

    def test_drift_alert_when_drift_exceeds_threshold(self, quota_service, mock_db, mock_settings):
        """Test drift alert is triggered when drift > 10% of Firestore value."""
        # Firestore says 1000 units consumed
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 1000}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        # GCP says 1200 units consumed (200 drift = 20% > 10%)
        now_pt = datetime.now(PACIFIC_TZ)
        mock_point = self._make_mock_point(1200, now_pt)
        mock_ts = self._make_mock_time_series([mock_point])

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = [mock_ts]

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                result = quota_service.get_gcp_quota_usage()
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result["available"] is True
        assert result["drift"] == 200  # abs(1200 - 1000)
        assert result["drift_alert"] is True  # 200/1000 = 20% > 10%

    def test_no_drift_alert_within_threshold(self, quota_service, mock_db, mock_settings):
        """Test no drift alert when drift <= 10% of Firestore value."""
        # Firestore says 1000 units consumed
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"units_consumed": 1000}
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        # GCP says 1050 units consumed (50 drift = 5% <= 10%)
        now_pt = datetime.now(PACIFIC_TZ)
        mock_point = self._make_mock_point(1050, now_pt)
        mock_ts = self._make_mock_time_series([mock_point])

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = [mock_ts]

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                result = quota_service.get_gcp_quota_usage()
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result["available"] is True
        assert result["drift"] == 50
        assert result["drift_alert"] is False

    def test_no_drift_when_firestore_zero(self, quota_service, mock_db, mock_settings):
        """Test drift_alert is False when Firestore has zero usage (avoid division by zero)."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        now_pt = datetime.now(PACIFIC_TZ)
        mock_point = self._make_mock_point(100, now_pt)
        mock_ts = self._make_mock_time_series([mock_point])

        mock_monitoring_client = Mock()
        mock_monitoring_client.list_time_series.return_value = [mock_ts]

        mock_monitoring_v3 = MagicMock()
        mock_monitoring_v3.MetricServiceClient.return_value = mock_monitoring_client

        with patch('backend.services.youtube_quota_service.settings', mock_settings):
            sys.modules['google.cloud.monitoring_v3'] = mock_monitoring_v3
            try:
                result = quota_service.get_gcp_quota_usage()
            finally:
                sys.modules.pop('google.cloud.monitoring_v3', None)

        assert result["available"] is True
        assert result["gcp_units_consumed"] == 100
        # Firestore consumed is 0, so drift_alert should be False (no division by zero)
        assert result["drift_alert"] is False
