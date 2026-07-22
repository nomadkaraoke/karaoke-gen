"""End-to-end portrait render on a tiny synthetic fixture.

Renders a real (short) portrait MP4 with ffmpeg and asserts the output dimensions,
total duration (title + body + end), and that lyrics are actually burned into the
body. Skipped when ffmpeg/ffprobe are unavailable.
"""
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from karaoke_gen.lyrics_transcriber.types import LyricsSegment, Word
from karaoke_gen.portrait import PortraitBrandConfig, PortraitLayout, render_portrait_video

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not available",
)


def _word(wid, text, start, end):
    return Word(id=wid, text=text, start_time=start, end_time=end)


def _fixture_result():
    seg1 = LyricsSegment(
        id="s1", text="Hello portrait world", start_time=0.2, end_time=1.8,
        words=[_word("w1", "Hello", 0.2, 0.7), _word("w2", "portrait", 0.8, 1.3),
               _word("w3", "world", 1.4, 1.8)],
    )
    seg2 = LyricsSegment(
        id="s2", text="Singing along tonight", start_time=2.0, end_time=3.6,
        words=[_word("w4", "Singing", 2.0, 2.5), _word("w5", "along", 2.6, 3.0),
               _word("w6", "tonight", 3.1, 3.6)],
    )
    return SimpleNamespace(corrected_segments=[seg1, seg2])


def _probe(path, entries):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", entries, "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return out


def test_render_portrait_end_to_end(tmp_path):
    audio = tmp_path / "instr.flac"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=stereo", "-t", "4", str(audio)],
        check=True,
    )

    out = tmp_path / "portrait.mp4"
    # Short intro/outro to keep the encode quick.
    styles = {"karaoke": {}, "intro": {"video_duration": 1}, "end": {"video_duration": 1}}
    render_portrait_video(
        correction_result=_fixture_result(),
        instrumental_path=str(audio),
        styles=styles,
        artist="Test Artist",
        title="Test Title",
        output_path=str(out),
        brand=PortraitBrandConfig(brand_text="NOMAD KARAOKE", footer_text="nomadkaraoke.com"),
        layout=PortraitLayout(),
    )

    assert out.exists()
    w, h = _probe(str(out), "stream=width,height")
    assert (int(w), int(h)) == (1080, 1920)

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())
    # 1s intro + 4s body + 1s outro.
    assert abs(dur - 6.0) < 0.5

    # Assert lyrics are burned in: a frame ~1s into the body must have bright text
    # pixels the empty title card does not.
    frame = tmp_path / "f.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "2.0",
         "-i", str(out), "-frames:v", "1", str(frame)],
        check=True,
    )
    from PIL import Image
    img = Image.open(frame).convert("L")
    # Central lyric band should contain near-white text pixels.
    band = img.crop((0, 900, 1080, 1500))
    assert band.getextrema()[1] > 180, "expected bright lyric text in the body frame"
