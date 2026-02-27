"""
Tests for the GCE encoding service (encoding_service.py).

Tests resilience features: idempotent submission handling, cached/in_progress
responses, and 409 fallback behavior.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.services.encoding_service import EncodingService


@pytest.fixture
def encoding_service():
    """Create an EncodingService with mocked credentials."""
    service = EncodingService()
    service._url = "http://fake-worker:8080"
    service._api_key = "test-key"
    service._initialized = True
    return service


class TestSubmitEncodingJob:
    """Tests for submit_encoding_job() resilience."""

    @pytest.mark.asyncio
    async def test_submit_returns_accepted(self, encoding_service):
        """Normal submission returns accepted response."""
        mock_resp = {"status": 200, "json": {"status": "accepted", "job_id": "j1"}, "text": None}
        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp):
            result = await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})
        assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_submit_returns_cached(self, encoding_service):
        """When worker returns cached (job already complete), pass through."""
        mock_resp = {"status": 200, "json": {"status": "cached", "job_id": "j1", "output_files": ["a.mp4"]}, "text": None}
        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp):
            result = await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})
        assert result["status"] == "cached"
        assert result["output_files"] == ["a.mp4"]

    @pytest.mark.asyncio
    async def test_submit_returns_in_progress(self, encoding_service):
        """When worker returns in_progress, pass through."""
        mock_resp = {"status": 200, "json": {"status": "in_progress", "job_id": "j1"}, "text": None}
        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp):
            result = await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})
        assert result["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_409_fallback_returns_cached_if_complete(self, encoding_service):
        """On 409, fall back to get_job_status(); return cached if complete."""
        mock_resp = {"status": 409, "json": None, "text": "Job j1 already exists"}
        mock_status = {"status": "complete", "output_files": ["a.mp4", "b.mp4"]}

        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp), \
             patch.object(encoding_service, "get_job_status", new_callable=AsyncMock, return_value=mock_status):
            result = await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})

        assert result["status"] == "cached"
        assert result["output_files"] == ["a.mp4", "b.mp4"]

    @pytest.mark.asyncio
    async def test_409_fallback_returns_in_progress_if_running(self, encoding_service):
        """On 409, fall back to get_job_status(); return in_progress if running."""
        mock_resp = {"status": 409, "json": None, "text": "Job j1 already exists"}
        mock_status = {"status": "running", "progress": 42}

        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp), \
             patch.object(encoding_service, "get_job_status", new_callable=AsyncMock, return_value=mock_status):
            result = await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})

        assert result["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_409_fallback_returns_in_progress_if_pending(self, encoding_service):
        """On 409, fall back to get_job_status(); return in_progress if pending."""
        mock_resp = {"status": 409, "json": None, "text": "Job j1 already exists"}
        mock_status = {"status": "pending", "progress": 0}

        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp), \
             patch.object(encoding_service, "get_job_status", new_callable=AsyncMock, return_value=mock_status):
            result = await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})

        assert result["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_409_fallback_raises_if_failed(self, encoding_service):
        """On 409, fall back to get_job_status(); raise if status is failed."""
        mock_resp = {"status": 409, "json": None, "text": "Job j1 already exists"}
        mock_status = {"status": "failed", "error": "ffmpeg crash"}

        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp), \
             patch.object(encoding_service, "get_job_status", new_callable=AsyncMock, return_value=mock_status):
            with pytest.raises(RuntimeError, match="already exists with status: failed"):
                await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})

    @pytest.mark.asyncio
    async def test_409_fallback_raises_if_status_check_404(self, encoding_service):
        """On 409, if status check returns 404 (worker restarted), raise conflict error."""
        mock_resp = {"status": 409, "json": None, "text": "Job j1 already exists"}

        with patch.object(encoding_service, "_request_with_retry", new_callable=AsyncMock, return_value=mock_resp), \
             patch.object(encoding_service, "get_job_status", new_callable=AsyncMock, side_effect=RuntimeError("Encoding job j1 not found")):
            with pytest.raises(RuntimeError, match="conflict: 409 but job not found"):
                await encoding_service.submit_encoding_job("j1", "gs://in", "gs://out", {})


class TestEncodeVideos:
    """Tests for encode_videos() handling of submit result status."""

    @pytest.mark.asyncio
    async def test_cached_submit_returns_immediately(self, encoding_service):
        """When submit returns cached, return immediately without polling."""
        cached_result = {"status": "cached", "job_id": "j1", "output_files": ["a.mp4"]}

        with patch.object(encoding_service, "submit_encoding_job", new_callable=AsyncMock, return_value=cached_result) as mock_submit, \
             patch.object(encoding_service, "wait_for_completion", new_callable=AsyncMock) as mock_wait:
            result = await encoding_service.encode_videos("j1", "gs://in", "gs://out")

        assert result["status"] == "complete"
        assert result["output_files"] == ["a.mp4"]
        mock_submit.assert_called_once()
        mock_wait.assert_not_called()

    @pytest.mark.asyncio
    async def test_in_progress_submit_falls_through_to_wait(self, encoding_service):
        """When submit returns in_progress, fall through to wait_for_completion."""
        in_progress_result = {"status": "in_progress", "job_id": "j1"}
        completed_result = {"status": "complete", "output_files": ["a.mp4"]}

        with patch.object(encoding_service, "submit_encoding_job", new_callable=AsyncMock, return_value=in_progress_result), \
             patch.object(encoding_service, "wait_for_completion", new_callable=AsyncMock, return_value=completed_result) as mock_wait:
            result = await encoding_service.encode_videos("j1", "gs://in", "gs://out")

        assert result["status"] == "complete"
        mock_wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_accepted_submit_falls_through_to_wait(self, encoding_service):
        """Normal accepted submission falls through to wait_for_completion."""
        accepted_result = {"status": "accepted", "job_id": "j1"}
        completed_result = {"status": "complete", "output_files": ["a.mp4"]}

        with patch.object(encoding_service, "submit_encoding_job", new_callable=AsyncMock, return_value=accepted_result), \
             patch.object(encoding_service, "wait_for_completion", new_callable=AsyncMock, return_value=completed_result) as mock_wait:
            result = await encoding_service.encode_videos("j1", "gs://in", "gs://out")

        assert result["status"] == "complete"
        mock_wait.assert_called_once()
