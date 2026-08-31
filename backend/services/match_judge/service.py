"""Match-judge orchestration.

Composes the deterministic+catalog classifier with the AI judge. The AI judge
runs ONLY when the classifier can't confidently decide, and never blocks or
errors the caller — any failure degrades to "no suggestion".
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Literal, Optional

from backend.services.match_judge.classifier import classify_catalog_match
from backend.services.match_judge.verdict import KIND_NONE, MatchVerdict

logger = logging.getLogger(__name__)

CATALOG_CANDIDATE_LIMIT = 8

Stage = Literal["fast", "full"]

# Audio tiers at or above this are "weak" (poor / no results). A weak tier hints
# the typed title may be a typo, so even a confident catalog match is verified
# with the AI judge (it can be a junk/misspelled Spotify entry).
WEAK_TIER = 3

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
    stage: Stage = "full",
    search_tracks: Optional[SearchTracks] = None,
    ai_judge: Optional[AiJudge] = None,
    enabled: Optional[bool] = None,
) -> MatchVerdict:
    """Return a :class:`MatchVerdict` for the typed artist/title.

    audio_tier: the audio search's confidence tier (1=best..3=poor), used by the
    AI judge to decide whether weak results suggest a typo. May be None.

    stage:
      "fast" — catalog-only pass (no AI, tier ignored). Runs in parallel with the
        audio search so the common cosmetic tidy is ready before results render.
        Returns the catalog verdict if confident, else a ``needs_ai=True`` marker
        telling the caller to make a "full" call once the tier is known.
      "full" — the complete pipeline: catalog, then the AI judge when catalog isn't
        confident OR (item 4) when a confident catalog match coincides with a weak
        audio tier (the match may be a junk/typo'd entry — let AI override).
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

    catalog_verdict = classify_catalog_match(artist, title, candidates)

    # Fast pass: catalog only. Never touches the AI layer.
    if stage == "fast":
        if catalog_verdict is not None:
            return catalog_verdict
        return MatchVerdict(
            KIND_NONE, False, artist, title,
            engine="catalog", reason="needs ai", needs_ai=True,
        )

    # Full pass.
    if catalog_verdict is not None:
        weak = audio_tier is not None and audio_tier >= WEAK_TIER
        if not (enabled and weak):
            return catalog_verdict
        # Item 4: weak audio results make the typo hypothesis plausible even though
        # the catalog "matched" — verify with AI and let a confident verdict override.
        try:
            ai_verdict = await ai(artist, title, candidates, audio_tier)
        except Exception:
            # Graceful degradation: a transient Vertex/Gemini blip just means we
            # keep the catalog verdict. Log at WARNING (with traceback) so it does
            # not page as a red "new error pattern" — it self-heals and needs no
            # action. Downgraded 2026-08-31 after a single benign occurrence alerted.
            logger.warning(
                "match-judge AI verification failed; keeping catalog verdict",
                exc_info=True,
            )
            return catalog_verdict
        if ai_verdict.confident and ai_verdict.kind != KIND_NONE:
            return ai_verdict
        return catalog_verdict

    if not enabled:
        return MatchVerdict(
            KIND_NONE, True, artist, title, engine="deterministic", reason="ai disabled"
        )

    try:
        return await ai(artist, title, candidates, audio_tier)
    except Exception:
        # Graceful degradation: returning no-suggestion is a safe fallback (the
        # user just gets no auto-match). Log at WARNING (with traceback) so a
        # transient AI blip does not page as a red "new error pattern".
        logger.warning(
            "match-judge AI call failed; returning no-suggestion", exc_info=True
        )
        return MatchVerdict(
            KIND_NONE, True, artist, title, engine="ai", reason="ai failed"
        )
