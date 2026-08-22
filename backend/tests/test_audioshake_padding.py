"""
Regression tests for AudioShake leading-padding handling.

Some user-provided audio (e.g. the Vocal Star bulk-batch MP3s) begins with a block of
null bytes before the first ID3/MPEG frame. ffmpeg tolerates it, but AudioShake rejects
the asset with "Error fetching or processing asset" (400 on /tasks). The AudioUploadOptimizer
must detect this and losslessly remux before upload — while leaving clean files and
container formats (.mp4/.mov, which legitimately start with null box-size bytes) untouched.
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

from karaoke_gen.lyrics_transcriber.transcribers.audioshake import AudioUploadOptimizer


def _write(tmp_path, name, head: bytes):
    p = os.path.join(tmp_path, name)
    with open(p, "wb") as f:
        f.write(head + b"\x00" * 2048)
    return p


def test_has_leading_padding_detects_null_start(tmp_path):
    opt = AudioUploadOptimizer(MagicMock())
    padded = _write(str(tmp_path), "padded.mp3", b"\x00\x00\x00\x00")
    assert opt._has_leading_padding(padded) is True


def test_has_leading_padding_false_for_id3(tmp_path):
    opt = AudioUploadOptimizer(MagicMock())
    clean = _write(str(tmp_path), "clean.mp3", b"ID3\x04")
    assert opt._has_leading_padding(clean) is False


def test_has_leading_padding_false_for_frame_sync(tmp_path):
    opt = AudioUploadOptimizer(MagicMock())
    clean = _write(str(tmp_path), "sync.mp3", b"\xff\xfb\xc0\x04")
    assert opt._has_leading_padding(clean) is False


def test_padded_mp3_is_remuxed(tmp_path):
    opt = AudioUploadOptimizer(MagicMock())
    padded = _write(str(tmp_path), "padded.mp3", b"\x00\x00\x00\x00")
    with patch.object(opt, "_remux_clean", return_value=("/tmp/clean.mp3", "/tmp/clean.mp3")) as remux:
        result = opt.prepare_for_upload(padded)
    remux.assert_called_once()
    assert result == ("/tmp/clean.mp3", "/tmp/clean.mp3")


def test_clean_mp3_uploaded_directly(tmp_path):
    opt = AudioUploadOptimizer(MagicMock())
    clean = _write(str(tmp_path), "clean.mp3", b"ID3\x04")
    with patch.object(opt, "_remux_clean") as remux:
        result = opt.prepare_for_upload(clean)
    remux.assert_not_called()
    assert result == (clean, None)


def test_container_format_not_remuxed_despite_null_start(tmp_path):
    """.mp4/.mov begin with a null box-size prefix — must NOT be treated as padding."""
    opt = AudioUploadOptimizer(MagicMock())
    mp4 = _write(str(tmp_path), "video.mp4", b"\x00\x00\x00\x20ftyp")
    with patch.object(opt, "_remux_clean") as remux:
        result = opt.prepare_for_upload(mp4)
    remux.assert_not_called()
    assert result == (mp4, None)
