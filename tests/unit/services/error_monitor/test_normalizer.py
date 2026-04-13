"""Tests for error_monitor.normalizer module."""

import pytest


class TestUUIDReplacement:
    """UUID patterns should be replaced with <ID>."""

    def test_uuid_v4_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Job failed for job_id=550e8400-e29b-41d4-a716-446655440000"
        result = normalize_message(msg)
        assert "550e8400-e29b-41d4-a716-446655440000" not in result
        assert "<ID>" in result

    def test_uuid_uppercase_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Error in request 550E8400-E29B-41D4-A716-446655440000"
        result = normalize_message(msg)
        assert "550E8400-E29B-41D4-A716-446655440000" not in result
        assert "<ID>" in result

    def test_multiple_uuids_all_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Copy from 550e8400-e29b-41d4-a716-446655440000 to 660f9511-f30c-52e5-b827-557766551111"
        result = normalize_message(msg)
        assert "550e8400" not in result
        assert "660f9511" not in result


class TestISOTimestampReplacement:
    """ISO 8601 timestamps should be replaced with <TS>."""

    def test_iso_datetime_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Error occurred at 2024-01-15T10:30:45Z"
        result = normalize_message(msg)
        assert "2024-01-15T10:30:45Z" not in result
        assert "<TS>" in result

    def test_iso_datetime_with_ms_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Timeout at 2024-03-22T14:55:01.123Z"
        result = normalize_message(msg)
        assert "2024-03-22T14:55:01.123Z" not in result
        assert "<TS>" in result

    def test_iso_datetime_with_offset_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Created at 2024-06-01T08:00:00+05:30"
        result = normalize_message(msg)
        assert "2024-06-01T08:00:00+05:30" not in result
        assert "<TS>" in result


class TestEpochTimestampReplacement:
    """Epoch timestamps (10+ digit floats) should be replaced with <EPOCH>."""

    def test_epoch_float_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Received at t=1711234567.891234"
        result = normalize_message(msg)
        assert "1711234567.891234" not in result
        assert "<EPOCH>" in result

    def test_epoch_integer_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "timestamp=1711234567890"
        result = normalize_message(msg)
        assert "1711234567890" not in result
        assert "<EPOCH>" in result

    def test_small_number_not_epoch(self):
        from backend.services.error_monitor.normalizer import normalize_message

        # 4-digit number should become <NUM>, not <EPOCH>
        msg = "Status code 404 returned"
        result = normalize_message(msg)
        assert "<EPOCH>" not in result


class TestEmailReplacement:
    """Email addresses should be replaced with <EMAIL>."""

    def test_simple_email_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "User user@example.com not found"
        result = normalize_message(msg)
        assert "user@example.com" not in result
        assert "<EMAIL>" in result

    def test_email_with_subdomain_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Sent to andrew@api.nomadkaraoke.com"
        result = normalize_message(msg)
        assert "andrew@api.nomadkaraoke.com" not in result
        assert "<EMAIL>" in result

    def test_email_with_plus_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Contact for test+filter@gmail.com"
        result = normalize_message(msg)
        assert "test+filter@gmail.com" not in result
        assert "<EMAIL>" in result


class TestIPAddressReplacement:
    """IP addresses should be replaced with <IP>."""

    def test_ipv4_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Connection refused from 192.168.1.100"
        result = normalize_message(msg)
        assert "192.168.1.100" not in result
        assert "<IP>" in result

    def test_ipv4_with_port_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Failed to connect to 10.0.0.1:8080"
        result = normalize_message(msg)
        assert "10.0.0.1" not in result
        assert "<IP>" in result

    def test_multiple_ips_all_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Route from 10.0.0.1 to 172.16.0.254 failed"
        result = normalize_message(msg)
        assert "10.0.0.1" not in result
        assert "172.16.0.254" not in result


class TestHexIDReplacement:
    """Hex/alphanumeric IDs (8+ chars with both digits and letters) should be replaced with <ID>."""

    def test_hex_id_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Resource abc12345def not found"
        result = normalize_message(msg)
        assert "abc12345def" not in result
        assert "<ID>" in result

    def test_pure_alpha_word_not_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        # Pure alphabetic strings should not be replaced
        msg = "Authentication failed completely"
        result = normalize_message(msg)
        assert result == "Authentication failed completely"

    def test_short_id_not_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        # IDs shorter than 8 chars should not be replaced
        msg = "Error in abc123 handler"
        result = normalize_message(msg)
        # Should not replace short mixed alphanum under 8 chars as <ID>
        assert "abc123" in result


class TestLargeNumberReplacement:
    """Numbers with 4+ digits should be replaced with <NUM>."""

    def test_four_digit_number_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "HTTP error code 5000 encountered"
        result = normalize_message(msg)
        assert "5000" not in result
        assert "<NUM>" in result

    def test_large_number_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Allocated 12345 bytes"
        result = normalize_message(msg)
        assert "12345" not in result
        assert "<NUM>" in result

    def test_small_number_preserved(self):
        from backend.services.error_monitor.normalizer import normalize_message

        # 3-digit numbers like HTTP status codes should be preserved
        msg = "HTTP 404 Not Found"
        result = normalize_message(msg)
        assert "404" in result

    def test_two_digit_number_preserved(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Retry attempt 42"
        result = normalize_message(msg)
        assert "42" in result


class TestJobIDReplacement:
    """Job IDs in paths should be replaced."""

    def test_job_id_in_path_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Failed processing /jobs/abc123def456 in queue"
        result = normalize_message(msg)
        assert "abc123def456" not in result
        assert "/jobs/<ID>" in result

    def test_job_id_alphanumeric_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Job at /jobs/XyZ9a1b2C3d4 timed out"
        result = normalize_message(msg)
        assert "XyZ9a1b2C3d4" not in result
        assert "/jobs/<ID>" in result


class TestGCSPathReplacement:
    """GCS paths should be normalized."""

    def test_gcs_path_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Could not read gs://my-bucket/path/to/file.mp4"
        result = normalize_message(msg)
        assert "gs://my-bucket/path/to/file.mp4" not in result
        assert "gs://<BUCKET>/<PATH>" in result

    def test_gcs_path_without_object_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Bucket access denied: gs://nomadkaraoke-prod-videos/"
        result = normalize_message(msg)
        assert "gs://nomadkaraoke-prod-videos/" not in result
        assert "gs://<BUCKET>" in result

    def test_gcs_path_with_nested_path_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "File gs://nomad-audio/jobs/abc123/output/stem.wav not found"
        result = normalize_message(msg)
        assert "gs://nomad-audio/jobs/abc123/output/stem.wav" not in result
        assert "gs://<BUCKET>/<PATH>" in result


class TestFirebaseUIDReplacement:
    """Firebase UIDs (20-28 mixed-case alphanumeric) should be replaced with <UID>."""

    def test_firebase_uid_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        # Firebase UIDs are 28 chars: letters and digits, mixed case
        msg = "User uid=ABC123defGHI456jklMNO7 not authorized"
        result = normalize_message(msg)
        assert "ABC123defGHI456jklMNO7" not in result
        assert "<UID>" in result

    def test_firebase_uid_28_chars_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Auth failed for 8JkLmNoPqRsT1uVwXyZ23456AB"
        result = normalize_message(msg)
        assert "8JkLmNoPqRsT1uVwXyZ23456AB" not in result
        assert "<UID>" in result


class TestFirestoreDocPathReplacement:
    """Firestore document paths should be replaced with <DOC_PATH>."""

    def test_firestore_doc_path_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Document jobs/abc123def/state_data/current does not exist"
        result = normalize_message(msg)
        assert "jobs/abc123def/state_data/current" not in result
        assert "<DOC_PATH>" in result

    def test_firestore_users_path_replaced(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Permission denied for users/uid12345/profile/settings"
        result = normalize_message(msg)
        assert "users/uid12345/profile/settings" not in result
        assert "<DOC_PATH>" in result


class TestURLNormalization:
    """URLs should be stripped to domain only."""

    def test_url_stripped_to_domain(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Failed to fetch https://api.example.com/v1/users/12345/data"
        result = normalize_message(msg)
        assert "/v1/users/12345/data" not in result
        assert "api.example.com" in result

    def test_http_url_stripped(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Redirect from http://old.example.com/path?q=abc123"
        result = normalize_message(msg)
        assert "/path?q=abc123" not in result
        assert "old.example.com" in result

    def test_url_with_port_domain_preserved(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Connection to https://internal.service.com:8080/api/endpoint failed"
        result = normalize_message(msg)
        assert "internal.service.com" in result


class TestTruncation:
    """Long messages should be truncated to MAX_NORMALIZED_MESSAGE_LENGTH (200 chars)."""

    def test_long_message_truncated(self):
        from backend.services.error_monitor.normalizer import normalize_message
        from backend.services.error_monitor.config import MAX_NORMALIZED_MESSAGE_LENGTH

        long_msg = "Error: " + "x" * 300
        result = normalize_message(long_msg)
        assert len(result) <= MAX_NORMALIZED_MESSAGE_LENGTH

    def test_short_message_not_truncated(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Short error message"
        result = normalize_message(msg)
        assert result == msg

    def test_exactly_200_chars_not_truncated(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "A" * 200
        result = normalize_message(msg)
        assert len(result) == 200


class TestEdgeCases:
    """Edge cases for normalize_message."""

    def test_empty_message_returns_empty_string(self):
        from backend.services.error_monitor.normalizer import normalize_message

        result = normalize_message("")
        assert result == ""

    def test_no_replacements_returns_original(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Connection refused by server"
        result = normalize_message(msg)
        assert result == msg

    def test_deterministic_same_input_same_output(self):
        from backend.services.error_monitor.normalizer import normalize_message

        msg = "Job failed for 550e8400-e29b-41d4-a716-446655440000 at 2024-01-15T10:30:45Z"
        result1 = normalize_message(msg)
        result2 = normalize_message(msg)
        assert result1 == result2


class TestComputePatternHash:
    """Tests for the compute_pattern_hash function."""

    def test_returns_64_char_hex(self):
        from backend.services.error_monitor.normalizer import compute_pattern_hash

        result = compute_pattern_hash("karaoke-backend", "Connection refused by server")
        assert isinstance(result, str)
        assert len(result) == 64
        # SHA-256 hex is lowercase hex
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_inputs_same_hash(self):
        from backend.services.error_monitor.normalizer import compute_pattern_hash

        h1 = compute_pattern_hash("karaoke-backend", "Connection refused by server")
        h2 = compute_pattern_hash("karaoke-backend", "Connection refused by server")
        assert h1 == h2

    def test_different_services_different_hash(self):
        from backend.services.error_monitor.normalizer import compute_pattern_hash

        h1 = compute_pattern_hash("karaoke-backend", "Connection refused by server")
        h2 = compute_pattern_hash("audio-separator", "Connection refused by server")
        assert h1 != h2

    def test_different_messages_different_hash(self):
        from backend.services.error_monitor.normalizer import compute_pattern_hash

        h1 = compute_pattern_hash("karaoke-backend", "Connection refused by server")
        h2 = compute_pattern_hash("karaoke-backend", "Timeout waiting for response")
        assert h1 != h2

    def test_empty_service_and_message(self):
        from backend.services.error_monitor.normalizer import compute_pattern_hash

        result = compute_pattern_hash("", "")
        assert isinstance(result, str)
        assert len(result) == 64
