"""Match-judge orchestration.

Composes the deterministic+catalog classifier with the AI judge. The AI judge
runs ONLY when the classifier can't confidently decide, and never blocks or
errors the caller — any failure degrades to "no suggestion".

Evidence comes from two places: decide's BigQuery catalogue (a July 2025 Spotify
snapshot + MusicBrainz) and, when that can't confirm the typed song, a **live
Spotify search** (via decide's partner route). The live search is what knows 2025+
releases and small artists.

**Grounding rule** (2026-11-06, "Cole Phillips – Roll of Dice" became "Roll of the
Dice"): a confident AI correction must name a song the catalogue or live Spotify
actually has. Otherwise it is downgraded to ``confident=False``, which both clients
show as "Did you mean…?" and never apply.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Literal, Optional

from dataclasses import replace

from backend.services.match_judge.classifier import classify_catalog_match, normalize_for_match
from backend.services.match_judge.verdict import KIND_CONTENT, KIND_COSMETIC, KIND_NONE, MatchVerdict

logger = logging.getLogger(__name__)

CATALOG_CANDIDATE_LIMIT = 8

Stage = Literal["fast", "full"]

# Audio tiers at or above this are "weak" (poor / no results). A weak tier hints
# the typed title may be a typo, so even a confident catalog match is verified
# with the AI judge (it can be a junk/misspelled Spotify entry).
WEAK_TIER = 3

SearchTracks = Callable[[str, Optional[str], int], Awaitable[list[dict]]]
AiJudge = Callable[[str, str, list[dict], Optional[int]], Awaitable[MatchVerdict]]
# Live Spotify track search: candidate dicts, or None when there's no evidence either way.
SpotifySearch = Callable[[str], Awaitable[Optional[list[dict]]]]


async def _default_search_tracks(query: str, artist: Optional[str], limit: int) -> list[dict]:
    from backend.services.catalog_proxy_service import search_tracks
    return await search_tracks(query, artist, limit)


async def _default_spotify_search(query: str) -> Optional[list[dict]]:
    from backend.services.catalog_proxy_service import spotify_search
    return await spotify_search(query)


async def _safe_spotify(spotify: SpotifySearch, query: str) -> list[dict]:
    try:
        return list(await spotify(query) or [])
    except Exception:
        logger.warning("match-judge Spotify search failed; continuing without it", exc_info=True)
        return []


def _merge(*groups: list[dict]) -> list[dict]:
    """Concatenate candidate lists, dropping repeats of the same artist/title."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for group in groups:
        for c in group:
            key = (normalize_for_match(c.get("artist_name") or ""), normalize_for_match(c.get("track_name") or ""))
            if key in seen or not all(key):
                continue
            seen.add(key)
            out.append(c)
    return out


def _names(candidate: dict) -> list[str]:
    names = [candidate.get("artist_name") or ""] + list(candidate.get("artist_names") or [])
    return [n for n in names if n]


def names_song(candidates: list[dict], artist: str, title: str) -> bool:
    """True when a candidate is this artist/title, after normalising. Any credited artist counts."""
    key_artist, key_title = normalize_for_match(artist), normalize_for_match(title)
    if not key_artist or not key_title:
        return False
    return any(
        normalize_for_match(c.get("track_name") or "") == key_title
        and any(normalize_for_match(n) == key_artist for n in _names(c))
        for c in candidates
    )


async def ground_verdict(
    verdict: MatchVerdict, candidates: list[dict], spotify: SpotifySearch
) -> MatchVerdict:
    """Apply the grounding rule to an AI verdict (see module docstring).

    A confident cosmetic/content verdict is kept only when its canonical song is
    among ``candidates``, or turns up in one live Spotify search for it.
    """
    if not (verdict.confident and verdict.kind in (KIND_COSMETIC, KIND_CONTENT)):
        return verdict
    artist, title = verdict.canonical_artist, verdict.canonical_title
    if names_song(candidates, artist, title):
        return verdict
    if names_song(await _safe_spotify(spotify, f"{artist} {title}"), artist, title):
        return verdict
    logger.info(
        f"match-judge: AI suggestion {artist!r} / {title!r} not found in catalog or Spotify; "
        "downgraded to not confident"
    )
    return replace(verdict, confident=False, reason=f"{verdict.reason} [unconfirmed: not in catalog or Spotify]".strip())


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
    spotify_search: Optional[SpotifySearch] = None,
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
    spotify = spotify_search or _default_spotify_search
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
    if catalog_verdict is None:
        # The snapshot doesn't have it: ask live Spotify. An exact (normalised) hit
        # confirms the typed song, e.g. "Roll of dice" -> "Roll of Dice".
        live = await _safe_spotify(spotify, f"{artist} {title}")
        spotify_verdict = classify_catalog_match(artist, title, live)
        if spotify_verdict is not None:
            catalog_verdict = replace(spotify_verdict, reason=f"{spotify_verdict.reason} (live Spotify)")
        candidates = _merge(candidates, live)

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
        ai_verdict = await ground_verdict(ai_verdict, candidates, spotify)
        if ai_verdict.confident and ai_verdict.kind != KIND_NONE:
            return ai_verdict
        return catalog_verdict

    if not enabled:
        return MatchVerdict(
            KIND_NONE, True, artist, title, engine="deterministic", reason="ai disabled"
        )

    try:
        ai_verdict = await ai(artist, title, candidates, audio_tier)
    except Exception as exc:
        from backend.services.gemini_client import note_gemini_failure

        note_gemini_failure("match_judge", exc)
        # Graceful degradation: returning no-suggestion is a safe fallback (the
        # user just gets no auto-match). Log at WARNING (with traceback) so a
        # transient AI blip does not page as a red "new error pattern".
        logger.warning(
            "match-judge AI call failed; returning no-suggestion", exc_info=True
        )
        return MatchVerdict(
            KIND_NONE, True, artist, title, engine="ai", reason="ai failed"
        )
    return await ground_verdict(ai_verdict, candidates, spotify)
