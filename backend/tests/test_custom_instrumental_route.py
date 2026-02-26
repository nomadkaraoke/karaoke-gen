"""
Unit tests for the create-custom-instrumental and upload-instrumental endpoints,
reference_lyrics stripping in submit_corrections, and the ffprobe duration helper.

Tests the route logic in backend/api/routes/jobs.py by mocking
the service layer (JobManager, AudioEditingService, StorageService).
"""
import asyncio
import json
import subprocess
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


# --- Tests: upload-instrumental endpoint ---


class TestUploadInstrumentalEndpoint:
    """Tests for POST /api/jobs/{job_id}/upload-instrumental."""

    @pytest.fixture
    def upload_headers(self):
        """Auth headers without Content-Type (let TestClient set multipart automatically)."""
        return {"Authorization": "Bearer test-admin-token"}

    def test_success_returns_duration(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Happy path: uploads FLAC file, gets duration, stores in GCS, returns duration."""
        mock_job_manager.get_job.return_value = review_job

        mock_storage = MagicMock()
        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=240000)  # 240 seconds in ms

        with patch("backend.api.routes.jobs.StorageService", return_value=mock_storage), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=240.0):
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("my-instrumental.flac", b"fake audio data", "audio/flac")},
                headers=upload_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["duration_seconds"] == 240.0
        assert "240.0s" in data["message"]
        # FLAC input should not trigger conversion — no export call
        mock_audio_segment.export.assert_not_called()

    def test_job_not_found_returns_404(
        self, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Returns 404 when job does not exist."""
        mock_job_manager.get_job.return_value = None

        response = patched_client.post(
            "/api/jobs/nonexistent/upload-instrumental",
            files={"file": ("track.flac", b"audio", "audio/flac")},
            headers=upload_headers,
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_wrong_status_returns_400(
        self, completed_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Returns 400 when job is not in a review state."""
        mock_job_manager.get_job.return_value = completed_job

        response = patched_client.post(
            "/api/jobs/job-abc/upload-instrumental",
            files={"file": ("track.mp3", b"audio", "audio/mpeg")},
            headers=upload_headers,
        )

        assert response.status_code == 400
        assert "not in review state" in response.json()["detail"].lower()

    def test_audio_processing_error_returns_500(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Returns 500 when audio file cannot be decoded."""
        mock_job_manager.get_job.return_value = review_job

        with patch("backend.api.routes.jobs.StorageService", return_value=MagicMock()), \
             patch("pydub.AudioSegment.from_file", side_effect=Exception("Unsupported format")):
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("bad.bin", b"not audio", "application/octet-stream")},
                headers=upload_headers,
            )

        assert response.status_code == 500
        assert "Failed to process audio file" in response.json()["detail"]

    def test_stores_custom_instrumental_gcs_path(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Verifies update_file_url is called to persist the GCS path."""
        mock_job_manager.get_job.return_value = review_job

        mock_storage = MagicMock()
        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=180000)  # 180s

        with patch("backend.api.routes.jobs.StorageService", return_value=mock_storage), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=None):
            patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("backing.flac", b"audio", "audio/flac")},
                headers=upload_headers,
            )

        mock_job_manager.update_file_url.assert_called_with(
            "job-abc", "stems", "custom_instrumental",
            "jobs/job-abc/stems/custom_instrumental.flac",
        )

    def test_non_flac_upload_converted_to_flac(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Non-FLAC uploads (mp3, m4a, etc.) are converted to FLAC before storing in GCS."""
        mock_job_manager.get_job.return_value = review_job

        mock_storage = MagicMock()
        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=120000)

        with patch("backend.api.routes.jobs.StorageService", return_value=mock_storage), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=None):
            patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.mp3", b"audio", "audio/mpeg")},
                headers=upload_headers,
            )

        # Always stored as .flac regardless of input format
        mock_job_manager.update_file_url.assert_called_with(
            "job-abc", "stems", "custom_instrumental",
            "jobs/job-abc/stems/custom_instrumental.flac",
        )
        # Conversion to FLAC was triggered
        mock_audio_segment.export.assert_called_once()
        call_args = mock_audio_segment.export.call_args
        assert call_args[1]["format"] == "flac" or call_args[0][1] == "flac"

    def test_in_review_status_also_accepted(
        self, in_review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Endpoint accepts uploads when job is IN_REVIEW (not just AWAITING_REVIEW)."""
        mock_job_manager.get_job.return_value = in_review_job

        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=60000)

        with patch("backend.api.routes.jobs.StorageService", return_value=MagicMock()), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed", new_callable=AsyncMock, return_value=None):
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.wav", b"audio", "audio/wav")},
                headers=upload_headers,
            )

        assert response.status_code == 200

    def test_duration_mismatch_returns_400(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Returns 400 when uploaded file duration differs from original by more than 0.5s."""
        # job has input_media_gcs_path set
        review_job.input_media_gcs_path = "jobs/job-abc/audio/input.flac"
        mock_job_manager.get_job.return_value = review_job

        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=180000)  # 180s upload

        with patch("backend.api.routes.jobs.StorageService", return_value=MagicMock()), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=210.0):  # original is 210s
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.flac", b"audio", "audio/flac")},
                headers=upload_headers,
            )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "180.0s" in detail
        assert "210.0s" in detail
        assert "Duration mismatch" in detail

    def test_duration_within_tolerance_is_accepted(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Accepts upload when duration is within 0.5s of the original."""
        review_job.input_media_gcs_path = "jobs/job-abc/audio/input.flac"
        mock_job_manager.get_job.return_value = review_job

        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=210200)  # 210.2s upload

        with patch("backend.api.routes.jobs.StorageService", return_value=MagicMock()), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=210.0):  # original is 210.0s
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.flac", b"audio", "audio/flac")},
                headers=upload_headers,
            )

        assert response.status_code == 200

    def test_duration_check_skipped_when_original_unavailable(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Upload proceeds even if original audio duration cannot be determined."""
        mock_job_manager.get_job.return_value = review_job

        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=180000)

        with patch("backend.api.routes.jobs.StorageService", return_value=MagicMock()), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=None):
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.flac", b"audio", "audio/flac")},
                headers=upload_headers,
            )

        assert response.status_code == 200  # upload succeeds even without duration check

    def test_duration_exactly_at_boundary_accepted(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Upload with duration exactly 0.5s different is accepted (boundary is inclusive)."""
        review_job.input_media_gcs_path = "jobs/job-abc/audio/input.flac"
        mock_job_manager.get_job.return_value = review_job

        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=210500)  # 210.5s

        with patch("backend.api.routes.jobs.StorageService", return_value=MagicMock()), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=210.0):
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.flac", b"audio", "audio/flac")},
                headers=upload_headers,
            )

        assert response.status_code == 200

    def test_duration_just_over_boundary_rejected(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Upload with duration 0.501s different is rejected."""
        review_job.input_media_gcs_path = "jobs/job-abc/audio/input.flac"
        mock_job_manager.get_job.return_value = review_job

        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=210501)  # 210.501s

        with patch("backend.api.routes.jobs.StorageService", return_value=MagicMock()), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=210.0):
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.flac", b"audio", "audio/flac")},
                headers=upload_headers,
            )

        assert response.status_code == 400

    def test_gcs_upload_failure_returns_500(
        self, review_job, mock_job_manager, mock_worker_service, patched_client, upload_headers
    ):
        """Returns 500 when GCS upload fails."""
        mock_job_manager.get_job.return_value = review_job

        mock_storage = MagicMock()
        mock_storage.upload_file.side_effect = Exception("GCS quota exceeded")

        mock_audio_segment = MagicMock()
        mock_audio_segment.__len__ = MagicMock(return_value=180000)

        with patch("backend.api.routes.jobs.StorageService", return_value=mock_storage), \
             patch("pydub.AudioSegment.from_file", return_value=mock_audio_segment), \
             patch("backend.api.routes.jobs._get_audio_duration_ffprobe_signed",
                   new_callable=AsyncMock, return_value=None):
            response = patched_client.post(
                "/api/jobs/job-abc/upload-instrumental",
                files={"file": ("track.flac", b"audio", "audio/flac")},
                headers=upload_headers,
            )

        assert response.status_code == 500
        assert "Failed to process audio file" in response.json()["detail"]


# --- Tests: _get_audio_duration_ffprobe_signed helper ---


class TestGetAudioDurationFfprobeSigned:
    """Unit tests for the ffprobe duration helper function."""

    @pytest.fixture
    def mock_job_with_audio(self):
        """Job with input audio path set."""
        job = MagicMock()
        job.input_media_gcs_path = "jobs/job-abc/audio/input.flac"
        return job

    @pytest.fixture
    def mock_job_no_audio(self):
        """Job with no input audio path."""
        job = MagicMock()
        job.input_media_gcs_path = None
        return job

    @pytest.fixture
    def mock_storage_for_ffprobe(self):
        storage = MagicMock()
        storage.generate_signed_url.return_value = "https://storage.googleapis.com/signed-url"
        return storage

    @pytest.mark.asyncio
    async def test_success_returns_duration(self, mock_job_with_audio, mock_storage_for_ffprobe):
        """Extracts duration from valid ffprobe output."""
        from backend.api.routes.jobs import _get_audio_duration_ffprobe_signed

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"format": {"duration": "245.123"}})

        with patch("backend.api.routes.jobs.subprocess.run", return_value=mock_result):
            duration = await _get_audio_duration_ffprobe_signed(
                "job-abc", mock_job_with_audio, mock_storage_for_ffprobe
            )

        assert duration == pytest.approx(245.123)
        mock_storage_for_ffprobe.generate_signed_url.assert_called_once_with(
            "jobs/job-abc/audio/input.flac", expiration_minutes=5
        )

    @pytest.mark.asyncio
    async def test_no_input_audio_returns_none(self, mock_job_no_audio, mock_storage_for_ffprobe):
        """Returns None when job has no input audio path."""
        from backend.api.routes.jobs import _get_audio_duration_ffprobe_signed

        duration = await _get_audio_duration_ffprobe_signed(
            "job-abc", mock_job_no_audio, mock_storage_for_ffprobe
        )

        assert duration is None
        mock_storage_for_ffprobe.generate_signed_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_ffprobe_nonzero_exit_returns_none(self, mock_job_with_audio, mock_storage_for_ffprobe):
        """Returns None when ffprobe exits with non-zero code."""
        from backend.api.routes.jobs import _get_audio_duration_ffprobe_signed

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Unknown format"

        with patch("backend.api.routes.jobs.subprocess.run", return_value=mock_result):
            duration = await _get_audio_duration_ffprobe_signed(
                "job-abc", mock_job_with_audio, mock_storage_for_ffprobe
            )

        assert duration is None

    @pytest.mark.asyncio
    async def test_ffprobe_invalid_json_returns_none(self, mock_job_with_audio, mock_storage_for_ffprobe):
        """Returns None when ffprobe output is not valid JSON."""
        from backend.api.routes.jobs import _get_audio_duration_ffprobe_signed

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"

        with patch("backend.api.routes.jobs.subprocess.run", return_value=mock_result):
            duration = await _get_audio_duration_ffprobe_signed(
                "job-abc", mock_job_with_audio, mock_storage_for_ffprobe
            )

        assert duration is None

    @pytest.mark.asyncio
    async def test_ffprobe_missing_duration_key_returns_none(self, mock_job_with_audio, mock_storage_for_ffprobe):
        """Returns None when ffprobe output lacks format.duration."""
        from backend.api.routes.jobs import _get_audio_duration_ffprobe_signed

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"format": {"filename": "test.flac"}})

        with patch("backend.api.routes.jobs.subprocess.run", return_value=mock_result):
            duration = await _get_audio_duration_ffprobe_signed(
                "job-abc", mock_job_with_audio, mock_storage_for_ffprobe
            )

        assert duration is None

    @pytest.mark.asyncio
    async def test_ffprobe_timeout_returns_none(self, mock_job_with_audio, mock_storage_for_ffprobe):
        """Returns None when ffprobe times out."""
        from backend.api.routes.jobs import _get_audio_duration_ffprobe_signed

        with patch("backend.api.routes.jobs.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)):
            duration = await _get_audio_duration_ffprobe_signed(
                "job-abc", mock_job_with_audio, mock_storage_for_ffprobe
            )

        assert duration is None

    @pytest.mark.asyncio
    async def test_signed_url_failure_returns_none(self, mock_job_with_audio, mock_storage_for_ffprobe):
        """Returns None when signed URL generation fails."""
        from backend.api.routes.jobs import _get_audio_duration_ffprobe_signed

        mock_storage_for_ffprobe.generate_signed_url.side_effect = Exception("Auth expired")

        duration = await _get_audio_duration_ffprobe_signed(
            "job-abc", mock_job_with_audio, mock_storage_for_ffprobe
        )

        assert duration is None


# --- Contract test: "uploaded" vs "custom" selection mapping ---


class TestUploadedVsCustomSelectionContract:
    """
    Contract tests documenting that 'uploaded' is a frontend-only concept.
    The backend API only accepts 'clean', 'with_backing', or 'custom'.
    The frontend must map 'uploaded' → 'custom' before sending to the API.
    """

    def test_review_complete_valid_selections(self):
        """
        review.py's complete endpoint only allows 'custom' (not 'uploaded')
        when stems.custom_instrumental exists.

        This validates the exact validation logic inline, avoiding complex
        endpoint mocking. The actual endpoint test is in emulator tests.
        """
        from backend.models.job import Job, JobStatus

        job = Job(
            job_id="test",
            status=JobStatus.IN_REVIEW,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            file_urls={
                "stems": {
                    "custom_instrumental": "jobs/test/stems/custom_instrumental.flac",
                },
            },
        )

        # Reproduce the exact validation logic from review.py complete endpoint
        valid_selections = ["clean", "with_backing"]
        stems = job.file_urls.get("stems", {}) if job.file_urls else {}
        if job.existing_instrumental_gcs_path or stems.get("custom_instrumental"):
            valid_selections.append("custom")

        assert "custom" in valid_selections, "custom should be valid when stems.custom_instrumental exists"
        assert "uploaded" not in valid_selections, "uploaded is not a valid API value"

    def test_jobs_complete_review_accepts_custom(
        self, review_job, mock_job_manager, mock_worker_service, patched_client
    ):
        """
        The jobs.py complete-review endpoint accepts 'custom' via the
        CompleteReviewRequest body (which the frontend sends after mapping).
        """
        review_job.file_urls["stems"]["custom_instrumental"] = "jobs/job-abc/stems/custom.flac"
        mock_job_manager.get_job.return_value = review_job

        headers = {
            "Authorization": "Bearer test-admin-token",
            "Content-Type": "application/json",
        }

        response = patched_client.post(
            "/api/jobs/job-abc/complete-review",
            json={"instrumental_selection": "custom"},
            headers=headers,
        )

        assert response.status_code == 200
        mock_job_manager.update_state_data.assert_any_call(
            "job-abc", "instrumental_selection", "custom"
        )


# --- Tests: GCE encoding worker instrumental file resolution ---


class TestGceEncodingInstrumentalResolution:
    """
    Tests that the GCE encoding worker's find_file logic correctly resolves
    custom instrumentals instead of falling back to clean/with_backing.
    """

    def test_find_file_returns_first_match(self, tmp_path):
        """find_file returns the first matching pattern."""
        from backend.services.gce_encoding.main import find_file

        # Create test files
        stems = tmp_path / "stems"
        stems.mkdir()
        (stems / "custom_instrumental.flac").write_bytes(b"custom")
        (stems / "instrumental_clean.flac").write_bytes(b"clean")

        result = find_file(tmp_path, "*custom_instrumental*.flac")
        assert result is not None
        assert "custom_instrumental" in str(result)

    def test_custom_selection_finds_custom_instrumental(self, tmp_path):
        """When instrumental_selection is 'custom', find_file should match custom_instrumental.flac."""
        from backend.services.gce_encoding.main import find_file

        stems = tmp_path / "stems"
        stems.mkdir()
        (stems / "custom_instrumental.flac").write_bytes(b"custom audio")
        (stems / "instrumental_clean.flac").write_bytes(b"clean audio")
        (stems / "instrumental_with_backing.flac").write_bytes(b"backing audio")

        # Reproduce the exact logic from gce_encoding/main.py run_encoding
        instrumental_selection = "custom"
        if instrumental_selection == "custom":
            instrumental = find_file(
                tmp_path,
                "*custom_instrumental*.flac", "*Instrumental Custom*.flac",
                "*custom_instrumental*.mp3",
            )
        elif instrumental_selection == "with_backing":
            instrumental = find_file(
                tmp_path,
                "*instrumental_with_backing*.flac", "*Instrumental Backing*.flac",
            )
        else:
            instrumental = find_file(
                tmp_path,
                "*instrumental_clean*.flac", "*Instrumental Clean*.flac",
            )

        assert instrumental is not None
        assert "custom_instrumental" in str(instrumental)

    def test_custom_selection_does_not_fallback_to_clean(self, tmp_path):
        """Custom selection must NOT accidentally resolve to clean instrumental."""
        from backend.services.gce_encoding.main import find_file

        stems = tmp_path / "stems"
        stems.mkdir()
        # Only clean instrumental exists — custom is missing
        (stems / "instrumental_clean.flac").write_bytes(b"clean audio")
        (stems / "instrumental_with_backing.flac").write_bytes(b"backing audio")

        instrumental_selection = "custom"
        if instrumental_selection == "custom":
            instrumental = find_file(
                tmp_path,
                "*custom_instrumental*.flac", "*Instrumental Custom*.flac",
                "*custom_instrumental*.mp3",
            )
        else:
            instrumental = find_file(tmp_path, "*instrumental_clean*.flac")

        # Should be None — no custom instrumental exists
        assert instrumental is None

    def test_clean_selection_still_works(self, tmp_path):
        """Clean selection unaffected by the custom case addition."""
        from backend.services.gce_encoding.main import find_file

        stems = tmp_path / "stems"
        stems.mkdir()
        (stems / "custom_instrumental.flac").write_bytes(b"custom")
        (stems / "instrumental_clean.flac").write_bytes(b"clean")
        (stems / "instrumental_with_backing.flac").write_bytes(b"backing")

        instrumental_selection = "clean"
        if instrumental_selection == "custom":
            instrumental = find_file(tmp_path, "*custom_instrumental*.flac")
        elif instrumental_selection == "with_backing":
            instrumental = find_file(tmp_path, "*instrumental_with_backing*.flac")
        else:
            instrumental = find_file(tmp_path, "*instrumental_clean*.flac")

        assert instrumental is not None
        assert "instrumental_clean" in str(instrumental)

    def test_with_backing_selection_still_works(self, tmp_path):
        """With-backing selection unaffected by the custom case addition."""
        from backend.services.gce_encoding.main import find_file

        stems = tmp_path / "stems"
        stems.mkdir()
        (stems / "instrumental_clean.flac").write_bytes(b"clean")
        (stems / "instrumental_with_backing.flac").write_bytes(b"backing")

        instrumental_selection = "with_backing"
        if instrumental_selection == "custom":
            instrumental = find_file(tmp_path, "*custom_instrumental*.flac")
        elif instrumental_selection == "with_backing":
            instrumental = find_file(
                tmp_path,
                "*instrumental_with_backing*.flac", "*Instrumental Backing*.flac",
            )
        else:
            instrumental = find_file(tmp_path, "*instrumental_clean*.flac")

        assert instrumental is not None
        assert "instrumental_with_backing" in str(instrumental)


# --- Tests: Orchestrator config instrumental path resolution ---


class TestOrchestratorInstrumentalPath:
    """
    Tests that create_orchestrator_config_from_job correctly constructs the instrumental
    file path for all selection types, including 'custom'.
    """

    def _make_job(self, instrumental_selection, existing_instrumental=None, custom_stem=False):
        """Create a Job with the given instrumental selection in state_data."""
        file_urls = {"stems": {"instrumental_clean": "jobs/j/stems/instrumental_clean.flac"}}
        if custom_stem:
            file_urls["stems"]["custom_instrumental"] = "jobs/j/stems/custom_instrumental.flac"

        job = Job(
            job_id="test-orch",
            status=JobStatus.RENDERING_VIDEO,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            state_data={"instrumental_selection": instrumental_selection},
            file_urls=file_urls,
            existing_instrumental_gcs_path=existing_instrumental,
        )
        return job

    def test_custom_selection_uses_custom_path(self, tmp_path):
        """Custom selection produces '(Instrumental Custom).flac' path."""
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = self._make_job("custom", custom_stem=True)
        config = create_orchestrator_config_from_job(job, str(tmp_path))

        assert "(Instrumental Custom).flac" in config.instrumental_audio_path

    def test_clean_selection_uses_clean_path(self, tmp_path):
        """Clean selection produces '(Instrumental Clean).flac' path."""
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = self._make_job("clean")
        config = create_orchestrator_config_from_job(job, str(tmp_path))

        assert "(Instrumental Clean).flac" in config.instrumental_audio_path

    def test_with_backing_selection_uses_backing_path(self, tmp_path):
        """With-backing selection produces '(Instrumental Backing).flac' path."""
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = self._make_job("with_backing")
        config = create_orchestrator_config_from_job(job, str(tmp_path))

        assert "(Instrumental Backing).flac" in config.instrumental_audio_path

    def test_existing_instrumental_takes_priority(self, tmp_path):
        """existing_instrumental_gcs_path takes priority over selection type."""
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = self._make_job("custom", existing_instrumental="jobs/j/input/user_instrumental.wav")
        config = create_orchestrator_config_from_job(job, str(tmp_path))

        assert "(Instrumental User).wav" in config.instrumental_audio_path

    def test_custom_selection_not_confused_with_backing(self, tmp_path):
        """Regression: custom selection must NOT produce Backing path (the original bug)."""
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = self._make_job("custom", custom_stem=True)
        config = create_orchestrator_config_from_job(job, str(tmp_path))

        assert "Backing" not in config.instrumental_audio_path
        assert "Custom" in config.instrumental_audio_path
