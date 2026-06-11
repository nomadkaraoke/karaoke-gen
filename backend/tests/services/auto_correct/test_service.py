"""Tests for AutoCorrectService validation and suggestion mapping."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services.auto_correct.service import (
    AutoCorrectService,
    AutoCorrectServiceError,
)
from backend.services.auto_correct.settings import AutoCorrectSettings


SEGMENTS = [
    {
        "id": "seg-1",
        "words": [
            {"id": "w0", "text": "Sippin'"},
            {"id": "w1", "text": "on"},
            {"id": "w2", "text": "straight"},
            {"id": "w3", "text": "glory"},
        ],
    },
    {
        "id": "seg-2",
        "words": [
            {"id": "w4", "text": "yeah"},
            {"id": "w5", "text": "yeah"},
        ],
    },
]
REFS = {"genius": {"segments": [{"text": "Sippin' on straight chlorine"}]}}


def _suggest(raw_response, settings=None):
    service = AutoCorrectService()
    with patch.object(service, "_call_model", return_value=raw_response):
        return service.suggest(
            job_id="job-1",
            segments=SEGMENTS,
            reference_lyrics=REFS,
            artist="A",
            title="T",
            settings=settings or AutoCorrectSettings(),
        )


def _raw(op="replace", start=3, end=3, text="chlorine", category="mishearing",
         confidence=0.95):
    return {
        "suggestions": [
            {
                "op": op,
                "start_idx": start,
                "end_idx": end,
                "new_text": text,
                "reason": "matches reference",
                "category": category,
                "confidence": confidence,
            }
        ]
    }


def test_replace_maps_indices_to_word_ids() -> None:
    result = _suggest(_raw())
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert s.op == "replace"
    assert s.word_ids == ["w3"]
    assert s.segment_ids == ["seg-1"]
    assert s.original_text == "glory"
    assert s.new_text == "chlorine"
    assert result.warnings == []


def test_multi_word_span_crossing_segments() -> None:
    result = _suggest(_raw(start=3, end=4, text="chlorine yeah"))
    s = result.suggestions[0]
    assert s.word_ids == ["w3", "w4"]
    assert sorted(s.segment_ids) == ["seg-1", "seg-2"]
    assert s.original_text == "glory yeah"


def test_delete_suggestion() -> None:
    result = _suggest(_raw(op="delete", start=4, end=5, text=""))
    s = result.suggestions[0]
    assert s.op == "delete"
    assert s.new_text == ""
    assert s.word_ids == ["w4", "w5"]


def test_insert_after_uses_anchor_word() -> None:
    result = _suggest(_raw(op="insert_after", start=2, end=2, text="up"))
    s = result.suggestions[0]
    assert s.op == "insert_after"
    assert s.word_ids == ["w2"]


def test_out_of_range_indices_dropped_with_warning() -> None:
    result = _suggest(_raw(start=99, end=99))
    assert result.suggestions == []
    assert len(result.warnings) == 1
    assert "out of range" in result.warnings[0]


def test_replace_with_empty_text_dropped() -> None:
    result = _suggest(_raw(text="  "))
    assert result.suggestions == []
    assert "empty new_text" in result.warnings[0]


def test_below_min_confidence_dropped() -> None:
    result = _suggest(
        _raw(confidence=0.4), settings=AutoCorrectSettings(min_confidence=0.6)
    )
    assert result.suggestions == []
    assert "below threshold" in result.warnings[0]


def test_adlib_removal_dropped_when_disabled() -> None:
    result = _suggest(
        _raw(op="delete", start=4, end=5, text="", category="adlib_removal"),
        settings=AutoCorrectSettings(suggest_adlib_removal=False),
    )
    assert result.suggestions == []
    assert "adlib removal disabled" in result.warnings[0]


def test_insertions_dropped_when_disabled() -> None:
    result = _suggest(
        _raw(op="insert_after", start=2, end=2, text="up"),
        settings=AutoCorrectSettings(allow_insertions=False),
    )
    assert result.suggestions == []
    assert "insertions disabled" in result.warnings[0]


def test_unknown_category_coerced_to_other() -> None:
    result = _suggest(_raw(category="weird"))
    assert result.suggestions[0].category == "other"


def test_confidence_clamped() -> None:
    result = _suggest(_raw(confidence=1.7))
    assert result.suggestions[0].confidence == 1.0


def test_no_reference_lyrics_raises_422() -> None:
    service = AutoCorrectService()
    with pytest.raises(AutoCorrectServiceError) as exc_info:
        service.suggest(
            job_id="job-1",
            segments=SEGMENTS,
            reference_lyrics={},
            artist=None,
            title=None,
            settings=AutoCorrectSettings(),
        )
    assert exc_info.value.status_code == 422


def test_empty_segments_raises_400() -> None:
    service = AutoCorrectService()
    with pytest.raises(AutoCorrectServiceError) as exc_info:
        service.suggest(
            job_id="job-1",
            segments=[],
            reference_lyrics=REFS,
            artist=None,
            title=None,
            settings=AutoCorrectSettings(),
        )
    assert exc_info.value.status_code == 400


def test_missing_segment_ids_raises() -> None:
    service = AutoCorrectService()
    with pytest.raises(AutoCorrectServiceError, match="segments must have ids"):
        service.suggest(
            job_id="job-1",
            segments=[{"words": [{"id": "w0", "text": "hi"}]}],
            reference_lyrics=REFS,
            artist=None,
            title=None,
            settings=AutoCorrectSettings(),
        )


def test_missing_word_ids_raises() -> None:
    service = AutoCorrectService()
    with pytest.raises(AutoCorrectServiceError, match="ids"):
        service.suggest(
            job_id="job-1",
            segments=[{"id": "s", "words": [{"text": "no-id"}]}],
            reference_lyrics=REFS,
            artist=None,
            title=None,
            settings=AutoCorrectSettings(),
        )


def test_malformed_model_response_raises_502() -> None:
    with pytest.raises(AutoCorrectServiceError) as exc_info:
        _suggest({"not_suggestions": []})
    assert exc_info.value.status_code == 502


def test_non_dict_suggestion_entries_dropped() -> None:
    result = _suggest({"suggestions": ["bogus", 42]})
    assert result.suggestions == []
    assert len(result.warnings) == 2
