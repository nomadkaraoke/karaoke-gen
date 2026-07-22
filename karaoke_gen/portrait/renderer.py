"""Render a portrait (9:16) karaoke video from corrected lyrics + an instrumental.

This is the self-contained core of the portrait-video feature. It re-renders the
lyrics for a 1080x1920 frame (re-wrapping long lines, larger relative font, centred
block) rather than transforming a finished landscape video — the design experiments
showed transforms (letterbox/crop) are unusable. It reuses the same
``SubtitlesGenerator`` engine as the landscape pipeline, so word-by-word karaoke
timing and styling are identical; only the layout changes.

Public entry point: :func:`render_portrait_video`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer
from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.portrait.background import PortraitBrandConfig, build_background
from karaoke_gen.portrait.wrap import balance_segments

PORTRAIT_WIDTH = 1080
PORTRAIT_HEIGHT = 1920


@dataclass
class PortraitLayout:
    """Tunable layout parameters for the portrait lyric render.

    Defaults were chosen from renders of real jobs: font 88 / line-height 118 keeps
    four wrapped lines large and readable in the lower-centre zone below the header.
    """

    width: int = PORTRAIT_WIDTH
    height: int = PORTRAIT_HEIGHT
    font_size: int = 88
    line_height: int = 118
    max_visible_lines: int = 4
    max_line_length: int = 19
    intro_seconds: float = 5.0
    outro_seconds: float = 5.0
    # Vertical centre of the lyric block as a fraction of frame height (below the
    # branded header, above the footer).
    block_center_frac: float = 0.60


def _ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _computed_top_padding(layout: PortraitLayout) -> int:
    """Top padding that centres the visible lyric block at ``block_center_frac``.

    ``SubtitlesGenerator`` positions the first line at
    ``top_padding + (H - total - top_padding) // 4`` (see
    ``ass.lyrics_screen.PositionCalculator``). Invert that so the block's centre
    lands where we want it.
    """
    total = layout.max_visible_lines * layout.line_height
    desired_first_top = layout.height * layout.block_center_frac - total / 2
    tp = (4.0 / 3.0) * (desired_first_top - (layout.height - total) / 4.0)
    return max(0, int(round(tp)))


def _escape_ass_path(path: str) -> str:
    """Escape a filesystem path for use inside an ffmpeg -vf ass= filter value."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def prepare_portrait_segments(
    correction_result,
    layout: PortraitLayout,
    logger: Optional[logging.Logger] = None,
) -> List[LyricsSegment]:
    """Resize + orphan-balance the corrected segments for the portrait frame."""
    resizer = SegmentResizer(max_line_length=layout.max_line_length, logger=logger)
    resized = resizer.resize_segments(correction_result.corrected_segments)
    return balance_segments(resized, layout.max_line_length, logger=logger)


def build_portrait_ass(
    correction_result,
    styles: dict,
    audio_filepath: str,
    output_dir: str,
    output_prefix: str,
    layout: PortraitLayout,
    font_path: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> str:
    """Generate the portrait lyrics ASS file and return its path."""
    # Merge the caller's karaoke block over the theme defaults so required ASS style
    # keys (primary_color, secondary_color, ...) always exist even for partial styles.
    from karaoke_gen.style_loader import DEFAULT_KARAOKE_STYLE

    merged = dict(DEFAULT_KARAOKE_STYLE)
    merged.update(styles.get("karaoke", {}))
    styles = {"karaoke": merged}
    k = styles["karaoke"]
    k["font_size"] = layout.font_size
    k["max_visible_lines"] = layout.max_visible_lines
    k["top_padding"] = _computed_top_padding(layout)
    if font_path:
        k["font_path"] = font_path
    elif k.get("font_path") and not os.path.isfile(k["font_path"]):
        # Drop an unresolved theme font path so libass falls back to the named font.
        k.pop("font_path", None)

    segments = prepare_portrait_segments(correction_result, layout, logger)

    gen = SubtitlesGenerator(
        output_dir=output_dir,
        video_resolution=(layout.width, layout.height),
        font_size=layout.font_size,
        line_height=layout.line_height,
        styles=styles,
        logger=logger or logging.getLogger(__name__),
    )
    ass_path = gen.generate_ass(segments, output_prefix, audio_filepath=audio_filepath)
    return ass_path


def render_portrait_video(
    correction_result,
    instrumental_path: str,
    styles: dict,
    artist: str,
    title: str,
    output_path: str,
    *,
    brand: Optional[PortraitBrandConfig] = None,
    layout: Optional[PortraitLayout] = None,
    font_path: Optional[str] = None,
    work_dir: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> str:
    """Render a portrait karaoke MP4 (title + lyrics body + end) and return its path.

    Args:
        correction_result: A ``CorrectionResult`` (already countdown-processed by the
            caller, so its segment timings match ``instrumental_path``).
        instrumental_path: The selected instrumental audio, padded to match the
            countdown offset applied to ``correction_result``.
        styles: Loaded theme style params (the ``karaoke`` block drives ASS colours).
        artist, title: Displayed in the branded header.
        output_path: Destination MP4 path.
        brand: Branding/background config; defaults to a plain themed background.
        layout: Portrait layout overrides.
        font_path: Resolved local font file for the lyrics + header.
        work_dir: Scratch dir for intermediates (a temp dir is used if omitted).
    """
    logger = logger or logging.getLogger(__name__)
    layout = layout or PortraitLayout()
    brand = brand or PortraitBrandConfig(font_path=font_path)
    if font_path and not brand.font_path:
        brand.font_path = font_path

    intro = float(styles.get("intro", {}).get("video_duration", layout.intro_seconds))
    outro = float(styles.get("end", {}).get("video_duration", layout.outro_seconds))

    _own_tmp = None
    if work_dir is None:
        _own_tmp = tempfile.TemporaryDirectory(prefix="portrait_")
        work_dir = _own_tmp.name
    os.makedirs(work_dir, exist_ok=True)

    try:
        # 1. Backgrounds
        bg_body = os.path.join(work_dir, "bg_body.png")
        bg_end = os.path.join(work_dir, "bg_end.png")
        build_background(brand, artist, title, variant="lyrics").save(bg_body)
        build_background(brand, artist, title, variant="end").save(bg_end)

        # 2. Portrait lyrics ASS
        ass_path = build_portrait_ass(
            correction_result, styles, instrumental_path, work_dir,
            "portrait", layout, font_path=font_path, logger=logger,
        )

        # 3. Assemble: title card + lyrics body + end card, single ffmpeg pass.
        body_dur = _ffprobe_duration(instrumental_path)
        ass_filter = f"ass='{_escape_ass_path(os.path.abspath(ass_path))}'"
        if font_path and os.path.isfile(font_path):
            ass_filter += f":fontsdir='{_escape_ass_path(os.path.dirname(os.path.abspath(font_path)))}'"

        w, h = layout.width, layout.height
        filtergraph = (
            f"[1:v]{ass_filter},trim=duration={body_dur:.3f},setpts=PTS-STARTPTS,"
            f"scale={w}:{h},setsar=1,fps=30[body];"
            f"[0:v]scale={w}:{h},setsar=1,fps=30[intro];"
            f"[2:v]scale={w}:{h},setsar=1,fps=30[outro];"
            # Pair each card with its own silence and the body with the instrumental:
            # intro(v)+intro_silence(4:a), body(v)+instrumental(3:a), outro(v)+outro_silence(5:a).
            f"[intro][4:a][body][3:a][outro][5:a]concat=n=3:v=1:a=1[v][a]"
        )

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-framerate", "30", "-t", f"{intro:.3f}", "-i", bg_body,
            "-loop", "1", "-framerate", "30", "-i", bg_body,
            "-loop", "1", "-framerate", "30", "-t", f"{outro:.3f}", "-i", bg_end,
            "-i", os.path.abspath(instrumental_path),
            "-f", "lavfi", "-t", f"{intro:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-f", "lavfi", "-t", f"{outro:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", filtergraph,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            os.path.abspath(output_path),
        ]
        logger.info(f"Rendering portrait video ({w}x{h}, body {body_dur:.1f}s) -> {output_path}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Portrait ffmpeg failed: {result.stderr[-2000:]}")
        if not os.path.isfile(output_path):
            raise RuntimeError(f"Portrait render produced no output: {output_path}")
        return output_path
    finally:
        if _own_tmp is not None:
            _own_tmp.cleanup()
