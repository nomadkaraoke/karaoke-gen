"""Batch orchestration + graceful degrade for karaoke-filename parsing."""
from __future__ import annotations

import logging

from backend.services.parse_titles import ai

logger = logging.getLogger(__name__)


def _blanks(items: list[dict]) -> list[dict]:
    return [
        {"id": str(it.get("id")), "artist": "", "title": "", "confidence": 0.0}
        for it in items
    ]


async def parse_titles(
    items: list[dict], *, model=None, generate=None
) -> list[dict]:
    """Parse a batch of filenames → id-aligned {id, artist, title, confidence}.

    Never raises: a disabled kill-switch or any model failure yields blank
    results so the (kjbox) caller keeps its deterministic guess.
    """
    if not items:
        return []
    try:
        from backend.config import settings
        enabled = getattr(settings, "parse_titles_enabled", True)
        max_items = int(getattr(settings, "parse_titles_max_items", 200))
    except Exception:  # pragma: no cover
        enabled, max_items = True, 200
    if not enabled:
        return _blanks(items)

    head, tail = items[:max_items], items[max_items:]
    try:
        results = await ai.ai_parse(head, model=model, generate=generate)
    except Exception as exc:
        logger.warning("parse_titles degraded (%s); returning blanks", exc)
        return _blanks(items)
    return results + _blanks(tail)
