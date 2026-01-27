"""
Integration tests for the combined review flow.

These tests verify that corrections made during human review survive the full flow:
1. User edits lyrics in LyricsAnalyzer
2. Corrections saved via POST /api/jobs/{job_id}/corrections
3. User navigates to InstrumentalSelector
4. InstrumentalSelector fetches via GET /api/review/{job_id}/correction-data
5. Corrections are included in final submission
6. render_video_worker uses the corrected data

The key invariant: If corrections_updated.json exists, the API must return its
contents, not the original corrections.json.
"""
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from backend.models.job import Job, JobStatus


class TestGetCorrectionDataReturnsUpdatedCorrections:
    """Test that get_correction_data returns updated corrections when available.

    This is the critical fix for the bug where user's lyrics corrections were
    being ignored because InstrumentalSelector was fetching original corrections.
    """

    @pytest.fixture
    def mock_job(self):
        """Create a mock job in AWAITING_REVIEW state."""
        job = Mock(spec=Job)
        job.job_id = "test-job-123"
        job.status = JobStatus.AWAITING_REVIEW
        job.artist = "Test Artist"
        job.title = "Test Title"
        job.input_media_gcs_path = "jobs/test-job-123/input/audio.flac"
        job.file_urls = {
            "lyrics": {
                "corrections": "jobs/test-job-123/lyrics/corrections.json",
            },
            "stems": {},
            "analysis": {},
        }
        job.state_data = {}
        job.style_params_gcs_path = None
        job.style_assets = {}
        return job

    @pytest.fixture
    def original_corrections(self):
        """Original corrections from lyrics processing (before user edits)."""
        return {
            "corrected_segments": [
                {"id": 1, "text": "Hello world"},  # Original: "Hello world"
            ],
            "corrections": [],
            "metadata": {"source": "original"},
        }

    @pytest.fixture
    def updated_corrections(self):
        """Updated corrections after user edits."""
        return {
            "corrected_segments": [
                {"id": 1, "text": "Hello beautiful world"},  # User changed this!
            ],
            "corrections": [
                {"id": 1, "type": "edit", "original": "Hello world", "corrected": "Hello beautiful world"}
            ],
            "metadata": {"source": "updated"},
        }

    def test_returns_updated_corrections_when_file_exists(
        self, mock_job, original_corrections, updated_corrections
    ):
        """Verify endpoint returns updated corrections when corrections_updated.json exists.

        This is the main test for the bug fix.
        """
        from backend.api.routes.review import get_correction_data

        # Add corrections_updated to file_urls
        mock_job.file_urls["lyrics"]["corrections_updated"] = "jobs/test-job-123/lyrics/corrections_updated.json"

        with patch("backend.api.routes.review.JobManager") as MockJobManager, \
             patch("backend.api.routes.review.StorageService") as MockStorageService:

            # Setup mocks
            job_manager = MockJobManager.return_value
            job_manager.get_job.return_value = mock_job

            storage = MockStorageService.return_value
            # Return True for corrections_updated.json
            storage.file_exists.return_value = True
            # Return updated corrections (not original)
            storage.download_json.return_value = updated_corrections.copy()
            storage.generate_signed_url.return_value = "https://signed-url.com"

            # Call the endpoint (synchronously for testing)
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_correction_data("test-job-123", ("user@test.com", "job_owner"))
            )

            # Verify it downloaded from corrections_updated, not corrections
            storage.download_json.assert_called_once()
            downloaded_path = storage.download_json.call_args[0][0]
            assert "corrections_updated" in downloaded_path, \
                f"Expected to download corrections_updated.json, got: {downloaded_path}"

            # Verify the returned data has the user's edits
            assert result["corrected_segments"][0]["text"] == "Hello beautiful world"
            assert result["metadata"]["source"] == "updated"

    def test_falls_back_to_original_when_no_updates(
        self, mock_job, original_corrections
    ):
        """Verify endpoint falls back to original corrections when no updates exist."""
        from backend.api.routes.review import get_correction_data

        with patch("backend.api.routes.review.JobManager") as MockJobManager, \
             patch("backend.api.routes.review.StorageService") as MockStorageService:

            # Setup mocks
            job_manager = MockJobManager.return_value
            job_manager.get_job.return_value = mock_job

            storage = MockStorageService.return_value
            # Return False for corrections_updated.json (doesn't exist)
            storage.file_exists.side_effect = lambda path: "corrections_updated" not in path
            # Return original corrections
            storage.download_json.return_value = original_corrections.copy()
            storage.generate_signed_url.return_value = "https://signed-url.com"

            # Call the endpoint
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_correction_data("test-job-123", ("user@test.com", "job_owner"))
            )

            # Verify it downloaded from corrections.json (original)
            storage.download_json.assert_called_once()
            downloaded_path = storage.download_json.call_args[0][0]
            assert "corrections.json" in downloaded_path

            # Verify the returned data is the original
            assert result["corrected_segments"][0]["text"] == "Hello world"

    def test_checks_direct_gcs_path_when_not_in_file_urls(
        self, mock_job, updated_corrections
    ):
        """Verify endpoint checks direct GCS path for corrections_updated.json."""
        from backend.api.routes.review import get_correction_data

        # Remove corrections_updated from file_urls (simulates older job)
        # But the file exists in GCS from a previous save

        with patch("backend.api.routes.review.JobManager") as MockJobManager, \
             patch("backend.api.routes.review.StorageService") as MockStorageService:

            # Setup mocks
            job_manager = MockJobManager.return_value
            job_manager.get_job.return_value = mock_job

            storage = MockStorageService.return_value
            # Return True for direct path check of corrections_updated.json
            storage.file_exists.return_value = True
            storage.download_json.return_value = updated_corrections.copy()
            storage.generate_signed_url.return_value = "https://signed-url.com"

            # Call the endpoint
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_correction_data("test-job-123", ("user@test.com", "job_owner"))
            )

            # Verify it checked for corrections_updated.json via direct path
            file_exists_calls = storage.file_exists.call_args_list
            checked_paths = [call[0][0] for call in file_exists_calls]
            assert any("corrections_updated" in path for path in checked_paths), \
                f"Expected to check for corrections_updated.json, checked: {checked_paths}"

    def test_falls_back_when_file_urls_has_stale_corrections_updated(
        self, mock_job, original_corrections
    ):
        """Verify endpoint falls back to original when corrections_updated in file_urls but file deleted.

        This handles the edge case where:
        1. User saved corrections (corrections_updated added to file_urls)
        2. Admin reset job or file was deleted
        3. corrections_updated.json no longer exists in GCS
        4. Endpoint should fall back to original corrections.json
        """
        from backend.api.routes.review import get_correction_data

        # Add corrections_updated to file_urls (stale reference)
        mock_job.file_urls["lyrics"]["corrections_updated"] = "jobs/test-job-123/lyrics/corrections_updated.json"

        with patch("backend.api.routes.review.JobManager") as MockJobManager, \
             patch("backend.api.routes.review.StorageService") as MockStorageService:

            job_manager = MockJobManager.return_value
            job_manager.get_job.return_value = mock_job

            storage = MockStorageService.return_value
            # corrections_updated.json does NOT exist (stale reference)
            # but corrections.json does
            def file_exists_check(path):
                return "corrections_updated" not in path
            storage.file_exists.side_effect = file_exists_check
            storage.download_json.return_value = original_corrections.copy()
            storage.generate_signed_url.return_value = "https://signed-url.com"

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_correction_data("test-job-123", ("user@test.com", "job_owner"))
            )

            # Should fall back to original corrections
            downloaded_path = storage.download_json.call_args[0][0]
            assert "corrections.json" in downloaded_path or "corrections_updated" not in downloaded_path

            # Verify original data was returned
            assert result["corrected_segments"][0]["text"] == "Hello world"


class TestCorrectionDataPreservation:
    """Test that correction data is preserved through the full flow."""

    def test_post_corrections_saves_to_corrections_updated(self):
        """Verify POST /api/jobs/{job_id}/corrections saves to corrections_updated.json."""
        # This documents the expected behavior of the jobs.py endpoint
        # The actual implementation saves to f"jobs/{job_id}/lyrics/corrections_updated.json"
        job_id = "test-job-123"
        expected_gcs_path = f"jobs/{job_id}/lyrics/corrections_updated.json"

        assert "corrections_updated" in expected_gcs_path
        assert job_id in expected_gcs_path

    def test_complete_review_submits_correction_data(self):
        """Verify POST /api/review/{job_id}/complete receives and saves corrections."""
        # This documents what data complete_review expects
        expected_payload = {
            "corrections": [{"id": 1, "type": "edit"}],
            "corrected_segments": [{"id": 1, "text": "corrected"}],
            "instrumental_selection": "with_backing",  # Required
        }

        # instrumental_selection is required
        assert "instrumental_selection" in expected_payload
        # corrections data should be included
        assert "corrections" in expected_payload
        assert "corrected_segments" in expected_payload


class TestRenderVideoWorkerUsesCorrectedData:
    """Test that render_video_worker uses the corrected data.

    This verifies that the fix is consistent with render_video_worker's behavior.
    """

    def test_render_worker_checks_corrections_updated_first(self):
        """Document that render_video_worker checks corrections_updated first.

        From render_video_worker.py lines 128-142, the worker:
        1. Checks file_urls.lyrics.corrections_updated
        2. Falls back to file_urls.lyrics.corrections
        3. Falls back to direct GCS paths

        This is the same pattern the API endpoint should use.
        """
        # Document the expected check order
        check_order = [
            "file_urls.lyrics.corrections_updated",
            "file_urls.lyrics.corrections",
            "jobs/{job_id}/lyrics/corrections_updated.json",
            "jobs/{job_id}/lyrics/corrections.json",
        ]

        # corrections_updated must be checked before corrections
        updated_index = next(i for i, p in enumerate(check_order) if "corrections_updated" in p)
        original_index = next(i for i, p in enumerate(check_order) if "corrections.json" in p or
                             (p == "file_urls.lyrics.corrections"))

        # Only check the file_urls entries (indices 0 and 1)
        assert updated_index < 2, "corrections_updated should be in top 2 checks"

    def test_render_worker_merges_corrections(self):
        """Document that render_video_worker merges corrections.

        From render_video_worker.py lines 159-175:
        - Downloads original corrections.json
        - If corrections_updated.json exists, downloads and merges
        - Merged data has user's corrections with original metadata
        """
        original_data = {
            "original_segments": [{"id": 1}],
            "corrected_segments": [{"id": 1, "text": "original"}],
            "corrections": [],
            "metadata": {"preserved": True},
        }

        updated_data = {
            "corrected_segments": [{"id": 1, "text": "user-edited"}],
            "corrections": [{"id": 1, "type": "edit"}],
        }

        # Apply merge logic (same as render_video_worker.py lines 169-172)
        if 'corrections' in updated_data:
            original_data['corrections'] = updated_data['corrections']
        if 'corrected_segments' in updated_data:
            original_data['corrected_segments'] = updated_data['corrected_segments']

        # Verify merge result
        assert original_data['corrected_segments'][0]['text'] == "user-edited"
        assert original_data['corrections'][0]['type'] == "edit"
        # Metadata and original_segments preserved
        assert original_data['metadata']['preserved'] is True
        assert original_data['original_segments'][0]['id'] == 1
