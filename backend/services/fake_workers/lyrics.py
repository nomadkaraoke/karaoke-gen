"""A fake but schema-valid ``corrections.json`` for the fake lyrics worker.

Built from the real ``CorrectionResult`` dataclasses, so if the schema the review
UI depends on changes, the fake changes with it (and its unit test catches
anything ``from_dict`` can no longer read).
"""
from __future__ import annotations

from typing import Any, Dict, List

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


def _segments(prefix: str, duration: float) -> List[LyricsSegment]:
    """Spread FAKE_LINES evenly over the song, one word slot per word."""
    usable = max(duration - LEAD_IN_SECONDS - 1.0, len(FAKE_LINES))
    line_len = usable / len(FAKE_LINES)
    segments = []
    for i, line in enumerate(FAKE_LINES):
        words_text = line.split()
        start = LEAD_IN_SECONDS + i * line_len
        slot = (line_len * 0.9) / len(words_text)
        words = [
            Word(
                id=f"{prefix}w{i}_{j}",
                text=text,
                start_time=round(start + j * slot, 3),
                end_time=round(start + (j + 1) * slot - 0.05, 3),
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
            )
        )
    return segments


def build_corrections(artist: str, title: str, duration: float) -> Dict[str, Any]:
    """Return a ``CorrectionResult.to_dict()`` whose transcription matches its reference."""
    transcribed = _segments("t", duration)
    reference = _segments("r", duration)
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
    return result.to_dict()


def build_lrc(corrections: Dict[str, Any]) -> str:
    lines = []
    for seg in corrections["corrected_segments"]:
        m, s = divmod(seg["start_time"], 60)
        lines.append(f"[{int(m):02d}:{s:05.2f}]{seg['text']}")
    return "\n".join(lines) + "\n"
