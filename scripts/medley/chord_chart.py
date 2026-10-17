#!/usr/bin/env python3
"""
Turn a customer's chord chart (chords written over lyrics) into timed chords for one song.

    python scripts/medley/chord_chart.py --chart chart.txt --lyrics corrections.json \\
        [--lyrics-updated corrections_updated.json] --detected chords_detected.json \\
        --out chords.json [--wrap 56]

`make_medley.py chart spec.json` runs this for every job that has a `chart.txt` (or
`chart.docx`) in its work dir. See docs/MEDLEYS.md § Guitar chords.

The chart is the source of truth for WHICH chord and WHERE in the lyrics; gen's reviewed
word timings say WHEN. Chart format (plain text in a monospaced layout, or a .docx of one):

    Capo 2                                  <- "Capo N" / "No Capo" (alone or e.g. "Key change Capo 1")
    C           Em       F            C     <- chord line: only chord symbols
    Amarillo by mornin', up from San Antone <- lyric line: each chord goes on the word under it
    C Em F G C                              <- chord line with no lyric under it: played in the
                                               break after the previous lyric
    [Chorus]  (Talking)  # note  Verse 2:   <- ignored, as is everything before the first
                                               chord or capo line (title, notes)

Chords keep the chart's spelling (capo shapes, "G/Em"). Intros, breaks and outros the
chart doesn't cover are filled from the detected chords (concert pitch), moved down by
the capo so they read in the same shapes. Each output chord carries its "capo".
"""
import argparse
import difflib
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree

CHORD_RE = re.compile(
    r"^(?:N\.?C\.?|[A-G][#b]?(?:maj|min|m|M|dim|aug|sus|add)?\d*(?:sus\d*|add\d+|[b#]\d+)*"
    r"(?:/[A-G][#b]?(?:maj|min|m)?\d*)?)$")
CAPO_RE = re.compile(r"\b(?:no\s+capo|capo\s*(\d+))\b", re.I)
COMMENT_RE = re.compile(r"^\s*(?:#.*|\[.*\]|\(.*\))\s*$")
SECTION_RE = re.compile(r"^\s*(?:pre-?chorus|chorus|verse|bridge|intro|outro|instrumental|interlude|solo|"
                        r"refrain|tag|ending)\b[\s\d:.)(-]*$", re.I)  # "Chorus:", "Verse 2", "Instrumental"
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]  # guitar-shape spelling
PITCH = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7,
         "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
# A detected change this soon before the chart's next chord (same root) is that chord, early
ANTICIPATION_SECONDS = 2.5
# A chord this close before a line's first word belongs to that line (gen's CHORD_LINE_LEAD_SECONDS)
LINE_LEAD_SECONDS = 0.6
FALLBACK_CHORD_SECONDS = 2.0
MAX_BLANKS_UNDER_CHORDS = 2


@dataclass
class ChartChord:
    label: str
    capo: int
    word: Optional[int] = None    # chart word index the chord sits over
    after: Optional[int] = None   # free chord: played after this chart word (-1 = before the first)
    group: int = 0                # free chords from the same spot form a group
    time: Optional[float] = None


@dataclass
class Chart:
    words: list = field(default_factory=list)   # chart lyric words, in order
    chords: list = field(default_factory=list)  # ChartChord
    lines: list = field(default_factory=list)   # (first word, last word) of lyric lines that have chords


# --- Reading -------------------------------------------------------------------------------


def docx_text(path: str) -> str:
    """Paragraph text of a .docx (line breaks and tabs kept), without python-docx."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(f"{ns}p"):
        text = ""
        # Only run content: <w:tabs> in the paragraph properties are tab stops, not tabs
        for el in (child for run in p.iter(f"{ns}r") for child in run):
            if el.tag == f"{ns}t":
                text += el.text or ""
            elif el.tag == f"{ns}tab":
                text += "\t"
            elif el.tag in (f"{ns}br", f"{ns}cr"):
                text += "\n"
        paras.append(text)
    return "\n".join(paras)


def chart_lines(text: str, wrap: Optional[int] = None) -> list[str]:
    """Lines as they appear on the page: soft breaks split, tabs expanded, trailing space
    dropped, and (wrap=N) paragraphs longer than N monospaced columns broken like Word does."""
    out = []
    for line in text.replace(" ", "\n").replace("\r", "").split("\n"):
        line = line.expandtabs(8).rstrip()
        while wrap and len(line) > wrap:
            out.append(line[:wrap].rstrip())
            line = line[wrap:]
        out.append(line)
    return out


def is_chord_line(line: str) -> bool:
    tokens = line.split()
    return bool(tokens) and all(CHORD_RE.match(t) for t in tokens)


def is_comment(line: str) -> bool:
    return bool(COMMENT_RE.match(line) or SECTION_RE.match(line))


def capo_directive(line: str) -> Optional[int]:
    """Capo for 'Capo 2' / 'No Capo' / 'Key change Capo 1' lines, else None."""
    m = CAPO_RE.search(line)
    if not m or len(line.split()) > 5:
        return None
    return int(m.group(1)) if m.group(1) else 0


def norm_word(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'"))


def parse_chart(text: str, wrap: Optional[int] = None) -> Chart:
    chart, capo, group = Chart(), 0, 0
    lines = chart_lines(text, wrap)
    i = 0
    started = False  # lines before the first chord / capo line are the title and notes
    while i < len(lines):
        line = lines[i]
        i += 1
        started = started or is_chord_line(line) or capo_directive(line) is not None
        if not started or not line.strip() or is_comment(line):
            continue
        directive = capo_directive(line)
        if directive is not None:
            capo = directive
            continue
        if is_chord_line(line):
            chords = [(m.start(), m.group()) for m in re.finditer(r"\S+", line)]
            j = i
            while j < len(lines) and j - i < MAX_BLANKS_UNDER_CHORDS and not lines[j].strip():
                j += 1  # Word charts often have an empty paragraph between chords and lyric
            lyric = lines[j] if j < len(lines) else ""
            if lyric.strip() and not is_chord_line(lyric) and capo_directive(lyric) is None \
                    and not is_comment(lyric):
                i = j + 1
                _add_line(chart, chords, lyric, capo, group)
            else:
                for _, label in chords:
                    chart.chords.append(ChartChord(label, capo, after=len(chart.words) - 1, group=group))
            group += 1
            continue
        _add_line(chart, [], line, capo, group)
    return chart


def _add_line(chart: Chart, chords, lyric: str, capo: int, group: int):
    """Lyric words, with each chord on the word under its first column (or the next word)."""
    spans = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", lyric)]
    base = len(chart.words)
    chart.words += [norm_word(w) for _, _, w in spans]
    if chords and spans:
        chart.lines.append((base, base + len(spans) - 1))
    used = set()
    for col, label in chords:
        target = next((k for k, (s, e, _) in enumerate(spans) if col < e), None)
        if target is None or target in used:  # past the end of the line, or a 2nd chord on a word
            after = base + (target if target is not None else len(spans) - 1)
            chart.chords.append(ChartChord(label, capo, after=after, group=group))
        else:
            used.add(target)
            chart.chords.append(ChartChord(label, capo, word=base + target))


# --- Timing --------------------------------------------------------------------------------


def root_pitches(label: str) -> set:
    """Pitch classes a label can be matched on: root, plus the part after '/' ("G/Em" = G or Em)."""
    out = set()
    for part in label.split("/"):
        m = re.match(r"[A-G][#b]?", part)
        if m:
            out.add(PITCH[m.group()])
    return out


def to_shape(label: str, capo: int) -> str:
    """Concert-pitch detected chord -> the shape played with this capo, simplified to the
    chart's vocabulary (triads, plus dominant 7ths)."""
    m = re.match(r"([A-G][#b]?)(.*)", label)
    if not m:
        return label
    quality = m.group(2).split("/")[0]
    quality = {"m7": "m", "maj7": "", "sus2": "", "sus4": "", "m7b5": "dim", "dim7": "dim"}.get(quality, quality)
    return NAMES[(PITCH[m.group(1)] - capo) % 12] + quality


def align_words(chart_words: list[str], sung: list[str]) -> dict:
    """chart word index -> sung word index, from matching runs (missed words interpolated)."""
    matcher = difflib.SequenceMatcher(a=chart_words, b=sung, autojunk=False)
    exact = {}
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            exact[a + k] = b + k
    if not exact:
        raise ValueError("chart lyrics don't match the song's lyrics at all")
    keys = sorted(exact)
    out = {}
    for i in range(len(chart_words)):
        if i in exact:
            out[i] = exact[i]
            continue
        lo = max((k for k in keys if k < i), default=None)
        hi = min((k for k in keys if k > i), default=None)
        if lo is None:
            out[i] = max(0, exact[hi] - (hi - i))
        elif hi is None:
            out[i] = min(len(sung) - 1, exact[lo] + (i - lo))
        else:
            out[i] = round(exact[lo] + (exact[hi] - exact[lo]) * (i - lo) / (hi - lo))
    return out


def chart_to_chords(chart: Chart, sung: list[tuple], detected: list[dict], duration: float) -> list[dict]:
    """Timed chords. sung = [(text, start, end)] for every reviewed lyric word, in order."""
    if not chart.chords:
        raise ValueError("no chords found in the chart")
    mapping = align_words(chart.words, [norm_word(w[0]) for w in sung])
    chords = [ChartChord(c.label, c.capo, c.word, c.after, c.group) for c in chart.chords]
    for c in chords:
        if c.word is not None:
            c.time = sung[mapping[c.word]][1]
    for group in sorted({c.group for c in chords if c.word is None}):
        _time_free_group([c for c in chords if c.word is None and c.group == group], chords, sung, mapping,
                         detected, duration)
    out = [{"start": c.time, "label": c.label, "capo": c.capo} for c in chords]
    out += _fill_gaps(chart, chords, sung, mapping, detected)
    out.sort(key=lambda c: c["start"])
    for a, b in zip(out, out[1:]):
        a["end"] = b["start"]
    if out:
        last_detected = max((d["end"] for d in detected), default=0.0)
        out[-1]["end"] = min(duration, max(last_detected, out[-1]["start"] + FALLBACK_CHORD_SECONDS))
    return [{"start": round(c["start"], 3), "end": round(c["end"], 3), "label": c["label"], "capo": c["capo"]}
            for c in out if c["end"] - c["start"] > 0.05]


def _time_free_group(group, chords, sung, mapping, detected, duration):
    """Chords played after a lyric (no word under them): matched in order to detected changes
    between that word and the next timed chord / sung word, else spaced evenly."""
    after = group[0].after
    w = mapping.get(after) if after is not None and after >= 0 else None
    t0 = sung[w][2] if w is not None else 0.0
    # Earlier groups after the same word (chords past the line end, then a chords-only
    # break line) are played first: start after them
    prior = [c.time for c in chords if c.word is None and c.after == after and c.time is not None]
    if prior:
        t0 = max(prior)
    nxt_word = sung[w + 1][1] if w is not None and w + 1 < len(sung) else (sung[0][1] if w is None else duration)
    later = [c.time for c in chords if c.time is not None and c.time > t0]
    t1 = min([nxt_word] + later)
    window = [d for d in detected if t0 - 0.3 <= d["start"] < t1 - 0.3]
    cursor = t0 if prior else t0 - 0.3
    for c in group:
        hit = next((d for d in window if d["start"] > cursor and
                    root_pitches(to_shape(d["label"], c.capo)) & root_pitches(c.label)), None)
        if hit:
            c.time = cursor = hit["start"]
    prev = t0
    for k, c in enumerate(group):  # the rest: evenly between their timed neighbours
        if c.time is None:
            j = next((j for j in range(k + 1, len(group)) if group[j].time is not None), None)
            missing = (j if j is not None else len(group)) - k
            if j is not None:
                step = (group[j].time - prev) / (missing + 1)
            else:
                step = min(FALLBACK_CHORD_SECONDS, (t1 - prev) / (missing + 1))
            c.time = prev + step
        prev = c.time


def _fill_gaps(chart, chords, sung, mapping, detected) -> list[dict]:
    """Detected chords wherever no charted line is being sung: intros, breaks, outros, the
    gaps between lines and uncharted lines (e.g. a spoken verse). Dropped when they only
    repeat the chord already ringing (same root) or anticipate the chart's next chord; moved into the
    shapes of whichever neighbouring capo they fit (that capo's chart chords)."""
    sung_lines = [(sung[mapping[a]][1] - 0.3, sung[mapping[b]][2]) for a, b in chart.lines]
    timed = sorted((c for c in chords if c.time is not None), key=lambda c: c.time)
    free = {}
    for c in chords:
        if c.word is None and c.time is not None:
            free.setdefault(c.group, []).append(c.time)
    charted = sung_lines + [(min(ts) - 0.3, max(ts) + 0.3) for ts in free.values()]
    vocab = {}
    for c in chords:
        vocab.setdefault(c.capo, set()).update(root_pitches(c.label))
    out = []
    for d in sorted(detected, key=lambda d: d["start"]):
        t = d["start"]
        if any(a <= t <= b for a, b in charted):
            continue
        before = [c for c in timed if c.time <= t][-1:]
        after = [c for c in timed if c.time > t][:1]
        capos = [x.capo for x in before + after]
        fits = [cp for cp in capos if root_pitches(to_shape(d["label"], cp)) & vocab.get(cp, set())]
        capo = fits[-1] if fits else (capos[-1] if capos else 0)
        label = to_shape(d["label"], capo)
        ringing = out[-1] if out and (not before or out[-1]["start"] > before[0].time) else (
            {"label": before[0].label, "capo": before[0].capo} if before else None)
        if ringing and ringing["capo"] == capo and root_pitches(label) & root_pitches(ringing["label"]):
            continue  # same chord (D7 under a charted D, Em under "G/Em")
        if after and after[0].time - t < ANTICIPATION_SECONDS and after[0].capo == capo \
                and root_pitches(label) & root_pitches(after[0].label):
            continue
        out.append({"start": t, "label": label, "capo": capo})
    return out


# --- CLI -----------------------------------------------------------------------------------


def read_chart(path: str, wrap: Optional[int]) -> Chart:
    text = docx_text(path) if path.lower().endswith(".docx") else open(path, encoding="utf-8").read()
    return parse_chart(text, wrap)


def sung_words(corrections: str, updated: Optional[str] = None) -> list[tuple]:
    """(text, start, end) of every reviewed word, via gen's own correction loading."""
    from karaoke_gen.lyrics_transcriber.correction.operations import CorrectionOperations
    from karaoke_gen.lyrics_transcriber.types import CorrectionResult
    with open(corrections) as f:
        result = CorrectionResult.from_dict(json.load(f))
    if updated:
        with open(updated) as f:
            data = json.load(f)
        if isinstance(data, dict) and "corrections" in data:
            result = CorrectionOperations.update_correction_result_with_data(result, data)
    return [(w.text, w.start_time, w.end_time) for seg in result.corrected_segments for w in seg.words]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chart", required=True, help="chord chart: .txt or .docx")
    p.add_argument("--lyrics", required=True, help="the job's corrections.json")
    p.add_argument("--lyrics-updated", help="the job's corrections_updated.json (reviewed edits)")
    p.add_argument("--detected", required=True, help="detected chords (detect_chords.py output)")
    p.add_argument("--duration", type=float, help="song length (default: end of the last detected chord)")
    p.add_argument("--wrap", type=int, help="page width in columns, to undo Word's soft wrapping of long lines")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    with open(args.detected) as f:
        detected = json.load(f)
    duration = args.duration or max(d["end"] for d in detected)
    chords = chart_to_chords(read_chart(args.chart, args.wrap), sung_words(args.lyrics, args.lyrics_updated),
                             detected, duration)
    with open(args.out, "w") as f:
        json.dump(chords, f, indent=1)
    print(f"{len(chords)} chord changes -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())
