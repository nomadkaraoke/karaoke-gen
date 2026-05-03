"""Tests for the LLM-call disk cache."""
from __future__ import annotations

import tempfile
from pathlib import Path

from backend.eval.custom_lyrics.cache import LlmCallCache


def test_miss_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp))
        assert c.get("system", "user", model="m1", settings_dict={"x": 1}) is None


def test_set_then_get_hits() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp))
        c.set("system", "user", model="m1", settings_dict={"x": 1}, response_lines=["a", "b"])
        assert c.get("system", "user", model="m1", settings_dict={"x": 1}) == ["a", "b"]


def test_different_settings_different_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp))
        c.set("system", "user", model="m1", settings_dict={"x": 1}, response_lines=["a"])
        assert c.get("system", "user", model="m1", settings_dict={"x": 2}) is None


def test_replay_only_raises_on_miss() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = LlmCallCache(Path(tmp), replay_only=True)
        try:
            c.get("system", "user", model="m1", settings_dict={"x": 1})
        except RuntimeError as e:
            assert "cache miss" in str(e).lower()
        else:
            raise AssertionError("expected RuntimeError")
