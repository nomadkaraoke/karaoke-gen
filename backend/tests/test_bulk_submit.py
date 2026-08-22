"""Tests for POST /api/bulk/submit — upfront credit gate + batch job creation."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.dependencies import require_auth, AuthResult, UserType
from backend.exceptions import InsufficientCreditsError


def _auth(is_admin=False, email="t@example.com"):
    def _dep():
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN if is_admin else UserType.STRIPE,
            remaining_uses=999,
            message="ok",
            user_email=email,
            is_admin=is_admin,
        )
    return _dep


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch):
    """Patch the heavy collaborators used by bulk_submit."""
    import backend.api.routes.bulk as bulk

    # Theme + settings + cdg/txt
    theme = MagicMock()
    theme.get_default_theme_id.return_value = "nomad"
    monkeypatch.setattr("backend.services.theme_service.get_theme_service", lambda: theme)
    monkeypatch.setattr("backend.services.job_defaults_service.resolve_cdg_txt_defaults", lambda *a, **k: (False, False))
    settings = MagicMock(
        default_brand_prefix="NOMAD", default_enable_youtube_upload=False,
        default_youtube_description="", default_discord_webhook_url=None,
        default_dropbox_path=None, default_gdrive_folder_id=None,
    )
    monkeypatch.setattr("backend.config.get_settings", lambda: settings)

    # Worker trigger
    worker = MagicMock()
    async def _trigger(batch_id):
        return True
    worker.trigger_bulk_search_worker = _trigger
    monkeypatch.setattr("backend.services.worker_service.get_worker_service", lambda: worker)

    yield


def _patch_user_service(monkeypatch, credits):
    us = MagicMock()
    us.check_credits.return_value = credits
    monkeypatch.setattr("backend.services.user_service.get_user_service", lambda: us)
    return us


def _patch_job_manager(monkeypatch, fail_after=None):
    """JobManager whose create_job returns sequential ids, optionally failing after N."""
    created = {"count": 0}
    jm = MagicMock()

    def _create_job(job_create, is_admin=False):
        if fail_after is not None and created["count"] >= fail_after:
            raise InsufficientCreditsError(message="out", credits_available=0, credits_required=1)
        created["count"] += 1
        job = MagicMock()
        job.job_id = f"job{created['count']}"
        return job

    jm.create_job.side_effect = _create_job
    jm.update_job.return_value = None
    monkeypatch.setattr("backend.services.job_manager.JobManager", lambda: jm)
    return jm


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def test_happy_path_creates_jobs_and_dispatches(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth()
    _patch_user_service(monkeypatch, credits=10)
    jm = _patch_job_manager(monkeypatch)

    resp = client.post("/api/bulk/submit", json={
        "songs": [{"artist": "Daft Punk", "title": "One More Time"},
                  {"artist": "Daft Punk", "title": "Aerodynamic"}],
        "settings": {"auto_select_if_lossless": True, "is_private": False},
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 2
    assert len(data["job_ids"]) == 2
    assert jm.create_job.call_count == 2
    # batch_id + bulk settings stamped via update_job
    assert jm.update_job.call_count == 2
    _, kwargs_call = jm.update_job.call_args
    stamped = jm.update_job.call_args[0][1]
    assert "state_data.batch_id" in stamped
    assert stamped["state_data.bulk_pending_search"] is True


def test_insufficient_credits_creates_nothing(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth()
    _patch_user_service(monkeypatch, credits=1)
    jm = _patch_job_manager(monkeypatch)

    resp = client.post("/api/bulk/submit", json={
        "songs": [{"artist": "A", "title": "B"}, {"artist": "C", "title": "D"}],
    })
    assert resp.status_code == 402
    assert jm.create_job.call_count == 0


def test_admin_bypasses_credit_gate(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth(is_admin=True)
    us = _patch_user_service(monkeypatch, credits=0)
    jm = _patch_job_manager(monkeypatch)

    resp = client.post("/api/bulk/submit", json={"songs": [{"artist": "A", "title": "B"}]})
    assert resp.status_code == 200
    us.check_credits.assert_not_called()
    assert jm.create_job.call_count == 1


def test_rejects_over_100(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth()
    _patch_user_service(monkeypatch, credits=1000)
    _patch_job_manager(monkeypatch)
    songs = [{"artist": f"A{i}", "title": f"T{i}"} for i in range(101)]
    resp = client.post("/api/bulk/submit", json={"songs": songs})
    assert resp.status_code == 422


def test_partial_failure_returns_created_jobs(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth()
    _patch_user_service(monkeypatch, credits=3)  # gate passes for 3
    jm = _patch_job_manager(monkeypatch, fail_after=2)  # but only 2 succeed

    resp = client.post("/api/bulk/submit", json={
        "songs": [{"artist": "A", "title": "1"}, {"artist": "A", "title": "2"}, {"artist": "A", "title": "3"}],
    })
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
