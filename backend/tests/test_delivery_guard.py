"""Unit tests for backend.services.delivery_guard (NOMAD-1800 Dropbox-full delivery guard)."""
from datetime import datetime, UTC
from unittest.mock import MagicMock, Mock, patch

import pytest

from backend.models.job import Job, JobStatus
from backend.services import delivery_guard as dg


INSUFFICIENT_SPACE_WARNING = (
    "Dropbox upload failed: ApiError('abc', UploadError('path', "
    "UploadWriteFailed(reason=WriteError('insufficient_space', None))))"
)


def _job(**overrides) -> Job:
    base = dict(
        job_id="dd892383",
        status=JobStatus.COMPLETE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        user_email="madeforyou@nomadkaraoke.com",
        artist="Test Artist",
        title="Test Song",
        dropbox_path="/Karaoke/Tracks-Organized",
        brand_prefix="NOMAD",
        state_data={"brand_code": "NOMAD-1800"},
    )
    base.update(overrides)
    return Job(**base)


# ---------------------------------------------------------------------------
# classify_dropbox_failure
# ---------------------------------------------------------------------------

class TestClassifyDropboxFailure:
    def test_insufficient_space_is_dropbox_full(self):
        code, reason = dg.classify_dropbox_failure([INSUFFICIENT_SPACE_WARNING])
        assert code == dg.REASON_DROPBOX_FULL
        assert "FULL" in reason

    def test_insufficient_space_found_among_other_warnings(self):
        code, _ = dg.classify_dropbox_failure([
            "Google Drive upload failed: boom",
            INSUFFICIENT_SPACE_WARNING,
        ])
        assert code == dg.REASON_DROPBOX_FULL

    def test_other_dropbox_error_is_upload_failed_with_detail(self):
        code, reason = dg.classify_dropbox_failure(["Dropbox upload failed: InternalServerError 500"])
        assert code == dg.REASON_DROPBOX_UPLOAD_FAILED
        assert "500" in reason

    def test_long_detail_is_truncated(self):
        _, reason = dg.classify_dropbox_failure(["Dropbox upload failed: " + "x" * 1000])
        assert len(reason) < 320

    def test_no_dropbox_warning_is_link_missing(self):
        code, _ = dg.classify_dropbox_failure(["Google Drive upload failed: boom"])
        assert code == dg.REASON_DROPBOX_LINK_MISSING
        assert dg.classify_dropbox_failure(None)[0] == dg.REASON_DROPBOX_LINK_MISSING


# ---------------------------------------------------------------------------
# completion_email_hold_reason
# ---------------------------------------------------------------------------

class TestCompletionEmailHoldReason:
    def test_holds_when_dropbox_expected_and_link_missing(self):
        job = _job(state_data={"distribution_warnings": [INSUFFICIENT_SPACE_WARNING]})
        hold = dg.completion_email_hold_reason(job)
        assert hold == {"reason_code": dg.REASON_DROPBOX_FULL, "reason": "Dropbox is FULL (insufficient_space)"}

    def test_no_hold_when_link_present(self):
        job = _job(state_data={"dropbox_link": "https://dropbox.com/x"})
        assert dg.completion_email_hold_reason(job) is None

    def test_no_hold_when_no_dropbox_configured(self):
        """Jobs that never deliver to Dropbox (e.g. tenant/no-dropbox) still email."""
        job = _job(dropbox_path=None, brand_prefix=None, state_data={})
        assert dg.completion_email_hold_reason(job) is None

    def test_no_hold_for_tenant_job_without_dropbox(self):
        job = _job(tenant_id="acme", is_private=True, dropbox_path=None, brand_prefix=None, state_data={})
        assert dg.completion_email_hold_reason(job) is None

    def test_no_hold_when_dropbox_path_but_no_brand_prefix(self):
        """Workers only upload when BOTH are set — mirror that."""
        job = _job(brand_prefix=None, state_data={})
        assert dg.completion_email_hold_reason(job) is None

    def test_private_job_uses_private_dropbox_defaults(self):
        settings = MagicMock(default_private_dropbox_path="/Karaoke/Tracks-NonPublished",
                             default_private_brand_prefix="NOMADNP")
        job = _job(is_private=True, dropbox_path=None, brand_prefix=None, state_data={})
        with patch("backend.services.job_defaults_service.get_settings", return_value=settings):
            hold = dg.completion_email_hold_reason(job)
        assert hold["reason_code"] == dg.REASON_DROPBOX_LINK_MISSING

    def test_private_job_without_private_dropbox_configured_does_not_hold(self):
        settings = MagicMock(default_private_dropbox_path=None, default_private_brand_prefix="NOMADNP")
        job = _job(is_private=True, dropbox_path=None, brand_prefix=None, state_data={})
        with patch("backend.services.job_defaults_service.get_settings", return_value=settings):
            assert dg.completion_email_hold_reason(job) is None

    def test_settings_error_fails_open(self):
        job = _job(state_data={})
        with patch("backend.services.job_defaults_service.get_effective_distribution_for_job",
                   side_effect=RuntimeError("boom")):
            assert dg.completion_email_hold_reason(job) is None


# ---------------------------------------------------------------------------
# hold_completion_email + alert text
# ---------------------------------------------------------------------------

class TestHoldCompletionEmail:
    def test_records_marker_and_sends_one_per_job_alert(self):
        job = _job(state_data={"brand_code": "NOMAD-1800",
                               "distribution_warnings": [INSUFFICIENT_SPACE_WARNING]})
        hold = dg.completion_email_hold_reason(job)
        jm = Mock()
        jm.update_job.return_value = None

        with patch("backend.services.ops_alerts.send_throttled_ops_alert", return_value=True) as send:
            dg.hold_completion_email(jm, job, hold, "christian@example.de")

        jm.update_job.assert_called_once()
        job_id, updates = jm.update_job.call_args[0]
        assert job_id == "dd892383"
        marker = updates["state_data.completion_email_held"]
        assert marker["reason_code"] == dg.REASON_DROPBOX_FULL
        assert marker["recipient"] == "christian@example.de"
        assert marker["at"]

        send.assert_called_once()
        key, message = send.call_args[0]
        assert key == "delivery-held-dd892383"
        assert send.call_args.kwargs["throttle_minutes"] >= 24 * 60
        assert "dd892383" in message and "NOMAD-1800" in message
        assert "Dropbox is FULL" in message
        assert "NOT sent" in message
        assert "free space" in message
        assert "send-completion-email" in message
        # Customer email is masked in #alerts
        assert "christian@example.de" not in message

    def test_firestore_and_alert_failures_never_raise(self):
        job = _job(state_data={})
        jm = Mock()
        jm.update_job.side_effect = RuntimeError("firestore down")
        with patch("backend.services.ops_alerts.send_throttled_ops_alert", side_effect=RuntimeError("x")):
            dg.hold_completion_email(jm, job, {"reason_code": "x", "reason": "y"}, None)

    def test_non_full_alert_points_at_credentials(self):
        job = _job(state_data={})
        msg = dg.build_held_delivery_alert(
            job, {"reason_code": dg.REASON_DROPBOX_UPLOAD_FAILED, "reason": "500"}, None
        )
        assert "credentials" in msg
        assert "(no recipient)" in msg


# ---------------------------------------------------------------------------
# Quota check
# ---------------------------------------------------------------------------

TIB = 1024 ** 4


def _team_usage(used, allocated):
    from dropbox.users import SpaceAllocation, SpaceUsage, TeamSpaceAllocation

    team = TeamSpaceAllocation(
        used=used, allocated=allocated, user_within_team_space_allocated=0,
        user_within_team_space_used_cached=0,
    )
    return SpaceUsage(used=1, allocation=SpaceAllocation.team(team))


def _individual_usage(used, allocated):
    from dropbox.users import IndividualSpaceAllocation, SpaceAllocation, SpaceUsage

    return SpaceUsage(used=used, allocation=SpaceAllocation.individual(
        IndividualSpaceAllocation(allocated=allocated)))


class TestSpaceUsageBytes:
    def test_team_allocation_uses_team_totals(self):
        assert dg.space_usage_bytes(_team_usage(6 * TIB, 6 * TIB)) == (6 * TIB, 6 * TIB)

    def test_individual_allocation(self):
        assert dg.space_usage_bytes(_individual_usage(10, 100)) == (10, 100)


class TestEvaluateQuota:
    @pytest.mark.parametrize("used,allocated,was_alerting,expected", [
        (94, 100, False, "none"),
        (95, 100, False, "alert"),      # threshold is inclusive
        (100, 100, False, "alert"),
        (99, 100, True, "none"),        # still high: don't re-page every run
        (90, 100, True, "recovered"),
        (50, 100, False, "none"),
        (10, 0, False, "none"),         # unknown allocation
    ])
    def test_threshold_logic(self, used, allocated, was_alerting, expected):
        assert dg.evaluate_quota(used, allocated, was_alerting) == expected


class _FakeDoc:
    def __init__(self, data=None):
        self.data = data
        self.writes = []

    def get(self):
        snap = Mock()
        snap.exists = self.data is not None
        snap.to_dict.return_value = dict(self.data or {})
        return snap

    def set(self, data, merge=False):
        self.writes.append(data)
        self.data = {**(self.data or {}), **data}


def _db_with(doc):
    db = MagicMock()
    db.collection.return_value.document.return_value = doc
    return db


def _dropbox(usage):
    svc = MagicMock()
    svc.is_configured = True
    svc.client.users_get_space_usage.return_value = usage
    return svc


class TestCheckDropboxSpaceUsage:
    def test_alerts_once_when_crossing_threshold(self):
        doc = _FakeDoc()
        svc = _dropbox(_team_usage(int(6.001 * TIB), 6 * TIB))
        with patch("backend.services.ops_alerts.send_ops_alert", return_value=True) as send:
            first = dg.check_dropbox_space_usage(db=_db_with(doc), dropbox_service=svc)
            second = dg.check_dropbox_space_usage(db=_db_with(doc), dropbox_service=svc)

        assert first["action"] == "alert" and first["alerting"] is True
        assert second["action"] == "none"
        send.assert_called_once()
        assert "Dropbox almost FULL" in send.call_args[0][0]
        assert doc.data["alerting"] is True

    def test_failed_send_does_not_latch(self):
        doc = _FakeDoc()
        svc = _dropbox(_team_usage(99, 100))
        with patch("backend.services.ops_alerts.send_ops_alert", return_value=False) as send:
            dg.check_dropbox_space_usage(db=_db_with(doc), dropbox_service=svc)
            dg.check_dropbox_space_usage(db=_db_with(doc), dropbox_service=svc)
        assert send.call_count == 2
        assert doc.data["alerting"] is False

    def test_rearms_after_recovery(self):
        doc = _FakeDoc({"alerting": True})
        with patch("backend.services.ops_alerts.send_ops_alert", return_value=True) as send:
            r = dg.check_dropbox_space_usage(db=_db_with(doc), dropbox_service=_dropbox(_team_usage(50, 100)))
            assert r["action"] == "recovered"
            assert doc.data["alerting"] is False
            r = dg.check_dropbox_space_usage(db=_db_with(doc), dropbox_service=_dropbox(_team_usage(97, 100)))
            assert r["action"] == "alert"
        send.assert_called_once()

    def test_below_threshold_no_alert(self):
        doc = _FakeDoc()
        with patch("backend.services.ops_alerts.send_ops_alert") as send:
            r = dg.check_dropbox_space_usage(db=_db_with(doc), dropbox_service=_dropbox(_team_usage(10, 100)))
        assert r == {"status": "ok", "used_pct": 10.0, "action": "none", "alerting": False}
        send.assert_not_called()

    def test_skips_when_dropbox_not_configured(self):
        svc = MagicMock()
        svc.is_configured = False
        assert dg.check_dropbox_space_usage(db=MagicMock(), dropbox_service=svc)["status"] == "skipped"

    def test_api_error_never_raises(self):
        svc = MagicMock()
        svc.is_configured = True
        svc.client.users_get_space_usage.side_effect = RuntimeError("api down")
        assert dg.check_dropbox_space_usage(db=MagicMock(), dropbox_service=svc)["status"] == "error"
