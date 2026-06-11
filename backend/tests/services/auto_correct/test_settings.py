"""Tests for AutoCorrectSettings parsing/validation."""
from __future__ import annotations

import pytest

from backend.services.auto_correct.settings import (
    AutoCorrectSettings,
    settings_from_dict,
)


def test_default_settings() -> None:
    s = AutoCorrectSettings()
    assert s.suggest_adlib_removal is True
    assert s.allow_insertions is True
    assert s.min_confidence == 0.0


def test_settings_from_dict_defaults_when_empty() -> None:
    assert settings_from_dict({}) == AutoCorrectSettings()


def test_settings_from_dict_partial() -> None:
    s = settings_from_dict({"suggest_adlib_removal": False})
    assert s.suggest_adlib_removal is False
    assert s.allow_insertions is True


def test_settings_from_dict_full() -> None:
    s = settings_from_dict(
        {
            "suggest_adlib_removal": False,
            "allow_insertions": False,
            "min_confidence": 0.75,
        }
    )
    assert s == AutoCorrectSettings(
        suggest_adlib_removal=False, allow_insertions=False, min_confidence=0.75
    )


def test_settings_from_dict_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown settings"):
        settings_from_dict({"bogus": 1})


@pytest.mark.parametrize("value", [-0.1, 1.5, "high", None])
def test_settings_from_dict_invalid_confidence_raises(value) -> None:
    with pytest.raises(ValueError, match="min_confidence"):
        settings_from_dict({"min_confidence": value})


@pytest.mark.parametrize("key", ["suggest_adlib_removal", "allow_insertions"])
def test_settings_from_dict_non_bool_flag_raises(key) -> None:
    with pytest.raises(ValueError, match=key):
        settings_from_dict({key: "yes"})


def test_to_dict_round_trip() -> None:
    s = AutoCorrectSettings(suggest_adlib_removal=False, min_confidence=0.5)
    assert settings_from_dict(s.to_dict()) == s
