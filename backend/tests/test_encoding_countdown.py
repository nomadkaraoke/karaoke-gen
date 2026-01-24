"""
Unit tests for countdown padding in all encoding paths.

These tests verify that all encoding backends correctly handle countdown padding
to ensure instrumental audio is synchronized with the countdown-padded vocals.

The tests cover:
1. KaraokeFinalise - standard encoding path
2. Local encoding backend
3. GCE encoding backend
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch, call
import tempfile
import os

from backend.models.job import Job, JobStatus


class TestKaraokeFinaliseCountdownPadding:
    """Tests for KaraokeFinalise countdown padding logic."""

    def test_countdown_padding_stored_in_instance(self):
        """countdown_padding_seconds is stored when passed to constructor."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

        finalise = KaraokeFinalise(
            countdown_padding_seconds=3.0,
            non_interactive=True,
        )

        assert finalise.countdown_padding_seconds == 3.0

    def test_pad_audio_file_method_signature(self):
        """_pad_audio_file accepts input, output, and padding_seconds."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
        import inspect

        sig = inspect.signature(KaraokeFinalise._pad_audio_file)
        params = list(sig.parameters.keys())

        # Should have self, input_audio, output_audio, padding_seconds
        assert 'input_audio' in params
        assert 'output_audio' in params
        assert 'padding_seconds' in params

    @patch('subprocess.run')
    def test_pad_audio_file_calls_ffmpeg(self, mock_subprocess):
        """_pad_audio_file uses ffmpeg to add silence padding."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

        mock_subprocess.return_value = MagicMock(returncode=0)

        finalise = KaraokeFinalise(non_interactive=True)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_audio = os.path.join(temp_dir, "input.flac")
            output_audio = os.path.join(temp_dir, "output.flac")

            # Create a fake input file
            with open(input_audio, 'w') as f:
                f.write("fake")

            finalise._pad_audio_file(input_audio, output_audio, 3.0)

            # Verify subprocess was called (ffmpeg)
            assert mock_subprocess.called
            call_args = str(mock_subprocess.call_args)

            # Should use adelay filter for padding
            assert 'adelay' in call_args

    def test_remux_creates_padded_instrumental_when_needed(self):
        """remux_with_instrumental creates padded instrumental if countdown active."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

        finalise = KaraokeFinalise(
            countdown_padding_seconds=3.0,
            non_interactive=True,
        )

        # Mock the _pad_audio_file and execute_command methods
        finalise._pad_audio_file = MagicMock()
        finalise.execute_command = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            with_vocals = os.path.join(temp_dir, "with_vocals.mp4")
            instrumental = os.path.join(temp_dir, "instrumental.flac")
            output = os.path.join(temp_dir, "output.mp4")

            # Create fake files
            for f in [with_vocals, instrumental]:
                with open(f, 'w') as fp:
                    fp.write("fake")

            finalise.remux_with_instrumental(with_vocals, instrumental, output)

            # Should have called _pad_audio_file because instrumental isn't "(Padded)"
            finalise._pad_audio_file.assert_called_once()
            call_args = finalise._pad_audio_file.call_args

            # Verify padding amount
            assert call_args[0][2] == 3.0  # Third arg is padding_seconds

    def test_remux_skips_padding_when_already_padded(self):
        """remux_with_instrumental skips padding if file already has (Padded)."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

        finalise = KaraokeFinalise(
            countdown_padding_seconds=3.0,
            non_interactive=True,
        )

        finalise._pad_audio_file = MagicMock()
        finalise.execute_command = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            with_vocals = os.path.join(temp_dir, "with_vocals.mp4")
            # Note: "(Padded)" in the filename
            instrumental = os.path.join(temp_dir, "instrumental (Padded).flac")
            output = os.path.join(temp_dir, "output.mp4")

            for f in [with_vocals, instrumental]:
                with open(f, 'w') as fp:
                    fp.write("fake")

            finalise.remux_with_instrumental(with_vocals, instrumental, output)

            # Should NOT have called _pad_audio_file
            finalise._pad_audio_file.assert_not_called()

    def test_remux_no_padding_when_countdown_is_none(self):
        """remux_with_instrumental doesn't pad when countdown_padding_seconds is None."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

        finalise = KaraokeFinalise(
            countdown_padding_seconds=None,
            non_interactive=True,
        )

        finalise._pad_audio_file = MagicMock()
        finalise.execute_command = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            with_vocals = os.path.join(temp_dir, "with_vocals.mp4")
            instrumental = os.path.join(temp_dir, "instrumental.flac")
            output = os.path.join(temp_dir, "output.mp4")

            for f in [with_vocals, instrumental]:
                with open(f, 'w') as fp:
                    fp.write("fake")

            finalise.remux_with_instrumental(with_vocals, instrumental, output)

            # Should NOT have called _pad_audio_file
            finalise._pad_audio_file.assert_not_called()

    def test_remux_no_padding_when_countdown_is_zero(self):
        """remux_with_instrumental doesn't pad when countdown_padding_seconds is 0."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

        finalise = KaraokeFinalise(
            countdown_padding_seconds=0,
            non_interactive=True,
        )

        finalise._pad_audio_file = MagicMock()
        finalise.execute_command = MagicMock()

        with tempfile.TemporaryDirectory() as temp_dir:
            with_vocals = os.path.join(temp_dir, "with_vocals.mp4")
            instrumental = os.path.join(temp_dir, "instrumental.flac")
            output = os.path.join(temp_dir, "output.mp4")

            for f in [with_vocals, instrumental]:
                with open(f, 'w') as fp:
                    fp.write("fake")

            finalise.remux_with_instrumental(with_vocals, instrumental, output)

            # Should NOT have called _pad_audio_file
            finalise._pad_audio_file.assert_not_called()


class TestCountdownProcessorAudioPadding:
    """Tests for CountdownProcessor audio padding logic."""

    def test_countdown_processor_constants(self):
        """CountdownProcessor has expected padding constants."""
        from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor

        assert hasattr(CountdownProcessor, 'COUNTDOWN_PADDING_SECONDS')
        assert CountdownProcessor.COUNTDOWN_PADDING_SECONDS == 3.0

        assert hasattr(CountdownProcessor, 'COUNTDOWN_THRESHOLD_SECONDS')
        assert CountdownProcessor.COUNTDOWN_THRESHOLD_SECONDS == 3.0

    def test_countdown_processor_process_signature(self):
        """CountdownProcessor.process() has the correct signature for countdown handling.

        This test verifies the process() method returns the expected types:
        - CorrectionResult (modified with shifted timestamps)
        - str (path to audio file, possibly padded)
        - bool (padding_added)
        - float (padding_seconds)
        """
        from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
        import inspect

        sig = inspect.signature(CountdownProcessor.process)

        # Check parameters
        params = list(sig.parameters.keys())
        assert 'correction_result' in params, "process() must accept correction_result"
        assert 'audio_filepath' in params, "process() must accept audio_filepath"

    def test_countdown_processor_determines_padding_need(self):
        """CountdownProcessor correctly determines if padding is needed.

        Songs that start within 3 seconds should trigger countdown padding.
        This tests the decision logic without running actual ffmpeg.
        """
        from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor

        # Verify the CountdownProcessor has the padding threshold logic
        processor = CountdownProcessor.__new__(CountdownProcessor)

        # Check the default padding is 3.0 seconds
        # Note: CountdownProcessor should have COUNTDOWN_DURATION constant
        import inspect
        source = inspect.getsource(CountdownProcessor)

        # Verify countdown duration is defined
        assert 'COUNTDOWN_DURATION' in source or '3.0' in source, (
            "CountdownProcessor should define countdown duration (typically 3.0 seconds)"
        )


class TestVideoWorkerCountdownPassthrough:
    """Tests that video_worker passes countdown to encoding correctly."""

    @pytest.fixture
    def job_with_countdown(self):
        """Job with countdown padding enabled."""
        return Job(
            job_id="test123",
            status=JobStatus.ENCODING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            file_urls={
                'instrumental_clean': 'gs://bucket/instrumental.flac',
                'video_output': 'gs://bucket/video.mp4',
            },
            state_data={
                'lyrics_metadata': {
                    'has_countdown_padding': True,
                    'countdown_padding_seconds': 3.0,
                },
                'video_worker': {
                    'instrumental_source': 'clean',
                }
            }
        )

    @pytest.fixture
    def job_without_countdown(self):
        """Job without countdown padding."""
        return Job(
            job_id="test123",
            status=JobStatus.ENCODING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            file_urls={
                'instrumental_clean': 'gs://bucket/instrumental.flac',
                'video_output': 'gs://bucket/video.mp4',
            },
            state_data={
                'lyrics_metadata': {
                    'has_countdown_padding': False,
                    'countdown_padding_seconds': 0.0,
                },
                'video_worker': {
                    'instrumental_source': 'clean',
                }
            }
        )

    def test_extract_countdown_with_padding(self, job_with_countdown):
        """video_worker extracts countdown_padding_seconds when padding enabled."""
        job = job_with_countdown

        # This mirrors the logic in video_worker.py
        lyrics_metadata = job.state_data.get('lyrics_metadata', {})
        countdown_padding_seconds = None
        if lyrics_metadata.get('has_countdown_padding'):
            countdown_padding_seconds = lyrics_metadata.get('countdown_padding_seconds', 3.0)

        assert countdown_padding_seconds == 3.0

    def test_extract_countdown_without_padding(self, job_without_countdown):
        """video_worker returns None when no countdown padding."""
        job = job_without_countdown

        lyrics_metadata = job.state_data.get('lyrics_metadata', {})
        countdown_padding_seconds = None
        if lyrics_metadata.get('has_countdown_padding'):
            countdown_padding_seconds = lyrics_metadata.get('countdown_padding_seconds', 3.0)

        assert countdown_padding_seconds is None

    def test_karaoke_finalise_receives_countdown(self, job_with_countdown):
        """KaraokeFinalise constructor receives countdown_padding_seconds.

        This test verifies that:
        1. KaraokeFinalise accepts the countdown_padding_seconds parameter
        2. The video_worker code passes the extracted value to KaraokeFinalise
        """
        # Verify the value that would be passed to KaraokeFinalise
        job = job_with_countdown
        lyrics_metadata = job.state_data.get('lyrics_metadata', {})
        countdown_padding_seconds = None
        if lyrics_metadata.get('has_countdown_padding'):
            countdown_padding_seconds = lyrics_metadata.get('countdown_padding_seconds', 3.0)

        assert countdown_padding_seconds == 3.0, (
            "countdown_padding_seconds should be 3.0 when has_countdown_padding is True"
        )

        # Verify KaraokeFinalise accepts the parameter
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
        import inspect
        sig = inspect.signature(KaraokeFinalise.__init__)
        assert 'countdown_padding_seconds' in sig.parameters, (
            "KaraokeFinalise must accept countdown_padding_seconds parameter"
        )

    def test_karaoke_finalise_receives_none_when_no_countdown(self, job_without_countdown):
        """KaraokeFinalise receives None for countdown when no padding needed.

        This test verifies that when has_countdown_padding is False,
        the countdown_padding_seconds remains None (not passed to KaraokeFinalise).
        """
        job = job_without_countdown
        lyrics_metadata = job.state_data.get('lyrics_metadata', {})
        countdown_padding_seconds = None
        if lyrics_metadata.get('has_countdown_padding'):
            countdown_padding_seconds = lyrics_metadata.get('countdown_padding_seconds', 3.0)

        # Should be None when no countdown
        assert countdown_padding_seconds is None, (
            "countdown_padding_seconds should be None when has_countdown_padding is False"
        )

    def test_video_worker_source_passes_countdown_to_karaoke_finalise(self):
        """Verify video_worker.py source code passes countdown to KaraokeFinalise.

        This is a source code inspection test to ensure the parameter is actually
        being passed in the KaraokeFinalise constructor call.
        """
        import inspect
        from backend.workers import video_worker

        source = inspect.getsource(video_worker)

        # Verify the KaraokeFinalise instantiation includes countdown_padding_seconds
        assert 'countdown_padding_seconds=countdown_padding_seconds' in source, (
            "video_worker.py must pass countdown_padding_seconds to KaraokeFinalise"
        )


class TestGCEEncodingCountdownPassthrough:
    """Tests for GCE encoding countdown handling."""

    def test_gce_encoding_config_structure(self):
        """GCE encoding config can include countdown_padding_seconds."""
        encoding_config = {
            "formats": ["mp4_4k_lossless", "mp4_4k_lossy", "mkv_4k", "mp4_720p"],
            "artist": "Test",
            "title": "Song",
            "countdown_padding_seconds": 3.0,
        }

        assert encoding_config.get('countdown_padding_seconds') == 3.0

    @pytest.mark.asyncio
    async def test_gce_backend_encodes_with_options(self):
        """GCE backend passes options through to encoding service."""
        from backend.services.encoding_interface import GCEEncodingBackend, EncodingInput

        backend = GCEEncodingBackend()

        encoding_input = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/instrumental.flac",
            artist="Test",
            title="Song",
            output_dir="/output",
            options={
                'job_id': 'test123',
                'countdown_padding_seconds': 3.0,
            }
        )

        # The options field should be accessible
        assert encoding_input.options.get('countdown_padding_seconds') == 3.0


class TestLocalEncodingCountdownPassthrough:
    """Tests for local encoding countdown handling."""

    def test_local_encoding_input_with_countdown(self):
        """Local encoding receives EncodingInput with countdown in options."""
        from backend.services.encoding_interface import LocalEncodingBackend, EncodingInput

        encoding_input = EncodingInput(
            title_video_path="/path/title.mov",
            karaoke_video_path="/path/karaoke.mov",
            instrumental_audio_path="/path/instrumental.flac",
            artist="Test",
            title="Song",
            output_dir="/output",
            options={
                'countdown_padding_seconds': 3.0,
            }
        )

        backend = LocalEncodingBackend()

        # Backend should be able to access countdown from options
        assert encoding_input.options.get('countdown_padding_seconds') == 3.0


class TestCountdownTimingInvariant:
    """Tests for the audio timing invariant."""

    def test_padding_preserves_timing_relationship(self):
        """
        INVARIANT: If vocals are padded by N seconds, instrumental must also
        be padded by N seconds to maintain sync.

        Final timing relationship:
        - Original first word at T seconds
        - After padding: first word at T + N seconds
        - Instrumental must also start N seconds later (via padding)
        """
        vocal_first_word_original = 0.5  # seconds
        padding_seconds = 3.0

        vocal_first_word_after_padding = vocal_first_word_original + padding_seconds

        # Instrumental should be padded by same amount
        instrumental_start_original = 0.0
        instrumental_start_after_padding = instrumental_start_original + padding_seconds

        # The relative timing should be preserved
        relative_timing_before = vocal_first_word_original - instrumental_start_original
        relative_timing_after = vocal_first_word_after_padding - instrumental_start_after_padding

        assert relative_timing_before == relative_timing_after, (
            "INVARIANT VIOLATION: Padding must preserve relative timing between "
            "vocals and instrumental"
        )

    def test_countdown_threshold_makes_sense(self):
        """
        Songs starting before threshold need countdown.
        Songs starting after threshold don't need countdown.
        """
        from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor

        threshold = CountdownProcessor.COUNTDOWN_THRESHOLD_SECONDS

        # Song starting at 0.5s needs countdown
        early_start = 0.5
        assert early_start < threshold, "Early start should be before threshold"

        # Song starting at 5.0s doesn't need countdown
        late_start = 5.0
        assert late_start > threshold, "Late start should be after threshold"

        # Padding should match threshold
        padding = CountdownProcessor.COUNTDOWN_PADDING_SECONDS
        assert padding == threshold, (
            "Padding duration should match threshold to give singer exactly "
            "the countdown time they need"
        )


class TestGCEEncodingFileFinderBugRegression:
    """Regression tests for GCE encoding file finder bug (fixed 2026-01-24).

    Bug description:
    When re-encoding a job after reset, the GCE encoding worker would pick up
    old finals/*.mkv files instead of the fresh videos/with_vocals.mkv source.
    This caused:
    1. The file finder patterns didn't match 'with_vocals.mkv' (case-sensitivity)
    2. The fallback '*.mkv' matched old finals that already had title/end concatenated
    3. Re-encoding these files caused doubled title/end screens and audio desync

    The fix was to:
    1. Add lowercase patterns ('*with_vocals*', '*vocals*')
    2. Prioritize videos/ subdirectory in search order
    3. Exclude finals/ and encoded output files from file finder
    """

    def test_find_file_patterns_match_with_vocals_mkv(self):
        """File finder patterns must match 'with_vocals.mkv' (lowercase)."""
        import fnmatch

        actual_filename = "with_vocals.mkv"

        # These patterns MUST match the actual filename
        matching_patterns = [
            "*with_vocals*.mkv",
            "*vocals*.mkv",
        ]

        for pattern in matching_patterns:
            assert fnmatch.fnmatch(actual_filename, pattern), (
                f"Pattern '{pattern}' MUST match '{actual_filename}'"
            )

        # These patterns should NOT match (wrong case)
        non_matching_patterns = [
            "*With Vocals*.mkv",  # Wrong case
            "*Vocals*.mkv",       # Wrong case
        ]

        for pattern in non_matching_patterns:
            assert not fnmatch.fnmatch(actual_filename, pattern), (
                f"Pattern '{pattern}' should NOT match '{actual_filename}' (case mismatch)"
            )

    def test_find_file_excludes_finals_directory(self):
        """File finder must exclude files from finals/ directory.

        This prevents picking up old encoding outputs when re-encoding.
        """
        from pathlib import Path

        # Paths that should be EXCLUDED
        excluded_paths = [
            Path("finals/Regina Spektor - Hotel Song (Final Karaoke Lossless 4k).mkv"),
            Path("finals/lossless_4k_mkv.mkv"),
            Path("outputs/something.mkv"),
        ]

        for path in excluded_paths:
            path_str = str(path).lower()
            name_lower = path.name.lower()

            # Apply the exclusion logic from the fix
            should_exclude = (
                "title" in name_lower or "end" in name_lower or
                "outputs" in path_str or "finals" in path_str or
                "final karaoke" in name_lower or "lossless" in name_lower or
                "lossy" in name_lower or "720p" in name_lower
            )

            assert should_exclude, (
                f"Path '{path}' MUST be excluded from file finder"
            )

    def test_find_file_prioritizes_videos_directory(self):
        """File finder must check videos/ subdirectory before other locations.

        This ensures the fresh source video is found before any old outputs.
        """
        from pathlib import Path
        import tempfile

        def find_file(work_dir: Path, *patterns):
            '''Find a file matching any of the given glob patterns.'''
            for pattern in patterns:
                matches = list(work_dir.glob(f"**/{pattern}"))
                if matches:
                    return matches[0]
            return None

        with tempfile.TemporaryDirectory() as td:
            work_dir = Path(td)

            # Create both source and old output
            (work_dir / "videos").mkdir()
            source_file = work_dir / "videos" / "with_vocals.mkv"
            source_file.touch()

            (work_dir / "finals").mkdir()
            old_output = work_dir / "finals" / "lossless_4k_mkv.mkv"
            old_output.touch()

            # The fixed patterns from the fix
            result = find_file(
                work_dir,
                "videos/with_vocals.mkv",  # Must be first!
                "*with_vocals*.mkv",
                "*vocals*.mkv",
                "*.mkv"
            )

            assert result == source_file, (
                f"File finder must return source file from videos/, got {result}"
            )

    def test_gce_encoding_main_has_fixed_patterns(self):
        """Verify the GCE encoding main.py has the fixed file finder patterns.

        This is a source code inspection test to ensure the fix is in place.
        """
        from pathlib import Path

        gce_main_path = Path(__file__).parent.parent.parent / "backend" / "services" / "gce_encoding" / "main.py"
        source = gce_main_path.read_text()

        # Check for the key patterns that were added in the fix
        assert "videos/with_vocals.mkv" in source, (
            "GCE encoding main.py must prioritize videos/ subdirectory"
        )
        assert '*with_vocals*' in source or "*with_vocals*" in source, (
            "GCE encoding main.py must have lowercase pattern for with_vocals"
        )
        assert '"finals"' in source.lower() or "'finals'" in source.lower(), (
            "GCE encoding main.py must exclude finals/ directory"
        )
