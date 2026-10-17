"""Guitar chord symbols for karaoke videos.

Chords are a timed sequence of ``ChordChange`` (start, end, label) for the whole song.
Each change that happens while a lyric line is being sung is drawn above the word it
lands on, in a row reserved above the line (see ``chord_layout``), and switches colour
when the chord is played. A "now / next" box in the top-right corner shows the current
chord at all times, which covers intros and instrumental breaks where there's no lyric
line to sit a chord above.
"""

from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional, Sequence

from karaoke_gen.lyrics_transcriber.types import LyricsSegment

# A chord that changes just before a word (strummed on the pickup) belongs to that word
CHORD_WORD_SNAP_SECONDS = 0.25
# A chord shortly before a line's first word is shown over that first word
CHORD_LINE_LEAD_SECONDS = 0.6
NO_CHORD_LABELS = frozenset({"", "N", "X", "N.C.", "NC"})


@dataclass(frozen=True)
class ChordChange:
    start: float
    end: float
    label: str
    capo: int = 0  # fret the label's shape is played at (shown in the chord box when > 0)


@dataclass(frozen=True)
class PlacedChord:
    chord: ChordChange
    word_index: int
    x: int  # left edge of the chord label, in pixels


def clean_chords(chords: Sequence[ChordChange]) -> List[ChordChange]:
    """Sorted, without no-chord markers, with repeated labels merged."""
    out: List[ChordChange] = []
    for c in sorted(chords, key=lambda c: c.start):
        if c.label.strip() in NO_CHORD_LABELS or c.end <= c.start:
            continue
        if out and out[-1].label == c.label.strip() and out[-1].capo == c.capo and c.start - out[-1].end < 0.05:
            out[-1] = replace(out[-1], end=c.end)
        else:
            out.append(replace(c, label=c.label.strip()))
    return out


def assign_chords_to_segments(
    segments: Sequence[LyricsSegment], chords: Sequence[ChordChange]
) -> Dict[str, List[ChordChange]]:
    """Map segment id -> chord changes to draw above that line.

    A change belongs to the line being sung when it happens (from slightly before the
    first word to the last word's end). Changes between lines go only in the corner box.
    The chord already ringing when a line starts is repeated over its first word, so a
    guitarist reading the line always sees what to play.
    """
    result: Dict[str, List[ChordChange]] = {}
    chords = list(chords)
    for seg in segments:
        if not seg.words:
            continue
        line_start, line_end = seg.words[0].start_time, seg.words[-1].end_time
        on_line = [c for c in chords if line_start - CHORD_LINE_LEAD_SECONDS <= c.start <= line_end]
        ringing = [c for c in chords if c.start < line_start - CHORD_LINE_LEAD_SECONDS and c.end > line_start]
        if ringing and (not on_line or on_line[0].start > line_start + CHORD_WORD_SNAP_SECONDS):
            on_line.insert(0, ringing[-1])
        if on_line:
            result[seg.id] = on_line
    return result


def word_index_for_time(segment: LyricsSegment, t: float) -> int:
    """Index of the word a chord change at time ``t`` sits above."""
    idx = 0
    for i, w in enumerate(segment.words):
        if w.start_time <= t + CHORD_WORD_SNAP_SECONDS:
            idx = i
        else:
            break
    return idx


def place_chords(
    segment: LyricsSegment,
    chords: Sequence[ChordChange],
    word_texts: Sequence[str],
    line_left: int,
    measure: Callable[[str], int],
    chord_measure: Callable[[str], int],
    min_spacing: int,
) -> List[PlacedChord]:
    """X positions for chords over a line whose rendered text starts at ``line_left``.

    ``word_texts`` are the words exactly as rendered (case-transformed) and joined by
    single spaces. Two chords over the same word (a quick change) are pushed right so
    they never overlap.
    """
    placed: List[PlacedChord] = []
    next_free = None
    for chord in chords:
        i = word_index_for_time(segment, chord.start)
        prefix = " ".join(word_texts[:i]) + (" " if i else "")
        x = line_left + (measure(prefix) if prefix else 0)
        if next_free is not None and x < next_free:
            x = next_free
        placed.append(PlacedChord(chord=chord, word_index=i, x=x))
        next_free = x + chord_measure(chord.label) + min_spacing
    return placed


def chord_at(chords: Sequence[ChordChange], t: float) -> Optional[ChordChange]:
    for c in chords:
        if c.start <= t < c.end:
            return c
    return None
