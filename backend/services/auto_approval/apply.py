"""Server-side application of AI auto-correct suggestions.

Faithful port of the review UI's auto-apply (frontend
``lib/lyrics-review/utils/autoCorrectApply.ts`` + ``autoCorrectConflicts.ts`` +
``useAutoCorrect.acceptAll``): the UI auto-applies every pending suggestion on
load (conflict groups resolved by consensus → confidence), so a human reviewer
always starts from the post-AI state. When a job is auto-approved without a
human, this module produces that same post-AI state server-side.

Everything here is pure (dict in, dict out); any anomaly is reported so the
caller can fall back to human review rather than ship a suspect result.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

Segment = Dict[str, Any]
Suggestion = Dict[str, Any]

_WORD_SPLIT_RE = re.compile(r"\s+")
_TOKEN_NORM_RE = re.compile(r"^[^\w]+|[^\w]+$")


def _new_word_id() -> str:
    return uuid.uuid4().hex[:8]


def _rebuild_text(words: List[Dict[str, Any]]) -> str:
    return " ".join(w.get("text", "") for w in words)


def pick_accept_all_winners(suggestions: List[Suggestion]) -> List[str]:
    """Winner ids for accept-all: one per conflict group (highest consensus,
    then confidence); non-conflicting suggestions all win. Original order."""
    best_of_group: Dict[str, Suggestion] = {}
    for s in suggestions:
        group = s.get("conflict_group")
        if not group:
            continue
        current = best_of_group.get(group)
        if (
            current is None
            or (s.get("consensus") or 0) > (current.get("consensus") or 0)
            or (
                (s.get("consensus") or 0) == (current.get("consensus") or 0)
                and (s.get("confidence") or 0) > (current.get("confidence") or 0)
            )
        ):
            best_of_group[group] = s
    return [
        s["id"]
        for s in suggestions
        if not s.get("conflict_group")
        or best_of_group.get(s["conflict_group"], {}).get("id") == s.get("id")
    ]


def _find_segment_with_words(
    segments: List[Segment], word_ids: List[str]
) -> Optional[Tuple[int, List[int]]]:
    if not word_ids:
        # all([]) is vacuously True — an empty/missing word_ids must read as
        # not-found, not as "matches the first segment".
        return None
    for i, seg in enumerate(segments):
        words = seg.get("words") or []
        indices = []
        for wid in word_ids:
            idx = next((j for j, w in enumerate(words) if w.get("id") == wid), -1)
            indices.append(idx)
        if all(x >= 0 for x in indices):
            return i, indices
    return None


def is_suggestion_stale(segments: List[Segment], suggestion: Suggestion) -> bool:
    """Stale when the target words no longer exist in one segment, contiguously."""
    found = _find_segment_with_words(segments, suggestion.get("word_ids") or [])
    if not found:
        return True
    _, indices = found
    s = sorted(indices)
    return any(s[i] != s[i - 1] + 1 for i in range(1, len(s)))


def _distribute_timings(
    n: int, start: Optional[float], end: Optional[float]
) -> List[Tuple[Optional[float], Optional[float]]]:
    if n <= 0:
        return []
    if start is None or end is None or end <= start:
        return [(start, end)] * n
    step = (end - start) / n
    return [(start + i * step, start + (i + 1) * step) for i in range(n)]


def _copy_segments(segments: List[Segment]) -> List[Segment]:
    return [{**s, "words": [dict(w) for w in (s.get("words") or [])]} for s in segments]


def apply_suggestion(
    segments: List[Segment], suggestion: Suggestion
) -> Optional[List[Segment]]:
    """Apply one suggestion; returns new segments or None when stale."""
    if is_suggestion_stale(segments, suggestion):
        return None
    found = _find_segment_with_words(segments, suggestion.get("word_ids") or [])
    if not found:
        return None
    seg_index, word_indices = found
    span_start = min(word_indices)
    span_end = max(word_indices)

    result = _copy_segments(segments)
    seg = result[seg_index]
    words = seg["words"]
    op = suggestion.get("op")
    new_text = suggestion.get("new_text") or ""

    if op == "insert_after":
        anchor = words[span_end]
        nxt = words[span_end + 1] if span_end + 1 < len(words) else None
        new_texts = [t for t in _WORD_SPLIT_RE.split(new_text) if t]
        window_start = anchor.get("end_time")
        if (
            nxt is not None
            and nxt.get("start_time") is not None
            and window_start is not None
            and nxt["start_time"] > window_start
        ):
            window_end: Optional[float] = nxt["start_time"]
        elif window_start is not None:
            window_end = window_start + 0.5 * len(new_texts)
        else:
            window_end = None
        timings = _distribute_timings(len(new_texts), window_start, window_end)
        new_words = [
            {
                "id": _new_word_id(),
                "text": text,
                "start_time": t[0],
                "end_time": t[1],
                "confidence": 1,
                "created_during_correction": True,
                "ai_corrected": True,
                # Inserted words have no source transcription -> synthetic timing.
                "timing_estimated": True,
                "correction_span_id": suggestion.get("id"),
            }
            for text, t in zip(new_texts, timings)
        ]
        words[span_end + 1 : span_end + 1] = new_words
        seg["text"] = _rebuild_text(words)
        return result

    removed = words[span_start : span_end + 1]

    if op == "delete":
        del words[span_start : span_start + len(removed)]
        if not words:
            del result[seg_index]
        else:
            seg["text"] = _rebuild_text(words)
            seg["start_time"] = words[0].get("start_time")
            seg["end_time"] = words[-1].get("end_time")
        return result

    # replace
    new_texts = [t for t in _WORD_SPLIT_RE.split(new_text) if t]
    timings = _distribute_timings(
        len(new_texts),
        removed[0].get("start_time") if removed else None,
        removed[-1].get("end_time") if removed else None,
    )
    # >1 replacement word forces an even split of one range -> estimated timing.
    timing_estimated = len(new_texts) > 1
    original_span_text = suggestion.get("original_text") or _rebuild_text(removed)
    new_words = []
    for i, (text, t) in enumerate(zip(new_texts, timings)):
        w = {
            "id": _new_word_id(),
            "text": text,
            "start_time": t[0],
            "end_time": t[1],
            "confidence": 1,
            "created_during_correction": True,
            "ai_corrected": True,
            "timing_estimated": timing_estimated,
            "correction_span_id": suggestion.get("id"),
        }
        if i == 0:
            w["original_text"] = original_span_text
        new_words.append(w)
    words[span_start : span_start + len(removed)] = new_words
    seg["text"] = _rebuild_text(words)
    return result


def apply_all_suggestions(
    segments: List[Segment], suggestions: List[Suggestion]
) -> Dict[str, Any]:
    """Accept-all with conflict resolution, mirroring the UI's on-load auto-apply.

    Returns ``{"segments", "applied_ids", "rejected_ids", "stale_ids"}``.
    """
    winners = set(pick_accept_all_winners(suggestions))
    current = segments
    applied: List[str] = []
    rejected: List[str] = []
    stale: List[str] = []
    for s in suggestions:
        sid = s.get("id") or ""
        if sid not in winners:
            rejected.append(sid)
            continue
        result = apply_suggestion(current, s)
        if result is None:
            stale.append(sid)
            continue
        current = result
        applied.append(sid)
    return {
        "segments": current,
        "applied_ids": applied,
        "rejected_ids": rejected,
        "stale_ids": stale,
    }


def build_applied_segments(
    corrections: Dict[str, Any], ai_suggestions: Optional[List[Suggestion]]
) -> Dict[str, Any]:
    """Apply the AI suggestions to a corrections payload with the safety gates.

    Shared by the executor (auto-complete) and the review-gate pre-apply. Returns
    ``{"aborted": <reason>}`` on any anomaly (caller falls back to human review /
    client-side apply), else ``{"segments", "applied_ids", "rejected_ids"}``.
    """
    segments = corrections.get("corrected_segments") or []
    if not segments:
        return {"aborted": "no_segments"}

    result = apply_all_suggestions(segments, ai_suggestions or [])
    if result["stale_ids"]:
        # Nothing edits segments between generation and now, so staleness means
        # the cache doesn't match this corrections.json -> don't trust the apply.
        return {"aborted": f"stale_suggestions:{len(result['stale_ids'])}"}

    new_segments = result["segments"]
    if not new_segments or any(not (s.get("words") or []) for s in new_segments):
        return {"aborted": "empty_segments_after_apply"}

    duplicates = find_suspicious_duplicates(new_segments, corrections.get("reference_lyrics"))
    if duplicates:
        # P1 signature: overlapping suggestions doubled a word ("you're you're").
        return {"aborted": f"duplicate_words:{','.join(duplicates[:5])}"}

    return {
        "segments": new_segments,
        "applied_ids": result["applied_ids"],
        "rejected_ids": result["rejected_ids"],
    }


def _norm_token(text: str) -> str:
    return _TOKEN_NORM_RE.sub("", (text or "").lower())


def find_suspicious_duplicates(
    segments: List[Segment], reference_lyrics: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Adjacent identical words that no reference supports (P1 self-conflict
    signature — e.g. two overlapping suggestions both adding "you're" produced
    "fire, you're you're gasoline"). Returns the offending tokens."""
    ref_texts: List[str] = []
    for ref in (reference_lyrics or {}).values():
        for ref_seg in ref.get("segments") or []:
            ref_texts.append(" ".join(
                _norm_token(t) for t in _WORD_SPLIT_RE.split(ref_seg.get("text") or "") if t
            ))
    ref_blob = "\n".join(ref_texts)

    offenders: List[str] = []
    for seg in segments:
        words = seg.get("words") or []
        for i in range(1, len(words)):
            a = _norm_token(words[i - 1].get("text") or "")
            b = _norm_token(words[i].get("text") or "")
            if a and a == b:
                # A doubled word is fine when a reference genuinely repeats it.
                if f"{a} {a}" in ref_blob:
                    continue
                offenders.append(a)
    return offenders
