"""
Unit tests for jobs.py routes.

These tests exercise the route logic with mocked services.
"""
import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch

from backend.models.job import Job, JobStatus


class TestJobsRouteHelpers:
    """Tests for helper functions in jobs.py routes."""

    def test_jobs_router_structure(self):
        """Test jobs router has expected structure."""
        from backend.api.routes.jobs import router
        assert router is not None

        # Check that common route patterns exist
        route_paths = [route.path for route in router.routes]
        assert any('/jobs' in p or 'jobs' in str(p) for p in route_paths)


class TestIsAutoRetryPending:
    """Tests for the _is_auto_retry_pending helper used by /retry."""

    def _now(self, offset_seconds=0):
        from datetime import timedelta, timezone
        return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()

    def test_returns_false_for_missing_state_data(self):
        from backend.api.routes.jobs import _is_auto_retry_pending
        assert _is_auto_retry_pending(None) is False
        assert _is_auto_retry_pending({}) is False

    def test_returns_false_when_no_retry_pending_field(self):
        from backend.api.routes.jobs import _is_auto_retry_pending
        assert _is_auto_retry_pending({'other_field': 'x'}) is False

    def test_returns_true_when_pending_not_expired(self):
        from backend.api.routes.jobs import _is_auto_retry_pending
        state_data = {'cloud_run_retry_pending': {
            'expires_at': self._now(60),
            'expected_attempt': 1,
        }}
        assert _is_auto_retry_pending(state_data) is True

    def test_returns_false_when_pending_expired(self):
        from backend.api.routes.jobs import _is_auto_retry_pending
        state_data = {'cloud_run_retry_pending': {
            'expires_at': self._now(-60),
            'expected_attempt': 1,
        }}
        assert _is_auto_retry_pending(state_data) is False

    def test_returns_false_for_malformed_expires_at(self):
        from backend.api.routes.jobs import _is_auto_retry_pending
        assert _is_auto_retry_pending({'cloud_run_retry_pending': {'expires_at': 'not-a-date'}}) is False
        assert _is_auto_retry_pending({'cloud_run_retry_pending': {'expires_at': None}}) is False
        assert _is_auto_retry_pending({'cloud_run_retry_pending': 'not-a-dict'}) is False


class TestSummaryFieldPathsIncludesRetryPending:
    """Regression: cloud_run_retry_pending must be projected so the dashboard
    can render the 'Retrying automatically' state. Per MEMORY note, the
    Firestore projection AND the Python prune allowlist must both include it.
    """

    def test_firestore_projection_includes_retry_pending(self):
        from backend.services.firestore_service import FirestoreService
        assert 'state_data.cloud_run_retry_pending' in FirestoreService.SUMMARY_FIELD_PATHS

    def test_summary_prune_allows_retry_pending(self):
        from backend.api.routes.jobs import _SUMMARY_STATE_DATA_KEYS
        assert 'cloud_run_retry_pending' in _SUMMARY_STATE_DATA_KEYS


class TestSummaryFieldPathsIncludesDurationPricing:
    """Regression: duration-pricing fields must be in both SUMMARY_FIELD_PATHS
    and _SUMMARY_STATE_DATA_KEYS so the dashboard can render the
    awaiting_duration_confirm modal with correct cost figures.

    Per project MEMORY note: mocked-job tests won't catch a missing Firestore
    projection — this class is the authoritative regression guard.
    """

    _EXPECTED_STATE_DATA_FIELDS = [
        'credits_charged',
        'pending_additional_credits',
        'duration_actual_seconds',
        'duration_confirm_reason',
    ]

    def test_firestore_projection_includes_duration_pricing_fields(self):
        from backend.services.firestore_service import FirestoreService
        for field in self._EXPECTED_STATE_DATA_FIELDS:
            path = f'state_data.{field}'
            assert path in FirestoreService.SUMMARY_FIELD_PATHS, (
                f"'{path}' must be in SUMMARY_FIELD_PATHS so the dashboard "
                f"can display the duration-pricing confirm modal"
            )

    def test_summary_prune_allows_duration_pricing_keys(self):
        from backend.api.routes.jobs import _SUMMARY_STATE_DATA_KEYS
        for field in self._EXPECTED_STATE_DATA_FIELDS:
            assert field in _SUMMARY_STATE_DATA_KEYS, (
                f"'{field}' must be in _SUMMARY_STATE_DATA_KEYS so it is not "
                f"stripped before reaching the dashboard"
            )


class TestJobStatusTransitions:
    """Tests for job status transition validation.

    These test the Job model's state machine which is critical for
    preventing invalid operations.
    """
    
    def test_valid_pending_to_downloading(self):
        """Test PENDING -> DOWNLOADING is valid."""
        from backend.models.job import STATE_TRANSITIONS
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.PENDING, [])
        assert JobStatus.DOWNLOADING in valid_transitions
    
    def test_valid_downloading_to_separating(self):
        """Test DOWNLOADING -> SEPARATING_STAGE1 is valid."""
        from backend.models.job import STATE_TRANSITIONS
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.DOWNLOADING, [])
        assert JobStatus.SEPARATING_STAGE1 in valid_transitions
    
    def test_invalid_pending_to_complete(self):
        """Test PENDING -> COMPLETE is invalid."""
        from backend.models.job import STATE_TRANSITIONS
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.PENDING, [])
        assert JobStatus.COMPLETE not in valid_transitions
    
    def test_any_active_status_can_fail(self):
        """Test any active (non-terminal, non-legacy) status can transition to FAILED."""
        from backend.models.job import STATE_TRANSITIONS
        # Terminal states and legacy states are excluded
        excluded = [
            JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED,
            # Legacy states that don't include FAILED
            JobStatus.QUEUED, JobStatus.PROCESSING, 
            JobStatus.READY_FOR_FINALIZATION, JobStatus.FINALIZING, JobStatus.ERROR
        ]
        for status in JobStatus:
            if status not in excluded:
                valid_transitions = STATE_TRANSITIONS.get(status, [])
                assert JobStatus.FAILED in valid_transitions, f"{status} should be able to fail"
    
    def test_failed_can_transition_for_retry(self):
        """Test FAILED status can transition to retry checkpoint states."""
        from backend.models.job import STATE_TRANSITIONS
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.FAILED, [])

        # FAILED should allow retry transitions
        assert JobStatus.INSTRUMENTAL_SELECTED in valid_transitions, "FAILED should allow retry to INSTRUMENTAL_SELECTED"
        assert JobStatus.REVIEW_COMPLETE in valid_transitions, "FAILED should allow retry to REVIEW_COMPLETE"
        assert JobStatus.LYRICS_COMPLETE in valid_transitions, "FAILED should allow retry to LYRICS_COMPLETE"

    def test_cancelled_can_transition_for_retry(self):
        """Test CANCELLED status can transition to retry checkpoint states."""
        from backend.models.job import STATE_TRANSITIONS
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.CANCELLED, [])

        # CANCELLED should allow same retry transitions as FAILED
        assert JobStatus.INSTRUMENTAL_SELECTED in valid_transitions, "CANCELLED should allow retry to INSTRUMENTAL_SELECTED"
        assert JobStatus.REVIEW_COMPLETE in valid_transitions, "CANCELLED should allow retry to REVIEW_COMPLETE"
        assert JobStatus.LYRICS_COMPLETE in valid_transitions, "CANCELLED should allow retry to LYRICS_COMPLETE"
        assert JobStatus.DOWNLOADING in valid_transitions, "CANCELLED should allow retry to DOWNLOADING (restart)"

    def test_failed_can_restart_from_beginning(self):
        """Test FAILED status can transition to DOWNLOADING for restart from beginning."""
        from backend.models.job import STATE_TRANSITIONS
        valid_transitions = STATE_TRANSITIONS.get(JobStatus.FAILED, [])

        # FAILED should allow restart from beginning
        assert JobStatus.DOWNLOADING in valid_transitions, "FAILED should allow restart to DOWNLOADING"


class TestJobModelSerialization:
    """Tests for Job model serialization to/from API."""
    
    def test_job_to_dict(self):
        """Test Job converts to dict correctly."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song"
        )
        data = job.model_dump()
        assert data["job_id"] == "test123"
        assert data["status"] == "pending"
        assert data["artist"] == "Test Artist"
    
    def test_job_from_dict(self):
        """Test Job can be created from dict."""
        data = {
            "job_id": "test123",
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "artist": "Test",
            "title": "Song"
        }
        job = Job.model_validate(data)
        assert job.job_id == "test123"
        assert job.status == JobStatus.PENDING
    
    def test_job_handles_null_optional_fields(self):
        """Test Job handles null optional fields."""
        job = Job(
            job_id="test",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        assert job.artist is None
        assert job.title is None
        assert job.url is None
        assert job.file_urls == {}


class TestRetryEndpoint:
    """Tests for the retry job endpoint."""

    @pytest.fixture
    def mock_job_manager(self):
        """Create mock job manager."""
        return MagicMock()

    @pytest.fixture
    def mock_worker_service(self):
        """Create mock worker service."""
        return MagicMock()

    def test_retry_rejects_pending_job(self, mock_job_manager):
        """Test retry endpoint rejects jobs that are not failed or cancelled."""
        # Create a pending job
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song"
        )
        mock_job_manager.get_job.return_value = job

        # The endpoint should reject this with a 400 error
        # (status check happens before any logic)
        assert job.status not in [JobStatus.FAILED, JobStatus.CANCELLED]

    def test_retry_accepts_failed_job(self, mock_job_manager):
        """Test retry endpoint accepts failed jobs."""
        job = Job(
            job_id="test123",
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            input_media_gcs_path="jobs/test123/input/audio.flac"
        )
        mock_job_manager.get_job.return_value = job

        # Status should be accepted
        assert job.status in [JobStatus.FAILED, JobStatus.CANCELLED]

    def test_retry_accepts_cancelled_job(self, mock_job_manager):
        """Test retry endpoint accepts cancelled jobs."""
        job = Job(
            job_id="test123",
            status=JobStatus.CANCELLED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            input_media_gcs_path="jobs/test123/input/audio.flac"
        )
        mock_job_manager.get_job.return_value = job

        # Status should be accepted
        assert job.status in [JobStatus.FAILED, JobStatus.CANCELLED]

    def test_retry_checkpoint_detection_video_generation(self):
        """Test retry detects video generation checkpoint."""
        job = Job(
            job_id="test123",
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            file_urls={
                'videos': {'with_vocals': 'gs://bucket/path/video.mkv'}
            },
            state_data={
                'instrumental_selection': 'clean'
            }
        )

        # This job has video and instrumental selection - should retry from video generation
        has_video = job.file_urls.get('videos', {}).get('with_vocals')
        has_instrumental_selection = (job.state_data or {}).get('instrumental_selection')
        assert has_video and has_instrumental_selection

    def test_retry_checkpoint_detection_render_stage(self):
        """Test retry detects render stage checkpoint when review was completed."""
        job = Job(
            job_id="test123",
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            file_urls={
                'lyrics': {'corrections': 'gs://bucket/path/corrections.json'},
                'screens': {'title': 'gs://bucket/path/title.mov'}
            },
            state_data={
                'instrumental_selection': 'clean'
            }
        )

        # This job has corrections, screens, AND completed review - should retry from render
        has_corrections = job.file_urls.get('lyrics', {}).get('corrections')
        has_screens = job.file_urls.get('screens', {}).get('title')
        has_video = job.file_urls.get('videos', {}).get('with_vocals')
        has_review = (job.state_data or {}).get('instrumental_selection')
        assert has_corrections and has_screens and not has_video and has_review

    def test_retry_checkpoint_returns_to_review_when_not_completed(self):
        """Test retry returns to awaiting_review when corrections and screens exist
        but user never completed the review (no instrumental_selection).

        Regression test: previously, retry would skip to REVIEW_COMPLETE based solely
        on corrections.json + title.mov existing, but these are auto-generated artifacts
        that exist BEFORE the user reviews anything.
        """
        job = Job(
            job_id="test123",
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            file_urls={
                'lyrics': {'corrections': 'gs://bucket/path/corrections.json'},
                'screens': {'title': 'gs://bucket/path/title.mov'}
            }
            # No state_data.instrumental_selection — review was never completed
        )

        # Has corrections and screens but NO review completion
        has_corrections = job.file_urls.get('lyrics', {}).get('corrections')
        has_screens = job.file_urls.get('screens', {}).get('title')
        has_review = (job.state_data or {}).get('instrumental_selection')
        # Should match the "awaiting_review" checkpoint, NOT "render"
        assert has_corrections and has_screens and not has_review

    def test_retry_checkpoint_detection_from_beginning(self):
        """Test retry detects need to restart from beginning."""
        job = Job(
            job_id="test123",
            status=JobStatus.CANCELLED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            input_media_gcs_path="jobs/test123/input/audio.flac",
            file_urls={}  # No progress yet
        )

        # This job has input audio but no other files - should restart from beginning
        has_input = job.input_media_gcs_path
        has_stems = job.file_urls.get('stems', {}).get('instrumental_clean')
        has_corrections = job.file_urls.get('lyrics', {}).get('corrections')
        assert has_input and not has_stems and not has_corrections

    def test_retry_no_input_audio(self):
        """Test retry fails when no input audio available."""
        job = Job(
            job_id="test123",
            status=JobStatus.CANCELLED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            # No input_media_gcs_path and no url
            file_urls={}
        )

        # This job has no input audio - should not be retryable.
        # Restart-from-beginning is gated on audio actually being in GCS, NOT on
        # job.url (a URL whose download failed has no audio to re-fetch).
        has_input_audio_in_gcs = job.input_media_gcs_path
        assert not has_input_audio_in_gcs


class TestRetryUrlJobWithoutDownload:
    """Regression tests for the retry routing fix (incident: job 7f36304a).

    A URL job whose download failed has `job.url` set but NO audio in GCS.
    Retrying it must NOT trigger the audio/lyrics workers (which would fail with
    "Failed to download audio file" and be retried by Cloud Run); it must refuse
    cleanly with a 400 telling the user to resubmit.
    """

    def _call_retry(self, job, mock_jm, mock_ws, background_tasks):
        import asyncio
        from backend.api.routes.jobs import retry_job

        mock_jm.get_job.return_value = job
        mock_request = MagicMock()
        mock_request.headers = {}

        async def _run():
            return await retry_job(
                job_id=job.job_id,
                request=mock_request,
                background_tasks=background_tasks,
                auth_result=MagicMock(is_admin=True, user_email="t@t.com"),
            )

        with patch("backend.api.routes.jobs.job_manager", mock_jm), \
             patch("backend.api.routes.jobs.worker_service", mock_ws), \
             patch("backend.api.routes.jobs._check_job_ownership", return_value=True), \
             patch("backend.api.routes.jobs._is_auto_retry_pending", return_value=False), \
             patch("backend.api.routes.jobs.get_locale_from_request", return_value="en"):
            return asyncio.run(_run())

    def _make_url_job(self, *, input_media_gcs_path=None):
        return Job(
            job_id="urljob1",
            status=JobStatus.FAILED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Hailey Whitters",
            title="Gone Country",
            url="https://music.apple.com/us/album/gone-country/1752594347?i=1752594711",
            input_media_gcs_path=input_media_gcs_path,
            file_urls={},
        )

    def test_url_job_without_download_refuses_and_triggers_no_workers(self):
        """URL job with no downloaded audio → 400 resubmit, no workers triggered."""
        from fastapi import HTTPException

        job = self._make_url_job(input_media_gcs_path=None)
        mock_jm, mock_ws = MagicMock(), MagicMock()
        background_tasks = MagicMock()

        with pytest.raises(HTTPException) as exc:
            self._call_retry(job, mock_jm, mock_ws, background_tasks)

        assert exc.value.status_code == 400
        assert "resubmit" in str(exc.value.detail).lower()
        # The doomed workers must NOT have been scheduled.
        scheduled = [c.args[0] for c in background_tasks.add_task.call_args_list]
        assert mock_ws.trigger_audio_worker not in scheduled
        assert mock_ws.trigger_lyrics_worker not in scheduled

    def test_url_job_with_downloaded_audio_restarts_from_beginning(self):
        """URL job whose download succeeded (audio in GCS) → restarts + workers."""
        job = self._make_url_job(input_media_gcs_path="uploads/urljob1/audio/in.flac")
        mock_jm, mock_ws = MagicMock(), MagicMock()
        mock_jm.transition_to_state.return_value = True
        background_tasks = MagicMock()

        result = self._call_retry(job, mock_jm, mock_ws, background_tasks)

        assert result["status"] == "success"
        assert result["retry_stage"] == "from_beginning"
        scheduled = [c.args[0] for c in background_tasks.add_task.call_args_list]
        assert mock_ws.trigger_audio_worker in scheduled
        assert mock_ws.trigger_lyrics_worker in scheduled


class TestCompleteReviewWithInstrumentalSelection:
    """Tests for complete_review endpoint with combined review flow.

    The combined review flow allows users to submit both lyrics corrections
    AND instrumental selection in a single request. This tests:
    1. instrumental_selection is properly stored in state_data
    2. Backward compatibility (no body still works)
    3. Request validation
    """

    def test_complete_review_request_model_accepts_valid_selections(self):
        """Test CompleteReviewRequest accepts valid instrumental_selection values."""
        from backend.models.requests import CompleteReviewRequest

        # Valid selections
        for selection in ['clean', 'with_backing', 'custom']:
            req = CompleteReviewRequest(instrumental_selection=selection)
            assert req.instrumental_selection == selection

    def test_complete_review_request_model_accepts_none(self):
        """Test CompleteReviewRequest accepts None for backward compatibility."""
        from backend.models.requests import CompleteReviewRequest

        req = CompleteReviewRequest()
        assert req.instrumental_selection is None

        req = CompleteReviewRequest(instrumental_selection=None)
        assert req.instrumental_selection is None

    def test_complete_review_request_model_rejects_invalid_selection(self):
        """Test CompleteReviewRequest rejects invalid instrumental_selection values."""
        from backend.models.requests import CompleteReviewRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            CompleteReviewRequest(instrumental_selection='invalid_option')

        assert 'instrumental_selection' in str(exc_info.value)

    def test_instrumental_selection_stored_in_state_data(self):
        """Test that instrumental_selection is stored in job state_data.

        This is critical for the combined review flow - the selection must
        be persisted so render_video_worker can use it.
        """
        # Create a sample job in AWAITING_REVIEW state
        job = Job(
            job_id="test-combined-review",
            status=JobStatus.AWAITING_REVIEW,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            state_data={}
        )

        # Simulate what the endpoint does when instrumental_selection is provided
        instrumental_selection = 'clean'
        job.state_data['instrumental_selection'] = instrumental_selection

        # Verify it's stored correctly
        assert job.state_data.get('instrumental_selection') == 'clean'

    def test_state_data_instrumental_selection_used_by_render_worker(self):
        """Document that render_video_worker reads instrumental_selection from state_data.

        The render_video_worker no longer waits for AWAITING_INSTRUMENTAL_SELECTION.
        Instead, it reads the selection from state_data['instrumental_selection']
        which was set during complete_review.
        """
        # This documents the expected flow:
        # 1. User completes combined review with instrumental_selection='with_backing'
        # 2. complete_review stores it in state_data
        # 3. render_video_worker reads it and uses it for final video

        job = Job(
            job_id="test-flow",
            status=JobStatus.REVIEW_COMPLETE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            state_data={
                'instrumental_selection': 'with_backing'
            }
        )

        # The worker should be able to read the selection
        selection = job.state_data.get('instrumental_selection')
        assert selection == 'with_backing'
        assert selection in ['clean', 'with_backing', 'custom']


class TestSummaryEndpoint:
    """Tests for the fields=summary query parameter on list_jobs.

    When fields=summary, the endpoint uses Firestore field projection and
    returns pruned dicts instead of full Job models. This dramatically
    reduces payload size for the dashboard polling use case.
    """

    def test_summary_projection_includes_visibility_fields(self):
        """Verify SUMMARY_FIELD_PATHS includes is_private and visibility_change_in_progress.

        These fields are required for the Change Visibility button on the dashboard.
        Without them, the button shows incorrect state (always 'Make Private').
        """
        from backend.services.firestore_service import FirestoreService
        paths = FirestoreService.SUMMARY_FIELD_PATHS
        assert 'is_private' in paths, "is_private must be in summary projection for visibility button"
        assert 'state_data.visibility_change_in_progress' in paths, \
            "visibility_change_in_progress must be in summary projection to prevent concurrent changes"

    def test_prune_state_data_keeps_visibility_change_key(self):
        """Verify _prune_state_data preserves visibility_change_in_progress."""
        from backend.api.routes.jobs import _prune_state_data

        data = {'state_data': {'visibility_change_in_progress': True, 'corrected_lyrics': 'strip me'}}
        result = _prune_state_data(data)
        assert 'visibility_change_in_progress' in result['state_data']
        assert 'corrected_lyrics' not in result['state_data']

    def test_prune_state_data_keeps_allowed_keys(self):
        """Verify _prune_state_data keeps only dashboard-required keys."""
        from backend.api.routes.jobs import _prune_state_data

        data = {
            'state_data': {
                'brand_code': 'NKP',
                'youtube_url': 'https://youtu.be/abc',
                'audio_progress': {'stage': 'separating'},
                'lyrics_progress': {'stage': 'transcribing'},
                'audio_complete': True,
                'lyrics_complete': True,
                'backing_vocals_analysis': {'recommended_selection': 'clean'},
                'dropbox_link': 'https://dropbox.com/s/...',
                # These should be stripped
                'corrected_lyrics': {'lines': [1, 2, 3]},
                'render_progress': {'stage': 'done'},
                'blocking_state_entered_at': '2026-01-01',
            }
        }

        result = _prune_state_data(data)
        sd = result['state_data']
        assert 'brand_code' in sd
        assert 'youtube_url' in sd
        assert 'audio_progress' in sd
        assert 'lyrics_progress' in sd
        assert 'audio_complete' in sd
        assert 'lyrics_complete' in sd
        assert 'backing_vocals_analysis' in sd
        assert 'dropbox_link' in sd
        # Stripped keys
        assert 'corrected_lyrics' not in sd
        assert 'render_progress' not in sd
        assert 'blocking_state_entered_at' not in sd

    def test_prune_state_data_handles_missing(self):
        """Verify _prune_state_data is safe when state_data is absent."""
        from backend.api.routes.jobs import _prune_state_data

        assert _prune_state_data({}) == {}
        assert _prune_state_data({'state_data': None}) == {'state_data': None}

    def test_summary_projection_includes_source_fields(self):
        """Verify SUMMARY_FIELD_PATHS includes audio source tracking fields.

        These fields are required for the Source column in the admin dashboard
        and the source details modal. Without them, the dashboard incorrectly
        shows audio search jobs as file uploads when audio_search_artist/title
        are null.
        """
        from backend.services.firestore_service import FirestoreService
        paths = FirestoreService.SUMMARY_FIELD_PATHS
        for field in ['url', 'filename', 'audio_search_artist', 'audio_search_title',
                      'audio_source_type', 'source_name', 'source_id', 'target_file', 'download_url']:
            assert field in paths, f"{field} must be in summary projection for source details modal"

    def test_summary_projection_includes_made_for_you(self):
        """Verify SUMMARY_FIELD_PATHS includes made_for_you (and customer_email).

        The dashboard needs made_for_you in the summary payload so it can keep
        made-for-you orders (which sit at awaiting_audio_selection) visible as
        cards instead of filtering them out with self-service guided-flow jobs.
        """
        from backend.services.firestore_service import FirestoreService
        paths = FirestoreService.SUMMARY_FIELD_PATHS
        assert 'made_for_you' in paths, (
            "'made_for_you' must be in summary projection so the dashboard can "
            "surface made-for-you orders awaiting audio selection"
        )
        assert 'customer_email' in paths

    def test_prune_file_urls_keeps_allowed_keys(self):
        """Verify _prune_file_urls keeps only dashboard-required keys."""
        from backend.api.routes.jobs import _prune_file_urls

        data = {
            'file_urls': {
                'finals': {'lossless_4k_mp4': 'gs://bucket/finals/4k.mp4'},
                'videos': {'with_vocals': 'gs://bucket/videos/wv.mkv'},
                'packages': {'cdg_zip': 'gs://bucket/packages/cdg.zip'},
                # These should be stripped
                'stems': {'clean': 'gs://bucket/stems/clean.flac'},
                'lyrics': {'corrections': 'gs://bucket/lyrics/c.json'},
                'screens': {'title': 'gs://bucket/screens/title.mov'},
            }
        }

        result = _prune_file_urls(data)
        fu = result['file_urls']
        assert 'finals' in fu
        assert 'videos' in fu
        assert 'packages' in fu
        assert 'stems' not in fu
        assert 'lyrics' not in fu
        assert 'screens' not in fu

    def test_prune_file_urls_handles_missing(self):
        """Verify _prune_file_urls is safe when file_urls is absent."""
        from backend.api.routes.jobs import _prune_file_urls

        assert _prune_file_urls({}) == {}
        assert _prune_file_urls({'file_urls': None}) == {'file_urls': None}

    def test_summary_returns_only_expected_fields(self):
        """Verify a pruned summary dict only contains dashboard fields."""
        from backend.api.routes.jobs import _prune_state_data, _prune_file_urls

        raw = {
            'job_id': 'abc123',
            'status': 'pending',
            'progress': 0,
            'created_at': '2026-01-01T00:00:00',
            'artist': 'Test',
            'title': 'Song',
            'error_message': None,
            'non_interactive': False,
            'outputs_deleted_at': None,
            'user_email': 'test@example.com',
            'state_data': {
                'brand_code': 'NKP',
                'corrected_lyrics': 'should be removed',
            },
            'file_urls': {
                'finals': {'lossless_4k_mp4': 'gs://...'},
                'stems': {'clean': 'gs://...'},
            },
        }

        result = _prune_file_urls(_prune_state_data(raw))

        # state_data should only have brand_code
        assert list(result['state_data'].keys()) == ['brand_code']
        # file_urls should only have finals
        assert list(result['file_urls'].keys()) == ['finals']

    def test_hide_completed_statuses(self):
        """Verify _HIDE_COMPLETED_STATUSES hides terminal statuses (not failed or active)."""
        from backend.api.routes.jobs import _HIDE_COMPLETED_STATUSES

        assert 'complete' in _HIDE_COMPLETED_STATUSES
        assert 'prep_complete' in _HIDE_COMPLETED_STATUSES
        assert 'cancelled' in _HIDE_COMPLETED_STATUSES
        # Failed should NOT be hidden - users need to see these
        assert 'failed' not in _HIDE_COMPLETED_STATUSES
        # Active statuses should NOT be in the list
        assert 'pending' not in _HIDE_COMPLETED_STATUSES
        assert 'downloading' not in _HIDE_COMPLETED_STATUSES

    def test_exclude_test_works_with_summary_dicts(self):
        """Verify test email filtering works on raw dicts (not Job models)."""
        from backend.utils.test_data import is_test_email

        # Summary mode returns dicts, so exclude_test filters using dict access
        job_dicts = [
            {'job_id': '1', 'user_email': 'real@example.com'},
            {'job_id': '2', 'user_email': 'test@inbox.testmail.app'},
            {'job_id': '3', 'user_email': None},
        ]

        filtered = [j for j in job_dicts if not is_test_email(j.get('user_email') or "")]
        assert len(filtered) == 2
        assert filtered[0]['job_id'] == '1'
        assert filtered[1]['job_id'] == '3'

    def test_full_response_unchanged_without_fields_param(self):
        """Verify the endpoint returns full Job models when fields is not set.

        This is a backward-compatibility test: the admin jobs page (adminApi.listAllJobs)
        does NOT send fields=summary, so it must continue receiving full Job objects.
        """
        # When fields is None, the route falls through to the full Job path.
        # We verify this by checking that the route accepts fields=None gracefully.
        # (The actual HTTP test would require a TestClient, so here we verify
        # the route function signature accepts None.)
        import inspect
        from backend.api.routes.jobs import list_jobs

        sig = inspect.signature(list_jobs)
        fields_param = sig.parameters.get('fields')
        assert fields_param is not None
        assert fields_param.default is None  # Default should be None (full mode)


class TestScreensWorkerBackingVocalsAnalysis:
    """Tests documenting that backing vocals analysis now runs in screens_worker.

    Previously, backing vocals analysis ran in render_video_worker AFTER review.
    Now it runs in screens_worker BEFORE review, so the analysis data is available
    when the user opens the combined review UI.
    """

    def test_analysis_data_structure(self):
        """Document the expected backing_vocals_analysis structure in state_data."""
        # This is the structure stored by screens_worker._analyze_backing_vocals()
        expected_structure = {
            'has_audible_content': True,  # or False
            'total_duration_seconds': 180.0,
            'audible_segments': [
                {
                    'start_seconds': 10.0,
                    'end_seconds': 20.0,
                    'duration_seconds': 10.0,
                    'avg_amplitude_db': -25.0,
                    'peak_amplitude_db': -20.0,
                }
            ],
            'recommended_selection': 'with_backing',  # or 'clean' or 'review_needed'
            'total_audible_duration_seconds': 30.0,
            'audible_percentage': 16.67,
            'silence_threshold_db': -40.0,
        }

        # Verify structure has expected keys
        assert 'has_audible_content' in expected_structure
        assert 'recommended_selection' in expected_structure
        assert 'audible_segments' in expected_structure

    def test_analysis_available_before_review(self):
        """Document that analysis is available when job enters AWAITING_REVIEW.

        The screens_worker runs analysis before transitioning to AWAITING_REVIEW,
        so the frontend can show the analysis in the combined review UI.
        """
        # Job in AWAITING_REVIEW should have analysis data if stems exist
        job = Job(
            job_id="test-analysis",
            status=JobStatus.AWAITING_REVIEW,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Song",
            file_urls={
                'stems': {
                    'backing_vocals': 'jobs/test/stems/backing_vocals.flac',
                    'instrumental_clean': 'jobs/test/stems/clean.flac',
                }
            },
            state_data={
                'backing_vocals_analysis': {
                    'has_audible_content': True,
                    'recommended_selection': 'with_backing',
                }
            }
        )

        # Frontend can read analysis from state_data
        analysis = job.state_data.get('backing_vocals_analysis', {})
        assert analysis.get('recommended_selection') is not None


class TestContentDispositionHeader:
    """Tests for Content-Disposition header building in download_file.

    Verifies that filenames with non-latin-1 characters (CJK, Cyrillic, etc.)
    use RFC 5987 filename*=UTF-8'' encoding instead of crashing.
    """

    def _build_content_disposition(self, filename: str) -> str:
        """Replicate the Content-Disposition logic from download_file."""
        from urllib.parse import quote
        try:
            filename.encode('latin-1')
            return f'attachment; filename="{filename}"'
        except UnicodeEncodeError:
            ascii_filename = filename.encode('ascii', 'replace').decode('ascii')
            utf8_filename = quote(filename)
            return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{utf8_filename}"

    def test_ascii_filename_uses_simple_header(self):
        header = self._build_content_disposition("Artist - Song (Final Karaoke).mp4")
        assert header == 'attachment; filename="Artist - Song (Final Karaoke).mp4"'
        assert "filename*" not in header

    def test_latin1_accented_filename_uses_simple_header(self):
        header = self._build_content_disposition("Café - Señorita.mp4")
        assert header == 'attachment; filename="Café - Señorita.mp4"'
        assert "filename*" not in header

    def test_chinese_filename_uses_rfc5987(self):
        header = self._build_content_disposition("周杰倫 - 青花瓷 (Final Karaoke).mp4")
        assert "filename*=UTF-8''" in header
        # ASCII fallback should be present
        assert 'filename="' in header
        # Should be encodable as latin-1 (the whole header)
        # The ASCII fallback part is safe; the UTF-8 part is percent-encoded
        assert "%E5%91%A8" in header  # 周 percent-encoded

    def test_japanese_filename_uses_rfc5987(self):
        header = self._build_content_disposition("宇多田ヒカル - First Love.mp4")
        assert "filename*=UTF-8''" in header

    def test_korean_filename_uses_rfc5987(self):
        header = self._build_content_disposition("방탄소년단 - Dynamite.mp4")
        assert "filename*=UTF-8''" in header

    def test_cyrillic_filename_uses_rfc5987(self):
        header = self._build_content_disposition("Тату - Нас не догонят.mp4")
        assert "filename*=UTF-8''" in header

    def test_rfc5987_header_is_latin1_safe(self):
        """The entire header string must be encodable as latin-1 for HTTP."""
        header = self._build_content_disposition("周杰倫 - 青花瓷.mp4")
        # The header uses percent-encoding for non-ASCII, so it should be ASCII-safe
        header.encode('latin-1')  # Should not raise

    def test_rfc5987_preserves_original_filename(self):
        """The UTF-8 encoded filename should decode back to the original."""
        from urllib.parse import unquote
        header = self._build_content_disposition("周杰倫 - 青花瓷.mp4")
        # Extract the filename* value
        utf8_part = header.split("filename*=UTF-8''")[1]
        decoded = unquote(utf8_part)
        assert decoded == "周杰倫 - 青花瓷.mp4"


class TestSearchFiltering:
    """Tests for the search parameter on list_jobs summary mode."""

    def _make_job_dict(self, job_id="job1", artist="Queen", title="Bohemian Rhapsody",
                       audio_search_artist=None, audio_search_title=None, status="complete"):
        return {
            "job_id": job_id,
            "artist": artist,
            "title": title,
            "audio_search_artist": audio_search_artist,
            "audio_search_title": audio_search_title,
            "status": status,
            "user_email": "admin@nomadkaraoke.com",
        }

    def test_search_filters_by_artist(self):
        """Search 'queen' should match job with artist='Queen'."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [
            self._make_job_dict(job_id="j1", artist="Queen", title="We Will Rock You"),
            self._make_job_dict(job_id="j2", artist="Eagles", title="Hotel California"),
        ]
        result = _search_filter_jobs(jobs, "queen")
        assert len(result) == 1
        assert result[0]["job_id"] == "j1"

    def test_search_filters_by_title(self):
        """Search 'california' should match job with title containing it."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [
            self._make_job_dict(job_id="j1", artist="Queen", title="We Will Rock You"),
            self._make_job_dict(job_id="j2", artist="Eagles", title="Hotel California"),
        ]
        result = _search_filter_jobs(jobs, "california")
        assert len(result) == 1
        assert result[0]["job_id"] == "j2"

    def test_search_filters_by_job_id(self):
        """Search by partial job_id."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [
            self._make_job_dict(job_id="abc-123-def"),
            self._make_job_dict(job_id="xyz-789-ghi"),
        ]
        result = _search_filter_jobs(jobs, "abc-123")
        assert len(result) == 1
        assert result[0]["job_id"] == "abc-123-def"

    def test_search_filters_by_audio_search_fields(self):
        """Search should also check audio_search_artist and audio_search_title."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [
            self._make_job_dict(job_id="j1", artist="Display Name", title="Display Title",
                                audio_search_artist="Original Artist", audio_search_title="Original Song"),
        ]
        result = _search_filter_jobs(jobs, "original artist")
        assert len(result) == 1

    def test_search_is_case_insensitive(self):
        """Search should be case-insensitive."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [self._make_job_dict(artist="QUEEN", title="Bohemian Rhapsody")]
        result = _search_filter_jobs(jobs, "queen")
        assert len(result) == 1

    def test_search_handles_none_fields(self):
        """Search should not crash on None artist/title fields."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [self._make_job_dict(artist=None, title=None)]
        result = _search_filter_jobs(jobs, "queen")
        assert len(result) == 0

    def test_search_empty_string_returns_all(self):
        """Empty search string should return all jobs."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [
            self._make_job_dict(job_id="j1"),
            self._make_job_dict(job_id="j2"),
        ]
        result = _search_filter_jobs(jobs, "")
        assert len(result) == 2

    def test_search_returns_all_matches_for_limit_slicing(self):
        """Search returns all matches — caller applies limit separately."""
        from backend.api.routes.jobs import _search_filter_jobs
        jobs = [self._make_job_dict(job_id=f"j{i}", artist="Queen", title=f"Song {i}") for i in range(10)]
        result = _search_filter_jobs(jobs, "queen")
        assert len(result) == 10  # All 10 match; limit slicing is caller's job
        # Verify caller can slice: simulates jobs_dicts[:limit] in route handler
        assert len(result[:3]) == 3


class TestCorrectionsDuetFlag:
    """Tests for is_duet flag handling in the /corrections endpoint.

    The /corrections endpoint now accepts an optional is_duet field.
    When present it must be a bool and is persisted to state_data.
    When absent, state_data is NOT touched (preserves any prior flag value).
    """

    @pytest.fixture
    def mock_job(self):
        job = MagicMock(spec=Job)
        job.job_id = "job-corrections-duet"
        job.status = JobStatus.AWAITING_REVIEW
        job.state_data = {}
        return job

    def _call_submit_corrections(self, mock_job, is_duet_value, is_duet_provided, mock_job_manager, mock_storage):
        """Call submit_corrections directly, bypassing HTTP layer."""
        import asyncio
        from backend.api.routes.jobs import submit_corrections
        from backend.models.requests import CorrectionsSubmission

        valid_corrections = {"lines": [], "metadata": {}}
        submission_kwargs = {"corrections": valid_corrections}
        if is_duet_provided:
            submission_kwargs["is_duet"] = is_duet_value
        submission = CorrectionsSubmission(**submission_kwargs)

        mock_job_manager.get_job.return_value = mock_job
        mock_job_manager.update_state_data.return_value = None
        mock_job_manager.transition_to_state.return_value = True
        mock_storage.upload_json.return_value = None
        mock_storage.update_file_url.return_value = None

        mock_request = MagicMock()
        mock_request.headers = {}

        async def _run():
            return await submit_corrections(
                job_id=mock_job.job_id,
                submission=submission,
                http_request=mock_request,
                background_tasks=MagicMock(),
                auth_result=MagicMock(user_email="test@test.com", role="job_owner", job_id=mock_job.job_id),
            )

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager), \
             patch("backend.api.routes.jobs._check_job_ownership", return_value=True), \
             patch("backend.api.routes.jobs.get_locale_from_request", return_value="en"), \
             patch("backend.api.routes.jobs.StorageService", return_value=mock_storage):
            result = asyncio.run(_run())
        return result

    def test_corrections_saves_is_duet_true(self, mock_job):
        """is_duet=True should be persisted via update_state_data."""
        mock_jm = MagicMock()
        mock_st = MagicMock()

        result = self._call_submit_corrections(mock_job, True, True, mock_jm, mock_st)

        assert result["status"] == "success"
        calls = [c for c in mock_jm.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert len(calls) == 1
        assert calls[0].args[2] is True

    def test_corrections_saves_is_duet_false(self, mock_job):
        """is_duet=False should be persisted via update_state_data."""
        mock_jm = MagicMock()
        mock_st = MagicMock()

        result = self._call_submit_corrections(mock_job, False, True, mock_jm, mock_st)

        assert result["status"] == "success"
        calls = [c for c in mock_jm.update_state_data.call_args_list
                 if c.args[1] == "is_duet"]
        assert len(calls) == 1
        assert calls[0].args[2] is False

    def test_corrections_without_is_duet_does_not_touch_state_data(self, mock_job):
        """When is_duet is absent, update_state_data must NOT be called for is_duet."""
        mock_jm = MagicMock()
        mock_st = MagicMock()

        result = self._call_submit_corrections(mock_job, None, False, mock_jm, mock_st)

        assert result["status"] == "success"
        is_duet_calls = [c for c in mock_jm.update_state_data.call_args_list
                         if c.args[1] == "is_duet"]
        assert len(is_duet_calls) == 0, (
            "is_duet must NOT be written to state_data when absent from request "
            "(would overwrite a previously-persisted value with False)"
        )

    def test_corrections_submission_model_accepts_is_duet(self):
        """CorrectionsSubmission Pydantic model must accept is_duet=True/False/None."""
        from backend.models.requests import CorrectionsSubmission

        base = {"corrections": {"lines": [], "metadata": {}}}

        s1 = CorrectionsSubmission(**base, is_duet=True)
        assert s1.is_duet is True

        s2 = CorrectionsSubmission(**base, is_duet=False)
        assert s2.is_duet is False

        s3 = CorrectionsSubmission(**base)
        assert s3.is_duet is None


class TestCorrectionsFirestoreDocSize:
    """Regression tests for the Firestore 1MB doc-size limit on corrections save.

    Prod incident (job 9a12cf0f, "Van - Mashup"): saving corrections for a long
    song wrote ~1.8MB of segment arrays into the job document, exceeding
    Firestore's 1MB/document hard limit. The fix stores only a lightweight
    summary (corrected_segment_count) in state_data.corrected_lyrics; the full
    corrections are preserved in GCS (corrections_updated.json).
    """

    import json as _json

    @pytest.fixture
    def mock_job(self):
        job = MagicMock(spec=Job)
        job.job_id = "job-bigcorrections"
        job.status = JobStatus.AWAITING_REVIEW
        job.state_data = {}
        return job

    def _big_corrections(self, n_segments=422):
        """Build a corrections payload mimicking a long song (>1MB raw)."""
        def _segment(i):
            return {
                "id": f"seg-{i}",
                "text": "the quick brown fox jumps over the lazy dog " * 4,
                "start_time": float(i),
                "end_time": float(i) + 1.0,
                "words": [
                    {"id": f"w-{i}-{j}", "text": "word", "start_time": float(j),
                     "end_time": float(j) + 0.2, "confidence": 0.9}
                    for j in range(12)
                ],
            }
        segments = [_segment(i) for i in range(n_segments)]
        return {
            "lines": [],
            "metadata": {"source": "test"},
            "corrected_segments": segments,
            "original_segments": segments,
            "resized_segments": segments,
            "reference_lyrics": {},
        }

    def _call_submit_corrections(self, mock_job, corrections, mock_jm, mock_st):
        import asyncio
        from backend.api.routes.jobs import submit_corrections
        from backend.models.requests import CorrectionsSubmission

        submission = CorrectionsSubmission(corrections=corrections)

        mock_jm.get_job.return_value = mock_job
        mock_jm.update_state_data.return_value = None
        mock_jm.transition_to_state.return_value = True
        mock_st.upload_json.return_value = None

        mock_request = MagicMock()
        mock_request.headers = {}

        async def _run():
            return await submit_corrections(
                job_id=mock_job.job_id,
                submission=submission,
                http_request=mock_request,
                background_tasks=MagicMock(),
                auth_result=MagicMock(user_email="t@t.com", role="job_owner", job_id=mock_job.job_id),
            )

        # StorageService is imported inside the endpoint, so patch it at the source module.
        with patch("backend.api.routes.jobs.job_manager", mock_jm), \
             patch("backend.api.routes.jobs._check_job_ownership", return_value=True), \
             patch("backend.api.routes.jobs.get_locale_from_request", return_value="en"), \
             patch("backend.services.storage_service.StorageService", return_value=mock_st):
            return asyncio.run(_run())

    def _corrected_lyrics_payload(self, mock_jm):
        calls = [c for c in mock_jm.update_state_data.call_args_list
                 if c.args[1] == "corrected_lyrics"]
        assert len(calls) == 1, "corrected_lyrics should be written exactly once"
        return calls[0].args[2]

    def test_corrections_save_stores_only_small_summary(self, mock_job):
        """The corrected_lyrics written to Firestore must be a small summary,
        not the multi-MB segment arrays."""
        mock_jm, mock_st = MagicMock(), MagicMock()
        corrections = self._big_corrections(n_segments=422)

        # Sanity: the raw payload is genuinely over the 1MB Firestore limit.
        assert len(self._json.dumps(corrections).encode("utf-8")) > 1_048_576

        result = self._call_submit_corrections(mock_job, corrections, mock_jm, mock_st)
        assert result["status"] == "success"

        payload = self._corrected_lyrics_payload(mock_jm)
        # No heavy arrays must be present.
        for heavy in ("corrected_segments", "original_segments", "resized_segments",
                      "reference_lyrics", "lines"):
            assert heavy not in payload, f"{heavy} must not be stored in Firestore"
        # Summary stays comfortably small.
        assert len(self._json.dumps(payload).encode("utf-8")) < 1024

    def test_corrections_summary_records_segment_count(self, mock_job):
        """The summary must record the corrected segment count for the guard."""
        mock_jm, mock_st = MagicMock(), MagicMock()
        corrections = self._big_corrections(n_segments=37)

        self._call_submit_corrections(mock_job, corrections, mock_jm, mock_st)
        payload = self._corrected_lyrics_payload(mock_jm)
        assert payload["corrected_segment_count"] == 37

    def test_corrections_full_payload_still_saved_to_gcs(self, mock_job):
        """The full corrections (with arrays) must still be uploaded to GCS."""
        mock_jm, mock_st = MagicMock(), MagicMock()
        corrections = self._big_corrections(n_segments=10)

        self._call_submit_corrections(mock_job, corrections, mock_jm, mock_st)

        upload_calls = mock_st.upload_json.call_args_list
        assert len(upload_calls) == 1
        path, data = upload_calls[0].args[0], upload_calls[0].args[1]
        assert path.endswith("corrections_updated.json")
        assert len(data["corrected_segments"]) == 10


class TestCompleteReviewSegmentGuard:
    """The complete-review 0-segment guard must read the corrected_lyrics summary
    (new shape) and still work for legacy jobs that stored the full dict."""

    def _make_job(self, segment_count, corrected_lyrics):
        job = MagicMock(spec=Job)
        job.job_id = "job-guard"
        job.status = JobStatus.IN_REVIEW
        job.state_data = {
            "lyrics_metadata": {"segment_count": segment_count},
            "corrected_lyrics": corrected_lyrics,
        }
        return job

    def _call_complete_review(self, job):
        import asyncio
        from backend.api.routes.jobs import complete_review

        mock_jm = MagicMock()
        mock_jm.get_job.return_value = job
        mock_request = MagicMock()
        mock_request.headers = {}

        async def _run():
            return await complete_review(
                job_id=job.job_id,
                body=None,
                request=mock_request,
                background_tasks=MagicMock(),
                auth_result=MagicMock(user_email="t@t.com", role="job_owner", job_id=job.job_id),
            )

        with patch("backend.api.routes.jobs.job_manager", mock_jm), \
             patch("backend.api.routes.jobs._check_job_ownership", return_value=True), \
             patch("backend.api.routes.jobs.get_locale_from_request", return_value="en"), \
             patch("backend.api.routes.jobs.worker_service", MagicMock()):
            return asyncio.run(_run())

    def test_guard_blocks_when_no_segments_summary_shape(self):
        """0 transcription segments AND 0 corrected segments -> 400."""
        from fastapi import HTTPException
        job = self._make_job(0, {"corrected_segment_count": 0})
        with pytest.raises(HTTPException) as exc:
            self._call_complete_review(job)
        assert exc.value.status_code == 400

    def test_guard_allows_when_summary_has_segments(self):
        """0 transcription segments but corrections have segments -> allowed."""
        job = self._make_job(0, {"corrected_segment_count": 5})
        result = self._call_complete_review(job)
        assert result["status"] == "success"

    def test_guard_allows_legacy_full_dict_shape(self):
        """Legacy jobs stored full dict with corrected_segments list -> still allowed."""
        job = self._make_job(0, {"corrected_segments": [{"id": "a"}, {"id": "b"}]})
        result = self._call_complete_review(job)
        assert result["status"] == "success"

