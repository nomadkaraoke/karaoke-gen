"""Tests for error_monitor.discord module."""

from datetime import datetime
from unittest import mock

import pytest
import requests


class TestFormatNewPatternAlert:
    """Tests for format_new_pattern_alert formatting function."""

    def test_contains_service_name(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="Connection refused to db at 192.168.1.1",
            count=5,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert "karaoke-backend" in result

    def test_contains_normalized_message(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="Connection refused to db at 192.168.1.1",
            count=5,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert "Connection refused" in result

    def test_contains_red_circle_emoji(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="Connection refused to db",
            count=3,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert "🔴" in result

    def test_contains_prod_investigate_prompt(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="Connection refused to db",
            count=3,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert "/prod-investigate" in result

    def test_truncates_long_message(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert
        from backend.services.error_monitor.config import DISCORD_MAX_MESSAGE_LENGTH

        very_long_message = "x" * 3000
        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message=very_long_message,
            sample_message="sample",
            count=1,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert len(result) <= DISCORD_MAX_MESSAGE_LENGTH

    def test_contains_count(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Timeout error",
            sample_message="Timeout after 30s",
            count=42,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert "42" in result

    def test_contains_first_seen(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Timeout error",
            sample_message="Timeout after 30s",
            count=1,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert "2026-04-10T12:00:00Z" in result

    def test_contains_sample_message(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Timeout error",
            sample_message="Timeout after 30s on job xyz",
            count=1,
            first_seen="2026-04-10T12:00:00Z",
        )
        assert "Timeout after 30s on job xyz" in result

    def test_contains_resource_type_label(self):
        from backend.services.error_monitor.discord import format_new_pattern_alert

        result = format_new_pattern_alert(
            service="encoding-worker-a",
            resource_type="gce_instance",
            normalized_message="Disk full",
            sample_message="No space left on device",
            count=2,
            first_seen="2026-04-10T08:00:00Z",
        )
        # Resource type should appear somewhere in the message
        assert "gce_instance" in result or "GCE" in result or "VM" in result or "Instance" in result


class TestFormatIncidentAlert:
    """Tests for format_incident_alert formatting function."""

    def test_p0_uses_siren_emoji(self):
        from backend.services.error_monitor.discord import format_incident_alert

        result = format_incident_alert(
            title="Database down",
            severity="P0",
            root_cause="Firestore quota exceeded",
            suggested_fix="Increase quota",
            primary_service="karaoke-backend",
            patterns=[],
        )
        assert "🚨" in result

    def test_p1_uses_red_circle(self):
        from backend.services.error_monitor.discord import format_incident_alert

        result = format_incident_alert(
            title="High error rate",
            severity="P1",
            root_cause="Memory pressure",
            suggested_fix="Scale up",
            primary_service="karaoke-backend",
            patterns=[],
        )
        assert "🔴" in result

    def test_p2_uses_warning_emoji(self):
        from backend.services.error_monitor.discord import format_incident_alert

        result = format_incident_alert(
            title="Elevated latency",
            severity="P2",
            root_cause="Slow queries",
            suggested_fix="Add index",
            primary_service="karaoke-backend",
            patterns=[],
        )
        assert "⚠️" in result

    def test_p3_uses_yellow_circle(self):
        from backend.services.error_monitor.discord import format_incident_alert

        result = format_incident_alert(
            title="Minor degradation",
            severity="P3",
            root_cause="Cache miss",
            suggested_fix="Warm cache",
            primary_service="karaoke-backend",
            patterns=[],
        )
        assert "🟡" in result

    def test_contains_title(self):
        from backend.services.error_monitor.discord import format_incident_alert

        result = format_incident_alert(
            title="My Special Incident Title",
            severity="P1",
            root_cause="Something broke",
            suggested_fix="Fix the thing",
            primary_service="karaoke-backend",
            patterns=[],
        )
        assert "My Special Incident Title" in result

    def test_contains_root_cause(self):
        from backend.services.error_monitor.discord import format_incident_alert

        result = format_incident_alert(
            title="Incident",
            severity="P1",
            root_cause="The root cause explanation here",
            suggested_fix="Fix it",
            primary_service="karaoke-backend",
            patterns=[],
        )
        assert "The root cause explanation here" in result

    def test_contains_suggested_fix(self):
        from backend.services.error_monitor.discord import format_incident_alert

        result = format_incident_alert(
            title="Incident",
            severity="P1",
            root_cause="Root cause",
            suggested_fix="The suggested fix steps here",
            primary_service="karaoke-backend",
            patterns=[],
        )
        assert "The suggested fix steps here" in result

    def test_shows_up_to_5_patterns(self):
        from backend.services.error_monitor.discord import format_incident_alert

        patterns = [
            {"service": f"service-{i}", "normalized_message": f"Error message {i}", "count": i + 1}
            for i in range(7)
        ]
        result = format_incident_alert(
            title="Multi-pattern incident",
            severity="P1",
            root_cause="Root cause",
            suggested_fix="Fix",
            primary_service="service-0",
            patterns=patterns,
        )
        # First 5 should appear
        for i in range(5):
            assert f"Error message {i}" in result
        # 6th and 7th should not appear
        assert "Error message 5" not in result
        assert "Error message 6" not in result

    def test_pattern_message_truncated_to_80_chars(self):
        from backend.services.error_monitor.discord import format_incident_alert

        long_msg = "A" * 100
        patterns = [{"service": "svc", "normalized_message": long_msg, "count": 1}]
        result = format_incident_alert(
            title="Incident",
            severity="P1",
            root_cause="Root cause",
            suggested_fix="Fix",
            primary_service="svc",
            patterns=patterns,
        )
        # The full 100-char message should not appear; only first 80
        assert long_msg not in result
        assert long_msg[:80] in result

    def test_truncates_to_discord_limit(self):
        from backend.services.error_monitor.discord import format_incident_alert
        from backend.services.error_monitor.config import DISCORD_MAX_MESSAGE_LENGTH

        result = format_incident_alert(
            title="T" * 500,
            severity="P0",
            root_cause="R" * 500,
            suggested_fix="F" * 500,
            primary_service="svc",
            patterns=[],
        )
        assert len(result) <= DISCORD_MAX_MESSAGE_LENGTH


class TestFormatAutoResolvedAlert:
    """Tests for format_auto_resolved_alert formatting function."""

    def test_contains_checkmark_emoji(self):
        from backend.services.error_monitor.discord import format_auto_resolved_alert

        result = format_auto_resolved_alert(
            resolved_patterns=[
                {"service": "karaoke-backend", "message": "Connection refused", "hours_silent": 8.5}
            ]
        )
        assert "✅" in result

    def test_consolidated_list_up_to_8_patterns(self):
        from backend.services.error_monitor.discord import format_auto_resolved_alert

        patterns = [
            {"service": f"service-{i}", "message": f"Error {i}", "hours_silent": float(i + 1)}
            for i in range(10)
        ]
        result = format_auto_resolved_alert(resolved_patterns=patterns)
        # First 8 should appear
        for i in range(8):
            assert f"Error {i}" in result
        # 9th and 10th should not appear
        assert "Error 8" not in result
        assert "Error 9" not in result

    def test_contains_service_name(self):
        from backend.services.error_monitor.discord import format_auto_resolved_alert

        result = format_auto_resolved_alert(
            resolved_patterns=[
                {"service": "audio-separator", "message": "GPU error", "hours_silent": 12.0}
            ]
        )
        assert "audio-separator" in result

    def test_contains_hours_silent(self):
        from backend.services.error_monitor.discord import format_auto_resolved_alert

        result = format_auto_resolved_alert(
            resolved_patterns=[
                {"service": "karaoke-backend", "message": "Timeout", "hours_silent": 7.5}
            ]
        )
        # Should mention 7.5 or the number somewhere
        assert "7.5" in result or "7" in result

    def test_message_truncated_to_60_chars(self):
        from backend.services.error_monitor.discord import format_auto_resolved_alert

        long_msg = "B" * 80
        result = format_auto_resolved_alert(
            resolved_patterns=[
                {"service": "svc", "message": long_msg, "hours_silent": 10.0}
            ]
        )
        assert long_msg not in result
        assert long_msg[:60] in result

    def test_empty_patterns_returns_string(self):
        from backend.services.error_monitor.discord import format_auto_resolved_alert

        result = format_auto_resolved_alert(resolved_patterns=[])
        assert isinstance(result, str)
        assert "✅" in result


class TestFormatSpikeAlert:
    """Tests for format_spike_alert formatting function."""

    def test_contains_warning_emoji(self):
        from backend.services.error_monitor.discord import format_spike_alert

        result = format_spike_alert(
            service="karaoke-backend",
            normalized_message="Connection refused",
            current_count=50,
            rolling_average=5.0,
        )
        assert "⚠️" in result

    def test_contains_service_name(self):
        from backend.services.error_monitor.discord import format_spike_alert

        result = format_spike_alert(
            service="audio-separator",
            normalized_message="OOM error",
            current_count=30,
            rolling_average=3.0,
        )
        assert "audio-separator" in result

    def test_contains_current_count(self):
        from backend.services.error_monitor.discord import format_spike_alert

        result = format_spike_alert(
            service="karaoke-backend",
            normalized_message="Timeout",
            current_count=75,
            rolling_average=10.0,
        )
        assert "75" in result

    def test_contains_rolling_average(self):
        from backend.services.error_monitor.discord import format_spike_alert

        result = format_spike_alert(
            service="karaoke-backend",
            normalized_message="Timeout",
            current_count=75,
            rolling_average=10.0,
        )
        assert "10" in result

    def test_contains_multiplier(self):
        from backend.services.error_monitor.discord import format_spike_alert

        result = format_spike_alert(
            service="karaoke-backend",
            normalized_message="Timeout",
            current_count=50,
            rolling_average=5.0,
        )
        # 50 / 5.0 = 10x
        assert "10" in result or "10.0" in result

    def test_contains_normalized_message(self):
        from backend.services.error_monitor.discord import format_spike_alert

        result = format_spike_alert(
            service="karaoke-backend",
            normalized_message="Database connection pool exhausted",
            current_count=50,
            rolling_average=5.0,
        )
        assert "Database connection pool exhausted" in result


class TestFormatDailyDigest:
    """Tests for format_daily_digest formatting function."""

    def test_contains_chart_emoji(self):
        from backend.services.error_monitor.discord import format_daily_digest

        result = format_daily_digest(
            total_errors=100,
            new_patterns=5,
            resolved_patterns=2,
            active_patterns=10,
            per_service_counts={"karaoke-backend": 60, "audio-separator": 40},
        )
        assert "📊" in result

    def test_contains_total_errors(self):
        from backend.services.error_monitor.discord import format_daily_digest

        result = format_daily_digest(
            total_errors=1234,
            new_patterns=5,
            resolved_patterns=2,
            active_patterns=10,
            per_service_counts={},
        )
        assert "1234" in result

    def test_contains_new_patterns(self):
        from backend.services.error_monitor.discord import format_daily_digest

        result = format_daily_digest(
            total_errors=100,
            new_patterns=7,
            resolved_patterns=2,
            active_patterns=10,
            per_service_counts={},
        )
        assert "7" in result

    def test_contains_per_service_counts(self):
        from backend.services.error_monitor.discord import format_daily_digest

        result = format_daily_digest(
            total_errors=100,
            new_patterns=5,
            resolved_patterns=2,
            active_patterns=10,
            per_service_counts={"karaoke-backend": 60, "audio-separator": 40},
        )
        assert "karaoke-backend" in result
        assert "audio-separator" in result
        assert "60" in result
        assert "40" in result

    def test_per_service_sorted_by_count_desc(self):
        from backend.services.error_monitor.discord import format_daily_digest

        result = format_daily_digest(
            total_errors=100,
            new_patterns=5,
            resolved_patterns=2,
            active_patterns=10,
            per_service_counts={
                "service-low": 10,
                "service-high": 80,
                "service-mid": 30,
            },
        )
        # service-high should appear before service-low in the output
        high_pos = result.index("service-high")
        low_pos = result.index("service-low")
        assert high_pos < low_pos

    def test_contains_active_patterns(self):
        from backend.services.error_monitor.discord import format_daily_digest

        result = format_daily_digest(
            total_errors=100,
            new_patterns=5,
            resolved_patterns=2,
            active_patterns=15,
            per_service_counts={},
        )
        assert "15" in result

    def test_truncates_to_discord_limit(self):
        from backend.services.error_monitor.discord import format_daily_digest
        from backend.services.error_monitor.config import DISCORD_MAX_MESSAGE_LENGTH

        # Create lots of services to potentially overflow
        per_service = {f"service-{i:03d}": i * 10 for i in range(100)}
        result = format_daily_digest(
            total_errors=99999,
            new_patterns=99,
            resolved_patterns=50,
            active_patterns=200,
            per_service_counts=per_service,
        )
        assert len(result) <= DISCORD_MAX_MESSAGE_LENGTH


class TestErrorMonitorDiscordSendMessage:
    """Tests for ErrorMonitorDiscord.send_message method."""

    def test_send_message_returns_true_on_success(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")
        mock_response = mock.MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status.return_value = None

        with mock.patch("requests.post", return_value=mock_response) as mock_post:
            result = client.send_message("Hello Discord!")
            assert result is True
            mock_post.assert_called_once_with(
                "https://discord.com/api/webhooks/123/abc",
                json={"content": "Hello Discord!"},
                timeout=30,
            )

    def test_send_message_returns_false_on_http_error(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")
        mock_response = mock.MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")

        with mock.patch("requests.post", return_value=mock_response):
            result = client.send_message("Hello Discord!")
            assert result is False

    def test_send_message_returns_false_on_connection_error(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")

        with mock.patch("requests.post", side_effect=requests.ConnectionError("timeout")):
            result = client.send_message("Hello Discord!")
            assert result is False

    def test_send_message_respects_rate_limit(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord
        from backend.services.error_monitor.config import MAX_DISCORD_MESSAGES_PER_RUN

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")
        client.messages_sent = MAX_DISCORD_MESSAGES_PER_RUN  # Already at limit

        with mock.patch("requests.post") as mock_post:
            result = client.send_message("Should be skipped")
            assert result is False
            mock_post.assert_not_called()

    def test_send_message_increments_counter_on_success(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")
        assert client.messages_sent == 0

        mock_response = mock.MagicMock()
        mock_response.raise_for_status.return_value = None

        with mock.patch("requests.post", return_value=mock_response):
            client.send_message("First message")
            assert client.messages_sent == 1
            client.send_message("Second message")
            assert client.messages_sent == 2

    def test_send_message_increments_counter_on_failure(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")
        assert client.messages_sent == 0

        mock_response = mock.MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")

        with mock.patch("requests.post", return_value=mock_response):
            client.send_message("Will fail")
            assert client.messages_sent == 1

    def test_send_message_initial_counter_is_zero(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")
        assert client.messages_sent == 0

    def test_send_message_does_not_increment_when_rate_limited(self):
        from backend.services.error_monitor.discord import ErrorMonitorDiscord
        from backend.services.error_monitor.config import MAX_DISCORD_MESSAGES_PER_RUN

        client = ErrorMonitorDiscord(webhook_url="https://discord.com/api/webhooks/123/abc")
        client.messages_sent = MAX_DISCORD_MESSAGES_PER_RUN

        with mock.patch("requests.post") as mock_post:
            client.send_message("Skipped")
            # Counter should not go beyond limit when rate-limited
            assert client.messages_sent == MAX_DISCORD_MESSAGES_PER_RUN
