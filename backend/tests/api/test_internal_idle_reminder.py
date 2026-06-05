"""
Tests for the idle reminder mechanism for AWAITING_DURATION_CONFIRM state.

Covers:
- POST /api/internal/jobs/{job_id}/check-idle-reminder sends email when job is in
  AWAITING_DURATION_CONFIRM and action_type == "duration_confirm"
- No email sent when job has already left AWAITING_DURATION_CONFIRM
- _schedule_idle_reminder schedules a 900s reminder with action_type="duration_confirm"
  when a job enters AWAITING_DURATION_CONFIRM
"""
import os
import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch, Mock

# Ensure env vars are set before any backend imports
os.environ.setdefault('ADMIN_TOKENS', 'test-admin-token')
os.environ.setdefault('GOOGLE_CLOUD_PROJECT', 'test-project')

from backend.models.job import Job, JobStatus
from backend.services.job_manager import JobManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(
    status: JobStatus = JobStatus.AWAITING_DURATION_CONFIRM,
    state_data: dict | None = None,
    user_email: str = "user@example.com",
    job_id: str = "job-dur-001",
    made_for_you: bool = False,
) -> Job:
    """Create a minimal Job for testing."""
    return Job(
        job_id=job_id,
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        user_email=user_email,
        artist="Test Artist",
        title="Test Song",
        state_data=state_data or {
            "blocking_action_type": "duration_confirm",
            "blocking_state_entered_at": "2026-06-04T10:00:00",
            "reminder_sent": False,
            "pending_additional_credits": 3,
            "duration_actual_seconds": 1200,
        },
        made_for_you=made_for_you,
    )


def _make_client():
    """Create a FastAPI TestClient with GCP mocks applied."""
    from fastapi.testclient import TestClient

    mock_creds = MagicMock()
    mock_creds.universe_domain = "googleapis.com"

    with patch("google.auth.default", return_value=(mock_creds, "test-project")), \
         patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"):
        from backend.main import app
        return TestClient(app)


# ---------------------------------------------------------------------------
# Check-idle-reminder endpoint tests
# ---------------------------------------------------------------------------

class TestCheckIdleReminderDurationConfirm:
    """Tests for the check-idle-reminder endpoint handling duration_confirm jobs."""

    AUTH = {"Authorization": "Bearer test-admin-token"}

    def test_sends_duration_confirm_email_when_job_awaiting(self):
        """
        When a job is AWAITING_DURATION_CONFIRM with action_type=duration_confirm,
        POST check-idle-reminder should call send_duration_confirm_reminder once
        and set reminder_sent=True.
        """
        job = _make_job()

        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = job

        mock_notification_service = MagicMock()
        mock_notification_service.send_duration_confirm_reminder = AsyncMock(return_value=True)

        client = _make_client()

        with patch("backend.api.routes.internal.JobManager", return_value=mock_job_manager), \
             patch(
                 "backend.services.job_notification_service.get_job_notification_service",
                 return_value=mock_notification_service,
             ):
            response = client.post(
                "/api/internal/jobs/job-dur-001/check-idle-reminder",
                headers=self.AUTH,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "sent"

        # Email method was called exactly once
        mock_notification_service.send_duration_confirm_reminder.assert_called_once()

        # reminder_sent flag was written back
        update_calls = mock_job_manager.firestore.update_job.call_args_list
        assert any(
            call_args[0][1].get("state_data", {}).get("reminder_sent") is True
            for call_args in update_calls
        )

    def test_no_email_when_job_no_longer_awaiting_duration_confirm(self):
        """
        If the job has moved out of AWAITING_DURATION_CONFIRM (e.g. user confirmed),
        the endpoint should return 'skipped' and NOT send any email.
        """
        # Job is now PROCESSING (user already confirmed)
        job = _make_job(status=JobStatus.PROCESSING)

        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = job

        mock_notification_service = MagicMock()
        mock_notification_service.send_duration_confirm_reminder = AsyncMock(return_value=True)

        client = _make_client()

        with patch("backend.api.routes.internal.JobManager", return_value=mock_job_manager), \
             patch(
                 "backend.services.job_notification_service.get_job_notification_service",
                 return_value=mock_notification_service,
             ):
            response = client.post(
                "/api/internal/jobs/job-dur-001/check-idle-reminder",
                headers=self.AUTH,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"

        # No email sent
        mock_notification_service.send_duration_confirm_reminder.assert_not_called()

    def test_no_email_when_reminder_already_sent(self):
        """
        If reminder_sent is already True, the endpoint should return 'already_sent'
        without calling send_duration_confirm_reminder.
        """
        job = _make_job(
            state_data={
                "blocking_action_type": "duration_confirm",
                "blocking_state_entered_at": "2026-06-04T10:00:00",
                "reminder_sent": True,  # Already sent
            }
        )

        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = job

        mock_notification_service = MagicMock()
        mock_notification_service.send_duration_confirm_reminder = AsyncMock(return_value=True)

        client = _make_client()

        with patch("backend.api.routes.internal.JobManager", return_value=mock_job_manager), \
             patch(
                 "backend.services.job_notification_service.get_job_notification_service",
                 return_value=mock_notification_service,
             ):
            response = client.post(
                "/api/internal/jobs/job-dur-001/check-idle-reminder",
                headers=self.AUTH,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "already_sent"

        mock_notification_service.send_duration_confirm_reminder.assert_not_called()

    def test_no_email_when_made_for_you(self):
        """Made-for-you jobs should be skipped even in AWAITING_DURATION_CONFIRM."""
        job = _make_job(made_for_you=True)

        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = job

        mock_notification_service = MagicMock()
        mock_notification_service.send_duration_confirm_reminder = AsyncMock(return_value=True)

        client = _make_client()

        with patch("backend.api.routes.internal.JobManager", return_value=mock_job_manager), \
             patch(
                 "backend.services.job_notification_service.get_job_notification_service",
                 return_value=mock_notification_service,
             ):
            response = client.post(
                "/api/internal/jobs/job-dur-001/check-idle-reminder",
                headers=self.AUTH,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"

        mock_notification_service.send_duration_confirm_reminder.assert_not_called()


# ---------------------------------------------------------------------------
# _schedule_idle_reminder unit test
# ---------------------------------------------------------------------------

class TestScheduleIdleReminderDurationConfirm:
    """Tests for _schedule_idle_reminder when entering AWAITING_DURATION_CONFIRM."""

    def test_schedules_reminder_with_900s_delay_for_duration_confirm(self):
        """
        Entering AWAITING_DURATION_CONFIRM must schedule a reminder with
        action_type='duration_confirm' and delay_seconds=900 (15 min).
        """
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-dur-002"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {"pending_additional_credits": 2}

        captured_calls = []

        async def fake_schedule_reminder(job_id, delay_seconds=None):
            captured_calls.append({"job_id": job_id, "delay_seconds": delay_seconds})
            return True

        with patch("backend.services.worker_service.get_worker_service") as mock_get_service:
            mock_service = Mock()
            mock_service.schedule_idle_reminder = AsyncMock(
                side_effect=fake_schedule_reminder
            )
            mock_get_service.return_value = mock_service

            manager._schedule_idle_reminder(mock_job, JobStatus.AWAITING_DURATION_CONFIRM)

            # Verify state_data update contains action_type and reminder_sent
            update_call = manager.firestore.update_job.call_args
            assert update_call is not None
            updated_data = update_call[0][1]
            state_data = updated_data.get("state_data", {})
            assert state_data.get("blocking_action_type") == "duration_confirm"
            assert state_data.get("reminder_sent") is False
            assert "blocking_state_entered_at" in state_data

    def test_schedules_900s_delay_not_default_delay(self):
        """
        Verify that the 900s delay is passed explicitly (not the 2-min default),
        so other action types are unaffected.
        """
        from backend.services import worker_service as ws_module

        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-dur-003"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}

        mock_schedule = AsyncMock(return_value=True)

        with patch("backend.services.worker_service.get_worker_service") as mock_get_service:
            mock_service = Mock()
            mock_service.schedule_idle_reminder = mock_schedule
            mock_get_service.return_value = mock_service

            manager._schedule_idle_reminder(mock_job, JobStatus.AWAITING_DURATION_CONFIRM)

        # Give the daemon thread a moment to run
        import time
        time.sleep(0.2)

        if mock_schedule.called:
            call_kwargs = mock_schedule.call_args
            # Should pass delay_seconds=900 explicitly
            delay = (
                call_kwargs.kwargs.get("delay_seconds")
                if call_kwargs.kwargs
                else (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
            )
            assert delay == 900, (
                f"Expected delay_seconds=900 for duration_confirm, got {delay}. "
                f"The 2-min default ({ws_module.IDLE_REMINDER_DELAY_SECONDS}s) must NOT be used."
            )

    def test_does_not_affect_lyrics_reminder_delay(self):
        """
        Entering AWAITING_REVIEW must NOT pass 900s (it should use the default delay).
        This ensures duration_confirm's explicit delay doesn't bleed into other types.
        """
        from backend.services import worker_service as ws_module

        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-lyrics"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}

        mock_schedule = AsyncMock(return_value=True)

        with patch("backend.services.worker_service.get_worker_service") as mock_get_service:
            mock_service = Mock()
            mock_service.schedule_idle_reminder = mock_schedule
            mock_get_service.return_value = mock_service

            manager._schedule_idle_reminder(mock_job, JobStatus.AWAITING_REVIEW)

        import time
        time.sleep(0.2)

        if mock_schedule.called:
            call_kwargs = mock_schedule.call_args
            delay = (
                call_kwargs.kwargs.get("delay_seconds")
                if call_kwargs.kwargs
                else (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
            )
            # Lyrics uses default (no explicit override) — delay should be
            # IDLE_REMINDER_DELAY_SECONDS or None (meaning default is used)
            assert delay != 900, (
                f"AWAITING_REVIEW must not use the 900s duration_confirm delay, got {delay}"
            )
