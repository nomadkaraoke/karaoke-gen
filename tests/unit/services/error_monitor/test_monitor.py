"""Tests for error_monitor.monitor module.

All external dependencies (Cloud Logging, Firestore, Discord, LLM, Secret Manager)
are mocked — no real GCP connections are made.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _import_monitor_module():
    from backend.services.error_monitor import monitor as m

    return m


def _import_error_monitor_class():
    from backend.services.error_monitor.monitor import ErrorMonitor

    return ErrorMonitor


# ---------------------------------------------------------------------------
# Helper to build a minimal mock log entry
# ---------------------------------------------------------------------------


def _make_entry(
    payload=None,
    resource_type="cloud_run_revision",
    service_name="karaoke-backend",
    job_name=None,
    function_name=None,
    instance_id=None,
    resource_name=None,
):
    """Return a mock Cloud Logging entry."""
    entry = MagicMock()
    entry.payload = payload
    entry.resource = MagicMock()
    entry.resource.type = resource_type
    entry.resource.labels = {
        "service_name": service_name,
        "job_name": job_name or "",
        "function_name": function_name or "",
        "instance_id": instance_id or "",
    }
    entry.labels = {}
    if resource_name:
        entry.labels["compute.googleapis.com/resource_name"] = resource_name
    return entry


# ---------------------------------------------------------------------------
# Tests: _build_log_filter
# ---------------------------------------------------------------------------


class TestBuildLogFilter:
    """_build_log_filter should produce correct Cloud Logging filter strings."""

    def test_cloud_run_revision_filter(self):
        m = _import_monitor_module()
        result = m._build_log_filter("cloud_run_revision", ["svc-a", "svc-b"], 15)
        assert 'resource.type="cloud_run_revision"' in result
        assert "severity>=ERROR" in result
        assert "svc-a" in result
        assert "svc-b" in result
        assert "resource.labels.service_name" in result

    def test_cloud_run_job_filter(self):
        m = _import_monitor_module()
        result = m._build_log_filter("cloud_run_job", ["job-x"], 15)
        assert 'resource.type="cloud_run_job"' in result
        assert "severity>=ERROR" in result
        assert "job-x" in result
        assert "resource.labels.job_name" in result

    def test_gce_instance_filter(self):
        m = _import_monitor_module()
        result = m._build_log_filter("gce_instance", ["enc-worker-a"], 15)
        assert 'resource.type="gce_instance"' in result
        assert "severity>=ERROR" in result
        assert "enc-worker-a" in result
        assert 'compute.googleapis.com/resource_name' in result

    def test_cloud_function_filter(self):
        m = _import_monitor_module()
        result = m._build_log_filter("cloud_function", ["fn-one", "fn-two"], 10)
        assert 'resource.type="cloud_function"' in result
        assert "fn-one" in result
        assert "fn-two" in result

    def test_filter_contains_timestamp(self):
        m = _import_monitor_module()
        result = m._build_log_filter("cloud_run_revision", ["svc"], 30)
        assert "timestamp>=" in result

    def test_single_resource_no_or_clause_needed(self):
        m = _import_monitor_module()
        result = m._build_log_filter("cloud_run_revision", ["only-svc"], 15)
        assert "only-svc" in result


# ---------------------------------------------------------------------------
# Tests: _extract_message
# ---------------------------------------------------------------------------


class TestExtractMessage:
    """_extract_message should handle all payload shapes."""

    def test_text_payload_string_returned_directly(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = "Something went wrong"
        result = m._extract_message(entry)
        assert result == "Something went wrong"

    def test_dict_payload_message_key(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = {"message": "DB connection refused"}
        result = m._extract_message(entry)
        assert result == "DB connection refused"

    def test_dict_payload_textPayload_key(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = {"textPayload": "File not found: /tmp/foo"}
        result = m._extract_message(entry)
        assert result == "File not found: /tmp/foo"

    def test_dict_payload_error_key(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = {"error": "timeout after 30s"}
        result = m._extract_message(entry)
        assert result == "timeout after 30s"

    def test_dict_payload_msg_key(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = {"msg": "panic: nil pointer dereference"}
        result = m._extract_message(entry)
        assert result == "panic: nil pointer dereference"

    def test_none_payload_returns_none(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = None
        result = m._extract_message(entry)
        assert result is None

    def test_dict_payload_no_known_key_returns_none(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = {"severity": "ERROR", "timestamp": "2026-04-10T10:00:00Z"}
        result = m._extract_message(entry)
        assert result is None

    def test_empty_string_payload_treated_as_text(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.payload = ""
        result = m._extract_message(entry)
        # Empty string is falsy — should return the string or None depending on impl
        # As long as it doesn't crash; either return value is acceptable
        assert result is None or result == ""


# ---------------------------------------------------------------------------
# Tests: _extract_service_name
# ---------------------------------------------------------------------------


class TestExtractServiceName:
    """_extract_service_name should return the correct label for each resource type."""

    def test_cloud_run_revision_returns_service_name(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.resource = MagicMock()
        entry.resource.labels = {"service_name": "karaoke-backend"}
        result = m._extract_service_name(entry, "cloud_run_revision")
        assert result == "karaoke-backend"

    def test_cloud_run_job_returns_job_name(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.resource = MagicMock()
        entry.resource.labels = {"job_name": "video-encoding-job"}
        result = m._extract_service_name(entry, "cloud_run_job")
        assert result == "video-encoding-job"

    def test_cloud_function_returns_function_name(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.resource = MagicMock()
        entry.resource.labels = {"function_name": "gdrive-validator"}
        result = m._extract_service_name(entry, "cloud_function")
        assert result == "gdrive-validator"

    def test_gce_instance_returns_resource_name_from_labels(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.resource = MagicMock()
        entry.resource.labels = {}
        entry.labels = {"compute.googleapis.com/resource_name": "encoding-worker-a"}
        result = m._extract_service_name(entry, "gce_instance")
        assert result == "encoding-worker-a"

    def test_gce_instance_falls_back_to_instance_id(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.resource = MagicMock()
        entry.resource.labels = {"instance_id": "1234567890"}
        entry.labels = {}
        result = m._extract_service_name(entry, "gce_instance")
        assert result == "1234567890"

    def test_missing_labels_returns_unknown(self):
        m = _import_monitor_module()
        entry = MagicMock()
        entry.resource = MagicMock()
        entry.resource.labels = {}
        entry.labels = {}
        # Should not raise — returns some fallback string
        result = m._extract_service_name(entry, "cloud_run_revision")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: _classify_resource_type
# ---------------------------------------------------------------------------


class TestClassifyResourceType:
    """_classify_resource_type should map log resource types to canonical types."""

    def test_cloud_run_revision_mapped(self):
        m = _import_monitor_module()
        assert m._classify_resource_type("cloud_run_revision") == "cloud_run_service"

    def test_cloud_run_job_mapped(self):
        m = _import_monitor_module()
        assert m._classify_resource_type("cloud_run_job") == "cloud_run_job"

    def test_cloud_function_mapped(self):
        m = _import_monitor_module()
        assert m._classify_resource_type("cloud_function") == "cloud_function"

    def test_gce_instance_mapped(self):
        m = _import_monitor_module()
        assert m._classify_resource_type("gce_instance") == "gce_instance"

    def test_unknown_type_returns_original_or_unknown(self):
        m = _import_monitor_module()
        result = m._classify_resource_type("unknown_type")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: _group_by_pattern (via _run_monitor_cycle logic)
# ---------------------------------------------------------------------------


class TestGroupByPattern:
    """_group_by_pattern should correctly group entries by hash and count duplicates."""

    def _make_monitor(self):
        """Create an ErrorMonitor instance without calling __init__ (no GCP calls)."""
        ErrorMonitor = _import_error_monitor_class()
        monitor = ErrorMonitor.__new__(ErrorMonitor)
        monitor.logging_client = MagicMock()
        monitor.firestore_adapter = MagicMock()
        monitor.discord = MagicMock()
        return monitor

    def test_groups_entries_by_hash(self):
        monitor = self._make_monitor()
        m = _import_monitor_module()

        entries = [
            {"service": "svc-a", "normalized": "Connection refused", "message": "msg1"},
            {"service": "svc-a", "normalized": "Connection refused", "message": "msg2"},
            {"service": "svc-b", "normalized": "Timeout error", "message": "msg3"},
        ]
        result = m._group_by_pattern(entries)
        # 2 unique patterns
        assert len(result) == 2

    def test_counts_duplicates_correctly(self):
        monitor = self._make_monitor()
        m = _import_monitor_module()

        entries = [
            {"service": "svc-a", "normalized": "Connection refused", "message": "msg1"},
            {"service": "svc-a", "normalized": "Connection refused", "message": "msg2"},
            {"service": "svc-a", "normalized": "Connection refused", "message": "msg3"},
        ]
        result = m._group_by_pattern(entries)
        assert len(result) == 1
        group = list(result.values())[0]
        assert group["count"] == 3

    def test_each_group_has_sample_message(self):
        m = _import_monitor_module()

        entries = [
            {"service": "svc-a", "normalized": "DB error", "message": "DB error at line 42"},
        ]
        result = m._group_by_pattern(entries)
        group = list(result.values())[0]
        assert "sample_message" in group
        assert group["sample_message"] == "DB error at line 42"


# ---------------------------------------------------------------------------
# Tests: _is_spike
# ---------------------------------------------------------------------------


class TestIsSpike:
    """_is_spike should detect spikes based on multiplier and minimum count."""

    def _make_monitor(self):
        ErrorMonitor = _import_error_monitor_class()
        monitor = ErrorMonitor.__new__(ErrorMonitor)
        monitor.logging_client = MagicMock()
        monitor.firestore_adapter = MagicMock()
        monitor.discord = MagicMock()
        return monitor

    def test_detects_spike_when_count_exceeds_multiplier_and_min(self):
        m = _import_monitor_module()
        # rolling_counts of 1 each for the past 5 periods, current count = 10
        # avg = 1.0, SPIKE_MULTIPLIER=5.0, so 10 > 5 and 10 >= SPIKE_MIN_COUNT=5
        rolling_counts = [{"count": 1}, {"count": 1}, {"count": 1}, {"count": 1}, {"count": 1}]
        assert m._is_spike(current_count=10, rolling_counts=rolling_counts) is True

    def test_rejects_non_spike_count(self):
        m = _import_monitor_module()
        rolling_counts = [{"count": 4}, {"count": 4}, {"count": 4}, {"count": 4}]
        # avg = 4.0, 5x = 20, current=5 → not a spike
        assert m._is_spike(current_count=5, rolling_counts=rolling_counts) is False

    def test_rejects_spike_below_min_count(self):
        m = _import_monitor_module()
        # avg = 0.5, 5x = 2.5, current = 3 → exceeds multiplier but below SPIKE_MIN_COUNT
        rolling_counts = [{"count": 1}, {"count": 0}]
        assert m._is_spike(current_count=3, rolling_counts=rolling_counts) is False

    def test_empty_rolling_counts_not_a_spike(self):
        m = _import_monitor_module()
        assert m._is_spike(current_count=100, rolling_counts=[]) is False


# ---------------------------------------------------------------------------
# Tests: _rolling_average
# ---------------------------------------------------------------------------


class TestRollingAverage:
    """_rolling_average should compute mean of rolling_counts."""

    def test_computes_average_correctly(self):
        m = _import_monitor_module()
        rolling = [{"count": 2}, {"count": 4}, {"count": 6}]
        result = m._rolling_average(rolling)
        assert result == pytest.approx(4.0)

    def test_single_entry_returns_that_value(self):
        m = _import_monitor_module()
        rolling = [{"count": 7}]
        result = m._rolling_average(rolling)
        assert result == pytest.approx(7.0)

    def test_empty_list_returns_zero(self):
        m = _import_monitor_module()
        result = m._rolling_average([])
        assert result == 0.0

    def test_counts_with_zeros(self):
        m = _import_monitor_module()
        rolling = [{"count": 0}, {"count": 0}, {"count": 10}]
        result = m._rolling_average(rolling)
        assert result == pytest.approx(10 / 3)


# ---------------------------------------------------------------------------
# Tests: _run_monitor_cycle end-to-end
# ---------------------------------------------------------------------------


def _make_monitor_with_mocks():
    """Return an ErrorMonitor with all external dependencies mocked."""
    ErrorMonitor = _import_error_monitor_class()
    monitor = ErrorMonitor.__new__(ErrorMonitor)
    monitor.logging_client = MagicMock()
    monitor.firestore_adapter = MagicMock()
    monitor.discord = MagicMock()
    return monitor


class TestRunMonitorCycleNoErrors:
    """When no error entries are found, auto-resolve check should still run."""

    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_auto_resolve_called_even_when_no_errors(self, mock_digest, mock_llm):
        monitor = _make_monitor_with_mocks()

        # Cloud Logging returns empty results for all resource types
        monitor.logging_client.list_entries.return_value = iter([])

        # Firestore adapter returns no patterns eligible for auto-resolve
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []

        monitor._run_monitor_cycle()

        # Auto-resolve check must always run
        monitor.firestore_adapter.get_patterns_for_auto_resolve.assert_called_once()

    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_no_discord_sent_when_no_errors(self, mock_digest, mock_llm):
        monitor = _make_monitor_with_mocks()

        monitor.logging_client.list_entries.return_value = iter([])
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []

        monitor._run_monitor_cycle()

        monitor.discord.send_message.assert_not_called()


class TestRunMonitorCycleOneNewError:
    """When one new error pattern is found, an individual alert should be sent."""

    def _make_log_entry(self):
        entry = MagicMock()
        entry.payload = "RuntimeError: connection refused"
        entry.resource = MagicMock()
        entry.resource.type = "cloud_run_revision"
        entry.resource.labels = {"service_name": "karaoke-backend"}
        entry.labels = {}
        return entry

    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_individual_alert_sent_for_new_pattern(self, mock_digest, mock_llm, mock_ignore):
        from backend.services.error_monitor.firestore_adapter import UpsertResult

        monitor = _make_monitor_with_mocks()

        log_entry = self._make_log_entry()
        monitor.logging_client.list_entries.return_value = iter([log_entry])

        # Upsert returns is_new=True for first encounter
        monitor.firestore_adapter.upsert_pattern.return_value = UpsertResult(
            pattern_id="abc123", is_new=True
        )
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_monitor_cycle()

        # An alert should have been sent
        assert monitor.discord.send_message.call_count >= 1

    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_upsert_called_for_each_pattern_group(self, mock_digest, mock_llm, mock_ignore):
        from backend.services.error_monitor.firestore_adapter import UpsertResult

        monitor = _make_monitor_with_mocks()

        log_entry = self._make_log_entry()
        monitor.logging_client.list_entries.return_value = iter([log_entry])

        monitor.firestore_adapter.upsert_pattern.return_value = UpsertResult(
            pattern_id="abc123", is_new=True
        )
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_monitor_cycle()

        assert monitor.firestore_adapter.upsert_pattern.call_count >= 1

    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_known_issue_skipped(self, mock_digest, mock_llm, mock_ignore):
        """Entries matched by should_ignore should not be upserted or alerted."""
        from backend.services.error_monitor.known_issues import IgnoreReason

        # Override: should_ignore returns a reason (i.e., ignore this entry)
        mock_ignore.return_value = IgnoreReason(
            pattern_name="startup_probe",
            reason="Cloud Run cold start — startup probe failure is transient",
        )

        monitor = _make_monitor_with_mocks()
        log_entry = self._make_log_entry()
        monitor.logging_client.list_entries.return_value = iter([log_entry])
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []

        monitor._run_monitor_cycle()

        monitor.firestore_adapter.upsert_pattern.assert_not_called()
        monitor.discord.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _run_daily_digest (P0)
# ---------------------------------------------------------------------------


class TestRunDailyDigest:
    """_run_daily_digest should fetch active patterns and send a digest to Discord."""

    @pytest.fixture
    def monitor(self):
        ErrorMonitor = _import_error_monitor_class()
        m = ErrorMonitor.__new__(ErrorMonitor)
        m.logging_client = MagicMock()
        m.firestore_adapter = MagicMock()
        m.discord = MagicMock()
        m.discord.messages_sent = 0
        m.logger = MagicMock()
        return m

    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    def test_digest_sends_message_with_chart_emoji(self, mock_llm, monitor):
        """_run_daily_digest should send a message containing the 📊 emoji."""
        monitor.firestore_adapter.get_active_patterns.return_value = [
            {"service": "karaoke-backend", "total_count": 10, "status": "new"},
            {"service": "video-worker", "total_count": 5, "status": "acknowledged"},
        ]
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_daily_digest()

        assert monitor.discord.send_message.call_count == 1
        sent_content = monitor.discord.send_message.call_args[0][0]
        assert "📊" in sent_content

    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    def test_digest_includes_per_service_counts(self, mock_llm, monitor):
        """_run_daily_digest should aggregate counts per service."""
        monitor.firestore_adapter.get_active_patterns.return_value = [
            {"service": "svc-alpha", "total_count": 7, "status": "new"},
            {"service": "svc-alpha", "total_count": 3, "status": "acknowledged"},
            {"service": "svc-beta", "total_count": 2, "status": "new"},
        ]
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_daily_digest()

        sent_content = monitor.discord.send_message.call_args[0][0]
        # Both services should be mentioned in the digest
        assert "svc-alpha" in sent_content
        assert "svc-beta" in sent_content

    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    def test_digest_with_no_active_patterns(self, mock_llm, monitor):
        """_run_daily_digest should still send a digest even when there are no patterns."""
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_daily_digest()

        assert monitor.discord.send_message.call_count == 1
        sent_content = monitor.discord.send_message.call_args[0][0]
        assert "📊" in sent_content


# ---------------------------------------------------------------------------
# Tests: PatternData construction (P0)
# ---------------------------------------------------------------------------


class TestPatternDataConstruction:
    """upsert_pattern should be called with a PatternData object with correct field types."""

    def _make_log_entry(self, message="RuntimeError: disk full", service="karaoke-backend"):
        entry = MagicMock()
        entry.payload = message
        entry.resource = MagicMock()
        entry.resource.type = "cloud_run_revision"
        entry.resource.labels = {"service_name": service}
        entry.labels = {}
        return entry

    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_pattern_data_fields_have_correct_types(self, mock_digest, mock_llm, mock_ignore):
        """upsert_pattern receives a PatternData with correctly typed fields."""
        from backend.services.error_monitor.firestore_adapter import PatternData, UpsertResult

        monitor = _make_monitor_with_mocks()
        log_entry = self._make_log_entry()
        monitor.logging_client.list_entries.return_value = iter([log_entry])
        monitor.firestore_adapter.upsert_pattern.return_value = UpsertResult(
            pattern_id="xyz789", is_new=True
        )
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_monitor_cycle()

        assert monitor.firestore_adapter.upsert_pattern.call_count >= 1
        call_arg = monitor.firestore_adapter.upsert_pattern.call_args[0][0]
        assert isinstance(call_arg, PatternData)

        # pattern_id should be a non-empty hex string
        assert isinstance(call_arg.pattern_id, str)
        assert len(call_arg.pattern_id) > 0
        # All characters should be valid hex (the hash function produces hex)
        assert all(c in "0123456789abcdef" for c in call_arg.pattern_id)

        # service must match the log entry's service label
        assert call_arg.service == "karaoke-backend"

        # resource_type maps to canonical string
        assert isinstance(call_arg.resource_type, str)
        assert len(call_arg.resource_type) > 0

        # normalized_message and sample_message must be non-empty strings
        assert isinstance(call_arg.normalized_message, str)
        assert len(call_arg.normalized_message) > 0
        assert isinstance(call_arg.sample_message, str)
        assert len(call_arg.sample_message) > 0

        # count is a positive int
        assert isinstance(call_arg.count, int)
        assert call_arg.count >= 1

        # timestamp is a datetime
        assert isinstance(call_arg.timestamp, datetime)


# ---------------------------------------------------------------------------
# Tests: Discord alert argument verification (P0)
# ---------------------------------------------------------------------------


class TestDiscordAlertContent:
    """The Discord alert message should contain the service name and normalized message."""

    def _make_log_entry(self, message, service):
        entry = MagicMock()
        entry.payload = message
        entry.resource = MagicMock()
        entry.resource.type = "cloud_run_revision"
        entry.resource.labels = {"service_name": service}
        entry.labels = {}
        return entry

    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_new_pattern_alert_contains_service_name(self, mock_digest, mock_llm, mock_ignore):
        """The alert message sent to Discord should contain the originating service name."""
        from backend.services.error_monitor.firestore_adapter import UpsertResult

        service_name = "my-special-service"
        monitor = _make_monitor_with_mocks()
        log_entry = self._make_log_entry("RuntimeError: boom", service=service_name)
        monitor.logging_client.list_entries.return_value = iter([log_entry])
        monitor.firestore_adapter.upsert_pattern.return_value = UpsertResult(
            pattern_id="aabbcc", is_new=True
        )
        monitor.firestore_adapter.get_pattern.return_value = {"first_seen": "2026-01-01T00:00:00Z"}
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_monitor_cycle()

        assert monitor.discord.send_message.call_count >= 1
        sent_content = monitor.discord.send_message.call_args[0][0]
        assert service_name in sent_content

    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_new_pattern_alert_contains_normalized_message(
        self, mock_digest, mock_llm, mock_ignore
    ):
        """The alert message should contain a recognizable part of the normalized error."""
        from backend.services.error_monitor.firestore_adapter import UpsertResult

        monitor = _make_monitor_with_mocks()
        # Use a distinctive error phrase that should survive normalization
        log_entry = self._make_log_entry(
            "ConnectionError: database connection refused", service="karaoke-backend"
        )
        monitor.logging_client.list_entries.return_value = iter([log_entry])
        monitor.firestore_adapter.upsert_pattern.return_value = UpsertResult(
            pattern_id="def456", is_new=True
        )
        monitor.firestore_adapter.get_pattern.return_value = {"first_seen": "2026-01-01T00:00:00Z"}
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_monitor_cycle()

        assert monitor.discord.send_message.call_count >= 1
        sent_content = monitor.discord.send_message.call_args[0][0]
        # The normalized message should contain at least "connection" or "refused"
        assert "connection" in sent_content.lower() or "refused" in sent_content.lower()


# ---------------------------------------------------------------------------
# Tests: analyze_patterns input format (P0)
# ---------------------------------------------------------------------------


class TestAnalyzePatternsInputFormat:
    """When LLM is enabled, analyze_patterns is called with correctly shaped dicts."""

    def _make_log_entry(self, message, service):
        entry = MagicMock()
        entry.payload = message
        entry.resource = MagicMock()
        entry.resource.type = "cloud_run_revision"
        entry.resource.labels = {"service_name": service}
        entry.labels = {}
        return entry

    @patch("backend.services.error_monitor.monitor.analyze_patterns")
    @patch("backend.services.error_monitor.monitor.find_duplicate_patterns")
    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=True)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_analyze_patterns_receives_correct_keys(
        self, mock_digest, mock_llm, mock_ignore, mock_find_dups, mock_analyze
    ):
        """analyze_patterns should receive a list of dicts with service, normalized_message, count."""
        from backend.services.error_monitor.firestore_adapter import UpsertResult

        mock_analyze.return_value = MagicMock(incidents=[])
        mock_find_dups.return_value = []

        monitor = _make_monitor_with_mocks()

        # Create 2 distinct log entries so LLM dedup + analysis paths are triggered
        entry1 = self._make_log_entry("RuntimeError: disk full", service="svc-one")
        entry2 = self._make_log_entry("ValueError: invalid input", service="svc-two")
        monitor.logging_client.list_entries.side_effect = [
            iter([entry1, entry2]),
            iter([]),
            iter([]),
            iter([]),
        ]

        call_count = [0]

        def upsert_side_effect(data):
            call_count[0] += 1
            return UpsertResult(pattern_id=data.pattern_id, is_new=True)

        monitor.firestore_adapter.upsert_pattern.side_effect = upsert_side_effect
        monitor.firestore_adapter.get_pattern.return_value = {"first_seen": "2026-01-01T00:00:00Z"}
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_monitor_cycle()

        # analyze_patterns should have been called once both new patterns exist
        assert mock_analyze.call_count >= 1
        call_args = mock_analyze.call_args[0][0]
        assert isinstance(call_args, list)
        assert len(call_args) >= 1

        for item in call_args:
            assert "service" in item, f"Missing 'service' key in {item}"
            assert "normalized_message" in item, f"Missing 'normalized_message' key in {item}"
            assert "count" in item, f"Missing 'count' key in {item}"
            assert isinstance(item["service"], str)
            assert isinstance(item["normalized_message"], str)
            assert isinstance(item["count"], int)


# ---------------------------------------------------------------------------
# Tests: Spike detection flow (P1)
# ---------------------------------------------------------------------------


class TestSpikeDetectionFlow:
    """_run_monitor_cycle should send a spike alert when counts exceed the threshold."""

    def _make_log_entry(self, message="RuntimeError: timeout", service="karaoke-backend"):
        entry = MagicMock()
        entry.payload = message
        entry.resource = MagicMock()
        entry.resource.type = "cloud_run_revision"
        entry.resource.labels = {"service_name": service}
        entry.labels = {}
        return entry

    @patch("backend.services.error_monitor.monitor.should_ignore", return_value=None)
    @patch("backend.services.error_monitor.monitor.get_llm_enabled", return_value=False)
    @patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False)
    def test_spike_alert_sent_when_count_exceeds_threshold(
        self, mock_digest, mock_llm, mock_ignore
    ):
        """A spike alert (⚠️) is sent when current count is >5x rolling average and >= min."""
        from backend.services.error_monitor.firestore_adapter import UpsertResult

        monitor = _make_monitor_with_mocks()

        # Send 10 identical entries to make count = 10 (> SPIKE_MIN_COUNT=5)
        entries = [self._make_log_entry() for _ in range(10)]
        monitor.logging_client.list_entries.return_value = iter(entries)

        # Upsert returns is_new=False (existing pattern) so spike check runs
        monitor.firestore_adapter.upsert_pattern.return_value = UpsertResult(
            pattern_id="spike-pattern-1", is_new=False
        )

        # get_pattern returns a pattern with rolling_counts that average to 1
        # avg=1.0, 5x=5.0, current_count=10 → spike detected
        monitor.firestore_adapter.get_pattern.return_value = {
            "pattern_id": "spike-pattern-1",
            "rolling_counts": [{"count": 1}, {"count": 1}, {"count": 1}, {"count": 1}],
        }
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = []
        monitor.firestore_adapter.get_active_patterns.return_value = []
        monitor.discord.send_message.return_value = True

        monitor._run_monitor_cycle()

        # At least one Discord message should have been sent
        assert monitor.discord.send_message.call_count >= 1

        # At least one of the messages should be a spike alert (⚠️)
        all_calls = monitor.discord.send_message.call_args_list
        spike_messages = [c for c in all_calls if "⚠️" in c[0][0]]
        assert len(spike_messages) >= 1, (
            "Expected at least one spike alert (⚠️) but none found. "
            f"Messages sent: {[c[0][0][:80] for c in all_calls]}"
        )


# ---------------------------------------------------------------------------
# Tests: Auto-resolve alert (P1)
# ---------------------------------------------------------------------------


class TestAutoResolveAlert:
    """_check_auto_resolve should resolve eligible patterns and send a ✅ alert."""

    @pytest.fixture
    def monitor(self):
        ErrorMonitor = _import_error_monitor_class()
        m = ErrorMonitor.__new__(ErrorMonitor)
        m.logging_client = MagicMock()
        m.firestore_adapter = MagicMock()
        m.discord = MagicMock()
        m.discord.messages_sent = 0
        m.logger = MagicMock()
        return m

    def test_auto_resolve_calls_resolve_and_sends_alert(self, monitor):
        """When a pattern qualifies for auto-resolve, the pattern is resolved and ✅ is sent."""
        pattern = {
            "pattern_id": "resolve-me-123",
            "service": "karaoke-backend",
            "normalized_message": "Connection refused",
            "status": "new",
        }
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = [pattern]
        monitor.firestore_adapter.check_auto_resolve.return_value = 25.5  # hours silent
        monitor.discord.send_message.return_value = True

        monitor._check_auto_resolve()

        # auto_resolve_pattern must have been called with the correct pattern_id
        monitor.firestore_adapter.auto_resolve_pattern.assert_called_once_with(
            "resolve-me-123", 25.5
        )

        # A Discord message must have been sent
        assert monitor.discord.send_message.call_count == 1
        sent_content = monitor.discord.send_message.call_args[0][0]
        assert "✅" in sent_content

    def test_auto_resolve_no_alert_when_no_patterns_qualify(self, monitor):
        """When no patterns qualify for auto-resolve, no Discord alert is sent."""
        pattern = {
            "pattern_id": "still-active",
            "service": "svc-x",
            "normalized_message": "Disk full",
            "status": "acknowledged",
        }
        monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = [pattern]
        # check_auto_resolve returns None → pattern does not qualify yet
        monitor.firestore_adapter.check_auto_resolve.return_value = None

        monitor._check_auto_resolve()

        monitor.firestore_adapter.auto_resolve_pattern.assert_not_called()
        monitor.discord.send_message.assert_not_called()

    def test_auto_resolve_via_monitor_cycle(self, monitor):
        """_run_monitor_cycle with no errors should still trigger the auto-resolve check."""
        with patch(
            "backend.services.error_monitor.monitor.get_llm_enabled", return_value=False
        ), patch("backend.services.error_monitor.monitor.get_digest_mode", return_value=False):
            monitor.logging_client.list_entries.return_value = iter([])

            pattern = {
                "pattern_id": "cycle-resolve",
                "service": "bg-worker",
                "normalized_message": "queue timeout",
                "status": "new",
            }
            monitor.firestore_adapter.get_patterns_for_auto_resolve.return_value = [pattern]
            monitor.firestore_adapter.check_auto_resolve.return_value = 12.0
            monitor.firestore_adapter.get_active_patterns.return_value = []
            monitor.discord.send_message.return_value = True

            monitor._run_monitor_cycle()

            monitor.firestore_adapter.auto_resolve_pattern.assert_called_once()
            sent_content = monitor.discord.send_message.call_args[0][0]
            assert "✅" in sent_content
