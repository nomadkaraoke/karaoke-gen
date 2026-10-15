"""Tests for recovering expired job inputs before a re-render (backend/services/input_recovery.py)."""
import os
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.services.input_recovery import (
    RECOVERED_MIX_NAME,
    InputMissingError,
    recover_missing_inputs,
)

JOB_ID = "5fca18dd"
MIX = f"uploads/{JOB_ID}/audio/S1106-1 Ruby Murray - Softly Softly Guide.mp3"
INST = f"uploads/{JOB_ID}/audio/existing_instrumental.mp3"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


class FakeStorage:
    """Blob store backed by a dict of path -> local file (or None for a placeholder)."""

    def __init__(self, blobs=None):
        self.blobs = dict(blobs or {})
        self.uploaded = {}

    def file_exists(self, path):
        return path in self.blobs

    def download_file(self, src, dest):
        shutil.copy(self.blobs[src], dest)
        return dest

    def upload_file(self, local, dest):
        with open(local, "rb") as fh:
            self.uploaded[dest] = fh.read()
        self.blobs[dest] = None
        return dest


def make_job(**overrides):
    job = SimpleNamespace(
        job_id=JOB_ID,
        input_media_gcs_path=MIX,
        existing_instrumental_gcs_path=INST,
        file_urls={"finals": {"with_vocals_mp4": f"jobs/{JOB_ID}/finals/with_vocals_mp4.mp4"}},
        state_data={"lyrics_metadata": {"has_countdown_padding": False, "countdown_padding_seconds": 0.0}},
        processing_metadata={},
    )
    for k, v in overrides.items():
        setattr(job, k, v)
    return job


def make_video(path, seconds, tone_start=0.0):
    """A tiny mp4 whose audio is silence for `tone_start`s then a tone."""
    filt = f"aevalsrc='if(gte(t,{tone_start}),0.5*sin(2*PI*440*t),0)':s=48000:d={seconds}"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x36:d=" + str(seconds),
         "-f", "lavfi", "-i", filt, "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def test_nothing_missing_is_a_noop():
    storage = FakeStorage({MIX: None, INST: None})
    jm = MagicMock()
    assert recover_missing_inputs(make_job(), storage, jm) == {}
    jm.update_job.assert_not_called()


def test_prefers_persisted_upload_copy():
    persisted = f"jobs/{JOB_ID}/input/audio/S1106-1 Ruby Murray - Softly Softly Guide.mp3"
    storage = FakeStorage({persisted: None, INST: None})
    jm = MagicMock()
    job = make_job()
    updates = recover_missing_inputs(job, storage, jm)
    assert updates == {"input_media_gcs_path": persisted}
    assert job.input_media_gcs_path == persisted
    call = jm.update_job.call_args[0][1]
    assert call["state_data.input_recovery"]["input_media_from"] == "persisted_upload"


@needs_ffmpeg
def test_rebuilds_mix_from_with_vocals_render(tmp_path):
    video = tmp_path / "wv.mp4"
    make_video(video, 4.0)
    wv = f"jobs/{JOB_ID}/finals/with_vocals_mp4.mp4"
    storage = FakeStorage({wv: str(video), INST: None})
    job = make_job()
    updates = recover_missing_inputs(job, storage, MagicMock())

    recovered = f"jobs/{JOB_ID}/input/{RECOVERED_MIX_NAME}"
    assert updates["input_media_gcs_path"] == recovered
    out = tmp_path / "out.flac"
    out.write_bytes(storage.uploaded[recovered])
    assert duration(out) == pytest.approx(4.0, abs=0.1)


@needs_ffmpeg
def test_trims_countdown_padding_from_rebuilt_mix(tmp_path):
    video = tmp_path / "wv.mp4"
    make_video(video, 6.0, tone_start=3.0)  # 3s countdown silence baked into the render
    wv = f"jobs/{JOB_ID}/finals/with_vocals_mp4.mp4"
    storage = FakeStorage({wv: str(video), INST: None})
    job = make_job(state_data={"lyrics_metadata": {"has_countdown_padding": True, "countdown_padding_seconds": 3.0}})
    recover_missing_inputs(job, storage, MagicMock())

    out = tmp_path / "out.flac"
    out.write_bytes(storage.uploaded[f"jobs/{JOB_ID}/input/{RECOVERED_MIX_NAME}"])
    assert duration(out) == pytest.approx(3.0, abs=0.1)


@pytest.mark.parametrize(
    "job_fields",
    [
        {"state_data": {"lyrics_metadata": {"has_countdown_padding": True, "countdown_padding_seconds": 3.0}}},
        {"processing_metadata": {"rendering": {"countdown_padding_added": True, "countdown_padding_seconds": 3.0}}},
        {"state_data": {"countdown_padding_added": True, "countdown_padding_seconds": 3.0}},
    ],
)
def test_countdown_padding_read_from_every_known_location(job_fields):
    from backend.services.input_recovery import _countdown_padding_seconds

    assert _countdown_padding_seconds(make_job(**job_fields)) == 3.0
    assert _countdown_padding_seconds(make_job()) == 0.0


def test_reuses_a_previous_recovery_without_reextracting():
    recovered = f"jobs/{JOB_ID}/input/{RECOVERED_MIX_NAME}"
    storage = FakeStorage({recovered: None, INST: None})
    updates = recover_missing_inputs(make_job(), storage, MagicMock())
    assert updates["input_media_gcs_path"] == recovered
    assert storage.uploaded == {}


def test_raises_actionable_error_when_nothing_survives():
    storage = FakeStorage({INST: None})
    with pytest.raises(InputMissingError, match="no longer stored"):
        recover_missing_inputs(make_job(), storage, MagicMock())


@pytest.mark.parametrize(
    "survivor",
    [
        f"jobs/{JOB_ID}/input/audio/existing_instrumental.mp3",
        f"jobs/{JOB_ID}/custom_instrumental.mp3",
        f"jobs/{JOB_ID}/existing_instrumental.flac",
        f"jobs/{JOB_ID}/stems/custom_instrumental.wav",
    ],
)
def test_repoints_missing_instrumental_to_surviving_copy(survivor):
    storage = FakeStorage({MIX: None, survivor: None})
    job = make_job()
    updates = recover_missing_inputs(job, storage, MagicMock())
    assert updates == {"existing_instrumental_gcs_path": survivor}
    assert job.existing_instrumental_gcs_path == survivor


def test_missing_instrumental_with_no_copy_is_logged_not_raised():
    storage = FakeStorage({MIX: None})
    assert recover_missing_inputs(make_job(), storage, MagicMock()) == {}


def test_gs_uri_paths_are_handled():
    storage = FakeStorage({MIX: None, INST: None})
    job = make_job(input_media_gcs_path=f"gs://bucket/{MIX}")
    assert recover_missing_inputs(job, storage, MagicMock()) == {}
