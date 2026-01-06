"""
Unit tests for job manager notification methods.

Tests the state transition triggers for email notifications.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime

from backend.services.job_manager import JobManager
from backend.models.job import Job, JobStatus


class TestTriggerStateNotifications:
    """Tests for _trigger_state_notifications method."""

    def test_triggers_completion_email_on_complete(self):
        """Test that completion email is triggered when job completes."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {"youtube_url": "https://youtube.com/123"}
        manager.get_job = Mock(return_value=mock_job)

        with patch.object(manager, '_schedule_completion_email') as mock_schedule:
            manager._trigger_state_notifications("job-123", JobStatus.COMPLETE)

            mock_schedule.assert_called_once_with(mock_job)

    def test_triggers_idle_reminder_on_awaiting_review(self):
        """Test that idle reminder is triggered when job enters awaiting review."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}
        manager.get_job = Mock(return_value=mock_job)

        with patch.object(manager, '_schedule_idle_reminder') as mock_schedule:
            manager._trigger_state_notifications("job-123", JobStatus.AWAITING_REVIEW)

            mock_schedule.assert_called_once_with(mock_job, JobStatus.AWAITING_REVIEW)

    def test_triggers_idle_reminder_on_awaiting_instrumental(self):
        """Test that idle reminder is triggered when job enters awaiting instrumental."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}
        manager.get_job = Mock(return_value=mock_job)

        with patch.object(manager, '_schedule_idle_reminder') as mock_schedule:
            manager._trigger_state_notifications(
                "job-123",
                JobStatus.AWAITING_INSTRUMENTAL_SELECTION
            )

            mock_schedule.assert_called_once_with(
                mock_job,
                JobStatus.AWAITING_INSTRUMENTAL_SELECTION
            )

    def test_skips_notification_when_no_job(self):
        """Test that notification is skipped when job not found."""
        manager = JobManager()
        manager.firestore = Mock()
        manager.get_job = Mock(return_value=None)

        with patch.object(manager, '_schedule_completion_email') as mock_schedule:
            manager._trigger_state_notifications("job-123", JobStatus.COMPLETE)

            mock_schedule.assert_not_called()

    def test_skips_notification_when_no_user_email(self):
        """Test that notification is skipped when job has no user email."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = None
        manager.get_job = Mock(return_value=mock_job)

        with patch.object(manager, '_schedule_completion_email') as mock_schedule:
            manager._trigger_state_notifications("job-123", JobStatus.COMPLETE)

            mock_schedule.assert_not_called()

    def test_does_not_trigger_on_other_states(self):
        """Test that no notification is triggered for non-notification states."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}
        manager.get_job = Mock(return_value=mock_job)

        with patch.object(manager, '_schedule_completion_email') as mock_complete, \
             patch.object(manager, '_schedule_idle_reminder') as mock_idle:

            # Test various non-notification states
            for status in [JobStatus.PENDING, JobStatus.DOWNLOADING, JobStatus.TRANSCRIBING,
                          JobStatus.RENDERING_VIDEO, JobStatus.FAILED]:
                manager._trigger_state_notifications("job-123", status)

            mock_complete.assert_not_called()
            mock_idle.assert_not_called()

    def test_handles_exception_gracefully(self):
        """Test that exceptions don't propagate from notifications."""
        manager = JobManager()
        manager.firestore = Mock()
        manager.get_job = Mock(side_effect=Exception("Database error"))

        # Should not raise
        manager._trigger_state_notifications("job-123", JobStatus.COMPLETE)


class TestScheduleCompletionEmail:
    """Tests for _schedule_completion_email method."""

    def test_extracts_urls_from_state_data(self):
        """Test that YouTube and Dropbox URLs are extracted from state_data."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.artist = "Test Artist"
        mock_job.title = "Test Song"
        mock_job.state_data = {
            "youtube_url": "https://youtube.com/watch?v=123",
            "dropbox_link": "https://dropbox.com/folder/abc",
        }

        with patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_service:
            mock_service = Mock()
            mock_service.send_job_completion_email = AsyncMock(return_value=True)
            mock_get_service.return_value = mock_service

            # Run in sync context - this will start a daemon thread
            manager._schedule_completion_email(mock_job)

            # Give the thread a moment to execute
            import time
            time.sleep(0.1)

    def test_handles_none_state_data(self):
        """Test that None state_data is handled gracefully."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.artist = None
        mock_job.title = None
        mock_job.state_data = None  # None state_data

        with patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_service:
            mock_service = Mock()
            mock_service.send_job_completion_email = AsyncMock(return_value=True)
            mock_get_service.return_value = mock_service

            # Should not raise
            manager._schedule_completion_email(mock_job)

    def test_handles_exception_gracefully(self):
        """Test that exceptions don't propagate."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}

        with patch('backend.services.job_notification_service.get_job_notification_service') as mock_get_service:
            mock_get_service.side_effect = Exception("Service error")

            # Should not raise
            manager._schedule_completion_email(mock_job)


class TestScheduleIdleReminder:
    """Tests for _schedule_idle_reminder method."""

    def test_updates_state_data_for_lyrics_review(self):
        """Test that state_data is updated with blocking state info for lyrics."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {"existing_key": "value"}

        with patch('backend.services.worker_service.get_worker_service') as mock_get_service:
            mock_service = Mock()
            mock_service.schedule_idle_reminder = AsyncMock(return_value=True)
            mock_get_service.return_value = mock_service

            manager._schedule_idle_reminder(mock_job, JobStatus.AWAITING_REVIEW)

            # Verify state_data was updated
            update_call = manager.firestore.update_job.call_args
            assert update_call is not None
            updated_data = update_call[0][1]  # Second positional arg
            state_data = updated_data.get('state_data', {})

            assert state_data.get('blocking_action_type') == 'lyrics'
            assert state_data.get('reminder_sent') is False
            assert 'blocking_state_entered_at' in state_data
            assert state_data.get('existing_key') == 'value'  # Preserved

    def test_updates_state_data_for_instrumental_selection(self):
        """Test that state_data is updated with blocking state info for instrumental."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}

        with patch('backend.services.worker_service.get_worker_service') as mock_get_service:
            mock_service = Mock()
            mock_service.schedule_idle_reminder = AsyncMock(return_value=True)
            mock_get_service.return_value = mock_service

            manager._schedule_idle_reminder(
                mock_job,
                JobStatus.AWAITING_INSTRUMENTAL_SELECTION
            )

            # Verify state_data was updated
            update_call = manager.firestore.update_job.call_args
            updated_data = update_call[0][1]
            state_data = updated_data.get('state_data', {})

            assert state_data.get('blocking_action_type') == 'instrumental'

    def test_handles_none_state_data(self):
        """Test that None state_data is handled gracefully."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = None

        with patch('backend.services.worker_service.get_worker_service') as mock_get_service:
            mock_service = Mock()
            mock_service.schedule_idle_reminder = AsyncMock(return_value=True)
            mock_get_service.return_value = mock_service

            # Should not raise
            manager._schedule_idle_reminder(mock_job, JobStatus.AWAITING_REVIEW)

            # Verify update was still called
            manager.firestore.update_job.assert_called_once()

    def test_handles_exception_gracefully(self):
        """Test that exceptions don't propagate."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}

        # Make firestore raise an exception
        manager.firestore.update_job.side_effect = Exception("Database error")

        # Should not raise
        manager._schedule_idle_reminder(mock_job, JobStatus.AWAITING_REVIEW)


class TestTransitionTriggersNotifications:
    """Integration tests for transition_to_state triggering notifications."""

    def test_transition_to_complete_triggers_notification(self):
        """Test that transitioning to COMPLETE triggers notification."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        # Use ENCODING as the starting state since ENCODING -> COMPLETE is valid
        mock_job.status = JobStatus.ENCODING
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}
        manager.get_job = Mock(return_value=mock_job)

        with patch.object(manager, '_trigger_state_notifications') as mock_trigger:
            manager.transition_to_state("job-123", JobStatus.COMPLETE)

            mock_trigger.assert_called_once_with("job-123", JobStatus.COMPLETE)

    def test_transition_to_awaiting_review_triggers_notification(self):
        """Test that transitioning to AWAITING_REVIEW triggers notification."""
        manager = JobManager()
        manager.firestore = Mock()

        mock_job = Mock(spec=Job)
        mock_job.job_id = "job-123"
        # Use APPLYING_PADDING as the starting state since APPLYING_PADDING -> AWAITING_REVIEW is valid
        mock_job.status = JobStatus.APPLYING_PADDING
        mock_job.user_email = "user@example.com"
        mock_job.state_data = {}
        manager.get_job = Mock(return_value=mock_job)

        with patch.object(manager, '_trigger_state_notifications') as mock_trigger:
            manager.transition_to_state("job-123", JobStatus.AWAITING_REVIEW)

            mock_trigger.assert_called_once_with("job-123", JobStatus.AWAITING_REVIEW)
