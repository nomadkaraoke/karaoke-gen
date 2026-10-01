"""Simple classification/extraction Gemini callers must request LOW thinking.

Gemini 3 Flash defaults to high/dynamic thinking, and thinking tokens are billed
as output — the dominant Vertex cost. These callers do short structured
extraction or grant/deny classification, so low thinking is ample (cost cut
2026-10-01). Quality-sensitive callers (auto-correct, custom lyrics, lyrics
translation) deliberately keep the model default and are not covered here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _fake_client():
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text="{}")
    return client


def _thinking_level(client) -> str:
    config = client.models.generate_content.call_args.kwargs["config"]
    return config.thinking_config.thinking_level.value.lower()


@pytest.mark.parametrize(
    "module_path",
    [
        "backend.services.match_judge.ai",
        "backend.services.match_judge.free_text",
        "backend.services.parse_titles.ai",
    ],
)
def test_blocking_generate_uses_low_thinking(module_path) -> None:
    import importlib

    mod = importlib.import_module(module_path)
    client = _fake_client()
    with patch("google.genai.Client", return_value=client):
        mod._blocking_generate("gemini-3.8-flash", "sys", "user")
    assert _thinking_level(client) == "low"


def test_tenant_bulk_generate_uses_low_thinking() -> None:
    from backend.services.tenant_bulk import analyze

    client = _fake_client()
    with patch("google.genai.Client", return_value=client):
        analyze.default_generate("sys", "user")
    assert _thinking_level(client) == "low"


def test_credit_eval_uses_low_thinking() -> None:
    from backend.services.credit_evaluation_service import CreditEvaluationService

    client = _fake_client()
    svc = CreditEvaluationService.__new__(CreditEvaluationService)
    svc.settings = SimpleNamespace(google_cloud_project="p", credit_eval_model="gemini-3.8-flash")
    with patch("google.genai.Client", return_value=client):
        svc._call_gemini("prompt")
    assert _thinking_level(client) == "low"
