"""
Tests for the job health service.

Tests consistency checks and worker validation functions.
"""
import pytest
from datetime import datetime, timedelta, timezone, UTC

from backend.models.job import Job, JobStatus
from backend.services.job_health_service import (
    check_job_consistency,
    check_job_consistency_detailed,
    validate_worker_can_run,
    get_worker_valid_statuses,
)


def create_test_job(
    job_id: str = "test-job-123",
    status: JobStatus = JobStatus.PENDING,
    state_data: dict = None,
    input_media_gcs_path: str = None,
) -> Job:
    """Helper to create test Job objects."""
    return Job(
        job_id=job_id,
        artist="Test Artist",
        title="Test Song",
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        state_data=state_data or {},
        input_media_gcs_path=input_media_gcs_path,
    )


class TestCheckJobConsistency:
    """Tests for check_job_consistency function."""

    def test_healthy_job_has_no_issues(self):
        """Test that a properly configured job has no issues."""
        job = create_test_job(
            status=JobStatus.DOWNLOADING,
            state_data={},
        )
        issues = check_job_consistency(job)
        assert issues == []

    def test_detects_audio_complete_with_pending_status(self):
        """Test detection of audio_complete flag with pending status."""
        job = create_test_job(
            status=JobStatus.PENDING,
            state_data={'audio_complete': True},
        )
        issues = check_job_consistency(job)
        assert len(issues) == 1
        assert "audio_complete_status_mismatch" in issues[0]
        assert "audio_complete=True but status=pending" in issues[0]

    def test_detects_lyrics_complete_with_pending_status(self):
        """Test detection of lyrics_complete flag with pending status."""
        job = create_test_job(
            status=JobStatus.PENDING,
            state_data={'lyrics_complete': True},
        )
        issues = check_job_consistency(job)
        assert len(issues) == 1
        assert "lyrics_complete_status_mismatch" in issues[0]

    def test_detects_screens_complete_with_wrong_status(self):
        """Test detection of screens complete but status not in post-screens states."""
        job = create_test_job(
            status=JobStatus.DOWNLOADING,
            state_data={'screens_progress': {'stage': 'complete'}},
        )
        issues = check_job_consistency(job)
        assert len(issues) == 1
        assert "screens_complete_status_mismatch" in issues[0]

    def test_screens_complete_valid_with_awaiting_review(self):
        """Test that screens complete is valid with AWAITING_REVIEW status."""
        job = create_test_job(
            status=JobStatus.AWAITING_REVIEW,
            state_data={'screens_progress': {'stage': 'complete'}},
        )
        issues = check_job_consistency(job)
        assert issues == []

    def test_screens_complete_valid_with_complete_status(self):
        """Test that screens complete is valid with COMPLETE status."""
        job = create_test_job(
            status=JobStatus.COMPLETE,
            state_data={'screens_progress': {'stage': 'complete'}},
        )
        issues = check_job_consistency(job)
        assert issues == []

    def test_detects_pending_with_input_media_path(self):
        """Test detection of job stuck at pending with input_media_gcs_path."""
        job = create_test_job(
            status=JobStatus.PENDING,
            input_media_gcs_path="gs://bucket/audio.mp3",
        )
        issues = check_job_consistency(job)
        assert len(issues) == 1
        assert "pending_with_input_media" in issues[0]

    def test_multiple_issues_detected(self):
        """Test that multiple issues are detected simultaneously."""
        job = create_test_job(
            status=JobStatus.PENDING,
            state_data={
                'audio_complete': True,
                'lyrics_complete': True,
            },
            input_media_gcs_path="gs://bucket/audio.mp3",
        )
        issues = check_job_consistency(job)
        assert len(issues) == 3

    def test_handles_invalid_status_string(self):
        """Test handling of invalid status string."""
        job = create_test_job(status=JobStatus.PENDING)
        job.status = "invalid_status"  # type: ignore
        issues = check_job_consistency(job)
        assert len(issues) == 1
        assert "invalid_status" in issues[0]

    def test_handles_empty_state_data(self):
        """Test handling of empty or None state_data."""
        job = create_test_job(
            status=JobStatus.DOWNLOADING,
            state_data=None,
        )
        issues = check_job_consistency(job)
        assert issues == []

    def test_encoding_recently_started_not_flagged(self):
        """Encoding job started 10 minutes ago should not be flagged."""
        job = create_test_job(status=JobStatus.ENCODING)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        issues = check_job_consistency(job)
        assert not any("encoding_stuck" in i for i in issues)

    def test_encoding_stuck_55_min_flagged(self):
        """Encoding job stuck for 55 minutes should be flagged."""
        job = create_test_job(status=JobStatus.ENCODING)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=55)
        issues = check_job_consistency(job)
        assert any("encoding_stuck" in i for i in issues)
        stuck_issue = [i for i in issues if "encoding_stuck" in i][0]
        assert "55 min" in stuck_issue

    def test_encoding_at_49_min_not_flagged(self):
        """Encoding job at 49 minutes should not be flagged (must exceed 50)."""
        job = create_test_job(status=JobStatus.ENCODING)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=49)
        issues = check_job_consistency(job)
        assert not any("encoding_stuck" in i for i in issues)

    def test_non_encoding_job_not_affected(self):
        """Non-encoding job should not trigger encoding_stuck check."""
        job = create_test_job(status=JobStatus.GENERATING_VIDEO)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=120)
        issues = check_job_consistency(job)
        assert not any("encoding_stuck" in i for i in issues)

    def test_encoding_stuck_with_naive_datetime(self):
        """Encoding-stuck check handles naive datetime (no tzinfo)."""
        job = create_test_job(status=JobStatus.ENCODING)
        job.updated_at = datetime.utcnow() - timedelta(minutes=60)
        issues = check_job_consistency(job)
        assert any("encoding_stuck" in i for i in issues)

    def test_downloading_audio_recently_started_not_flagged(self):
        """Download started 3 minutes ago should not be flagged."""
        job = create_test_job(status=JobStatus.DOWNLOADING_AUDIO)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=3)
        issues = check_job_consistency(job)
        assert not any("downloading_audio_stuck" in i for i in issues)

    def test_downloading_audio_stuck_15_min_flagged(self):
        """Download stuck for 15 minutes should be flagged."""
        job = create_test_job(status=JobStatus.DOWNLOADING_AUDIO)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        issues = check_job_consistency(job)
        assert any("downloading_audio_stuck" in i for i in issues)
        stuck_issue = [i for i in issues if "downloading_audio_stuck" in i][0]
        assert "15 min" in stuck_issue

    def test_downloading_audio_at_9_min_not_flagged(self):
        """Download at 9 minutes should not be flagged (must exceed 10)."""
        job = create_test_job(status=JobStatus.DOWNLOADING_AUDIO)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=9)
        issues = check_job_consistency(job)
        assert not any("downloading_audio_stuck" in i for i in issues)

    def test_non_downloading_audio_not_affected(self):
        """Non-downloading_audio job should not trigger this check."""
        job = create_test_job(status=JobStatus.DOWNLOADING)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=120)
        issues = check_job_consistency(job)
        assert not any("downloading_audio_stuck" in i for i in issues)


class TestCheckJobConsistencyDetailed:
    """Tests for check_job_consistency_detailed function."""

    def test_returns_detailed_info(self):
        """Test that detailed info is returned."""
        job = create_test_job(
            status=JobStatus.PENDING,
            state_data={'audio_complete': True},
            input_media_gcs_path="gs://bucket/audio.mp3",
        )
        result = check_job_consistency_detailed(job)

        assert result['job_id'] == "test-job-123"
        assert result['status'] == "pending"
        assert len(result['issues']) == 2
        assert result['state_data_summary']['audio_complete'] is True
        assert result['state_data_summary']['lyrics_complete'] is False
        assert result['state_data_summary']['has_input_media_gcs_path'] is True
        assert result['is_healthy'] is False

    def test_healthy_job_returns_is_healthy_true(self):
        """Test that healthy job returns is_healthy=True."""
        job = create_test_job(status=JobStatus.DOWNLOADING)
        result = check_job_consistency_detailed(job)
        assert result['is_healthy'] is True
        assert result['issues'] == []


class TestValidateWorkerCanRun:
    """Tests for validate_worker_can_run function."""

    def test_audio_worker_valid_with_downloading_status(self):
        """Test audio worker is valid with DOWNLOADING status."""
        job = create_test_job(status=JobStatus.DOWNLOADING)
        error = validate_worker_can_run("audio_worker", job)
        assert error is None

    def test_audio_worker_invalid_with_pending_status(self):
        """Test audio worker is invalid with PENDING status."""
        job = create_test_job(status=JobStatus.PENDING)
        error = validate_worker_can_run("audio_worker", job)
        assert error is not None
        assert "audio_worker called but job status is pending" in error

    def test_lyrics_worker_valid_with_downloading_status(self):
        """Test lyrics worker is valid with DOWNLOADING status."""
        job = create_test_job(status=JobStatus.DOWNLOADING)
        error = validate_worker_can_run("lyrics_worker", job)
        assert error is None

    def test_screens_worker_valid_with_audio_complete_status(self):
        """Test screens worker is valid with AUDIO_COMPLETE status."""
        job = create_test_job(status=JobStatus.AUDIO_COMPLETE)
        error = validate_worker_can_run("screens_worker", job)
        assert error is None

    def test_screens_worker_valid_with_lyrics_complete_status(self):
        """Test screens worker is valid with LYRICS_COMPLETE status."""
        job = create_test_job(status=JobStatus.LYRICS_COMPLETE)
        error = validate_worker_can_run("screens_worker", job)
        assert error is None

    def test_screens_worker_invalid_with_awaiting_review_status(self):
        """Test screens worker is invalid with AWAITING_REVIEW status (too late)."""
        job = create_test_job(status=JobStatus.AWAITING_REVIEW)
        error = validate_worker_can_run("screens_worker", job)
        assert error is not None
        assert "screens_worker called but job status is awaiting_review" in error

    def test_video_worker_valid_with_instrumental_selected_status(self):
        """Test video worker is valid with INSTRUMENTAL_SELECTED status."""
        job = create_test_job(status=JobStatus.INSTRUMENTAL_SELECTED)
        error = validate_worker_can_run("video_worker", job)
        assert error is None

    def test_video_worker_invalid_with_pending_status(self):
        """Test video worker is invalid with PENDING status."""
        job = create_test_job(status=JobStatus.PENDING)
        error = validate_worker_can_run("video_worker", job)
        assert error is not None
        assert "video_worker called but job status is pending" in error

    def test_render_video_worker_valid_with_review_complete_status(self):
        """Test render video worker is valid with REVIEW_COMPLETE status."""
        job = create_test_job(status=JobStatus.REVIEW_COMPLETE)
        error = validate_worker_can_run("render_video_worker", job)
        assert error is None

    def test_unknown_worker_returns_none(self):
        """Test that unknown worker names return None (no validation)."""
        job = create_test_job(status=JobStatus.PENDING)
        error = validate_worker_can_run("unknown_worker", job)
        assert error is None

    def test_handles_invalid_status_string(self):
        """Test handling of invalid status string."""
        job = create_test_job(status=JobStatus.PENDING)
        job.status = "invalid_status"  # type: ignore
        error = validate_worker_can_run("audio_worker", job)
        assert error is not None
        assert "Invalid job status" in error


class TestGetWorkerValidStatuses:
    """Tests for get_worker_valid_statuses function."""

    def test_returns_dict_with_all_workers(self):
        """Test that all known workers are included."""
        statuses = get_worker_valid_statuses()
        assert "audio_worker" in statuses
        assert "lyrics_worker" in statuses
        assert "screens_worker" in statuses
        assert "video_worker" in statuses
        assert "render_video_worker" in statuses

    def test_audio_worker_includes_downloading(self):
        """Test that audio worker includes DOWNLOADING status."""
        statuses = get_worker_valid_statuses()
        assert JobStatus.DOWNLOADING in statuses["audio_worker"]

    def test_lyrics_worker_includes_downloading(self):
        """Test that lyrics worker includes DOWNLOADING status."""
        statuses = get_worker_valid_statuses()
        assert JobStatus.DOWNLOADING in statuses["lyrics_worker"]
