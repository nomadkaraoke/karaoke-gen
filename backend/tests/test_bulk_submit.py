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


def test_album_batch_stamps_album_and_track_positions(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth()
    _patch_user_service(monkeypatch, credits=10)
    jm = _patch_job_manager(monkeypatch)

    resp = client.post("/api/bulk/submit", json={
        "songs": [{"artist": "Daft Punk", "title": "One More Time", "position": 1},
                  {"artist": "Daft Punk", "title": "Aerodynamic", "position": 2}],
        "settings": {},
        "album": {"artist": "Daft Punk", "title": "Discovery", "release_group_mbid": "rg-1"},
    })
    assert resp.status_code == 200, resp.text
    stamps = [c[0][1] for c in jm.update_job.call_args_list]
    assert [s["state_data.bulk_track_position"] for s in stamps] == [1, 2]
    assert stamps[0]["state_data.bulk_album"] == {
        "artist": "Daft Punk", "title": "Discovery", "release_group_mbid": "rg-1", "release_mbid": None,
    }


def test_text_batch_has_no_album_state(client, monkeypatch):
    app.dependency_overrides[require_auth] = _auth()
    _patch_user_service(monkeypatch, credits=10)
    jm = _patch_job_manager(monkeypatch)
    client.post("/api/bulk/submit", json={"songs": [{"artist": "Daft Punk", "title": "One More Time"}]})
    stamped = jm.update_job.call_args[0][1]
    assert "state_data.bulk_album" not in stamped
    assert "state_data.bulk_track_position" not in stamped


@pytest.mark.asyncio
async def test_cancelled_request_still_dispatches_created_batch(monkeypatch):
    """A client disconnect mid-creation must not strand created (and charged) jobs:
    the create+dispatch step is shielded, so dispatch still runs after cancellation."""
    import asyncio
    import threading
    import backend.api.routes.bulk as bulk
    from starlette.requests import Request

    _patch_user_service(monkeypatch, credits=10)
    jm = _patch_job_manager(monkeypatch)
    started, release = threading.Event(), threading.Event()
    inner_create = jm.create_job.side_effect

    def _slow_create(job_create, is_admin=False):
        started.set()
        release.wait(5)
        return inner_create(job_create, is_admin=is_admin)

    jm.create_job.side_effect = _slow_create
    dispatched = []

    async def _trigger(batch_id):
        dispatched.append(batch_id)
        return True

    worker = MagicMock()
    worker.trigger_bulk_search_worker = _trigger
    monkeypatch.setattr("backend.services.worker_service.get_worker_service", lambda: worker)

    request = Request({"type": "http", "method": "POST", "path": "/api/bulk/submit", "headers": []})
    body = bulk.BulkSubmitRequest(songs=[{"artist": "A", "title": "B"}], settings={})
    route = asyncio.create_task(bulk.bulk_submit(request, body, _auth()()))
    await asyncio.to_thread(started.wait, 5)
    route.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await route
    for _ in range(100):  # let the shielded task finish
        if dispatched:
            break
        await asyncio.sleep(0.01)
    assert len(dispatched) == 1
