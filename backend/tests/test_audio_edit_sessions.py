"""
Tests for audio edit session persistence (Phase 2).

Tests the model, API endpoints, and deduplication logic.
"""

import hashlib
import json
import pytest
from datetime import datetime, UTC, timezone
from unittest.mock import Mock, patch, MagicMock
from backend.models.audio_edit_session import AudioEditSession, AudioEditSessionSummary
from backend.models.job import Job, JobStatus

NOW = datetime.now(UTC)
PATCH_JM = "backend.api.routes.review.JobManager"
PATCH_FS = "backend.api.routes.review.FirestoreService"
PATCH_STORAGE = "backend.api.routes.review.StorageService"


def _job(**kwargs):
    defaults = dict(created_at=NOW, updated_at=NOW)
    defaults.update(kwargs)
    return Job(**defaults)


# --- Model Tests ---

class TestAudioEditSessionModel:

    def test_to_dict_and_from_dict_roundtrip(self):
        summary = AudioEditSessionSummary(
            total_operations=3,
            operations_breakdown={"trim_start": 1, "mute": 2},
            duration_change_seconds=-15.5,
            net_duration_seconds=184.5,
        )
        session = AudioEditSession(
            session_id="test-id",
            job_id="job-1",
            user_email="user@example.com",
            edit_count=3,
            trigger="manual",
            audio_duration_seconds=184.5,
            original_duration_seconds=200.0,
            artist="Test Artist",
            title="Test Song",
            summary=summary,
            edit_data_gcs_path="jobs/job-1/audio_edit_sessions/test-id.json",
            data_hash="abc123",
        )

        d = session.to_dict()
        restored = AudioEditSession.from_dict(d)

        assert restored.session_id == "test-id"
        assert restored.job_id == "job-1"
        assert restored.edit_count == 3
        assert restored.trigger == "manual"
        assert restored.audio_duration_seconds == 184.5
        assert restored.original_duration_seconds == 200.0
        assert restored.artist == "Test Artist"
        assert restored.summary.total_operations == 3
        assert restored.summary.operations_breakdown == {"trim_start": 1, "mute": 2}
        assert restored.data_hash == "abc123"

    def test_from_dict_with_string_datetimes(self):
        data = {
            "session_id": "sid",
            "created_at": "2026-03-08T12:00:00",
            "updated_at": "2026-03-08T12:30:00",
        }
        session = AudioEditSession.from_dict(data)
        assert session.created_at.year == 2026
        assert session.updated_at.minute == 30

    def test_from_dict_defaults(self):
        session = AudioEditSession.from_dict({})
        assert session.edit_count == 0
        assert session.trigger == "auto"
        assert session.summary.total_operations == 0

    def test_summary_to_dict_and_from_dict(self):
        s = AudioEditSessionSummary(total_operations=5, operations_breakdown={"cut": 5})
        d = s.to_dict()
        restored = AudioEditSessionSummary.from_dict(d)
        assert restored.total_operations == 5
        assert restored.operations_breakdown == {"cut": 5}


# --- API Endpoint Tests ---

@pytest.fixture(autouse=True)
def _ensure_auth_overrides():
    """Ensure auth overrides are set for every test."""
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
def mock_firestore_svc():
    with patch(PATCH_FS) as MockFS:
        fs = Mock()
        MockFS.return_value = fs
        yield fs


@pytest.fixture
def mock_storage_svc():
    with patch(PATCH_STORAGE) as MockS:
        s = Mock()
        MockS.return_value = s
        yield s


class TestSaveAudioEditSession:

    def test_save_new_session(self, test_client, mock_job_manager, mock_firestore_svc, mock_storage_svc):
        job = _job(job_id="j1", status=JobStatus.IN_AUDIO_EDIT,
                   artist="Artist", title="Song", state_data={})
        mock_job_manager.get_job.return_value = job
        mock_firestore_svc.get_latest_audio_edit_session_hash.return_value = None

        edit_data = {"entries": [{"operation": "trim_start", "params": {"end_seconds": 30}}]}
        resp = test_client.post("/api/review/j1/audio-edit-sessions", json={
            "edit_data": edit_data,
            "edit_count": 1,
            "trigger": "auto",
            "summary": {
                "total_operations": 1,
                "operations_breakdown": {"trim_start": 1},
                "duration_change_seconds": -30.0,
                "net_duration_seconds": 170.0,
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "saved"
        assert "session_id" in data

        # Should upload to GCS and save to Firestore
        mock_storage_svc.upload_json.assert_called_once()
        mock_firestore_svc.save_audio_edit_session.assert_called_once()

    def test_skip_duplicate_session(self, test_client, mock_job_manager, mock_firestore_svc):
        job = _job(job_id="j2", status=JobStatus.IN_AUDIO_EDIT, state_data={})
        mock_job_manager.get_job.return_value = job

        edit_data = {"entries": []}
        data_json = json.dumps(edit_data, sort_keys=True, default=str)
        existing_hash = hashlib.sha256(data_json.encode()).hexdigest()
        mock_firestore_svc.get_latest_audio_edit_session_hash.return_value = existing_hash

        resp = test_client.post("/api/review/j2/audio-edit-sessions", json={
            "edit_data": edit_data,
            "edit_count": 0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert data["reason"] == "identical_data"

    def test_save_job_not_found(self, test_client, mock_job_manager):
        mock_job_manager.get_job.return_value = None
        resp = test_client.post("/api/review/missing/audio-edit-sessions", json={
            "edit_data": {},
        })
        assert resp.status_code == 404


class TestListAudioEditSessions:

    def test_list_sessions(self, test_client, mock_firestore_svc):
        session = AudioEditSession(
            session_id="s1", job_id="j1", edit_count=2,
            summary=AudioEditSessionSummary(total_operations=2),
        )
        mock_firestore_svc.list_audio_edit_sessions.return_value = [session]

        resp = test_client.get("/api/review/j1/audio-edit-sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "s1"

    def test_list_empty(self, test_client, mock_firestore_svc):
        mock_firestore_svc.list_audio_edit_sessions.return_value = []
        resp = test_client.get("/api/review/j1/audio-edit-sessions")
        assert resp.status_code == 200
        assert len(resp.json()["sessions"]) == 0


class TestGetAudioEditSession:

    def test_get_with_gcs_data(self, test_client, mock_firestore_svc, mock_storage_svc):
        session = AudioEditSession(
            session_id="s1", job_id="j1",
            edit_data_gcs_path="jobs/j1/audio_edit_sessions/s1.json",
        )
        mock_firestore_svc.get_audio_edit_session.return_value = session
        mock_storage_svc.download_json.return_value = {"entries": [{"op": "trim"}]}

        resp = test_client.get("/api/review/j1/audio-edit-sessions/s1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "s1"
        assert data["edit_data"] == {"entries": [{"op": "trim"}]}

    def test_get_not_found(self, test_client, mock_firestore_svc):
        mock_firestore_svc.get_audio_edit_session.return_value = None
        resp = test_client.get("/api/review/j1/audio-edit-sessions/missing")
        assert resp.status_code == 404


class TestDeleteAudioEditSession:

    def test_delete_session(self, test_client, mock_firestore_svc, mock_storage_svc):
        session = AudioEditSession(
            session_id="s1", job_id="j1",
            edit_data_gcs_path="jobs/j1/audio_edit_sessions/s1.json",
        )
        mock_firestore_svc.get_audio_edit_session.return_value = session

        resp = test_client.delete("/api/review/j1/audio-edit-sessions/s1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        mock_storage_svc.delete_file.assert_called_once_with("jobs/j1/audio_edit_sessions/s1.json")
        mock_firestore_svc.delete_audio_edit_session.assert_called_once_with("j1", "s1")

    def test_delete_not_found(self, test_client, mock_firestore_svc):
        mock_firestore_svc.get_audio_edit_session.return_value = None
        resp = test_client.delete("/api/review/j1/audio-edit-sessions/missing")
        assert resp.status_code == 404
