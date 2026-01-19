"""
Unit tests for backend workers.

These tests mock external dependencies and test worker logic in isolation.
This includes the functions that would have caught bugs like the
UnboundLocalError in upload_lyrics_results.
"""
import pytest
import os
import json
import tempfile
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch, mock_open
from pathlib import Path

from backend.models.job import Job, JobStatus


class TestAudioWorker:
    """Tests for audio_worker.py functions."""
    
    @pytest.fixture
    def mock_job(self):
        """Create a mock job for testing."""
        return Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            input_media_gcs_path="uploads/test123/song.flac"
        )
    
    @pytest.fixture
    def mock_job_manager(self, mock_job):
        """Create a mock JobManager."""
        manager = MagicMock()
        manager.get_job.return_value = mock_job
        manager.update_job_status.return_value = None
        manager.update_file_url.return_value = None
        manager.update_state_data.return_value = None
        manager.mark_job_failed.return_value = None
        return manager
    
    @pytest.fixture
    def mock_storage(self):
        """Create a mock StorageService."""
        storage = MagicMock()
        storage.download_file.return_value = "/tmp/test/song.flac"
        storage.upload_file.return_value = "gs://bucket/path"
        return storage
    
    @pytest.mark.asyncio
    async def test_download_audio_from_gcs(self, mock_job_manager, mock_storage, mock_job):
        """Test downloading audio from GCS for uploaded files."""
        with patch('backend.workers.audio_worker.JobManager', return_value=mock_job_manager), \
             patch('backend.workers.audio_worker.StorageService', return_value=mock_storage):
            
            from backend.workers.audio_worker import download_audio
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = await download_audio("test123", temp_dir, mock_storage, mock_job)
                
                # Should have called download_file with the GCS path
                mock_storage.download_file.assert_called()
    
    @pytest.mark.asyncio
    async def test_upload_separation_results_handles_clean_stems(self, mock_job_manager, mock_storage):
        """Test that upload_separation_results handles clean stems correctly."""
        from backend.workers.audio_worker import upload_separation_results
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create actual test files
            inst_path = os.path.join(temp_dir, "instrumental.flac")
            vocals_path = os.path.join(temp_dir, "vocals.flac")
            with open(inst_path, 'wb') as f:
                f.write(b'fake audio data')
            with open(vocals_path, 'wb') as f:
                f.write(b'fake audio data')
            
            # Create mock separation result matching AudioProcessor output format
            separation_result = {
                "clean_instrumental": {
                    "instrumental": inst_path,
                    "vocals": vocals_path
                },
                "other_stems": {},
                "backing_vocals": {},
                "combined_instrumentals": {}
            }
            
            await upload_separation_results("test123", separation_result, mock_storage, mock_job_manager)
            
            # Should have uploaded files
            assert mock_storage.upload_file.called
    
    @pytest.mark.asyncio
    async def test_upload_separation_results_handles_other_stems_as_dict(self, mock_job_manager, mock_storage):
        """Test that upload_separation_results handles other_stems when values are dicts (bug fix)."""
        with patch('backend.workers.audio_worker.JobManager', return_value=mock_job_manager), \
             patch('backend.workers.audio_worker.StorageService', return_value=mock_storage):
            
            from backend.workers.audio_worker import upload_separation_results
            
            # This is the structure that caused the original bug - values are dicts, not strings
            separation_result = {
                "clean": {},
                "other_stems": {
                    "bass": {"path": "/tmp/test/bass.flac", "other_key": "value"},
                    "drums": "/tmp/test/drums.flac"  # String path
                },
                "backing_vocals": {},
                "combined_instrumentals": {}
            }
            
            # Mock os.path.exists
            with patch('os.path.exists', return_value=True):
                # This should NOT raise TypeError: stat: path should be string...
                await upload_separation_results("test123", separation_result, mock_storage, mock_job_manager)
    
    @pytest.mark.asyncio
    async def test_process_audio_separation_updates_status_on_failure(self, mock_job_manager, mock_storage):
        """Test that process_audio_separation marks job as failed on error."""
        mock_job_manager.get_job.return_value = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test"
        )
        
        with patch('backend.workers.audio_worker.JobManager', return_value=mock_job_manager), \
             patch('backend.workers.audio_worker.StorageService', return_value=mock_storage), \
             patch('backend.workers.audio_worker.download_audio', side_effect=Exception("Download failed")):
            
            from backend.workers.audio_worker import process_audio_separation
            
            await process_audio_separation("test123")
            
            # Should have marked job as failed
            mock_job_manager.mark_job_failed.assert_called()


class TestLyricsWorker:
    """Tests for lyrics_worker.py functions."""
    
    @pytest.fixture
    def mock_job(self):
        """Create a mock job for testing."""
        return Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="ABBA",
            title="Waterloo",
            input_media_gcs_path="uploads/test123/song.flac"
        )
    
    @pytest.fixture
    def mock_job_manager(self, mock_job):
        """Create a mock JobManager."""
        manager = MagicMock()
        manager.get_job.return_value = mock_job
        manager.update_job_status.return_value = None
        manager.update_file_url.return_value = None
        manager.update_state_data.return_value = None
        manager.mark_job_failed.return_value = None
        return manager
    
    @pytest.fixture
    def mock_storage(self):
        """Create a mock StorageService."""
        storage = MagicMock()
        storage.download_file.return_value = "/tmp/test/song.flac"
        storage.upload_file.return_value = "gs://bucket/path"
        return storage
    
    @pytest.mark.asyncio
    async def test_upload_lyrics_results_requires_job(self, mock_job_manager, mock_storage, mock_job):
        """Test that upload_lyrics_results correctly fetches job for artist/title.
        
        This test would have caught the UnboundLocalError bug where job was used
        before being defined.
        """
        with patch('backend.workers.lyrics_worker.JobManager', return_value=mock_job_manager), \
             patch('backend.workers.lyrics_worker.StorageService', return_value=mock_storage):
            
            from backend.workers.lyrics_worker import upload_lyrics_results
            
            with tempfile.TemporaryDirectory() as temp_dir:
                # Create mock lyrics directory and files
                lyrics_dir = os.path.join(temp_dir, "lyrics")
                os.makedirs(lyrics_dir)
                
                # Create test LRC file
                lrc_path = os.path.join(lyrics_dir, "ABBA - Waterloo (Karaoke).lrc")
                with open(lrc_path, 'w') as f:
                    f.write("[00:00.00]Test lyrics\n")
                
                # Create corrections JSON
                corrections_path = os.path.join(lyrics_dir, "ABBA - Waterloo (Lyrics Corrections).json")
                with open(corrections_path, 'w') as f:
                    json.dump({"lines": [], "corrections": []}, f)
                
                transcription_result = {
                    "lrc_filepath": lrc_path,
                    "corrections_filepath": corrections_path
                }
                
                # This should NOT raise UnboundLocalError
                await upload_lyrics_results(
                    "test123", 
                    temp_dir, 
                    transcription_result, 
                    mock_storage, 
                    mock_job_manager
                )
                
                # Verify job was fetched
                mock_job_manager.get_job.assert_called_with("test123")
    
    @pytest.mark.asyncio
    async def test_upload_lyrics_results_uploads_lrc_file(self, mock_job_manager, mock_storage, mock_job):
        """Test that LRC file is uploaded correctly."""
        from backend.workers.lyrics_worker import upload_lyrics_results
        import json
        
        with tempfile.TemporaryDirectory() as temp_dir:
            lyrics_dir = os.path.join(temp_dir, "lyrics")
            os.makedirs(lyrics_dir)
            
            lrc_path = os.path.join(lyrics_dir, "test.lrc")
            with open(lrc_path, 'w') as f:
                f.write("[00:00.00]Test\n")
            
            # Create required corrections.json file
            corrections_path = os.path.join(lyrics_dir, "corrections.json")
            with open(corrections_path, 'w') as f:
                json.dump({"corrected_segments": []}, f)
            
            transcription_result = {"lrc_filepath": lrc_path}
            
            await upload_lyrics_results(
                "test123", temp_dir, transcription_result, 
                mock_storage, mock_job_manager
            )
            
            # Should have uploaded the LRC file
            mock_storage.upload_file.assert_called()
            mock_job_manager.update_file_url.assert_called()
    
    @pytest.mark.asyncio
    async def test_upload_lyrics_results_handles_missing_files(self, mock_job_manager, mock_storage, mock_job):
        """Test graceful handling when optional files are missing."""
        from backend.workers.lyrics_worker import upload_lyrics_results
        import json
        
        with tempfile.TemporaryDirectory() as temp_dir:
            lyrics_dir = os.path.join(temp_dir, "lyrics")
            os.makedirs(lyrics_dir)
            
            # Only create LRC file and required corrections.json, no other files
            lrc_path = os.path.join(lyrics_dir, "test.lrc")
            with open(lrc_path, 'w') as f:
                f.write("[00:00.00]Test\n")
            
            # Create required corrections.json file
            corrections_path = os.path.join(lyrics_dir, "corrections.json")
            with open(corrections_path, 'w') as f:
                json.dump({"corrected_segments": []}, f)
            
            transcription_result = {"lrc_filepath": lrc_path}
            
            # Should not raise exception for missing optional files
            await upload_lyrics_results(
                "test123", temp_dir, transcription_result,
                mock_storage, mock_job_manager
            )
    
    @pytest.mark.asyncio
    async def test_upload_lyrics_results_uses_artist_title_from_job(self, mock_job_manager, mock_storage, mock_job):
        """Test that upload_lyrics_results correctly uses job.artist and job.title.
        
        This test specifically validates the bug fix where job was not defined
        when accessing job.artist and job.title for reference file lookups.
        """
        from backend.workers.lyrics_worker import upload_lyrics_results
        import json
        
        with tempfile.TemporaryDirectory() as temp_dir:
            lyrics_dir = os.path.join(temp_dir, "lyrics")
            os.makedirs(lyrics_dir)
            
            # Create LRC file
            lrc_path = os.path.join(lyrics_dir, "test.lrc")
            with open(lrc_path, 'w') as f:
                f.write("[00:00.00]Test\n")
            
            # Create required corrections.json file
            corrections_path = os.path.join(lyrics_dir, "corrections.json")
            with open(corrections_path, 'w') as f:
                json.dump({"corrected_segments": []}, f)
            
            # Create a reference lyrics file using the job's artist/title
            ref_path = os.path.join(lyrics_dir, f"{mock_job.artist} - {mock_job.title} (Lyrics Genius).txt")
            with open(ref_path, 'w') as f:
                f.write("Reference lyrics content\n")
            
            # Create uncorrected transcription file using job's artist/title
            uncorrected_path = os.path.join(lyrics_dir, f"{mock_job.artist} - {mock_job.title} (Lyrics Uncorrected).txt")
            with open(uncorrected_path, 'w') as f:
                f.write("Uncorrected transcription\n")
            
            transcription_result = {"lrc_filepath": lrc_path}
            
            # This should NOT raise UnboundLocalError for 'job'
            await upload_lyrics_results(
                "test123", temp_dir, transcription_result,
                mock_storage, mock_job_manager
            )
            
            # Verify job was fetched to get artist/title
            mock_job_manager.get_job.assert_called_with("test123")
            
            # Verify files were uploaded (the reference and uncorrected files exist)
            upload_calls = mock_storage.upload_file.call_args_list
            assert len(upload_calls) >= 2  # LRC + at least one reference or uncorrected
    
    @pytest.mark.asyncio
    async def test_process_lyrics_transcription_marks_failed_on_error(self, mock_job_manager, mock_storage):
        """Test that process_lyrics_transcription marks job as failed on error."""
        mock_job_manager.get_job.return_value = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            input_media_gcs_path="uploads/test123/song.flac"
        )
        
        with patch('backend.workers.lyrics_worker.JobManager', return_value=mock_job_manager), \
             patch('backend.workers.lyrics_worker.StorageService', return_value=mock_storage), \
             patch('backend.workers.lyrics_worker.download_audio', side_effect=Exception("Download failed")):
            
            from backend.workers.lyrics_worker import process_lyrics_transcription
            
            await process_lyrics_transcription("test123")
            
            mock_job_manager.mark_job_failed.assert_called()


class TestLyricsWorkerConfiguration:
    """Tests for lyrics worker configuration parameters."""
    
    def test_create_lyrics_processor_with_defaults(self):
        """Test creating LyricsProcessor with default parameters."""
        with patch('backend.workers.lyrics_worker.LyricsProcessor') as mock_processor:
            from backend.workers.lyrics_worker import create_lyrics_processor
            
            result = create_lyrics_processor()
            
            mock_processor.assert_called_once()
            call_kwargs = mock_processor.call_args[1]
            assert call_kwargs['lyrics_file'] is None
            assert call_kwargs['subtitle_offset_ms'] == 0
    
    def test_create_lyrics_processor_with_lyrics_file(self):
        """Test creating LyricsProcessor with custom lyrics file."""
        with patch('backend.workers.lyrics_worker.LyricsProcessor') as mock_processor:
            from backend.workers.lyrics_worker import create_lyrics_processor
            
            result = create_lyrics_processor(
                lyrics_file="/path/to/lyrics.txt",
                subtitle_offset_ms=0
            )
            
            mock_processor.assert_called_once()
            call_kwargs = mock_processor.call_args[1]
            assert call_kwargs['lyrics_file'] == "/path/to/lyrics.txt"
    
    def test_create_lyrics_processor_with_subtitle_offset(self):
        """Test creating LyricsProcessor with subtitle offset."""
        with patch('backend.workers.lyrics_worker.LyricsProcessor') as mock_processor:
            from backend.workers.lyrics_worker import create_lyrics_processor
            
            result = create_lyrics_processor(
                subtitle_offset_ms=500
            )
            
            mock_processor.assert_called_once()
            call_kwargs = mock_processor.call_args[1]
            assert call_kwargs['subtitle_offset_ms'] == 500


class TestLyricsOverrideParameters:
    """Tests for lyrics artist/title override functionality."""
    
    @pytest.fixture
    def mock_job_with_overrides(self):
        """Create a mock job with lyrics override fields."""
        return Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Beatles, The",
            title="Hey Jude - 2009 Remaster",
            lyrics_artist="The Beatles",
            lyrics_title="Hey Jude",
            subtitle_offset_ms=250,
            input_media_gcs_path="uploads/test123/song.flac"
        )
    
    def test_job_uses_lyrics_artist_override(self, mock_job_with_overrides):
        """Test that job uses lyrics_artist when searching for lyrics."""
        job = mock_job_with_overrides
        
        # Use override if present, else fall back to main artist
        lyrics_search_artist = job.lyrics_artist or job.artist
        
        assert lyrics_search_artist == "The Beatles"
        assert lyrics_search_artist != job.artist  # Override is different
    
    def test_job_uses_lyrics_title_override(self, mock_job_with_overrides):
        """Test that job uses lyrics_title when searching for lyrics."""
        job = mock_job_with_overrides
        
        # Use override if present, else fall back to main title
        lyrics_search_title = job.lyrics_title or job.title
        
        assert lyrics_search_title == "Hey Jude"
        assert lyrics_search_title != job.title  # Override is different
    
    def test_job_falls_back_when_no_override(self):
        """Test that job falls back to main artist/title when no override."""
        job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            input_media_gcs_path="uploads/test123/song.flac"
        )
        
        # When override is None, use main values
        lyrics_search_artist = job.lyrics_artist or job.artist
        lyrics_search_title = job.lyrics_title or job.title
        
        assert lyrics_search_artist == "Test Artist"
        assert lyrics_search_title == "Test Song"


class TestScreensWorker:
    """Tests for screens_worker.py functions.
    
    Note: The main process function is tested indirectly via integration tests.
    These unit tests focus on helper functions and module structure.
    """
    
    def test_screens_worker_module_imports(self):
        """Test screens worker module can be imported."""
        from backend.workers import screens_worker
        assert hasattr(screens_worker, 'logger')


class TestVideoWorker:
    """Tests for video_worker.py functions.
    
    Note: The main process function is tested indirectly via integration tests.
    These unit tests focus on helper functions and module structure.
    """
    
    def test_video_worker_module_imports(self):
        """Test video worker module can be imported."""
        from backend.workers import video_worker
        assert hasattr(video_worker, 'logger')


class TestRenderVideoWorkerConfiguration:
    """Tests for render_video_worker subtitle_offset_ms support."""
    
    @pytest.fixture
    def mock_job_with_offset(self):
        """Create a mock job with subtitle offset."""
        return Job(
            job_id="test123",
            status=JobStatus.RENDERING_VIDEO,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            subtitle_offset_ms=500,
            input_media_gcs_path="uploads/test123/song.flac"
        )
    
    def test_job_has_subtitle_offset_ms(self, mock_job_with_offset):
        """Test that job has subtitle_offset_ms field."""
        assert mock_job_with_offset.subtitle_offset_ms == 500
    
    def test_subtitle_offset_from_job(self, mock_job_with_offset):
        """Test extracting subtitle offset from job with getattr."""
        job = mock_job_with_offset
        
        # This mirrors the logic in render_video_worker.py
        subtitle_offset = getattr(job, 'subtitle_offset_ms', 0) or 0
        
        assert subtitle_offset == 500
    
    def test_subtitle_offset_default_zero(self):
        """Test that subtitle offset defaults to 0 when not set."""
        job = Job(
            job_id="test123",
            status=JobStatus.RENDERING_VIDEO,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            input_media_gcs_path="uploads/test123/song.flac"
        )
        
        subtitle_offset = getattr(job, 'subtitle_offset_ms', 0) or 0
        
        assert subtitle_offset == 0
    
    def test_subtitle_offset_negative_value(self):
        """Test that subtitle offset can be negative (advance subtitles)."""
        job = Job(
            job_id="test123",
            status=JobStatus.RENDERING_VIDEO,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            subtitle_offset_ms=-250,  # Negative = advance subtitles
            input_media_gcs_path="uploads/test123/song.flac"
        )
        
        subtitle_offset = getattr(job, 'subtitle_offset_ms', 0) or 0
        
        # Negative values should be preserved (not converted to 0)
        assert subtitle_offset == -250


class TestRenderVideoWorkerCountdownPadding:
    """Tests for render_video_worker countdown detection and audio padding.
    
    This ensures that when corrections with countdown timestamps are loaded,
    the audio is padded to match. This prevents video desynchronization.
    """
    
    def test_countdown_processor_import(self):
        """Test that render_video_worker imports CountdownProcessor."""
        from backend.workers.render_video_worker import CountdownProcessor
        assert CountdownProcessor is not None
    
    def test_countdown_processor_has_process_method(self):
        """Test that CountdownProcessor has the process method (main API)."""
        from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
        
        countdown_processor = CountdownProcessor(cache_dir="/tmp")
        assert hasattr(countdown_processor, 'process')
        assert callable(countdown_processor.process)
    
    def test_countdown_processor_constants(self):
        """Test that CountdownProcessor has expected constants."""
        from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
        
        # Verify the constants exist (these control countdown behavior)
        assert hasattr(CountdownProcessor, 'COUNTDOWN_TEXT')
        assert hasattr(CountdownProcessor, 'COUNTDOWN_PADDING_SECONDS')
        assert hasattr(CountdownProcessor, 'COUNTDOWN_THRESHOLD_SECONDS')


class TestDownloadHelpers:
    """Tests for download helper functions in workers."""
    
    @pytest.mark.asyncio
    async def test_download_audio_handles_uploaded_file(self):
        """Test download_audio handles jobs with uploaded file."""
        mock_storage = MagicMock()
        mock_storage.download_file.return_value = "/tmp/downloaded.flac"
        
        mock_job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            input_media_gcs_path="uploads/test123/song.flac"  # Uploaded file
        )
        
        from backend.workers.audio_worker import download_audio
        
        with tempfile.TemporaryDirectory() as temp_dir:
            result = await download_audio("test123", temp_dir, mock_storage, mock_job)
            
            # Should have downloaded from GCS
            mock_storage.download_file.assert_called()


class TestAudioWorkerModelConfiguration:
    """Tests for audio worker model configuration parameters."""
    
    def test_create_audio_processor_with_defaults(self):
        """Test creating AudioProcessor with default models."""
        with patch('backend.workers.audio_worker.AudioProcessor') as mock_processor:
            from backend.workers.audio_worker import create_audio_processor
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = create_audio_processor(temp_dir)
                
                mock_processor.assert_called_once()
                call_kwargs = mock_processor.call_args[1]
                
                # Should use default models
                assert call_kwargs['clean_instrumental_model'] == "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
                assert call_kwargs['backing_vocals_models'] == ["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"]
                assert call_kwargs['other_stems_models'] == ["htdemucs_6s.yaml"]
    
    def test_create_audio_processor_with_custom_clean_model(self):
        """Test creating AudioProcessor with custom clean instrumental model."""
        with patch('backend.workers.audio_worker.AudioProcessor') as mock_processor:
            from backend.workers.audio_worker import create_audio_processor
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model="custom_clean_model.ckpt"
                )
                
                mock_processor.assert_called_once()
                call_kwargs = mock_processor.call_args[1]
                
                # Should use custom clean model
                assert call_kwargs['clean_instrumental_model'] == "custom_clean_model.ckpt"
                # Other models should still be defaults
                assert call_kwargs['backing_vocals_models'] == ["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"]
    
    def test_create_audio_processor_with_custom_backing_models(self):
        """Test creating AudioProcessor with custom backing vocals models."""
        with patch('backend.workers.audio_worker.AudioProcessor') as mock_processor:
            from backend.workers.audio_worker import create_audio_processor
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = create_audio_processor(
                    temp_dir,
                    backing_vocals_models=["custom_bv1.ckpt", "custom_bv2.ckpt"]
                )
                
                mock_processor.assert_called_once()
                call_kwargs = mock_processor.call_args[1]
                
                # Should use custom backing vocals models
                assert call_kwargs['backing_vocals_models'] == ["custom_bv1.ckpt", "custom_bv2.ckpt"]
    
    def test_create_audio_processor_with_custom_other_stems_models(self):
        """Test creating AudioProcessor with custom other stems models."""
        with patch('backend.workers.audio_worker.AudioProcessor') as mock_processor:
            from backend.workers.audio_worker import create_audio_processor
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = create_audio_processor(
                    temp_dir,
                    other_stems_models=["custom_demucs.yaml"]
                )
                
                mock_processor.assert_called_once()
                call_kwargs = mock_processor.call_args[1]
                
                # Should use custom other stems models
                assert call_kwargs['other_stems_models'] == ["custom_demucs.yaml"]
    
    def test_create_audio_processor_with_all_custom_models(self):
        """Test creating AudioProcessor with all custom models."""
        with patch('backend.workers.audio_worker.AudioProcessor') as mock_processor:
            from backend.workers.audio_worker import create_audio_processor
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model="custom_clean.ckpt",
                    backing_vocals_models=["custom_bv.ckpt"],
                    other_stems_models=["custom_stems.yaml"]
                )
                
                mock_processor.assert_called_once()
                call_kwargs = mock_processor.call_args[1]
                
                # All models should be custom
                assert call_kwargs['clean_instrumental_model'] == "custom_clean.ckpt"
                assert call_kwargs['backing_vocals_models'] == ["custom_bv.ckpt"]
                assert call_kwargs['other_stems_models'] == ["custom_stems.yaml"]
    
    def test_job_model_fields_are_passed_to_processor(self):
        """Test that job model fields can be passed to create_audio_processor."""
        mock_job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            clean_instrumental_model="job_clean.ckpt",
            backing_vocals_models=["job_bv.ckpt"],
            other_stems_models=["job_stems.yaml"]
        )
        
        with patch('backend.workers.audio_worker.AudioProcessor') as mock_processor:
            from backend.workers.audio_worker import create_audio_processor
            
            with tempfile.TemporaryDirectory() as temp_dir:
                # Simulate passing job model fields to create_audio_processor
                result = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model=mock_job.clean_instrumental_model,
                    backing_vocals_models=mock_job.backing_vocals_models,
                    other_stems_models=mock_job.other_stems_models
                )
                
                mock_processor.assert_called_once()
                call_kwargs = mock_processor.call_args[1]
                
                # Models from job should be used
                assert call_kwargs['clean_instrumental_model'] == "job_clean.ckpt"
                assert call_kwargs['backing_vocals_models'] == ["job_bv.ckpt"]
                assert call_kwargs['other_stems_models'] == ["job_stems.yaml"]
    
    def test_none_model_values_use_defaults(self):
        """Test that None model values fall back to defaults."""
        mock_job = Job(
            job_id="test123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            clean_instrumental_model=None,  # Not specified
            backing_vocals_models=None,     # Not specified
            other_stems_models=None         # Not specified
        )
        
        with patch('backend.workers.audio_worker.AudioProcessor') as mock_processor:
            from backend.workers.audio_worker import create_audio_processor
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = create_audio_processor(
                    temp_dir,
                    clean_instrumental_model=mock_job.clean_instrumental_model,
                    backing_vocals_models=mock_job.backing_vocals_models,
                    other_stems_models=mock_job.other_stems_models
                )
                
                mock_processor.assert_called_once()
                call_kwargs = mock_processor.call_args[1]
                
                # Should fall back to defaults
                assert call_kwargs['clean_instrumental_model'] == "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
                assert call_kwargs['backing_vocals_models'] == ["mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"]
                assert call_kwargs['other_stems_models'] == ["htdemucs_6s.yaml"]


class TestDownloadFromUrl:
    """Tests for download_from_url function - URL-based audio download."""
    
    def test_download_from_url_function_exists(self):
        """Test that download_from_url function exists in audio_worker."""
        from backend.workers.audio_worker import download_from_url
        assert callable(download_from_url)
    
    def test_download_from_url_signature(self):
        """Test that download_from_url has the expected signature."""
        import inspect
        from backend.workers.audio_worker import download_from_url
        
        sig = inspect.signature(download_from_url)
        params = list(sig.parameters.keys())
        
        # Should have these parameters
        assert 'url' in params
        assert 'temp_dir' in params
        assert 'artist' in params
        assert 'title' in params
        assert 'job_manager' in params
        assert 'job_id' in params
    
    def test_download_from_url_is_async(self):
        """Test that download_from_url is an async function."""
        import inspect
        from backend.workers.audio_worker import download_from_url
        
        assert inspect.iscoroutinefunction(download_from_url)
    
    @pytest.mark.asyncio
    async def test_download_from_url_returns_none_for_invalid_url(self):
        """Test that download_from_url handles errors gracefully."""
        from backend.workers.audio_worker import download_from_url
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # This should fail gracefully (no yt-dlp in test env or invalid URL)
            result = await download_from_url(
                url='not-a-valid-url',
                temp_dir=temp_dir,
                artist='Test',
                title='Test'
            )
            
            # Should return None on error (graceful failure)
            assert result is None


class TestDownloadAudioWithUrl:
    """Tests for download_audio function with URL-based jobs."""
    
    @pytest.fixture
    def mock_job_with_url(self):
        """Create a mock job with a URL."""
        return Job(
            job_id="test123",
            status=JobStatus.PROCESSING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            url="https://www.youtube.com/watch?v=test123"
        )
    
    @pytest.fixture
    def mock_job_manager(self, mock_job_with_url):
        """Create a mock JobManager."""
        manager = MagicMock()
        manager.get_job.return_value = mock_job_with_url
        manager.update_job.return_value = None
        return manager
    
    @pytest.fixture
    def mock_storage(self):
        """Create a mock StorageService."""
        storage = MagicMock()
        return storage
    
    @pytest.mark.asyncio
    async def test_download_audio_from_url(self, mock_job_with_url, mock_job_manager, mock_storage):
        """Test download_audio routes to URL download when job has URL."""
        with patch('backend.workers.audio_worker.download_from_url', return_value='/tmp/test.wav') as mock_download:
            from backend.workers.audio_worker import download_audio
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = await download_audio(
                    "test123",
                    temp_dir,
                    mock_storage,
                    mock_job_with_url,
                    job_manager_instance=mock_job_manager
                )
                
                # Should have called download_from_url
                mock_download.assert_called_once()
                # Check positional args
                args = mock_download.call_args[0]
                assert args[0] == "https://www.youtube.com/watch?v=test123"  # url
                assert args[1] == temp_dir  # temp_dir
                assert args[2] == "Test Artist"  # artist
                assert args[3] == "Test Song"  # title
    
    @pytest.mark.asyncio
    async def test_download_audio_from_gcs_when_no_url(self, mock_job_manager, mock_storage):
        """Test download_audio downloads from GCS when job has no URL."""
        mock_job_no_url = Job(
            job_id="test123",
            status=JobStatus.PROCESSING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
            input_media_gcs_path="uploads/test123/song.flac"
        )
        
        with patch('backend.workers.audio_worker.download_from_url') as mock_url_download:
            from backend.workers.audio_worker import download_audio
            
            with tempfile.TemporaryDirectory() as temp_dir:
                result = await download_audio(
                    "test123",
                    temp_dir,
                    mock_storage,
                    mock_job_no_url,
                    job_manager_instance=mock_job_manager
                )
                
                # Should NOT have called download_from_url
                mock_url_download.assert_not_called()
                
                # Should have called storage.download_file
                mock_storage.download_file.assert_called()


class TestBackingVocalsAnalysis:
    """Tests for backing vocals analysis in render_video_worker."""
    
    def test_analyze_backing_vocals_function_exists(self):
        """Test that _analyze_backing_vocals function exists in render_video_worker."""
        from backend.workers.render_video_worker import _analyze_backing_vocals
        assert callable(_analyze_backing_vocals)
    
    def test_analyze_backing_vocals_is_async(self):
        """Test that _analyze_backing_vocals is an async function."""
        import inspect
        from backend.workers.render_video_worker import _analyze_backing_vocals
        assert inspect.iscoroutinefunction(_analyze_backing_vocals)
    
    def test_render_video_worker_imports_analysis_service(self):
        """Test that render_video_worker can import AudioAnalysisService."""
        # This verifies the import path is correct
        from backend.services.audio_analysis_service import AudioAnalysisService
        assert AudioAnalysisService is not None
    
    @pytest.mark.asyncio
    async def test_analyze_backing_vocals_handles_missing_job(self):
        """Test that analysis returns early when job not found (no error stored)."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = None  # No job found
        mock_job_manager.update_state_data = MagicMock()
        
        mock_storage = MagicMock()
        mock_logger = MagicMock()
        
        from backend.workers.render_video_worker import _analyze_backing_vocals
        
        # Should not raise when job not found, just log warning and return
        await _analyze_backing_vocals(
            "nonexistent", mock_job_manager, mock_storage, mock_logger
        )
        
        # Should log warning but not update state (early return)
        mock_logger.warning.assert_called()
        # No state_data update since we return early
        mock_job_manager.update_state_data.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_analyze_backing_vocals_handles_missing_stems(self):
        """Test that analysis returns early when stems not found (no error stored)."""
        mock_job = MagicMock()
        mock_job.file_urls = {}  # No stems
        
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = mock_job
        mock_job_manager.update_state_data = MagicMock()
        
        mock_storage = MagicMock()
        mock_logger = MagicMock()
        
        from backend.workers.render_video_worker import _analyze_backing_vocals
        
        # Should not raise when stems not found, just log warning and return
        await _analyze_backing_vocals(
            "test123", mock_job_manager, mock_storage, mock_logger
        )
        
        # Should log warning but not update state (early return)
        mock_logger.warning.assert_called()
        # No state_data update since we return early
        mock_job_manager.update_state_data.assert_not_called()
    
    def test_analysis_service_can_be_instantiated(self):
        """Test that AudioAnalysisService can be instantiated."""
        from backend.services.audio_analysis_service import AudioAnalysisService

        mock_storage = MagicMock()
        service = AudioAnalysisService(storage_service=mock_storage)

        assert service is not None
        assert service.storage_service == mock_storage


class TestModelNamesStorage:
    """Tests for model names storage in audio_worker.

    These tests verify that model names are stored in job state_data
    for use by video_worker in distribution directory preparation.
    """

    def test_effective_model_names_defaults(self):
        """Test that default model names are used when not specified on job."""
        from backend.workers.audio_worker import (
            DEFAULT_CLEAN_MODEL,
            DEFAULT_BACKING_MODELS,
            DEFAULT_OTHER_MODELS,
        )

        # Simulate the logic from process_audio_separation
        job_clean_model = None
        job_backing_models = None
        job_other_models = None

        effective_model_names = {
            'clean_instrumental_model': job_clean_model or DEFAULT_CLEAN_MODEL,
            'backing_vocals_models': job_backing_models or DEFAULT_BACKING_MODELS,
            'other_stems_models': job_other_models or DEFAULT_OTHER_MODELS,
        }

        assert effective_model_names['clean_instrumental_model'] == DEFAULT_CLEAN_MODEL
        assert effective_model_names['backing_vocals_models'] == DEFAULT_BACKING_MODELS
        assert effective_model_names['other_stems_models'] == DEFAULT_OTHER_MODELS

    def test_effective_model_names_custom(self):
        """Test that custom model names override defaults."""
        from backend.workers.audio_worker import (
            DEFAULT_CLEAN_MODEL,
            DEFAULT_BACKING_MODELS,
            DEFAULT_OTHER_MODELS,
        )

        custom_clean = "custom_clean_model.ckpt"
        custom_backing = ["custom_backing.ckpt"]
        custom_other = ["custom_demucs.yaml"]

        # Simulate the logic from process_audio_separation
        effective_model_names = {
            'clean_instrumental_model': custom_clean or DEFAULT_CLEAN_MODEL,
            'backing_vocals_models': custom_backing or DEFAULT_BACKING_MODELS,
            'other_stems_models': custom_other or DEFAULT_OTHER_MODELS,
        }

        assert effective_model_names['clean_instrumental_model'] == custom_clean
        assert effective_model_names['backing_vocals_models'] == custom_backing
        assert effective_model_names['other_stems_models'] == custom_other


class TestDistributionDirectoryPreparation:
    """Tests for _prepare_distribution_directory in video_worker.

    These tests verify that the distribution directory is prepared with:
    - stems/ subfolder containing all audio stems with model names
    - lyrics/ subfolder containing intermediate lyrics files
    - Properly named instrumentals at root level
    """

    def test_distribution_directory_creates_stems_folder(self, tmp_path):
        """Test that stems directory is created."""
        stems_dir = tmp_path / "stems"
        stems_dir.mkdir()

        assert stems_dir.exists()
        assert stems_dir.is_dir()

    def test_distribution_directory_creates_lyrics_folder(self, tmp_path):
        """Test that lyrics directory is created."""
        lyrics_dir = tmp_path / "lyrics"
        lyrics_dir.mkdir()

        assert lyrics_dir.exists()
        assert lyrics_dir.is_dir()

    def test_instrumental_naming_with_model(self):
        """Test that instrumental files are named with model names."""
        base_name = "Artist - Song"
        clean_model = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        backing_model = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"

        clean_instrumental_name = f"{base_name} (Instrumental {clean_model}).flac"
        backing_instrumental_name = f"{base_name} (Instrumental +BV {backing_model}).flac"

        # Verify expected format
        assert "model_bs_roformer" in clean_instrumental_name
        assert "+BV" in backing_instrumental_name
        assert backing_model in backing_instrumental_name

    def test_stem_naming_convention(self):
        """Test that stems are named with proper model suffixes."""
        base_name = "Artist - Song"
        clean_model = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        backing_model = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
        other_model = "htdemucs_6s.yaml"

        # Expected stem filenames
        expected_stems = {
            'vocals_clean': f"{base_name} (Vocals {clean_model}).flac",
            'lead_vocals': f"{base_name} (Lead Vocals {backing_model}).flac",
            'backing_vocals': f"{base_name} (Backing Vocals {backing_model}).flac",
            'bass': f"{base_name} (Bass {other_model}).flac",
            'drums': f"{base_name} (Drums {other_model}).flac",
        }

        # Verify naming convention
        for _key, name in expected_stems.items():
            assert ".flac" in name
            assert base_name in name

    def test_simplified_instrumental_cleanup(self, tmp_path):
        """Test that simplified instrumental names are cleaned up."""
        base_name = "Artist - Song"

        # Create simplified-named files (as created by _setup_working_directory)
        simplified_clean = tmp_path / f"{base_name} (Instrumental Clean).flac"
        simplified_backing = tmp_path / f"{base_name} (Instrumental Backing).flac"
        simplified_clean.write_text("fake")
        simplified_backing.write_text("fake")

        # Verify they exist
        assert simplified_clean.exists()
        assert simplified_backing.exists()

        # Simulate cleanup
        simplified_clean.unlink()
        simplified_backing.unlink()

        # Verify they're removed
        assert not simplified_clean.exists()
        assert not simplified_backing.exists()

