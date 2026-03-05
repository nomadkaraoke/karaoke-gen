"""
Tests for audio select endpoint overrides (guided flow Step 3).

When the guided flow reaches Step 3 (Customize), the user can change:
- is_private: Whether the job should use private distribution
- display_artist: Override the display artist name
- display_title: Override the display title

These overrides are sent with the select request and applied to the job
BEFORE the pipeline starts, since the job was created in Step 2 before
these fields were available.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock

from backend.models.job import JobStatus


class TestAudioSelectRequestOverrides:
    """Tests for AudioSelectRequest model with optional overrides."""

    def test_model_accepts_overrides(self):
        """Test AudioSelectRequest accepts is_private, display_artist, display_title."""
        from backend.api.routes.audio_search import AudioSelectRequest

        req = AudioSelectRequest(
            selection_index=0,
            is_private=True,
            display_artist="Custom Artist",
            display_title="Custom Title",
        )
        assert req.selection_index == 0
        assert req.is_private is True
        assert req.display_artist == "Custom Artist"
        assert req.display_title == "Custom Title"

    def test_model_defaults_overrides_to_none(self):
        """Test AudioSelectRequest defaults override fields to None."""
        from backend.api.routes.audio_search import AudioSelectRequest

        req = AudioSelectRequest(selection_index=0)
        assert req.is_private is None
        assert req.display_artist is None
        assert req.display_title is None

    def test_model_accepts_is_private_false(self):
        """Test AudioSelectRequest distinguishes is_private=False from None."""
        from backend.api.routes.audio_search import AudioSelectRequest

        req = AudioSelectRequest(selection_index=0, is_private=False)
        assert req.is_private is False


class TestSelectEndpointAppliesOverrides:
    """Tests that the select_audio_source endpoint applies overrides to the job."""

    @pytest.fixture
    def mock_job_manager(self):
        """Mock the job manager singleton."""
        with patch('backend.api.routes.audio_search.job_manager') as mock:
            yield mock

    @pytest.fixture
    def mock_storage_service(self):
        """Mock the storage service singleton."""
        with patch('backend.api.routes.audio_search.storage_service') as mock:
            yield mock

    def test_overrides_applied_before_selection(self, mock_job_manager, mock_storage_service):
        """Test is_private and display overrides are applied to job before pipeline starts."""
        from backend.api.routes.audio_search import AudioSelectRequest

        # Setup mock job in AWAITING_AUDIO_SELECTION state
        mock_job = Mock()
        mock_job.status = JobStatus.AWAITING_AUDIO_SELECTION
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Test Song',
                'artist': 'Test Artist',
                'provider': 'RED',
                'quality': 'FLAC',
                'source_id': 'abc123',
                'target_file': 'test.flac',
                'url': '',
            }]
        }
        mock_job_manager.get_job.return_value = mock_job

        body = AudioSelectRequest(
            selection_index=0,
            is_private=True,
            display_artist="DJ Test",
            display_title="The Remix",
        )

        # We test the override logic directly by checking update_job calls
        # The first update_job call should be the overrides (before _validate_and_prepare_selection)
        import asyncio
        from unittest.mock import call
        from fastapi import BackgroundTasks

        # Mock BackgroundTasks and auth
        bg_tasks = Mock(spec=BackgroundTasks)
        auth_result = Mock()

        # Mock the audio search service and worker service
        mock_worker_service = AsyncMock()
        mock_worker_service.trigger_audio_download_worker.return_value = True
        with patch('backend.api.routes.audio_search.get_audio_search_service'), \
             patch('backend.api.routes.audio_search.get_worker_service', return_value=mock_worker_service):
            from backend.api.routes.audio_search import select_audio_source
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    select_audio_source(
                        job_id="test-job",
                        background_tasks=bg_tasks,
                        body=body,
                        auth_result=auth_result,
                    )
                )
            finally:
                loop.close()

        # The overrides should be applied via update_job
        update_calls = mock_job_manager.update_job.call_args_list
        # First call should be the overrides
        override_call = update_calls[0]
        assert override_call.args[0] == "test-job"
        override_data = override_call.args[1]
        assert override_data["is_private"] is True
        assert override_data["display_artist"] == "DJ Test"
        assert override_data["display_title"] == "The Remix"

    def test_no_overrides_skips_update(self, mock_job_manager, mock_storage_service):
        """Test that when no overrides are provided, update_job is NOT called for overrides."""
        from backend.api.routes.audio_search import AudioSelectRequest

        mock_job = Mock()
        mock_job.status = JobStatus.AWAITING_AUDIO_SELECTION
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Test Song',
                'artist': 'Test Artist',
                'provider': 'RED',
                'quality': 'FLAC',
                'source_id': 'abc123',
                'target_file': 'test.flac',
                'url': '',
            }]
        }
        mock_job_manager.get_job.return_value = mock_job

        body = AudioSelectRequest(selection_index=0)

        import asyncio
        from fastapi import BackgroundTasks

        bg_tasks = Mock(spec=BackgroundTasks)
        auth_result = Mock()

        mock_worker_service = AsyncMock()
        mock_worker_service.trigger_audio_download_worker.return_value = True
        with patch('backend.api.routes.audio_search.get_audio_search_service'), \
             patch('backend.api.routes.audio_search.get_worker_service', return_value=mock_worker_service):
            from backend.api.routes.audio_search import select_audio_source
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    select_audio_source(
                        job_id="test-job",
                        background_tasks=bg_tasks,
                        body=body,
                        auth_result=auth_result,
                    )
                )
            finally:
                loop.close()

        # update_job will be called by _validate_and_prepare_selection for download params
        # but NOT for overrides. The override call would have is_private/display_artist/display_title
        for call_args in mock_job_manager.update_job.call_args_list:
            update_data = call_args.args[1]
            assert "is_private" not in update_data
            assert "display_artist" not in update_data
            assert "display_title" not in update_data

    def test_partial_overrides_only_applies_provided(self, mock_job_manager, mock_storage_service):
        """Test that only provided override fields are sent to update_job."""
        from backend.api.routes.audio_search import AudioSelectRequest

        mock_job = Mock()
        mock_job.status = JobStatus.AWAITING_AUDIO_SELECTION
        mock_job.state_data = {
            'audio_search_results': [{
                'title': 'Test Song',
                'artist': 'Test Artist',
                'provider': 'RED',
                'quality': 'FLAC',
                'source_id': 'abc123',
                'target_file': 'test.flac',
                'url': '',
            }]
        }
        mock_job_manager.get_job.return_value = mock_job

        # Only is_private, no display overrides
        body = AudioSelectRequest(selection_index=0, is_private=True)

        import asyncio
        from fastapi import BackgroundTasks

        bg_tasks = Mock(spec=BackgroundTasks)
        auth_result = Mock()

        mock_worker_service = AsyncMock()
        mock_worker_service.trigger_audio_download_worker.return_value = True
        with patch('backend.api.routes.audio_search.get_audio_search_service'), \
             patch('backend.api.routes.audio_search.get_worker_service', return_value=mock_worker_service):
            from backend.api.routes.audio_search import select_audio_source
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    select_audio_source(
                        job_id="test-job",
                        background_tasks=bg_tasks,
                        body=body,
                        auth_result=auth_result,
                    )
                )
            finally:
                loop.close()

        # Find the override call (first update_job call)
        override_call = mock_job_manager.update_job.call_args_list[0]
        override_data = override_call.args[1]
        assert override_data == {"is_private": True}
        assert "display_artist" not in override_data
        assert "display_title" not in override_data
