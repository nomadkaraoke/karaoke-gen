#!/usr/bin/env python3
"""Experimental portrait karaoke ASS generator driving the real karaoke-gen generator.

Loads a real job's corrected_segments + style params, then re-lays-out the lyrics
for a portrait (9:16) frame at a chosen resolution / font / wrapping, and writes a
portrait .ass file. Rendering to video/still is done separately with ffmpeg.

Usage:
  python render_portrait.py --segments piri/corrections_updated.json \
      --style piri/style_params.json --out out/portrait_tuned.ass \
      --width 1080 --height 1920 --font 90 --lines 4 --maxlen 21 --top-padding 90
"""
import argparse
import json
import logging
import os
import sys
import tempfile

REPO = "/Users/andrew/Projects/nomadkaraoke/karaoke-gen-portrait-video"
sys.path.insert(0, REPO)

from karaoke_gen.lyrics_transcriber.types import LyricsSegment
from karaoke_gen.lyrics_transcriber.output.subtitles import SubtitlesGenerator
from karaoke_gen.lyrics_transcriber.output.segment_resizer import SegmentResizer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--style", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--font", type=int, default=90)
    ap.add_argument("--line-height", type=int, default=None, help="defaults to font")
    ap.add_argument("--lines", type=int, default=4, help="max visible lines")
    ap.add_argument("--maxlen", type=int, default=21, help="max chars per line (wrapping)")
    ap.add_argument("--top-padding", type=int, default=None, help="defaults to line height")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    logger = logging.getLogger("portrait")

    with open(args.segments) as f:
        data = json.load(f)
    segments = [LyricsSegment.from_dict(s) for s in data["corrected_segments"]]
    logger.warning(f"Loaded {len(segments)} corrected segments")

    with open(args.style) as f:
        style_params = json.load(f)

    # Build styles dict from the real theme, with portrait overrides.
    styles = {"karaoke": dict(style_params.get("karaoke", {}))}
    k = styles["karaoke"]
    k["font_size"] = args.font
    k["max_visible_lines"] = args.lines
    if args.top_padding is not None:
        k["top_padding"] = args.top_padding
    # Resolve font_path: theme paths are relative to the GCS themes/ mirror; if a local
    # copy exists use it, otherwise drop it so libass falls back to the named font.
    fp = k.get("font_path")
    if fp and not os.path.isfile(fp):
        local_fp = os.path.join(os.path.dirname(args.style), "font.ttf")
        k["font_path"] = local_fp if os.path.isfile(local_fp) else None

    line_height = args.line_height or args.font

    # Re-wrap segments for the narrow portrait frame.
    resizer = SegmentResizer(max_line_length=args.maxlen, logger=logger)
    resized = resizer.resize_segments(segments)
    logger.warning(f"Resized to {len(resized)} segments (maxlen={args.maxlen})")

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    gen = SubtitlesGenerator(
        output_dir=out_dir,
        video_resolution=(args.width, args.height),
        font_size=args.font,
        line_height=line_height,
        styles=styles,
        logger=logger,
    )
    prefix = os.path.splitext(os.path.basename(args.out))[0]
    ass_path = gen.generate_ass(resized, prefix, audio_filepath="dummy.flac")
    # generate_ass writes "{prefix} (Karaoke).ass" — normalize to requested --out
    if os.path.abspath(ass_path) != os.path.abspath(args.out):
        os.replace(ass_path, args.out)
    print(args.out)


if __name__ == "__main__":
    main()
