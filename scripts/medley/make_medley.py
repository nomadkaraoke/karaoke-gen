#!/usr/bin/env python3
"""
Build a karaoke medley (audio + synced karaoke video) from completed gen jobs.

See docs/MEDLEYS.md for the full process and the craft of choosing cut points.

Every section of the medley is cut from a gen job for the ORIGINAL song. Gen already gave
us the lossless source, sample-aligned stems and reviewed word-level lyrics for each one,
so the medley is just: the same cuts applied to every audio variant, plus the reviewed
lyric segments shifted onto the medley timeline and rendered with gen's own renderer.

Usage (from the repo root, in an env where karaoke_gen imports):
    python scripts/medley/make_medley.py fetch spec.json   # Firestore + GCS -> <workdir>/<job_id>/
    python scripts/medley/make_medley.py audio spec.json   # medley WAV/MP3 per audio variant
    python scripts/medley/make_medley.py video spec.json [--resolution 4k]  # karaoke + guide MP4s

Requires Firestore read access (ADC; the claude-readonly SA is enough), the gcloud CLI for GCS
downloads (set MEDLEY_GCLOUD_ACCOUNT to pick an account, e.g. the read-only SA when your user
login has expired) and ffmpeg.
See scripts/medley/example-spec.json for the spec format.
"""
import argparse
import dataclasses
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

PROJECT = "nomadkaraoke"
BUCKET = "karaoke-gen-storage-nomadkaraoke"
DEFAULT_CROSSFADE = 2.0
SEGMENT_TARGET_LUFS = -18.0
FINAL_LUFS = -14.0
FINAL_TRUE_PEAK = -1.0
# Audio variants built by `audio`: name -> stem file (None = the job's original input audio)
VARIANTS = {
    "instrumental_with_backing": "instrumental_with_backing.flac",
    "instrumental_clean": "instrumental_clean.flac",
    "guide_with_vocals": None,
}
KARAOKE_VARIANT = "instrumental_with_backing"
GUIDE_VARIANT = "guide_with_vocals"

log = logging.getLogger("medley")


# --- Spec + timeline (pure, unit-tested) -------------------------------------------------


@dataclass
class Section:
    job_id: str
    start: float                    # source seconds
    end: Optional[float]            # source seconds; None = to the end of the song
    join: str = "crossfade"         # how this section joins the PREVIOUS one: crossfade | gap
    join_duration: float = DEFAULT_CROSSFADE  # crossfade length, or silence length for a gap
    fade_in: float = 0.0            # "pot in": fade-in length at the start of this section
    fade_out_start: Optional[float] = None  # "pot out": source second the fader starts dropping
    label: str = ""


def parse_spec(spec: dict) -> list[Section]:
    sections = []
    for i, s in enumerate(spec["sections"]):
        join = s.get("join", "crossfade")
        if join not in ("crossfade", "gap"):
            raise ValueError(f"section {i}: join must be 'crossfade' or 'gap', got {join!r}")
        sec = Section(
            job_id=s["job_id"], start=float(s.get("start", 0.0)),
            end=None if s.get("end") is None else float(s["end"]),
            join=join,
            join_duration=float(s.get("join_duration", DEFAULT_CROSSFADE if join == "crossfade" else 0.5)),
            fade_in=float(s.get("fade_in", 0.0)),
            fade_out_start=None if s.get("fade_out_start") is None else float(s["fade_out_start"]),
            label=s.get("label", ""),
        )
        if sec.end is not None and sec.end <= sec.start:
            raise ValueError(f"section {i}: end must be after start")
        if sec.fade_out_start is not None and sec.end is not None and not sec.start < sec.fade_out_start < sec.end:
            raise ValueError(f"section {i}: fade_out_start must fall inside the section")
        sections.append(sec)
    if not sections:
        raise ValueError("spec has no sections")
    return sections


def compute_offsets(sections: list[Section], durations: list[float]) -> list[float]:
    """Medley start time of each section. durations[i] = section length in seconds.

    A crossfade overlaps the previous section's tail by join_duration; a gap inserts
    join_duration seconds of silence. Offsets are what the lyric shift adds to source times.
    """
    offsets, cursor = [], 0.0
    for i, (sec, dur) in enumerate(zip(sections, durations)):
        if i == 0:
            start = 0.0
        elif sec.join == "crossfade":
            start = cursor - sec.join_duration
        else:
            start = cursor + sec.join_duration
        offsets.append(start)
        cursor = start + dur
    return offsets


def shift_segments(segments, start: float, end: Optional[float], offset: float, prefix: str, tolerance: float = 0.05):
    """Keep lyric lines that START inside [start, end) and move them by offset.

    IDs are prefixed so the same song can appear twice (e.g. opener + finale).
    """
    out = []
    for seg in segments:
        if seg.start_time >= start - tolerance and (end is None or seg.start_time < end):
            words = [dataclasses.replace(w, id=f"{prefix}_{w.id}", start_time=w.start_time + offset,
                                         end_time=w.end_time + offset) for w in seg.words]
            out.append(dataclasses.replace(seg, id=f"{prefix}_{seg.id}", words=words,
                                           start_time=seg.start_time + offset, end_time=seg.end_time + offset))
    return out


def build_filtergraph(sections: list[Section], durations: list[float], gains_db: list[float]) -> str:
    """ffmpeg filter_complex joining inputs 0..n-1 (one per section, already trimmed) into [out]."""
    chains = []
    for i, (sec, dur, gain) in enumerate(zip(sections, durations, gains_db)):
        f = f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={gain:.2f}dB"
        if sec.fade_in > 0:
            f += f",afade=t=in:st=0:d={sec.fade_in:.3f}:curve=qsin"
        if sec.fade_out_start is not None:
            st = sec.fade_out_start - sec.start
            f += f",afade=t=out:st={st:.3f}:d={dur - st:.3f}:curve=qsin"
        nxt = sections[i + 1] if i + 1 < len(sections) else None
        if nxt is not None and nxt.join == "gap":
            f += f",apad=pad_dur={nxt.join_duration:.3f}"
        chains.append(f + f"[s{i}]")
    cur = "s0"
    for i in range(1, len(sections)):
        out = f"x{i}"
        if sections[i].join == "crossfade":
            chains.append(f"[{cur}][s{i}]acrossfade=d={sections[i].join_duration:.3f}:c1=qsin:c2=qsin[{out}]")
        else:
            chains.append(f"[{cur}][s{i}]concat=n=2:v=0:a=1[{out}]")
        cur = out
    chains.append(f"[{cur}]loudnorm=I={FINAL_LUFS}:TP={FINAL_TRUE_PEAK}:LRA=11,aresample=44100[out]")
    return ";".join(chains)


# --- I/O helpers ---------------------------------------------------------------------------


def _run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def probe_duration(path: str) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
               capture_output=True, text=True).stdout
    return float(out.strip())


def measure_lufs(path: str, start: float, end: Optional[float]) -> float:
    args = ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start)]
    if end is not None:
        args += ["-to", str(end)]
    args += ["-i", path, "-af", "ebur128", "-f", "null", "-"]
    res = subprocess.run(args, capture_output=True, text=True)
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", res.stderr)
    if res.returncode != 0 or not found:
        raise RuntimeError(f"loudness measurement failed for {path}: {res.stderr.strip()[-300:]}")
    return float(found[-1])


def gcs_download(src: str, dst: str, required: bool = True) -> bool:
    """Download gs://BUCKET/src via the gcloud CLI (the Python client stalled for ~1h on large FLACs)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    args = ["gcloud", "storage", "cp", f"gs://{BUCKET}/{src}", dst]
    if os.environ.get("MEDLEY_GCLOUD_ACCOUNT"):
        args.insert(3, f"--account={os.environ['MEDLEY_GCLOUD_ACCOUNT']}")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        if required:
            raise RuntimeError(f"download failed for gs://{BUCKET}/{src}: {result.stderr.strip()[-300:]}")
        return False
    return True


def job_dir(workdir: str, job_id: str) -> str:
    return os.path.join(workdir, job_id)


def load_job_meta(workdir: str, job_id: str) -> dict:
    with open(os.path.join(job_dir(workdir, job_id), "job.json")) as f:
        return json.load(f)


def variant_path(workdir: str, job_id: str, variant: str) -> str:
    d = job_dir(workdir, job_id)
    stem = VARIANTS[variant]
    if stem is None:
        return os.path.join(d, "input", os.path.basename(load_job_meta(workdir, job_id)["input_media_gcs_path"]))
    return os.path.join(d, "stems", stem)


def section_durations(sections: list[Section], workdir: str) -> list[float]:
    durs = []
    for sec in sections:
        end = sec.end if sec.end is not None else probe_duration(variant_path(workdir, sec.job_id, GUIDE_VARIANT))
        durs.append(end - sec.start)
    return durs


# --- Commands ------------------------------------------------------------------------------


def cmd_fetch(spec: dict, workdir: str):
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT)
    for job_id in dict.fromkeys(s["job_id"] for s in spec["sections"]):
        doc = db.collection("jobs").document(job_id).get()
        if not doc.exists:
            raise SystemExit(f"job {job_id} not found")
        job = doc.to_dict()
        if job.get("status") != "complete":
            log.warning("job %s is %s, not complete: lyrics may not be reviewed yet", job_id, job.get("status"))
        d = job_dir(workdir, job_id)
        os.makedirs(os.path.join(d, "stems"), exist_ok=True)
        os.makedirs(os.path.join(d, "input"), exist_ok=True)
        meta = {k: job.get(k) for k in ("job_id", "artist", "title", "status", "input_media_gcs_path",
                                         "style_params_gcs_path", "style_assets")}
        with open(os.path.join(d, "job.json"), "w") as f:
            json.dump(meta, f, indent=1, default=str)
        wanted = {job["input_media_gcs_path"]: os.path.join(d, "input", os.path.basename(job["input_media_gcs_path"]))}
        for name in ("instrumental_with_backing", "instrumental_clean", "lead_vocals", "backing_vocals"):
            wanted[f"jobs/{job_id}/stems/{name}.flac"] = os.path.join(d, "stems", f"{name}.flac")
        for name in ("corrections.json", "corrections_updated.json"):
            wanted[f"jobs/{job_id}/lyrics/{name}"] = os.path.join(d, name)
        for src, dst in wanted.items():
            optional = "corrections_updated" in src or src.endswith(("lead_vocals.flac", "backing_vocals.flac"))
            if not gcs_download(src, dst, required=not optional) and not optional:
                log.warning("missing gs://%s/%s", BUCKET, src)
        log.info("fetched %s - %s (%s)", job.get("artist"), job.get("title"), job_id)


def cmd_audio(spec: dict, workdir: str, outdir: str):
    sections = parse_spec(spec)
    durations = section_durations(sections, workdir)
    os.makedirs(outdir, exist_ok=True)
    for variant in VARIANTS:
        inputs, gains = [], []
        for sec in sections:
            p = variant_path(workdir, sec.job_id, variant)
            gains.append(SEGMENT_TARGET_LUFS - measure_lufs(p, sec.start, sec.end))
            inputs += ["-ss", str(sec.start)] + (["-to", str(sec.end)] if sec.end is not None else []) + ["-i", p]
        wav = os.path.join(outdir, f"medley_{variant}.wav")
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
             ["-filter_complex", build_filtergraph(sections, durations, gains), "-map", "[out]", "-c:a", "pcm_s24le", wav])
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", wav, "-c:a", "libmp3lame", "-b:a", "320k",
              wav[:-4] + ".mp3"])
        log.info("%s: %.1fs", wav, probe_duration(wav))
    offsets = compute_offsets(sections, durations)
    with open(os.path.join(outdir, "timeline.json"), "w") as f:
        json.dump([{"label": s.label, "job_id": s.job_id, "source_start": s.start, "source_end": s.start + d,
                    "medley_start": o, "medley_end": o + d} for s, d, o in zip(sections, durations, offsets)], f, indent=1)


def _load_reviewed_lyrics(workdir: str, job_id: str):
    from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
    from karaoke_gen.lyrics_transcriber.types import CorrectionResult
    d = job_dir(workdir, job_id)
    with open(os.path.join(d, "corrections.json")) as f:
        base = CorrectionResult.from_dict(json.load(f))
    updated = os.path.join(d, "corrections_updated.json")
    if os.path.exists(updated):
        with open(updated) as f:
            data = json.load(f)
        if isinstance(data, dict) and "corrections" in data:
            return CorrectionOperations.update_correction_result_with_data(base, data)
    return base


def cmd_video(spec: dict, workdir: str, outdir: str, resolution: str):
    from karaoke_gen.lyrics_transcriber.core.config import OutputConfig
    from karaoke_gen.lyrics_transcriber.output.countdown_processor import CountdownProcessor
    from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
    from karaoke_gen.style_loader import get_end_format, get_intro_format, load_styles_from_gcs
    from karaoke_gen.video_generator import VideoGenerator

    sections = parse_spec(spec)
    durations = section_durations(sections, workdir)
    offsets = compute_offsets(sections, durations)
    karaoke_wav = os.path.join(outdir, f"medley_{KARAOKE_VARIANT}.wav")
    guide_wav = os.path.join(outdir, f"medley_{GUIDE_VARIANT}.wav")
    if not os.path.exists(karaoke_wav):
        raise SystemExit("run the `audio` step first")
    render_dir = os.path.join(outdir, f"render_{resolution}")
    cache_dir = os.path.join(render_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    lyrics = {j: _load_reviewed_lyrics(workdir, j) for j in dict.fromkeys(s.job_id for s in sections)}
    segments = []
    for n, (sec, dur, off) in enumerate(zip(sections, durations, offsets)):
        segments += shift_segments(lyrics[sec.job_id].corrected_segments, sec.start, sec.start + dur,
                                   off - sec.start, prefix=f"m{n}")
    first = lyrics[sections[0].job_id]
    medley = dataclasses.replace(first, corrected_segments=segments, resized_segments=[])
    for seg in segments:
        log.info("%7.2f-%7.2f  %s", seg.start_time, seg.end_time, seg.text)

    # Theme: the style of the job named in the spec (default: first section's job)
    theme_job = load_job_meta(workdir, spec.get("theme_job_id", sections[0].job_id))
    styles_path, _ = load_styles_from_gcs(theme_job["style_params_gcs_path"], theme_job["style_assets"],
                                          os.path.join(render_dir, "style"), gcs_download, log)
    with open(styles_path) as f:
        style = json.load(f)

    # Same countdown gen adds when the first word starts within 3s (pads audio + shifts lyrics)
    medley, audio, _, pad = CountdownProcessor(cache_dir=cache_dir, logger=log).process(
        correction_result=medley, audio_filepath=karaoke_wav)

    artist, title = spec["artist"], spec["title"]
    vg = VideoGenerator(logger=log, ffmpeg_base_command="ffmpeg -hide_banner -loglevel error -nostats -y",
                        render_bounding_boxes=False, output_png=True, output_jpg=False)
    title_png, end_png = os.path.join(render_dir, "title"), os.path.join(render_dir, "end")
    vg.create_title_video(artist=artist, title=title, format=get_intro_format(style), output_image_filepath_noext=title_png,
                          output_video_filepath=title_png + ".mov", existing_title_image=None, intro_video_duration=0)
    vg.create_end_video(artist=artist, title=title, format=get_end_format(style), output_image_filepath_noext=end_png,
                        output_video_filepath=end_png + ".mov", existing_end_image=None, end_video_duration=0)

    cfg = OutputConfig(output_dir=render_dir, cache_dir=cache_dir, output_styles_json=styles_path, render_video=True,
                       generate_cdg=False, generate_plain_text=True, generate_lrc=True, video_resolution=resolution)
    outputs = OutputGenerator(cfg, log).generate_outputs(
        transcription_corrected=medley, lyrics_results={}, output_prefix=title, audio_filepath=audio,
        artist=artist, title=title)

    w, h = {"4k": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720), "360p": (640, 360)}[resolution]
    intro_s, end_s = style["intro"]["video_duration"], style["end"]["video_duration"]
    master = os.path.join(outdir, f"{title} (Karaoke {resolution}).mp4")
    scale = f"scale={w}:{h},fps=30,format=yuv420p,setsar=1"
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
          "-loop", "1", "-t", str(intro_s), "-i", title_png + ".png", "-f", "lavfi", "-t", str(intro_s), "-i", "anullsrc=r=44100:cl=stereo",
          "-i", outputs.video,
          "-loop", "1", "-t", str(end_s), "-i", end_png + ".png", "-f", "lavfi", "-t", str(end_s), "-i", "anullsrc=r=44100:cl=stereo",
          "-filter_complex", f"[0:v]{scale}[t];[2:v]{scale}[k];[3:v]{scale}[e];[t][k][e]concat=n=3:v=1:a=0[v];[1:a][2:a][4:a]concat=n=3:v=0:a=1[a]",
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-c:a", "aac", "-b:a", "320k",
          "-movflags", "+faststart", master])
    # Guide: same picture, original-vocals medley delayed by title + countdown (for checking sync)
    delay_ms = int((intro_s + pad) * 1000)
    guide = os.path.join(outdir, f"{title} (With Vocals {resolution}).mp4")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", master, "-i", guide_wav,
          "-filter_complex", f"[1:a]adelay={delay_ms}|{delay_ms},apad[a]", "-map", "0:v", "-map", "[a]", "-shortest",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", guide])
    if outputs.lrc:
        _run(["cp", outputs.lrc, os.path.join(outdir, f"{title} (Karaoke).lrc")])
    log.info("karaoke: %s\nguide: %s", master, guide)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["fetch", "audio", "video"])
    p.add_argument("spec")
    p.add_argument("--resolution", default="4k", choices=["4k", "1080p", "720p", "360p"])
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with open(args.spec) as f:
        spec = json.load(f)
    parse_spec(spec)  # validate early
    base = os.path.dirname(os.path.abspath(args.spec))
    workdir = os.path.join(base, spec.get("workdir", "work"))
    outdir = os.path.join(base, spec.get("outdir", "out"))
    if args.command == "fetch":
        cmd_fetch(spec, workdir)
    elif args.command == "audio":
        cmd_audio(spec, workdir, outdir)
    else:
        cmd_video(spec, workdir, outdir, args.resolution)


if __name__ == "__main__":
    sys.exit(main())
