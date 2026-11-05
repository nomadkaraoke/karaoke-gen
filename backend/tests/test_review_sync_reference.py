"""POST /api/review/{job_id}/sync-reference: admin-only "perfectly synced" mark that
puts a job's reviewed word timing in the reference set (full-auto v2)."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.dependencies import require_admin, require_auth
from backend.main import app
from backend.services.auth_service import AuthResult, UserType


def _admin():
    return AuthResult(is_valid=True, user_type=UserType.ADMIN, remaining_uses=999, message="ok",
                      is_admin=True, user_email="andrew@nomadkaraoke.com")


@pytest.fixture
def jm():
    m = MagicMock()
    m.get_job.return_value = MagicMock(
        job_id="j1", file_urls={"lyrics": {"corrections_updated": "jobs/j1/lyrics/corrections_updated.json"}})
    return m


@pytest.fixture
def client(jm):
    async def fake_admin():
        return _admin()

    saved = dict(app.dependency_overrides)
    app.dependency_overrides[require_admin] = fake_admin
    try:
        with patch("backend.api.routes.review.JobManager", return_value=jm):
            yield TestClient(app)
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(saved)


def test_admin_marks_job_verified(client, jm):
    r = client.post("/api/review/j1/sync-reference", json={"verified": True, "notes": "  spot-checked chorus  "})
    assert r.status_code == 200
    jid, key, record = jm.update_state_data.call_args.args
    assert (jid, key) == ("j1", "sync_reference")
    assert record["verified"] is True
    assert record["by"] == "andrew@nomadkaraoke.com"
    assert record["notes"] == "spot-checked chorus"
    assert record["at"]


def test_blank_notes_stored_as_none(client, jm):
    client.post("/api/review/j1/sync-reference", json={"verified": False, "notes": "   "})
    assert jm.update_state_data.call_args.args[2]["notes"] is None
    assert jm.update_state_data.call_args.args[2]["verified"] is False


def test_unknown_job_404(client, jm):
    jm.get_job.return_value = None
    r = client.post("/api/review/nope/sync-reference", json={"verified": True})
    assert r.status_code == 404
    jm.update_state_data.assert_not_called()


def test_missing_verified_is_422(client):
    assert client.post("/api/review/j1/sync-reference", json={}).status_code == 422


def test_non_admin_forbidden(jm):
    async def fake_user():
        return AuthResult(is_valid=True, user_type=UserType.UNLIMITED, remaining_uses=999, message="ok",
                          is_admin=False, user_email="someone@example.com")

    saved = dict(app.dependency_overrides)
    app.dependency_overrides.pop(require_admin, None)   # conftest stubs it to always-admin
    app.dependency_overrides[require_auth] = fake_user
    try:
        with patch("backend.api.routes.review.JobManager", return_value=jm):
            r = TestClient(app).post("/api/review/j1/sync-reference", json={"verified": True})
        assert r.status_code == 403
        jm.update_state_data.assert_not_called()
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(saved)


def test_verified_requires_saved_review(client, jm):
    jm.get_job.return_value = MagicMock(job_id="j1", file_urls={"lyrics": {}})
    with patch("backend.api.routes.review.StorageService") as st:
        st.return_value.file_exists.return_value = False
        r = client.post("/api/review/j1/sync-reference", json={"verified": True})
    assert r.status_code == 409
    jm.update_state_data.assert_not_called()
    # revoking is always allowed
    assert client.post("/api/review/j1/sync-reference", json={"verified": False}).status_code == 200


def test_verified_accepts_direct_path_file(client, jm):
    jm.get_job.return_value = MagicMock(job_id="j1", file_urls={})
    with patch("backend.api.routes.review.StorageService") as st:
        st.return_value.file_exists.return_value = True
        r = client.post("/api/review/j1/sync-reference", json={"verified": True})
    assert r.status_code == 200
    st.return_value.file_exists.assert_called_with("jobs/j1/lyrics/corrections_updated.json")


def test_reasons_stored_trimmed_and_capped(client, jm):
    reasons = ["timing off in places", "  lyrics errors ", ""] + [f"r{i}" for i in range(20)]
    client.post("/api/review/j1/sync-reference", json={"verified": False, "reasons": reasons})
    stored = jm.update_state_data.call_args.args[2]["reasons"]
    assert stored[:2] == ["timing off in places", "lyrics errors"]
    assert len(stored) == 10


# --- vocal waveform for old (stems-purged) jobs falls back to the backfilled lead stem ---

def _job(stems=None, purged=None):
    j = MagicMock()
    j.job_id = "j1"
    j.file_urls = {"stems": stems or {}}
    j.stems_purged_at = purged
    return j


def test_review_vocals_path_prefers_registered_stem():
    from backend.api.routes.review import _review_vocals_path
    assert _review_vocals_path(_job({"vocals_clean": "jobs/j1/stems/vocals_clean.flac"})) == "jobs/j1/stems/vocals_clean.flac"


def test_review_vocals_path_purged_job_uses_training_copy():
    from backend.api.routes.review import _review_vocals_path
    with patch("backend.api.routes.review.StorageService") as st:
        st.return_value.file_exists.return_value = True
        assert _review_vocals_path(_job(purged="2026-10-05")) == "training/lead_vocals/j1.flac"
        st.return_value.file_exists.return_value = False
        assert _review_vocals_path(_job(purged="2026-10-05")) is None


def test_review_vocals_path_unpurged_job_never_checks_training():
    from backend.api.routes.review import _review_vocals_path
    with patch("backend.api.routes.review.StorageService") as st:
        assert _review_vocals_path(_job()) is None
        st.assert_not_called()
