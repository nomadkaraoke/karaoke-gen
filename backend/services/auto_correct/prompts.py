"""Prompt construction for AI auto-correction suggestions.

Kept in sync with the offline eval harness
(lyrics-alignment-eval-dataset/auto-correct-eval/harness.py) — changes here
should be re-scored against the 30-job ground-truth dataset.
"""
from __future__ import annotations

from backend.services.auto_correct.settings import AutoCorrectSettings

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["replace", "delete", "insert_after"],
                    },
                    "start_idx": {"type": "integer"},
                    "end_idx": {"type": "integer"},
                    "new_text": {"type": "string"},
                    "reason": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "mishearing",
                            "grammar",
                            "adlib_removal",
                            "repeated_chorus_fix",
                            "formatting",
                            "other",
                        ],
                    },
                    "confidence": {"type": "number"},
                },
                "required": [
                    "op",
                    "start_idx",
                    "end_idx",
                    "new_text",
                    "reason",
                    "category",
                    "confidence",
                ],
            },
        }
    },
    "required": ["suggestions"],
}

_BASE_SYSTEM_PROMPT = """\
You are an expert lyrics transcription reviewer for a karaoke video company. \
You are given an automatic speech transcription of a song's vocals (already \
segmented into lines, each word tagged with a numeric index) plus lyrics for \
the same song from internet sources. Internet sources are usually (not always) \
closer to the true lyrics, but lack the transcription's timing and line layout.

Propose word-level corrections to the TRANSCRIPTION so its words match what is \
actually sung. A human reviewer who has listened to the song will accept or \
reject each suggestion individually, so precision matters far more than \
recall: only suggest a change when you are confident a careful human reviewer \
would make it. If reference sources disagree and you have no other evidence, \
make no suggestion.

Guidelines:
- Transcription errors are usually sound-alike mishearings: the wrong words \
sound similar to the true lyric (e.g. "glory" vs "chlorine", "ladder" vs \
"matter"). Prefer corrections that are phonetically plausible.
- The same mishearing often repeats in every chorus; correct every occurrence.
- Keep the transcription's line structure. NEVER suggest timing changes.
- Match the casing and punctuation style of the surrounding transcription.
- Do not censor or bowdlerise explicit lyrics.
- Do not rewrite lines wholesale unless the transcription is clearly garbled \
and a reference line is unambiguously what is sung at that point.
- new_text may contain multiple words, or differ in word count from the span \
it replaces.
"""

_ADLIB_ALLOWED = """\
- Parenthesised backing vocals / adlibs that the transcription contains but \
references omit may be real (references often drop them). Only suggest \
deleting them when they look like transcription hallucinations or the \
references make clear they are not sung lead lines; use category \
"adlib_removal" and lower confidence.
"""

_ADLIB_FORBIDDEN = """\
- NEVER suggest deleting backing vocals or adlibs solely because the \
references omit them; references often drop adlibs that are really sung.
"""

_INSERTIONS_FORBIDDEN = """\
- NEVER use the "insert_after" operation; only suggest "replace" and \
"delete" operations.
"""

_OUTPUT_SPEC = """\
Output JSON only, matching this shape exactly:
{"suggestions": [{"op": "replace"|"delete"|"insert_after",
  "start_idx": <int>, "end_idx": <int>,
  "new_text": "<replacement, empty for delete>",
  "reason": "<short justification>",
  "category": "mishearing"|"grammar"|"adlib_removal"|"repeated_chorus_fix"|"formatting"|"other",
  "confidence": <0.0-1.0>}]}
start_idx and end_idx are inclusive word indices. For "insert_after", \
start_idx == end_idx == the index of the word the new text should follow. \
If nothing needs changing, return {"suggestions": []}.
"""


def build_system_prompt(settings: AutoCorrectSettings) -> str:
    parts = [_BASE_SYSTEM_PROMPT]
    parts.append(_ADLIB_ALLOWED if settings.suggest_adlib_removal else _ADLIB_FORBIDDEN)
    if not settings.allow_insertions:
        parts.append(_INSERTIONS_FORBIDDEN)
    parts.append(_OUTPUT_SPEC)
    return "\n".join(parts)


def build_user_prompt(
    *,
    segments: list[dict],
    reference_lyrics: dict[str, dict],
    artist: str | None,
    title: str | None,
) -> str:
    """Render segments (with global word indices) + reference sources."""
    lines = [f"ARTIST: {artist or 'unknown'}", f"TITLE: {title or 'unknown'}", "", "TRANSCRIPTION:"]
    idx = 0
    for li, seg in enumerate(segments):
        toks = []
        for w in seg.get("words") or []:
            toks.append(f"{idx}:{w.get('text', '')}")
            idx += 1
        lines.append(f"[L{li:03d}] " + " ".join(toks))
    for source, ref in (reference_lyrics or {}).items():
        lines.append("")
        lines.append(f"REFERENCE LYRICS ({source}):")
        for seg in ref.get("segments") or []:
            lines.append(seg.get("text", ""))
    return "\n".join(lines)
