"""Batch orchestration + graceful degrade for karaoke-filename parsing."""
from __future__ import annotations

import asyncio
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

    Large batches are split into concurrent chunks of parse_titles_chunk_size
    so a single Gemini call never outgrows parse_titles_timeout_ms (a 100-item
    single call did, and the timeout blanked the whole batch).

    Never raises: a disabled kill-switch or any model failure yields blank
    results — per chunk, so one bad chunk doesn't blank its neighbours — and
    the (kjbox) caller keeps its deterministic guess.
    """
    if not items:
        return []
    try:
        from backend.config import settings
        enabled = getattr(settings, "parse_titles_enabled", True)
        max_items = int(getattr(settings, "parse_titles_max_items", 200))
        chunk_size = max(1, int(getattr(settings, "parse_titles_chunk_size", 10)))
    except Exception:  # pragma: no cover
        # Fail closed: if config can't load, don't reach for the external AI.
        logger.warning("parse_titles settings unavailable; disabling parser")
        enabled, max_items, chunk_size = False, 200, 10
    if not enabled:
        return _blanks(items)

    head, tail = items[:max_items], items[max_items:]
    chunks = [head[i:i + chunk_size] for i in range(0, len(head), chunk_size)]
    parsed = await asyncio.gather(
        *(_parse_chunk(chunk, model=model, generate=generate) for chunk in chunks)
    )
    return [r for chunk_results in parsed for r in chunk_results] + _blanks(tail)


async def _parse_chunk(chunk: list[dict], *, model, generate) -> list[dict]:
    """One Gemini call; any failure degrades to blanks for this chunk only."""
    try:
        results = await ai.ai_parse(chunk, model=model, generate=generate)
    except Exception as exc:
        logger.warning(
            "parse_titles chunk degraded (%s); blanks for %d items", exc, len(chunk))
        return _blanks(chunk)
    # ai_parse is id-aligned by construction; guard the contract defensively so a
    # future refactor can't silently return a mis-aligned/short batch.
    if len(results) != len(chunk):
        logger.warning(
            "parse_titles chunk length mismatch; blanks for %d items", len(chunk))
        return _blanks(chunk)
    return results
