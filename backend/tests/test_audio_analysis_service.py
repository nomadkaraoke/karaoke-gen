"""
Tests for audio_analysis_service.py - Audio analysis for backing vocals.

These tests mock the storage service and shared karaoke_gen modules to verify:
- GCS file download/upload operations
- Audio analysis delegation to shared AudioAnalyzer
- Waveform generation delegation to shared WaveformGenerator
- Waveform data caching (cache_waveform_data / load_cached_waveform)
"""
import json
import pytest
from unittest.mock import Mock, MagicMock, patch, ANY
import tempfile
import os


class TestAudioAnalysisServiceInit:
    """Test AudioAnalysisService initialization."""
    
    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_init_creates_dependencies(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Test initialization creates storage service and analyzers."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        service = AudioAnalysisService()
        
        mock_storage_class.assert_called_once()
        mock_analyzer_class.assert_called_once_with(
            silence_threshold_db=-40.0,
            min_segment_duration_ms=100,
        )
        mock_waveform_class.assert_called_once()
    
    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_init_with_custom_params(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Test initialization with custom threshold and duration."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        service = AudioAnalysisService(
            silence_threshold_db=-50.0,
            min_segment_duration_ms=200,
        )
        
        mock_analyzer_class.assert_called_once_with(
            silence_threshold_db=-50.0,
            min_segment_duration_ms=200,
        )
    
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_init_with_provided_storage_service(
        self, mock_waveform_class, mock_analyzer_class
    ):
        """Test initialization with externally provided storage service."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        mock_storage = Mock()
        service = AudioAnalysisService(storage_service=mock_storage)
        
        assert service.storage_service is mock_storage


class TestAnalyzeBackingVocals:
    """Test analyze_backing_vocals method."""
    
    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_analyze_downloads_and_analyzes(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Test analyze_backing_vocals downloads file and runs analysis."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        # Setup mocks
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        
        mock_result = Mock()
        mock_result.has_audible_content = True
        mock_result.segment_count = 5
        mock_result.recommended_selection = Mock(value="with_backing")
        
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = mock_result
        mock_analyzer_class.return_value = mock_analyzer
        
        service = AudioAnalysisService()
        
        # Call method
        result = service.analyze_backing_vocals(
            gcs_audio_path="uploads/job123/backing_vocals.flac",
            job_id="job123",
        )
        
        # Verify storage download was called
        mock_storage.download_file.assert_called_once()
        call_args = mock_storage.download_file.call_args
        assert call_args[0][0] == "uploads/job123/backing_vocals.flac"
        
        # Verify analyzer was called
        mock_analyzer.analyze.assert_called_once()
        
        # Verify result
        assert result is mock_result
        assert result.has_audible_content is True
    
    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_analyze_cleans_up_temp_files(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Test that temp files are cleaned up after analysis."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        
        mock_result = Mock()
        mock_result.has_audible_content = False
        mock_result.segment_count = 0
        mock_result.recommended_selection = Mock(value="clean")
        
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = mock_result
        mock_analyzer_class.return_value = mock_analyzer
        
        service = AudioAnalysisService()
        
        # Call method
        service.analyze_backing_vocals(
            gcs_audio_path="uploads/job123/backing.flac",
            job_id="job123",
        )
        
        # Temp directory should be cleaned up (no assertion needed,
        # TemporaryDirectory context manager handles this)
        # Just verify the method completes without error


class TestAnalyzeAndGenerateWaveform:
    """Test analyze_and_generate_waveform method."""
    
    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_analyze_and_generate_waveform(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Test analyze_and_generate_waveform does both operations."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        
        mock_result = Mock()
        mock_result.has_audible_content = True
        mock_result.audible_segments = [Mock(), Mock()]
        
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = mock_result
        mock_analyzer.silence_threshold_db = -40.0
        mock_analyzer_class.return_value = mock_analyzer
        
        mock_waveform = Mock()
        mock_waveform_class.return_value = mock_waveform
        
        service = AudioAnalysisService()
        
        # Call method
        result, waveform_path = service.analyze_and_generate_waveform(
            gcs_audio_path="uploads/job123/backing.flac",
            job_id="job123",
            gcs_waveform_destination="uploads/job123/waveform.png",
        )
        
        # Verify analysis was performed
        mock_analyzer.analyze.assert_called_once()
        
        # Verify waveform was generated
        mock_waveform.generate.assert_called_once()
        call_kwargs = mock_waveform.generate.call_args.kwargs
        assert call_kwargs["segments"] == mock_result.audible_segments
        assert call_kwargs["show_time_axis"] is True
        
        # Verify waveform was uploaded
        assert mock_storage.upload_file.call_count == 1
        
        # Verify return values
        assert result is mock_result
        assert waveform_path == "uploads/job123/waveform.png"


class TestGetWaveformData:
    """Test get_waveform_data method."""
    
    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_get_waveform_data(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Test get_waveform_data returns amplitude data."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        
        mock_waveform = Mock()
        mock_waveform.generate_data_only.return_value = (
            [0.1, 0.3, 0.5, 0.3, 0.1],  # amplitudes
            180.5,  # duration
        )
        mock_waveform_class.return_value = mock_waveform
        
        service = AudioAnalysisService()
        
        amplitudes, duration = service.get_waveform_data(
            gcs_audio_path="uploads/job123/backing.flac",
            job_id="job123",
            num_points=500,
        )
        
        # Verify storage download
        mock_storage.download_file.assert_called_once()
        
        # Verify waveform generator called
        mock_waveform.generate_data_only.assert_called_once()
        call_kwargs = mock_waveform.generate_data_only.call_args.kwargs
        assert call_kwargs["num_points"] == 500
        
        # Verify return values
        assert amplitudes == [0.1, 0.3, 0.5, 0.3, 0.1]
        assert duration == 180.5


class TestGenerateWaveformWithMutes:
    """Test generate_waveform_with_mutes method."""
    
    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_generate_waveform_with_mutes(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Test generate_waveform_with_mutes highlights mute regions."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        
        mock_result = Mock()
        mock_result.audible_segments = [Mock(), Mock()]
        
        mock_analyzer = Mock()
        mock_analyzer.analyze.return_value = mock_result
        mock_analyzer_class.return_value = mock_analyzer
        
        mock_waveform = Mock()
        mock_waveform_class.return_value = mock_waveform
        
        # Create mock mute regions
        mock_mute1 = Mock()
        mock_mute2 = Mock()
        mute_regions = [mock_mute1, mock_mute2]
        
        service = AudioAnalysisService()
        
        result_path = service.generate_waveform_with_mutes(
            gcs_audio_path="uploads/job123/backing.flac",
            job_id="job123",
            gcs_waveform_destination="uploads/job123/waveform_muted.png",
            mute_regions=mute_regions,
        )
        
        # Verify waveform generator was called with mute_regions
        mock_waveform.generate.assert_called_once()
        call_kwargs = mock_waveform.generate.call_args.kwargs
        assert call_kwargs["mute_regions"] == mute_regions
        assert call_kwargs["segments"] == mock_result.audible_segments
        
        # Verify upload
        mock_storage.upload_file.assert_called_once()
        
        # Verify return
        assert result_path == "uploads/job123/waveform_muted.png"


class TestCacheWaveformData:
    """Test cache_waveform_data method."""

    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_caches_waveform_to_gcs(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """cache_waveform_data should generate waveform and upload JSON to GCS."""
        from backend.services.audio_analysis_service import AudioAnalysisService

        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage

        mock_waveform = Mock()
        mock_waveform.generate_data_only.return_value = ([0.1, 0.5, 0.3], 200.0)
        mock_waveform_class.return_value = mock_waveform

        service = AudioAnalysisService()

        amplitudes, duration = service.cache_waveform_data(
            gcs_audio_path="jobs/j1/input/song.flac",
            job_id="j1",
            cache_gcs_path="jobs/j1/audio_edit/waveform_original.json",
            num_points=1000,
        )

        assert amplitudes == [0.1, 0.5, 0.3]
        assert duration == 200.0

        # Verify JSON was uploaded with correct structure
        mock_storage.upload_json.assert_called_once()
        call_args = mock_storage.upload_json.call_args
        assert call_args[0][0] == "jobs/j1/audio_edit/waveform_original.json"
        uploaded_data = call_args[0][1]
        assert uploaded_data["amplitudes"] == [0.1, 0.5, 0.3]
        assert uploaded_data["duration_seconds"] == 200.0
        assert uploaded_data["num_points"] == 1000
        assert uploaded_data["source_gcs_path"] == "jobs/j1/input/song.flac"


class TestLoadCachedWaveform:
    """Test load_cached_waveform method."""

    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_returns_none_when_cache_missing(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Should return None when cache file doesn't exist."""
        from backend.services.audio_analysis_service import AudioAnalysisService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = False
        mock_storage_class.return_value = mock_storage

        service = AudioAnalysisService()
        result = service.load_cached_waveform("jobs/j1/audio_edit/waveform_original.json")

        assert result is None

    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_loads_cached_waveform(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Should load and parse cached waveform JSON from GCS."""
        from backend.services.audio_analysis_service import AudioAnalysisService

        cache_data = {
            "amplitudes": [0.2, 0.6, 0.4],
            "duration_seconds": 180.0,
            "num_points": 1000,
            "source_gcs_path": "jobs/j1/input/song.flac",
        }

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        # Simulate download_file writing JSON to the local path
        def write_json(gcs_path, local_path):
            with open(local_path, 'w') as f:
                json.dump(cache_data, f)
        mock_storage.download_file.side_effect = write_json
        mock_storage_class.return_value = mock_storage

        service = AudioAnalysisService()
        result = service.load_cached_waveform("jobs/j1/audio_edit/waveform_original.json")

        assert result is not None
        amplitudes, duration = result
        assert amplitudes == [0.2, 0.6, 0.4]
        assert duration == 180.0

    @patch("backend.services.audio_analysis_service.StorageService")
    @patch("backend.services.audio_analysis_service.AudioAnalyzer")
    @patch("backend.services.audio_analysis_service.WaveformGenerator")
    def test_returns_none_on_error(
        self, mock_waveform_class, mock_analyzer_class, mock_storage_class
    ):
        """Should return None gracefully on any error."""
        from backend.services.audio_analysis_service import AudioAnalysisService

        mock_storage = Mock()
        mock_storage.file_exists.return_value = True
        mock_storage.download_file.side_effect = Exception("GCS timeout")
        mock_storage_class.return_value = mock_storage

        service = AudioAnalysisService()
        result = service.load_cached_waveform("jobs/j1/audio_edit/waveform_original.json")

        assert result is None

