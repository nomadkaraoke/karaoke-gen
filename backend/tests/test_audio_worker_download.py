"""
Tests for download_audio() upload persistence in audio_worker.py.

Verifies that uploaded files (uploads/ prefix) are copied to jobs/ prefix
so they survive the 7-day GCS lifecycle rule on uploads/.
"""

import os
import pytest
from unittest.mock import Mock, patch, AsyncMock


@pytest.mark.asyncio
@patch("backend.workers.audio_worker.JobManager")
async def test_upload_persisted_to_jobs_prefix(MockJobManager):
    """Uploaded audio (uploads/ prefix) should be copied to jobs/{id}/input/."""
    from backend.workers.audio_worker import download_audio

    mock_storage = Mock()
    mock_jm = Mock()

    mock_job = Mock()
    mock_job.input_media_gcs_path = "uploads/abc123/audio/My Song.flac"
    mock_job.filename = "My Song.flac"
    mock_job.url = None
    mock_job.file_urls = {}

    result = await download_audio(
        job_id="abc123",
        temp_dir="/tmp/test",
        storage=mock_storage,
        job=mock_job,
        job_manager_instance=mock_jm,
    )

    # Should download from original upload path
    mock_storage.download_file.assert_called_once_with(
        "uploads/abc123/audio/My Song.flac", os.path.join("/tmp/test", "My Song.flac")
    )

    # Should re-upload to persistent jobs/ path
    mock_storage.upload_file.assert_called_once_with(
        os.path.join("/tmp/test", "My Song.flac"),
        "jobs/abc123/input/My Song.flac",
    )

    # Should update job metadata
    mock_jm.update_job.assert_called_once_with(
        "abc123", {"input_media_gcs_path": "jobs/abc123/input/My Song.flac"}
    )
    mock_jm.update_file_url.assert_called_once_with(
        "abc123", "input", "audio", "jobs/abc123/input/My Song.flac"
    )

    assert result == os.path.join("/tmp/test", "My Song.flac")


@pytest.mark.asyncio
@patch("backend.workers.audio_worker.JobManager")
async def test_jobs_prefix_not_re_persisted(MockJobManager):
    """Files already in jobs/ prefix should NOT be re-uploaded."""
    from backend.workers.audio_worker import download_audio

    mock_storage = Mock()
    mock_jm = Mock()

    mock_job = Mock()
    mock_job.input_media_gcs_path = "jobs/abc123/input/song.flac"
    mock_job.filename = "song.flac"
    mock_job.url = None
    mock_job.file_urls = {}

    result = await download_audio(
        job_id="abc123",
        temp_dir="/tmp/test",
        storage=mock_storage,
        job=mock_job,
        job_manager_instance=mock_jm,
    )

    # Should download but NOT re-upload
    mock_storage.download_file.assert_called_once()
    mock_storage.upload_file.assert_not_called()
    mock_jm.update_job.assert_not_called()


@pytest.mark.asyncio
@patch("backend.workers.audio_worker.JobManager")
async def test_upload_persistence_creates_job_manager_if_none(MockJobManager):
    """If no job_manager_instance provided, creates one for persistence."""
    from backend.workers.audio_worker import download_audio

    mock_storage = Mock()
    mock_jm_instance = Mock()
    MockJobManager.return_value = mock_jm_instance

    mock_job = Mock()
    mock_job.input_media_gcs_path = "uploads/def456/audio/track.flac"
    mock_job.filename = "track.flac"
    mock_job.url = None
    mock_job.file_urls = {}

    await download_audio(
        job_id="def456",
        temp_dir="/tmp/test",
        storage=mock_storage,
        job=mock_job,
        job_manager_instance=None,
    )

    # Should have created a JobManager
    MockJobManager.assert_called_once()
    # Should use the created instance
    mock_jm_instance.update_job.assert_called_once()
    mock_jm_instance.update_file_url.assert_called_once()


@pytest.mark.asyncio
@patch("backend.workers.audio_worker.JobManager")
async def test_persistence_failure_does_not_block_download(MockJobManager):
    """If upload_file fails during persistence, download still succeeds."""
    from backend.workers.audio_worker import download_audio

    mock_storage = Mock()
    mock_storage.upload_file.side_effect = Exception("GCS upload failed")
    mock_jm = Mock()

    mock_job = Mock()
    mock_job.input_media_gcs_path = "uploads/abc123/audio/song.flac"
    mock_job.filename = "song.flac"
    mock_job.url = None
    mock_job.file_urls = {}

    result = await download_audio(
        job_id="abc123",
        temp_dir="/tmp/test",
        storage=mock_storage,
        job=mock_job,
        job_manager_instance=mock_jm,
    )

    # Download succeeded despite persistence failure
    assert result == os.path.join("/tmp/test", "song.flac")
    mock_storage.download_file.assert_called_once()
    # upload_file was attempted but failed
    mock_storage.upload_file.assert_called_once()
    # Metadata update should NOT have been called (upload failed first)
    mock_jm.update_job.assert_not_called()
