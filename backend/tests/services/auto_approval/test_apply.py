"""Tests for the server-side AI-suggestion apply engine.

This is a port of the review UI's auto-apply (autoCorrectApply.ts /
autoCorrectConflicts.ts); these tests pin the port to the frontend's observable
behaviour: op semantics, timing distribution, word flags, conflict resolution.
"""
from __future__ import annotations

from typing import Any, Dict, List

from backend.services.auto_approval.apply import (
    apply_all_suggestions,
    apply_suggestion,
    find_suspicious_duplicates,
    is_suggestion_stale,
    pick_accept_all_winners,
)


def _word(wid: str, text: str, start: float, end: float) -> Dict[str, Any]:
    return {"id": wid, "text": text, "start_time": start, "end_time": end, "confidence": 0.9}


def _segments() -> List[Dict[str, Any]]:
    return [
        {
            "id": "s0",
            "text": "I am a fire",
            "words": [
                _word("w0", "I", 0.0, 0.3),
                _word("w1", "am", 0.3, 0.6),
                _word("w2", "a", 0.6, 0.9),
                _word("w3", "fire", 0.9, 1.5),
            ],
            "start_time": 0.0,
            "end_time": 1.5,
        },
        {
            "id": "s1",
            "text": "gasoline",
            "words": [_word("w4", "gasoline", 2.0, 2.8)],
            "start_time": 2.0,
            "end_time": 2.8,
        },
    ]


def _sug(op: str, word_ids: List[str], new_text: str = "", **kw: Any) -> Dict[str, Any]:
    return {
        "id": kw.pop("id", "sug1"),
        "op": op,
        "word_ids": word_ids,
        "segment_ids": ["s0"],
        "original_text": kw.pop("original_text", ""),
        "new_text": new_text,
        "confidence": kw.pop("confidence", 0.9),
        "consensus": kw.pop("consensus", 2),
        "total_models": kw.pop("total_models", 2),
        "conflict_group": kw.pop("conflict_group", None),
    }


# --- replace ---

def test_replace_single_word_inherits_timing_and_flags() -> None:
    result = apply_suggestion(_segments(), _sug("replace", ["w1"], "was", original_text="am"))
    assert result is not None
    words = result[0]["words"]
    assert [w["text"] for w in words] == ["I", "was", "a", "fire"]
    new = words[1]
    assert new["start_time"] == 0.3 and new["end_time"] == 0.6
    assert new["ai_corrected"] is True
    assert new["created_during_correction"] is True
    assert new["timing_estimated"] is False  # single word inherits a real range
    assert new["original_text"] == "am"
    assert result[0]["text"] == "I was a fire"


def test_replace_multi_word_splits_timing_and_marks_estimated() -> None:
    result = apply_suggestion(_segments(), _sug("replace", ["w3"], "fire, you're"))
    assert result is not None
    words = result[0]["words"]
    assert [w["text"] for w in words] == ["I", "am", "a", "fire,", "you're"]
    a, b = words[3], words[4]
    assert a["start_time"] == 0.9 and abs(a["end_time"] - 1.2) < 1e-9
    assert abs(b["start_time"] - 1.2) < 1e-9 and b["end_time"] == 1.5
    assert a["timing_estimated"] is True and b["timing_estimated"] is True
    assert "original_text" in a and "original_text" not in b


# --- insert_after ---

def test_insert_after_uses_gap_to_next_word() -> None:
    result = apply_suggestion(_segments(), _sug("insert_after", ["w3"], "burning"))
    assert result is not None
    words = result[0]["words"]
    assert [w["text"] for w in words] == ["I", "am", "a", "fire", "burning"]
    new = words[4]
    assert new["start_time"] == 1.5  # anchor end
    assert new["timing_estimated"] is True


# --- delete ---

def test_delete_word_updates_segment_text_and_timing() -> None:
    result = apply_suggestion(_segments(), _sug("delete", ["w0"]))
    assert result is not None
    seg = result[0]
    assert seg["text"] == "am a fire"
    assert seg["start_time"] == 0.3


def test_delete_last_word_removes_segment() -> None:
    result = apply_suggestion(_segments(), _sug("delete", ["w4"]))
    assert result is not None
    assert len(result) == 1
    assert result[0]["id"] == "s0"


# --- staleness ---

def test_missing_word_is_stale() -> None:
    assert is_suggestion_stale(_segments(), _sug("replace", ["nope"], "x"))
    assert apply_suggestion(_segments(), _sug("replace", ["nope"], "x")) is None


def test_non_contiguous_words_are_stale() -> None:
    assert is_suggestion_stale(_segments(), _sug("replace", ["w0", "w2"], "x"))


# --- conflict groups / accept-all ---

def test_conflict_group_winner_by_consensus_then_confidence() -> None:
    s1 = _sug("replace", ["w1"], "was", id="a", conflict_group="g1", consensus=1, confidence=0.95)
    s2 = _sug("replace", ["w1"], "is", id="b", conflict_group="g1", consensus=2, confidence=0.7)
    s3 = _sug("replace", ["w0"], "You", id="c")
    assert pick_accept_all_winners([s1, s2, s3]) == ["b", "c"]


def test_apply_all_resolves_conflicts_and_reports() -> None:
    s1 = _sug("replace", ["w1"], "was", id="a", conflict_group="g1", consensus=1)
    s2 = _sug("replace", ["w1"], "is", id="b", conflict_group="g1", consensus=2)
    out = apply_all_suggestions(_segments(), [s1, s2])
    assert out["applied_ids"] == ["b"]
    assert out["rejected_ids"] == ["a"]
    assert out["stale_ids"] == []
    assert [w["text"] for w in out["segments"][0]["words"]] == ["I", "is", "a", "fire"]


def test_apply_all_flags_stale() -> None:
    out = apply_all_suggestions(_segments(), [_sug("replace", ["gone"], "x", id="a")])
    assert out["stale_ids"] == ["a"]
    assert out["applied_ids"] == []


def test_p1_self_conflict_produces_detectable_duplicate() -> None:
    # Corpus f6439692: overlapping suggestions (conflict_group=null) both add
    # "you're" -> "fire, you're you're gasoline". The apply engine mirrors the
    # UI (both apply); find_suspicious_duplicates must catch the result.
    s1 = _sug("insert_after", ["w3"], "you're", id="a")
    s2 = _sug("replace", ["w3"], "fire, you're", id="b")
    out = apply_all_suggestions(_segments(), [s1, s2])
    dups = find_suspicious_duplicates(out["segments"], {})
    assert "you're" in dups


def test_duplicates_supported_by_reference_are_ok() -> None:
    segments = [{
        "id": "s0", "text": "hey hey you",
        "words": [_word("w0", "hey", 0.0, 0.2), _word("w1", "hey", 0.2, 0.4),
                  _word("w2", "you", 0.4, 0.6)],
        "start_time": 0.0, "end_time": 0.6,
    }]
    refs = {"genius": {"segments": [{"text": "Hey hey you"}]}}
    assert find_suspicious_duplicates(segments, refs) == []
    assert find_suspicious_duplicates(segments, {}) == ["hey"]
