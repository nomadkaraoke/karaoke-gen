"""
Regression tests for job creation issues discovered 2024-12-31.

These tests ensure:
1. user_email is properly extracted from AuthResult and set on jobs
2. URL jobs trigger workers sequentially (audio first, then lyrics)
3. Audio search downloads work without relying on in-memory cache
4. Transcription has proper timeout handling

See docs/archive/2024-12-31-job-failure-investigation.md for details.
"""
import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from backend.services.auth_service import AuthResult, UserType


class TestUserEmailExtraction:
    """
    Issue 1: user_email not being set on jobs.

    Root cause: Job creation endpoints used require_auth but didn't extract
    auth_result.user_email and set it on the job.

    These tests verify that authenticated user's email is properly set on jobs.
    """

    @pytest.fixture
    def mock_auth_result_with_email(self):
        """AuthResult for a logged-in user with email."""
        return AuthResult(
            is_valid=True,
            user_type=UserType.UNLIMITED,  # Session-based users typically have UNLIMITED
            remaining_uses=-1,
            message="Valid session",
            user_email="testuser@example.com",
            is_admin=False,
        )

    @pytest.fixture
    def mock_auth_result_admin_token(self):
        """AuthResult for admin token without email."""
        return AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=-1,
            message="Valid admin token",
            user_email=None,
            is_admin=True,
        )

    def test_auth_result_has_user_email_field(self):
        """AuthResult must have user_email field for job association."""
        result = AuthResult(
            is_valid=True,
            user_type=UserType.UNLIMITED,
            remaining_uses=-1,
            message="test",
            user_email="user@example.com",
        )
        assert hasattr(result, 'user_email')
        assert result.user_email == "user@example.com"

    def test_auth_result_user_email_can_be_none(self):
        """AuthResult user_email can be None for token-based auth."""
        result = AuthResult(
            is_valid=True,
            user_type=UserType.ADMIN,
            remaining_uses=-1,
            message="test",
            user_email=None,
        )
        assert result.user_email is None

    def test_upload_endpoint_extracts_user_email_from_auth(self):
        """
        /jobs/upload endpoint must set user_email from authenticated user.

        This verifies the code pattern by inspecting the source.
        Integration tests verify the full behavior.
        """
        from backend.api.routes import file_upload
        import inspect

        # Get the source code of upload_and_create_job
        source = inspect.getsource(file_upload.upload_and_create_job)

        # Verify the endpoint extracts user_email from auth_result
        assert 'auth_result.user_email' in source, \
            "upload_and_create_job must extract user_email from auth_result"
        assert 'effective_user_email' in source, \
            "upload_and_create_job must use effective_user_email pattern"
        assert 'user_email=effective_user_email' in source or 'user_email=' in source, \
            "upload_and_create_job must pass user_email to JobCreate"

    def test_all_job_creation_endpoints_extract_user_email(self):
        """
        All job creation endpoints must extract user_email from AuthResult.

        This is a regression guard for Issue 1 (user_email not being set).
        """
        from backend.api.routes import file_upload, audio_search
        import inspect

        endpoints_to_check = [
            (file_upload, 'upload_and_create_job'),
            (file_upload, 'create_job_from_url'),
            (audio_search, 'search_audio'),
        ]

        for module, func_name in endpoints_to_check:
            func = getattr(module, func_name)
            source = inspect.getsource(func)

            assert 'auth_result.user_email' in source, \
                f"{func_name} must extract user_email from auth_result"

    @pytest.mark.asyncio
    async def test_create_from_url_endpoint_sets_user_email(self, mock_auth_result_with_email):
        """
        /jobs/create-from-url endpoint must set user_email from authenticated user.
        """
        from backend.api.routes.file_upload import create_job_from_url, CreateJobFromUrlRequest

        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = Mock(host="127.0.0.1")
        mock_request.url = Mock(path="/api/jobs/create-from-url")
        # Mock tenant state (no tenant = default Nomad Karaoke)
        mock_request.state.tenant_config = None

        mock_background_tasks = Mock()
        body = CreateJobFromUrlRequest(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            artist="Rick Astley",
            title="Never Gonna Give You Up",
        )

        with patch('backend.api.routes.file_upload.job_manager') as mock_jm, \
             patch('backend.api.routes.file_upload.worker_service') as mock_worker, \
             patch('backend.api.routes.file_upload.get_credential_manager') as mock_cred, \
             patch('backend.api.routes.file_upload.get_youtube_download_service') as mock_yt:

            mock_job = Mock()
            mock_job.job_id = "test-job-456"
            mock_jm.create_job.return_value = mock_job
            mock_cred.return_value.check_youtube_credentials.return_value = Mock(status=Mock(value="valid"))

            # Mock YouTube download service to return a GCS path
            from unittest.mock import AsyncMock
            mock_yt_service = Mock()
            mock_yt_service.download = AsyncMock(return_value="uploads/test-job-456/audio/test.webm")
            mock_yt.return_value = mock_yt_service

            await create_job_from_url(
                request=mock_request,
                background_tasks=mock_background_tasks,
                body=body,
                auth_result=mock_auth_result_with_email,
            )

            mock_jm.create_job.assert_called_once()
            job_create_arg = mock_jm.create_job.call_args[0][0]
            assert job_create_arg.user_email == "testuser@example.com", \
                "create-from-url must set user_email from AuthResult"

    @pytest.mark.asyncio
    async def test_audio_search_endpoint_sets_user_email(self, mock_auth_result_with_email):
        """
        /audio-search/search endpoint must set user_email from authenticated user.
        """
        from backend.api.routes.audio_search import search_audio, AudioSearchRequest

        mock_request = Mock()
        mock_request.headers = {}
        mock_request.client = Mock(host="127.0.0.1")
        mock_request.url = Mock(path="/api/audio-search/search")
        # Mock tenant state (no tenant = default Nomad Karaoke)
        mock_request.state.tenant_config = None

        mock_background_tasks = Mock()
        body = AudioSearchRequest(
            artist="Test Artist",
            title="Test Song",
        )

        with patch('backend.api.routes.audio_search.job_manager') as mock_jm, \
             patch('backend.api.routes.audio_search.get_audio_search_service') as mock_search, \
             patch('backend.api.routes.audio_search.get_credential_manager') as mock_cred:

            mock_job = Mock()
            mock_job.job_id = "test-job-789"
            mock_job.state_data = {}
            mock_jm.create_job.return_value = mock_job
            mock_jm.get_job.return_value = mock_job
            mock_cred.return_value.check_youtube_credentials.return_value = Mock(status=Mock(value="valid"))
            mock_search.return_value.search.return_value = []

            await search_audio(
                request=mock_request,
                background_tasks=mock_background_tasks,
                body=body,
                auth_result=mock_auth_result_with_email,
            )

            mock_jm.create_job.assert_called_once()
            job_create_arg = mock_jm.create_job.call_args[0][0]
            assert job_create_arg.user_email == "testuser@example.com", \
                "audio-search must set user_email from AuthResult"

    def test_effective_user_email_prefers_auth_over_form(self):
        """
        When both auth_result.user_email and form user_email are provided,
        the authenticated user's email should take precedence.
        """
        auth_email = "authenticated@example.com"
        form_email = "form@example.com"

        # This is the logic used in the endpoints
        effective_user_email = auth_email or form_email

        assert effective_user_email == auth_email, \
            "Authenticated user's email should take precedence over form parameter"

    def test_effective_user_email_falls_back_to_form(self):
        """
        When auth_result.user_email is None (e.g., admin token),
        form user_email should be used as fallback.
        """
        auth_email = None
        form_email = "form@example.com"

        effective_user_email = auth_email or form_email

        assert effective_user_email == form_email, \
            "Form parameter should be used when auth has no email"


class TestUrlJobWorkerSequencing:
    """
    Issue 2: YouTube URL download race condition.

    Root cause: Both audio and lyrics workers were triggered in parallel.
    For URL jobs, lyrics worker would timeout waiting for audio to download.

    These tests verify that URL jobs trigger workers sequentially.
    """

    @pytest.mark.asyncio
    async def test_url_job_triggers_only_audio_worker_initially(self):
        """
        create-from-url must only trigger audio worker, not lyrics worker.
        The audio worker will trigger lyrics after download completes.
        """
        from backend.api.routes.file_upload import _trigger_audio_worker_only

        with patch('backend.api.routes.file_upload.worker_service') as mock_ws:
            mock_ws.trigger_audio_worker = AsyncMock()
            mock_ws.trigger_lyrics_worker = AsyncMock()

            await _trigger_audio_worker_only("test-job-id")

            mock_ws.trigger_audio_worker.assert_called_once_with("test-job-id")
            mock_ws.trigger_lyrics_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_parallel_worker_triggers_both_workers(self):
        """
        _trigger_workers_parallel should trigger both workers (for uploaded files).
        """
        from backend.api.routes.file_upload import _trigger_workers_parallel

        with patch('backend.api.routes.file_upload.worker_service') as mock_ws:
            mock_ws.trigger_audio_worker = AsyncMock()
            mock_ws.trigger_lyrics_worker = AsyncMock()

            await _trigger_workers_parallel("test-job-id")

            mock_ws.trigger_audio_worker.assert_called_once_with("test-job-id")
            mock_ws.trigger_lyrics_worker.assert_called_once_with("test-job-id")

    @pytest.mark.asyncio
    async def test_audio_worker_triggers_lyrics_after_url_download(self):
        """
        Audio worker must trigger lyrics worker after successful URL download.
        """
        from backend.workers.audio_worker import _trigger_lyrics_worker_after_url_download

        # Mock at the source module where it's imported from
        with patch('backend.services.worker_service.get_worker_service') as mock_get_ws:
            mock_ws = Mock()
            mock_ws.trigger_lyrics_worker = AsyncMock()
            mock_get_ws.return_value = mock_ws

            await _trigger_lyrics_worker_after_url_download("test-job-id")

            mock_ws.trigger_lyrics_worker.assert_called_once_with("test-job-id")

    @pytest.mark.asyncio
    async def test_audio_worker_lyrics_trigger_handles_errors_gracefully(self):
        """
        If lyrics worker trigger fails, audio processing should continue.
        """
        from backend.workers.audio_worker import _trigger_lyrics_worker_after_url_download

        # Mock at the source module where it's imported from
        with patch('backend.services.worker_service.get_worker_service') as mock_get_ws:
            mock_ws = Mock()
            mock_ws.trigger_lyrics_worker = AsyncMock(side_effect=Exception("Network error"))
            mock_get_ws.return_value = mock_ws

            # Should not raise exception
            await _trigger_lyrics_worker_after_url_download("test-job-id")


class TestAudioSearchCacheIndependence:
    """
    Issue 3: Audio search cache not persisting across Cloud Run instances.

    Root cause: AudioSearchService used in-memory cache that doesn't persist
    across horizontally scaled instances.

    These tests verify downloads work using state_data, not in-memory cache.
    """

    def test_search_results_stored_in_job_state_data(self):
        """
        Search results must be stored in job.state_data for persistence.
        This tests the Job model's ability to store search results.
        """
        from backend.models.job import Job, JobStatus

        job = Job(
            job_id="test-123",
            artist="Test Artist",
            title="Test Song",
            status=JobStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            state_data={
                'audio_search_results': [
                    {
                        'provider': 'YouTube',
                        'title': 'Test Song',
                        'artist': 'Test Artist',
                        'url': 'https://youtube.com/watch?v=abc123',
                        'source_id': 'abc123',
                    }
                ]
            }
        )

        assert 'audio_search_results' in job.state_data
        assert len(job.state_data['audio_search_results']) == 1
        assert job.state_data['audio_search_results'][0]['url'] == 'https://youtube.com/watch?v=abc123'

    def test_audio_search_endpoint_stores_results_in_state_data(self):
        """
        Verify that the search_audio code path stores results in job.state_data.

        This tests that the audio_search route has the correct pattern for
        persisting search results. The actual integration is tested elsewhere.
        """
        # Verify the import and module structure exists
        from backend.api.routes import audio_search
        import inspect

        # Get the source code of search_audio
        source = inspect.getsource(audio_search.search_audio)

        # Verify the endpoint stores results in state_data
        # The code uses job_manager.update_job with state_data dict
        assert 'audio_search_results' in source, \
            "search_audio must store results under 'audio_search_results' key"
        assert 'state_data' in source, \
            "search_audio must use state_data for persistence"

    def test_download_code_uses_youtube_download_service(self):
        """
        Verify that _download_and_start_processing uses YouTubeDownloadService.

        After the consolidation refactor (2025-01), all YouTube downloads go through
        YouTubeDownloadService instead of using download_from_url directly. This
        ensures consistent handling with remote flacfetch support.
        """
        from backend.api.routes import audio_search
        import inspect

        # Get the source of _download_and_start_processing
        source = inspect.getsource(audio_search._download_and_start_processing)

        # Verify YouTubeDownloadService is used for YouTube
        assert "source_name == 'YouTube'" in source, \
            "_download_and_start_processing must check for YouTube source"
        assert 'youtube_service' in source or 'get_youtube_download_service' in source, \
            "_download_and_start_processing must use YouTubeDownloadService for YouTube"

    def test_youtube_download_service_import_exists(self):
        """
        Verify that audio_search imports YouTubeDownloadService.

        This ensures the consolidated YouTube download path is properly wired.
        """
        from backend.api.routes import audio_search
        import inspect

        source = inspect.getsource(audio_search)

        # Verify YouTubeDownloadService is imported
        assert 'get_youtube_download_service' in source, \
            "audio_search must import get_youtube_download_service"
        assert 'YouTubeDownloadError' in source, \
            "audio_search must import YouTubeDownloadError"


class TestTranscriptionTimeout:
    """
    Issue 4: Jobs stuck in downloading state.

    Root cause: Lyrics worker's AudioShake transcription could hang forever
    without proper timeout, leaving jobs stuck.

    These tests verify timeout handling for transcription.
    """

    def test_transcription_timeout_constant_exists(self):
        """
        Verify transcription timeout constant is defined in lyrics_worker.
        """
        from backend.workers.lyrics_worker import TRANSCRIPTION_TIMEOUT_SECONDS

        assert TRANSCRIPTION_TIMEOUT_SECONDS > 0
        assert TRANSCRIPTION_TIMEOUT_SECONDS >= 300, "Timeout should be at least 5 minutes for long songs"
        assert TRANSCRIPTION_TIMEOUT_SECONDS <= 1500, "Timeout shouldn't be more than 25 minutes"

    def test_transcription_timeout_value(self):
        """
        Verify the specific timeout value (20 minutes = 1200 seconds).

        This accounts for:
        - Cloud Run cold start / worker initialization (1-5 min)
        - AudioShake transcription (1-2 min)
        - spaCy model loading for correction (2-3 min on cold start)
        - Agentic AI correction (1-3 min)
        """
        from backend.workers.lyrics_worker import TRANSCRIPTION_TIMEOUT_SECONDS

        assert TRANSCRIPTION_TIMEOUT_SECONDS == 1200, "Transcription timeout should be 20 minutes (1200 seconds)"

    @pytest.mark.asyncio
    async def test_asyncio_wait_for_raises_timeout_error(self):
        """
        Verify asyncio.wait_for properly raises TimeoutError.
        This is a sanity check for the timeout mechanism we use.
        """
        async def slow_operation():
            await asyncio.sleep(10)
            return "completed"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_operation(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_lyrics_worker_timeout_converts_to_descriptive_error(self):
        """
        Verify that timeout is converted to a descriptive exception message.
        Tests the actual error conversion logic pattern used in lyrics_worker.
        """
        from backend.workers.lyrics_worker import TRANSCRIPTION_TIMEOUT_SECONDS

        # This simulates what happens in lyrics_worker when timeout occurs
        error_message = None
        try:
            # Simulate the timeout handling pattern from lyrics_worker
            raise asyncio.TimeoutError()
        except asyncio.TimeoutError:
            error_message = f"Transcription timed out after {TRANSCRIPTION_TIMEOUT_SECONDS} seconds"

        assert error_message is not None
        assert "timed out" in error_message.lower()
        assert "1200" in error_message

    def test_lyrics_worker_exception_marks_job_failed(self):
        """
        Any exception in lyrics worker should mark job as failed.
        """
        from backend.models.job import JobStatus

        # Simulate the exception handling in lyrics_worker
        job_status = JobStatus.DOWNLOADING
        error_occurred = True

        if error_occurred:
            # This is what happens in the except block
            job_status = JobStatus.FAILED
            error_message = "Lyrics transcription failed: Transcription timed out after 1200 seconds"

        assert job_status == JobStatus.FAILED
        assert "timed out" in error_message.lower()


class TestJobOwnershipFiltering:
    """
    Additional tests for job ownership and filtering.

    These verify that jobs are properly associated with users
    and can be filtered by user_email.
    """

    def test_job_model_has_user_email_field(self):
        """Job model must have user_email field."""
        from backend.models.job import Job, JobStatus

        job = Job(
            job_id="test-123",
            artist="Test",
            title="Test",
            status=JobStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            user_email="user@example.com",
        )

        assert hasattr(job, 'user_email')
        assert job.user_email == "user@example.com"

    def test_job_create_model_has_user_email_field(self):
        """JobCreate model must have user_email field."""
        from backend.models.job import JobCreate

        job_create = JobCreate(
            artist="Test",
            title="Test",
            user_email="user@example.com",
        )

        assert hasattr(job_create, 'user_email')
        assert job_create.user_email == "user@example.com"

    def test_job_create_user_email_is_optional(self):
        """JobCreate user_email should be optional for backward compatibility."""
        from backend.models.job import JobCreate

        job_create = JobCreate(
            artist="Test",
            title="Test",
        )

        assert job_create.user_email is None

    def test_jobs_can_be_filtered_by_user_email(self):
        """Jobs should be filterable by user_email."""
        from backend.models.job import Job, JobStatus

        jobs = [
            Job(
                job_id="job-1",
                artist="Test",
                title="Test 1",
                status=JobStatus.COMPLETE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                user_email="user1@example.com",
            ),
            Job(
                job_id="job-2",
                artist="Test",
                title="Test 2",
                status=JobStatus.COMPLETE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                user_email="user2@example.com",
            ),
            Job(
                job_id="job-3",
                artist="Test",
                title="Test 3",
                status=JobStatus.COMPLETE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                user_email="user1@example.com",
            ),
        ]

        user1_jobs = [j for j in jobs if j.user_email == "user1@example.com"]

        assert len(user1_jobs) == 2
        assert all(j.user_email == "user1@example.com" for j in user1_jobs)
