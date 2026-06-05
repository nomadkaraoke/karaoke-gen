import pytest
from unittest.mock import MagicMock, call
from backend.services.duration_reconciliation import reconcile_duration, ReconcileResult


def _job(credits_charged=2, gcs="gs://b/audio.flac", email="u@test.com", payment_bypassed=False):
    job = MagicMock()
    job.id = "job1"
    job.user_email = email
    job.artist = "Test Artist"
    job.title = "Test Song"
    job.input_media_gcs_path = gcs
    state = {"credits_charged": credits_charged}
    if payment_bypassed:
        state["payment_bypassed"] = True
    job.state_data = state
    return job


def _ctx(actual_seconds, credits_charged=2):
    job_manager = MagicMock()
    job_manager.get_job.return_value = _job(credits_charged=credits_charged)
    user_service = MagicMock()
    user_service.add_credits.return_value = (True, 99, "ok")
    user_service.deduct_credits.return_value = (True, 1, "ok")
    probe = MagicMock(return_value=actual_seconds)
    return job_manager, user_service, probe


def test_equal_proceeds():
    jm, us, probe = _ctx(actual_seconds=900, credits_charged=2)  # 15min -> 2 credits
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed"
    us.add_credits.assert_not_called()
    us.deduct_credits.assert_not_called()


def test_shorter_auto_refunds_and_proceeds():
    jm, us, probe = _ctx(actual_seconds=300, credits_charged=2)  # 5min -> 1 credit, refund 1
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed"
    us.add_credits.assert_called_once()
    _, kwargs = us.add_credits.call_args
    assert kwargs.get("amount") == 1
    assert "refund" in (kwargs.get("reason", "") or "")


def test_longer_pauses_for_reconfirm():
    jm, us, probe = _ctx(actual_seconds=1800, credits_charged=2)  # 30min -> 3 credits, +1 owed
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "pause"
    assert result.pending_additional_credits == 1
    jm.transition_to_state.assert_called_once()
    us.deduct_credits.assert_not_called()


def test_over_limit_refunds_all_and_cancels():
    jm, us, probe = _ctx(actual_seconds=4000, credits_charged=2)  # >60min

    # Track call order via a parent mock
    parent = MagicMock()
    parent.attach_mock(jm.update_job, "update_job")
    parent.attach_mock(jm.cancel_job, "cancel_job")

    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "cancel"

    # Full refund called exactly once with the right amount
    us.add_credits.assert_called_once()
    _, kwargs = us.add_credits.call_args
    assert kwargs.get("amount") == 2  # full refund

    # Suppression flag must be set BEFORE cancel_job so cancel_job's built-in
    # _refund_credit_for_job guard sees credit_refunded=True and skips.
    calls = parent.mock_calls
    update_suppression = next(
        (c for c in calls
         if c[0] == "update_job" and len(c[1]) >= 2 and isinstance(c[1][1], dict) and c[1][1].get("credit_refunded") is True),
        None,
    )
    assert update_suppression is not None, "update_job({'credit_refunded': True}) was never called"
    cancel_idx = next(i for i, c in enumerate(calls) if c[0] == "cancel_job")
    suppress_idx = calls.index(update_suppression)
    assert suppress_idx < cancel_idx, (
        "update_job(credit_refunded=True) must be called BEFORE cancel_job"
    )

    jm.cancel_job.assert_called_once()


def test_probe_none_proceeds_without_charge_change():
    jm, us, probe = _ctx(actual_seconds=None, credits_charged=2)
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed"
    us.add_credits.assert_not_called()
    us.deduct_credits.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 1 regression: payment_bypassed jobs must never pause/charge/refund/cancel
# ---------------------------------------------------------------------------

def _bypassed_job(actual_seconds, credits_charged=0):
    """Return (job_manager, user_service, probe) for a payment-bypassed job."""
    job = _job(credits_charged=credits_charged, payment_bypassed=True)
    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    user_service = MagicMock()
    user_service.add_credits.return_value = (True, 99, "ok")
    probe = MagicMock(return_value=actual_seconds)
    return job_manager, user_service, probe


def test_payment_bypassed_long_duration_proceeds_without_pause():
    """Bug 1: admin/payment-bypassed job whose duration exceeds estimate must NOT pause."""
    jm, us, probe = _bypassed_job(actual_seconds=1800, credits_charged=0)  # 30min -> would be +3 credits
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed", (
        f"payment_bypassed job must proceed, got {result.action!r}"
    )
    jm.transition_to_state.assert_not_called()
    us.add_credits.assert_not_called()
    us.deduct_credits.assert_not_called()


def test_payment_bypassed_over_limit_proceeds_without_cancel():
    """Bug 1: payment-bypassed job >60min must NOT be cancelled (admin bypass)."""
    jm, us, probe = _bypassed_job(actual_seconds=4000, credits_charged=0)  # >60min
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed", (
        f"payment_bypassed job >60min must proceed, got {result.action!r}"
    )
    jm.cancel_job.assert_not_called()
    us.add_credits.assert_not_called()


def test_payment_bypassed_short_duration_proceeds_without_refund():
    """Bug 1: payment-bypassed job shorter than estimate must NOT trigger a credits refund."""
    jm, us, probe = _bypassed_job(actual_seconds=300, credits_charged=0)  # 5min
    result = reconcile_duration("job1", jm, us, probe)
    assert result.action == "proceed"
    us.add_credits.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 2 regression: over-limit cancellation email called with correct kwargs
# ---------------------------------------------------------------------------

def test_over_limit_sends_email_with_correct_kwargs():
    """Bug 2: over-limit cancel must call email_service with named kwargs, not positional job arg."""
    jm, us, probe = _ctx(actual_seconds=4000, credits_charged=2)  # >60min
    email_service = MagicMock()
    email_service.send_duration_confirm_expired.return_value = True

    result = reconcile_duration("job1", jm, us, probe, email_service=email_service)
    assert result.action == "cancel"

    email_service.send_duration_confirm_expired.assert_called_once()
    _, kwargs = email_service.send_duration_confirm_expired.call_args
    assert kwargs.get("to_email") == "u@test.com", f"Expected to_email=u@test.com, got {kwargs}"
    assert kwargs.get("credits_refunded") == 2, f"Expected credits_refunded=2, got {kwargs}"


def test_over_limit_no_email_when_email_service_is_none():
    """Bug 2: when email_service is None, no crash on over-limit path."""
    jm, us, probe = _ctx(actual_seconds=4000, credits_charged=2)
    # Should not raise
    result = reconcile_duration("job1", jm, us, probe, email_service=None)
    assert result.action == "cancel"
