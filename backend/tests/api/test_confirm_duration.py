"""
Tests for POST /api/jobs/{job_id}/confirm-duration endpoint.

Covers:
- reconcile path: deduct pending_additional_credits, trigger workers
- preflight path: deduct pending_additional_credits (delta, not full cost), trigger workers
- insufficient credits → 402
- acknowledged_credits mismatch → 409
- wrong status → 409
- not found / not owner → 404
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus
from backend.services.auth_service import UserType, AuthResult

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _ensure_auth_overrides():
    """Ensure auth dependency overrides are set for every test in this file.

    Mirrors the pattern used by test_audio_edit_routes.py — necessary because
    the conftest autouse fixture can be cleared by intervening tests when running
    the whole suite with -k filters.
    """
    from backend.api.dependencies import require_auth, require_admin
    from backend.main import app

    async def mock_require_auth():
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=999,
            message="Test admin token",
            is_admin=True,
            user_email="test@example.com",
        )

    app.dependency_overrides[require_auth] = mock_require_auth
    app.dependency_overrides[require_admin] = mock_require_auth
    yield
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(require_admin, None)


def _job(**kwargs) -> Job:
    defaults = dict(
        job_id="job-dur-1",
        created_at=NOW,
        updated_at=NOW,
        user_email="owner@example.com",
    )
    defaults.update(kwargs)
    return Job(**defaults)


def _dur_confirm_job_reconcile(
    job_id="job-dur-1",
    credits_charged=2,
    pending_additional_credits=1,
    user_email="owner@example.com",
):
    """Job in AWAITING_DURATION_CONFIRM with reason=reconcile."""
    return _job(
        job_id=job_id,
        status=JobStatus.AWAITING_DURATION_CONFIRM,
        user_email=user_email,
        state_data={
            "duration_confirm_reason": "reconcile",
            "credits_charged": credits_charged,
            "pending_additional_credits": pending_additional_credits,
        },
    )


def _dur_confirm_job_preflight(
    job_id="job-dur-2",
    credits_charged=1,
    pending_additional_credits=2,
    user_email="owner@example.com",
):
    """Job in AWAITING_DURATION_CONFIRM with reason=preflight.

    Delta model: create_job already charged 1 credit; the upload's duration
    estimate (e.g. ~25 min / 3-credit song) means 2 MORE credits are owed.
    pending_additional_credits=2, credits_charged=1, expected_total=3.
    """
    return _job(
        job_id=job_id,
        status=JobStatus.AWAITING_DURATION_CONFIRM,
        user_email=user_email,
        state_data={
            "duration_confirm_reason": "preflight",
            "credits_charged": credits_charged,
            "pending_additional_credits": pending_additional_credits,
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_job_manager():
    jm = MagicMock()
    jm.get_job.return_value = _dur_confirm_job_reconcile()
    jm.update_job.return_value = None
    jm.update_state_data.return_value = None
    jm.transition_to_state.return_value = True
    return jm


@pytest.fixture
def mock_user_service():
    us = MagicMock()
    # Default: successful deduction
    us.deduct_credits.return_value = (True, 10, "ok")
    return us


@pytest.fixture
def mock_worker_service():
    ws = MagicMock()
    ws.trigger_audio_worker = AsyncMock(return_value=True)
    ws.trigger_lyrics_worker = AsyncMock(return_value=True)
    return ws


@pytest.fixture
def patched_app(mock_job_manager, mock_user_service, mock_worker_service):
    """Patch module-level singletons and yield the TestClient."""
    from fastapi.testclient import TestClient
    from backend.main import app

    # get_user_service and get_worker_service are called at request-time inside the
    # endpoint (not at module load), so we patch them where they are looked up:
    # get_user_service at the jobs module scope, get_worker_service at the module level.
    with (
        patch("backend.api.routes.jobs.job_manager", mock_job_manager),
        patch(
            "backend.api.routes.jobs.get_user_service",
            return_value=mock_user_service,
        ),
        patch(
            "backend.api.routes.jobs.get_worker_service",
            return_value=mock_worker_service,
        ),
    ):
        yield TestClient(app), mock_job_manager, mock_user_service, mock_worker_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(client, job_id, acknowledged_credits):
    return client.post(
        f"/api/jobs/{job_id}/confirm-duration",
        json={"acknowledged_credits": acknowledged_credits},
    )


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestConfirmDurationReconcile:
    """Reconcile path: credits_charged=2, pending_additional_credits=1, owed=1, expected_total=3."""

    def test_returns_200_and_deducts_one_credit(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_reconcile(
            credits_charged=2, pending_additional_credits=1
        )

        resp = _post(client, "job-dur-1", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        # Verify deduct was called with count_as_job_creation=False
        us.deduct_credits.assert_called_once()
        call_kwargs = us.deduct_credits.call_args
        args, kwargs = call_kwargs
        # Signature: deduct_credits(email, job_id, amount, reason, count_as_job_creation)
        assert kwargs.get("amount", args[2] if len(args) > 2 else None) == 1
        assert kwargs.get("reason", args[3] if len(args) > 3 else None) == "duration_confirm"
        count_flag = kwargs.get(
            "count_as_job_creation", args[4] if len(args) > 4 else True
        )
        assert count_flag is False, "reconcile top-up must NOT increment total_jobs_created"

    def test_state_data_updated_after_confirm(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_reconcile(
            credits_charged=2, pending_additional_credits=1
        )

        resp = _post(client, "job-dur-1", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        # update_job must have been called to persist state changes
        jm.update_job.assert_called()

    def test_returns_job_in_response(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_reconcile()

        resp = _post(client, "job-dur-1", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "job_id" in body

    def test_insufficient_credits_returns_402(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_reconcile(
            credits_charged=2, pending_additional_credits=1
        )
        us.deduct_credits.return_value = (False, 0, "Insufficient credits")

        resp = _post(client, "job-dur-1", acknowledged_credits=3)

        assert resp.status_code == 402, resp.text

    def test_acknowledged_mismatch_returns_409(self, patched_app):
        """Client sends acknowledged_credits=99, expected is 3 → 409."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_reconcile(
            credits_charged=2, pending_additional_credits=1
        )

        resp = _post(client, "job-dur-1", acknowledged_credits=99)

        assert resp.status_code == 409, resp.text
        # No credits should be deducted
        us.deduct_credits.assert_not_called()

    def test_workers_triggered_on_success(self, patched_app):
        """Verify that create_task was called (worker trigger fired)."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_reconcile()

        with patch("backend.api.routes.jobs.asyncio.create_task") as mock_ct:
            mock_ct.return_value = MagicMock()
            resp = _post(client, "job-dur-1", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        mock_ct.assert_called_once()

    def test_workers_not_triggered_on_credit_failure(self, patched_app):
        """If deduction fails, workers must NOT be triggered."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_reconcile()
        us.deduct_credits.return_value = (False, 0, "Insufficient credits")

        with patch("backend.api.routes.jobs.asyncio.create_task") as mock_ct:
            resp = _post(client, "job-dur-1", acknowledged_credits=3)

        assert resp.status_code == 402
        mock_ct.assert_not_called()


class TestConfirmDurationPreflight:
    """Preflight path (delta model): credits_charged=1, pending_additional_credits=2, owed=2, expected_total=3.

    create_job charged 1 credit at upload time; the pause-creating site computed
    pending_additional_credits=2 as the delta for a ~25-min (3-credit) song.
    confirm_duration deducts ONLY the delta (2), bringing credits_charged to 3.
    """

    def test_preflight_returns_200(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_preflight(
            credits_charged=1, pending_additional_credits=2
        )

        resp = _post(client, "job-dur-2", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text

    def test_preflight_deducts_delta_only(self, patched_app):
        """Delta model: only deduct pending_additional_credits (2), not the full cost (3)."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_preflight(
            credits_charged=1, pending_additional_credits=2
        )

        resp = _post(client, "job-dur-2", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        us.deduct_credits.assert_called_once()
        call_args, call_kwargs = us.deduct_credits.call_args
        amount = call_kwargs.get("amount", call_args[2] if len(call_args) > 2 else None)
        assert amount == 2, (
            "preflight must deduct the delta (pending_additional_credits=2), "
            "not the full cost — create_job already charged 1 credit"
        )

    def test_preflight_count_as_job_creation_false(self, patched_app):
        """Preflight uses count_as_job_creation=False (job was already counted at creation)."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_preflight(
            credits_charged=1, pending_additional_credits=2
        )

        resp = _post(client, "job-dur-2", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        call_args, call_kwargs = us.deduct_credits.call_args
        count_flag = call_kwargs.get(
            "count_as_job_creation",
            call_args[4] if len(call_args) > 4 else True,
        )
        assert count_flag is False, (
            "preflight: job already counted at create_job() credit deduction; "
            "do not double-count total_jobs_created"
        )

    def test_preflight_credits_charged_becomes_total(self, patched_app):
        """After confirm, credits_charged should be updated to credits_charged + owed = 3."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_preflight(
            credits_charged=1, pending_additional_credits=2
        )

        resp = _post(client, "job-dur-2", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        jm.update_job.assert_called()
        update_call_args = jm.update_job.call_args
        update_dict = update_call_args[0][1] if update_call_args[0] else update_call_args[1]
        assert update_dict.get("state_data.credits_charged") == 3, (
            "credits_charged must be updated to 1 (prior) + 2 (owed) = 3"
        )

    def test_preflight_insufficient_credits_returns_402(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_preflight(
            credits_charged=1, pending_additional_credits=2
        )
        us.deduct_credits.return_value = (False, 1, "Insufficient credits")

        resp = _post(client, "job-dur-2", acknowledged_credits=3)

        assert resp.status_code == 402, resp.text

    def test_preflight_mismatch_returns_409(self, patched_app):
        """acknowledged_credits=5 but expected 3 (1+2) → 409."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_preflight(
            credits_charged=1, pending_additional_credits=2
        )

        resp = _post(client, "job-dur-2", acknowledged_credits=5)

        assert resp.status_code == 409, resp.text

    def test_preflight_workers_triggered(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _dur_confirm_job_preflight(
            credits_charged=1, pending_additional_credits=2
        )

        with patch("backend.api.routes.jobs.asyncio.create_task") as mock_ct:
            mock_ct.return_value = MagicMock()
            resp = _post(client, "job-dur-2", acknowledged_credits=3)

        assert resp.status_code == 200, resp.text
        mock_ct.assert_called_once()


class TestConfirmDurationErrorCases:
    """Status guard, not-found, and not-owner cases."""

    def test_wrong_status_returns_409(self, patched_app):
        """Job not in AWAITING_DURATION_CONFIRM → 409."""
        client, jm, us, ws = patched_app
        jm.get_job.return_value = _job(
            job_id="done-job",
            status=JobStatus.COMPLETE,
            user_email="owner@example.com",
            state_data={},
        )

        resp = _post(client, "done-job", acknowledged_credits=1)

        assert resp.status_code == 409, resp.text

    def test_job_not_found_returns_404(self, patched_app):
        client, jm, us, ws = patched_app
        jm.get_job.return_value = None

        resp = _post(client, "nonexistent", acknowledged_credits=1)

        assert resp.status_code == 404, resp.text

    def test_not_owner_returns_404(self, patched_app):
        """Job owned by different user (non-admin) should 404 to avoid leaking job existence."""
        client, jm, us, ws = patched_app
        # The conftest mock_auth_dependency sets user_email="test@example.com" and is_admin=True.
        # To test the non-owner path we need to override auth to return a non-admin user.
        from backend.api.dependencies import require_auth
        from backend.main import app

        async def non_owner_auth():
            return AuthResult(
                is_valid=True,
                user_type=UserType.STRIPE,
                remaining_uses=5,
                message="regular user",
                is_admin=False,
                user_email="other@example.com",  # different from owner@example.com
            )

        jm.get_job.return_value = _dur_confirm_job_reconcile(user_email="owner@example.com")
        app.dependency_overrides[require_auth] = non_owner_auth

        try:
            resp = _post(client, "job-dur-1", acknowledged_credits=3)
        finally:
            app.dependency_overrides.pop(require_auth, None)

        assert resp.status_code == 404, resp.text
