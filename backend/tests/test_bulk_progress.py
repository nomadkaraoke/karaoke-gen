"""Tests for GET /api/bulk/{batch_id} progress summary."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.dependencies import require_auth, AuthResult, UserType
from backend.models.job import JobStatus


def _auth(is_admin=False, email="t@example.com"):
    def _dep():
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN if is_admin else UserType.STRIPE,
            remaining_uses=999, message="ok", user_email=email, is_admin=is_admin,
        )
    return _dep


def _job(job_id, status, email="t@example.com", pending=False, auto=False, artist="A", title="T"):
    j = MagicMock()
    j.job_id = job_id
    j.status = status
    j.user_email = email
    j.artist = artist
    j.title = title
    j.audio_search_artist = artist
    j.audio_search_title = title
    j.state_data = {"batch_id": "b1", "bulk_pending_search": pending, "bulk_auto_selected": auto}
    return j


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def test_progress_buckets(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth()
    jm = MagicMock()
    jm.list_jobs_by_batch_id.return_value = [
        _job("j1", JobStatus.AWAITING_AUDIO_SELECTION),
        _job("j2", JobStatus.DOWNLOADING_AUDIO, auto=True),
        _job("j3", JobStatus.COMPLETE, auto=True),
        _job("j4", JobStatus.SEARCHING_AUDIO, pending=True),
    ]
    monkeypatch.setattr("backend.services.job_manager.JobManager", lambda: jm)

    resp = client.get("/api/bulk/b1")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 4
    assert data["counts"]["awaiting_selection"] == 1
    assert data["counts"]["processing"] == 1
    assert data["counts"]["complete"] == 1
    assert data["counts"]["searching"] == 1
    j2 = next(j for j in data["jobs"] if j["job_id"] == "j2")
    assert j2["auto_selected"] is True


def test_other_users_batch_is_404(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth(email="me@example.com")
    jm = MagicMock()
    jm.list_jobs_by_batch_id.return_value = [_job("j1", JobStatus.COMPLETE, email="someone@else.com")]
    monkeypatch.setattr("backend.services.job_manager.JobManager", lambda: jm)

    resp = client.get("/api/bulk/b1")
    assert resp.status_code == 404


def test_admin_sees_any_batch(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth(is_admin=True, email="admin@example.com")
    jm = MagicMock()
    jm.list_jobs_by_batch_id.return_value = [_job("j1", JobStatus.COMPLETE, email="someone@else.com")]
    monkeypatch.setattr("backend.services.job_manager.JobManager", lambda: jm)

    resp = client.get("/api/bulk/b1")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
