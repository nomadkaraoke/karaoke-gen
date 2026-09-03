"""Tests for encoding worker error classification helpers."""

from backend.services.encoding_errors import (
    EncodingWorkerInfraError,
    EncodingWorkerStartError,
    is_worker_infra_error,
)


# The verbatim worker-reported error from the prod incident (job 6452888e).
_METADATA_FAILURE = (
    "Failed to retrieve https://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/?recursive=true from the Google Compute Engine metadata "
    "service. Compute Engine Metadata server unavailable. Last exception: "
    "HTTPSConnectionPool(host='metadata.google.internal', port=443): Max retries "
    "exceeded with url: /computeMetadata/v1/instance/service-accounts/default/"
    "?recursive=true (Caused by SSLError(SSLCertVerificationError(1, '[SSL: "
    "CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer "
    "certificate (_ssl.c:1018)')))"
)


class TestIsWorkerInfraError:
    def test_metadata_failure_is_infra(self):
        assert is_worker_infra_error(_METADATA_FAILURE) is True

    def test_matches_are_case_insensitive(self):
        assert is_worker_infra_error(_METADATA_FAILURE.upper()) is True

    def test_individual_markers(self):
        for msg in (
            "Compute Engine Metadata server unavailable",
            "SSLCertVerificationError: certificate_verify_failed",
            "google.auth.exceptions.DefaultCredentialsError: could not automatically "
            "determine credentials",
            "GET /computeMetadata/v1/instance/service-accounts/default/ failed",
        ):
            assert is_worker_infra_error(msg) is True, msg

    def test_genuine_encode_error_is_not_infra(self):
        # Real ffmpeg / input problems must stay terminal, never masquerade as infra.
        for msg in (
            "ffmpeg exploded",
            "Invalid data found when processing input",
            "Unknown encoder 'libx265'",
            "Output file is empty, nothing was encoded",
            "",
        ):
            assert is_worker_infra_error(msg) is False, msg

    def test_none_is_not_infra(self):
        assert is_worker_infra_error(None) is False  # type: ignore[arg-type]


class TestEncodingWorkerInfraError:
    def test_is_recoverable_start_error_subclass(self):
        """Subclassing EncodingWorkerStartError is what routes it through the render
        worker's park-for-auto-retry path (and the final-encode Cloud Run Job retry)."""
        err = EncodingWorkerInfraError("boom", vm_name="encoding-worker-fallback-c4a")
        assert isinstance(err, EncodingWorkerStartError)
        assert err.vm_name == "encoding-worker-fallback-c4a"
