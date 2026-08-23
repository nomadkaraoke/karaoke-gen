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


# ---------------------------------------------------------------------------
# Bug C regression: over-limit cancel uses reason="over_limit" (not "timeout")
# ---------------------------------------------------------------------------

def test_over_limit_sends_email_with_over_limit_reason():
    """
    Bug C: over-limit immediate cancellation must send send_duration_confirm_expired
    with reason='over_limit', NOT the default reason='timeout' which says
    '48-hour expiry'.  The user sees the wrong cancellation message otherwise.
    """
    jm, us, probe = _ctx(actual_seconds=4000, credits_charged=2)  # >60min
    email_service = MagicMock()
    email_service.send_duration_confirm_expired.return_value = True

    result = reconcile_duration("job1", jm, us, probe, email_service=email_service)
    assert result.action == "cancel"

    email_service.send_duration_confirm_expired.assert_called_once()
    _, kwargs = email_service.send_duration_confirm_expired.call_args
    assert kwargs.get("reason") == "over_limit", (
        f"Expected reason='over_limit' for immediate over-60-min cancellation, "
        f"got reason={kwargs.get('reason')!r}. "
        "The user would see a misleading '48-hour expiry' message."
    )


def test_stale_48h_expiry_sends_email_with_timeout_reason():
    """
    The 48h stale path (stale_review_processor) calls send_duration_confirm_expired
    without a reason kwarg — which defaults to 'timeout'.  This test verifies that
    a 'timeout' reason (or no explicit reason) produces the correct timeout message
    in send_duration_confirm_expired, guarding against accidentally using 'over_limit'.

    This is a unit test of the email_service method, not the reconcile path.
    """
    import sys
    sys.modules.setdefault('google.cloud.firestore', MagicMock())
    sys.modules.setdefault('google.cloud.storage', MagicMock())

    from unittest.mock import patch, MagicMock as MM2
    with patch('backend.services.email_service.EmailService._build_email_html', return_value="html"), \
         patch('backend.services.email_service.EmailService.provider', create=True) as mock_provider:

        from backend.services.email_service import EmailService

        class _FakeProvider:
            def send_email(self, **kwargs):
                return True

        svc = EmailService.__new__(EmailService)
        svc.frontend_url = "https://gen.nomadkaraoke.com"
        svc.provider = _FakeProvider()
        svc._build_email_html = lambda content, styles, locale="en": content

        # Default reason (timeout) — should mention 48 hours, not 60 minutes
        import io, contextlib
        out = io.StringIO()

        # Capture text content by monkeypatching send_email
        sent = {}

        class _CapturingProvider:
            def send_email(self, to_email, subject, html_content, text_content=None, **kwargs):
                sent['subject'] = subject
                sent['text'] = text_content
                return True

        svc.provider = _CapturingProvider()
        svc.send_duration_confirm_expired(
            to_email="u@test.com", artist="A", title="B", credits_refunded=2
            # no reason= → default "timeout"
        )
        assert "48" in sent['subject'] or "48" in sent['text'] or "not confirmed" in sent['subject'], (
            f"Default (timeout) email should mention 48-hour expiry. subject={sent.get('subject')!r}"
        )
        assert "60" not in sent['subject'], (
            f"Default (timeout) email should not say '60 minutes'. subject={sent.get('subject')!r}"
        )

        # over_limit reason — should mention 60 minutes, not 48 hours
        svc.send_duration_confirm_expired(
            to_email="u@test.com", artist="A", title="B", credits_refunded=2, reason="over_limit"
        )
        assert "60" in sent['subject'], (
            f"over_limit email subject should mention 60 minutes. subject={sent.get('subject')!r}"
        )
        assert "48" not in sent['subject'] and "not confirmed" not in sent['subject'], (
            f"over_limit email should not say '48 hours'. subject={sent.get('subject')!r}"
        )


# ---------------------------------------------------------------------------
# Regression (job a453d1d5): a failing email-service construction must NOT
# crash an otherwise-successful audio download. reconcile_and_maybe_pause runs
# in the audio-download-job Cloud Run Job; EmailService() raises in production
# when POSTMARK_SERVER_TOKEN is unset. Reconcile must degrade to no-email.
# ---------------------------------------------------------------------------

def _patch_reconcile_singletons(monkeypatch, jm, us, actual_seconds):
    """Patch the function-scoped imports reconcile_and_maybe_pause pulls in.

    These are imported inside the function (to avoid circular imports), so they
    must be patched at their source modules, not on duration_reconciliation.
    """
    from backend.services import duration_reconciliation as dr
    monkeypatch.setattr("backend.services.job_manager.JobManager", lambda: jm)
    monkeypatch.setattr("backend.services.user_service.get_user_service", lambda: us)
    monkeypatch.setattr("backend.services.storage_service.StorageService", lambda: MagicMock())
    monkeypatch.setattr(
        "backend.services.email_service.get_email_service",
        MagicMock(side_effect=RuntimeError("POSTMARK_SERVER_TOKEN is not set in production")),
    )
    monkeypatch.setattr(dr, "_ffprobe_seconds", lambda job, storage: actual_seconds)


@pytest.mark.asyncio
async def test_reconcile_and_maybe_pause_survives_email_service_failure(monkeypatch):
    from backend.services import duration_reconciliation as dr

    jm = MagicMock()
    jm.get_job.return_value = _job(credits_charged=2)
    # Duration matches estimate → proceed, no email needed anyway.
    _patch_reconcile_singletons(monkeypatch, jm, MagicMock(), actual_seconds=900.0)  # 15min -> 2 credits

    # Must not raise despite email service construction failing.
    blocked = await dr.reconcile_and_maybe_pause("job1")
    assert blocked is False  # proceeds normally


@pytest.mark.asyncio
async def test_reconcile_and_maybe_pause_over_limit_still_cancels_without_email(monkeypatch):
    """Over-limit cancel must still refund+cancel even if email can't be built."""
    from backend.services import duration_reconciliation as dr

    jm = MagicMock()
    jm.get_job.return_value = _job(credits_charged=2)
    us = MagicMock()
    us.add_credits.return_value = (True, 99, "ok")
    _patch_reconcile_singletons(monkeypatch, jm, us, actual_seconds=4000.0)  # >60min

    blocked = await dr.reconcile_and_maybe_pause("job1")
    assert blocked is True  # cancelled
    jm.cancel_job.assert_called_once()
    us.add_credits.assert_called_once()  # full refund still happens
