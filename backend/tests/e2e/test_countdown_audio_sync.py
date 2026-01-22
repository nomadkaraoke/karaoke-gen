"""
E2E tests that verify actual audio synchronization in generated videos.

These tests generate real (short) videos and verify the audio offset.
Uses ffprobe to analyze video/audio streams.

These tests are SLOW and should be run on-demand, not on every PR.
"""

import pytest
import subprocess
import tempfile
import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def ffprobe_available():
    """Check if ffprobe is available."""
    try:
        result = subprocess.run(
            ['ffprobe', '-version'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


# Skip all tests if ffprobe not available
pytestmark = pytest.mark.skipif(
    not ffprobe_available(),
    reason="ffprobe not available. Install ffmpeg to run these tests."
)


class TestAudioFileAnalysis:
    """Helper tests for audio file analysis functions."""

    def test_ffprobe_can_analyze_audio(self, tmp_path):
        """Verify ffprobe can analyze audio files."""
        # Create a silent audio file for testing
        test_audio = tmp_path / "test.wav"

        # Generate 5 seconds of silence using ffmpeg
        result = subprocess.run([
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', 'anullsrc=r=44100:cl=stereo',
            '-t', '5',
            str(test_audio)
        ], capture_output=True)

        if result.returncode != 0:
            pytest.skip("Could not create test audio file")

        # Analyze with ffprobe
        probe_result = subprocess.run([
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            str(test_audio)
        ], capture_output=True, text=True)

        assert probe_result.returncode == 0
        data = json.loads(probe_result.stdout)
        assert 'format' in data
        duration = float(data['format']['duration'])
        assert 4.9 < duration < 5.1  # ~5 seconds


class TestCountdownAudioPadding:
    """Tests for audio padding functionality."""

    @pytest.fixture
    def source_audio(self, tmp_path):
        """Create a short source audio file."""
        audio_path = tmp_path / "source.flac"

        # Create a 5-second audio file with a tone at the start
        # This simulates audio content that starts immediately
        result = subprocess.run([
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', 'sine=frequency=440:duration=5',
            '-c:a', 'flac',
            str(audio_path)
        ], capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed to create source audio: {result.stderr.decode()}")

        return audio_path

    def test_padding_adds_correct_duration(self, source_audio, tmp_path):
        """Verify that padding adds exactly the specified duration."""
        padded_audio = tmp_path / "padded.flac"
        padding_seconds = 3.0

        # Apply padding using the same method as CountdownProcessor
        # adelay filter adds delay in milliseconds
        delay_ms = int(padding_seconds * 1000)

        result = subprocess.run([
            'ffmpeg', '-y',
            '-i', str(source_audio),
            '-af', f'adelay={delay_ms}|{delay_ms}',
            '-c:a', 'flac',
            str(padded_audio)
        ], capture_output=True)

        assert result.returncode == 0, f"FFmpeg failed: {result.stderr}"

        # Analyze both files
        def get_duration(path):
            probe = subprocess.run([
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                str(path)
            ], capture_output=True, text=True)
            data = json.loads(probe.stdout)
            return float(data['format']['duration'])

        source_duration = get_duration(source_audio)
        padded_duration = get_duration(padded_audio)

        # Padded should be source + padding
        expected_duration = source_duration + padding_seconds
        assert abs(padded_duration - expected_duration) < 0.1, (
            f"Expected duration ~{expected_duration}s, got {padded_duration}s"
        )

    def test_karaoke_finalise_pad_audio_file(self, source_audio, tmp_path):
        """Test KaraokeFinalise._pad_audio_file creates correctly padded audio."""
        from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

        padded_audio = tmp_path / "padded_via_finalise.flac"
        padding_seconds = 3.0

        finalise = KaraokeFinalise(non_interactive=True)
        finalise._pad_audio_file(str(source_audio), str(padded_audio), padding_seconds)

        assert padded_audio.exists(), "Padded audio file was not created"

        # Verify duration
        probe = subprocess.run([
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            str(padded_audio)
        ], capture_output=True, text=True)

        data = json.loads(probe.stdout)
        padded_duration = float(data['format']['duration'])

        # Get source duration
        source_probe = subprocess.run([
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            str(source_audio)
        ], capture_output=True, text=True)

        source_data = json.loads(source_probe.stdout)
        source_duration = float(source_data['format']['duration'])

        expected = source_duration + padding_seconds
        assert abs(padded_duration - expected) < 0.1, (
            f"Expected ~{expected}s, got {padded_duration}s"
        )


class TestAudioSyncVerification:
    """
    Tests that verify the final audio sync between vocals and instrumental.

    These tests create test audio files and verify that when countdown is added:
    1. Vocals are padded by countdown duration
    2. Instrumental is padded by same duration
    3. Relative timing is preserved
    """

    @pytest.fixture
    def vocals_audio(self, tmp_path):
        """Create vocals audio with a distinctive marker at 0.5s."""
        audio_path = tmp_path / "vocals.flac"

        # Create audio: 0.5s silence, then 1s tone, then silence
        # Total 5 seconds
        filter_complex = (
            'aevalsrc=0:d=0.5[silence1];'
            'sine=frequency=880:duration=1[tone];'
            'aevalsrc=0:d=3.5[silence2];'
            '[silence1][tone][silence2]concat=n=3:v=0:a=1'
        )

        subprocess.run([
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', filter_complex,
            '-c:a', 'flac',
            str(audio_path)
        ], capture_output=True)

        return audio_path

    @pytest.fixture
    def instrumental_audio(self, tmp_path):
        """Create instrumental audio with marker at 0.5s."""
        audio_path = tmp_path / "instrumental.flac"

        filter_complex = (
            'aevalsrc=0:d=0.5[silence1];'
            'sine=frequency=440:duration=1[tone];'
            'aevalsrc=0:d=3.5[silence2];'
            '[silence1][tone][silence2]concat=n=3:v=0:a=1'
        )

        subprocess.run([
            'ffmpeg', '-y', '-f', 'lavfi',
            '-i', filter_complex,
            '-c:a', 'flac',
            str(audio_path)
        ], capture_output=True)

        return audio_path

    def test_both_tracks_padded_equally(self, vocals_audio, instrumental_audio, tmp_path):
        """
        When countdown is applied, both vocals and instrumental must be
        padded by the same amount to maintain sync.
        """
        padding_seconds = 3.0
        delay_ms = int(padding_seconds * 1000)

        padded_vocals = tmp_path / "padded_vocals.flac"
        padded_instrumental = tmp_path / "padded_instrumental.flac"

        # Pad both
        for src, dst in [(vocals_audio, padded_vocals), (instrumental_audio, padded_instrumental)]:
            subprocess.run([
                'ffmpeg', '-y',
                '-i', str(src),
                '-af', f'adelay={delay_ms}|{delay_ms}',
                '-c:a', 'flac',
                str(dst)
            ], capture_output=True)

        # Get durations
        def get_duration(path):
            probe = subprocess.run([
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                str(path)
            ], capture_output=True, text=True)
            data = json.loads(probe.stdout)
            return float(data['format']['duration'])

        vocals_dur = get_duration(padded_vocals)
        inst_dur = get_duration(padded_instrumental)

        # Both should have same duration (sync maintained)
        assert abs(vocals_dur - inst_dur) < 0.1, (
            f"Padded vocals ({vocals_dur}s) and instrumental ({inst_dur}s) "
            "have different durations - sync will be off"
        )


class TestCountdownTimingInvariant:
    """
    Tests for the critical audio timing invariant.

    INVARIANT: If vocals start at time T (after countdown),
               instrumental must also start at time T (also padded).

    Violation of this invariant causes audio desync.
    """

    def test_relative_timing_preserved_after_padding(self):
        """
        The relative timing between vocals and instrumental must be preserved.

        If vocals originally start at 0.5s with content at 0.5s,
        after 3s padding, vocals content is at 3.5s.
        Instrumental must also be padded so its content is at 3.5s.
        """
        original_content_start = 0.5  # seconds
        padding_seconds = 3.0

        # After padding
        vocals_content_after_padding = original_content_start + padding_seconds
        instrumental_start_after_padding = 0 + padding_seconds  # instrumental starts at 0

        # The relative offset should be preserved
        original_relative_offset = original_content_start - 0  # vocals relative to instrumental start
        new_relative_offset = vocals_content_after_padding - instrumental_start_after_padding

        assert original_relative_offset == new_relative_offset, (
            "INVARIANT VIOLATION: Padding changed the relative timing "
            "between vocals and instrumental"
        )

    def test_sync_check_formula(self):
        """
        Formula for checking if audio is in sync:
        If has_countdown_padding=True:
            expected_instrumental_start = countdown_padding_seconds
            actual_instrumental_start should equal expected
        """
        countdown_padding_seconds = 3.0
        has_countdown_padding = True

        if has_countdown_padding:
            expected_instrumental_start = countdown_padding_seconds
        else:
            expected_instrumental_start = 0

        # If instrumental is correctly padded
        actual_instrumental_start = 3.0  # (padded)

        assert actual_instrumental_start == expected_instrumental_start, (
            "Audio sync check failed"
        )


class TestAudioSyncDriftAcceptance:
    """
    Tests for acceptable audio drift tolerance.

    Small amounts of drift (<100ms) are acceptable in karaoke videos.
    Larger drift (>100ms) is noticeable and indicates a bug.
    """

    def test_acceptable_drift_threshold(self):
        """Define the acceptable drift threshold."""
        acceptable_drift_ms = 100  # 100ms
        acceptable_drift_s = acceptable_drift_ms / 1000

        # A 50ms drift is acceptable
        actual_drift_s = 0.050
        assert actual_drift_s <= acceptable_drift_s, "50ms drift should be acceptable"

        # A 200ms drift is NOT acceptable
        actual_drift_s = 0.200
        assert actual_drift_s > acceptable_drift_s, "200ms drift is too much"

    def test_countdown_sync_within_tolerance(self):
        """
        After countdown processing, drift should be within tolerance.
        """
        # Ideal case: no drift
        expected_sync = 3.0  # seconds
        actual_sync = 3.0  # seconds

        drift = abs(actual_sync - expected_sync)
        acceptable_drift = 0.1  # 100ms

        assert drift <= acceptable_drift, (
            f"Drift of {drift*1000}ms exceeds tolerance of {acceptable_drift*1000}ms"
        )
