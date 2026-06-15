"""Match-judge orchestration.

Composes the deterministic+catalog classifier with the AI judge. The AI judge
runs ONLY when the classifier can't confidently decide, and never blocks or
errors the caller — any failure degrades to "no suggestion".
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from backend.services.match_judge.classifier import classify_catalog_match
from backend.services.match_judge.verdict import KIND_NONE, MatchVerdict

logger = logging.getLogger(__name__)

CATALOG_CANDIDATE_LIMIT = 8

SearchTracks = Callable[[str, Optional[str], int], Awaitable[list[dict]]]
AiJudge = Callable[[str, str, list[dict], Optional[int]], Awaitable[MatchVerdict]]


async def _default_search_tracks(query: str, artist: Optional[str], limit: int) -> list[dict]:
    from backend.services.catalog_proxy_service import search_tracks
    return await search_tracks(query, artist, limit)


async def _default_ai_judge(
    artist: str, title: str, candidates: list[dict], audio_tier: Optional[int]
) -> MatchVerdict:
    from backend.services.match_judge.ai import ai_judge_match
    return await ai_judge_match(artist, title, candidates, audio_tier)


def _default_enabled() -> bool:
    try:
        from backend.config import settings
        return bool(getattr(settings, "match_judge_enabled", True))
    except Exception:  # pragma: no cover - config import guard
        return True


async def judge_match(
    artist: str,
    title: str,
    audio_tier: Optional[int] = None,
    *,
    search_tracks: Optional[SearchTracks] = None,
    ai_judge: Optional[AiJudge] = None,
    enabled: Optional[bool] = None,
) -> MatchVerdict:
    """Return a :class:`MatchVerdict` for the typed artist/title.

    audio_tier: the audio search's confidence tier (1=best..3=poor), used by the
    AI judge to decide whether weak results suggest a typo. May be None.
    """
    search = search_tracks or _default_search_tracks
    ai = ai_judge or _default_ai_judge
    if enabled is None:
        enabled = _default_enabled()

    try:
        candidates = await search(title, artist, CATALOG_CANDIDATE_LIMIT)
    except Exception:
        logger.warning(
            "match-judge catalog search failed; continuing without candidates",
            exc_info=True,
        )
        candidates = []

    verdict = classify_catalog_match(artist, title, candidates)
    if verdict is not None:
        return verdict

    if not enabled:
        return MatchVerdict(
            KIND_NONE, True, artist, title, engine="deterministic", reason="ai disabled"
        )

    try:
        return await ai(artist, title, candidates, audio_tier)
    except Exception:
        logger.exception("match-judge AI call failed; returning no-suggestion")
        return MatchVerdict(
            KIND_NONE, True, artist, title, engine="ai", reason="ai failed"
        )
