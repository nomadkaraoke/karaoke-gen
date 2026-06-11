"""Tests for multi-model compare: parallel calls, dedup, consensus, conflicts."""
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
            {"id": "w4", "text": "today"},
        ],
    },
]
REFS = {"genius": {"segments": [{"text": "Sippin' on straight chlorine"}]}}
COMPARE = AutoCorrectSettings(compare_models=True)


def _sugg(start, end, text, op="replace", confidence=0.9):
    return {
        "op": op,
        "start_idx": start,
        "end_idx": end,
        "new_text": text,
        "reason": "r",
        "category": "mishearing",
        "confidence": confidence,
    }


def _run_compare(responses_by_model, compare_models_env="model-a;model-b",
                 settings=COMPARE, single_model="gemini-3.1-pro-preview"):
    """responses_by_model: model name -> raw dict response or Exception."""
    service = AutoCorrectService()

    def fake_call(model, system_prompt, user_prompt, *, job_id):
        resp = responses_by_model[model]
        if isinstance(resp, Exception):
            raise resp
        return resp

    with patch.object(
        service.settings, "auto_correct_compare_models", compare_models_env
    ), patch.object(
        service.settings, "auto_correct_model", single_model
    ), patch.object(service, "_call_model", side_effect=fake_call):
        return service.suggest(
            job_id="job-1",
            segments=SEGMENTS,
            reference_lyrics=REFS,
            artist="A",
            title="T",
            settings=settings,
        )


def test_identical_suggestions_deduped_with_consensus() -> None:
    result = _run_compare({
        "model-a": {"suggestions": [_sugg(3, 3, "chlorine", confidence=0.9)]},
        "model-b": {"suggestions": [_sugg(3, 3, "chlorine", confidence=0.95)]},
    })
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert sorted(s.models) == ["model-a", "model-b"]
    assert s.consensus == 2
    assert s.total_models == 2
    assert s.confidence == 0.95  # max across models
    assert s.conflict_group is None
    assert result.model == "model-a, model-b"


def test_conflicting_suggestions_share_group() -> None:
    result = _run_compare({
        "model-a": {"suggestions": [_sugg(3, 3, "chlorine")]},
        "model-b": {"suggestions": [_sugg(2, 3, "straight chlorine gas")]},
    })
    assert len(result.suggestions) == 2
    groups = {s.conflict_group for s in result.suggestions}
    assert len(groups) == 1 and None not in groups
    assert all(s.consensus == 1 and s.total_models == 2 for s in result.suggestions)


def test_non_overlapping_suggestions_have_no_group() -> None:
    result = _run_compare({
        "model-a": {"suggestions": [_sugg(3, 3, "chlorine")]},
        "model-b": {"suggestions": [_sugg(0, 0, "Sipping")]},
    })
    assert all(s.conflict_group is None for s in result.suggestions)
    # sorted by document position
    assert [s.word_ids[0] for s in result.suggestions] == ["w0", "w3"]


def test_insert_after_does_not_conflict_with_replace_on_anchor() -> None:
    result = _run_compare({
        "model-a": {"suggestions": [_sugg(3, 3, "chlorine")]},
        "model-b": {"suggestions": [_sugg(3, 3, "up", op="insert_after")]},
    })
    assert all(s.conflict_group is None for s in result.suggestions)


def test_one_model_failing_produces_warning_not_error() -> None:
    result = _run_compare({
        "model-a": {"suggestions": [_sugg(3, 3, "chlorine")]},
        "model-b": AutoCorrectServiceError("AI model call failed", status_code=502),
    })
    assert len(result.suggestions) == 1
    assert result.suggestions[0].total_models == 1  # only one model answered
    assert any("model-b failed" in w for w in result.warnings)
    assert result.model == "model-a"


def test_malformed_model_output_warns_instead_of_aborting_compare() -> None:
    result = _run_compare({
        "model-a": {"suggestions": [_sugg(3, 3, "chlorine")]},
        "model-b": {"not_suggestions": []},  # malformed -> _validate raises
    })
    assert len(result.suggestions) == 1
    assert any("model-b failed" in w for w in result.warnings)
    assert result.model == "model-a"


def test_all_models_failing_raises_502() -> None:
    with pytest.raises(AutoCorrectServiceError) as exc_info:
        _run_compare({
            "model-a": AutoCorrectServiceError("boom", status_code=502),
            "model-b": AutoCorrectServiceError("boom", status_code=502),
        })
    assert exc_info.value.status_code == 502


def test_compare_falls_back_to_single_when_unconfigured() -> None:
    result = _run_compare(
        {"gemini-3.1-pro-preview": {"suggestions": [_sugg(3, 3, "chlorine")]}},
        compare_models_env="",
    )
    s = result.suggestions[0]
    assert s.total_models == 1
    assert s.consensus == 1


def test_single_model_mode_unchanged() -> None:
    result = _run_compare(
        {"gemini-3.1-pro-preview": {"suggestions": [_sugg(3, 3, "chlorine")]}},
        settings=AutoCorrectSettings(),  # compare off
    )
    assert len(result.suggestions) == 1
    assert result.suggestions[0].models == ["gemini-3.1-pro-preview"]


def test_three_way_conflict_chain_unifies_groups() -> None:
    # a: w2-w3, b: w3-w4, c: w4 — chained overlaps must land in ONE group
    result = _run_compare({
        "model-a": {"suggestions": [_sugg(2, 3, "x y")]},
        "model-b": {"suggestions": [_sugg(3, 4, "y z"), _sugg(4, 4, "z")]},
    })
    groups = {s.conflict_group for s in result.suggestions}
    assert len(groups) == 1 and None not in groups
