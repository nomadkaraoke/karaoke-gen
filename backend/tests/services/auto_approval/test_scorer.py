"""Tests for the auto-approvability scorer.

These fixtures mirror the shape of a production ``corrections.json`` built by the
uncorrected controller path (auto-correction disabled): ``corrections_made == 0``,
``confidence == 1.0``, but anchors/gaps/reference_lyrics still populated.
"""
from __future__ import annotations

from typing import Any, Dict, List

from backend.services.auto_approval.models import BackingVerdict, LyricsVerdict
from backend.services.auto_approval.scorer import (
    score_backing,
    score_job,
    score_lyrics,
)


def _word(wid: str, text: str) -> Dict[str, Any]:
    return {"id": wid, "text": text, "start_time": 0.0, "end_time": 0.5}


def _segments(n_words: int) -> List[Dict[str, Any]]:
    words = [_word(f"w{i}", f"word{i}") for i in range(n_words)]
    return [{"id": "s0", "text": " ".join(w["text"] for w in words),
             "words": words, "start_time": 0.0, "end_time": 10.0}]


def _corrections_json(
    n_words: int = 20,
    anchor_words: int = 20,
    gap_words: int = 0,
    synced: bool = True,
    sources: List[str] | None = None,
) -> Dict[str, Any]:
    sources = sources if sources is not None else ["spotify", "genius"]
    anchor_seq = [{"id": "a0", "transcribed_word_ids": [f"w{i}" for i in range(anchor_words)]}]
    gap_seq = (
        [{"id": "g0", "transcribed_word_ids": [f"w{i}" for i in range(anchor_words, anchor_words + gap_words)]}]
        if gap_words
        else []
    )
    reference_lyrics = {
        src: {"metadata": {"is_synced": synced}, "segments": [], "source": src}
        for src in sources
    }
    return {
        "corrected_segments": _segments(n_words),
        "corrections": [],
        "corrections_made": 0,
        "confidence": 1.0,  # the trap — must not drive AUTO
        "reference_lyrics": reference_lyrics,
        "anchor_sequences": anchor_seq,
        "gap_sequences": gap_seq,
        "metadata": {
            "correction_type": "none",
            "reason": "correction_disabled",
            "agentic_routing": "disabled",
            "total_words": n_words,
            "anchor_sequences_count": len(anchor_seq),
            "gap_sequences_count": len(gap_seq),
        },
    }


# --- Lyrics ---

def test_synced_perfect_is_auto() -> None:
    res = score_lyrics(_corrections_json(anchor_words=20, gap_words=0, synced=True))
    assert res.verdict == LyricsVerdict.AUTO
    assert res.tier == "synced-perfect"
    assert res.signals.anchor_word_fraction == 1.0
    assert res.signals.uncorrected_gap_fraction == 0.0


def test_confidence_one_alone_does_not_trigger_auto() -> None:
    # No reference sources at all -> confidence 1.0 must NOT yield AUTO.
    data = _corrections_json(sources=[], anchor_words=0, gap_words=0)
    data["anchor_sequences"] = []
    res = score_lyrics(data)
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "no-reference"


def test_unsynced_reference_never_auto() -> None:
    res = score_lyrics(_corrections_json(anchor_words=20, gap_words=0, synced=False))
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "near-miss"
    assert not res.signals.has_synced_reference


def test_gaps_push_to_review() -> None:
    # 16 anchored / 4 gap of 20 -> 80% anchor, 20% gap -> needs-review.
    res = score_lyrics(_corrections_json(n_words=20, anchor_words=16, gap_words=4, synced=True))
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "needs-review"


def test_single_gap_word_blocks_auto() -> None:
    # Regression for job 79c4f60c "Clarity": 241/242 anchored (99.6%), one gap word that
    # was a real mis-transcription. A single unresolved gap must NOT be auto-approved.
    data = _corrections_json(n_words=242, anchor_words=241, gap_words=1, synced=True)
    res = score_lyrics(data)
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.signals.gap_word_count == 1


def test_near_miss_band() -> None:
    # 19 anchored / 1 gap of 20 -> 95% anchor, 5% gap: within near-miss, below AUTO.
    res = score_lyrics(_corrections_json(n_words=20, anchor_words=19, gap_words=1, synced=True))
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "near-miss"


def test_too_few_words() -> None:
    res = score_lyrics(_corrections_json(n_words=5, anchor_words=5, gap_words=0))
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "too-few-words"


# --- AI-resolved tier (gap words covered by auto-applied suggestions) ---

def _suggestions_for_gaps(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    gap_ids = [wid for seq in data["gap_sequences"] for wid in seq["transcribed_word_ids"]]
    return [
        {"id": f"sug{i}", "op": "replace", "word_ids": [wid], "segment_ids": ["s0"],
         "original_text": "x", "new_text": "y", "confidence": 0.9,
         "consensus": 2, "total_models": 2}
        for i, wid in enumerate(gap_ids)
    ]


def test_ai_covered_gaps_are_auto_resolved() -> None:
    # 95% anchored with 5 gap words, every gap covered by an auto-applied AI
    # suggestion -> the post-AI state is clean -> AUTO (ai-resolved tier).
    data = _corrections_json(n_words=100, anchor_words=95, gap_words=5, synced=True)
    res = score_lyrics(data, _suggestions_for_gaps(data))
    assert res.verdict == LyricsVerdict.AUTO
    assert res.tier == "ai-resolved"
    assert res.signals.gap_words_covered_by_ai == 5
    assert res.signals.uncovered_gap_word_count == 0
    assert res.signals.ai_full_consensus_count == 5


def test_uncovered_gaps_beyond_threshold_stay_review() -> None:
    # 10 gaps, none covered -> 10% uncovered -> review.
    data = _corrections_json(n_words=100, anchor_words=90, gap_words=10, synced=True)
    res = score_lyrics(data, [])
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.signals.uncovered_gap_word_count == 10


def test_no_suggestion_data_never_ai_resolved() -> None:
    # Without the suggestion cache we can't know what the AI will fix -> the
    # ai-resolved tier must not apply (only the strict zero-gap tier can).
    data = _corrections_json(n_words=100, anchor_words=95, gap_words=5, synced=True)
    res = score_lyrics(data, None)
    assert res.verdict == LyricsVerdict.REVIEW
    assert not res.signals.ai_suggestions_available


def test_gates_override_ai_resolution() -> None:
    # Even with every gap covered, a vocalization section still gates to human.
    data = _corrections_json(n_words=100, anchor_words=95, gap_words=5, synced=True)
    voc = [_timed_word(f"v{i}", t, 10.0 + i * 0.4, 10.3 + i * 0.4)
           for i, t in enumerate(["Da-", "da-", "dun,", "da-", "da-", "dun"])]
    data = _with_extra_segment(data, voc, 10.0, 12.5)
    res = score_lyrics(data, _suggestions_for_gaps(data))
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "vocalization-gate"


def test_low_anchor_never_ai_resolved() -> None:
    # Heavy correction of a messy transcription (corpus f247364e: 47.6% anchor,
    # nearly all gaps covered) still means likely uncaught errors -> review.
    data = _corrections_json(n_words=100, anchor_words=48, gap_words=52, synced=True)
    sugs = _suggestions_for_gaps(data)[:50]  # 50 of 52 covered
    res = score_lyrics(data, sugs)
    assert res.verdict == LyricsVerdict.REVIEW


# --- Never-auto gating classes (Pattern 3 vocalizations, Pattern 8 phantoms) ---

def _timed_word(wid: str, text: str, start: float, end: float) -> Dict[str, Any]:
    return {"id": wid, "text": text, "start_time": start, "end_time": end}


def _with_extra_segment(data: Dict[str, Any], words: List[Dict[str, Any]],
                        seg_start: float, seg_end: float,
                        anchored: bool = True) -> Dict[str, Any]:
    """Append a segment and (optionally) anchor its words so gates are the only blocker."""
    data["corrected_segments"].append({
        "id": f"s{len(data['corrected_segments'])}",
        "text": " ".join(w["text"] for w in words),
        "words": words,
        "start_time": seg_start,
        "end_time": seg_end,
    })
    if anchored:
        data["anchor_sequences"][0]["transcribed_word_ids"].extend(w["id"] for w in words)
    data["metadata"]["total_words"] += len(words)
    return data


def test_vocalization_run_gates_even_when_otherwise_perfect() -> None:
    # b5a7b8aa "Two Birds": lines of "Da- da- dun, da- da- dun" — anchored or not,
    # the grouping/timing is a musical judgement -> never fully auto.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    voc = [_timed_word(f"v{i}", t, 10.0 + i * 0.4, 10.3 + i * 0.4)
           for i, t in enumerate(["Da-", "da-", "dun,", "da-", "da-", "dun"])]
    data = _with_extra_segment(data, voc, 10.0, 12.5)
    res = score_lyrics(data)
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "vocalization-gate"
    assert res.signals.has_vocalization_section
    assert res.signals.vocalization_max_run == 6


def test_single_very_long_vocalization_gates() -> None:
    # 8f2305ee seg 35: a single "Ooh," spanning 10.1s = many vocalizations lumped into one.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(data, [_timed_word("v0", "Ooh,", 100.0, 110.1)], 100.0, 110.1)
    res = score_lyrics(data)
    assert res.tier == "vocalization-gate"
    assert res.signals.long_vocalization_word_count == 1


def test_two_multi_second_vocalizations_gate() -> None:
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(
        data,
        [_timed_word("v0", "Ooh,", 100.0, 104.0), _timed_word("v1", "ooh", 105.0, 109.1)],
        100.0, 109.1,
    )
    res = score_lyrics(data)
    assert res.tier == "vocalization-gate"


def test_few_normal_vocalization_words_do_not_gate() -> None:
    # A couple of short "oh yeah"s in a normal song must not trip the gate.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(
        data,
        [_timed_word("v0", "Oh,", 50.0, 50.4), _timed_word("v1", "yeah", 50.4, 50.9)],
        50.0, 50.9,
    )
    res = score_lyrics(data)
    assert res.verdict == LyricsVerdict.AUTO
    assert not res.signals.has_vocalization_section


def test_absurd_word_duration_is_phantom_gate() -> None:
    # 33453fa0 "Angel": phantom "(I'm sorry)" line with a 7.4s word.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(
        data, [_timed_word("p0", "sorry", 0.0, 7.4)], 0.0, 7.4,
    )
    res = score_lyrics(data)
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "phantom-gate"
    assert res.signals.absurd_duration_word_count == 1
    assert res.signals.max_word_duration_s == 7.4


def test_stretched_short_parenthetical_is_phantom_gate() -> None:
    # Short parenthetical line spanning several seconds, individual words normal-length.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(
        data,
        [_timed_word("p0", "(I'm", 0.0, 0.4), _timed_word("p1", "sorry)", 4.4, 4.8)],
        0.0, 4.8,
    )
    res = score_lyrics(data)
    assert res.tier == "phantom-gate"
    assert res.signals.suspicious_parenthetical_count == 1


def test_normal_parenthetical_backing_bit_does_not_gate() -> None:
    # Pattern 7 trailing "(Dreaming)" bits are left alone — a short parenthetical with
    # a normal duration must not be treated as a phantom.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(
        data, [_timed_word("p0", "(Dreaming)", 60.0, 61.2)], 60.0, 61.2,
    )
    res = score_lyrics(data)
    assert res.verdict == LyricsVerdict.AUTO


def _delete_suggestion(sid: str, word_ids: List[str], conflict_group=None,
                       consensus: int = 1, confidence: float = 0.9) -> Dict[str, Any]:
    return {
        "id": sid, "op": "delete", "word_ids": word_ids, "new_text": "",
        "conflict_group": conflict_group, "consensus": consensus,
        "confidence": confidence, "total_models": consensus,
    }


def test_phantom_gate_clears_when_a_delete_suggestion_removes_the_phantom() -> None:
    # The P8 phantom-parenthetical fixer emits a delete for the phantom line; the
    # scorer treats those words as removed, so the phantom gate no longer fires.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(
        data,
        [_timed_word("p0", "(I'm", 0.0, 0.4), _timed_word("p1", "sorry)", 4.4, 4.8)],
        0.0, 4.8, anchored=False,
    )
    gated = score_lyrics(data)
    assert gated.tier == "phantom-gate"

    cleared = score_lyrics(data, [_delete_suggestion("d0", ["p0", "p1"])])
    assert not cleared.signals.has_phantom_signature
    assert cleared.tier != "phantom-gate"


def test_phantom_gate_stays_when_the_delete_loses_its_conflict_group() -> None:
    # A delete that loses its conflict group won't actually remove the words, so
    # the scorer must NOT treat the phantom as handled.
    data = _corrections_json(anchor_words=20, gap_words=0, synced=True)
    data = _with_extra_segment(
        data,
        [_timed_word("p0", "(I'm", 0.0, 0.4), _timed_word("p1", "sorry)", 4.4, 4.8)],
        0.0, 4.8, anchored=False,
    )
    losing_delete = _delete_suggestion("d0", ["p0", "p1"], conflict_group="c1",
                                       consensus=1, confidence=0.9)
    winning_replace = {
        "id": "r0", "op": "replace", "word_ids": ["p0", "p1"], "new_text": "I'm sorry",
        "conflict_group": "c1", "consensus": 3, "confidence": 0.95, "total_models": 3,
    }
    res = score_lyrics(data, [losing_delete, winning_replace])
    assert res.signals.has_phantom_signature
    assert res.tier == "phantom-gate"


def test_verdict_to_dict_is_json_safe() -> None:
    import json

    v = score_job(
        _corrections_json(anchor_words=20, gap_words=0, synced=True),
        {"has_audible_content": False, "audible_percentage": 0.0, "audible_segments": []},
    )
    d = v.to_dict()
    json.dumps(d)  # must not raise (enums converted to plain strings)
    assert d["lyrics"]["verdict"] == "auto"
    assert d["backing"]["verdict"] == "clean"


# --- Backing ---

def test_no_audible_backing_is_non_subjective_clean() -> None:
    res = score_backing({"has_audible_content": False, "audible_percentage": 0.0,
                         "audible_segments": [], "recommended_selection": "clean"})
    assert res.verdict == BackingVerdict.CLEAN
    assert res.non_subjective is True


def test_near_silent_backing_is_clean() -> None:
    res = score_backing({"has_audible_content": True, "audible_percentage": 0.3,
                         "audible_segments": [{"avg_amplitude_db": -35.0}],
                         "recommended_selection": "clean"})
    assert res.verdict == BackingVerdict.CLEAN
    assert res.non_subjective is True


def test_audible_backing_is_subjective_review() -> None:
    res = score_backing({"has_audible_content": True, "audible_percentage": 42.0,
                         "audible_segments": [{"avg_amplitude_db": -12.0, "peak_amplitude_db": -6.0}],
                         "recommended_selection": "with_backing"})
    assert res.verdict == BackingVerdict.REVIEW
    assert res.non_subjective is False
    assert res.signals.loud_segment_count == 1


def test_missing_backing_analysis_is_review() -> None:
    res = score_backing(None)
    assert res.verdict == BackingVerdict.REVIEW
    assert res.non_subjective is False


def test_errored_backing_analysis_is_review_not_clean() -> None:
    # A failed analysis stores has_audible_content=None + analysis_error; it
    # must NOT read as "no audible content" (that would wrongly auto-pick clean).
    res = score_backing({
        "has_audible_content": None,
        "analysis_error": "download failed",
        "recommended_selection": "clean",
    })
    assert res.verdict == BackingVerdict.REVIEW
    assert res.non_subjective is False
    assert res.signals.analysis_present is False


# --- Combined ---

def test_overall_auto_requires_both() -> None:
    v = score_job(
        _corrections_json(anchor_words=20, gap_words=0, synced=True),
        {"has_audible_content": False, "audible_percentage": 0.0, "audible_segments": []},
    )
    assert v.overall_auto is True


def test_overall_not_auto_when_backing_subjective() -> None:
    v = score_job(
        _corrections_json(anchor_words=20, gap_words=0, synced=True),
        {"has_audible_content": True, "audible_percentage": 30.0,
         "audible_segments": [{"avg_amplitude_db": -10.0}]},
    )
    assert v.lyrics.verdict == LyricsVerdict.AUTO
    assert v.overall_auto is False


# --- Timing-plausibility gate (never-auto; signals computed by the executor) ---

def test_fired_timing_signals_gate_confident_lyrics() -> None:
    from backend.services.auto_approval.timing_check import TimingSignals

    sig = TimingSignals(
        n_words=100, pct_start_inactive=40.0, n_suspect_bad=30,
        max_unclaimed_run_s=4.9, fired=["start-silence", "suspect-mistimed"],
    )
    res = score_lyrics(_corrections_json(), timing_signals=sig)
    assert res.verdict == LyricsVerdict.REVIEW
    assert res.tier == "timing-gate"
    assert "timing-plausibility gate fired" in res.reasons[0]


def test_unfired_timing_signals_leave_auto_untouched() -> None:
    from backend.services.auto_approval.timing_check import TimingSignals

    res = score_lyrics(_corrections_json(), timing_signals=TimingSignals(n_words=100))
    assert res.verdict == LyricsVerdict.AUTO
    assert res.tier == "synced-perfect"


def test_absent_timing_signals_leave_auto_untouched() -> None:
    res = score_lyrics(_corrections_json(), timing_signals=None)
    assert res.verdict == LyricsVerdict.AUTO


def test_timing_gate_flows_through_score_job() -> None:
    from backend.services.auto_approval.timing_check import TimingSignals

    sig = TimingSignals(n_words=50, pct_start_inactive=40.0, fired=["start-silence"])
    verdict = score_job(_corrections_json(), None, None, timing_signals=sig)
    assert verdict.lyrics.tier == "timing-gate"
    assert verdict.overall_auto is False
