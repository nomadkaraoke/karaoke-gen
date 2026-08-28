"""Deterministic auto-correct suggestion generators (no LLM).

Mechanical residual-edit classes from the 2026-08 20-job replay-review corpus,
emitted as ordinary suggestions so they flow through the exact same paths as
the LLM ones: shown in the review UI, auto-applied on load, applied server-side
by the auto-approval executor, counted by the scorer's gap-coverage signal, and
cached alongside the LLM output.

Pattern 4 — leading-connective over-insertion: AudioShake is too eager to
insert a short connective ("And", "Oh", …) at a segment start. When that word
is a gap word (no confident reference match) and no reference source's reading
of the gap supports it, emit a delete (+ re-capitalize the next word).

Pattern 5 — reference-majority resolution: when a gap's transcription contains
an implausible proper noun (capitalized mid-line token absent from every
reference source) and >=2/3 of the reference sources agree on the same reading
for that gap, replace the gap with the majority reading. The red-flag token is
REQUIRED, not just preferred: the corpus shows plain 2/3-majority gaps that the
human deliberately left alone (Miguel 6d0640fa "though"->"dog" and an
explicit-lyrics rewrite), while the one edit he did make ("yo Mick," ->
"Come here,") had the proper-noun signature.

Everything here is pure and conservative: no evidence -> no suggestion.
"""
from __future__ import annotations

import difflib
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.services.auto_approval.scorer import _is_vocalization_token

logger = logging.getLogger(__name__)

# Short connectives/interjections AudioShake over-inserts at segment starts —
# exactly the set documented in the corpus (Pattern 4: And / But / So / Oh / A).
# Deliberately NOT the near-misses like "ah"/"yeah": those double as
# vocalization tokens, where deleting the leading word is a musical judgement.
# The reference check below does the real gating; this list only scopes
# which leading words are candidates.
LEADING_CONNECTIVES = frozenset({"and", "but", "so", "oh", "a"})

# P4/P5 confidences: high enough to auto-apply, below full LLM consensus so a
# conflicting multi-model AI suggestion wins its conflict group.
P4_CONFIDENCE = 0.9
P5_CONFIDENCE = 0.85

# P5 size guards: never rewrite long spans deterministically.
P5_MAX_GAP_WORDS = 6
P5_MAX_READING_WORDS = 8
# A red-flag token this similar to some reference token is a spelling/
# transliteration variant ("Crick"~"Cricket", "Projectorinsky"~
# "Projektorinski"), not an implausible name. The right fix there is the
# LLM's (it handles proper-noun spelling well); the gap alignment is also
# often skewed in exactly these cases, so the majority "reading" is junk
# (corpus 507513ba aligned gap "Crick" to reference "and").
P5_SPELLING_VARIANT_RATIO = 0.75

_TOKEN_NORM_RE = re.compile(r"^[^\w]+|[^\w]+$")


def _norm(text: str) -> str:
    return _TOKEN_NORM_RE.sub("", (text or "").lower())


def _norm_join(texts: List[str]) -> str:
    return " ".join(t for t in (_norm(x) for x in texts) if t)


def _ref_word_streams(
    reference_lyrics: Dict[str, Any],
) -> Dict[str, Tuple[List[str], Dict[str, int]]]:
    """Per source: (ordered word texts, word_id -> position)."""
    streams: Dict[str, Tuple[List[str], Dict[str, int]]] = {}
    for source, ref in (reference_lyrics or {}).items():
        texts: List[str] = []
        index: Dict[str, int] = {}
        for seg in ref.get("segments") or []:
            for w in seg.get("words") or []:
                wid = w.get("id")
                if wid:
                    index[wid] = len(texts)
                texts.append(w.get("text") or "")
        streams[source] = (texts, index)
    return streams


def _gap_readings(
    gap: Dict[str, Any],
    anchors_by_id: Dict[str, Dict[str, Any]],
    streams: Dict[str, Tuple[List[str], Dict[str, int]]],
) -> Dict[str, List[str]]:
    """Each source's reading of the gap (raw word texts).

    Prefer the gap's own per-source alignment. When a source aligned neither
    gap word (empty list — common), derive the reading from the reference
    stream around the surrounding anchors: the words strictly between the
    anchors when both aligned for that source, else the same-count window
    after the preceding (or before the following) anchor. A source with no
    usable anchor alignment contributes no reading.
    """
    n = len(gap.get("transcribed_word_ids") or [])
    pre = anchors_by_id.get(gap.get("preceding_anchor_id") or "")
    fol = anchors_by_id.get(gap.get("following_anchor_id") or "")
    readings: Dict[str, List[str]] = {}
    for source, (texts, index) in streams.items():
        aligned_ids = (gap.get("reference_word_ids") or {}).get(source) or []
        aligned = [texts[index[i]] for i in aligned_ids if i in index]
        if aligned:
            readings[source] = aligned
            continue
        pre_ids = ((pre or {}).get("reference_word_ids") or {}).get(source) or []
        fol_ids = ((fol or {}).get("reference_word_ids") or {}).get(source) or []
        lo = index[pre_ids[-1]] + 1 if pre_ids and pre_ids[-1] in index else None
        hi = index[fol_ids[0]] if fol_ids and fol_ids[0] in index else None
        if lo is not None and hi is not None:
            if lo <= hi <= lo + n + 4:
                readings[source] = texts[lo:hi]
        elif lo is not None:
            readings[source] = texts[lo : lo + n]
        elif hi is not None:
            readings[source] = texts[max(0, hi - n) : hi]
    return readings


def _word_maps(
    segments: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """(word_id -> word dict, word_id -> segment_id) from the CURRENT segments."""
    words: Dict[str, Dict[str, Any]] = {}
    seg_of: Dict[str, str] = {}
    for seg in segments or []:
        for w in seg.get("words") or []:
            wid = w.get("id")
            if wid:
                words[wid] = w
                seg_of[wid] = seg.get("id") or ""
    return words, seg_of


def _make_suggestion(
    *,
    op: str,
    word_ids: List[str],
    segment_ids: List[str],
    original_text: str,
    new_text: str,
    reason: str,
    category: str,
    confidence: float,
) -> Dict[str, Any]:
    """A suggestion dict shaped exactly like the LLM ones after validation."""
    return {
        "id": str(uuid.uuid4()),
        "op": op,
        "word_ids": word_ids,
        "segment_ids": segment_ids,
        "original_text": original_text,
        "new_text": new_text,
        "reason": reason,
        "category": category,
        "confidence": confidence,
        "models": ["deterministic"],
        "consensus": 1,
        "total_models": 1,
        "conflict_group": None,
    }


def _capitalize(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1 :]
    return text


def leading_connective_suggestions(
    segments: List[Dict[str, Any]],
    correction_data: Dict[str, Any],
    streams: Dict[str, Tuple[List[str], Dict[str, int]]],
) -> List[Dict[str, Any]]:
    """Pattern 4: delete an unsupported leading connective, re-capitalize next word."""
    gaps = correction_data.get("gap_sequences") or []
    anchors_by_id = {
        a.get("id"): a for a in correction_data.get("anchor_sequences") or [] if a.get("id")
    }
    gap_of_word: Dict[str, Dict[str, Any]] = {}
    for gap in gaps:
        for wid in gap.get("transcribed_word_ids") or []:
            gap_of_word[wid] = gap

    out: List[Dict[str, Any]] = []
    for seg in segments or []:
        words = seg.get("words") or []
        if len(words) < 3:
            continue
        first = words[0]
        wid = first.get("id")
        token = _norm(first.get("text") or "")
        if not wid or token not in LEADING_CONNECTIVES:
            continue
        gap = gap_of_word.get(wid)
        if gap is None:
            # A confidently reference-anchored leading word is legitimate.
            continue
        if _is_vocalization_token(words[1].get("text") or ""):
            # "Oh- whoa, ..." — a vocalization run, not an over-inserted
            # connective before a sentence; grouping there is musical judgement.
            continue
        readings = _gap_readings(gap, anchors_by_id, streams)
        if not readings:
            continue  # no reference evidence either way -> leave it alone
        if any(token in {_norm(t) for t in reading} for reading in readings.values()):
            continue  # some source supports the connective at this position
        seg_id = seg.get("id") or ""
        out.append(
            _make_suggestion(
                op="delete",
                word_ids=[wid],
                segment_ids=[seg_id],
                original_text=first.get("text") or "",
                new_text="",
                reason=(
                    f'Removed leading "{first.get("text", "").strip()}" — the '
                    "transcription inserted it at the line start but no reference "
                    "source has it at this position"
                ),
                category="mishearing",
                confidence=P4_CONFIDENCE,
            )
        )
        nxt = words[1]
        nxt_text = nxt.get("text") or ""
        capitalized = _capitalize(nxt_text)
        if nxt.get("id") and capitalized != nxt_text:
            out.append(
                _make_suggestion(
                    op="replace",
                    word_ids=[nxt.get("id")],
                    segment_ids=[seg_id],
                    original_text=nxt_text,
                    new_text=capitalized,
                    reason="Capitalized the new first word of the line",
                    category="formatting",
                    confidence=P4_CONFIDENCE,
                )
            )
    return out


def _majority_reading(
    readings: Dict[str, List[str]], total_sources: int
) -> Optional[List[str]]:
    """The reading >=2/3 of ALL sources agree on (>=2 absolute), else None."""
    votes: Dict[str, List[List[str]]] = {}
    for reading in readings.values():
        if reading:
            votes.setdefault(_norm_join(reading), []).append(reading)
    if not votes:
        return None
    key, group = max(votes.items(), key=lambda kv: len(kv[1]))
    if not key or len(group) < 2 or len(group) * 3 < total_sources * 2:
        return None
    return group[0]


def reference_majority_suggestions(
    segments: List[Dict[str, Any]],
    correction_data: Dict[str, Any],
    streams: Dict[str, Tuple[List[str], Dict[str, int]]],
) -> List[Dict[str, Any]]:
    """Pattern 5: replace an implausible-proper-noun gap with the >=2/3 majority reading."""
    words_by_id, seg_of = _word_maps(segments)
    anchors_by_id = {
        a.get("id"): a for a in correction_data.get("anchor_sequences") or [] if a.get("id")
    }
    all_ref_tokens = {
        _norm(t) for texts, _ in streams.values() for t in texts if _norm(t)
    }
    seg_first_word_ids = {
        (seg.get("words") or [{}])[0].get("id") for seg in segments or []
    }
    total_sources = len(streams)

    out: List[Dict[str, Any]] = []
    for gap in correction_data.get("gap_sequences") or []:
        gap_ids = gap.get("transcribed_word_ids") or []
        if not gap_ids or len(gap_ids) > P5_MAX_GAP_WORDS:
            continue
        gap_words = [words_by_id.get(i) for i in gap_ids]
        if any(w is None for w in gap_words):
            continue  # a gap word no longer exists in the working segments
        # Red flag (REQUIRED): a capitalized mid-line token no reference knows.
        # A token merely SIMILAR to a reference token is a spelling variant,
        # not an implausible name — that's the LLM's fix, not ours.
        red_flag = None
        for wid, w in zip(gap_ids, gap_words):
            text = (w.get("text") or "").strip()
            norm = _norm(text)
            if not (
                text
                and text[0].isupper()
                and wid not in seg_first_word_ids
                and norm
                and norm not in all_ref_tokens
            ):
                continue
            if any(
                difflib.SequenceMatcher(None, norm, ref).ratio()
                >= P5_SPELLING_VARIANT_RATIO
                for ref in all_ref_tokens
            ):
                continue
            red_flag = text
            break
        if red_flag is None:
            continue
        readings = _gap_readings(gap, anchors_by_id, streams)
        majority = _majority_reading(readings, total_sources)
        if majority is None or len(majority) > P5_MAX_READING_WORDS:
            continue
        original = [w.get("text") or "" for w in gap_words]
        if _norm_join(majority) == _norm_join(original):
            continue
        segment_ids = sorted({seg_of.get(i, "") for i in gap_ids})
        out.append(
            _make_suggestion(
                op="replace",
                word_ids=list(gap_ids),
                segment_ids=segment_ids,
                original_text=" ".join(original),
                new_text=" ".join(majority).strip(),
                reason=(
                    f'Transcription "{" ".join(original)}" contains "{red_flag}", '
                    "which no reference source has; the majority of reference "
                    f'sources read this as "{" ".join(majority).strip()}"'
                ),
                category="mishearing",
                confidence=P5_CONFIDENCE,
            )
        )
    return out


def deterministic_suggestions(
    segments: List[Dict[str, Any]],
    reference_lyrics: Dict[str, Any],
    correction_data: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """All deterministic suggestions for the current working segments.

    Needs the job's ``corrections.json`` dict for gap/anchor alignment; with
    none available (or on any internal error) it returns [] — deterministic
    fixes are an enhancement, never a failure mode.
    """
    if not correction_data or not reference_lyrics:
        return []
    try:
        streams = _ref_word_streams(reference_lyrics)
        if not streams:
            return []
        return leading_connective_suggestions(
            segments, correction_data, streams
        ) + reference_majority_suggestions(segments, correction_data, streams)
    except Exception:
        logger.warning("deterministic suggestion generation failed", exc_info=True)
        return []
