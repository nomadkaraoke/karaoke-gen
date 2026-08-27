"""Reconstruct exactly what a human changed during lyrics review.

Diffs the raw transcription (``corrections.json`` -> ``corrected_segments``) against
the reviewer's final result (``corrections_updated.json`` -> ``corrected_segments``).
Because production runs with auto-correction disabled, the original ``corrected_segments``
IS the raw AudioShake transcription, so this diff is *exactly* the set of manual edits
Andrew made — the ground truth the recording corpus is built on.

Words carry stable ``id``s, BUT the review UI assigns a NEW id when a word's text is
replaced (rather than mutating in place). It preserves the word's start/end time and line,
though — so a text correction shows up as a delete+insert at the *same timing*. We recover
those as ``replacements`` by pairing a deletion with an insertion sharing (line, start, end).

The diff is keyed on word id and yields:
- ``replacements``    — a word swapped for another at the same time/line (`Carl` → `Karl`);
                        the common case for mis-transcribed words
- ``text_edits``      — same id, changed text (in-place edit; rare)
- ``timing_changes``  — same id, moved start/end (a sync nudge)
- ``deletions``       — a word removed with no same-timing replacement (adlib/dupe/etc.)
- ``insertions``      — a word added with no same-timing original
- ``segmentation``    — line count changed or a word moved between lines (split/merge/rewrap)

See ``docs/archive/2026-08-25-full-auto-review-design.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Minimum start/end move (seconds) to count as a deliberate timing change, not float noise.
TIMING_EPSILON = 0.005
# Max start/end difference (seconds) to treat a delete+insert as an in-place replacement.
REPLACE_TIMING_EPSILON = 0.02


@dataclass
class TextEdit:
    word_id: str
    original_text: str
    final_text: str
    segment_index: int


@dataclass
class TimingChange:
    word_id: str
    text: str
    original_start: Optional[float]
    original_end: Optional[float]
    final_start: Optional[float]
    final_end: Optional[float]
    start_delta: Optional[float]
    end_delta: Optional[float]


@dataclass
class Replacement:
    """A word swapped for another at the same time/line (mis-transcription fix)."""

    original_word_id: str
    final_word_id: str
    original_text: str
    final_text: str
    segment_index: int
    start_time: Optional[float]


@dataclass
class WordRef:
    word_id: str
    text: str
    segment_index: int


@dataclass
class SegmentMove:
    """A word that stayed but moved to a different line (split/merge/rewrap)."""

    word_id: str
    text: str
    original_segment_index: int
    final_segment_index: int


@dataclass
class LyricsDiff:
    has_changes: bool = False
    total_changes: int = 0
    original_segment_count: int = 0
    final_segment_count: int = 0
    original_word_count: int = 0
    final_word_count: int = 0
    segmentation_changed: bool = False
    replacements: List[Replacement] = field(default_factory=list)
    text_edits: List[TextEdit] = field(default_factory=list)
    timing_changes: List[TimingChange] = field(default_factory=list)
    deletions: List[WordRef] = field(default_factory=list)
    insertions: List[WordRef] = field(default_factory=list)
    segment_moves: List[SegmentMove] = field(default_factory=list)
    # The UI-recorded WordCorrection list from corrections_updated.json (supplementary).
    ui_recorded_corrections: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _index_words(segments: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """Return {word_id: {text,start,end,segment_index}} and total word count."""
    by_id: Dict[str, Dict[str, Any]] = {}
    count = 0
    for seg_index, seg in enumerate(segments or []):
        for word in seg.get("words") or []:
            wid = word.get("id")
            count += 1
            if not wid:
                continue
            by_id[wid] = {
                "text": (word.get("text") or "").strip(),
                "start": word.get("start_time"),
                "end": word.get("end_time"),
                "segment_index": seg_index,
            }
    return by_id, count


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(b - a, 4)


def compute_lyrics_diff(
    original_data: Dict[str, Any],
    updated_data: Optional[Dict[str, Any]],
) -> LyricsDiff:
    """Diff raw transcription against the reviewer's final result.

    ``original_data``: parsed ``corrections.json``.
    ``updated_data``:  parsed ``corrections_updated.json`` (or ``None`` if the job was
                       never edited — yields an empty diff).
    """
    orig_segments = original_data.get("corrected_segments") or []
    orig_by_id, orig_count = _index_words(orig_segments)

    diff = LyricsDiff(
        original_segment_count=len(orig_segments),
        original_word_count=orig_count,
    )

    if not updated_data or "corrected_segments" not in updated_data:
        # No human edits recorded — final == raw transcription.
        diff.final_segment_count = len(orig_segments)
        diff.final_word_count = orig_count
        return diff

    final_segments = updated_data.get("corrected_segments") or []
    final_by_id, final_count = _index_words(final_segments)
    diff.final_segment_count = len(final_segments)
    diff.final_word_count = final_count
    diff.ui_recorded_corrections = len(updated_data.get("corrections") or [])

    orig_ids = set(orig_by_id)
    final_ids = set(final_by_id)

    deleted_ids = sorted(orig_ids - final_ids)
    inserted_ids = sorted(final_ids - orig_ids)

    # Pair delete+insert at the same line & timing into replacements (mis-transcription
    # fixes: the UI re-keys the word but keeps its slot). One-to-one, greedy.
    unmatched_inserts = list(inserted_ids)
    for did in deleted_ids:
        o = orig_by_id[did]
        match = None
        for iid in unmatched_inserts:
            f = final_by_id[iid]
            if (
                o["segment_index"] == f["segment_index"]
                and o["start"] is not None and f["start"] is not None
                and abs((o["start"] or 0) - (f["start"] or 0)) <= REPLACE_TIMING_EPSILON
                and abs((o["end"] or 0) - (f["end"] or 0)) <= REPLACE_TIMING_EPSILON
            ):
                match = iid
                break
        if match is not None:
            f = final_by_id[match]
            unmatched_inserts.remove(match)
            diff.replacements.append(
                Replacement(
                    original_word_id=did,
                    final_word_id=match,
                    original_text=o["text"],
                    final_text=f["text"],
                    segment_index=f["segment_index"],
                    start_time=f["start"],
                )
            )
        else:
            diff.deletions.append(WordRef(did, o["text"], o["segment_index"]))

    for iid in unmatched_inserts:
        w = final_by_id[iid]
        diff.insertions.append(WordRef(iid, w["text"], w["segment_index"]))

    for wid in sorted(orig_ids & final_ids):
        o = orig_by_id[wid]
        f = final_by_id[wid]

        if o["text"] != f["text"]:
            diff.text_edits.append(
                TextEdit(wid, o["text"], f["text"], f["segment_index"])
            )

        start_d = _delta(o["start"], f["start"])
        end_d = _delta(o["end"], f["end"])
        if (start_d is not None and abs(start_d) > TIMING_EPSILON) or (
            end_d is not None and abs(end_d) > TIMING_EPSILON
        ):
            diff.timing_changes.append(
                TimingChange(
                    word_id=wid,
                    text=f["text"],
                    original_start=o["start"],
                    original_end=o["end"],
                    final_start=f["start"],
                    final_end=f["end"],
                    start_delta=start_d,
                    end_delta=end_d,
                )
            )

        if o["segment_index"] != f["segment_index"]:
            diff.segment_moves.append(
                SegmentMove(wid, f["text"], o["segment_index"], f["segment_index"])
            )

    diff.segmentation_changed = bool(
        diff.segment_moves
        or diff.original_segment_count != diff.final_segment_count
    )
    diff.total_changes = (
        len(diff.replacements)
        + len(diff.text_edits)
        + len(diff.timing_changes)
        + len(diff.deletions)
        + len(diff.insertions)
        + len(diff.segment_moves)
    )
    diff.has_changes = diff.total_changes > 0
    return diff
