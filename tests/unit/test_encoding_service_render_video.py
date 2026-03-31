"""Tests for EncodingService render_video methods."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.encoding_service import EncodingService


@pytest.fixture
def encoding_service():
    """Create an EncodingService with mocked credentials."""
    with patch.object(EncodingService, '_load_credentials') as mock_load:
        service = EncodingService()
        service._url = "http://test-worker:8080"
        service._api_key = "test-key"
        service._initialized = True
        yield service


class TestSubmitRenderVideoJob:
    def test_posts_to_render_video_endpoint(self, encoding_service):
        render_config = {
            "job_id": "test-123",
            "original_corrections_gcs_path": "gs://bucket/corrections.json",
            "audio_gcs_path": "gs://bucket/audio.flac",
            "output_gcs_prefix": "gs://bucket/jobs/test-123/",
            "artist": "Test",
            "title": "Song",
        }
        mock_response = {"status": 200, "json": {"status": "accepted", "job_id": "test-123"}, "text": None}
        with patch.object(encoding_service, '_request_with_retry', new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.get_event_loop().run_until_complete(
                encoding_service.submit_render_video_job("test-123", render_config)
            )
        assert result["status"] == "accepted"

    def test_raises_on_401(self, encoding_service):
        render_config = {"job_id": "test-123", "original_corrections_gcs_path": "gs://bucket/corrections.json", "audio_gcs_path": "gs://bucket/audio.flac", "output_gcs_prefix": "gs://bucket/jobs/test-123/", "artist": "Test", "title": "Song"}
        mock_response = {"status": 401, "json": None, "text": "Unauthorized"}
        with patch.object(encoding_service, '_request_with_retry', new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(RuntimeError, match="Invalid API key"):
                asyncio.get_event_loop().run_until_complete(
                    encoding_service.submit_render_video_job("test-123", render_config)
                )

    def test_handles_cached_result(self, encoding_service):
        render_config = {"job_id": "test-123", "original_corrections_gcs_path": "gs://bucket/corrections.json", "audio_gcs_path": "gs://bucket/audio.flac", "output_gcs_prefix": "gs://bucket/jobs/test-123/", "artist": "Test", "title": "Song"}
        submit_response = {"status": 409, "json": None, "text": "Conflict"}
        status_response = {"status": "complete", "output_files": ["jobs/test-123/videos/with_vocals.mkv"], "metadata": {"countdown_padding_added": False}}
        with patch.object(encoding_service, '_request_with_retry', new_callable=AsyncMock, return_value=submit_response):
            with patch.object(encoding_service, 'get_job_status', new_callable=AsyncMock, return_value=status_response):
                result = asyncio.get_event_loop().run_until_complete(
                    encoding_service.submit_render_video_job("test-123", render_config)
                )
        assert result["status"] == "cached"


class TestRenderVideoOnGce:
    def test_submits_and_waits(self, encoding_service):
        render_config = {"job_id": "test-123", "original_corrections_gcs_path": "gs://bucket/corrections.json", "audio_gcs_path": "gs://bucket/audio.flac", "output_gcs_prefix": "gs://bucket/jobs/test-123/", "artist": "Test", "title": "Song"}
        submit_result = {"status": "accepted", "job_id": "test-123"}
        wait_result = {"status": "complete", "output_files": ["jobs/test-123/videos/with_vocals.mkv"], "metadata": {"countdown_padding_added": False}}
        with patch.object(encoding_service, 'submit_render_video_job', new_callable=AsyncMock, return_value=submit_result):
            with patch.object(encoding_service, 'wait_for_completion', new_callable=AsyncMock, return_value=wait_result):
                result = asyncio.get_event_loop().run_until_complete(
                    encoding_service.render_video_on_gce("test-123", render_config)
                )
        assert result["status"] == "complete"

    def test_returns_immediately_on_cached(self, encoding_service):
        render_config = {"job_id": "test-123", "original_corrections_gcs_path": "gs://bucket/corrections.json", "audio_gcs_path": "gs://bucket/audio.flac", "output_gcs_prefix": "gs://bucket/jobs/test-123/", "artist": "Test", "title": "Song"}
        submit_result = {"status": "cached", "job_id": "test-123", "output_files": ["jobs/test-123/videos/with_vocals.mkv"], "metadata": {"countdown_padding_added": False}}
        with patch.object(encoding_service, 'submit_render_video_job', new_callable=AsyncMock, return_value=submit_result):
            with patch.object(encoding_service, 'wait_for_completion', new_callable=AsyncMock) as mock_wait:
                result = asyncio.get_event_loop().run_until_complete(
                    encoding_service.render_video_on_gce("test-123", render_config)
                )
                mock_wait.assert_not_called()
        assert result["status"] == "complete"
