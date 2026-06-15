"""Tests for the AI judge: prompt construction + response→verdict mapping.

The model call (`generate`) is injected, so these tests are offline and
deterministic. The real `generate` wraps the Vertex genai client.
"""
import pytest

from backend.services.match_judge.ai import ai_judge_match, build_prompts
from backend.services.match_judge.verdict import (
    KIND_AMBIGUOUS,
    KIND_CONTENT,
    KIND_NONE,
)


def _gen(response: dict):
    async def generate(model, system_prompt, user_prompt):
        return response
    return generate


class TestBuildPrompts:
    def test_prompt_includes_input_candidates_and_tier(self):
        system, user = build_prompts(
            "queen",
            "bohemian rapsody",
            [{"artist_name": "Queen", "track_name": "Bohemian Rhapsody"}],
            audio_tier=3,
        )
        assert "bohemian rapsody" in user
        assert "Bohemian Rhapsody" in user  # candidate shown
        # The poor-results signal must reach the model so it can favour a typo fix.
        assert "3" in user


class TestResponseMapping:
    @pytest.mark.asyncio
    async def test_confident_content_correction(self):
        verdict = await ai_judge_match(
            "queen",
            "bohemian rapsody",
            [],
            3,
            generate=_gen(
                {
                    "kind": "content",
                    "confident": True,
                    "canonical_artist": "Queen",
                    "canonical_title": "Bohemian Rhapsody",
                    "alternatives": [],
                    "reason": "typo",
                }
            ),
        )
        assert verdict.kind == KIND_CONTENT
        assert verdict.confident is True
        assert verdict.canonical_title == "Bohemian Rhapsody"
        assert verdict.engine == "ai"

    @pytest.mark.asyncio
    async def test_ambiguous_carries_alternatives(self):
        verdict = await ai_judge_match(
            "",
            "bruises",
            [],
            3,
            generate=_gen(
                {
                    "kind": "ambiguous",
                    "confident": False,
                    "canonical_artist": "Lewis Capaldi",
                    "canonical_title": "Bruises",
                    "alternatives": [{"artist": "Fox Stevenson", "title": "Bruises"}],
                    "reason": "multiple artists",
                }
            ),
        )
        assert verdict.kind == KIND_AMBIGUOUS
        assert verdict.alternatives == [{"artist": "Fox Stevenson", "title": "Bruises"}]

    @pytest.mark.asyncio
    async def test_unknown_kind_is_treated_as_none(self):
        verdict = await ai_judge_match(
            "queen",
            "bohemian rhapsody",
            [],
            1,
            generate=_gen({"kind": "garbage", "confident": True}),
        )
        assert verdict.kind == KIND_NONE
        assert verdict.canonical_artist == "queen"  # falls back to user input

    @pytest.mark.asyncio
    async def test_missing_canonical_falls_back_to_input(self):
        verdict = await ai_judge_match(
            "queen",
            "bohemian rhapsody",
            [],
            1,
            generate=_gen({"kind": "content", "confident": True}),
        )
        assert verdict.canonical_artist == "queen"
        assert verdict.canonical_title == "bohemian rhapsody"
