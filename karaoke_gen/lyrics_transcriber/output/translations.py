"""Attach translated lyrics (``translations.json``) to corrected segments.

The backend translates the reviewed lyrics once per job and stores the result at
``jobs/{job_id}/lyrics/translations.json``::

    {
      "language": "es",
      "language_name": "Spanish",
      "model": "gemini-3.8-flash",
      "lines": [{"segment_id": "...", "text": "<original line>", "translation": "..."}]
    }

Renderers (landscape, portrait) load it and set ``LyricsSegment.translation`` before
resizing, so ``SubtitlesGenerator`` draws each translation beneath its line.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from karaoke_gen.lyrics_transcriber.types import LyricsSegment

TRANSLATIONS_FILENAME = "translations.json"

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def apply_translations(segments: List[LyricsSegment], data: Optional[Dict[str, Any]]) -> int:
    """Set ``translation`` on each segment from ``data``; returns how many were set.

    Matches by segment id, falling back to the line text (ids can differ when the
    translation was made from an equivalent copy of the lyrics).
    """
    if not data or not segments:
        return 0
    by_id: Dict[str, str] = {}
    by_text: Dict[str, str] = {}
    for line in data.get("lines") or []:
        translation = (line.get("translation") or "").strip()
        if not translation:
            continue
        if line.get("segment_id"):
            by_id[line["segment_id"]] = translation
        if line.get("text"):
            by_text.setdefault(_norm(line["text"]), translation)

    applied = 0
    for seg in segments:
        translation = by_id.get(seg.id) or by_text.get(_norm(seg.text))
        if translation:
            seg.translation = translation
            applied += 1
    return applied


def load_and_apply_translations(segments: List[LyricsSegment], path: Optional[str]) -> int:
    """``apply_translations`` from a JSON file; a missing/unreadable file applies none."""
    if not path or not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.error(f"Could not read translations file {path}: {e}")
        return 0
    applied = apply_translations(segments, data)
    logger.info(f"Applied {applied}/{len(segments)} translations ({data.get('language')}) from {path}")
    return applied
