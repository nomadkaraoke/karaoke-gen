"""Orchestration tests for the match-judge service.

The service composes the deterministic+catalog classifier with the AI judge:
the AI judge is invoked ONLY when the classifier can't confidently decide.
Catalog search and the AI judge are injected so these tests touch no network.
"""
import pytest

from backend.services.match_judge.service import judge_match
from backend.services.match_judge.verdict import (
    KIND_AMBIGUOUS,
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


# --- Fast stage (catalog-only parallel pass) ---


@pytest.mark.asyncio
async def test_fast_stage_returns_catalog_verdict_without_ai():
    verdict = await judge_match(
        "paramore",
        "big man, little dignity",
        stage="fast",
        search_tracks=_candidates(("Paramore", "Big Man, Little Dignity")),
        ai_judge=_ai_never,
        enabled=True,
    )
    assert verdict.kind == KIND_COSMETIC
    assert verdict.engine == "catalog"
    assert verdict.needs_ai is False


@pytest.mark.asyncio
async def test_fast_stage_undecided_signals_needs_ai_without_calling_ai():
    verdict = await judge_match(
        "queen",
        "bohemian rapsody",
        stage="fast",
        search_tracks=_candidates(("Queen", "Radio Ga Ga")),
        ai_judge=_ai_never,
        enabled=True,
    )
    assert verdict.kind == KIND_NONE
    assert verdict.needs_ai is True
    assert verdict.confident is False


@pytest.mark.asyncio
async def test_fast_stage_never_calls_ai_even_when_undecided():
    # _ai_never raises if called; reaching here proves the fast pass skipped AI.
    verdict = await judge_match(
        "queen", "bohemian rapsody", stage="fast",
        search_tracks=_candidates(), ai_judge=_ai_never, enabled=True,
    )
    assert verdict.needs_ai is True


# --- Item 4: verify a confident catalog match with AI when the audio tier is weak ---


@pytest.mark.asyncio
async def test_weak_tier_catalog_match_is_verified_and_overridden_by_ai():
    async def ai(artist, title, candidates, audio_tier):
        return MatchVerdict(
            KIND_CONTENT, True, "Queen", "Bohemian Rhapsody",
            engine="ai", reason="typo",
        )

    # Catalog "matched" the user's exact typo (junk entry) → would normally be
    # 'none'/already-canonical. Weak tier (3) makes us verify with AI, which overrides.
    verdict = await judge_match(
        "Queen", "Bohemian Rapsody",
        audio_tier=3,
        search_tracks=_candidates(("Queen", "Bohemian Rapsody")),
        ai_judge=ai,
        enabled=True,
    )
    assert verdict.kind == KIND_CONTENT
    assert verdict.canonical_title == "Bohemian Rhapsody"
    assert verdict.engine == "ai"


@pytest.mark.asyncio
async def test_strong_tier_catalog_match_still_skips_ai():
    verdict = await judge_match(
        "Queen", "Bohemian Rapsody",
        audio_tier=1,
        search_tracks=_candidates(("Queen", "Bohemian Rapsody")),
        ai_judge=_ai_never,
        enabled=True,
    )
    # Strong audio results → trust the catalog match, no AI.
    assert verdict.engine == "catalog"


@pytest.mark.asyncio
async def test_weak_tier_keeps_catalog_verdict_when_ai_unconfident():
    async def ai(artist, title, candidates, audio_tier):
        return MatchVerdict(KIND_NONE, False, artist, title, engine="ai")

    verdict = await judge_match(
        "paramore", "big man, little dignity",
        audio_tier=3,
        search_tracks=_candidates(("Paramore", "Big Man, Little Dignity")),
        ai_judge=ai,
        enabled=True,
    )
    # AI wasn't confident about a change → keep the cosmetic catalog tidy.
    assert verdict.kind == KIND_COSMETIC
    assert verdict.engine == "catalog"


@pytest.mark.asyncio
async def test_weak_tier_keeps_catalog_verdict_when_ai_fails():
    async def ai(*_a, **_k):
        raise RuntimeError("model exploded")

    verdict = await judge_match(
        "paramore", "big man, little dignity",
        audio_tier=3,
        search_tracks=_candidates(("Paramore", "Big Man, Little Dignity")),
        ai_judge=ai,
        enabled=True,
    )
    assert verdict.kind == KIND_COSMETIC
    assert verdict.engine == "catalog"


@pytest.mark.asyncio
async def test_weak_tier_does_not_verify_when_ai_disabled():
    verdict = await judge_match(
        "paramore", "big man, little dignity",
        audio_tier=3,
        search_tracks=_candidates(("Paramore", "Big Man, Little Dignity")),
        ai_judge=_ai_never,
        enabled=False,
    )
    assert verdict.kind == KIND_COSMETIC
    assert verdict.engine == "catalog"


@pytest.mark.asyncio
async def test_weak_tier_ai_can_escalate_to_ambiguous():
    async def ai(artist, title, candidates, audio_tier):
        return MatchVerdict(
            KIND_AMBIGUOUS, True, "Lewis Capaldi", "Bruises",
            alternatives=[{"artist": "Fox Stevenson", "title": "Bruises"}],
            engine="ai",
        )

    verdict = await judge_match(
        "capaldi", "bruises",
        audio_tier=3,
        search_tracks=_candidates(("Lewis Capaldi", "Bruises")),
        ai_judge=ai,
        enabled=True,
    )
    assert verdict.kind == KIND_AMBIGUOUS
    assert verdict.alternatives == [{"artist": "Fox Stevenson", "title": "Bruises"}]


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
