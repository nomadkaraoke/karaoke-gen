"""Unit tests for CustomLyricsService."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.services.custom_lyrics_service import (
    CustomLyricsResult,
    CustomLyricsService,
    CustomLyricsServiceError,
)


@pytest.fixture
def service() -> CustomLyricsService:
    """Service with mocked settings."""
    with patch(
        "backend.services.custom_lyrics_service.get_settings"
    ) as mock_settings:
        mock_settings.return_value = MagicMock(
            google_cloud_project="test-project",
            custom_lyrics_model="gemini-3.1-pro-preview",
            custom_lyrics_max_file_mb=5,
            custom_lyrics_max_input_lines=500,
        )
        yield CustomLyricsService()


def _mock_gemini_response(lines: list[str]) -> MagicMock:
    """Build a MagicMock that mimics genai's GenerateContentResponse."""
    response = MagicMock()
    response.text = json.dumps({"lines": lines})
    return response


def test_text_only_happy_path(service: CustomLyricsService) -> None:
    existing_lines = ["happy birthday to you", "happy birthday to you"]
    expected_output = ["happy birthday dear jane", "happy birthday dear jane"]

    with patch(
        "backend.services.custom_lyrics_service.genai.Client"
    ) as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response(
            expected_output
        )
        mock_client_cls.return_value = mock_client

        result = service.generate(
            job_id="job-123",
            existing_lines=existing_lines,
            artist="Anonymous",
            title="Happy Birthday",
            custom_text="Replace 'to you' with 'dear jane' wherever it makes sense",
            file_bytes=None,
            file_mime=None,
            file_name=None,
            notes=None,
        )

    assert isinstance(result, CustomLyricsResult)
    assert result.lines == expected_output
    assert result.line_count_mismatch is False
    assert result.retry_count == 0
    assert result.model == "gemini-3.1-pro-preview"
    assert mock_client_cls.call_args.kwargs == {
        "vertexai": True,
        "project": "test-project",
        "location": "global",
    }
