#!/usr/bin/env python3
"""
Detect guitar chords for one song: an ensemble of ChordMini models over several stem mixes.

    python scripts/medley/detect_chords.py --chordmini ~/ChordMini \\
        --input mix=song.flac --input inst=instrumental_clean.flac --input harm=harmonic.flac \\
        --out chords.json

Run it with ChordMini's own Python (it needs torch + librosa + mir_eval); see
docs/MEDLEYS.md § Guitar chords for setup and why this ensemble.

Pipeline:
1. Both ChordMini checkpoints (BTC and 2E1D, continual-learning versions; the best open
   chord models we found, ~80% MIREX/majmin) on every input -> .lab files.
2. Each label simplified to a guitar chord (root + one of: major, m, 7, maj7, m7, sus2,
   sus4, dim, aug, m7b5, dim7), then a weighted frame-by-frame vote across all runs.
3. Changes shorter than MIN_CHORD_SECONDS merged away; boundaries snapped to the
   nearest beat when one is within BEAT_SNAP_SECONDS.
4. Roots spelled for the local key (Ab/Db/Eb7 in A-flat, not G#/C#/D#7).

Output: JSON list of {"start", "end", "label"} (seconds, no "no chord" entries).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict

import numpy as np

FRAME = 0.05
MIN_CHORD_SECONDS = 0.6
BEAT_SNAP_SECONDS = 0.2
KEY_WINDOW_SECONDS = 20.0
MODELS = {"BTC": ("btc_model_best.pth", 1.1), "ChordNet": ("2e1d_model_best.pth", 1.0)}
INPUT_WEIGHTS = {"inst": 1.2}  # vocals removed = cleaner harmony; everything else 1.0

PITCH = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4, "E#": 5, "F": 5, "F#": 6,
         "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11, "B#": 0}
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
FLAT_KEYS = {1, 3, 5, 6, 8, 10}  # Db Eb F Gb Ab Bb major
QUALITY = {"maj": "", "": "", "min": "m", "7": "7", "maj7": "maj7", "min7": "m7", "sus4": "sus4",
           "sus2": "sus2", "dim": "dim", "aug": "aug", "maj6": "", "min6": "m", "dim7": "dim7",
           "hdim7": "m7b5", "minmaj7": "m", "9": "7", "maj9": "maj7", "min9": "m7", "11": "7", "13": "7"}
# Scale degree (semitones above the key) -> qualities that are diatonic there in a major key
DIATONIC = {0: {"", "maj7"}, 2: {"m", "m7"}, 4: {"m", "m7"}, 5: {"", "maj7"}, 7: {"", "7"}, 9: {"m", "m7"},
            11: {"dim", "m7b5"}}


def simplify(label: str):
    """'G#:7' -> (8, '7'); 'N'/'X' -> None. Drops inversions ('C/E' -> C)."""
    label = label.strip()
    if label in ("N", "X", ""):
        return None
    root, _, qual = label.partition(":")
    qual = qual.split("/")[0].split("(")[0]
    root = root.split("/")[0]
    if root not in PITCH:
        return None
    return PITCH[root], QUALITY.get(qual, "")


def spell(pc: int, qual: str, key: int) -> str:
    names = FLAT_NAMES if key in FLAT_KEYS else SHARP_NAMES
    name = names[pc]
    # Borrowed bIII / bVI / bVII in sharp keys read better as flats (Eb, Ab, Bb in C)
    if key not in FLAT_KEYS and pc in ((key + 3) % 12, (key + 8) % 12, (key + 10) % 12) and len(name) > 1:
        name = FLAT_NAMES[pc]
    return name + qual


def estimate_key(chords, t0: float, t1: float) -> int:
    """Major key whose diatonic chords cover the most time in [t0, t1]."""
    scores = np.zeros(12)
    for start, end, (pc, qual) in chords:
        overlap = min(end, t1) - max(start, t0)
        if overlap <= 0:
            continue
        for k in range(12):
            if qual in DIATONIC.get((pc - k) % 12, ()):
                scores[k] += overlap * (1.5 if (pc - k) % 12 in (0, 7) else 1.0)
    return int(np.argmax(scores))


def read_lab(path):
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3:
                rows.append((float(parts[0]), float(parts[1]), " ".join(parts[2:])))
    return rows


def vote(labs, duration: float):
    """labs: list of (weight, rows). Returns per-frame winning (pc, qual) or None."""
    n = int(np.ceil(duration / FRAME))
    tallies = [defaultdict(float) for _ in range(n)]
    for weight, rows in labs:
        for start, end, label in rows:
            chord = simplify(label)
            for i in range(int(start / FRAME), min(n, int(np.ceil(end / FRAME)))):
                tallies[i][chord] += weight
    return [max(t.items(), key=lambda kv: kv[1])[0] if t else None for t in tallies]


def frames_to_segments(frames):
    segs, start = [], 0
    for i in range(1, len(frames) + 1):
        if i == len(frames) or frames[i] != frames[start]:
            segs.append([start * FRAME, i * FRAME, frames[start]])
            start = i
    return segs


def merge_short(segs, min_len: float = MIN_CHORD_SECONDS):
    """Fold segments shorter than min_len into the longer neighbour, until none remain."""
    segs = [s[:] for s in segs]
    while True:
        short = [i for i, s in enumerate(segs) if s[1] - s[0] < min_len]
        if not short or len(segs) == 1:
            break
        i = min(short, key=lambda j: segs[j][1] - segs[j][0])
        prev_len = segs[i - 1][1] - segs[i - 1][0] if i > 0 else -1
        next_len = segs[i + 1][1] - segs[i + 1][0] if i + 1 < len(segs) else -1
        if prev_len >= next_len:
            segs[i - 1][1] = segs[i][1]
        else:
            segs[i + 1][0] = segs[i][0]
        del segs[i]
        # Re-join neighbours that now carry the same chord
        j = 0
        while j + 1 < len(segs):
            if segs[j][2] == segs[j + 1][2]:
                segs[j][1] = segs[j + 1][1]
                del segs[j + 1]
            else:
                j += 1
    return segs


def snap_to_beats(segs, beats, tolerance: float = BEAT_SNAP_SECONDS):
    if len(beats) == 0:
        return segs
    beats = np.asarray(beats)
    for i in range(1, len(segs)):
        b = beats[np.argmin(np.abs(beats - segs[i][0]))]
        if abs(b - segs[i][0]) <= tolerance and segs[i - 1][0] < b < segs[i][1]:
            segs[i - 1][1] = segs[i][0] = float(b)
    return segs


def label_segments(segs):
    """Spell each chord for the key around it; drop no-chord segments."""
    chords = [(s, e, c) for s, e, c in segs if c is not None]
    out = []
    for start, end, (pc, qual) in chords:
        mid = (start + end) / 2
        key = estimate_key(chords, mid - KEY_WINDOW_SECONDS / 2, mid + KEY_WINDOW_SECONDS / 2)
        out.append({"start": round(start, 3), "end": round(end, 3), "label": spell(pc, qual, key)})
    return out


def run_chordmini(chordmini: str, audio: str, model: str, out_dir: str) -> str:
    ckpt, _ = MODELS[model]
    in_dir = tempfile.mkdtemp(prefix="cm_in_")
    link = os.path.join(in_dir, "song" + os.path.splitext(audio)[1])
    os.symlink(os.path.abspath(audio), link)
    args = [sys.executable, "src/evaluation/test.py", "--model_type", model, "--checkpoint", f"checkpoints/{ckpt}",
            "--config", "config/ChordMini.yaml", "--audio_dir", in_dir, "--save_dir", out_dir, "--use_overlap",
            "--vote_aggregation", "logit", "--min_segment_duration", "0.5", "--smooth_predictions",
            "--use_gaussian", "--kernel_size", "9"]
    if model == "BTC":
        args.append("--smooth_logits")
    subprocess.run(args, cwd=chordmini, check=True, capture_output=True)
    return os.path.join(out_dir, "song.lab")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chordmini", required=True, help="ChordMini checkout (github.com/ptnghia-j/ChordMini)")
    p.add_argument("--input", action="append", required=True, help="name=path; e.g. mix=song.flac inst=inst.flac")
    p.add_argument("--beats-from", help="audio to beat-track (default: the 'mix' input)")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    import librosa
    import scipy.signal

    if not hasattr(scipy.signal, "hann"):  # librosa 0.10.1 beat tracking on scipy >= 1.13
        scipy.signal.hann = scipy.signal.windows.hann

    inputs = dict(i.split("=", 1) for i in args.input)
    labs, duration = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        for name, path in inputs.items():
            duration = max(duration, librosa.get_duration(path=path))
            for model, (_, model_weight) in MODELS.items():
                lab = run_chordmini(args.chordmini, path, model, os.path.join(tmp, f"{name}_{model}"))
                labs.append((model_weight * INPUT_WEIGHTS.get(name, 1.0), read_lab(lab)))
    segs = merge_short(frames_to_segments(vote(labs, duration)))
    beat_src = args.beats_from or inputs.get("mix") or next(iter(inputs.values()))
    y, sr = librosa.load(beat_src, sr=22050, mono=True)
    _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    segs = snap_to_beats(segs, librosa.frames_to_time(beat_frames, sr=sr))
    chords = label_segments(segs)
    with open(args.out, "w") as f:
        json.dump(chords, f, indent=1)
    print(f"{len(chords)} chord changes -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())
