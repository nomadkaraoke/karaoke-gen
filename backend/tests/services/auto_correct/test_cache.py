"""Tests for the per-job auto-correct suggestion cache."""
from __future__ import annotations

from unittest.mock import patch

from backend.services.auto_correct.service import (
    AutoCorrectResult,
    AutoCorrectService,
    Suggestion,
)
from backend.services.auto_correct.settings import AutoCorrectSettings


SEGMENTS = [
    {
        "id": "seg-1",
        "words": [
            {"id": "w0", "text": "straight"},
            {"id": "w1", "text": "glory"},
        ],
    },
]
REFS = {"genius": {"segments": [{"text": "straight chlorine"}]}}
RAW = {
    "suggestions": [
        {
            "op": "replace",
            "start_idx": 1,
            "end_idx": 1,
            "new_text": "chlorine",
            "reason": "r",
            "category": "mishearing",
            "confidence": 0.9,
        }
    ]
}

FLAT = [("w0", "straight", "seg-1"), ("w1", "glory", "seg-1")]


def _suggest(service, **overrides):
    kwargs = dict(
        job_id="job-1",
        segments=SEGMENTS,
        reference_lyrics=REFS,
        artist="A",
        title="T",
        settings=AutoCorrectSettings(),
    )
    kwargs.update(overrides)
    return service.suggest(**kwargs)


def test_cache_hit_skips_model_call() -> None:
    service = AutoCorrectService()
    cached_result = AutoCorrectResult(
        suggestions=[
            Suggestion(
                id="cached-1",
                op="replace",
                word_ids=["w1"],
                segment_ids=["seg-1"],
                original_text="glory",
                new_text="chlorine",
                reason="r",
                category="mishearing",
                confidence=0.9,
            )
        ],
        model="claude-fable-5",
        elapsed_seconds=0.0,
        settings_applied=AutoCorrectSettings(),
        cached=True,
    )
    with patch.object(service, "_cache_get", return_value=cached_result), \
         patch.object(service, "_call_model") as call, \
         patch.object(service, "_cache_put") as put:
        result = _suggest(service)
    call.assert_not_called()
    put.assert_not_called()
    assert result.cached is True
    assert result.suggestions[0].id == "cached-1"


def test_cache_miss_calls_model_and_writes_cache() -> None:
    service = AutoCorrectService()
    with patch.object(service, "_cache_get", return_value=None), \
         patch.object(service, "_call_model", return_value=(RAW, None)) as call, \
         patch.object(service, "_cache_put") as put:
        result = _suggest(service)
    call.assert_called_once()
    put.assert_called_once()
    assert result.cached is False
    assert put.call_args.args[1] is result


def test_cache_key_stable_for_identical_input() -> None:
    s = AutoCorrectSettings()
    k1 = AutoCorrectService._cache_path("j", ["m"], s, FLAT, REFS, "A", "T")
    k2 = AutoCorrectService._cache_path("j", ["m"], s, FLAT, REFS, "A", "T")
    assert k1 == k2
    assert k1.startswith("jobs/j/lyrics/auto_correct_cache/")


def test_cache_key_changes_with_input() -> None:
    s = AutoCorrectSettings()
    base = AutoCorrectService._cache_path("j", ["m"], s, FLAT, REFS, "A", "T")
    edited_words = [("w0", "straight", "seg-1"), ("w1", "chlorine", "seg-1")]
    assert AutoCorrectService._cache_path("j", ["m"], s, edited_words, REFS, "A", "T") != base
    assert AutoCorrectService._cache_path("j", ["m", "m2"], s, FLAT, REFS, "A", "T") != base
    other_settings = AutoCorrectSettings(min_confidence=0.8)
    assert AutoCorrectService._cache_path("j", ["m"], other_settings, FLAT, REFS, "A", "T") != base
    other_refs = {"genius": {"segments": [{"text": "different text"}]}}
    assert AutoCorrectService._cache_path("j", ["m"], s, FLAT, other_refs, "A", "T") != base


def test_cache_get_round_trips_via_storage_payload() -> None:
    """_cache_put's payload must be readable by _cache_get."""
    service = AutoCorrectService()
    stored = {}

    class FakeBlob:
        def exists(self):
            return bool(stored)

    class FakeStorage:
        class bucket:  # noqa: N801 - mimic attribute access
            @staticmethod
            def blob(path):
                return FakeBlob()

        @staticmethod
        def upload_json(path, data):
            stored[path] = data

        @staticmethod
        def download_json(path):
            return stored[path]

    with patch.object(service, "_get_storage", return_value=FakeStorage()):
        original = AutoCorrectResult(
            suggestions=[
                Suggestion(
                    id="s1",
                    op="replace",
                    word_ids=["w1"],
                    segment_ids=["seg-1"],
                    original_text="glory",
                    new_text="chlorine",
                    reason="r",
                    category="mishearing",
                    confidence=0.9,
                    models=["m1", "m2"],
                    consensus=2,
                    total_models=2,
                    conflict_group=None,
                )
            ],
            model="m1, m2",
            elapsed_seconds=5.0,
            settings_applied=AutoCorrectSettings(compare_models=True),
            warnings=["w"],
        )
        service._cache_put("p", original)
        restored = service._cache_get("p")

    assert restored is not None
    assert restored.cached is True
    assert restored.model == "m1, m2"
    assert restored.warnings == ["w"]
    assert restored.settings_applied == AutoCorrectSettings(compare_models=True)
    s = restored.suggestions[0]
    assert s.consensus == 2 and s.models == ["m1", "m2"]


def test_corrupt_cache_entry_is_a_miss() -> None:
    service = AutoCorrectService()

    class FakeBlob:
        def exists(self):
            return True

    class FakeStorage:
        class bucket:  # noqa: N801
            @staticmethod
            def blob(path):
                return FakeBlob()

        @staticmethod
        def download_json(path):
            return {"suggestions": [{"bogus": True}], "model": "m"}

    with patch.object(service, "_get_storage", return_value=FakeStorage()):
        assert service._cache_get("p") is None


def test_storage_failure_never_breaks_request() -> None:
    service = AutoCorrectService()
    with patch.object(service, "_get_storage", side_effect=RuntimeError("no gcs")), \
         patch.object(service, "_call_model", return_value=(RAW, None)):
        result = _suggest(service)
    assert result.cached is False
    assert len(result.suggestions) == 1
