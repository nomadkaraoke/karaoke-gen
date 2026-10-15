"""Tiny, real media files for the fake workers.

Everything downstream of a worker (ffprobe duration reconciliation, the review
waveform / OGG transcode, backing-vocals analysis, the local preview render,
downloads) reads real files, so the fakes write real ones — just small: mono
22.05 kHz tones and 320x180 video. Files are generated once per duration with
ffmpeg and cached in a temp dir for the life of the process.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050
VIDEO_SIZE = "320x180"

_lock = threading.Lock()
_cache: Dict[int, "MediaSet"] = {}
_root = tempfile.mkdtemp(prefix="fake_worker_media_")


@dataclass(frozen=True)
class MediaSet:
    duration: int
    input_audio: str
    instrumental_clean: str
    instrumental_with_backing: str
    vocals: str
    lead_vocals: str
    backing_vocals: str
    title_png: str
    title_jpg: str
    end_png: str
    end_jpg: str
    with_vocals_mkv: str
    final_mp4: str
    final_mkv: str
    cdg_zip: str
    txt_zip: str


def _ffmpeg(*args: str) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)


def _tone(path: str, freq: int, duration: int, volume: float = 0.3) -> None:
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={SAMPLE_RATE}:duration={duration}",
        "-af", f"volume={volume}", "-ac", "1", path,
    )


def _silence(path: str, duration: int) -> None:
    _ffmpeg(
        "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", str(duration), path,
    )


def _mix(path: str, *inputs: str) -> None:
    args = []
    for i in inputs:
        args += ["-i", i]
    _ffmpeg(*args, "-filter_complex", f"amix=inputs={len(inputs)}:normalize=0", "-ac", "1", path)


def _screen(png_path: str, jpg_path: str, label: str) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 360), (24, 16, 48))
    ImageDraw.Draw(img).text((24, 160), label, fill=(255, 122, 204))
    img.save(png_path)
    img.save(jpg_path, quality=80)


def _video(path: str, audio: str, duration: int, codec_args: list) -> None:
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=0x181030:s={VIDEO_SIZE}:d={duration}:r=10",
        "-i", audio, "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", *codec_args, path,
    )


def _zip(path: str, members: Dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _build(duration: int) -> MediaSet:
    d = os.path.join(_root, str(duration))
    os.makedirs(d, exist_ok=True)
    p = lambda name: os.path.join(d, name)  # noqa: E731

    # "Instrumental" = low tone, "lead vocals" = high tone, backing vocals = silence,
    # so the real backing-vocals analysis recommends the clean instrumental.
    _tone(p("instrumental_clean.flac"), 220, duration)
    _tone(p("lead_vocals.flac"), 660, duration, volume=0.2)
    _silence(p("backing_vocals.flac"), duration)
    _mix(p("vocals_clean.flac"), p("lead_vocals.flac"), p("backing_vocals.flac"))
    _mix(p("instrumental_with_backing.flac"), p("instrumental_clean.flac"), p("backing_vocals.flac"))
    _mix(p("input.flac"), p("instrumental_clean.flac"), p("vocals_clean.flac"))

    _screen(p("title.png"), p("title.jpg"), "Fake title screen")
    _screen(p("end.png"), p("end.jpg"), "Fake end screen")

    _video(p("with_vocals.mkv"), p("input.flac"), duration, ["-c:a", "flac"])
    _video(p("final.mp4"), p("instrumental_clean.flac"), duration, ["-c:a", "aac", "-b:a", "64k"])
    _video(p("final.mkv"), p("instrumental_clean.flac"), duration, ["-c:a", "flac"])

    _zip(p("cdg.zip"), {"karaoke.cdg": b"\0" * 2400, "karaoke.mp3": b""})
    _zip(p("txt.zip"), {"karaoke.txt": b"fake lyrics\n", "karaoke.mp3": b""})

    return MediaSet(
        duration=duration,
        input_audio=p("input.flac"),
        instrumental_clean=p("instrumental_clean.flac"),
        instrumental_with_backing=p("instrumental_with_backing.flac"),
        vocals=p("vocals_clean.flac"),
        lead_vocals=p("lead_vocals.flac"),
        backing_vocals=p("backing_vocals.flac"),
        title_png=p("title.png"),
        title_jpg=p("title.jpg"),
        end_png=p("end.png"),
        end_jpg=p("end.jpg"),
        with_vocals_mkv=p("with_vocals.mkv"),
        final_mp4=p("final.mp4"),
        final_mkv=p("final.mkv"),
        cdg_zip=p("cdg.zip"),
        txt_zip=p("txt.zip"),
    )


def get_media(duration: int) -> MediaSet:
    """Return (building once) the media set for a song of ``duration`` seconds."""
    with _lock:
        media = _cache.get(duration)
        if media is None:
            logger.info(f"fake_workers: generating {duration}s media set in {_root}")
            media = _build(duration)
            _cache[duration] = media
        return media
