"""Tests for model-call retry/backoff on transient Vertex/Anthropic errors.

A Vertex 429 RESOURCE_EXHAUSTED is a recurring, transient condition (other
call sites like parse_titles degrade gracefully). Auto-correct should retry
with backoff and only surface a distinct, retryable 429 to the caller when
the rate limit persists — never a generic 502 that reads as a hard failure.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services.auto_correct.service import (
    AutoCorrectService,
    AutoCorrectServiceError,
    _env_number,
    _is_transient_model_error,
)


class _FakeClientError(Exception):
    """Mimics google.genai.errors.ClientError (has a numeric ``code``)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _service() -> AutoCorrectService:
    return AutoCorrectService()


def test_is_transient_detects_429_resource_exhausted() -> None:
    exc = _FakeClientError(429, "429 RESOURCE_EXHAUSTED. Resource exhausted.")
    assert _is_transient_model_error(exc) is True


def test_is_transient_detects_503_and_overloaded() -> None:
    assert _is_transient_model_error(_FakeClientError(503, "UNAVAILABLE")) is True
    assert _is_transient_model_error(Exception("529 overloaded_error")) is True


def test_is_transient_ignores_permanent_client_errors() -> None:
    assert _is_transient_model_error(_FakeClientError(400, "INVALID_ARGUMENT")) is False
    assert _is_transient_model_error(ValueError("bad schema")) is False


def test_env_number_uses_default_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("AC_TEST_KNOB", raising=False)
    assert _env_number("AC_TEST_KNOB", 3, minimum=1, is_int=True) == 3


def test_env_number_clamps_below_minimum(monkeypatch) -> None:
    monkeypatch.setenv("AC_TEST_KNOB", "0")
    assert _env_number("AC_TEST_KNOB", 3, minimum=1, is_int=True) == 1


def test_env_number_falls_back_on_garbage(monkeypatch) -> None:
    monkeypatch.setenv("AC_TEST_KNOB", "not-a-number")
    assert _env_number("AC_TEST_KNOB", 2.0, minimum=0.0, is_int=False) == 2.0


def test_env_number_rejects_non_finite(monkeypatch) -> None:
    monkeypatch.setenv("AC_TEST_KNOB", "inf")
    assert _env_number("AC_TEST_KNOB", 2.0, minimum=0.0, is_int=False) == 0.0


def test_retryable_call_retries_then_succeeds() -> None:
    """A transient failure is retried; a later success is returned."""
    service = _service()
    attempts = {"n": 0}

    def flaky(model, system_prompt, user_prompt, *, job_id):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise AutoCorrectServiceError(
                "AI model is rate-limited", status_code=429, retryable=True
            )
        return {"suggestions": []}, None

    with patch.object(service, "_call_gemini", side_effect=flaky), \
        patch.object(service, "_call_anthropic", side_effect=flaky), \
        patch("backend.services.auto_correct.service.time.sleep") as sleep:
        raw, usage = service._call_model(
            "gemini-3.1-pro-preview", "sys", "user", job_id="job-1"
        )

    assert raw == {"suggestions": []}
    assert attempts["n"] == 3
    assert sleep.call_count == 2  # slept before each retry


def test_retryable_call_exhausts_and_raises_429() -> None:
    """Persistent rate-limit surfaces a retryable 429, not a generic 502."""
    service = _service()

    def always_rate_limited(model, system_prompt, user_prompt, *, job_id):
        raise AutoCorrectServiceError(
            "AI model is rate-limited", status_code=429, retryable=True
        )

    with patch.object(service, "_call_gemini", side_effect=always_rate_limited), \
        patch("backend.services.auto_correct.service.time.sleep"), \
        patch("backend.services.auto_correct.service._MODEL_CALL_MAX_ATTEMPTS", 3):
        with pytest.raises(AutoCorrectServiceError) as ei:
            service._call_model("gemini-3.1-pro-preview", "sys", "user", job_id="j")

    assert ei.value.status_code == 429
    assert ei.value.retryable is True


def test_non_retryable_error_fails_fast_without_sleeping() -> None:
    """A permanent error (e.g. bad output) is not retried."""
    service = _service()
    calls = {"n": 0}

    def bad_output(model, system_prompt, user_prompt, *, job_id):
        calls["n"] += 1
        raise AutoCorrectServiceError("AI returned non-JSON output", status_code=502)

    with patch.object(service, "_call_gemini", side_effect=bad_output), \
        patch("backend.services.auto_correct.service.time.sleep") as sleep:
        with pytest.raises(AutoCorrectServiceError) as ei:
            service._call_model("gemini-3.1-pro-preview", "sys", "user", job_id="j")

    assert calls["n"] == 1
    assert ei.value.status_code == 502
    sleep.assert_not_called()
