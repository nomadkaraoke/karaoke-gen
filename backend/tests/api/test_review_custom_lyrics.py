"""Route tests for POST /api/review/{job_id}/custom-lyrics/generate."""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.custom_lyrics_service import (
    CustomLyricsResult,
    CustomLyricsServiceError,
)


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
    """Install a service mock via dependency_overrides."""
    from backend.api.routes.review import _get_custom_lyrics_service_dep

    mock_service = MagicMock()
    if isinstance(result_or_exc, Exception):
        mock_service.generate.side_effect = result_or_exc
    else:
        mock_service.generate.return_value = result_or_exc

    app.dependency_overrides[_get_custom_lyrics_service_dep] = lambda: mock_service
    return mock_service


def teardown_function(_):  # noqa: ANN001
    app.dependency_overrides.pop(
        __import__(
            "backend.api.routes.review", fromlist=["_get_custom_lyrics_service_dep"]
        )._get_custom_lyrics_service_dep,
        None,
    )


def _make_result(
    lines: list[str] | None = None,
    *,
    iterations_used: int = 0,
    stop_reason_value: str = "success",
    line_count_mismatch: bool = False,
    new_segment_timing: list[tuple[float, float]] | None = None,
    warnings: list[str] | None = None,
    line_metadata: list | None = None,
) -> "CustomLyricsResult":
    """Build a minimal CustomLyricsResult for service-mock returns."""
    from backend.services.custom_lyrics.result import CustomLyricsResult as _Result, StopReason
    from backend.services.custom_lyrics.settings import GenerationSettings
    from backend.services.custom_lyrics.validator import LineValidation, Severity

    out_lines = lines if lines is not None else ["dear jane", "dear jane"]

    if line_metadata is None:
        line_metadata = [
            LineValidation(
                line_index=i,
                target_text=f"target {i}",
                candidate_text=line,
                target_syllables=[2, 2, 2, 2],
                candidate_syllables=[2, 2, 2, 2],
                min_delta=0,
                passes=True,
                severity=Severity.OK,
                time_budget_seconds=1.0,
            )
            for i, line in enumerate(out_lines)
        ]

    return _Result(
        lines=out_lines,
        line_metadata=line_metadata,
        iterations_used=iterations_used,
        stop_reason=StopReason(stop_reason_value),
        settings_applied=GenerationSettings(),
        model="gemini-3.1-pro-preview",
        duration_ms=100,
        new_segment_timing=new_segment_timing,
        line_count_mismatch=line_count_mismatch,
        warnings=warnings or [],
    )


def test_happy_path_text(client: TestClient) -> None:
    _override_service(_make_result(["a", "b"]))

    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["one", "two"]),
            "custom_text": "make it about cats",
            "notes": "for clara's birthday",
            "artist": "Test",
            "title": "Test Song",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["lines"] == ["a", "b"]
    assert body["line_count_mismatch"] is False
    assert body["model"] == "gemini-3.1-pro-preview"
    assert body["iterations_used"] == 0
    assert body["stop_reason"] == "success"


def test_missing_existing_lines_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={"custom_text": "anything"},
    )
    assert response.status_code == 422  # Form(...) required field


def test_existing_lines_not_json_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": "not-json",
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400
    assert "JSON" in response.json()["detail"]


def test_existing_lines_wrong_type_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps([1, 2, 3]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400


def test_existing_lines_empty_array_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps([]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400


def test_no_text_and_no_file_400(client: TestClient) -> None:
    _override_service(MagicMock())
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={"existing_lines": json.dumps(["a"])},
    )
    assert response.status_code == 400
    assert "custom_text" in response.json()["detail"]


def test_file_upload_passed_through(client: TestClient) -> None:
    mock_service = _override_service(_make_result(["x"]))

    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={"existing_lines": json.dumps(["a"])},
        files={
            "file": ("brief.txt", io.BytesIO(b"do this"), "text/plain"),
        },
    )

    assert response.status_code == 200
    call_kwargs = mock_service.generate.call_args.kwargs
    assert call_kwargs["file_bytes"] == b"do this"
    assert call_kwargs["file_mime"] == "text/plain"
    assert call_kwargs["file_name"] == "brief.txt"


def test_service_validation_error_propagates_status(client: TestClient) -> None:
    _override_service(
        CustomLyricsServiceError("file too big", status_code=400)
    )
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["a"]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 400
    assert "file too big" in response.json()["detail"]


def test_service_502_propagates_status(client: TestClient) -> None:
    _override_service(
        CustomLyricsServiceError("AI returned non-JSON", status_code=502)
    )
    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["a"]),
            "custom_text": "anything",
        },
    )
    assert response.status_code == 502


def test_line_count_mismatch_returns_200_with_flag(client: TestClient) -> None:
    _override_service(
        _make_result(
            ["only-one"],
            line_count_mismatch=True,
            stop_reason_value="line_count_mismatch",
            warnings=["AI returned 1 lines but 2 were expected."],
        )
    )

    response = client.post(
        "/api/review/test-job/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["a", "b"]),
            "custom_text": "anything",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["line_count_mismatch"] is True
    assert len(body["warnings"]) == 1
    assert body["stop_reason"] == "line_count_mismatch"


def test_endpoint_accepts_settings_json(client: TestClient) -> None:
    """Posting valid settings_json plumbs settings through to the service."""
    mock_service = _override_service(_make_result(["dear", "jane"]))
    response = client.post(
        "/api/review/job-1/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["aa", "bb"]),
            "custom_text": "x",
            "settings_json": json.dumps({
                "allow_reword": False,
                "strictness": "tight",
            }),
        },
    )
    assert response.status_code == 200, response.text
    call_kwargs = mock_service.generate.call_args.kwargs
    settings = call_kwargs["settings"]
    assert settings.allow_reword is False
    assert settings.strictness.value == "tight"
    # Defaults preserved when not specified
    assert settings.allow_omit is True
    assert settings.fixed_line_count is True


def test_endpoint_rejects_invalid_settings_json(client: TestClient) -> None:
    _override_service(_make_result())
    response = client.post(
        "/api/review/job-1/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["aa"]),
            "custom_text": "x",
            "settings_json": "not json",
        },
    )
    assert response.status_code == 400
    assert "settings_json" in response.json()["detail"].lower()


def test_endpoint_rejects_invalid_strictness(client: TestClient) -> None:
    _override_service(_make_result())
    response = client.post(
        "/api/review/job-1/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["aa"]),
            "custom_text": "x",
            "settings_json": json.dumps({"strictness": "extreme"}),
        },
    )
    assert response.status_code == 400


def test_endpoint_returns_line_metadata(client: TestClient) -> None:
    """Response includes per-line metadata."""
    _override_service(_make_result(["dear", "jane"]))
    response = client.post(
        "/api/review/job-1/custom-lyrics/generate",
        data={
            "existing_lines": json.dumps(["aa", "bb"]),
            "custom_text": "x",
        },
    )
    body = response.json()
    assert "line_metadata" in body
    assert len(body["line_metadata"]) == 2
    for entry in body["line_metadata"]:
        assert "min_delta" in entry
        assert "severity" in entry
        assert entry["severity"] in {"ok", "minor", "major"}
