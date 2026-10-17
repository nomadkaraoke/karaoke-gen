"""credits_charged must be recorded at creation and read the same way everywhere.

Regression for the mobile local-stack E2E finding (2026-10-17): POST
/api/jobs/create-from-search charged 1 credit in create_job, then replaced the
whole ``state_data`` map with the search results — wiping ``credits_charged``.
Reconcile then read the missing field as 1 (pending=1 for a 15-min track, total 2)
while confirm-duration read it as 0 (expected acknowledged_credits == 1), so a
client acknowledging the true total (2) got 409, the web hook displayed "1 credit
in total", and a later cancel/expiry under-refunded.

Fix: create paths merge into state_data (dotted paths), and every reader uses
backend.services.job_credits.get_credits_charged.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.models.job import Job, JobStatus
from backend.services.auth_service import AuthResult, UserType
from backend.services.duration_reconciliation import reconcile_duration
from backend.services.job_credits import (
    get_credits_charged,
    is_payment_bypassed,
    preserve_billing_state,
)

NOW = datetime.now(UTC)
FIFTEEN_MIN = 900.0  # duration_to_credits(900) == 2


def _job(state_data=None, **kw) -> Job:
    defaults = dict(
        job_id="job-cc-1",
        created_at=NOW,
        updated_at=NOW,
        user_email="owner@example.com",
        status=JobStatus.DOWNLOADING,
        state_data=state_data if state_data is not None else {},
    )
    defaults.update(kw)
    return Job(**defaults)


def apply_firestore_update(job: Job, updates: dict) -> None:
    """Mimic Firestore update(): dotted ``state_data.x`` merges, bare ``state_data`` replaces."""
    for key, value in updates.items():
        if key.startswith("state_data."):
            job.state_data = {**(job.state_data or {}), key.split(".", 1)[1]: value}
        elif key == "state_data":
            job.state_data = dict(value)
        elif hasattr(job, key):
            setattr(job, key, value)


class FakeJobManager:
    """Single-job store applying updates with Firestore semantics."""

    def __init__(self, job: Job):
        self.job = job

    def get_job(self, job_id):
        return self.job

    def update_job(self, job_id, updates):
        apply_firestore_update(self.job, updates)

    def transition_to_state(self, job_id, new_status, progress=None, message=None, **kw):
        self.job.status = new_status
        return True

    def cancel_job(self, job_id, reason=None):
        self.job.status = JobStatus.CANCELLED
        return True


# ---------------------------------------------------------------------------
# Helper semantics
# ---------------------------------------------------------------------------


class TestGetCreditsCharged:
    def test_explicit_value_wins(self):
        assert get_credits_charged(_job({"credits_charged": 3})) == 3
        assert get_credits_charged(_job({"credits_charged": 0})) == 0

    def test_legacy_standard_job_defaults_to_one(self):
        assert get_credits_charged(_job({})) == 1
        assert get_credits_charged(_job(None)) == 1
        assert not is_payment_bypassed(_job({}))

    def test_bypass_flag_means_zero(self):
        job = _job({"payment_bypassed": True})
        assert get_credits_charged(job) == 0
        assert is_payment_bypassed(job)

    @pytest.mark.parametrize(
        "kw",
        [
            {"user_email": None},
            {"tenant_id": "acme"},
            {"creation_params": {"is_admin": True}},
            {"user_email": "andrew@nomadkaraoke.com"},
        ],
    )
    def test_legacy_unbilled_jobs_default_to_zero(self, kw):
        job = _job({}, **kw)
        assert get_credits_charged(job) == 0
        assert is_payment_bypassed(job)

    def test_explicit_charge_is_not_bypassed_even_for_tenant(self):
        # Explicit field is authoritative; inference only applies when missing.
        assert not is_payment_bypassed(_job({"credits_charged": 1}, tenant_id="acme"))

    def test_preserve_billing_state(self):
        sd = {"credits_charged": 2, "select_charge_applied": True, "screens_progress": {"x": 1}}
        assert preserve_billing_state(sd) == {"credits_charged": 2, "select_charge_applied": True}
        assert preserve_billing_state(None) == {}


# ---------------------------------------------------------------------------
# create-from-search records credits_charged
# ---------------------------------------------------------------------------


def _search_session():
    now = datetime.utcnow()
    from datetime import timedelta

    return {
        "session_id": "sess-1",
        "user_email": "test@example.com",
        "tenant_id": None,
        "artist": "ABBA",
        "title": "Waterloo",
        "results": [{"index": 0, "title": "Waterloo", "artist": "ABBA",
                     "provider": "YouTube", "url": "https://youtube.com/watch?v=abc"}],
        "remote_search_id": "remote-9",
        "created_at": now.isoformat(),
        "ttl_expiry": now + timedelta(days=7),
    }


class TestCreateFromSearchRecordsCreditsCharged:
    def _run(self, auth_headers, created_state):
        job = _job(dict(created_state), job_id="test-job-123", status=JobStatus.PENDING)
        jm = MagicMock()
        jm.create_job.return_value = job
        jm.get_job.return_value = job
        jm.update_job.side_effect = lambda job_id, updates: apply_firestore_update(job, updates)

        session = _search_session()
        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = session
        mock_firestore.consume_search_session.return_value = session
        theme = MagicMock()
        theme.get_default_theme_id.return_value = None
        worker = AsyncMock()
        worker.trigger_audio_download_worker.return_value = True
        creds = MagicMock()
        creds.universe_domain = "googleapis.com"

        with patch("backend.api.routes.jobs.job_manager", jm), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=theme), \
             patch("backend.api.routes.jobs.get_worker_service", return_value=worker), \
             patch("backend.api.routes.audio_search._validate_and_prepare_selection"), \
             patch("backend.api.routes.audio_search.extract_request_metadata", return_value={}), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(creds, "test-project")):
            from backend.main import app

            resp = TestClient(app).post(
                "/api/jobs/create-from-search",
                json={"search_session_id": "sess-1", "selection_index": 0,
                      "artist": "ABBA", "title": "Waterloo", "requires_audio_edit": True},
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text
        for c in jm.update_job.call_args_list:
            assert "state_data" not in c.args[1], "must merge (dotted paths), never replace state_data"
        return job

    def test_charged_job_keeps_credits_charged_1(self, auth_headers):
        job = self._run(auth_headers, {"credits_charged": 1})
        assert job.state_data["credits_charged"] == 1
        assert get_credits_charged(job) == 1
        # search data still stored
        assert job.state_data["audio_search_count"] == 1
        assert job.state_data["remote_search_id"] == "remote-9"
        assert job.state_data["requires_audio_edit"] is True

    def test_bypassed_job_keeps_payment_bypassed(self, auth_headers):
        job = self._run(auth_headers, {"credits_charged": 0, "payment_bypassed": True})
        assert job.state_data["payment_bypassed"] is True
        assert job.state_data["credits_charged"] == 0


class TestCreateJobRecordsCharge:
    """create_job is the single place that debits at creation; it records the amount."""

    def _jm(self):
        from backend.services.job_manager import JobManager

        jm = JobManager.__new__(JobManager)
        jm.firestore = MagicMock()
        return jm

    def test_standard_job_records_credits_charged(self):
        from backend.models.job import JobCreate

        jm = self._jm()
        us = MagicMock()
        us.check_credits.return_value = 5
        us.deduct_credits.return_value = (True, 4, "ok")
        with patch("backend.services.user_service.get_user_service", return_value=us):
            job = jm.create_job(JobCreate(artist="A", title="T", user_email="u@example.com",
                                          theme_id="nomad"))
        assert job.state_data == {"credits_charged": 1}
        assert us.deduct_credits.call_args.kwargs["amount"] == 1

    def test_admin_job_records_zero_and_bypass(self):
        from backend.models.job import JobCreate

        jm = self._jm()
        job = jm.create_job(JobCreate(artist="A", title="T", user_email="u@example.com",
                                      theme_id="nomad"), is_admin=True)
        assert job.state_data == {"credits_charged": 0, "payment_bypassed": True}


# ---------------------------------------------------------------------------
# reconcile → confirm-duration → cancel agree (new + legacy)
# ---------------------------------------------------------------------------


@pytest.fixture
def owner_auth():
    from backend.api.dependencies import require_auth
    from backend.main import app

    async def _auth():
        return AuthResult(is_valid=True, user_type=UserType.ADMIN, remaining_uses=999,
                          message="t", is_admin=True, user_email="owner@example.com")

    app.dependency_overrides[require_auth] = _auth
    yield
    app.dependency_overrides.pop(require_auth, None)


def _reconcile_15min(job: Job):
    fjm = FakeJobManager(job)
    us = MagicMock()
    us.add_credits.return_value = (True, 5, "ok")
    result = reconcile_duration(job.job_id, fjm, us, lambda _j: FIFTEEN_MIN)
    return fjm, us, result


def _confirm(job: Job, fjm: FakeJobManager, acknowledged: int):
    from backend.main import app

    us = MagicMock()
    us.deduct_credits.return_value = (True, 3, "ok")
    ws = MagicMock()
    ws.trigger_audio_worker = AsyncMock(return_value=True)
    ws.trigger_lyrics_worker = AsyncMock(return_value=True)
    with patch("backend.api.routes.jobs.job_manager", fjm), \
         patch("backend.api.routes.jobs.get_user_service", return_value=us), \
         patch("backend.api.routes.jobs.get_worker_service", return_value=ws):
        resp = TestClient(app).post(f"/api/jobs/{job.job_id}/confirm-duration",
                                    json={"acknowledged_credits": acknowledged})
    return resp, us


def _cancel_refund_amount(job: Job) -> int:
    from backend.services.job_manager import JobManager

    jm = JobManager.__new__(JobManager)
    jm.firestore = MagicMock()
    us = MagicMock()
    us.add_credits.return_value = (True, 9, "ok")
    with patch("backend.services.user_service.get_user_service", return_value=us):
        assert jm._refund_credit_for_job(job.job_id, job, reason="job_cancelled") is True
    return us.add_credits.call_args.kwargs["amount"]


@pytest.mark.parametrize(
    "initial_state",
    [{"credits_charged": 1}, {}],
    ids=["new-job-with-field", "legacy-job-missing-field"],
)
class TestReconcileConfirmCancelAgree:
    def test_15min_track_total_two_confirm_and_cancel_refund_two(self, owner_auth, initial_state):
        job = _job(dict(initial_state))
        fjm, _, result = _reconcile_15min(job)

        assert result.action == "pause"
        assert result.pending_additional_credits == 1
        # Reconcile persists the resolved charge so every client sees it explicitly.
        assert job.state_data["credits_charged"] == 1
        assert job.state_data["pending_additional_credits"] == 1

        # The true total (what web + mobile display/acknowledge) is accepted.
        resp, us = _confirm(job, fjm, acknowledged=2)
        assert resp.status_code == 200, resp.text
        assert us.deduct_credits.call_args.kwargs["amount"] == 1
        assert job.state_data["credits_charged"] == 2
        assert job.state_data["pending_additional_credits"] == 0

        # Cancelling afterwards refunds everything the user paid.
        assert _cancel_refund_amount(job) == 2

    def test_stale_total_is_rejected(self, owner_auth, initial_state):
        job = _job(dict(initial_state))
        fjm, _, _ = _reconcile_15min(job)
        resp, us = _confirm(job, fjm, acknowledged=1)
        assert resp.status_code == 409
        us.deduct_credits.assert_not_called()


class TestConfirmLegacyAlreadyPaused:
    """Jobs paused BEFORE this fix lack credits_charged entirely (only pending set)."""

    def test_true_total_accepted_and_charge_recorded(self, owner_auth):
        job = _job({"duration_confirm_reason": "reconcile", "pending_additional_credits": 1},
                   status=JobStatus.AWAITING_DURATION_CONFIRM)
        fjm = FakeJobManager(job)
        resp, us = _confirm(job, fjm, acknowledged=2)  # (credits_charged ?? 1) + pending
        assert resp.status_code == 200, resp.text
        assert us.deduct_credits.call_args.kwargs["amount"] == 1
        assert job.state_data["credits_charged"] == 2
        assert _cancel_refund_amount(job) == 2

    def test_old_zero_based_total_now_rejected(self, owner_auth):
        job = _job({"duration_confirm_reason": "reconcile", "pending_additional_credits": 1},
                   status=JobStatus.AWAITING_DURATION_CONFIRM)
        resp, us = _confirm(job, FakeJobManager(job), acknowledged=1)
        assert resp.status_code == 409
        us.deduct_credits.assert_not_called()


class TestRefundAmountsUnchanged:
    """Refund amounts for jobs that carry the field are unchanged by the helper."""

    @pytest.mark.parametrize("charged", [1, 2, 3])
    def test_explicit_charge_refunded_in_full(self, charged):
        assert _cancel_refund_amount(_job({"credits_charged": charged})) == charged

    def test_legacy_standard_job_still_refunds_one(self):
        assert _cancel_refund_amount(_job({})) == 1

    def test_bypassed_job_not_refunded(self):
        from backend.services.job_manager import JobManager

        jm = JobManager.__new__(JobManager)
        jm.firestore = MagicMock()
        us = MagicMock()
        with patch("backend.services.user_service.get_user_service", return_value=us):
            assert jm._refund_credit_for_job("j", _job({"payment_bypassed": True}), "job_cancelled") is False
        us.add_credits.assert_not_called()

    def test_reconcile_skips_legacy_bypassed_job(self):
        # Made-for-you jobs (admin-owned) lost payment_bypassed the same way; they
        # must never be paused/billed by reconcile.
        job = _job({}, user_email="admin@nomadkaraoke.com")
        _, us, result = _reconcile_15min(job)
        assert result.action == "proceed"
        us.add_credits.assert_not_called()
        assert job.status == JobStatus.DOWNLOADING


class TestRetryKeepsBillingState:
    def test_preserved_keys_survive_reset(self):
        job = _job({"credits_charged": 3, "select_charge_applied": True, "screens_progress": {}})
        apply_firestore_update(job, {"state_data": preserve_billing_state(job.state_data)})
        assert job.state_data == {"credits_charged": 3, "select_charge_applied": True}
        assert get_credits_charged(job) == 3
