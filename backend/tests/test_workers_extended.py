"""
Extended worker tests focusing on helper functions and utilities.

These tests increase coverage without requiring complex external mocking.
"""
import pytest
import os
import json
import tempfile
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch

from backend.models.job import Job, JobStatus


class TestAudioWorkerHelpers:
    """Tests for audio_worker.py helper functions and utilities."""
    
    def test_audio_worker_module_structure(self):
        """Test audio worker module has expected structure."""
        from backend.workers import audio_worker
        assert hasattr(audio_worker, 'process_audio_separation')
        assert hasattr(audio_worker, 'download_audio')
        assert hasattr(audio_worker, 'upload_separation_results')
    
    def test_audio_worker_logger_configured(self):
        """Test audio worker has logger configured."""
        from backend.workers.audio_worker import logger
        assert logger is not None
    
    @pytest.mark.asyncio
    async def test_upload_separation_results_with_empty_result(self):
        """Test upload with empty separation result."""
        from backend.workers.audio_worker import upload_separation_results
        
        mock_storage = MagicMock()
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = Job(
            job_id="test",
            status=JobStatus.SEPARATING_STAGE2,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)
        )
        
        # Empty result should not crash
        await upload_separation_results(
            "test123",
            {"clean_instrumental": {}, "other_stems": {}, "backing_vocals": {}, "combined_instrumentals": {}},
            mock_storage,
            mock_job_manager
        )


class TestLyricsWorkerHelpers:
    """Tests for lyrics_worker.py helper functions."""
    
    def test_lyrics_worker_module_structure(self):
        """Test lyrics worker module has expected structure."""
        from backend.workers import lyrics_worker
        assert hasattr(lyrics_worker, 'process_lyrics_transcription')
        assert hasattr(lyrics_worker, 'upload_lyrics_results')
    
    def test_lyrics_worker_logger_configured(self):
        """Test lyrics worker has logger configured."""
        from backend.workers.lyrics_worker import logger
        assert logger is not None


class TestScreensWorkerHelpers:
    """Tests for screens_worker.py helper functions."""
    
    def test_screens_worker_module_structure(self):
        """Test screens worker module has expected structure."""
        from backend.workers import screens_worker
        assert hasattr(screens_worker, 'generate_screens')
    
    def test_screens_worker_logger_configured(self):
        """Test screens worker has logger configured."""
        from backend.workers.screens_worker import logger
        assert logger is not None


class TestVideoWorkerHelpers:
    """Tests for video_worker.py helper functions."""
    
    def test_video_worker_module_structure(self):
        """Test video worker module has expected structure."""
        from backend.workers import video_worker
        assert hasattr(video_worker, 'generate_video')
    
    def test_video_worker_logger_configured(self):
        """Test video worker has logger configured."""
        from backend.workers.video_worker import logger
        assert logger is not None


class TestWorkerJobValidation:
    """Tests for job validation in workers."""
    
    @pytest.fixture
    def mock_storage(self):
        return MagicMock()
    
    @pytest.fixture
    def mock_job_manager(self):
        manager = MagicMock()
        manager.mark_job_failed = MagicMock()
        return manager
    
    @pytest.mark.asyncio
    async def test_audio_worker_handles_missing_job(self, mock_storage, mock_job_manager):
        """Test audio worker handles missing job gracefully."""
        mock_job_manager.get_job.return_value = None
        
        with patch('backend.workers.audio_worker.JobManager', return_value=mock_job_manager), \
             patch('backend.workers.audio_worker.StorageService', return_value=mock_storage):
            from backend.workers.audio_worker import process_audio_separation
            
            await process_audio_separation("nonexistent")
            # Should handle gracefully, potentially marking as failed
    
    @pytest.mark.asyncio
    async def test_lyrics_worker_handles_missing_job(self, mock_storage, mock_job_manager):
        """Test lyrics worker handles missing job gracefully."""
        mock_job_manager.get_job.return_value = None
        
        with patch('backend.workers.lyrics_worker.JobManager', return_value=mock_job_manager), \
             patch('backend.workers.lyrics_worker.StorageService', return_value=mock_storage):
            from backend.workers.lyrics_worker import process_lyrics_transcription
            
            await process_lyrics_transcription("nonexistent")


class TestWorkerFileOperations:
    """Tests for worker file operations."""
    
    @pytest.mark.asyncio
    async def test_download_audio_from_gcs_path(self):
        """Test downloading audio from GCS path."""
        from backend.workers.audio_worker import download_audio
        
        mock_storage = MagicMock()
        mock_storage.download_file.return_value = "/tmp/test.flac"
        
        mock_job = Job(
            job_id="test123",
            status=JobStatus.DOWNLOADING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="uploads/test123/song.flac"
        )
        
        with tempfile.TemporaryDirectory() as temp_dir:
            result = await download_audio("test123", temp_dir, mock_storage, mock_job)
            mock_storage.download_file.assert_called()
    
    @pytest.mark.asyncio
    async def test_lyrics_download_audio(self):
        """Test lyrics worker downloads audio."""
        from backend.workers.lyrics_worker import download_audio
        
        mock_storage = MagicMock()
        mock_storage.download_file.return_value = "/tmp/test.flac"
        
        mock_job_manager = MagicMock()
        mock_job = Job(
            job_id="test123",
            status=JobStatus.TRANSCRIBING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input_media_gcs_path="uploads/test123/song.flac"
        )
        mock_job_manager.get_job.return_value = mock_job
        
        with tempfile.TemporaryDirectory() as temp_dir:
            result = await download_audio("test123", temp_dir, mock_storage, mock_job, mock_job_manager)
            mock_storage.download_file.assert_called()

    @pytest.mark.asyncio
    async def test_lyrics_download_audio_no_source_raises(self):
        """Lyrics worker download_audio must fail loudly when there's no source."""
        from backend.workers.lyrics_worker import download_audio
        from backend.services.audio_search_service import DownloadError

        mock_storage = MagicMock()
        mock_job_manager = MagicMock()
        mock_job = Job(
            job_id="test123",
            status=JobStatus.TRANSCRIBING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            # No input_media_gcs_path and no url → nothing to download
        )
        mock_job_manager.get_job.return_value = mock_job

        with tempfile.TemporaryDirectory() as temp_dir:
            with pytest.raises(DownloadError, match="No input audio available"):
                await download_audio("test123", temp_dir, mock_storage, mock_job, mock_job_manager)
            mock_storage.download_file.assert_not_called()

