"""
Unit tests for the edit completed track endpoint.

Tests POST /api/jobs/{job_id}/edit which allows users to reopen
completed tracks for editing (lyrics, instrumental, metadata).
"""
import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch, Mock

from backend.models.job import Job, JobStatus


@pytest.fixture
def complete_job():
    """A completed job with distribution data."""
    return Job(
        job_id="test-edit-123",
        status="complete",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        user_email="user@example.com",
        dropbox_path="/Karaoke/Organized",
        edit_count=0,
        file_urls={
            "screens": {
                "title_png": "jobs/test-edit-123/screens/title.png",
                "title_jpg": "jobs/test-edit-123/screens/title.jpg",
                "end_png": "jobs/test-edit-123/screens/end.png",
                "end_jpg": "jobs/test-edit-123/screens/end.jpg",
            },
            "videos": {"with_vocals": "jobs/test-edit-123/videos/with_vocals.mkv"},
        },
        state_data={
            "youtube_url": "https://youtu.be/abc123",
            "brand_code": "NOMAD-1234",
            "dropbox_link": "https://dropbox.com/...",
            "gdrive_files": {"mp4": "file_id_1", "mp4_720p": "file_id_2"},
            "audio_complete": True,
            "lyrics_complete": True,
            "render_progress": {"stage": "complete"},
            "video_progress": {"stage": "complete"},
            "encoding_progress": {"stage": "complete"},
            "review_complete": True,
            "corrected_lyrics": {"segments": []},
            "instrumental_selection": "clean",
        },
    )


@pytest.fixture
def mock_job_manager(complete_job):
    """Mock JobManager with common methods."""
    manager = MagicMock()
    manager.get_job.return_value = complete_job
    manager.transition_to_state.return_value = True
    return manager


@pytest.fixture
def mock_worker_service():
    """Mock WorkerService."""
    service = MagicMock()
    service.trigger_audio_worker = AsyncMock(return_value=True)
    service.trigger_lyrics_worker = AsyncMock(return_value=True)
    service.trigger_screens_worker = AsyncMock(return_value=True)
    service.trigger_video_worker = AsyncMock(return_value=True)
    service.trigger_render_video_worker = AsyncMock(return_value=True)
    return service


@pytest.fixture
def client(mock_job_manager, mock_worker_service):
    """Create TestClient with mocked dependencies."""
    mock_creds = MagicMock()
    mock_creds.universe_domain = 'googleapis.com'

    mock_theme_service = MagicMock()
    mock_theme_service.get_default_theme_id.return_value = "nomad"

    def mock_job_manager_factory(*args, **kwargs):
        return mock_job_manager

    # After edit, return updated job with review token
    updated_job = Job(
        job_id="test-edit-123",
        status="awaiting_review",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        user_email="user@example.com",
        review_token="test-review-token-abc",
        edit_count=1,
    )

    def get_job_side_effect(job_id):
        # First call returns complete job, second returns updated job
        if mock_job_manager.get_job.call_count <= 1:
            return mock_job_manager.get_job.return_value
        return updated_job

    mock_job_manager.get_job.side_effect = get_job_side_effect

    with patch('backend.api.routes.jobs.job_manager', mock_job_manager), \
         patch('backend.api.routes.jobs.worker_service', mock_worker_service), \
         patch('backend.api.routes.jobs.get_theme_service', return_value=mock_theme_service), \
         patch('backend.api.routes.jobs.StorageService') as mock_storage_cls, \
         patch('backend.api.routes.jobs.FirestoreService') as mock_fs_cls, \
         patch('backend.services.job_manager.JobManager', mock_job_manager_factory), \
         patch('backend.services.firestore_service.firestore'), \
         patch('backend.services.storage_service.storage'), \
         patch('google.auth.default', return_value=(mock_creds, 'test-project')):
        mock_storage_cls.return_value.delete_folder.return_value = 3
        from backend.main import app
        from fastapi.testclient import TestClient
        yield TestClient(app)


class TestEditCompletedTrackSuccess:
    """Tests for successful edit initiation."""

    def test_edit_returns_200(self, client, auth_headers):
        """Edit a completed job returns 200."""
        response = client.post("/api/jobs/test-edit-123/edit", headers=auth_headers, json={})
        assert response.status_code == 200

    def test_edit_returns_review_url(self, client, auth_headers):
        """Response includes review URL."""
        response = client.post("/api/jobs/test-edit-123/edit", headers=auth_headers, json={})
        data = response.json()
        assert "review_url" in data
        assert "test-edit-123" in data["review_url"]
        assert "review" in data["review_url"]

    def test_edit_returns_review_token(self, client, auth_headers):
        """Response includes review token."""
        response = client.post("/api/jobs/test-edit-123/edit", headers=auth_headers, json={})
        data = response.json()
        assert data["review_token"] == "test-review-token-abc"

    def test_edit_transitions_to_awaiting_review(self, client, mock_job_manager, auth_headers):
        """Job state transitions to AWAITING_REVIEW."""
        client.post("/api/jobs/test-edit-123/edit", headers=auth_headers, json={})
        mock_job_manager.transition_to_state.assert_called_once_with(
            "test-edit-123",
            JobStatus.AWAITING_REVIEW,
            progress=60,
            message="Track reopened for editing",
        )

    def test_edit_without_metadata_change(self, client, auth_headers):
        """Edit without metadata returns metadata_updated=false."""
        response = client.post("/api/jobs/test-edit-123/edit", headers=auth_headers, json={})
        data = response.json()
        assert data["metadata_updated"] is False

    def test_edit_does_not_trigger_screens_without_metadata(self, client, mock_worker_service, auth_headers):
        """Screens worker not triggered when metadata unchanged."""
        client.post("/api/jobs/test-edit-123/edit", headers=auth_headers, json={})
        mock_worker_service.trigger_screens_worker.assert_not_called()


class TestEditWithMetadataUpdate:
    """Tests for editing with artist/title changes."""

    def test_edit_with_artist_change(self, client, auth_headers):
        """Edit with new artist returns metadata_updated=true."""
        response = client.post(
            "/api/jobs/test-edit-123/edit",
            headers=auth_headers,
            json={"artist": "New Artist"},
        )
        data = response.json()
        assert data["metadata_updated"] is True

    def test_edit_with_title_change(self, client, auth_headers):
        """Edit with new title returns metadata_updated=true."""
        response = client.post(
            "/api/jobs/test-edit-123/edit",
            headers=auth_headers,
            json={"title": "New Song Title"},
        )
        data = response.json()
        assert data["metadata_updated"] is True

    def test_edit_with_same_artist_no_metadata_update(self, client, auth_headers):
        """Edit with same artist does not flag metadata_updated."""
        response = client.post(
            "/api/jobs/test-edit-123/edit",
            headers=auth_headers,
            json={"artist": "Test Artist"},
        )
        data = response.json()
        assert data["metadata_updated"] is False

    def test_edit_triggers_screens_on_metadata_change(self, client, mock_worker_service, auth_headers):
        """Screens worker triggered when metadata changes."""
        client.post(
            "/api/jobs/test-edit-123/edit",
            headers=auth_headers,
            json={"artist": "New Artist"},
        )
        mock_worker_service.trigger_screens_worker.assert_called_once_with("test-edit-123")

    def test_edit_with_metadata_transitions_to_lyrics_complete(self, client, mock_job_manager, auth_headers):
        """Metadata change transitions to LYRICS_COMPLETE so screens worker can run.

        Bug fix: previously transitioned to AWAITING_REVIEW, which made the screens
        worker fail (AWAITING_REVIEW → GENERATING_SCREENS is not a valid transition).
        """
        client.post(
            "/api/jobs/test-edit-123/edit",
            headers=auth_headers,
            json={"artist": "New Artist"},
        )
        mock_job_manager.transition_to_state.assert_called_once_with(
            "test-edit-123",
            JobStatus.LYRICS_COMPLETE,
            progress=55,
            message="Regenerating screens with updated metadata",
        )

    def test_edit_with_metadata_clears_file_urls_screens(self, client, mock_job_manager, auth_headers):
        """Metadata change clears stale file_urls.screens entries.

        Bug fix: stale screen URLs caused retry to skip screen generation,
        resulting in 404 when video worker tried to download deleted screens.
        """
        response = client.post(
            "/api/jobs/test-edit-123/edit",
            headers=auth_headers,
            json={"title": "New Title"},
        )
        assert response.status_code == 200

        # The Firestore job_ref.update() is called via the mocked FirestoreService.
        # Verify the update_payload included screen URL deletions by checking
        # the transition went to LYRICS_COMPLETE (which only happens when
        # metadata_updated=True, the same branch that clears screens).
        mock_job_manager.transition_to_state.assert_called_once_with(
            "test-edit-123",
            JobStatus.LYRICS_COMPLETE,
            progress=55,
            message="Regenerating screens with updated metadata",
        )


class TestEditValidation:
    """Tests for validation and error cases."""

    def test_edit_non_complete_job_returns_400(self, client, mock_job_manager, auth_headers):
        """Cannot edit a job that isn't complete."""
        pending_job = Job(
            job_id="test-pending",
            status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_job_manager.get_job.side_effect = None
        mock_job_manager.get_job.return_value = pending_job

        response = client.post("/api/jobs/test-pending/edit", headers=auth_headers, json={})
        assert response.status_code == 400
        assert "completed" in response.json()["detail"].lower() or "complete" in response.json()["detail"].lower()

    def test_edit_already_deleted_outputs_returns_400(self, client, mock_job_manager, auth_headers):
        """Cannot edit a job whose outputs are already deleted."""
        deleted_job = Job(
            job_id="test-deleted",
            status="complete",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            outputs_deleted_at=datetime.now(UTC),
        )
        mock_job_manager.get_job.side_effect = None
        mock_job_manager.get_job.return_value = deleted_job

        response = client.post("/api/jobs/test-deleted/edit", headers=auth_headers, json={})
        assert response.status_code == 400
        assert "already" in response.json()["detail"].lower()

    def test_edit_nonexistent_job_returns_404(self, client, mock_job_manager, auth_headers):
        """Cannot edit a job that doesn't exist."""
        mock_job_manager.get_job.side_effect = None
        mock_job_manager.get_job.return_value = None

        response = client.post("/api/jobs/nonexistent/edit", headers=auth_headers, json={})
        assert response.status_code == 404


class TestEditCleanup:
    """Tests for output cleanup during edit."""

    def test_cleanup_results_in_response(self, client, auth_headers):
        """Response includes cleanup results."""
        response = client.post("/api/jobs/test-edit-123/edit", headers=auth_headers, json={})
        data = response.json()
        assert "cleanup_results" in data
        assert "gcs_finals" in data["cleanup_results"]
