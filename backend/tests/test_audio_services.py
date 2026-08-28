"""
Unit tests for audio analysis and editing services.

These tests verify the backend service wrappers that use the shared
karaoke_gen.instrumental_review module with GCS integration.

NOTE: These tests require ffmpeg to be installed. They will be skipped
if ffmpeg is not available (e.g., in CI environments without ffmpeg).
"""

import os
import shutil
import tempfile
import pytest
from unittest.mock import MagicMock, patch, ANY

# Check if ffmpeg is available
def _ffmpeg_available():
    """Check if ffmpeg is available on the system."""
    return shutil.which('ffmpeg') is not None

# Skip all tests in this module if ffmpeg is not available
pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="ffmpeg not available - required for audio processing tests"
)

from pydub import AudioSegment
from pydub.generators import Sine

from karaoke_gen.instrumental_review import MuteRegion


class TestAudioAnalysisService:
    """Tests for AudioAnalysisService."""
    
    @pytest.fixture
    def mock_storage_service(self):
        """Create a mock storage service."""
        mock = MagicMock()
        return mock
    
    @pytest.fixture
    def temp_audio_file(self, tmp_path):
        """Create a temporary audio file for testing."""
        audio_path = tmp_path / "test_audio.flac"
        
        # Create a simple sine wave audio
        tone = Sine(440).to_audio_segment(duration=5000) - 15
        tone.export(str(audio_path), format="flac")
        
        return str(audio_path)
    
    @pytest.fixture
    def silent_audio_file(self, tmp_path):
        """Create a silent audio file for testing."""
        audio_path = tmp_path / "silent_audio.flac"
        
        audio = AudioSegment.silent(duration=5000, frame_rate=44100)
        audio.export(str(audio_path), format="flac")
        
        return str(audio_path)
    
    def test_service_initialization(self, mock_storage_service):
        """Test service initializes with default parameters."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        service = AudioAnalysisService(storage_service=mock_storage_service)
        
        assert service.storage_service == mock_storage_service
        assert service.analyzer.silence_threshold_db == -40.0
    
    def test_service_custom_threshold(self, mock_storage_service):
        """Test service accepts custom silence threshold."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        service = AudioAnalysisService(
            storage_service=mock_storage_service,
            silence_threshold_db=-35.0,
        )
        
        assert service.analyzer.silence_threshold_db == -35.0
    
    def test_analyze_backing_vocals_downloads_file(
        self, mock_storage_service, temp_audio_file
    ):
        """Test that analyze_backing_vocals downloads file from GCS."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        # Set up mock to copy the temp file to the download location
        def mock_download(gcs_path, local_path):
            import shutil
            shutil.copy(temp_audio_file, local_path)
            return local_path
        
        mock_storage_service.download_file.side_effect = mock_download
        
        service = AudioAnalysisService(storage_service=mock_storage_service)
        result = service.analyze_backing_vocals(
            gcs_audio_path="jobs/test/stems/backing_vocals.flac",
            job_id="test-job",
        )
        
        # Verify download was called
        mock_storage_service.download_file.assert_called_once()
        
        # Verify result is valid
        assert result is not None
        assert hasattr(result, 'has_audible_content')
    
    def test_analyze_silent_audio_returns_no_audible(
        self, mock_storage_service, silent_audio_file
    ):
        """Test that silent audio returns has_audible_content=False."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        def mock_download(gcs_path, local_path):
            import shutil
            shutil.copy(silent_audio_file, local_path)
            return local_path
        
        mock_storage_service.download_file.side_effect = mock_download
        
        service = AudioAnalysisService(storage_service=mock_storage_service)
        result = service.analyze_backing_vocals(
            gcs_audio_path="jobs/test/stems/backing_vocals.flac",
            job_id="test-job",
        )
        
        assert result.has_audible_content is False
        assert len(result.audible_segments) == 0
    
    def test_analyze_and_generate_waveform_uploads_image(
        self, mock_storage_service, temp_audio_file
    ):
        """Test that analyze_and_generate_waveform uploads waveform to GCS."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        def mock_download(gcs_path, local_path):
            import shutil
            shutil.copy(temp_audio_file, local_path)
            return local_path
        
        mock_storage_service.download_file.side_effect = mock_download
        mock_storage_service.upload_file.return_value = "jobs/test/analysis/waveform.png"
        
        service = AudioAnalysisService(storage_service=mock_storage_service)
        result, waveform_path, comparison = service.analyze_and_generate_waveform(
            gcs_audio_path="jobs/test/stems/backing_vocals.flac",
            job_id="test-job",
            gcs_waveform_destination="jobs/test/analysis/waveform.png",
        )

        # Verify upload was called
        mock_storage_service.upload_file.assert_called_once()

        # Verify waveform path is returned
        assert waveform_path == "jobs/test/analysis/waveform.png"
        # No lead/vocals stems passed -> no 3-stem comparison
        assert comparison is None

    def test_analyze_with_extra_stems_returns_comparison(
        self, mock_storage_service, temp_audio_file
    ):
        """Passing lead + vocals stem paths yields a stem_comparison dict."""
        from backend.services.audio_analysis_service import AudioAnalysisService

        def mock_download(gcs_path, local_path):
            import shutil
            shutil.copy(temp_audio_file, local_path)
            return local_path

        mock_storage_service.download_file.side_effect = mock_download
        mock_storage_service.upload_file.return_value = "jobs/test/analysis/waveform.png"

        service = AudioAnalysisService(storage_service=mock_storage_service)
        _result, _waveform, comparison = service.analyze_and_generate_waveform(
            gcs_audio_path="jobs/test/stems/backing_vocals.flac",
            job_id="test-job",
            gcs_waveform_destination="jobs/test/analysis/waveform.png",
            gcs_lead_vocals_path="jobs/test/stems/lead_vocals.flac",
            gcs_vocals_path="jobs/test/stems/vocals_clean.flac",
        )

        assert comparison is not None
        assert comparison["error"] is None
        # All three "stems" are the same file here -> full coverage + corr 1.
        assert comparison["coverage_ratio"] == 1.0

    def test_comparison_download_failure_is_non_fatal(
        self, mock_storage_service, temp_audio_file
    ):
        """A failure fetching the extra stems yields comparison=None, no raise."""
        from backend.services.audio_analysis_service import AudioAnalysisService

        calls = {"n": 0}

        def mock_download(gcs_path, local_path):
            import shutil
            calls["n"] += 1
            if calls["n"] > 1:  # backing succeeds, extra stems fail
                raise RuntimeError("gcs down")
            shutil.copy(temp_audio_file, local_path)
            return local_path

        mock_storage_service.download_file.side_effect = mock_download
        mock_storage_service.upload_file.return_value = "jobs/test/analysis/waveform.png"

        service = AudioAnalysisService(storage_service=mock_storage_service)
        _result, waveform_path, comparison = service.analyze_and_generate_waveform(
            gcs_audio_path="jobs/test/stems/backing_vocals.flac",
            job_id="test-job",
            gcs_waveform_destination="jobs/test/analysis/waveform.png",
            gcs_lead_vocals_path="jobs/test/stems/lead_vocals.flac",
            gcs_vocals_path="jobs/test/stems/vocals_clean.flac",
        )

        assert waveform_path == "jobs/test/analysis/waveform.png"
        assert comparison is None
    
    def test_get_waveform_data_returns_amplitudes(
        self, mock_storage_service, temp_audio_file
    ):
        """Test that get_waveform_data returns amplitude data."""
        from backend.services.audio_analysis_service import AudioAnalysisService
        
        def mock_download(gcs_path, local_path):
            import shutil
            shutil.copy(temp_audio_file, local_path)
            return local_path
        
        mock_storage_service.download_file.side_effect = mock_download
        
        service = AudioAnalysisService(storage_service=mock_storage_service)
        amplitudes, duration = service.get_waveform_data(
            gcs_audio_path="jobs/test/stems/backing_vocals.flac",
            job_id="test-job",
            num_points=100,
        )
        
        # Verify we get amplitude data
        assert isinstance(amplitudes, list)
        assert len(amplitudes) > 0
        assert all(isinstance(a, float) for a in amplitudes)
        
        # Verify duration is reasonable
        assert duration > 0
        assert duration <= 10  # Our test file is 5 seconds


class TestAudioEditingService:
    """Tests for AudioEditingService."""
    
    @pytest.fixture
    def mock_storage_service(self):
        """Create a mock storage service."""
        mock = MagicMock()
        return mock
    
    @pytest.fixture
    def temp_audio_files(self, tmp_path):
        """Create temporary audio files for testing."""
        clean_path = tmp_path / "clean_instrumental.flac"
        backing_path = tmp_path / "backing_vocals.flac"
        
        # Create clean instrumental (chord)
        tone1 = Sine(220).to_audio_segment(duration=5000) - 15
        tone2 = Sine(330).to_audio_segment(duration=5000) - 15
        clean = tone1.overlay(tone2)
        clean.export(str(clean_path), format="flac")
        
        # Create backing vocals
        backing = Sine(440).to_audio_segment(duration=5000) - 20
        backing.export(str(backing_path), format="flac")
        
        return str(clean_path), str(backing_path)
    
    def test_service_initialization(self, mock_storage_service):
        """Test service initializes correctly."""
        from backend.services.audio_editing_service import AudioEditingService
        
        service = AudioEditingService(storage_service=mock_storage_service)
        
        assert service.storage_service == mock_storage_service
        assert service.editor.output_format == "flac"
    
    def test_create_custom_instrumental_downloads_files(
        self, mock_storage_service, temp_audio_files
    ):
        """Test that create_custom_instrumental downloads input files."""
        from backend.services.audio_editing_service import AudioEditingService
        
        clean_path, backing_path = temp_audio_files
        
        # Track which files were downloaded
        downloaded_files = []
        
        def mock_download(gcs_path, local_path):
            downloaded_files.append(gcs_path)
            # Copy the appropriate test file
            import shutil
            if 'clean' in gcs_path:
                shutil.copy(clean_path, local_path)
            else:
                shutil.copy(backing_path, local_path)
            return local_path
        
        mock_storage_service.download_file.side_effect = mock_download
        mock_storage_service.upload_file.return_value = "jobs/test/stems/custom.flac"
        
        service = AudioEditingService(storage_service=mock_storage_service)
        
        mute_regions = [MuteRegion(start_seconds=1.0, end_seconds=2.0)]
        
        result = service.create_custom_instrumental(
            gcs_clean_instrumental_path="jobs/test/stems/clean.flac",
            gcs_backing_vocals_path="jobs/test/stems/backing.flac",
            mute_regions=mute_regions,
            gcs_output_path="jobs/test/stems/custom.flac",
            job_id="test-job",
        )
        
        # Verify both files were downloaded
        assert len(downloaded_files) == 2
        assert mock_storage_service.upload_file.called
    
    def test_create_custom_instrumental_uploads_result(
        self, mock_storage_service, temp_audio_files
    ):
        """Test that create_custom_instrumental uploads the result."""
        from backend.services.audio_editing_service import AudioEditingService
        
        clean_path, backing_path = temp_audio_files
        
        def mock_download(gcs_path, local_path):
            import shutil
            if 'clean' in gcs_path:
                shutil.copy(clean_path, local_path)
            else:
                shutil.copy(backing_path, local_path)
            return local_path
        
        mock_storage_service.download_file.side_effect = mock_download
        mock_storage_service.upload_file.return_value = "jobs/test/stems/custom.flac"
        
        service = AudioEditingService(storage_service=mock_storage_service)
        
        mute_regions = [MuteRegion(start_seconds=1.0, end_seconds=2.0)]
        
        result = service.create_custom_instrumental(
            gcs_clean_instrumental_path="jobs/test/stems/clean.flac",
            gcs_backing_vocals_path="jobs/test/stems/backing.flac",
            mute_regions=mute_regions,
            gcs_output_path="jobs/test/stems/custom.flac",
            job_id="test-job",
        )
        
        # Verify upload was called with correct destination
        mock_storage_service.upload_file.assert_called_once()
        call_args = mock_storage_service.upload_file.call_args
        assert call_args[0][1] == "jobs/test/stems/custom.flac"
        
        # Verify result has correct path
        assert result.output_path == "jobs/test/stems/custom.flac"
    
    def test_create_custom_instrumental_returns_statistics(
        self, mock_storage_service, temp_audio_files
    ):
        """Test that create_custom_instrumental returns correct statistics."""
        from backend.services.audio_editing_service import AudioEditingService
        
        clean_path, backing_path = temp_audio_files
        
        def mock_download(gcs_path, local_path):
            import shutil
            if 'clean' in gcs_path:
                shutil.copy(clean_path, local_path)
            else:
                shutil.copy(backing_path, local_path)
            return local_path
        
        mock_storage_service.download_file.side_effect = mock_download
        mock_storage_service.upload_file.return_value = "jobs/test/stems/custom.flac"
        
        service = AudioEditingService(storage_service=mock_storage_service)
        
        mute_regions = [
            MuteRegion(start_seconds=1.0, end_seconds=2.0),
            MuteRegion(start_seconds=3.0, end_seconds=4.0),
        ]
        
        result = service.create_custom_instrumental(
            gcs_clean_instrumental_path="jobs/test/stems/clean.flac",
            gcs_backing_vocals_path="jobs/test/stems/backing.flac",
            mute_regions=mute_regions,
            gcs_output_path="jobs/test/stems/custom.flac",
            job_id="test-job",
        )
        
        # Verify statistics
        assert len(result.mute_regions_applied) == 2
        assert result.total_muted_duration_seconds == 2.0  # 1s + 1s
        assert result.output_duration_seconds > 0
    
    def test_validate_mute_regions_returns_errors_for_invalid(
        self, mock_storage_service
    ):
        """Test that validate_mute_regions catches invalid regions."""
        from backend.services.audio_editing_service import AudioEditingService
        
        service = AudioEditingService(storage_service=mock_storage_service)
        
        mute_regions = [
            MuteRegion(start_seconds=0.0, end_seconds=1.0),  # Valid
            MuteRegion(start_seconds=100.0, end_seconds=110.0),  # Beyond duration
        ]
        
        errors = service.validate_mute_regions(mute_regions, total_duration_seconds=60.0)
        
        # Should have error for region exceeding duration
        assert len(errors) == 1
        assert "exceeds" in errors[0].lower()
    
    def test_validate_mute_regions_returns_empty_for_valid(
        self, mock_storage_service
    ):
        """Test that validate_mute_regions returns empty for valid regions."""
        from backend.services.audio_editing_service import AudioEditingService
        
        service = AudioEditingService(storage_service=mock_storage_service)
        
        mute_regions = [
            MuteRegion(start_seconds=0.0, end_seconds=1.0),
            MuteRegion(start_seconds=10.0, end_seconds=15.0),
        ]
        
        errors = service.validate_mute_regions(mute_regions, total_duration_seconds=60.0)
        
        assert len(errors) == 0
