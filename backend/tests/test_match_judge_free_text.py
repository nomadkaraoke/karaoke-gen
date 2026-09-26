"""Free-text song resolution (kjbox singer search auto-correct)."""
import asyncio

import pytest

from backend.services.match_judge.free_text import resolve_free_text, verdict_from_response


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
