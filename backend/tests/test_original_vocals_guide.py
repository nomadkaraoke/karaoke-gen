"""Unit tests for the padded original-vocals guide builder."""
import os
import shutil
import subprocess

import pytest

from backend.services.original_vocals_guide import (
    build_original_vocals_guide,
    probe_duration,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


@pytest.fixture
def tone(tmp_path):
    """A 4-second 440Hz tone standing in for the mixed-vocals stem."""
    src = tmp_path / "vocals.flac"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=4", "-c:a", "flac", str(src)],
        check=True,
    )
    return str(src)


def _first_sound_onset(path):
    """Seconds until audio first exceeds the silence threshold (leading-silence length)."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af",
         "silencedetect=noise=-50dB:d=0.1", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    # A guide with N seconds of leading silence reports "silence_end: N".
    for line in out.splitlines():
        if "silence_end:" in line:
            return float(line.split("silence_end:")[1].split("|")[0].strip())
    return 0.0


def test_prepends_intro_silence_and_reports_expected_duration(tmp_path, tone):
    dest = str(tmp_path / "NOMAD-1500 - A - B.flac")
    out = build_original_vocals_guide(tone, intro_seconds=5, dest_path=dest)

    assert out == dest
    assert os.path.isfile(dest)
    # silence[5] + 4s tone ≈ 9s total, with the tone starting ~5s in.
    total = probe_duration(dest)
    assert total == pytest.approx(9.0, abs=0.3)
    assert _first_sound_onset(dest) == pytest.approx(5.0, abs=0.3)


def test_respects_non_default_intro(tmp_path, tone):
    dest = str(tmp_path / "guide.flac")
    build_original_vocals_guide(tone, intro_seconds=10, dest_path=dest)
    # Proves we honour the job's actual intro, not a hardcoded 5s.
    assert _first_sound_onset(dest) == pytest.approx(10.0, abs=0.3)


def test_caps_to_master_duration(tmp_path, tone):
    dest = str(tmp_path / "guide.flac")
    # silence[5] + 4s tone would be 9s; cap to 7s.
    build_original_vocals_guide(tone, intro_seconds=5, dest_path=dest, master_duration=7.0)
    assert probe_duration(dest) == pytest.approx(7.0, abs=0.3)


def test_output_is_valid_flac(tmp_path, tone):
    dest = str(tmp_path / "guide.flac")
    build_original_vocals_guide(tone, intro_seconds=1, dest_path=dest)
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of",
         "default=noprint_wrappers=1:nokey=1", dest],
        capture_output=True, text=True,
    ).stdout.strip()
    assert codec == "flac"


def test_no_part_file_left_behind(tmp_path, tone):
    dest = str(tmp_path / "guide.flac")
    build_original_vocals_guide(tone, intro_seconds=1, dest_path=dest)
    assert not os.path.exists(dest + ".part")


def test_missing_input_returns_none(tmp_path):
    out = build_original_vocals_guide(
        str(tmp_path / "nope.flac"), intro_seconds=5, dest_path=str(tmp_path / "g.flac")
    )
    assert out is None
    assert not os.path.exists(tmp_path / "g.flac")


def test_negative_intro_returns_none(tmp_path, tone):
    assert build_original_vocals_guide(tone, intro_seconds=-1, dest_path=str(tmp_path / "g.flac")) is None


def test_ffmpeg_failure_is_non_fatal(tmp_path, tone):
    # A bogus ffmpeg binary must not raise; just returns None.
    out = build_original_vocals_guide(
        tone, intro_seconds=5, dest_path=str(tmp_path / "g.flac"),
        ffmpeg_path="/nonexistent/ffmpeg",
    )
    assert out is None
    assert not os.path.exists(str(tmp_path / "g.flac") + ".part")


def test_probe_duration_missing_file_returns_none(tmp_path):
    assert probe_duration(str(tmp_path / "nope.flac")) is None
