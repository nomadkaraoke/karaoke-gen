"""
Unit tests for the create-custom-instrumental endpoint and
reference_lyrics stripping in submit_corrections.

Tests the route logic in backend/api/routes/jobs.py by mocking
the service layer (JobManager, AudioEditingService, StorageService).
"""
import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch
from contextlib import ExitStack
from fastapi.testclient import TestClient

from backend.models.job import Job, JobStatus


# --- Fixtures ---


@pytest.fixture
def review_job():
    """Create a job in AWAITING_REVIEW status with stems."""
    return Job(
        job_id="job-abc",
        status=JobStatus.AWAITING_REVIEW,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        user_email="owner@example.com",
        file_urls={
            "stems": {
                "instrumental_clean": "jobs/job-abc/stems/instrumental_clean.flac",
                "backing_vocals": "jobs/job-abc/stems/backing_vocals.flac",
            }
        },
    )


@pytest.fixture
def in_review_job():
    """Create a job in IN_REVIEW status with stems."""
    return Job(
        job_id="job-abc",
        status=JobStatus.IN_REVIEW,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        user_email="owner@example.com",
        file_urls={
            "stems": {
                "instrumental_clean": "jobs/job-abc/stems/instrumental_clean.flac",
                "backing_vocals": "jobs/job-abc/stems/backing_vocals.flac",
            }
        },
    )


@pytest.fixture
def job_no_stems():
    """Create a job in review state missing stems."""
    return Job(
        job_id="job-abc",
        status=JobStatus.AWAITING_REVIEW,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        file_urls={},
    )


@pytest.fixture
def completed_job():
    """Create a job in COMPLETE status."""
    return Job(
        job_id="job-abc",
        status=JobStatus.COMPLETE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        file_urls={
            "stems": {
                "instrumental_clean": "jobs/job-abc/stems/clean.flac",
                "backing_vocals": "jobs/job-abc/stems/backing.flac",
            }
        },
    )


@pytest.fixture
def mock_job_manager():
    """Create a mock JobManager."""
    manager = MagicMock()
    manager.update_state_data.return_value = None
    manager.update_file_url.return_value = None
    manager.transition_to_state.return_value = True
    return manager


@pytest.fixture
def mock_worker_service():
    """Create a mock WorkerService."""
    service = MagicMock()
    service.trigger_audio_worker = AsyncMock(return_value=True)
    service.trigger_lyrics_worker = AsyncMock(return_value=True)
    return service


@pytest.fixture
def patched_client(mock_job_manager, mock_worker_service):
    """Create TestClient with all core patches active for the test duration."""
    mock_creds = MagicMock()
    mock_creds.universe_domain = "googleapis.com"

    with patch("backend.api.routes.jobs.job_manager", mock_job_manager), \
         patch("backend.api.routes.jobs.worker_service", mock_worker_service), \
         patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"), \
         patch("google.auth.default", return_value=(mock_creds, "test-project")):
        from backend.main import app
        yield TestClient(app)


# --- Tests: create-custom-instrumental endpoint ---


class TestCreateCustomInstrumentalEndpoint:
    """Tests for POST /api/jobs/{job_id}/create-custom-instrumental."""

    def test_success_returns_audio_url(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Happy path: creates custom instrumental and returns signed audio URL."""
        mock_job_manager.get_job.return_value = review_job

        mock_result = MagicMock()
        mock_result.total_muted_duration_seconds = 5.0

        mock_editing_service = MagicMock()
        mock_editing_service.create_custom_instrumental.return_value = mock_result

        mock_transcoding = MagicMock()
        mock_transcoding.get_review_audio_url_async = AsyncMock(
            return_value="https://storage.googleapis.com/signed-url"
        )

        with patch(
            "backend.services.audio_editing_service.AudioEditingService",
            return_value=mock_editing_service,
        ), patch(
            "backend.services.audio_transcoding_service.AudioTranscodingService",
            return_value=mock_transcoding,
        ), patch(
            "backend.api.routes.jobs.StorageService",
            return_value=MagicMock(),
        ):
            response = patched_client.post(
                "/api/jobs/job-abc/create-custom-instrumental",
                json={
                    "mute_regions": [
                        {"start_seconds": 10.0, "end_seconds": 20.0},
                        {"start_seconds": 60.0, "end_seconds": 65.0},
                    ]
                },
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["audio_url"] == "https://storage.googleapis.com/signed-url"
        assert data["muted_duration_seconds"] == 5.0
        assert "2 mute regions" in data["message"]

    def test_success_in_review_status(
        self, in_review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Endpoint works when job is IN_REVIEW (not just AWAITING_REVIEW)."""
        mock_job_manager.get_job.return_value = in_review_job

        mock_result = MagicMock()
        mock_result.total_muted_duration_seconds = 3.0

        mock_editing_service = MagicMock()
        mock_editing_service.create_custom_instrumental.return_value = mock_result

        mock_transcoding = MagicMock()
        mock_transcoding.get_review_audio_url_async = AsyncMock(
            return_value="https://example.com/signed"
        )

        with patch(
            "backend.services.audio_editing_service.AudioEditingService",
            return_value=mock_editing_service,
        ), patch(
            "backend.services.audio_transcoding_service.AudioTranscodingService",
            return_value=mock_transcoding,
        ), patch(
            "backend.api.routes.jobs.StorageService",
            return_value=MagicMock(),
        ):
            response = patched_client.post(
                "/api/jobs/job-abc/create-custom-instrumental",
                json={"mute_regions": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_job_not_found_returns_404(
        self, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Returns 404 when job does not exist."""
        mock_job_manager.get_job.return_value = None

        response = patched_client.post(
            "/api/jobs/nonexistent/create-custom-instrumental",
            json={"mute_regions": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_wrong_status_returns_400(
        self, completed_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Returns 400 when job is not in a review state."""
        mock_job_manager.get_job.return_value = completed_job

        response = patched_client.post(
            "/api/jobs/job-abc/create-custom-instrumental",
            json={"mute_regions": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "not in review state" in response.json()["detail"].lower()

    def test_missing_stems_returns_400(
        self, job_no_stems, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Returns 400 when job does not have required stem files."""
        mock_job_manager.get_job.return_value = job_no_stems

        response = patched_client.post(
            "/api/jobs/job-abc/create-custom-instrumental",
            json={"mute_regions": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "missing required stems" in response.json()["detail"].lower()

    def test_empty_mute_regions_returns_422(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Returns 422 when mute_regions is empty (Pydantic validation)."""
        mock_job_manager.get_job.return_value = review_job

        response = patched_client.post(
            "/api/jobs/job-abc/create-custom-instrumental",
            json={"mute_regions": []},
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_stores_custom_instrumental_path(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Verifies update_file_url is called to persist the custom instrumental path."""
        mock_job_manager.get_job.return_value = review_job

        mock_result = MagicMock()
        mock_result.total_muted_duration_seconds = 2.0

        mock_editing_service = MagicMock()
        mock_editing_service.create_custom_instrumental.return_value = mock_result

        mock_transcoding = MagicMock()
        mock_transcoding.get_review_audio_url_async = AsyncMock(
            return_value="https://example.com/signed"
        )

        with patch(
            "backend.services.audio_editing_service.AudioEditingService",
            return_value=mock_editing_service,
        ), patch(
            "backend.services.audio_transcoding_service.AudioTranscodingService",
            return_value=mock_transcoding,
        ), patch(
            "backend.api.routes.jobs.StorageService",
            return_value=MagicMock(),
        ):
            patched_client.post(
                "/api/jobs/job-abc/create-custom-instrumental",
                json={"mute_regions": [{"start_seconds": 5.0, "end_seconds": 10.0}]},
                headers=auth_headers,
            )

        # Verify file URL was stored
        mock_job_manager.update_file_url.assert_called_once_with(
            "job-abc", "stems", "custom_instrumental",
            "jobs/job-abc/stems/custom_instrumental.flac",
        )

    def test_service_error_returns_500(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Returns 500 when the editing service raises an exception."""
        mock_job_manager.get_job.return_value = review_job

        mock_editing_service = MagicMock()
        mock_editing_service.create_custom_instrumental.side_effect = RuntimeError(
            "FFmpeg failed"
        )

        with patch(
            "backend.services.audio_editing_service.AudioEditingService",
            return_value=mock_editing_service,
        ), patch(
            "backend.api.routes.jobs.StorageService",
            return_value=MagicMock(),
        ):
            response = patched_client.post(
                "/api/jobs/job-abc/create-custom-instrumental",
                json={"mute_regions": [{"start_seconds": 1.0, "end_seconds": 2.0}]},
                headers=auth_headers,
            )

        assert response.status_code == 500
        assert "FFmpeg failed" in response.json()["detail"]


# --- Tests: reference_lyrics stripping in submit_corrections ---


class TestSubmitCorrectionsReferenceLyricsStripping:
    """Tests for reference_lyrics being stripped from Firestore save in submit_corrections."""

    def _make_corrections_payload(self, extra_keys=None):
        """Build a valid corrections payload that passes CorrectionsSubmission validation.

        The CorrectionsSubmission model requires 'lines' and 'metadata' in corrections.
        """
        data = {
            "lines": [{"text": "Hello world", "start": 0.0, "end": 1.0}],
            "metadata": {"source": "test", "version": "1.0"},
        }
        if extra_keys:
            data.update(extra_keys)
        return data

    def test_reference_lyrics_stripped_from_firestore_save(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Verify reference_lyrics is excluded from Firestore state_data update."""
        mock_job_manager.get_job.return_value = review_job

        mock_storage = MagicMock()

        corrections_data = self._make_corrections_payload(extra_keys={
            "corrected_segments": [{"text": "hello"}],
            "reference_lyrics": {
                "genius": "Very long lyrics content..." * 1000,
            },
        })

        with patch("backend.services.storage_service.StorageService", return_value=mock_storage), \
             patch("backend.api.routes.jobs.StorageService", return_value=mock_storage):
            response = patched_client.post(
                "/api/jobs/job-abc/corrections",
                json={"corrections": corrections_data},
                headers=auth_headers,
            )

        assert response.status_code == 200

        # Check update_state_data was called with data that does NOT contain reference_lyrics
        state_data_call = mock_job_manager.update_state_data.call_args_list[0]
        saved_data = state_data_call[0][2]  # Third positional arg is the data dict
        assert "reference_lyrics" not in saved_data

        # Check preserved keys are still present
        assert "lines" in saved_data
        assert "metadata" in saved_data
        assert "corrected_segments" in saved_data

    def test_full_corrections_saved_to_gcs(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Verify the full corrections data (including reference_lyrics) goes to GCS."""
        mock_job_manager.get_job.return_value = review_job

        mock_storage = MagicMock()

        corrections_data = self._make_corrections_payload(extra_keys={
            "reference_lyrics": {
                "genius": "Full lyrics text",
            },
        })

        with patch("backend.services.storage_service.StorageService", return_value=mock_storage), \
             patch("backend.api.routes.jobs.StorageService", return_value=mock_storage):
            response = patched_client.post(
                "/api/jobs/job-abc/corrections",
                json={"corrections": corrections_data},
                headers=auth_headers,
            )

        assert response.status_code == 200

        # Check upload_json was called with the FULL data including reference_lyrics
        mock_storage.upload_json.assert_called_once()
        gcs_path, gcs_data = mock_storage.upload_json.call_args[0]
        assert "corrections_updated.json" in gcs_path
        assert "reference_lyrics" in gcs_data

    def test_corrections_without_reference_lyrics_still_works(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, auth_headers
    ):
        """Verify submit_corrections works fine when reference_lyrics is not present."""
        mock_job_manager.get_job.return_value = review_job

        mock_storage = MagicMock()

        corrections_data = self._make_corrections_payload(extra_keys={
            "corrected_segments": [{"text": "hello world"}],
        })

        with patch("backend.services.storage_service.StorageService", return_value=mock_storage), \
             patch("backend.api.routes.jobs.StorageService", return_value=mock_storage):
            response = patched_client.post(
                "/api/jobs/job-abc/corrections",
                json={"corrections": corrections_data},
                headers=auth_headers,
            )

        assert response.status_code == 200

        # State data should have all keys (no reference_lyrics to strip)
        state_data_call = mock_job_manager.update_state_data.call_args_list[0]
        saved_data = state_data_call[0][2]
        assert "lines" in saved_data
        assert "metadata" in saved_data
        assert "reference_lyrics" not in saved_data
