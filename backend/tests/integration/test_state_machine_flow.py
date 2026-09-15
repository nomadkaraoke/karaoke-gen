"""
Integration tests for the job state machine flow.

These tests verify complete state transitions without mocking the state machine,
ensuring the flow works correctly end-to-end (with mocked external services).

Created as part of state machine robustness improvements (2026-02-02).
"""
import pytest
from datetime import datetime, UTC
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Mock Firestore before importing JobManager
import sys
sys.modules['google.cloud.firestore'] = MagicMock()

from backend.models.job import Job, JobStatus
from backend.services.job_manager import JobManager, InvalidStateTransitionError


@pytest.fixture
def mock_firestore_service():
    """Mock FirestoreService."""
    with patch('backend.services.job_manager.FirestoreService') as mock:
        service = Mock()
        mock.return_value = service
        yield service


@pytest.fixture
def job_manager(mock_firestore_service):
    """Create JobManager instance with mocked Firestore."""
    return JobManager()


class TestMadeForYouYouTubeFlow:
    """
    Integration tests for Made-For-You YouTube order flow.

    This was the flow that caused jobs 06cfea29 and 984da08b to get stuck.
    The fix ensures proper state transitions from PENDING -> DOWNLOADING.
    """

    @pytest.mark.asyncio
    async def test_complete_flow_pending_to_downloading(self, job_manager, mock_firestore_service):
        """
        Test the flow from PENDING to DOWNLOADING for YouTube orders.

        This verifies the fix for the bug where jobs got stuck at PENDING
        because start_job_processing wasn't called properly.
        """
        # Create initial job in PENDING state
        job = Job(
            job_id="test-mfy-001",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="gs://bucket/test.mp3",
            state_data={},
        )
        # Access the firestore through the job_manager's instance
        job_manager.firestore.get_job.return_value = job
        job_manager.firestore.update_job_status.return_value = True

        # Mock worker service
        with patch('backend.services.worker_service.get_worker_service') as mock_get_worker:
            mock_worker = Mock()
            mock_worker.trigger_audio_worker = AsyncMock()
            mock_worker.trigger_lyrics_worker = AsyncMock()
            mock_get_worker.return_value = mock_worker

            # Call start_job_processing - this is what _handle_made_for_you_order now uses
            await job_manager.start_job_processing("test-mfy-001")

            # Verify transition to DOWNLOADING was called
            job_manager.firestore.update_job_status.assert_called()
            call_kwargs = job_manager.firestore.update_job_status.call_args.kwargs
            assert call_kwargs['status'] == JobStatus.DOWNLOADING

            # Verify both workers were triggered
            mock_worker.trigger_audio_worker.assert_called_once_with("test-mfy-001")
            mock_worker.trigger_lyrics_worker.assert_called_once_with("test-mfy-001")

    @pytest.mark.asyncio
    async def test_fails_if_job_stuck_at_wrong_state(self, job_manager, mock_firestore_service):
        """
        Test that starting processing fails if job is in wrong state.

        Jobs that are already past PENDING (e.g., DOWNLOADING) shouldn't
        be able to start processing again - this would indicate a bug.
        """
        # Create job already in DOWNLOADING state
        job = Job(
            job_id="test-mfy-002",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.DOWNLOADING,  # Already past PENDING
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="gs://bucket/test.mp3",
            state_data={},
        )
        job_manager.firestore.get_job.return_value = job

        # Attempting to start processing should raise InvalidStateTransitionError
        # because DOWNLOADING -> DOWNLOADING is not a valid transition
        with pytest.raises(InvalidStateTransitionError):
            await job_manager.start_job_processing("test-mfy-002")


class TestParallelProcessingCoordination:
    """
    Integration tests for parallel audio + lyrics processing coordination.

    Both workers run in parallel and set flags when complete.
    Screens worker is triggered when both are done.
    """

    def test_audio_complete_updates_state_data(self, job_manager, mock_firestore_service):
        """Test that mark_audio_complete updates state_data correctly."""
        job = Job(
            job_id="test-parallel-001",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.DOWNLOADING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={},
        )
        job_manager.firestore.get_job.return_value = job
        job_manager.firestore.update_job.return_value = True

        # Call mark_audio_complete
        job_manager.mark_audio_complete("test-parallel-001")

        # Verify update_job was called to set the audio_complete flag
        job_manager.firestore.update_job.assert_called()

    def test_lyrics_complete_updates_state_data(self, job_manager, mock_firestore_service):
        """Test that mark_lyrics_complete updates state_data correctly."""
        job = Job(
            job_id="test-parallel-002",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.DOWNLOADING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={},
        )
        job_manager.firestore.get_job.return_value = job
        job_manager.firestore.update_job.return_value = True

        # Call mark_lyrics_complete
        job_manager.mark_lyrics_complete("test-parallel-002")

        # Verify update_job was called to set the lyrics_complete flag
        job_manager.firestore.update_job.assert_called()

    @pytest.mark.asyncio
    async def test_both_complete_triggers_screens(self, job_manager, mock_firestore_service):
        """When lyrics is complete and the job is still DOWNLOADING, advancing
        triggers the screens worker via an AWAITED (durable) dispatch.

        mark_lyrics_complete now only sets the flag; advance_to_screens_if_ready
        does the dispatch (the old fire-and-forget trigger was lost on Cloud Run
        Job loop teardown, orphaning jobs at `downloading`).
        """
        job_both_complete = Job(
            job_id="test-parallel-003",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.DOWNLOADING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={'audio_complete': True, 'lyrics_complete': True},
        )
        job_manager.firestore.get_job.return_value = job_both_complete
        job_manager.firestore.update_job.return_value = True

        mock_ws = MagicMock()
        mock_ws.trigger_screens_worker = AsyncMock(return_value=True)
        with patch("backend.services.worker_service.get_worker_service", return_value=mock_ws):
            job_manager.mark_lyrics_complete("test-parallel-003")
            result = await job_manager.advance_to_screens_if_ready("test-parallel-003")

        assert result is True
        mock_ws.trigger_screens_worker.assert_awaited_once_with("test-parallel-003")


class TestStateTransitionValidation:
    """
    Integration tests for state transition validation.

    Ensures invalid transitions are rejected with clear errors.
    """

    def test_valid_transition_succeeds(self, job_manager, mock_firestore_service):
        """Test that valid transitions succeed."""
        job = Job(
            job_id="test-transition-001",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={},
        )
        job_manager.firestore.get_job.return_value = job
        job_manager.firestore.update_job_status.return_value = True

        # PENDING -> DOWNLOADING is valid
        result = job_manager.transition_to_state(
            job_id="test-transition-001",
            new_status=JobStatus.DOWNLOADING,
            progress=10,
            message="Starting processing"
        )

        assert result is True

    def test_invalid_transition_raises_by_default(self, job_manager, mock_firestore_service):
        """Test that invalid transitions raise exception by default."""
        job = Job(
            job_id="test-transition-002",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={},
        )
        job_manager.firestore.get_job.return_value = job

        # PENDING -> COMPLETE is invalid (skips many steps)
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            job_manager.transition_to_state(
                job_id="test-transition-002",
                new_status=JobStatus.COMPLETE,
            )

        assert "pending" in str(exc_info.value).lower()
        assert "complete" in str(exc_info.value).lower()

    def test_invalid_transition_returns_false_when_not_raising(self, job_manager, mock_firestore_service):
        """Test that invalid transitions return False when raise_on_invalid=False."""
        job = Job(
            job_id="test-transition-003",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={},
        )
        job_manager.firestore.get_job.return_value = job

        # PENDING -> COMPLETE is invalid, but with raise_on_invalid=False
        result = job_manager.transition_to_state(
            job_id="test-transition-003",
            new_status=JobStatus.COMPLETE,
            raise_on_invalid=False
        )

        assert result is False


class TestAdminResetFlow:
    """
    Integration tests for admin reset functionality.

    Admin resets bypass normal state machine validation but must
    properly clear state_data flags to avoid inconsistent state.
    """

    def test_reset_to_pending_clears_parallel_flags(self, job_manager, mock_firestore_service):
        """
        Test that resetting to pending clears audio_complete and lyrics_complete.

        This was a bug where the Start button would reset to pending but
        leave these flags set, causing inconsistent state.
        """
        # This is tested via the admin endpoint, not directly in JobManager
        # The test in test_admin_job_reset.py covers this
        pass


class TestConsistencyWithHealthService:
    """
    Integration tests that verify the job health service correctly
    identifies inconsistencies in real job flows.
    """

    def test_detects_stuck_job_scenario(self, job_manager, mock_firestore_service):
        """
        Test that the health service would detect the exact bug that
        caused jobs 06cfea29 and 984da08b to get stuck.
        """
        from backend.services.job_health_service import check_job_consistency

        # Simulate the stuck job scenario:
        # - Job has input_media_gcs_path (YouTube download completed)
        # - But status is still PENDING (transition never happened)
        stuck_job = Job(
            job_id="test-stuck-001",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="gs://bucket/downloaded.mp3",
            state_data={},
        )

        issues = check_job_consistency(stuck_job)

        # Should detect the pending_with_input_media issue
        assert len(issues) > 0
        assert any("pending_with_input_media" in issue for issue in issues)

    def test_detects_flag_status_mismatch(self, job_manager, mock_firestore_service):
        """
        Test that the health service detects when flags don't match status.
        """
        from backend.services.job_health_service import check_job_consistency

        # Simulate: audio_complete=True but status still PENDING
        # (would happen if mark_audio_complete ran but transition didn't)
        inconsistent_job = Job(
            job_id="test-inconsistent-001",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            state_data={'audio_complete': True},
        )

        issues = check_job_consistency(inconsistent_job)

        # Should detect the audio_complete_status_mismatch issue
        assert len(issues) > 0
        assert any("audio_complete_status_mismatch" in issue for issue in issues)
