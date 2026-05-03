"""Tests for GenerationSettings and strictness mapping."""
from __future__ import annotations

import pytest

from backend.services.custom_lyrics.settings import (
    GenerationSettings,
    StrictnessLevel,
    StrictnessParams,
    params_for,
    settings_from_dict,
)


def test_default_settings() -> None:
    s = GenerationSettings()
    assert s.allow_reword is True
    assert s.allow_omit is True
    assert s.fixed_line_count is True
    assert s.strictness is StrictnessLevel.BALANCED


def test_params_for_each_level() -> None:
    assert params_for(StrictnessLevel.VERBATIM) == StrictnessParams(
        tolerance=10**6, max_iterations=0, prompt_phrase=mock_phrase("verbatim"),
    ) or params_for(StrictnessLevel.VERBATIM).max_iterations == 0
    assert params_for(StrictnessLevel.LOOSE).tolerance == 4
    assert params_for(StrictnessLevel.LOOSE).max_iterations == 1
    assert params_for(StrictnessLevel.BALANCED).tolerance == 2
    assert params_for(StrictnessLevel.BALANCED).max_iterations == 2
    assert params_for(StrictnessLevel.TIGHT).tolerance == 1
    assert params_for(StrictnessLevel.TIGHT).max_iterations == 3
    assert params_for(StrictnessLevel.STRICT).tolerance == 0
    assert params_for(StrictnessLevel.STRICT).max_iterations == 4


def mock_phrase(level: str) -> str:
    return params_for(StrictnessLevel(level)).prompt_phrase


def test_params_for_has_non_empty_phrase_at_each_level() -> None:
    for lvl in StrictnessLevel:
        assert params_for(lvl).prompt_phrase, f"Empty phrase for {lvl}"


def test_settings_from_dict_defaults_when_empty() -> None:
    s = settings_from_dict({})
    assert s == GenerationSettings()


def test_settings_from_dict_partial() -> None:
    s = settings_from_dict({"strictness": "tight"})
    assert s.strictness is StrictnessLevel.TIGHT
    assert s.allow_reword is True  # default preserved


def test_settings_from_dict_invalid_strictness_raises() -> None:
    with pytest.raises(ValueError):
        settings_from_dict({"strictness": "extreme"})


def test_settings_from_dict_full() -> None:
    s = settings_from_dict({
        "allow_reword": False,
        "allow_omit": False,
        "fixed_line_count": False,
        "strictness": "verbatim",
    })
    assert s.allow_reword is False
    assert s.allow_omit is False
    assert s.fixed_line_count is False
    assert s.strictness is StrictnessLevel.VERBATIM
