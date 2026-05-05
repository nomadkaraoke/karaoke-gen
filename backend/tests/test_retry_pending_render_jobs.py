"""Tests for the auto-retry endpoint for capacity-parked render jobs.

Endpoint: POST /api/internal/retry-pending-render-jobs

Phase 2 of the encoding-worker capacity-resilience plan. Cloud Scheduler
fires this endpoint every 5 minutes; for each job parked in
RENDER_PENDING_CAPACITY it either:
  - Times the job out (>24h waiting) and transitions to FAILED with a
    permanent-failure message, or
  - Triggers the render worker again so it can attempt to start the GCE VM.
"""

from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.dependencies import require_admin
from backend.main import app
from backend.models.job import JobStatus
from backend.services.auth_service import AuthResult, UserType


@pytest.fixture
def client():
    """TestClient with admin auth explicitly stubbed.

    The repo-wide autouse fixture in conftest.py sets these overrides too,
    but other tests (test_anti_abuse.py, test_resend_magic_link.py) call
    `app.dependency_overrides.clear()` in their teardown which can wipe the
    autouse setup before our tests run. Re-establish the override here so
    these tests are insensitive to ordering.
    """
    async def fake_admin():
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=999,
            message="test admin",
            is_admin=True,
            user_email="test@example.com",
        )

    app.dependency_overrides[require_admin] = fake_admin
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _make_doc(job_id, *, first_seen, attempt_count=1, status=None):
    """Build a Firestore stream-style snapshot mock for a pending-capacity job."""
    doc = MagicMock()
    doc.id = job_id
    doc.to_dict.return_value = {
        "job_id": job_id,
        "status": (status or JobStatus.RENDER_PENDING_CAPACITY).value
        if hasattr(status or JobStatus.RENDER_PENDING_CAPACITY, "value")
        else (status or "render_pending_capacity"),
        "state_data": {
            "render_pending_capacity": {
                "first_seen_at": first_seen,
                "last_attempt_at": first_seen,
                "attempt_count": attempt_count,
                "last_code": "ZONE_RESOURCE_POOL_EXHAUSTED",
                "last_zone": "us-central1-c",
                "last_vm": "encoding-worker-b",
            }
        },
    }
    return doc


def _patch_endpoint_dependencies(stream_docs, *, transition_returns=True):
    """Common patch context for the retry endpoint internals."""
    mock_jm = MagicMock()
    mock_jm.firestore.db.collection.return_value.where.return_value.order_by.return_value.limit.return_value.stream.return_value = (
        iter(stream_docs)
    )
    mock_jm.transition_to_state.return_value = transition_returns

    mock_worker_service = MagicMock()
    mock_worker_service.trigger_render_video_worker = AsyncMock(return_value=True)

    return mock_jm, mock_worker_service


def test_retries_one_job_per_tick(client):
    """The endpoint retries up to MAX_PER_TICK (1) job per fire."""
    now = datetime.now(UTC)
    recent = (now - timedelta(minutes=10)).isoformat()

    docs = [
        _make_doc("job-a", first_seen=recent, attempt_count=2),
        _make_doc("job-b", first_seen=recent, attempt_count=1),
    ]

    mock_jm, mock_worker_service = _patch_endpoint_dependencies(docs)

    with patch("backend.api.routes.internal.JobManager", return_value=mock_jm), \
         patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service):
        response = client.post("/api/internal/retry-pending-render-jobs")

    assert response.status_code == 200
    data = response.json()
    assert data["retried_count"] == 1
    assert data["timed_out_count"] == 0
    assert data["skipped_for_next_tick"] == 1
    assert data["retried_jobs"] == ["job-a"]  # oldest by stream order

    mock_worker_service.trigger_render_video_worker.assert_awaited_once_with("job-a")


def test_times_out_jobs_older_than_max_wait(client):
    """A job older than MAX_WAIT_SECONDS (24h) is failed with a permanent message."""
    now = datetime.now(UTC)
    too_old = (now - timedelta(hours=25)).isoformat()
    fresh = (now - timedelta(minutes=10)).isoformat()

    docs = [
        _make_doc("old-job", first_seen=too_old, attempt_count=200),
        _make_doc("fresh-job", first_seen=fresh, attempt_count=1),
    ]

    mock_jm, mock_worker_service = _patch_endpoint_dependencies(docs)

    with patch("backend.api.routes.internal.JobManager", return_value=mock_jm), \
         patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service):
        response = client.post("/api/internal/retry-pending-render-jobs")

    data = response.json()
    assert data["timed_out_count"] == 1
    assert "old-job" in data["timed_out_jobs"]
    assert data["retried_count"] == 1
    assert data["retried_jobs"] == ["fresh-job"]

    # fail_job called for the timed-out one with permanent flag
    mock_jm.fail_job.assert_called_once()
    fail_args = mock_jm.fail_job.call_args
    assert fail_args.args[0] == "old-job"
    assert "manual" in fail_args.args[1].lower() or "support" in fail_args.args[1].lower()
    error_details = fail_args.kwargs.get("error_details") or {}
    assert error_details.get("permanent_capacity_timeout") is True


def test_no_op_when_queue_empty(client):
    """Endpoint succeeds with zero retries when no jobs are pending."""
    mock_jm, mock_worker_service = _patch_endpoint_dependencies([])

    with patch("backend.api.routes.internal.JobManager", return_value=mock_jm), \
         patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service):
        response = client.post("/api/internal/retry-pending-render-jobs")

    assert response.status_code == 200
    data = response.json()
    assert data["retried_count"] == 0
    assert data["timed_out_count"] == 0
    mock_worker_service.trigger_render_video_worker.assert_not_called()


def test_clears_error_state_and_resets_render_progress_before_retry(client):
    """Before retrying, the endpoint clears error state so the worker starts clean.

    Mirrors the manual /retry endpoint flow — without this, the prior failure
    metadata could mislead operators looking at the job after the retry.
    """
    now = datetime.now(UTC)
    docs = [_make_doc("job-x", first_seen=(now - timedelta(minutes=5)).isoformat())]

    mock_jm, mock_worker_service = _patch_endpoint_dependencies(docs)

    with patch("backend.api.routes.internal.JobManager", return_value=mock_jm), \
         patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service):
        response = client.post("/api/internal/retry-pending-render-jobs")

    assert response.status_code == 200

    update_calls = mock_jm.update_job.call_args_list
    assert update_calls, "Expected update_job to be called to clear error fields"
    cleared = update_calls[0].args[1]
    assert cleared.get("error_message") is None
    assert cleared.get("error_details") is None

    state_data_calls = mock_jm.update_state_data.call_args_list
    render_progress_resets = [c for c in state_data_calls if c.args[1] == "render_progress"]
    assert render_progress_resets
    assert render_progress_resets[-1].args[2] == {"stage": "pending"}

    mock_jm.transition_to_state.assert_called_once()
    transition_kwargs = mock_jm.transition_to_state.call_args.kwargs
    assert transition_kwargs["new_status"] == JobStatus.REVIEW_COMPLETE
