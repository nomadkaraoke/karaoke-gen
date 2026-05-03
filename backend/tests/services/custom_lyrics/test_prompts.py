"""Snapshot tests for prompt construction across settings."""
from __future__ import annotations

from backend.services.custom_lyrics.prompts import (
    build_initial_user_prompt,
    build_repair_user_prompt,
    build_system_prompt,
)
from backend.services.custom_lyrics.settings import GenerationSettings, StrictnessLevel
from backend.services.custom_lyrics.validator import LineValidation, Severity


def test_system_prompt_balanced_default() -> None:
    s = GenerationSettings()
    out = build_system_prompt(s)
    assert "professional karaoke lyricist" in out
    assert "Match each line's syllable count within 2" in out
    # default toggles: no extra restrictive rules added
    assert "Do NOT paraphrase" not in out
    assert "MUST appear in the output" not in out


def test_system_prompt_no_reword_no_omit() -> None:
    s = GenerationSettings(allow_reword=False, allow_omit=False)
    out = build_system_prompt(s)
    assert "Do NOT paraphrase" in out
    assert "MUST appear in the output" in out


def test_system_prompt_strictness_phrase() -> None:
    out = build_system_prompt(GenerationSettings(strictness=StrictnessLevel.STRICT))
    assert "Rhythm precision is the priority" in out


def test_system_prompt_fixed_line_count_rule() -> None:
    out = build_system_prompt(GenerationSettings(fixed_line_count=True))
    assert "exactly the same number of lines" in out


def test_system_prompt_flexible_line_count() -> None:
    out = build_system_prompt(GenerationSettings(fixed_line_count=False))
    assert "any number of lines" in out
    assert "exactly the same number" not in out


def test_initial_prompt_includes_per_line_metadata() -> None:
    target_lines = ["alpha beta", "gamma"]
    target_syllables = [[3, 3, 3, 3], [2, 2, 2, 2]]
    time_budgets = [1.5, 0.7]
    rates = [2.0, 2.86]
    out = build_initial_user_prompt(
        artist="Some Artist",
        title="Some Title",
        target_lines=target_lines,
        target_syllables=target_syllables,
        time_budgets=time_budgets,
        observed_rates=rates,
        custom_text_block="my custom lyric content",
        notes=None,
        settings=GenerationSettings(),
    )
    assert "Some Artist" in out
    assert "Some Title" in out
    assert "≈3 syl" in out
    assert "1.5s" in out
    assert "alpha beta" in out
    assert "my custom lyric content" in out


def test_repair_prompt_lists_violations_only() -> None:
    violations = [
        LineValidation(
            line_index=0,
            target_text="alpha",
            candidate_text="alpha beta gamma delta",
            target_syllables=[2, 2, 2, 2],
            candidate_syllables=[6, 6, 6, 6],
            min_delta=4,
            passes=False,
            severity=Severity.MAJOR,
            time_budget_seconds=0.8,
        )
    ]
    previous_output = ["alpha beta gamma delta", "fine line"]
    out = build_repair_user_prompt(
        previous_output=previous_output,
        violations=violations,
        target_lines=["alpha", "fine"],
        target_syllables=[[2, 2, 2, 2], [1, 1, 1, 1]],
        time_budgets=[0.8, 0.4],
        observed_rates=[2.5, 2.5],
        settings=GenerationSettings(),
    )
    assert "Line 1" in out
    assert "alpha" in out
    assert "alpha beta gamma delta" in out
    assert "+4 over" in out
    assert "Lines to keep unchanged: 2" in out


def test_repair_prompt_no_violations_returns_empty_signal() -> None:
    out = build_repair_user_prompt(
        previous_output=["fine"],
        violations=[],
        target_lines=["fine"],
        target_syllables=[[1, 1, 1, 1]],
        time_budgets=[0.4],
        observed_rates=[2.5],
        settings=GenerationSettings(),
    )
    # Caller should never invoke with empty violations; we still return a sensible string
    assert "no violations" in out.lower() or out == ""
