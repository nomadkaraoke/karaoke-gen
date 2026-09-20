"""Tests for WaveformGenerator.generate_peaks (review Waveforms-mode envelope)."""

import math
import tempfile

import numpy as np
import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from karaoke_gen.instrumental_review.waveform import WaveformGenerator


@pytest.fixture(scope="module")
def stereo_wav(tmp_path_factory):
    """3s stereo: -6dB 440Hz left, -20dB 880Hz right."""
    left = Sine(440).to_audio_segment(duration=3000).apply_gain(-6)
    right = Sine(880).to_audio_segment(duration=3000).apply_gain(-20)
    seg = AudioSegment.from_mono_audiosegments(left, right)
    path = tmp_path_factory.mktemp("audio") / "stereo.wav"
    seg.export(str(path), format="wav")
    return str(path)


class TestGeneratePeaks:
    def test_bucket_count_and_duration(self, stereo_wav):
        peaks, duration = WaveformGenerator().generate_peaks(stereo_wav, peaks_per_second=400)
        assert duration == pytest.approx(3.0, abs=0.05)
        assert len(peaks) == math.ceil(duration * 400)

    def test_peaks_are_max_abs_across_channels_normalized(self, stereo_wav):
        peaks, _ = WaveformGenerator().generate_peaks(stereo_wav, peaks_per_second=400)
        # The louder channel (-6 dB sine) dominates: peak amplitude ~10^(-6/20)=0.501
        assert float(peaks.max()) == pytest.approx(0.501, abs=0.01)
        assert float(peaks.min()) >= 0.0
        assert peaks.dtype == np.float32

    def test_matches_naive_reference(self, stereo_wav):
        pps = 400
        peaks, duration = WaveformGenerator().generate_peaks(stereo_wav, peaks_per_second=pps)

        audio = AudioSegment.from_file(stereo_wav)
        s = np.abs(np.asarray(audio.get_array_of_samples(), dtype=np.float32)) / audio.max_possible_amplitude
        frames = len(s) // audio.channels
        s = s[: frames * audio.channels].reshape(frames, audio.channels).max(axis=1)
        bucket_count = math.ceil(duration * pps)
        spb = len(s) / bucket_count
        ref = np.array(
            [
                s[math.floor(i * spb): (len(s) if i == bucket_count - 1 else max(math.floor((i + 1) * spb), math.floor(i * spb) + 1))].max()
                for i in range(bucket_count)
            ],
            dtype=np.float32,
        )
        assert np.abs(ref - peaks).max() == 0.0

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            WaveformGenerator().generate_peaks("/nonexistent.wav")
