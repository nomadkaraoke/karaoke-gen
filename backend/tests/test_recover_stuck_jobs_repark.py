"""Tests for POST /api/internal/recover-stuck-jobs resilience paths.

Covers the three recovery behaviours the endpoint drives every 5 min:
  1. Stuck torrent downloads (RED/OPS) → parked into DOWNLOAD_PENDING_RETRY and
     re-tried for up to 24h (rare tracks / intermittent seeders / tracker blips).
  2. Parked downloads → re-triggered, or permanently failed after 24h.
  3. Orphaned renders stuck at RENDERING_VIDEO → re-parked into
     RENDER_PENDING_CAPACITY so the existing retry cron resumes them.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.dependencies import require_admin
from backend.main import app
from backend.models.job import Job, JobStatus
from backend.services.auth_service import AuthResult, UserType


@pytest.fixture
def client():
    async def fake_admin():
        return AuthResult(
            is_valid=True, user_type=UserType.ADMIN, remaining_uses=999,
            message="test admin", is_admin=True, user_email="test@example.com",
        )

    app.dependency_overrides[require_admin] = fake_admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _doc(job_id, status):
    doc = MagicMock()
    doc.id = job_id
    doc.to_dict.return_value = {"job_id": job_id, "status": status.value}
    return doc


def _job(job_id, status, *, minutes_stale=0, source_name=None, download_retry=None):
    sd = {}
    if download_retry is not None:
        sd["download_retry"] = download_retry
    return Job(
        job_id=job_id, artist="A", title="B", status=status,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_stale),
        state_data=sd, source_name=source_name, audio_source_type="audio_search",
        source_id="123",
    )


def _mock_jm(*, downloading=None, pending=None, rendering=None, jobs=None):
    """Wire a JobManager mock for the endpoint's 3 status queries.

    downloading/rendering use .where().stream(); pending uses .where().limit().stream().
    `jobs` maps job_id -> Job returned by get_job().
    """
    mock_jm = MagicMock()
    where = mock_jm.firestore.db.collection.return_value.where.return_value
    # Two non-limited .stream() calls in order: downloading_audio, rendering_video
    where.stream.side_effect = [iter(downloading or []), iter(rendering or [])]
    # One .limit().stream() call: download_pending_retry
    where.limit.return_value.stream.return_value = iter(pending or [])
    mock_jm.get_job.side_effect = lambda jid: (jobs or {}).get(jid)
    mock_jm.transition_to_state.return_value = True
    return mock_jm


def _run(client, mock_jm):
    mock_ws = MagicMock()
    mock_ws.trigger_audio_download_worker = AsyncMock(return_value=True)
    with patch("backend.api.routes.internal.JobManager", return_value=mock_jm), \
         patch("backend.services.worker_service.get_worker_service", return_value=mock_ws):
        resp = client.post("/api/internal/recover-stuck-jobs")
    return resp, mock_ws


def test_stuck_torrent_download_is_parked(client):
    """A stuck RED download is parked into DOWNLOAD_PENDING_RETRY, not failed."""
    job = _job("dl1", JobStatus.DOWNLOADING_AUDIO, minutes_stale=15, source_name="RED")
    mock_jm = _mock_jm(downloading=[_doc("dl1", JobStatus.DOWNLOADING_AUDIO)], jobs={"dl1": job})
    resp, _ = _run(client, mock_jm)
    data = resp.json()
    assert data["download_parked_jobs"] == ["dl1"]
    assert data["recovered_count"] == 0
    assert mock_jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.DOWNLOAD_PENDING_RETRY
    mock_jm.fail_job.assert_not_called()


def test_stuck_nontorrent_download_is_failed(client):
    """A stuck YouTube download still fails (deterministic source, no auto-retry)."""
    job = _job("dl2", JobStatus.DOWNLOADING_AUDIO, minutes_stale=15, source_name="YouTube")
    mock_jm = _mock_jm(downloading=[_doc("dl2", JobStatus.DOWNLOADING_AUDIO)], jobs={"dl2": job})
    resp, _ = _run(client, mock_jm)
    data = resp.json()
    assert data["recovered_jobs"] == ["dl2"]
    assert data["download_parked_jobs"] == []
    mock_jm.fail_job.assert_called_once()


def test_parked_download_is_retriggered(client):
    """A parked download within 24h is re-triggered."""
    job = _job("dl3", JobStatus.DOWNLOAD_PENDING_RETRY,
               download_retry={"first_seen_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                               "attempt_count": 3})
    mock_jm = _mock_jm(pending=[_doc("dl3", JobStatus.DOWNLOAD_PENDING_RETRY)], jobs={"dl3": job})
    resp, mock_ws = _run(client, mock_jm)
    data = resp.json()
    assert data["download_retried_jobs"] == ["dl3"]
    mock_ws.trigger_audio_download_worker.assert_awaited_once_with("dl3")


def test_parked_download_times_out_after_24h(client):
    """A parked download older than 24h is permanently failed."""
    job = _job("dl4", JobStatus.DOWNLOAD_PENDING_RETRY,
               download_retry={"first_seen_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
                               "attempt_count": 40})
    mock_jm = _mock_jm(pending=[_doc("dl4", JobStatus.DOWNLOAD_PENDING_RETRY)], jobs={"dl4": job})
    resp, mock_ws = _run(client, mock_jm)
    data = resp.json()
    assert data["download_timed_out_jobs"] == ["dl4"]
    assert data["download_retried_jobs"] == []
    mock_jm.fail_job.assert_called_once()
    mock_ws.trigger_audio_download_worker.assert_not_awaited()


def test_orphaned_render_is_reparked(client):
    """A render stale >45 min is re-parked into RENDER_PENDING_CAPACITY."""
    job = _job("r1", JobStatus.RENDERING_VIDEO, minutes_stale=60)
    mock_jm = _mock_jm(rendering=[_doc("r1", JobStatus.RENDERING_VIDEO)], jobs={"r1": job})
    resp, _ = _run(client, mock_jm)
    data = resp.json()
    assert data["reparked_render_jobs"] == ["r1"]
    assert mock_jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.RENDER_PENDING_CAPACITY
    meta = mock_jm.update_state_data.call_args.args[2]
    assert meta["last_code"] == "render_stalled"


def test_fresh_render_not_reparked(client):
    """A render updated 10 min ago is NOT re-parked."""
    job = _job("r2", JobStatus.RENDERING_VIDEO, minutes_stale=10)
    mock_jm = _mock_jm(rendering=[_doc("r2", JobStatus.RENDERING_VIDEO)], jobs={"r2": job})
    resp, _ = _run(client, mock_jm)
    assert resp.json()["reparked_render_count"] == 0
    mock_jm.transition_to_state.assert_not_called()
