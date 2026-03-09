"""
Tests for audio edit API endpoints in backend/api/routes/review.py.

Tests the 6 new endpoints: input-audio-info, apply, undo, redo, upload, submit.
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from backend.models.job import Job, JobStatus
from backend.services.audio_edit_service import AudioMetadata

NOW = datetime.now(UTC)

# Patch paths - these are imported inside endpoint functions
PATCH_JM = "backend.api.routes.review.JobManager"
PATCH_EDIT_SVC = "backend.services.audio_edit_service.AudioEditService"
PATCH_ANALYSIS_SVC = "backend.services.audio_analysis_service.AudioAnalysisService"
PATCH_TRANSCODE_SVC = "backend.services.audio_transcoding_service.AudioTranscodingService"
PATCH_STORAGE_SVC = "backend.api.routes.review.StorageService"
PATCH_WORKER_SVC = "backend.services.worker_service.get_worker_service"


def _job(**kwargs):
    """Create a Job with required fields filled in."""
    defaults = dict(created_at=NOW, updated_at=NOW)
    defaults.update(kwargs)
    return Job(**defaults)


# --- Fixtures ---

@pytest.fixture(autouse=True)
def _ensure_auth_overrides():
    """Ensure auth overrides are set for every test, even when conftest autouse is fragile."""
    from backend.api.dependencies import require_auth, require_admin, require_review_auth
    from backend.services.auth_service import UserType, AuthResult
    from backend.main import app

    async def mock_require_review_auth(job_id: str = "test123"):
        return (job_id, "full")

    async def mock_require_auth():
        return AuthResult(is_valid=True, user_type=UserType.ADMIN, remaining_uses=999,
                          message="Test", is_admin=True, user_email="test@example.com")

    app.dependency_overrides[require_review_auth] = mock_require_review_auth
    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_admin] = mock_require_auth
    yield
    app.dependency_overrides.pop(require_review_auth, None)
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def mock_job_manager():
    with patch(PATCH_JM) as MockJM:
        jm = Mock()
        MockJM.return_value = jm
        yield jm


@pytest.fixture
def mock_services():
    """Mock all service dependencies used by audio edit endpoints."""
    with patch(PATCH_EDIT_SVC) as MockES, \
         patch(PATCH_ANALYSIS_SVC) as MockAS, \
         patch(PATCH_TRANSCODE_SVC) as MockTS:
        es = Mock()
        MockES.return_value = es
        # Default metadata for duration_before lookups
        es.get_metadata_from_gcs.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )
        as_inst = Mock()
        MockAS.return_value = as_inst
        as_inst.get_waveform_data.return_value = ([0.1, 0.5, 0.3], 200.0)
        ts = Mock()
        MockTS.return_value = ts
        ts.get_review_audio_url.return_value = "https://example.com/audio.ogg"
        yield {"edit": es, "analysis": as_inst, "transcode": ts}


@pytest.fixture
def audio_edit_job():
    """Job in awaiting_audio_edit state with input media."""
    return _job(
        job_id="edit-job-1",
        status=JobStatus.AWAITING_AUDIO_EDIT,
        progress=15,
        input_media_gcs_path="jobs/edit-job-1/input/song.flac",
        state_data={},
    )


@pytest.fixture
def audio_edit_job_with_edits():
    """Job in in_audio_edit state with existing edit stack."""
    return _job(
        job_id="edit-job-2",
        status=JobStatus.IN_AUDIO_EDIT,
        progress=15,
        input_media_gcs_path="jobs/edit-job-2/input/song.flac",
        state_data={
            "audio_edit_stack": [
                {
                    "edit_id": "aaa",
                    "operation": "trim_start",
                    "params": {"end_seconds": 10.0},
                    "gcs_path": "jobs/edit-job-2/audio_edit/edit_aaa.flac",
                    "duration_seconds": 190.0,
                    "timestamp": "2026-03-08T12:00:00",
                }
            ],
            "audio_edit_redo_stack": [],
        },
    )


# --- GET /input-audio-info ---

class TestGetInputAudioInfo:

    def test_returns_audio_info(self, test_client, mock_job_manager, mock_services, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        mock_services["edit"].get_metadata_from_gcs.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        resp = test_client.get("/api/review/edit-job-1/input-audio-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "edit-job-1"
        assert data["original_duration_seconds"] > 0
        assert data["current_duration_seconds"] > 0
        assert data["original_audio_url"] == "https://example.com/audio.ogg"
        assert data["current_audio_url"] == "https://example.com/audio.ogg"
        assert data["edit_stack"] == []
        assert data["can_undo"] is False
        assert "waveform_data" in data
        assert "original_waveform_data" in data

    def test_returns_edited_audio_when_edits_exist(self, test_client, mock_job_manager, mock_services, audio_edit_job_with_edits):
        mock_job_manager.get_job.return_value = audio_edit_job_with_edits
        mock_services["edit"].get_metadata_from_gcs.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        resp = test_client.get("/api/review/edit-job-2/input-audio-info")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edit_stack"]) == 1
        assert data["can_undo"] is True
        assert data["edit_stack"][0]["edit_id"] == "aaa"
        assert data["edit_stack"][0]["operation"] == "trim_start"

    def test_404_when_job_not_found(self, test_client, mock_job_manager):
        mock_job_manager.get_job.return_value = None
        resp = test_client.get("/api/review/nonexistent/input-audio-info")
        assert resp.status_code == 404

    def test_404_when_no_input_audio(self, test_client, mock_job_manager):
        job = _job(job_id="no-audio", status=JobStatus.AWAITING_AUDIO_EDIT,
                   input_media_gcs_path=None, state_data={})
        mock_job_manager.get_job.return_value = job
        resp = test_client.get("/api/review/no-audio/input-audio-info")
        assert resp.status_code == 404

    def test_uses_cached_waveform_when_available(self, test_client, mock_job_manager, mock_services):
        """When waveform cache exists, should use it instead of on-demand generation."""
        job = _job(
            job_id="cached-job",
            status=JobStatus.AWAITING_AUDIO_EDIT,
            input_media_gcs_path="jobs/cached-job/input/song.flac",
            state_data={"audio_edit_waveform_cache_path": "jobs/cached-job/audio_edit/waveform_original.json"},
        )
        mock_job_manager.get_job.return_value = job
        mock_services["analysis"].load_cached_waveform.return_value = ([0.2, 0.6, 0.4], 180.0)
        mock_services["edit"].get_metadata_from_gcs.return_value = AudioMetadata(
            duration_seconds=180.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        resp = test_client.get("/api/review/cached-job/input-audio-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["original_duration_seconds"] == 180.0
        assert data["original_waveform_data"]["amplitudes"] == [0.2, 0.6, 0.4]
        # Should have used cache, not on-demand generation
        mock_services["analysis"].load_cached_waveform.assert_called_once_with(
            "jobs/cached-job/audio_edit/waveform_original.json"
        )
        mock_services["analysis"].get_waveform_data.assert_not_called()

    def test_falls_back_to_on_demand_when_cache_missing(self, test_client, mock_job_manager, mock_services):
        """When waveform cache path is set but cache doesn't exist, should fall back."""
        job = _job(
            job_id="no-cache-job",
            status=JobStatus.AWAITING_AUDIO_EDIT,
            input_media_gcs_path="jobs/no-cache-job/input/song.flac",
            state_data={"audio_edit_waveform_cache_path": "jobs/no-cache-job/audio_edit/waveform_original.json"},
        )
        mock_job_manager.get_job.return_value = job
        mock_services["analysis"].load_cached_waveform.return_value = None
        mock_services["edit"].get_metadata_from_gcs.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        resp = test_client.get("/api/review/no-cache-job/input-audio-info")
        assert resp.status_code == 200
        # Should have fallen back to on-demand
        mock_services["analysis"].get_waveform_data.assert_called_once()

    def test_on_demand_when_no_cache_path_in_state(self, test_client, mock_job_manager, mock_services, audio_edit_job):
        """Jobs without cache path in state_data should use on-demand generation."""
        mock_job_manager.get_job.return_value = audio_edit_job
        mock_services["edit"].get_metadata_from_gcs.return_value = AudioMetadata(
            duration_seconds=200.0, sample_rate=44100, channels=2,
            format="flac", file_size_bytes=30000000,
        )

        resp = test_client.get("/api/review/edit-job-1/input-audio-info")
        assert resp.status_code == 200
        # No cache path → should not attempt to load cache
        mock_services["analysis"].load_cached_waveform.assert_not_called()
        mock_services["analysis"].get_waveform_data.assert_called_once()


# --- POST /audio-edit/apply ---

class TestApplyAudioEdit:

    def test_apply_trim_start(self, test_client, mock_job_manager, mock_services, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        mock_services["edit"].apply_edit.return_value = (
            AudioMetadata(duration_seconds=170.0, sample_rate=44100, channels=2,
                          format="flac", file_size_bytes=25000000),
            "jobs/edit-job-1/audio_edit/edit_abc.flac",
        )

        resp = test_client.post("/api/review/edit-job-1/audio-edit/apply", json={
            "operation": "trim_start",
            "params": {"end_seconds": 30.0},
            "edit_id": "abc",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["edit_id"] == "abc"
        assert data["can_undo"] is True
        assert len(data["edit_stack"]) == 1
        assert data["edit_stack"][0]["operation"] == "trim_start"
        assert "current_audio_url" in data
        assert "waveform_data" in data
        assert "duration_after" in data

        # Should transition to IN_AUDIO_EDIT
        mock_job_manager.transition_to_state.assert_called_once()

    def test_apply_transitions_to_in_audio_edit(self, test_client, mock_job_manager, mock_services, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        mock_services["edit"].apply_edit.return_value = (
            AudioMetadata(duration_seconds=170.0, sample_rate=44100, channels=2,
                          format="flac", file_size_bytes=25000000),
            "jobs/edit-job-1/audio_edit/edit_abc.flac",
        )

        test_client.post("/api/review/edit-job-1/audio-edit/apply", json={
            "operation": "trim_start",
            "params": {"end_seconds": 30.0},
        })

        mock_job_manager.transition_to_state.assert_called_once_with(
            job_id="edit-job-1",
            new_status=JobStatus.IN_AUDIO_EDIT,
            progress=15,
            message="Audio editing in progress",
        )

    def test_apply_skips_transition_if_already_in_edit(self, test_client, mock_job_manager, mock_services, audio_edit_job_with_edits):
        mock_job_manager.get_job.return_value = audio_edit_job_with_edits
        mock_services["edit"].apply_edit.return_value = (
            AudioMetadata(duration_seconds=170.0, sample_rate=44100, channels=2,
                          format="flac", file_size_bytes=25000000),
            "jobs/edit-job-2/audio_edit/edit_xyz.flac",
        )

        test_client.post("/api/review/edit-job-2/audio-edit/apply", json={
            "operation": "mute",
            "params": {"start_seconds": 10.0, "end_seconds": 15.0},
        })

        mock_job_manager.transition_to_state.assert_not_called()

    def test_apply_missing_operation(self, test_client, mock_job_manager, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        resp = test_client.post("/api/review/edit-job-1/audio-edit/apply", json={
            "params": {"end_seconds": 30.0},
        })
        assert resp.status_code == 400
        assert "operation" in resp.json()["detail"].lower()

    def test_apply_invalid_operation(self, test_client, mock_job_manager, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        resp = test_client.post("/api/review/edit-job-1/audio-edit/apply", json={
            "operation": "reverse",
            "params": {},
        })
        assert resp.status_code == 400
        assert "Invalid operation" in resp.json()["detail"]

    def test_apply_wrong_status(self, test_client, mock_job_manager):
        job = _job(job_id="wrong-status", status=JobStatus.COMPLETE,
                   input_media_gcs_path="jobs/x/input/song.flac", state_data={})
        mock_job_manager.get_job.return_value = job
        resp = test_client.post("/api/review/wrong-status/audio-edit/apply", json={
            "operation": "trim_start",
            "params": {"end_seconds": 10.0},
        })
        assert resp.status_code == 400
        assert "not in audio edit state" in resp.json()["detail"]

    def test_apply_join_requires_upload_id(self, test_client, mock_job_manager, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        resp = test_client.post("/api/review/edit-job-1/audio-edit/apply", json={
            "operation": "join_end",
            "params": {},
        })
        assert resp.status_code == 400
        assert "upload_id" in resp.json()["detail"].lower()

    def test_apply_join_invalid_upload_id(self, test_client, mock_job_manager, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        resp = test_client.post("/api/review/edit-job-1/audio-edit/apply", json={
            "operation": "join_end",
            "params": {"upload_id": "nonexistent"},
        })
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_apply_clears_redo_stack(self, test_client, mock_job_manager, mock_services):
        """When applying a new edit, the redo stack should be cleared."""
        job = _job(
            job_id="redo-clear",
            status=JobStatus.IN_AUDIO_EDIT,
            input_media_gcs_path="jobs/redo-clear/input/song.flac",
            state_data={
                "audio_edit_stack": [],
                "audio_edit_redo_stack": [{"edit_id": "old", "gcs_path": "old.flac"}],
            },
        )
        mock_job_manager.get_job.return_value = job
        mock_services["edit"].apply_edit.return_value = (
            AudioMetadata(duration_seconds=180.0, sample_rate=44100, channels=2,
                          format="flac", file_size_bytes=25000000),
            "jobs/redo-clear/audio_edit/edit_new.flac",
        )

        resp = test_client.post("/api/review/redo-clear/audio-edit/apply", json={
            "operation": "mute",
            "params": {"start_seconds": 5.0, "end_seconds": 10.0},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["can_redo"] is False


# --- POST /audio-edit/undo ---

class TestUndoAudioEdit:

    def test_undo_pops_edit_stack(self, test_client, mock_job_manager, mock_services, audio_edit_job_with_edits):
        mock_job_manager.get_job.return_value = audio_edit_job_with_edits

        resp = test_client.post("/api/review/edit-job-2/audio-edit/undo")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edit_stack"]) == 0
        assert data["can_undo"] is False
        assert data["can_redo"] is True

        # Verify state_data was updated
        update_call = mock_job_manager.update_job.call_args[0]
        state_data = update_call[1]["state_data"]
        assert len(state_data["audio_edit_stack"]) == 0
        assert len(state_data["audio_edit_redo_stack"]) == 1

    def test_undo_nothing_to_undo(self, test_client, mock_job_manager, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job
        resp = test_client.post("/api/review/edit-job-1/audio-edit/undo")
        assert resp.status_code == 400
        assert "Nothing to undo" in resp.json()["detail"]

    def test_undo_wrong_status(self, test_client, mock_job_manager):
        job = _job(job_id="done", status=JobStatus.COMPLETE, state_data={})
        mock_job_manager.get_job.return_value = job
        resp = test_client.post("/api/review/done/audio-edit/undo")
        assert resp.status_code == 400


# --- POST /audio-edit/redo ---

class TestRedoAudioEdit:

    def test_redo_pops_redo_stack(self, test_client, mock_job_manager, mock_services):
        job = _job(
            job_id="redo-job",
            status=JobStatus.IN_AUDIO_EDIT,
            input_media_gcs_path="jobs/redo-job/input/song.flac",
            state_data={
                "audio_edit_stack": [],
                "audio_edit_redo_stack": [
                    {
                        "edit_id": "bbb",
                        "operation": "trim_end",
                        "params": {"end_seconds": 180.0},
                        "gcs_path": "jobs/redo-job/audio_edit/edit_bbb.flac",
                        "duration_seconds": 180.0,
                    }
                ],
            },
        )
        mock_job_manager.get_job.return_value = job

        resp = test_client.post("/api/review/redo-job/audio-edit/redo")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edit_stack"]) == 1
        assert data["can_undo"] is True
        assert data["can_redo"] is False

    def test_redo_nothing_to_redo(self, test_client, mock_job_manager, audio_edit_job_with_edits):
        mock_job_manager.get_job.return_value = audio_edit_job_with_edits
        resp = test_client.post("/api/review/edit-job-2/audio-edit/redo")
        assert resp.status_code == 400
        assert "Nothing to redo" in resp.json()["detail"]


# --- POST /audio-edit/submit ---

class TestSubmitAudioEdit:

    def test_submit_with_edits_copies_audio(self, test_client, mock_job_manager, audio_edit_job_with_edits):
        mock_job_manager.get_job.return_value = audio_edit_job_with_edits

        with patch(PATCH_STORAGE_SVC) as MockStorage, \
             patch(PATCH_WORKER_SVC) as mock_get_ws:
            mock_ws = AsyncMock()
            mock_get_ws.return_value = mock_ws

            resp = test_client.post("/api/review/edit-job-2/audio-edit/submit",
                                    json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["edits_applied"] == 1

            # Should copy edited audio to canonical path
            storage_inst = MockStorage.return_value
            storage_inst.download_file.assert_called_once()
            storage_inst.upload_file.assert_called_once()

            # Should update input_media_gcs_path
            update_call = mock_job_manager.update_job.call_args[0]
            assert update_call[1]["input_media_gcs_path"] == "jobs/edit-job-2/input/edited.flac"

            # Should transition to AUDIO_EDIT_COMPLETE
            mock_job_manager.transition_to_state.assert_called_once_with(
                job_id="edit-job-2",
                new_status=JobStatus.AUDIO_EDIT_COMPLETE,
                progress=18,
                message="Audio edit complete, starting processing",
            )

    def test_submit_without_edits_uses_original(self, test_client, mock_job_manager, audio_edit_job):
        mock_job_manager.get_job.return_value = audio_edit_job

        with patch(PATCH_STORAGE_SVC) as MockStorage, \
             patch(PATCH_WORKER_SVC) as mock_get_ws:
            mock_ws = AsyncMock()
            mock_get_ws.return_value = mock_ws

            resp = test_client.post("/api/review/edit-job-1/audio-edit/submit",
                                    json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["edits_applied"] == 0

            # Should NOT copy any files
            MockStorage.return_value.download_file.assert_not_called()

            # Should NOT update input_media_gcs_path
            mock_job_manager.update_job.assert_not_called()

    def test_submit_wrong_status(self, test_client, mock_job_manager):
        job = _job(job_id="done", status=JobStatus.COMPLETE, state_data={})
        mock_job_manager.get_job.return_value = job
        resp = test_client.post("/api/review/done/audio-edit/submit", json={})
        assert resp.status_code == 400

    def test_submit_job_not_found(self, test_client, mock_job_manager):
        mock_job_manager.get_job.return_value = None
        resp = test_client.post("/api/review/nonexistent/audio-edit/submit", json={})
        assert resp.status_code == 404


# --- Helper function tests ---

class TestHelperFunctions:

    def test_get_current_audio_gcs_path_no_edits(self):
        from backend.api.routes.review import _get_current_audio_gcs_path
        job = _job(
            job_id="test",
            status=JobStatus.AWAITING_AUDIO_EDIT,
            input_media_gcs_path="jobs/test/input/song.flac",
            state_data={},
        )
        assert _get_current_audio_gcs_path(job) == "jobs/test/input/song.flac"

    def test_get_current_audio_gcs_path_with_edits(self):
        from backend.api.routes.review import _get_current_audio_gcs_path
        job = _job(
            job_id="test",
            status=JobStatus.IN_AUDIO_EDIT,
            input_media_gcs_path="jobs/test/input/song.flac",
            state_data={
                "audio_edit_stack": [
                    {"gcs_path": "jobs/test/audio_edit/edit_1.flac"},
                    {"gcs_path": "jobs/test/audio_edit/edit_2.flac"},
                ],
            },
        )
        assert _get_current_audio_gcs_path(job) == "jobs/test/audio_edit/edit_2.flac"

    def test_get_current_audio_gcs_path_empty_state_data(self):
        from backend.api.routes.review import _get_current_audio_gcs_path
        job = _job(
            job_id="test",
            status=JobStatus.AWAITING_AUDIO_EDIT,
            input_media_gcs_path="jobs/test/input/song.flac",
            state_data={},
        )
        assert _get_current_audio_gcs_path(job) == "jobs/test/input/song.flac"
