"""Tests for auto-correct token pricing and usage extraction/logging."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from backend.services.auto_correct.pricing import (
    MODEL_PRICING,
    estimate_cost_usd,
    get_rate,
)
from backend.services.auto_correct.service import AutoCorrectService
from backend.services.auto_correct.settings import AutoCorrectSettings


# ---- pricing ----

def test_estimate_cost_fable_known_rates() -> None:
    # 5000 in @ $10/M + 4000 out @ $50/M = 0.05 + 0.20 = 0.25
    usage = {
        "input_tokens": 5000,
        "output_tokens": 4000,
        "cache_read_tokens": 0,
        "thinking_tokens": None,
    }
    cost = estimate_cost_usd("claude-fable-5", usage)
    assert cost == round(0.05 + 0.20, 10)


def test_estimate_cost_includes_cache_read() -> None:
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 1_000_000,
        "thinking_tokens": None,
    }
    # 1M input @ $10 + 1M cache_read @ $1 = 11.0
    assert estimate_cost_usd("claude-fable-5", usage) == 11.0


def test_estimate_cost_none_usage_returns_none() -> None:
    assert estimate_cost_usd("claude-fable-5", None) is None


def test_estimate_cost_unknown_model_returns_none() -> None:
    usage = {
        "input_tokens": 1000,
        "output_tokens": 1000,
        "cache_read_tokens": 0,
        "thinking_tokens": None,
    }
    assert estimate_cost_usd("some-unknown-model-9", usage) is None


def test_get_rate_prefix_match_for_dated_snapshot() -> None:
    # A dated snapshot id still resolves to its family rate card.
    assert get_rate("claude-fable-5-20260601") == MODEL_PRICING["claude-fable-5"]


# ---- usage extraction ----

def test_anthropic_usage_extraction() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=4321,
            output_tokens=3210,
            cache_read_input_tokens=100,
        )
    )
    usage = AutoCorrectService._anthropic_usage(response)
    assert usage == {
        "input_tokens": 4321,
        "output_tokens": 3210,  # already includes thinking
        "cache_read_tokens": 100,
        "thinking_tokens": None,
    }


def test_gemini_usage_folds_thoughts_into_output() -> None:
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=4000,
            candidates_token_count=500,
            thoughts_token_count=2500,
            cached_content_token_count=0,
        )
    )
    usage = AutoCorrectService._gemini_usage(response)
    assert usage == {
        "input_tokens": 4000,
        "output_tokens": 3000,  # 500 visible + 2500 thinking
        "cache_read_tokens": 0,
        "thinking_tokens": 2500,
    }


def test_usage_extraction_never_raises_on_bad_shape() -> None:
    # Missing usage attribute entirely -> None, not an exception.
    assert AutoCorrectService._anthropic_usage(SimpleNamespace()) is None
    assert AutoCorrectService._gemini_usage(SimpleNamespace()) is None


# ---- logging ----

SEGMENTS = [
    {"id": "seg-1", "words": [{"id": "w0", "text": "glory"}]},
]
REFS = {"genius": {"segments": [{"text": "chlorine"}]}}


def _raw():
    return {
        "suggestions": [
            {
                "op": "replace",
                "start_idx": 0,
                "end_idx": 0,
                "new_text": "chlorine",
                "reason": "r",
                "category": "mishearing",
                "confidence": 0.9,
            }
        ]
    }


def test_suggest_logs_per_model_and_total_cost(caplog) -> None:
    service = AutoCorrectService()
    usage = {
        "input_tokens": 5000,
        "output_tokens": 4000,
        "cache_read_tokens": 0,
        "thinking_tokens": None,
    }
    with patch.object(service, "_cache_get", return_value=None), \
         patch.object(service, "_cache_put"), \
         patch.object(service, "_call_model", return_value=(_raw(), usage)), \
         patch.object(service.settings, "auto_correct_model", "claude-fable-5"):
        with caplog.at_level("INFO"):
            service.suggest(
                job_id="job-1",
                segments=SEGMENTS,
                reference_lyrics=REFS,
                artist="A",
                title="T",
                settings=AutoCorrectSettings(),
            )
    text = caplog.text
    assert "auto-correct usage job=job-1 model=claude-fable-5" in text
    assert "input_tokens=5000" in text
    assert "output_tokens=4000" in text
    assert "est_cost_usd=0.2500" in text
    assert "auto-correct cost job=job-1" in text
    assert "total_est_cost_usd=0.2500" in text


def test_suggest_logs_unavailable_usage_without_crashing(caplog) -> None:
    service = AutoCorrectService()
    with patch.object(service, "_cache_get", return_value=None), \
         patch.object(service, "_cache_put"), \
         patch.object(service, "_call_model", return_value=(_raw(), None)), \
         patch.object(service.settings, "auto_correct_model", "claude-fable-5"):
        with caplog.at_level("INFO"):
            service.suggest(
                job_id="job-2",
                segments=SEGMENTS,
                reference_lyrics=REFS,
                artist="A",
                title="T",
                settings=AutoCorrectSettings(),
            )
    assert "auto-correct usage job=job-2 model=claude-fable-5 tokens=unavailable" in caplog.text
    assert "total_est_cost_usd=unknown" in caplog.text
