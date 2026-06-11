"""Tests for auto-correct prompt construction."""
from __future__ import annotations

from backend.services.auto_correct.prompts import (
    build_system_prompt,
    build_user_prompt,
)
from backend.services.auto_correct.settings import AutoCorrectSettings


def test_system_prompt_default_allows_adlib_suggestions() -> None:
    p = build_system_prompt(AutoCorrectSettings())
    assert "adlib_removal" in p
    assert "NEVER suggest deleting backing vocals" not in p
    assert "insert_after" in p


def test_system_prompt_adlib_disabled() -> None:
    p = build_system_prompt(AutoCorrectSettings(suggest_adlib_removal=False))
    assert "NEVER suggest deleting backing vocals" in p


def test_system_prompt_insertions_disabled() -> None:
    p = build_system_prompt(AutoCorrectSettings(allow_insertions=False))
    assert 'NEVER use the "insert_after" operation' in p


def test_user_prompt_word_indices_are_global_and_sequential() -> None:
    segments = [
        {"id": "s1", "words": [{"id": "a", "text": "Hello"}, {"id": "b", "text": "there"}]},
        {"id": "s2", "words": [{"id": "c", "text": "world"}]},
    ]
    refs = {
        "genius": {"segments": [{"text": "Hello there"}, {"text": "world"}]},
        "lrclib": {"segments": [{"text": "Hello their world"}]},
    }
    p = build_user_prompt(
        segments=segments, reference_lyrics=refs, artist="Artist", title="Title"
    )
    assert "ARTIST: Artist" in p
    assert "[L000] 0:Hello 1:there" in p
    assert "[L001] 2:world" in p
    assert "REFERENCE LYRICS (genius):" in p
    assert "REFERENCE LYRICS (lrclib):" in p
    assert "Hello their world" in p


def test_user_prompt_handles_missing_metadata() -> None:
    p = build_user_prompt(
        segments=[{"id": "s1", "words": []}],
        reference_lyrics={},
        artist=None,
        title=None,
    )
    assert "ARTIST: unknown" in p
    assert "TITLE: unknown" in p
