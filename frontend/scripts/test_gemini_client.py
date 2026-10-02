"""Unit tests for scripts/gemini_client.py (run: python -m pytest scripts/test_gemini_client.py)."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import gemini_client  # noqa: E402
from gemini_client import (  # noqa: E402
    GeminiKeyUnavailableError,
    get_api_key,
    is_quota_or_billing_error,
)


class FakeAPIError(Exception):
    """Mimics google.genai.errors.APIError (numeric ``code`` + ``status``)."""

    def __init__(self, code, status, message):
        super().__init__(f"{code} {status}. {message}")
        self.code = code
        self.status = status


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(gemini_client, "_cached_key", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


class TestIsQuotaOrBillingError:
    @pytest.mark.parametrize(
        "exc",
        [
            FakeAPIError(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota"),
            FakeAPIError(403, "PERMISSION_DENIED", "Your prepayment credits are depleted"),
            FakeAPIError(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
            FakeAPIError(400, "FAILED_PRECONDITION", "Please check your plan and billing details"),
            RuntimeError("429 RESOURCE_EXHAUSTED quota"),
            RuntimeError("Your credits have been exhausted"),
            GeminiKeyUnavailableError("no key"),
            RuntimeError("429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel, retryDelay 3600s"),
        ],
    )
    def test_positive(self, exc):
        assert is_quota_or_billing_error(exc)

    @pytest.mark.parametrize(
        "exc",
        [
            FakeAPIError(500, "INTERNAL", "Internal error"),
            FakeAPIError(503, "UNAVAILABLE", "The model is overloaded"),
            FakeAPIError(400, "INVALID_ARGUMENT", "Request contains an invalid argument."),
            TimeoutError("timed out"),
            ValueError("bad JSON from model, processed 429 tokens"),
            FakeAPIError(
                429,
                "RESOURCE_EXHAUSTED",
                "You exceeded your current quota. quotaId: "
                "GenerateRequestsPerMinutePerProjectPerModel-PaidTier, retryDelay: 7s",
            ),
            RuntimeError("429 RESOURCE_EXHAUSTED ... Please retry in 7.2s"),
            None,
        ],
    )
    def test_negative(self, exc):
        assert not is_quota_or_billing_error(exc)

    def test_walks_cause_chain(self):
        try:
            try:
                raise FakeAPIError(429, "RESOURCE_EXHAUSTED", "quota")
            except FakeAPIError as inner:
                raise RuntimeError("translation failed") from inner
        except RuntimeError as outer:
            assert is_quota_or_billing_error(outer)


class TestGetApiKey:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", " from-env ")
        with mock.patch.object(subprocess, "run") as run:
            assert get_api_key() == "from-env"
        run.assert_not_called()

    def test_falls_back_to_gcloud_and_caches(self):
        done = subprocess.CompletedProcess([], 0, stdout="from-secret\n", stderr="")
        with mock.patch.object(subprocess, "run", return_value=done) as run:
            assert get_api_key() == "from-secret"
            assert get_api_key() == "from-secret"
        assert run.call_count == 1
        cmd = run.call_args.args[0]
        assert "--secret=gemini-api-key" in cmd and "--project=nomadkaraoke" in cmd

    def test_gcloud_failure_raises(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="PERMISSION_DENIED")
        with mock.patch.object(subprocess, "run", return_value=failed):
            with pytest.raises(GeminiKeyUnavailableError):
                get_api_key()

    def test_gcloud_missing_raises(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("gcloud")):
            with pytest.raises(GeminiKeyUnavailableError):
                get_api_key()


def test_quota_message_hints_env_key(monkeypatch):
    assert "environment" not in gemini_client.quota_exhausted_message()
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert "GEMINI_API_KEY environment variable" in gemini_client.quota_exhausted_message()
