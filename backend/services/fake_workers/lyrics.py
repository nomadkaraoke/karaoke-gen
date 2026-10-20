"""A fake but schema-valid ``corrections.json`` for the fake lyrics worker.

Built from the real ``CorrectionResult`` dataclasses, so if the schema the review
UI depends on changes, the fake changes with it (and its unit test catches
anything ``from_dict`` can no longer read).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from karaoke_gen.lyrics_transcriber.types import (
    AnchorSequence,
    CorrectionResult,
    LyricsData,
    LyricsMetadata,
    LyricsSegment,
    Word,
)

FAKE_LINES = (
    "This is a fake song for testing",
    "Every word arrives right on time",
    "Sing along with the local stack",
    "Nothing here was ever transcribed",
    "Workers pretend and the jobs move on",
    "Until the video is done",
)

REFERENCE_SOURCE = "lrclib"
LEAD_IN_SECONDS = 2.0

# Right-to-left fixture: Hebrew lines, one of them also carrying Arabic (mixed-script bidi).
RTL_LINES = (
    "שיר בדיקה קטן",
    "כל מילה מגיעה בזמן",
    "שרים יחד עם המערכת המקומית",
    "שום דבר כאן לא תומלל أغنية للاختبار",
    "העובדים מעמידים פנים והעבודות ממשיכות",
    "עד שהסרטון מוכן",
)

# Duet fixture: per-line singer ids cycle 1, 2, 0 (0 = both).
DUET_SINGERS = (1, 2, 0)

LARGE_LINE_COUNT = 300
_LARGE_VOCAB = (
    "sing", "along", "with", "the", "local", "stack", "every", "word", "arrives",
    "right", "on", "time", "fake", "song", "for", "testing", "workers", "pretend",
)

# The corrections fixture holds one word for longer than the UI's "long word" threshold.
LONG_WORD_SECONDS = 2.2
# A line the transcription missed, only present in the synced reference (the vocal gap).
MISSED_REFERENCE_LINE = "Thanks for singing along tonight"


def _large_lines() -> List[str]:
    lines = []
    for i in range(LARGE_LINE_COUNT):
        count = 6 + (i % 3)  # 6-8 words, 7 on average -> 2,100 words
        words = [_LARGE_VOCAB[(i * 5 + j) % len(_LARGE_VOCAB)] for j in range(count)]
        lines.append(" ".join(words).capitalize())
    return lines


def _lines_for(fixture: str) -> Sequence[str]:
    if fixture == "rtl":
        return RTL_LINES
    if fixture == "large":
        return _large_lines()
    return FAKE_LINES


def _segments(prefix: str, duration: float, lines: Sequence[str] = FAKE_LINES,
              singers: Optional[Sequence[int]] = None) -> List[LyricsSegment]:
    """Spread ``lines`` evenly over the song, one word slot per word."""
    usable = max(duration - LEAD_IN_SECONDS - 1.0, len(lines))
    line_len = usable / len(lines)
    segments = []
    for i, line in enumerate(lines):
        words_text = line.split()
        start = LEAD_IN_SECONDS + i * line_len
        slot = (line_len * 0.9) / len(words_text)
        gap = min(0.05, slot * 0.1)
        words = [
            Word(
                id=f"{prefix}w{i}_{j}",
                text=text,
                start_time=round(start + j * slot, 3),
                end_time=round(start + (j + 1) * slot - gap, 3),
                confidence=0.99,
            )
            for j, text in enumerate(words_text)
        ]
        segments.append(
            LyricsSegment(
                id=f"{prefix}s{i}",
                text=line,
                words=words,
                start_time=words[0].start_time,
                end_time=words[-1].end_time,
                singer=singers[i % len(singers)] if singers else None,
            )
        )
    return segments


def build_corrections(artist: str, title: str, duration: float, fixture: str = "default") -> Dict[str, Any]:
    """Return a ``CorrectionResult.to_dict()`` whose transcription matches its reference.

    ``fixture`` picks the lyrics shape (see ``FakeConfig.review_fixture``). Everything but
    ``corrections`` is built straight from the real dataclasses; ``corrections`` then edits
    the dict into the post-AI-apply state gen's server-side pre-apply produces (extra word
    fields the dataclasses don't model), and carries the matching ``vocal_gaps`` block under
    a ``vocal_gaps`` key for the lyrics worker to store in ``state_data``.
    """
    lines = _lines_for(fixture)
    singers = DUET_SINGERS if fixture == "duet" else None
    transcribed = _segments("t", duration, lines, singers)
    reference = _segments("r", duration, lines, singers)
    reference_lyrics = {
        REFERENCE_SOURCE: LyricsData(
            segments=reference,
            metadata=LyricsMetadata(
                source=REFERENCE_SOURCE,
                track_name=title,
                artist_names=artist,
                duration_ms=int(duration * 1000),
                is_synced=True,
                lyrics_provider=REFERENCE_SOURCE,
                lyrics_provider_id="fake",
            ),
            source=REFERENCE_SOURCE,
        )
    }
    t_ids = [w.id for s in transcribed for w in s.words]
    r_ids = [w.id for s in reference for w in s.words]
    anchors = [
        AnchorSequence(
            id="fake-anchor-0",
            transcribed_word_ids=t_ids,
            transcription_position=0,
            reference_positions={REFERENCE_SOURCE: 0},
            reference_word_ids={REFERENCE_SOURCE: r_ids},
            confidence=1.0,
        )
    ]
    result = CorrectionResult(
        original_segments=transcribed,
        corrected_segments=transcribed,
        corrections=[],
        corrections_made=0,
        confidence=1.0,
        reference_lyrics=reference_lyrics,
        anchor_sequences=anchors,
        gap_sequences=[],
        resized_segments=transcribed,
        metadata={
            "correction_type": "none",
            "reason": "fake_workers",
            "anchor_sequences_count": len(anchors),
            "gap_sequences_count": 0,
            "total_words": len(t_ids),
            "correction_ratio": 0.0,
            "available_handlers": [],
            "enabled_handlers": [],
            "agentic_routing": "disabled",
        },
        correction_steps=[],
        word_id_map={},
        segment_id_map={},
    )
    doc = result.to_dict()
    if fixture == "duet":
        doc["is_duet"] = True
    elif fixture == "corrections":
        _apply_corrections_fixture(doc, duration)
    return doc


def _word_dict(word_id: str, text: str, start: Optional[float], end: Optional[float], **extra: Any) -> Dict[str, Any]:
    return {"id": word_id, "text": text, "start_time": start, "end_time": end, "confidence": 1, **extra}


def _apply_corrections_fixture(doc: Dict[str, Any], duration: float) -> None:
    """Turn ``doc`` into the post-AI-apply state: two applied suggestions with undo info,
    an untimed word, a long word and an evidenced vocal gap with synced reference lines."""
    import copy
    from datetime import datetime, timezone

    original = doc["original_segments"]
    corrected = copy.deepcopy(original)
    suggestions: List[Dict[str, Any]] = []
    undo_info: Dict[str, Any] = {}

    # Segment 1: replace "arrives" (heard as "arrive") -> a single AI-corrected word, real timing.
    seg = corrected[1]
    idx = next(i for i, w in enumerate(seg["words"]) if w["text"] == "arrives")
    old = copy.deepcopy(seg["words"][idx])
    original[1]["words"][idx]["text"] = "arrive"
    original[1]["text"] = original[1]["text"].replace("arrives", "arrive")
    sid = "fake-sug-replace"
    new_word = _word_dict(
        "fake-ai-arrives", "arrives", old["start_time"], old["end_time"],
        created_during_correction=True, ai_corrected=True, timing_estimated=False,
        correction_span_id=sid, original_text="arrive",
    )
    removed = {**old, "text": "arrive"}
    seg["words"][idx] = new_word
    suggestions.append(_suggestion(sid, "replace", [old["id"]], [seg["id"]], "arrive", "arrives",
                                   "Matches the synced reference", "mishearing"))
    undo_info[sid] = {
        "op": "replace", "new_word_ids": [new_word["id"]], "removed_words": [removed],
        "segment_id": seg["id"], "prev_word_id": seg["words"][idx - 1]["id"] if idx else None,
        "removed_segment": None,
    }

    # Segment 2: one heard word "localstack" -> two words sharing its slot (estimated timing).
    seg = corrected[2]
    idx = next(i for i, w in enumerate(seg["words"]) if w["text"] == "local")
    old = copy.deepcopy(seg["words"][idx])
    stack = seg["words"][idx + 1]
    span_end = stack["end_time"]
    original[2]["words"][idx]["text"] = "localstack"
    original[2]["words"][idx]["end_time"] = span_end
    del original[2]["words"][idx + 1]
    original[2]["text"] = original[2]["text"].replace("local stack", "localstack")
    removed_word = {**old, "text": "localstack", "end_time": span_end}
    sid2 = "fake-sug-split"
    mid = round((old["start_time"] + span_end) / 2, 3)
    new_words = [
        _word_dict("fake-ai-local", "local", old["start_time"], mid, created_during_correction=True,
                   ai_corrected=True, timing_estimated=True, correction_span_id=sid2,
                   original_text="localstack"),
        _word_dict("fake-ai-stack", "stack", mid, span_end, created_during_correction=True,
                   ai_corrected=True, timing_estimated=True, correction_span_id=sid2),
    ]
    seg["words"][idx:idx + 2] = new_words
    suggestions.append(_suggestion(sid2, "replace", [old["id"]], [seg["id"]], "localstack", "local stack",
                                   "Two words run together", "mishearing"))
    undo_info[sid2] = {
        "op": "replace", "new_word_ids": [w["id"] for w in new_words], "removed_words": [removed_word],
        "segment_id": seg["id"], "prev_word_id": seg["words"][idx - 1]["id"] if idx else None,
        "removed_segment": None,
    }

    # Segment 3: one word with no timing (the render would crash on it if submitted as-is).
    untimed = next(w for w in corrected[3]["words"] if w["text"] == "ever")
    untimed["start_time"] = None
    untimed["end_time"] = None

    # Last segment: first word held for longer than 2 s, the rest squeezed into what remains.
    seg = corrected[-1]
    first, rest = seg["words"][0], seg["words"][1:]
    held_end = round(first["start_time"] + LONG_WORD_SECONDS, 3)
    span = seg["words"][-1]["end_time"] - held_end
    step = span / len(rest)
    first["end_time"] = held_end
    for k, w in enumerate(rest):
        w["start_time"] = round(held_end + k * step, 3)
        w["end_time"] = round(held_end + (k + 1) * step - 0.03, 3)

    for segment in corrected:
        timed = [w for w in segment["words"] if w["start_time"] is not None]
        segment["text"] = " ".join(w["text"] for w in segment["words"])
        segment["start_time"] = timed[0]["start_time"]
        segment["end_time"] = timed[-1]["end_time"]

    doc["corrected_segments"] = corrected
    doc["resized_segments"] = copy.deepcopy(corrected)
    doc["corrections_made"] = len(suggestions)
    doc["metadata"]["auto_approval"] = {
        "pre_applied": True,
        "applied_suggestion_ids": [s["id"] for s in suggestions],
        "rejected_suggestion_ids": [],
        "suggestions": suggestions,
        "undo_info": undo_info,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

    # One evidenced vocal gap after the last line, where only the synced reference has a line.
    last_end = corrected[-1]["end_time"]
    gap_start = round(last_end + 0.2, 3)
    gap_end = round(max(min(last_end + 1.8, duration - 0.1), gap_start + 0.5), 3)
    ref = doc["reference_lyrics"][REFERENCE_SOURCE]
    ref["segments"].append({
        "id": "rs-missed", "text": MISSED_REFERENCE_LINE,
        "words": [_word_dict(f"rw-missed-{j}", t, gap_start, gap_end)
                  for j, t in enumerate(MISSED_REFERENCE_LINE.split())],
        "start_time": gap_start, "end_time": gap_end,
    })
    doc["vocal_gaps"] = {
        "version": "fake",
        "gaps": [{
            "start": gap_start, "end": gap_end, "duration": round(gap_end - gap_start, 3),
            "active_fraction": 0.85, "longest_run_s": round(gap_end - gap_start - 0.1, 3),
            "reference_lines": {REFERENCE_SOURCE: [MISSED_REFERENCE_LINE]},
            "synced_reference_lines": {REFERENCE_SOURCE: [MISSED_REFERENCE_LINE]},
            "suspect": True, "evidenced": True,
        }],
        "suspect_count": 1,
        "evidenced_count": 1,
        "max_suspect_run_s": round(gap_end - gap_start - 0.1, 3),
    }


def _suggestion(sid: str, op: str, word_ids: List[str], segment_ids: List[str], original_text: str,
                new_text: str, reason: str, category: str) -> Dict[str, Any]:
    return {
        "id": sid, "op": op, "word_ids": word_ids, "segment_ids": segment_ids,
        "original_text": original_text, "new_text": new_text, "reason": reason,
        "category": category, "confidence": 0.95, "models": ["fake-model"],
        "consensus": 1, "total_models": 1, "conflict_group": None,
    }


def build_lrc(corrections: Dict[str, Any]) -> str:
    lines = []
    for seg in corrections["corrected_segments"]:
        m, s = divmod(seg["start_time"], 60)
        lines.append(f"[{int(m):02d}:{s:05.2f}]{seg['text']}")
    return "\n".join(lines) + "\n"
