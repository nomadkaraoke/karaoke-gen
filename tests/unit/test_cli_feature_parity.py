"""
Tests verifying feature parity between local and remote CLIs.

These tests ensure that both local (karaoke-gen) and remote (karaoke-gen-remote)
CLIs use the same shared core module for instrumental review functionality.

Note: The standalone InstrumentalReviewServer has been removed. Instrumental
review is now integrated into the combined review flow via ReviewServer.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestSharedCoreModuleAccess:
    """Verify both CLIs can access the shared core module."""

    def test_local_cli_can_import_analyzer(self):
        """Local CLI should be able to import AudioAnalyzer."""
        from karaoke_gen.instrumental_review import AudioAnalyzer
        assert AudioAnalyzer is not None

    def test_local_cli_can_import_editor(self):
        """Local CLI should be able to import AudioEditor."""
        from karaoke_gen.instrumental_review import AudioEditor
        assert AudioEditor is not None

    def test_local_cli_can_import_waveform_generator(self):
        """Local CLI should be able to import WaveformGenerator."""
        from karaoke_gen.instrumental_review import WaveformGenerator
        assert WaveformGenerator is not None

    def test_backend_can_import_analyzer(self):
        """Backend should be able to import AudioAnalyzer."""
        from karaoke_gen.instrumental_review import AudioAnalyzer
        # Backend service wrapper imports from the same module
        from backend.services.audio_analysis_service import AudioAnalysisService

        # Verify they use the same analyzer class
        mock_storage = MagicMock()
        service = AudioAnalysisService(storage_service=mock_storage)
        assert service.analyzer.__class__.__name__ == "AudioAnalyzer"

    def test_backend_can_import_editor(self):
        """Backend should be able to import AudioEditor."""
        from karaoke_gen.instrumental_review import AudioEditor
        from backend.services.audio_editing_service import AudioEditingService

        mock_storage = MagicMock()
        service = AudioEditingService(storage_service=mock_storage)
        assert service.editor.__class__.__name__ == "AudioEditor"


class TestAnalyzerParity:
    """Verify that both CLIs get identical analysis results."""

    def test_analyzer_parameters_match(self):
        """Analyzer should have same default parameters for both CLIs."""
        from karaoke_gen.instrumental_review import AudioAnalyzer

        # Default analyzer for local CLI
        local_analyzer = AudioAnalyzer()

        # The backend service uses the same analyzer with defaults
        from backend.services.audio_analysis_service import AudioAnalysisService
        mock_storage = MagicMock()
        backend_service = AudioAnalysisService(storage_service=mock_storage)

        # Same default threshold
        assert local_analyzer.silence_threshold_db == backend_service.analyzer.silence_threshold_db
        assert local_analyzer.silence_threshold_db == -40.0

        # Same default min segment duration
        assert local_analyzer.min_segment_duration_ms == backend_service.analyzer.min_segment_duration_ms


class TestEditorParity:
    """Verify that both CLIs create identical custom instrumentals."""

    def test_editor_output_format_match(self):
        """Editor should produce same output format for both CLIs."""
        from karaoke_gen.instrumental_review import AudioEditor

        # Default editor for local CLI
        local_editor = AudioEditor()

        # Backend service uses same editor with same defaults
        from backend.services.audio_editing_service import AudioEditingService
        mock_storage = MagicMock()
        backend_service = AudioEditingService(storage_service=mock_storage)

        # Same default output format
        assert local_editor.output_format == backend_service.editor.output_format
        assert local_editor.output_format == "flac"


class TestModelConsistency:
    """Verify that models are consistent across CLIs."""

    def test_mute_region_model_shared(self):
        """MuteRegion model should be shared across CLIs."""
        from karaoke_gen.instrumental_review import MuteRegion
        from backend.models.requests import MuteRegionRequest

        # Both should validate start < end
        region = MuteRegion(start_seconds=10.0, end_seconds=20.0)
        request = MuteRegionRequest(start_seconds=10.0, end_seconds=20.0)

        assert region.start_seconds == request.start_seconds
        assert region.end_seconds == request.end_seconds

    def test_analysis_result_fields_match(self):
        """AnalysisResult should have fields expected by both CLIs."""
        from karaoke_gen.instrumental_review import AnalysisResult, RecommendedSelection

        # Create an analysis result
        result = AnalysisResult(
            has_audible_content=True,
            total_duration_seconds=180.0,
            audible_segments=[],
            recommended_selection=RecommendedSelection.REVIEW_NEEDED,
            silence_threshold_db=-40.0,
        )

        # Fields expected by the API response
        assert hasattr(result, 'has_audible_content')
        assert hasattr(result, 'total_duration_seconds')
        assert hasattr(result, 'audible_segments')
        assert hasattr(result, 'recommended_selection')
        assert hasattr(result, 'total_audible_duration_seconds')
        assert hasattr(result, 'audible_percentage')


class TestCombinedReviewServer:
    """Verify that combined review server is available."""

    def test_review_server_exists(self):
        """Combined review server should exist."""
        from karaoke_gen.lyrics_transcriber.review.server import ReviewServer
        assert ReviewServer is not None

    def test_review_server_accepts_instrumental_options(self):
        """Combined review server should accept instrumental options parameter."""
        import inspect
        from karaoke_gen.lyrics_transcriber.review.server import ReviewServer

        # Check constructor signature accepts instrumental parameters
        sig = inspect.signature(ReviewServer.__init__)
        params = sig.parameters

        assert 'instrumental_options' in params
        assert 'backing_vocals_analysis' in params
        assert 'clean_instrumental_path' in params
        assert 'with_backing_path' in params
        assert 'backing_vocals_path' in params


class TestRemoteCLIAnalysisIntegration:
    """Verify remote CLI uses the analysis API."""

    def test_remote_cli_has_analysis_method(self):
        """Remote CLI client should have get_instrumental_analysis method."""
        from karaoke_gen.utils.remote_cli import RemoteKaraokeClient

        # The client should have the analysis method
        assert hasattr(RemoteKaraokeClient, 'get_instrumental_analysis')

    def test_remote_cli_has_browser_method(self):
        """Remote CLI should be able to open browser for review."""
        from karaoke_gen.utils.remote_cli import JobMonitor

        # The job monitor should have browser review method
        assert hasattr(JobMonitor, '_open_instrumental_review_and_wait')

    def test_remote_cli_handle_instrumental_selection_exists(self):
        """Remote CLI should have handle_instrumental_selection method."""
        from karaoke_gen.utils.remote_cli import JobMonitor

        assert hasattr(JobMonitor, 'handle_instrumental_selection')


class TestLocalCLIIntegration:
    """Verify local CLI actually integrates the combined review UI.

    These tests ensure the local CLI (gen_cli.py) properly integrates with
    the combined review flow that handles both lyrics and instrumental review.
    """

    def test_gen_cli_has_run_combined_review_function(self):
        """gen_cli.py should have run_combined_review function."""
        from karaoke_gen.utils import gen_cli

        # The function should exist
        assert hasattr(gen_cli, 'run_combined_review')
        assert callable(gen_cli.run_combined_review)

    def test_gen_cli_imports_audio_analysis_components(self):
        """gen_cli.py should import audio analysis components."""
        import karaoke_gen.utils.gen_cli as gen_cli
        import inspect

        # Get the source of the module to verify imports
        source = inspect.getsource(gen_cli)

        # Should import the key components for analysis
        # Note: WaveformGenerator is now used internally by the controller/ReviewServer
        assert 'from karaoke_gen.instrumental_review import' in source
        assert 'AudioAnalyzer' in source

    def test_gen_cli_has_skip_instrumental_review_flag(self):
        """gen_cli should support --skip_instrumental_review flag."""
        from karaoke_gen.utils.cli_args import create_parser

        parser = create_parser(prog="test")

        # Parse with the flag
        args = parser.parse_args(['--skip_instrumental_review', 'Test', 'Song'])

        assert hasattr(args, 'skip_instrumental_review')
        assert args.skip_instrumental_review == True

    def test_gen_cli_skip_instrumental_review_defaults_false(self):
        """skip_instrumental_review should default to False."""
        from karaoke_gen.utils.cli_args import create_parser

        parser = create_parser(prog="test")

        # Parse without the flag
        args = parser.parse_args(['Test', 'Song'])

        assert hasattr(args, 'skip_instrumental_review')
        assert args.skip_instrumental_review == False
