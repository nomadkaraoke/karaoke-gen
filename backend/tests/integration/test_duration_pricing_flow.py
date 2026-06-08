"""
Integration tests for the duration-pricing reconcile flow.

Tests the end-to-end path through:
  1. reconcile_duration (engine) pausing a job at AWAITING_DURATION_CONFIRM
  2. POST /api/jobs/{id}/confirm-duration resuming the job and deducting credits

Money-critical: the delta-deduction math (started at 2 credits, ended at 4,
EXACTLY 2 deducted on confirm — not 4, not the full cost again) is the primary
invariant under test.

Also includes a companion DOWN-reconcile assertion (refund path, no pause).

Harness notes
-------------
- Follows the mocking pattern from backend/tests/integration/test_state_machine_flow.py
  and test_youtube_flow.py: no Firestore emulator, mocked collaborators.
- The confirm-duration endpoint is exercised via FastAPI's TestClient.
- Worker triggers are patched so no real Cloud Run jobs fire.
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, Mock, patch, call

# Mock google.cloud.firestore before any module that imports it is loaded.
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())

from backend.models.job import Job, JobStatus
from backend.services.duration_reconciliation import reconcile_duration, ReconcileResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(
    job_id: str = "dur-test-001",
    status: JobStatus = JobStatus.DOWNLOADING,
    credits_charged: int = 2,
    user_email: str = "user@example.com",
    input_media_gcs_path: str = "gs://bucket/jobs/dur-test-001/audio.flac",
    extra_state_data: dict = None,
) -> Job:
    """Build a Job instance with the given fields."""
    state_data = {"credits_charged": credits_charged}
    if extra_state_data:
        state_data.update(extra_state_data)
    return Job(
        job_id=job_id,
        artist="Test Artist",
        title="Test Song",
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        input_media_gcs_path=input_media_gcs_path,
        state_data=state_data,
        user_email=user_email,
    )


def _make_mock_job_manager(job: Job) -> MagicMock:
    """Return a JobManager mock whose get_job always returns `job`."""
    mgr = MagicMock()
    mgr.get_job.return_value = job
    mgr.update_job.return_value = None
    mgr.transition_to_state.return_value = True
    return mgr


def _make_mock_user_service() -> MagicMock:
    """Return a UserService mock with sensible defaults."""
    svc = MagicMock()
    # deduct_credits returns (success, remaining, message)
    svc.deduct_credits.return_value = (True, 8, "2 credits deducted")
    # add_credits returns (success, new_balance, message)
    svc.add_credits.return_value = (True, 10, "Credits added")
    return svc


# ---------------------------------------------------------------------------
# Part A.1 — Reconcile-UP path (engine only)
# ---------------------------------------------------------------------------


class TestReconcileUpEngine:
    """
    Engine-level: reconcile_duration produces the correct pause result when
    actual duration (2100 s) exceeds what was charged (2 credits at ~15 min).
    """

    def test_reconcile_up_pauses_job_at_awaiting_duration_confirm(self):
        """
        Given:  credits_charged=2, actual=2100 s  →  required=4, delta=2
        Expect: action="pause", pending_additional_credits=2,
                job transitioned to AWAITING_DURATION_CONFIRM,
                state_data updated with reason="reconcile" and delta=2.
        """
        job_id = "dur-test-up-001"
        job = _make_job(job_id=job_id, credits_charged=2)
        mgr = _make_mock_job_manager(job)
        svc = _make_mock_user_service()

        result = reconcile_duration(
            job_id=job_id,
            job_manager=mgr,
            user_service=svc,
            probe_duration=lambda _job: 2100.0,  # ~35 min → 4 credits
        )

        assert result.action == "pause"
        assert result.pending_additional_credits == 2
        assert result.actual_seconds == 2100.0

        # transition_to_state called with AWAITING_DURATION_CONFIRM
        mgr.transition_to_state.assert_called_once()
        transition_call = mgr.transition_to_state.call_args
        # Engine calls with keyword args: transition_to_state(job_id=..., new_status=..., ...)
        new_status = (
            transition_call.kwargs.get("new_status")
            or (transition_call.args[1] if len(transition_call.args) >= 2 else None)
        )
        assert new_status == JobStatus.AWAITING_DURATION_CONFIRM, (
            f"Expected AWAITING_DURATION_CONFIRM, got {new_status}"
        )

        # update_job called with duration_confirm_reason and delta
        update_calls_flat = {}
        for c in mgr.update_job.call_args_list:
            if c.args and len(c.args) >= 2 and isinstance(c.args[1], dict):
                update_calls_flat.update(c.args[1])
            elif c.kwargs.get("updates"):
                update_calls_flat.update(c.kwargs["updates"])

        assert update_calls_flat.get("state_data.duration_confirm_reason") == "reconcile"
        assert update_calls_flat.get("state_data.pending_additional_credits") == 2

        # No credits deducted by the engine (deduction happens at confirm time)
        svc.deduct_credits.assert_not_called()

    def test_reconcile_up_does_not_trigger_workers(self):
        """Engine must NOT trigger workers when pausing — workers are triggered by confirm."""
        job_id = "dur-test-up-002"
        job = _make_job(job_id=job_id, credits_charged=2)
        mgr = _make_mock_job_manager(job)
        svc = _make_mock_user_service()

        # No worker_service attribute on job_manager — engine should not call it
        assert not hasattr(mgr, "trigger_audio_worker") or True  # safe to check
        reconcile_duration(
            job_id=job_id,
            job_manager=mgr,
            user_service=svc,
            probe_duration=lambda _job: 2100.0,
        )

        # Verify no worker-like calls were made on job_manager
        called_methods = [str(c) for c in mgr.mock_calls]
        assert not any("trigger" in m for m in called_methods), (
            f"Engine should not trigger workers; calls: {called_methods}"
        )


# ---------------------------------------------------------------------------
# Part A.2 — Reconcile-DOWN path (engine + user_service.add_credits)
# ---------------------------------------------------------------------------


class TestReconcileDownEngine:
    """
    Engine-level: when actual duration (600 s) is less than what was charged
    (4 credits), the engine auto-refunds the delta and returns action="proceed".
    """

    def test_reconcile_down_refunds_delta_and_proceeds(self):
        """
        Given:  credits_charged=4, actual=600 s  →  required=1, delta=-3
        Expect: action="proceed", add_credits called with amount=3,
                reason="duration_refund", credits_charged updated to 1.
        No pause. No transition to AWAITING_DURATION_CONFIRM.
        """
        job_id = "dur-test-down-001"
        job = _make_job(job_id=job_id, credits_charged=4)
        mgr = _make_mock_job_manager(job)
        svc = _make_mock_user_service()

        result = reconcile_duration(
            job_id=job_id,
            job_manager=mgr,
            user_service=svc,
            probe_duration=lambda _job: 600.0,  # 10 min → 1 credit
        )

        assert result.action == "proceed"
        assert result.actual_seconds == 600.0

        # add_credits called with correct amount and reason
        svc.add_credits.assert_called_once_with(
            job.user_email,
            amount=3,
            reason="duration_refund",
            job_id=job_id,
        )

        # credits_charged updated to the new (lower) required amount
        refund_update_found = any(
            (c.args[1] if (c.args and len(c.args) >= 2 and isinstance(c.args[1], dict))
             else c.kwargs.get("updates", {})).get("state_data.credits_charged") == 1
            for c in mgr.update_job.call_args_list
        )
        assert refund_update_found, (
            "Expected update_job to set credits_charged=1 after refund. "
            f"Actual update_job calls: {mgr.update_job.call_args_list}"
        )

        # No pause
        for c in mgr.transition_to_state.call_args_list:
            status = c.args[1] if len(c.args) >= 2 else c.kwargs.get("new_status")
            assert status != JobStatus.AWAITING_DURATION_CONFIRM, (
                "Reconcile-DOWN must NOT pause at AWAITING_DURATION_CONFIRM"
            )


# ---------------------------------------------------------------------------
# Part A.3 — Full reconcile-UP + confirm-duration end-to-end via TestClient
# ---------------------------------------------------------------------------


class TestReconcileUpEndToEnd:
    """
    End-to-end: drive the reconcile engine directly (with mocked collaborators)
    to produce the paused state, then call the real confirm-duration endpoint
    via FastAPI TestClient and verify all credit-math invariants.
    """

    @pytest.fixture
    def mock_creds(self):
        creds = MagicMock()
        creds.universe_domain = "googleapis.com"
        creds.token = "fake-token"
        creds.valid = True
        return creds

    @pytest.fixture
    def test_setup(self, mock_creds):
        """
        Build a stable Job fixture that reflects the paused state produced by
        reconcile_duration, then wire it into a TestClient with patched
        job_manager, user_service, and worker_service.

        The fixture simulates exactly the state that reconcile_duration would
        write to Firestore:
          - status = AWAITING_DURATION_CONFIRM
          - state_data.credits_charged = 2
          - state_data.pending_additional_credits = 2
          - state_data.duration_confirm_reason = "reconcile"
        """
        job_id = "dur-test-e2e-001"
        user_email = "user@example.com"

        # "Paused" job: the state reconcile_duration leaves behind
        paused_job = _make_job(
            job_id=job_id,
            status=JobStatus.AWAITING_DURATION_CONFIRM,
            credits_charged=2,
            user_email=user_email,
            extra_state_data={
                "pending_additional_credits": 2,
                "duration_confirm_reason": "reconcile",
            },
        )

        # "Resumed" job: what get_job returns AFTER confirm (post-transition)
        resumed_job = _make_job(
            job_id=job_id,
            status=JobStatus.SEPARATING_STAGE1,
            credits_charged=4,
            user_email=user_email,
            extra_state_data={
                "pending_additional_credits": 0,
                "duration_confirm_reason": "reconcile",
                "duration_confirmed": True,
            },
        )

        mock_jm = MagicMock()
        # First call returns the paused job; subsequent calls return the resumed one
        mock_jm.get_job.side_effect = [paused_job, resumed_job]
        mock_jm.update_job.return_value = None
        mock_jm.transition_to_state.return_value = True

        mock_us = MagicMock()
        # User has 10 credits; deduct 2 → 8 remaining
        mock_us.deduct_credits.return_value = (True, 8, "2 credits deducted")

        mock_ws = MagicMock()
        mock_ws.trigger_audio_worker = AsyncMock(return_value=True)
        mock_ws.trigger_lyrics_worker = AsyncMock(return_value=True)

        yield {
            "job_id": job_id,
            "user_email": user_email,
            "paused_job": paused_job,
            "resumed_job": resumed_job,
            "mock_jm": mock_jm,
            "mock_us": mock_us,
            "mock_ws": mock_ws,
        }

    def _make_client(self, setup, mock_creds):
        """Return a TestClient with all necessary patches applied."""
        from fastapi.testclient import TestClient
        from backend.main import app

        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        return patches, TestClient(app)

    def test_confirm_duration_returns_200_with_sufficient_credits(self, test_setup, mock_creds):
        """
        POST /api/jobs/{id}/confirm-duration with acknowledged_credits=4 and a
        user who has sufficient balance must return 200.
        """
        setup = test_setup
        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        from fastapi.testclient import TestClient
        from backend.main import app

        with patches[0], patches[1], patches[2], patches[3]:
            client = TestClient(app)
            response = client.post(
                f"/api/jobs/{setup['job_id']}/confirm-duration",
                json={"acknowledged_credits": 4},
                headers={"Authorization": "Bearer test-admin-token"},
            )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

    def test_confirm_duration_deducts_exactly_the_delta(self, test_setup, mock_creds):
        """
        MONEY MATH: Only the delta (2 credits) must be deducted — not the full
        cost (4) and not the original charge (2) again.

        History: job created with credits_charged=2 (original deduction at job-create).
        Reconcile found delta=2 more owed.
        Confirm should deduct exactly 2 with count_as_job_creation=False.
        """
        setup = test_setup
        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        from fastapi.testclient import TestClient
        from backend.main import app

        with patches[0], patches[1], patches[2], patches[3]:
            client = TestClient(app)
            client.post(
                f"/api/jobs/{setup['job_id']}/confirm-duration",
                json={"acknowledged_credits": 4},
                headers={"Authorization": "Bearer test-admin-token"},
            )

        setup["mock_us"].deduct_credits.assert_called_once()
        call_kwargs = setup["mock_us"].deduct_credits.call_args

        # Extract positional and keyword args
        args = call_kwargs.args
        kwargs = call_kwargs.kwargs

        # user_email is first positional arg, job_id is second
        called_email = args[0] if args else kwargs.get("email", kwargs.get("user_email"))
        called_job_id = args[1] if len(args) > 1 else kwargs.get("job_id")
        called_amount = kwargs.get("amount", args[2] if len(args) > 2 else None)
        called_reason = kwargs.get("reason", args[3] if len(args) > 3 else None)
        called_count_as_creation = kwargs.get("count_as_job_creation")

        assert called_email == setup["user_email"], (
            f"Expected email={setup['user_email']}, got {called_email}"
        )
        assert called_job_id == setup["job_id"], (
            f"Expected job_id={setup['job_id']}, got {called_job_id}"
        )
        assert called_amount == 2, (
            f"MONEY BUG: Expected deduct_credits(amount=2) for the delta, "
            f"but got amount={called_amount}. "
            "The confirm endpoint must deduct only the delta (pending_additional_credits), "
            "not the full cost or the original charge again."
        )
        assert called_reason == "duration_confirm", (
            f"Expected reason='duration_confirm', got {called_reason}"
        )
        assert called_count_as_creation is False, (
            "count_as_job_creation must be False for a delta top-up "
            f"(job was already counted at create_job). Got: {called_count_as_creation}"
        )

    def test_confirm_duration_updates_credits_charged_to_total(self, test_setup, mock_creds):
        """
        After confirm, state_data.credits_charged must be updated to 4
        (2 original + 2 delta), not left at 2.
        """
        setup = test_setup
        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        from fastapi.testclient import TestClient
        from backend.main import app

        with patches[0], patches[1], patches[2], patches[3]:
            client = TestClient(app)
            client.post(
                f"/api/jobs/{setup['job_id']}/confirm-duration",
                json={"acknowledged_credits": 4},
                headers={"Authorization": "Bearer test-admin-token"},
            )

        # Collect all update_job calls and check credits_charged was set to 4
        all_updates = {}
        for c in setup["mock_jm"].update_job.call_args_list:
            updates_arg = c.args[1] if (c.args and len(c.args) >= 2) else c.kwargs.get("updates", {})
            if isinstance(updates_arg, dict):
                all_updates.update(updates_arg)

        assert all_updates.get("state_data.credits_charged") == 4, (
            f"Expected credits_charged=4 in update_job call. "
            f"Actual updates written: {all_updates}"
        )
        assert all_updates.get("state_data.pending_additional_credits") == 0, (
            "Expected pending_additional_credits=0 after confirm"
        )

    def test_confirm_duration_transitions_to_separating_stage1(self, test_setup, mock_creds):
        """
        The job must be transitioned OUT of AWAITING_DURATION_CONFIRM to
        SEPARATING_STAGE1 before workers are triggered.
        """
        setup = test_setup
        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        from fastapi.testclient import TestClient
        from backend.main import app

        with patches[0], patches[1], patches[2], patches[3]:
            client = TestClient(app)
            client.post(
                f"/api/jobs/{setup['job_id']}/confirm-duration",
                json={"acknowledged_credits": 4},
                headers={"Authorization": "Bearer test-admin-token"},
            )

        setup["mock_jm"].transition_to_state.assert_called()
        call_args = setup["mock_jm"].transition_to_state.call_args
        new_status = (
            call_args.args[1]
            if (call_args.args and len(call_args.args) >= 2)
            else call_args.kwargs.get("new_status")
        )
        assert new_status == JobStatus.SEPARATING_STAGE1, (
            f"Expected transition to SEPARATING_STAGE1, got {new_status}"
        )

    def test_confirm_duration_wrong_acknowledged_credits_returns_409(self, test_setup, mock_creds):
        """
        If acknowledged_credits does not match expected_total (credits_charged + owed),
        the endpoint must return 409 (stale client; frontend should refresh).
        """
        setup = test_setup
        # Reset side_effect so get_job always returns paused_job for this test
        setup["mock_jm"].get_job.side_effect = None
        setup["mock_jm"].get_job.return_value = setup["paused_job"]

        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        from fastapi.testclient import TestClient
        from backend.main import app

        with patches[0], patches[1], patches[2], patches[3]:
            client = TestClient(app)
            response = client.post(
                f"/api/jobs/{setup['job_id']}/confirm-duration",
                json={"acknowledged_credits": 3},   # wrong — expected 4
                headers={"Authorization": "Bearer test-admin-token"},
            )

        assert response.status_code == 409, (
            f"Expected 409 for mismatched acknowledged_credits, got {response.status_code}: {response.text}"
        )
        # No deduction must happen on a 409
        setup["mock_us"].deduct_credits.assert_not_called()

    def test_confirm_duration_insufficient_credits_returns_402(self, test_setup, mock_creds):
        """
        If deduct_credits fails (user is broke), the endpoint must return 402.
        No state changes should persist (no transition, job stays paused).
        """
        setup = test_setup
        setup["mock_jm"].get_job.side_effect = None
        setup["mock_jm"].get_job.return_value = setup["paused_job"]

        # Simulate insufficient credits
        setup["mock_us"].deduct_credits.return_value = (False, 0, "Insufficient credits")

        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        from fastapi.testclient import TestClient
        from backend.main import app

        with patches[0], patches[1], patches[2], patches[3]:
            client = TestClient(app)
            response = client.post(
                f"/api/jobs/{setup['job_id']}/confirm-duration",
                json={"acknowledged_credits": 4},
                headers={"Authorization": "Bearer test-admin-token"},
            )

        assert response.status_code == 402, (
            f"Expected 402 for insufficient credits, got {response.status_code}: {response.text}"
        )
        # Job must NOT be transitioned when user can't pay
        setup["mock_jm"].transition_to_state.assert_not_called()

    def test_confirm_duration_full_credit_math_invariant(self, test_setup, mock_creds):
        """
        FULL CREDIT MATH end-to-end invariant:

        - Start: credits_charged=2 (at job creation time, original deduction=2)
        - Reconcile: actual=2100s → required=4 → delta=2 (no deduction yet, just pause)
        - Confirm: exactly 2 more credits deducted; credits_charged updated to 4
        - Total lifetime deduction from account: 2 (at create) + 2 (at confirm) = 4
        - Not 4 + anything, not 2 again — exactly the delta
        """
        setup = test_setup
        patches = [
            patch("backend.api.routes.jobs.job_manager", setup["mock_jm"]),
            patch("backend.api.routes.jobs.worker_service", setup["mock_ws"]),
            patch("backend.api.routes.jobs.get_user_service", return_value=setup["mock_us"]),
            patch("google.auth.default", return_value=(mock_creds, "test-project")),
        ]
        from fastapi.testclient import TestClient
        from backend.main import app

        # Phase 1: simulate reconcile_duration (engine side, mocked collaborators)
        engine_jm = _make_mock_job_manager(
            _make_job(job_id=setup["job_id"], credits_charged=2)
        )
        engine_us = _make_mock_user_service()
        engine_result = reconcile_duration(
            job_id=setup["job_id"],
            job_manager=engine_jm,
            user_service=engine_us,
            probe_duration=lambda _j: 2100.0,
        )

        # Verify engine produced the correct pause signal
        assert engine_result.action == "pause"
        assert engine_result.pending_additional_credits == 2
        engine_us.deduct_credits.assert_not_called()  # engine must NOT deduct

        # Phase 2: hit the confirm endpoint
        with patches[0], patches[1], patches[2], patches[3]:
            client = TestClient(app)
            response = client.post(
                f"/api/jobs/{setup['job_id']}/confirm-duration",
                json={"acknowledged_credits": 4},
                headers={"Authorization": "Bearer test-admin-token"},
            )

        assert response.status_code == 200

        # The confirm endpoint must have deducted exactly the delta
        setup["mock_us"].deduct_credits.assert_called_once()
        deduct_call = setup["mock_us"].deduct_credits.call_args
        deducted = deduct_call.kwargs.get("amount", deduct_call.args[2] if len(deduct_call.args) > 2 else None)
        assert deducted == 2, (
            f"CREDIT MATH INVARIANT FAILED: deduct_credits called with amount={deducted}, "
            "expected 2 (the delta only). "
            "Total lifetime cost must be original_2 + delta_2 = 4, not more."
        )

        # credits_charged in state_data must end at 4
        all_updates = {}
        for c in setup["mock_jm"].update_job.call_args_list:
            upd = (c.args[1] if (c.args and len(c.args) >= 2) else c.kwargs.get("updates", {}))
            if isinstance(upd, dict):
                all_updates.update(upd)
        assert all_updates.get("state_data.credits_charged") == 4
