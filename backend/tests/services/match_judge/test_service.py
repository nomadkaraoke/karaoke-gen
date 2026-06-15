"""Orchestration tests for the match-judge service.

The service composes the deterministic+catalog classifier with the AI judge:
the AI judge is invoked ONLY when the classifier can't confidently decide.
Catalog search and the AI judge are injected so these tests touch no network.
"""
import pytest

from backend.services.match_judge.service import judge_match
from backend.services.match_judge.verdict import (
    KIND_COSMETIC,
    KIND_CONTENT,
    KIND_NONE,
    MatchVerdict,
)


def _candidates(*pairs):
    async def _search(query, artist, limit):
        return [{"artist_name": a, "track_name": t} for a, t in pairs]
    return _search


def _ai_never(*_a, **_k):
    raise AssertionError("AI judge must not be called")


@pytest.mark.asyncio
async def test_catalog_cosmetic_match_skips_ai():
    calls = {"ai": 0}

    async def ai(*_a, **_k):
        calls["ai"] += 1
        raise AssertionError("should not run")

    verdict = await judge_match(
        "paramore",
        "big man, little dignity",
        search_tracks=_candidates(("Paramore", "Big Man, Little Dignity")),
        ai_judge=ai,
        enabled=True,
    )
    assert verdict.kind == KIND_COSMETIC
    assert verdict.engine == "catalog"
    assert calls["ai"] == 0


@pytest.mark.asyncio
async def test_no_catalog_match_calls_ai():
    seen = {}

    async def ai(artist, title, candidates, audio_tier):
        seen["candidates"] = candidates
        seen["tier"] = audio_tier
        return MatchVerdict(
            kind=KIND_CONTENT,
            confident=True,
            canonical_artist="Queen",
            canonical_title="Bohemian Rhapsody",
            engine="ai",
            reason="typo fix",
        )

    verdict = await judge_match(
        "queen",
        "bohemian rapsody",
        audio_tier=3,
        search_tracks=_candidates(("Queen", "Radio Ga Ga")),
        ai_judge=ai,
        enabled=True,
    )
    assert verdict.kind == KIND_CONTENT
    assert verdict.engine == "ai"
    assert seen["tier"] == 3
    assert seen["candidates"] == [{"artist_name": "Queen", "track_name": "Radio Ga Ga"}]


@pytest.mark.asyncio
async def test_disabled_flag_skips_ai_and_returns_none():
    verdict = await judge_match(
        "queen",
        "bohemian rapsody",
        search_tracks=_candidates(("Queen", "Radio Ga Ga")),
        ai_judge=_ai_never,
        enabled=False,
    )
    assert verdict.kind == KIND_NONE
    assert verdict.confident is True
    assert verdict.canonical_artist == "queen"  # leaves user input untouched


@pytest.mark.asyncio
async def test_ai_failure_falls_back_to_none():
    async def ai(*_a, **_k):
        raise RuntimeError("model exploded")

    verdict = await judge_match(
        "queen",
        "bohemian rapsody",
        search_tracks=_candidates(("Queen", "Radio Ga Ga")),
        ai_judge=ai,
        enabled=True,
    )
    assert verdict.kind == KIND_NONE
    assert verdict.canonical_title == "bohemian rapsody"


@pytest.mark.asyncio
async def test_catalog_search_failure_still_lets_ai_run():
    async def failing_search(query, artist, limit):
        raise RuntimeError("catalog down")

    async def ai(artist, title, candidates, audio_tier):
        assert candidates == []  # gracefully degraded to no candidates
        return MatchVerdict(KIND_NONE, True, artist, title, engine="ai")

    verdict = await judge_match(
        "queen",
        "bohemian rhapsody",
        search_tracks=failing_search,
        ai_judge=ai,
        enabled=True,
    )
    assert verdict.engine == "ai"
