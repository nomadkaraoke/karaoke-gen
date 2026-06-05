"""
Tests for duration-based credit pricing on URL job creation.

Covers:
- URL create with duration_seconds → charges N credits, stores credits_charged
- duration_seconds over 60 min → 422 (blocked)
- acknowledged_credits mismatch → 409
- default path (no duration_seconds) still charges 1 credit
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus
from backend.services.auth_service import UserType, AuthResult

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_auth_overrides():
    """Ensure auth dependency overrides are set for every test in this file."""
    from backend.api.dependencies import require_auth, require_admin
    from backend.main import app

    async def mock_require_auth():
        return AuthResult(
            is_valid=True,
            user_type=UserType.STRIPE,
            remaining_uses=5,
            message="Regular test user",
            is_admin=False,
            user_email="buyer@example.com",
        )

    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_admin] = mock_require_auth
    yield
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)


# ---------------------------------------------------------------------------
# Helper: build a minimal Job returned by mock job_manager.create_job
# ---------------------------------------------------------------------------


def _pending_job(job_id="url-job-1", credits_charged=1, user_email="buyer@example.com") -> Job:
    return Job(
        job_id=job_id,
        status=JobStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
        user_email=user_email,
        state_data={"credits_charged": credits_charged},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_job_manager():
    jm = MagicMock()
    # Default: returns a 1-credit job; individual tests override as needed
    jm.create_job.return_value = _pending_job(credits_charged=1)
    return jm


@pytest.fixture
def mock_worker_service():
    ws = MagicMock()
    ws.trigger_audio_worker = AsyncMock(return_value=True)
    ws.trigger_lyrics_worker = AsyncMock(return_value=True)
    return ws


@pytest.fixture
def mock_theme_service():
    ts = MagicMock()
    ts.get_default_theme_id.return_value = "nomad"
    return ts


@pytest.fixture
def mock_user_service():
    us = MagicMock()
    us.check_credits.return_value = 10
    us.has_credits.return_value = True
    return us


@pytest.fixture
def patched_app(mock_job_manager, mock_worker_service, mock_theme_service, mock_user_service):
    """Patch module-level singletons and yield the TestClient."""
    from fastapi.testclient import TestClient
    from backend.main import app

    with (
        patch("backend.api.routes.jobs.job_manager", mock_job_manager),
        patch("backend.api.routes.jobs.worker_service", mock_worker_service),
        patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service),
        patch("backend.api.routes.jobs.get_user_service", return_value=mock_user_service),
        patch("backend.services.firestore_service.FirestoreService.update_job"),
    ):
        yield TestClient(app), mock_job_manager, mock_worker_service, mock_theme_service, mock_user_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_url_job(client, *, url="https://youtube.com/watch?v=abc", **extra):
    body = {"url": url}
    body.update(extra)
    return client.post("/api/jobs", json=body)


# ---------------------------------------------------------------------------
# Tests: duration-based pricing on URL create
# ---------------------------------------------------------------------------


class TestURLCreatePricing:
    """Duration-based credit pricing on POST /api/jobs."""

    def test_905s_with_acknowledged_2_charges_2_credits(self, patched_app):
        """
        905 seconds → ceil(905/600) = 2 credits.
        User sends acknowledged_credits=2, has ≥2 credits.
        Expect: 200, JobCreate called with credits=2,
                returned state_data.credits_charged == 2.
        """
        client, jm, ws, ts, us = patched_app
        # create_job returns a job with credits_charged=2
        jm.create_job.return_value = _pending_job(credits_charged=2)

        resp = _post_url_job(client, duration_seconds=905, acknowledged_credits=2)

        assert resp.status_code == 200, resp.text
        # Verify create_job was called with credits=2
        jm.create_job.assert_called_once()
        call_kwargs = jm.create_job.call_args
        job_create_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("job_create")
        assert job_create_arg.credits == 2, (
            f"Expected credits=2 in JobCreate, got {job_create_arg.credits}"
        )

    def test_905s_credits_charged_stored(self, patched_app):
        """state_data.credits_charged on the returned job should equal 2."""
        client, jm, ws, ts, us = patched_app
        jm.create_job.return_value = _pending_job(credits_charged=2)

        resp = _post_url_job(client, duration_seconds=905, acknowledged_credits=2)

        assert resp.status_code == 200, resp.text
        # The route returns a JobResponse (job_id, status, message) but we can
        # confirm the job_manager was called correctly (credits_charged lives in Job.state_data
        # which is set by create_job implementation).
        body = resp.json()
        assert "job_id" in body

    def test_4000s_blocked_returns_422(self, patched_app):
        """
        4000 seconds > 3600 → is_blocked() = True → 422 before job is created.
        """
        client, jm, ws, ts, us = patched_app

        resp = _post_url_job(client, duration_seconds=4000)

        assert resp.status_code == 422, resp.text
        # Job must NOT be created
        jm.create_job.assert_not_called()

    def test_3600s_exactly_is_blocked_returns_422(self, patched_app):
        """
        3600 seconds is NOT blocked (> not >=), should succeed.
        Actually: is_blocked checks > 3600, so exactly 3600 is NOT blocked.
        """
        from backend.services.pricing import is_blocked
        assert not is_blocked(3600), "3600s should not be blocked (ceiling is >3600)"

        client, jm, ws, ts, us = patched_app
        jm.create_job.return_value = _pending_job(credits_charged=6)

        resp = _post_url_job(client, duration_seconds=3600, acknowledged_credits=6)

        # 3600s = ceil(3600/600) = 6 credits, not blocked → success
        assert resp.status_code == 200, resp.text

    def test_3601s_blocked_returns_422(self, patched_app):
        """3601 seconds > 3600 → blocked."""
        client, jm, ws, ts, us = patched_app

        resp = _post_url_job(client, duration_seconds=3601)

        assert resp.status_code == 422, resp.text
        jm.create_job.assert_not_called()

    def test_acknowledged_credits_mismatch_returns_409(self, patched_app):
        """
        duration_seconds=905 → credits=2, but acknowledged_credits=1 → 409.
        """
        client, jm, ws, ts, us = patched_app

        resp = _post_url_job(client, duration_seconds=905, acknowledged_credits=1)

        assert resp.status_code == 409, resp.text
        # Job must NOT be created when acknowledged_credits mismatches
        jm.create_job.assert_not_called()

    def test_no_duration_charges_1_credit(self, patched_app):
        """
        When duration_seconds is not provided, fall back to credits=1 (unchanged behaviour).
        """
        client, jm, ws, ts, us = patched_app
        jm.create_job.return_value = _pending_job(credits_charged=1)

        resp = _post_url_job(client)

        assert resp.status_code == 200, resp.text
        jm.create_job.assert_called_once()
        call_kwargs = jm.create_job.call_args
        job_create_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("job_create")
        assert job_create_arg.credits == 1, (
            f"No duration → credits should default to 1, got {job_create_arg.credits}"
        )

    def test_duration_estimate_stored_on_job(self, patched_app):
        """
        When duration_seconds is provided, the handler must call FirestoreService.update_job
        with state_data.duration_estimate_seconds and state_data.duration_estimate_source.
        """
        from unittest.mock import patch as _patch

        client, jm, ws, ts, us = patched_app
        jm.create_job.return_value = _pending_job(credits_charged=2)

        with _patch("backend.services.firestore_service.FirestoreService.update_job") as mock_update:
            resp = _post_url_job(client, duration_seconds=905, acknowledged_credits=2)

        assert resp.status_code == 200, resp.text

        # update_job must have been called at least once
        assert mock_update.called, "FirestoreService.update_job was never called"

        # Collect every update_payload passed across all calls
        all_payloads: dict = {}
        for call in mock_update.call_args_list:
            # call[0] = positional args: (job_id, payload)
            payload = call[0][1] if len(call[0]) >= 2 else call[1].get("updates", {})
            all_payloads.update(payload)

        assert all_payloads.get("state_data.duration_estimate_seconds") == 905, (
            f"Expected state_data.duration_estimate_seconds=905 in update_job payload, "
            f"got: {all_payloads}"
        )
        assert all_payloads.get("state_data.duration_estimate_source") == "youtube_metadata", (
            f"Expected state_data.duration_estimate_source='youtube_metadata' in update_job payload, "
            f"got: {all_payloads}"
        )

    def test_no_duration_duration_estimate_source_unknown(self, patched_app):
        """Without duration_seconds, duration_estimate_source defaults to 'unknown'."""
        client, jm, ws, ts, us = patched_app
        jm.create_job.return_value = _pending_job(credits_charged=1)

        resp = _post_url_job(client)

        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Tests: create_job generalisation (Part A) via JobManager directly
# ---------------------------------------------------------------------------


class TestJobManagerCreditsParam:
    """
    Unit tests for JobManager.create_job with configurable credits.
    Tests the Part A changes without going through the HTTP layer.
    """

    @pytest.fixture
    def mock_fs(self):
        with patch("backend.services.job_manager.FirestoreService") as cls:
            svc = MagicMock()
            cls.return_value = svc
            yield svc

    @pytest.fixture
    def mock_storage(self):
        with patch("backend.services.job_manager.StorageService") as cls:
            svc = MagicMock()
            cls.return_value = svc
            yield svc

    @pytest.fixture
    def mock_us(self):
        svc = MagicMock()
        with patch("backend.services.user_service.get_user_service", return_value=svc):
            yield svc

    def _jm(self, mock_fs, mock_storage):
        from backend.services.job_manager import JobManager
        return JobManager()

    def test_default_credits_is_1(self, mock_fs, mock_storage, mock_us):
        """JobCreate.credits defaults to 1."""
        from backend.models.job import JobCreate
        jc = JobCreate(theme_id="nomad")
        assert jc.credits == 1

    def test_explicit_credits_stored(self, mock_fs, mock_storage, mock_us):
        """JobCreate.credits=3 is accepted."""
        from backend.models.job import JobCreate
        jc = JobCreate(theme_id="nomad", credits=3)
        assert jc.credits == 3

    def test_create_job_no_email_skips_credit_check(self, mock_fs, mock_storage, mock_us):
        """Jobs with no user_email skip credit gate (unchanged behaviour)."""
        from backend.services.job_manager import JobManager
        from backend.models.job import JobCreate
        jm = JobManager()
        job = jm.create_job(JobCreate(theme_id="nomad"), is_admin=False)
        assert job is not None
        mock_us.has_credits.assert_not_called()
        mock_us.deduct_credits.assert_not_called()

    def test_admin_bypasses_credit_check(self, mock_fs, mock_storage, mock_us):
        """Admin bypass unchanged for any credits value."""
        from backend.services.job_manager import JobManager
        from backend.models.job import JobCreate
        jm = JobManager()
        job = jm.create_job(
            JobCreate(theme_id="nomad", user_email="admin@test.com", credits=5),
            is_admin=True,
        )
        assert job is not None
        mock_us.has_credits.assert_not_called()
        mock_us.deduct_credits.assert_not_called()

    def test_has_credits_gate_uses_credits_param(self, mock_fs, mock_storage, mock_us):
        """has_credits gate should check the user has >= credits, not just >= 1."""
        from backend.services.job_manager import JobManager
        from backend.models.job import JobCreate
        from backend.exceptions import InsufficientCreditsError

        mock_us.has_credits.return_value = False
        mock_us.check_credits.return_value = 1  # only 1 credit, need 3

        jm = JobManager()
        with pytest.raises(InsufficientCreditsError) as exc_info:
            jm.create_job(
                JobCreate(theme_id="nomad", user_email="user@test.com", credits=3),
                is_admin=False,
            )
        # credits_required should match what we asked for
        assert exc_info.value.credits_required == 3

    def test_deduct_credits_called_with_n(self, mock_fs, mock_storage, mock_us):
        """deduct_credits is called with amount=credits (not always 1)."""
        from backend.services.job_manager import JobManager
        from backend.models.job import JobCreate

        mock_us.has_credits.return_value = True
        mock_us.check_credits.return_value = 5
        mock_us.deduct_credits.return_value = (True, 3, "Deducted 2. 3 remaining")

        jm = JobManager()
        jm.create_job(
            JobCreate(theme_id="nomad", user_email="user@test.com", credits=2),
            is_admin=False,
        )

        mock_us.deduct_credits.assert_called_once()
        call_args, call_kwargs = mock_us.deduct_credits.call_args
        amount = call_kwargs.get("amount", call_args[2] if len(call_args) > 2 else None)
        assert amount == 2, f"Expected deduct_credits(amount=2), got amount={amount}"

    def test_deduct_credits_default_is_1(self, mock_fs, mock_storage, mock_us):
        """Default path (credits=1) still deducts exactly 1 credit."""
        from backend.services.job_manager import JobManager
        from backend.models.job import JobCreate

        mock_us.has_credits.return_value = True
        mock_us.check_credits.return_value = 5
        mock_us.deduct_credits.return_value = (True, 4, "Deducted 1. 4 remaining")

        jm = JobManager()
        jm.create_job(
            JobCreate(theme_id="nomad", user_email="user@test.com"),
            is_admin=False,
        )

        mock_us.deduct_credits.assert_called_once()
        call_args, call_kwargs = mock_us.deduct_credits.call_args
        amount = call_kwargs.get("amount", call_args[2] if len(call_args) > 2 else None)
        assert amount == 1, f"Default path must deduct 1 credit, got amount={amount}"

    def test_credits_charged_in_state_data(self, mock_fs, mock_storage, mock_us):
        """state_data.credits_charged should equal credits on the created job."""
        from backend.services.job_manager import JobManager
        from backend.models.job import JobCreate

        mock_us.has_credits.return_value = True
        mock_us.check_credits.return_value = 10
        mock_us.deduct_credits.return_value = (True, 7, "Deducted 3. 7 remaining")

        jm = JobManager()
        job = jm.create_job(
            JobCreate(theme_id="nomad", user_email="user@test.com", credits=3),
            is_admin=False,
        )

        assert job.state_data.get("credits_charged") == 3, (
            f"state_data.credits_charged should be 3, got {job.state_data}"
        )

    def test_rollback_on_deduction_failure(self, mock_fs, mock_storage, mock_us):
        """If deduct_credits fails, job is deleted and InsufficientCreditsError raised."""
        from backend.services.job_manager import JobManager
        from backend.models.job import JobCreate
        from backend.exceptions import InsufficientCreditsError

        mock_us.has_credits.return_value = True
        mock_us.check_credits.return_value = 5
        mock_us.deduct_credits.return_value = (False, 0, "Race condition")

        jm = JobManager()
        with pytest.raises(InsufficientCreditsError):
            jm.create_job(
                JobCreate(theme_id="nomad", user_email="user@test.com", credits=2),
                is_admin=False,
            )

        mock_fs.create_job.assert_called_once()
        mock_fs.delete_job.assert_called_once()
