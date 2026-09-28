"""Quick-version renderer — a scrolling-lyrics 480p karaoke video in seconds.

Ported from kjbox ``fastgen/fastgen.py`` (the local POC Andrew uses at shows).
Given an instrumental (+ optional vocals guide), artist and title, it:

1. fetches lyrics from LRCLIB (line-synced preferred, plain as fallback);
2. lays the lines out as one tall PIL-rendered PNG;
3. scrolls that PNG upward with an ffmpeg ``overlay`` whose y expression is a
   piecewise-linear "sum of ramps" anchored so each synced line reaches the
   reading row at its sung time (constant crawl when there is no timing);
4. muxes it with the instrumental (and the vocals quietly mixed back in as a
   singer guide) in a single ffmpeg pass.

Deliberately primitive: no word-level highlighting and no review — it exists
so a kjbox singer can sing a song minutes after asking, while the full gen job
keeps working toward the proper NOMAD version.

PIL rather than ffmpeg ``drawtext``: ffmpeg 8's always-on harfbuzz shaping
renders a ``.notdef`` box for every newline in a multi-line textfile.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "nomadkaraoke-quick-version/1.0 (https://nomadkaraoke.com)"

_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "karaoke_gen", "resources", "Montserrat-Bold.ttf",
)

# (text, anchor_seconds | None). Only the first visual row of a lyric line
# carries its anchor.
TimedLine = Tuple[str, Optional[float]]

_LRC_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


@dataclass
class Lyrics:
    kind: str  # "synced" | "plain"
    plain: str
    timed: Optional[List[Tuple[float, str]]] = None


@dataclass
class RenderSettings:
    height: int = 480
    fps: int = 24
    wrap: int = 34
    reading_frac: float = 0.42
    vocals_level: float = 0.3
    gap_threshold: float = 5.0
    gap_lines: int = 1
    font_path: str = _FONT_PATH


@dataclass
class RenderResult:
    output_path: str
    lyrics_tier: str  # "synced" | "constant" | "none"
    line_count: int
    duration_seconds: float
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Lyrics
# --------------------------------------------------------------------------- #
def _strip_lrc_timestamps(synced: str) -> str:
    return "\n".join(_LRC_RE.sub("", raw).strip() for raw in synced.splitlines()).strip()


def parse_synced(synced: str) -> List[Tuple[float, str]]:
    """Parse ``[mm:ss.xx] line`` LRC into sorted (start_seconds, text) pairs.

    A line may carry several timestamps (``[00:10.00][01:10.00] chorus``); each
    becomes its own entry.
    """
    out: List[Tuple[float, str]] = []
    for raw in synced.splitlines():
        stamps = _LRC_RE.findall(raw)
        if not stamps:
            continue
        text = _LRC_RE.sub("", raw).strip()
        for mins, secs in stamps:
            out.append((int(mins) * 60 + float(secs), text))
    out.sort(key=lambda p: p[0])
    return out


def lyrics_from_lrclib_payload(data: dict) -> Optional[Lyrics]:
    synced = data.get("syncedLyrics")
    if synced and synced.strip():
        timed = parse_synced(synced)
        if timed:
            return Lyrics("synced", _strip_lrc_timestamps(synced), timed)
    plain = data.get("plainLyrics")
    if plain and plain.strip():
        return Lyrics("plain", plain)
    return None


def fetch_lrclib_lyrics(
    artist: str, title: str, duration: Optional[float], timeout: float = 10.0
) -> Optional[Lyrics]:
    """Exact ``/get`` (with duration when known), then ``/search`` preferring synced."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    params = {"artist_name": artist, "track_name": title}
    if duration:
        params["duration"] = int(round(duration))
    try:
        r = session.get(f"{LRCLIB_BASE}/get", params=params, timeout=timeout)
        if r.status_code == 200:
            got = lyrics_from_lrclib_payload(r.json())
            if got and got.kind == "synced":
                return got
            exact_plain = got
        else:
            exact_plain = None
    except (requests.RequestException, ValueError) as exc:
        logger.warning(f"LRCLIB get failed: {exc}")
        exact_plain = None

    try:
        r = session.get(f"{LRCLIB_BASE}/search", params={"q": f"{artist} {title}"}, timeout=timeout)
        if r.status_code == 200:
            hits = r.json() or []
            for hit in sorted(hits, key=lambda h: 0 if h.get("syncedLyrics") else 1):
                got = lyrics_from_lrclib_payload(hit)
                if got and got.kind == "synced":
                    return got
                if got and exact_plain is None:
                    exact_plain = got
    except (requests.RequestException, ValueError) as exc:
        logger.warning(f"LRCLIB search failed: {exc}")

    return exact_plain


def resolve_timed_lines(lyrics: Optional[Lyrics]) -> Tuple[List[TimedLine], str]:
    """Lyrics → (line, anchor|None) pairs + tier label (synced / constant / none)."""
    if lyrics is None:
        return [], "none"
    if lyrics.kind == "synced" and lyrics.timed:
        return [(text, t) for t, text in lyrics.timed if text.strip()], "synced"
    return [(raw.strip(), None) for raw in lyrics.plain.splitlines()], "constant"


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def insert_instrumental_gaps(timed_lines: List[TimedLine], threshold: float, gap_lines: int = 1) -> List[TimedLine]:
    """Blank spacer rows where consecutive anchored lines are > threshold apart,
    so the crawl visibly opens up during an instrumental break."""
    if threshold <= 0:
        return list(timed_lines)
    out: List[TimedLine] = []
    last_t: Optional[float] = None
    for text, anchor in timed_lines:
        if anchor is not None:
            if last_t is not None and anchor - last_t > threshold:
                out.extend([("", None)] * gap_lines)
            last_t = anchor
        out.append((text, anchor))
    return out


def build_visual_lines(artist: str, title: str, timed_lines: List[TimedLine], wrap: int) -> List[TimedLine]:
    header: List[TimedLine] = [("", None), ("", None), (artist.upper(), None), (title.upper(), None),
                               ("", None), ("", None)]
    body: List[TimedLine] = []
    for text, anchor in timed_lines:
        text = text.strip()
        if not text:
            body.append(("", None))
            continue
        for i, row in enumerate(textwrap.wrap(text, width=wrap) or [""]):
            body.append((row, anchor if i == 0 else None))
    return header + body + [("", None)] * 3


def render_crawl_png(
    lines: List[TimedLine], width: int, fontsize: int, font_path: str, workdir: str
) -> Tuple[str, int, List[float]]:
    """Rasterise the whole crawl to one tall PNG → (path, height, row centres)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_path, fontsize)
    ascent, descent = font.getmetrics()
    line_advance = ascent + descent + round(fontsize * 0.35)
    stroke = max(2, fontsize // 16)
    pad = fontsize

    height = pad * 2 + line_advance * len(lines)
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    centers: List[float] = []
    y = pad
    for text, _anchor in lines:
        if text:
            line_w = draw.textlength(text, font=font)
            draw.text(((width - line_w) / 2, y), text, font=font, fill=(255, 232, 31),
                      stroke_width=stroke, stroke_fill=(0, 0, 0))
        centers.append(y + line_advance / 2)
        y += line_advance

    png_path = os.path.join(workdir, "crawl.png")
    img.save(png_path)
    return png_path, height, centers


def build_scroll_y_expr(
    lines: List[TimedLine], centers: List[float], duration: float,
    frame_h: int, img_h: int, reading_frac: float,
) -> Tuple[str, bool]:
    """ffmpeg ``overlay`` y expression → (expr, is_time_anchored).

    y is the PNG's top edge relative to the frame top; each anchored line's
    centre ``c_i`` should sit on the reading row ``R`` at ``t_i`` (y = R - c_i).
    Piecewise-linear between anchors, written as a flat sum of ramps
    ``Y0 + M0*(t-T0) + Σ (Mk-M[k-1])*max(0,t-Tk)`` with commas escaped for the
    filtergraph parser.
    """
    reading_row = reading_frac * frame_h
    pts = [(0.0, float(frame_h))]
    for (_text, anchor), c in zip(lines, centers):
        if anchor is not None and 0.0 < anchor < duration:
            pts.append((float(anchor), reading_row - c))

    timed = len(pts) > 1
    if timed:
        pts.append((duration, pts[-1][1]))
    else:
        pts.append((duration, -float(img_h)))

    clean = [pts[0]]
    for t, y in pts[1:]:
        if t > clean[-1][0] + 1e-3:
            clean.append((t, y))
    pts = clean
    if len(pts) < 2:  # degenerate (zero-length audio) — park the crawl off-screen
        return f"{float(frame_h):.2f}", False

    slopes = [(pts[i + 1][1] - pts[i][1]) / (pts[i + 1][0] - pts[i][0]) for i in range(len(pts) - 1)]
    terms = [f"{pts[0][1]:.2f}", f"({slopes[0]:.5f})*(t-{pts[0][0]:.3f})"]
    for k in range(1, len(slopes)):
        dm = slopes[k] - slopes[k - 1]
        if abs(dm) < 1e-6:
            continue
        terms.append(f"({dm:.5f})*max(0\\,t-{pts[k][0]:.3f})")
    return "+".join(terms), timed


def build_ffmpeg_cmd(
    instrumental: str, vocals: Optional[str], vocals_level: float,
    crawl_png: str, y_expr: str, out_path: str, width: int, height: int, fps: int,
    duration: float,
) -> List[str]:
    """Every input and the output are explicitly bounded to ``duration``.

    ``-shortest`` alone is NOT enough: ffmpeg 4.4 (the GPU image's Ubuntu 22.04
    package) never ends when an ``amix`` output is paired with the infinite
    lavfi colour + looped PNG video — a 20 s song rendered 540 s+ of video until
    the timeout (prod job 405eed71, 2026-09-28). ffmpeg 8 happens to stop."""
    dur = f"{max(duration, 0.1):.3f}"
    overlay = f"[0:v][1:v]overlay=x=(W-w)/2:y={y_expr}[v]"
    inputs = [
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}:d={dur}",
        "-loop", "1", "-framerate", str(fps), "-t", dur, "-i", crawl_png,
        "-i", instrumental,
    ]
    if vocals and vocals_level > 0:
        # Faint lead vocal as a singer guide (there is no word highlighting).
        inputs += ["-i", vocals]
        audio = (f"[2:a]volume=1[gi];[3:a]volume={vocals_level:.3f}[gv];"
                 f"[gi][gv]amix=inputs=2:normalize=0:duration=first[a]")
        filt, amap = overlay + ";" + audio, ["-map", "[a]"]
    else:
        filt, amap = overlay, ["-map", "2:a"]
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", filt,
        "-map", "[v]", *amap,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-t", dur,
        "-shortest",
        out_path,
    ]


def probe_duration(audio_path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", audio_path],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return float(out.stdout.strip())


def render_quick_video(
    *,
    instrumental_path: str,
    vocals_path: Optional[str],
    artist: str,
    title: str,
    out_path: str,
    workdir: str,
    lyrics: Optional[Lyrics] = None,
    fetch_lyrics: bool = True,
    settings: Optional[RenderSettings] = None,
    ffmpeg_timeout: float = 600.0,
) -> RenderResult:
    """Render the quick video. ``lyrics`` overrides the LRCLIB fetch."""
    s = settings or RenderSettings()
    duration = probe_duration(instrumental_path)

    if lyrics is None and fetch_lyrics:
        lyrics = fetch_lrclib_lyrics(artist, title, duration)

    timed_lines, tier = resolve_timed_lines(lyrics)
    if tier == "synced":
        timed_lines = insert_instrumental_gaps(timed_lines, s.gap_threshold, s.gap_lines)
    if tier == "none":
        timed_lines = [("(lyrics unavailable)", None)]

    width = round(s.height * 16 / 9)
    width += width % 2
    fontsize = max(18, round(s.height * 0.07))
    lines = build_visual_lines(artist, title, timed_lines, s.wrap)
    crawl_png, img_h, centers = render_crawl_png(lines, width, fontsize, s.font_path, workdir)
    y_expr, _ = build_scroll_y_expr(lines, centers, duration, s.height, img_h, s.reading_frac)

    cmd = build_ffmpeg_cmd(instrumental_path, vocals_path, s.vocals_level, crawl_png, y_expr,
                           out_path, width, s.height, s.fps, duration)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=ffmpeg_timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-2000:]}")

    return RenderResult(
        output_path=out_path,
        lyrics_tier=tier,
        line_count=sum(1 for t, _ in timed_lines if t.strip()),
        duration_seconds=duration,
    )
