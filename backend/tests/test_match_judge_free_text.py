"""Free-text song resolution (kjbox singer search auto-correct)."""
import asyncio

import pytest

from backend.services.match_judge.free_text import (
    resolve_and_tidy,
    resolve_free_text,
    verdict_from_response,
)
from backend.services.match_judge.verdict import MatchVerdict


def _run(data=None, exc=None):
    async def gen(model, system_prompt, user_prompt):
        assert "the strokes max picu" in user_prompt
        if exc:
            raise exc
        return data
    return asyncio.run(resolve_free_text("the strokes max picu", generate=gen, model="m"))


def test_typo_is_corrected_and_split():
    v = _run({"kind": "content", "confident": True, "typed_artist": "the strokes",
              "typed_title": "max picu", "canonical_artist": "The Strokes",
              "canonical_title": "Machu Picchu", "reason": "typo"})
    assert v["kind"] == "content" and v["confident"] is True
    assert (v["canonical_artist"], v["canonical_title"]) == ("The Strokes", "Machu Picchu")
    assert (v["typed_artist"], v["typed_title"]) == ("the strokes", "max picu")
    assert v["engine"] == "ai"


def test_model_failure_is_none_not_an_error():
    v = _run(exc=TimeoutError("slow"))
    assert v["kind"] == "none" and v["reason"] == "unavailable"


@pytest.mark.parametrize("data", [
    None, "nope", {"kind": "bogus", "confident": True},
    {"kind": "content", "confident": True, "canonical_artist": "The Strokes"},   # no title
    {"kind": "ambiguous", "confident": False},                                   # no alternatives
    {"kind": "content", "confident": True, "canonical_artist": ["x"], "canonical_title": "T"},
    {"kind": "ambiguous", "confident": False, "alternatives": "Radiohead - Creep"},
    {"kind": "ambiguous", "confident": False, "alternatives": [{"artist": {"a": 1}, "title": "T"}]},
])
def test_bad_model_output_degrades_to_none(data):
    assert verdict_from_response(data, "q")["kind"] == "none"


def test_ambiguous_keeps_up_to_four_alternatives():
    alts = [{"artist": f"A{i}", "title": f"T{i}"} for i in range(6)] + [{"artist": "x"}]
    v = verdict_from_response({"kind": "ambiguous", "confident": False, "alternatives": alts}, "q")
    assert v["kind"] == "ambiguous" and len(v["alternatives"]) == 4


# ---- resolve_and_tidy: AI split → the job flow's judge_match catalog pass

def _tidy(query, ai, judge_result=None, judge_exc=None):
    seen = []

    async def gen(model, system_prompt, user_prompt):
        return ai

    async def judge(artist, title):
        seen.append((artist, title))
        if judge_exc:
            raise judge_exc
        return judge_result
    v = asyncio.run(resolve_and_tidy(query, generate=gen, judge=judge, model="m"))
    return v, seen


_CATALOG_HIT = MatchVerdict("cosmetic", True, "Rihanna", "Push Up On Me", engine="catalog")


def test_lowercase_query_is_tidied_to_catalog_formatting():
    ai = {"kind": "cosmetic", "confident": True, "typed_artist": "rihanna",
          "typed_title": "push up on me", "canonical_artist": "Rihanna",
          "canonical_title": "Push Up on Me"}
    v, seen = _tidy("rihanna push up on me", ai, _CATALOG_HIT)
    assert seen == [("Rihanna", "Push Up on Me")]            # AI's pick goes to the catalog
    assert (v["canonical_artist"], v["canonical_title"]) == ("Rihanna", "Push Up On Me")
    assert v["kind"] == "cosmetic" and v["confident"] is True and v["engine"] == "catalog"
    assert (v["typed_artist"], v["typed_title"]) == ("rihanna", "push up on me")


def test_title_first_word_order_is_still_cosmetic():
    ai = {"kind": "cosmetic", "confident": True, "typed_artist": "rihanna",
          "typed_title": "push up on me", "canonical_artist": "Rihanna",
          "canonical_title": "Push Up On Me"}
    v, _ = _tidy("push up on me rihanna", ai, _CATALOG_HIT)
    assert v["kind"] == "cosmetic"


def test_typo_fix_confirmed_by_catalog_is_content():
    ai = {"kind": "content", "confident": True, "typed_artist": "the strokes",
          "typed_title": "max picu", "canonical_artist": "the Strokes",
          "canonical_title": "Machu picchu"}
    hit = MatchVerdict("cosmetic", True, "The Strokes", "Machu Picchu", engine="catalog")
    v, _ = _tidy("the strokes max picu", ai, hit)
    assert v["kind"] == "content" and v["engine"] == "catalog"
    assert (v["canonical_artist"], v["canonical_title"]) == ("The Strokes", "Machu Picchu")


def test_unrecognised_by_ai_but_split_is_checked_against_catalog():
    ai = {"kind": "none", "confident": False, "typed_artist": "some band",
          "typed_title": "deep cut"}
    hit = MatchVerdict("none", True, "some band", "deep cut", engine="catalog",
                       reason="already canonical")
    v, seen = _tidy("some band deep cut", ai, hit)
    assert seen == [("some band", "deep cut")]
    assert v["kind"] == "cosmetic" and v["canonical_title"] == "deep cut"


@pytest.mark.parametrize("judge_result,judge_exc", [
    (MatchVerdict("none", False, "Rihanna", "Push Up on Me", engine="catalog",
                  needs_ai=True), None),                              # catalog inconclusive
    (None, RuntimeError("decide down")),
])
def test_no_catalog_match_keeps_ai_verdict(judge_result, judge_exc):
    ai = {"kind": "cosmetic", "confident": True, "typed_artist": "rihanna",
          "typed_title": "push up on me", "canonical_artist": "Rihanna",
          "canonical_title": "Push Up on Me"}
    v, _ = _tidy("rihanna push up on me", ai, judge_result, judge_exc)
    assert v["engine"] == "ai" and v["canonical_title"] == "Push Up on Me"
    # A failed catalog pass is transient — the route must not cache it.
    assert (v["reason"] == "unavailable") is (judge_exc is not None)


def test_ambiguous_and_failures_skip_the_catalog():
    alts = [{"artist": "A", "title": "T"}, {"artist": "B", "title": "T"}]
    v, seen = _tidy("t", {"kind": "ambiguous", "confident": False, "alternatives": alts})
    assert v["kind"] == "ambiguous" and seen == []
    v, seen = _tidy("t", None)
    assert v["kind"] == "none" and seen == []


def test_prompt_covers_descriptive_queries():
    from backend.services.match_judge.free_text import _SYSTEM_PROMPT
    assert "DESCRIBE" in _SYSTEM_PROMPT and "titanic" in _SYSTEM_PROMPT


def test_description_resolved_to_a_song_is_content():
    ai = {"kind": "content", "confident": True, "typed_artist": "", "typed_title": "that song from titanic",
          "canonical_artist": "Céline Dion", "canonical_title": "My Heart Will Go On"}
    hit = MatchVerdict("cosmetic", True, "Céline Dion", "My Heart Will Go On", engine="catalog")
    v, seen = _tidy("that song from titanic", ai, hit)
    assert seen == [("Céline Dion", "My Heart Will Go On")]
    assert v["kind"] == "content" and v["canonical_title"] == "My Heart Will Go On"
