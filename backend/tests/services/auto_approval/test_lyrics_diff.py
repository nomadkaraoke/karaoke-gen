"""Tests for the review-diff reconstruction."""
from __future__ import annotations

from typing import Any, Dict, List

from backend.services.auto_approval.lyrics_diff import compute_lyrics_diff


def _w(wid: str, text: str, start: float, end: float) -> Dict[str, Any]:
    return {"id": wid, "text": text, "start_time": start, "end_time": end}


def _seg(sid: str, words: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": sid,
        "text": " ".join(w["text"] for w in words),
        "words": words,
        "start_time": words[0]["start_time"] if words else 0.0,
        "end_time": words[-1]["end_time"] if words else 0.0,
    }


def _doc(segments: List[Dict[str, Any]], corrections: List[Any] | None = None) -> Dict[str, Any]:
    return {"corrected_segments": segments, "corrections": corrections or []}


def test_no_updated_data_means_no_changes() -> None:
    orig = _doc([_seg("s0", [_w("w0", "hello", 0.0, 0.5)])])
    diff = compute_lyrics_diff(orig, None)
    assert diff.has_changes is False
    assert diff.total_changes == 0
    assert diff.final_word_count == 1


def test_text_edit_detected() -> None:
    orig = _doc([_seg("s0", [_w("w0", "helo", 0.0, 0.5), _w("w1", "wrld", 0.5, 1.0)])])
    final = _doc([_seg("s0", [_w("w0", "hello", 0.0, 0.5), _w("w1", "world", 0.5, 1.0)])])
    diff = compute_lyrics_diff(orig, final)
    assert diff.total_changes == 2
    assert {e.final_text for e in diff.text_edits} == {"hello", "world"}
    assert not diff.timing_changes


def test_replacement_paired_by_timing() -> None:
    # UI re-keys a corrected word but keeps its start/end/line -> should pair as replacement.
    orig = _doc([_seg("s0", [_w("wA", "Carl", 231.18, 231.80)])])
    final = _doc([_seg("s0", [_w("wB", "Karl", 231.18, 231.80)])])
    diff = compute_lyrics_diff(orig, final)
    assert diff.total_changes == 1
    assert len(diff.replacements) == 1
    assert not diff.deletions and not diff.insertions
    r = diff.replacements[0]
    assert (r.original_text, r.final_text) == ("Carl", "Karl")
    assert r.original_word_id == "wA" and r.final_word_id == "wB"


def test_replacement_not_paired_when_timing_differs() -> None:
    # Different timing -> genuine delete + insert, not a replacement.
    orig = _doc([_seg("s0", [_w("wA", "Carl", 231.18, 231.80)])])
    final = _doc([_seg("s0", [_w("wB", "Karl", 10.0, 10.5)])])
    diff = compute_lyrics_diff(orig, final)
    assert not diff.replacements
    assert [d.text for d in diff.deletions] == ["Carl"]
    assert [i.text for i in diff.insertions] == ["Karl"]


def test_timing_change_respects_epsilon() -> None:
    orig = _doc([_seg("s0", [_w("w0", "hey", 1.000, 1.500)])])
    # start moved 0.2s (significant), end moved 0.001s (noise, below epsilon)
    final = _doc([_seg("s0", [_w("w0", "hey", 1.200, 1.501)])])
    diff = compute_lyrics_diff(orig, final)
    assert len(diff.timing_changes) == 1
    tc = diff.timing_changes[0]
    assert tc.start_delta == 0.2
    assert not diff.text_edits


def test_deletion_and_insertion() -> None:
    orig = _doc([_seg("s0", [_w("w0", "one", 0.0, 0.5), _w("w1", "two", 0.5, 1.0)])])
    final = _doc([_seg("s0", [_w("w0", "one", 0.0, 0.5), _w("w2", "three", 1.0, 1.5)])])
    diff = compute_lyrics_diff(orig, final)
    assert [d.text for d in diff.deletions] == ["two"]
    assert [i.text for i in diff.insertions] == ["three"]
    assert diff.total_changes == 2


def test_split_detected_as_segmentation_change() -> None:
    orig = _doc([_seg("s0", [_w("w0", "a", 0.0, 0.5), _w("w1", "b", 0.5, 1.0)])])
    # Same words, now on two lines -> segment move for w1 + segment count change.
    final = _doc([
        _seg("s0", [_w("w0", "a", 0.0, 0.5)]),
        _seg("s1", [_w("w1", "b", 0.5, 1.0)]),
    ])
    diff = compute_lyrics_diff(orig, final)
    assert diff.segmentation_changed is True
    assert len(diff.segment_moves) == 1
    assert diff.segment_moves[0].word_id == "w1"
    assert diff.original_segment_count == 1
    assert diff.final_segment_count == 2


def test_clean_unedited_job_yields_empty_diff() -> None:
    seg = [_seg("s0", [_w("w0", "same", 0.0, 0.5)])]
    diff = compute_lyrics_diff(_doc(seg), _doc(seg))
    assert diff.has_changes is False
    assert diff.total_changes == 0
