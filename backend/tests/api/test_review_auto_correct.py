"""Route tests for POST /api/review/{job_id}/auto-correct."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.auto_correct.service import (
    AutoCorrectResult,
    AutoCorrectServiceError,
    Suggestion,
)
from backend.services.auto_correct.settings import AutoCorrectSettings


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    """Bypass require_review_auth for these tests."""
    from backend.api.dependencies import require_review_auth as real_dep

    async def fake_dep():
        return ("test-job", "full")

    app.dependency_overrides[real_dep] = fake_dep
    yield
    app.dependency_overrides.pop(real_dep, None)


def _override_service(result_or_exc):
    from backend.api.routes.review import _get_auto_correct_service_dep

    mock_service = MagicMock()
    if isinstance(result_or_exc, Exception):
        mock_service.suggest.side_effect = result_or_exc
    else:
        mock_service.suggest.return_value = result_or_exc
    app.dependency_overrides[_get_auto_correct_service_dep] = lambda: mock_service
    return mock_service


def teardown_function(_):  # noqa: ANN001
    from backend.api.routes.review import _get_auto_correct_service_dep

    app.dependency_overrides.pop(_get_auto_correct_service_dep, None)


def _make_result(suggestions=None) -> AutoCorrectResult:
    return AutoCorrectResult(
        suggestions=suggestions
        if suggestions is not None
        else [
            Suggestion(
                id="sug-1",
                op="replace",
                word_ids=["w3"],
                segment_ids=["seg-1"],
                original_text="glory",
                new_text="chlorine",
                reason="matches reference",
                category="mishearing",
                confidence=0.95,
            )
        ],
        model="gemini-test",
        elapsed_seconds=12.3,
        settings_applied=AutoCorrectSettings(),
        warnings=["dropped suggestion 3: out of range"],
    )


BODY = {
    "segments": [{"id": "seg-1", "words": [{"id": "w3", "text": "glory"}]}],
    "reference_lyrics": {"genius": {"segments": [{"text": "chlorine"}]}},
    "artist": "Twenty One Pilots",
    "title": "Chlorine",
}


def test_happy_path(client: TestClient) -> None:
    mock = _override_service(_make_result())
    resp = client.post("/api/review/test-job/auto-correct", json=BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "gemini-test"
    assert len(data["suggestions"]) == 1
    s = data["suggestions"][0]
    assert s["op"] == "replace"
    assert s["word_ids"] == ["w3"]
    assert s["new_text"] == "chlorine"
    assert data["warnings"] == ["dropped suggestion 3: out of range"]
    assert data["settings_applied"]["suggest_adlib_removal"] is True
    mock.suggest.assert_called_once()


def test_settings_passed_through(client: TestClient) -> None:
    mock = _override_service(_make_result([]))
    resp = client.post(
        "/api/review/test-job/auto-correct",
        json={**BODY, "settings": {"suggest_adlib_removal": False, "min_confidence": 0.8}},
    )
    assert resp.status_code == 200
    settings = mock.suggest.call_args.kwargs["settings"]
    assert settings.suggest_adlib_removal is False
    assert settings.min_confidence == 0.8


def test_invalid_settings_400(client: TestClient) -> None:
    _override_service(_make_result([]))
    resp = client.post(
        "/api/review/test-job/auto-correct",
        json={**BODY, "settings": {"bogus_knob": True}},
    )
    assert resp.status_code == 400
    assert "invalid settings" in resp.json()["detail"]


def test_no_references_422_propagates(client: TestClient) -> None:
    _override_service(AutoCorrectServiceError("no reference lyrics", status_code=422))
    resp = client.post("/api/review/test-job/auto-correct", json=BODY)
    assert resp.status_code == 422


def test_model_failure_502_propagates(client: TestClient) -> None:
    _override_service(AutoCorrectServiceError("AI model call failed", status_code=502))
    resp = client.post("/api/review/test-job/auto-correct", json=BODY)
    assert resp.status_code == 502


def test_missing_body_fields_422(client: TestClient) -> None:
    _override_service(_make_result([]))
    resp = client.post("/api/review/test-job/auto-correct", json={"segments": []})
    assert resp.status_code == 422  # reference_lyrics is required
