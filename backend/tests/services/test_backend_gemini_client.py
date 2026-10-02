"""Tests for backend.services.gemini_client (Gemini Developer API helper) and the
per-caller graceful degradation when Gemini's quota/credit/key is unusable."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services import gemini_client
from backend.services.gemini_client import (
    GeminiKeyUnavailableError,
    get_api_key,
    get_genai_client,
    is_quota_or_billing_error,
    note_gemini_failure,
)


class FakeAPIError(Exception):
    """Mimics google.genai.errors.APIError (numeric ``code`` + ``status``)."""

    def __init__(self, code, status, message=""):
        super().__init__(f"{code} {status}. {message}")
        self.code = code
        self.status = status


QUOTA = FakeAPIError(429, "RESOURCE_EXHAUSTED", "Your prepayment credits are depleted")
RATE_LIMIT = FakeAPIError(
    429, "RESOURCE_EXHAUSTED",
    "quotaId: GenerateRequestsPerMinutePerProjectPerModel-PaidTier, retryDelay: 5s",
)


@pytest.fixture(autouse=True)
def _reset_alert_throttle(monkeypatch):
    monkeypatch.setattr(gemini_client, "_last_alert_attempt", 0.0)


# ---- classifier -------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        QUOTA,
        FakeAPIError(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota"),
        FakeAPIError(403, "PERMISSION_DENIED", "Generative Language API has not been used"),
        FakeAPIError(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
        FakeAPIError(400, "FAILED_PRECONDITION", "check your plan and billing details"),
        RuntimeError("API key expired. Please renew the API key."),
        GeminiKeyUnavailableError("no key"),
    ],
)
def test_classifier_positive(exc):
    assert is_quota_or_billing_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        RATE_LIMIT,
        FakeAPIError(500, "INTERNAL", "Internal error"),
        FakeAPIError(503, "UNAVAILABLE", "The model is overloaded"),
        FakeAPIError(400, "INVALID_ARGUMENT", "Request contains an invalid argument."),
        TimeoutError("timed out"),
        ValueError("processed 429 tokens"),
        None,
    ],
)
def test_classifier_negative(exc):
    assert not is_quota_or_billing_error(exc)


def test_classifier_walks_cause_chain():
    try:
        try:
            raise QUOTA
        except FakeAPIError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert is_quota_or_billing_error(outer)


# ---- key + client -------------------------------------------------------------


def test_env_key_wins(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", " env-key ")
    with patch("backend.config.Settings.get_secret") as get_secret:
        assert get_api_key() == "env-key"
    get_secret.assert_not_called()


def test_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch("backend.config.Settings.get_secret", return_value="sm-key") as get_secret:
        assert get_api_key() == "sm-key"
    get_secret.assert_called_once_with("gemini-api-key")


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch("backend.config.Settings.get_secret", return_value=None):
        with pytest.raises(GeminiKeyUnavailableError):
            get_api_key()


def test_client_uses_api_key_never_vertex(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with patch("google.genai.Client") as client_cls:
        get_genai_client(timeout_ms=1234)
    kwargs = client_cls.call_args.kwargs
    assert kwargs["api_key"] == "k"
    assert kwargs["http_options"].timeout == 1234
    assert "vertexai" not in kwargs and "project" not in kwargs and "location" not in kwargs


# ---- note_gemini_failure / alert throttle --------------------------------------


def test_note_failure_ignores_ordinary_errors():
    with patch.object(gemini_client, "_maybe_alert") as alert:
        assert note_gemini_failure("x", RATE_LIMIT) is False
    alert.assert_not_called()


def test_note_failure_alerts_once_per_window():
    with patch("backend.services.ops_alerts.send_throttled_ops_alert", return_value=True) as send:
        assert note_gemini_failure("auto_correct", QUOTA) is True
        assert note_gemini_failure("match_judge", QUOTA) is True
    send.assert_called_once()
    key, message = send.call_args.args
    assert key == "gemini-quota-exhausted"
    assert "auto_correct" in message and "rotate gemini-api-key" in message
    assert send.call_args.kwargs["throttle_minutes"] == 360


def test_note_failure_never_raises():
    with patch("backend.services.ops_alerts.send_throttled_ops_alert", side_effect=RuntimeError):
        assert note_gemini_failure("x", QUOTA) is False


def test_throttled_ops_alert_respects_firestore_window(monkeypatch):
    from backend.services import ops_alerts

    monkeypatch.setenv("FAILURE_ALERTS_ENABLED", "true")
    db = MagicMock()
    with patch("backend.services.firestore_service.get_firestore_client", return_value=db), \
        patch.object(ops_alerts, "_should_alert", return_value=(False, False, 0, None)) as should, \
        patch.object(ops_alerts, "send_ops_alert") as send:
        assert ops_alerts.send_throttled_ops_alert("k", "msg", throttle_minutes=360) is False
    send.assert_not_called()
    assert should.call_args.kwargs["throttle_minutes"] == 360

    with patch("backend.services.firestore_service.get_firestore_client", return_value=db), \
        patch.object(ops_alerts, "_should_alert", return_value=(True, False, 2, "ref")), \
        patch.object(ops_alerts, "send_ops_alert", return_value=True) as send, \
        patch.object(ops_alerts, "_mark_alerted") as mark:
        assert ops_alerts.send_throttled_ops_alert("k", "msg", throttle_minutes=360) is True
    assert "Collapsed 2" in send.call_args.args[0]
    mark.assert_called_once()


# ---- per-caller degradation ----------------------------------------------------


def _quota_client():
    client = MagicMock()
    client.models.generate_content.side_effect = QUOTA
    return client


def test_auto_correct_quota_is_non_retryable_and_skips_gemini_leg():
    from backend.services.auto_correct.service import AutoCorrectService, AutoCorrectServiceError

    service = AutoCorrectService()
    with patch("backend.services.gemini_client.get_genai_client", return_value=_quota_client()), \
        patch.object(gemini_client, "_maybe_alert"), \
        patch("backend.services.auto_correct.service.time.sleep") as sleep:
        with pytest.raises(AutoCorrectServiceError) as ei:
            service._call_model("gemini-3.8-flash", "sys", "user", job_id="j")
    assert ei.value.retryable is False and ei.value.status_code == 503
    sleep.assert_not_called()


def test_auto_correct_compare_keeps_opus_when_gemini_quota_exhausted():
    from backend.services.auto_correct.service import AutoCorrectService, AutoCorrectServiceError
    from backend.services.auto_correct.settings import AutoCorrectSettings

    segments = [{"id": "s1", "words": [{"id": "w0", "text": "glory"}]}]
    refs = {"genius": {"segments": [{"text": "chlorine"}]}}
    sugg = {"op": "replace", "start_idx": 0, "end_idx": 0, "new_text": "chlorine",
            "reason": "r", "category": "mishearing", "confidence": 0.9}

    def fake_call(model, *_a, **_k):
        if model.startswith("gemini"):
            raise AutoCorrectServiceError("Gemini is unavailable", status_code=503)
        return {"suggestions": [sugg]}, None

    service = AutoCorrectService()
    with patch.object(service.settings, "auto_correct_compare_models", "claude-opus-5-5;gemini-3.8-flash"), \
        patch.object(service, "_cache_get", return_value=None), \
        patch.object(service, "_cache_put"), \
        patch.object(service, "_call_model", side_effect=fake_call):
        result = service.suggest(
            job_id="j", segments=segments, reference_lyrics=refs, artist="A", title="T",
            settings=AutoCorrectSettings(compare_models=True),
        )
    assert result.model == "claude-opus-5-5"
    assert any("gemini-3.8-flash failed" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_match_judge_quota_returns_no_suggestion_and_alerts():
    from backend.services.match_judge import service as mj

    async def no_candidates(*_a, **_k):
        return []

    async def boom(*_a, **_k):
        raise QUOTA

    with patch.object(gemini_client, "_maybe_alert") as alert:
        verdict = await mj.judge_match(
            "A", "T", search_tracks=no_candidates, ai_judge=boom, enabled=True
        )
    assert verdict.reason == "ai failed"
    alert.assert_called_once()


def test_custom_lyrics_quota_is_clean_503():
    from backend.services.custom_lyrics.settings import GenerationSettings
    from backend.services.custom_lyrics_service import CustomLyricsService, CustomLyricsServiceError

    service = CustomLyricsService()
    with patch("backend.services.gemini_client.get_genai_client", return_value=_quota_client()), \
        patch.object(gemini_client, "_maybe_alert"):
        with pytest.raises(CustomLyricsServiceError) as ei:
            service._call_gemini(system_prompt="s", user_prompt="u", pdf_bytes=None,
                                 settings=GenerationSettings())
    assert ei.value.status_code == 503
    assert "temporarily unavailable" in str(ei.value)


def test_credit_eval_quota_goes_to_manual_review():
    from backend.services.credit_evaluation_service import CreditEvaluationService

    svc = CreditEvaluationService.__new__(CreditEvaluationService)
    svc.settings = MagicMock(credit_eval_enabled=True, credit_eval_model="gemini-3.8-flash")
    signals = {"fingerprint_matches": [{"x": 1}], "ip_matches": [], "recent_signups_ip": 0,
               "recent_signups_fp": 0, "user_data": {}, "ip_geo": None, "user_agent": None}
    with patch.object(svc, "_collect_signals", return_value=signals), \
        patch.object(svc, "_log_evaluation"), \
        patch("backend.services.gemini_client.get_genai_client", return_value=_quota_client()), \
        patch.object(gemini_client, "_maybe_alert") as alert:
        evaluation = svc.evaluate("a@b.c", "welcome")
    assert evaluation.decision == "pending_review"
    alert.assert_called_once()


def test_error_monitor_llm_does_not_retry_on_quota():
    from backend.services.error_monitor import llm_analysis

    with patch.object(llm_analysis, "get_genai_client", return_value=_quota_client()), \
        patch.object(gemini_client, "_maybe_alert"), \
        patch.object(llm_analysis.time, "sleep") as sleep:
        with pytest.raises(FakeAPIError):
            llm_analysis._call_llm("sys", "user")
    sleep.assert_not_called()
