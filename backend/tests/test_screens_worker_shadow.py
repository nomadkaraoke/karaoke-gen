"""Tests for the SHADOW-mode auto-approval scoring in screens_worker.

The shadow recorder must (a) write the verdict into processing_metadata and
(b) never affect the job's path to review, whatever goes wrong.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.workers.screens_worker import _record_auto_approval_shadow


def _corrections_fixture() -> dict:
    words = [
        {"id": f"w{i}", "text": f"word{i}", "start_time": i * 0.5, "end_time": i * 0.5 + 0.4}
        for i in range(20)
    ]
    return {
        "corrected_segments": [{
            "id": "s0",
            "text": " ".join(w["text"] for w in words),
            "words": words,
            "start_time": 0.0,
            "end_time": 10.0,
        }],
        "corrections_made": 0,
        "reference_lyrics": {"spotify": {"metadata": {"is_synced": True}}},
        "anchor_sequences": [{"id": "a0", "transcribed_word_ids": [w["id"] for w in words]}],
        "gap_sequences": [],
        "metadata": {"total_words": 20, "agentic_routing": "disabled", "correction_type": "none"},
    }


def _job(state_data: dict) -> SimpleNamespace:
    return SimpleNamespace(state_data=state_data)


@pytest.mark.asyncio
async def test_shadow_verdict_recorded_in_processing_metadata() -> None:
    job_manager = MagicMock()
    job_manager.get_job.return_value = _job({
        "audio_complete": True,
        "backing_vocals_analysis": {
            "has_audible_content": False,
            "audible_percentage": 0.0,
            "audible_segments": [],
            "recommended_selection": "clean",
        },
    })
    storage = MagicMock()
    storage.download_json.return_value = _corrections_fixture()
    storage.list_files.return_value = []

    await _record_auto_approval_shadow("job1", job_manager, storage, MagicMock())

    storage.download_json.assert_called_once_with("jobs/job1/lyrics/corrections.json")
    job_manager.update_processing_metadata.assert_called_once()
    section, payload = job_manager.update_processing_metadata.call_args.args[1:3]
    assert section == "auto_approval_shadow"
    assert payload["shadow"] is True
    assert payload["overall_auto"] is True
    assert payload["lyrics"]["verdict"] == "auto"
    assert payload["backing"]["verdict"] == "clean"
    assert payload["audio_complete_at_scoring"] is True
    assert payload["backing_analysis_available"] is True
    assert payload["scored_at"]


@pytest.mark.asyncio
async def test_shadow_notes_missing_backing_analysis() -> None:
    # Backing analysis lands later (audio_worker); the shadow record must say so.
    job_manager = MagicMock()
    job_manager.get_job.return_value = _job({"audio_complete": False})
    storage = MagicMock()
    storage.download_json.return_value = _corrections_fixture()
    storage.list_files.return_value = []

    await _record_auto_approval_shadow("job1", job_manager, storage, MagicMock())

    payload = job_manager.update_processing_metadata.call_args.args[2]
    assert payload["audio_complete_at_scoring"] is False
    assert payload["backing_analysis_available"] is False
    assert payload["overall_auto"] is False  # backing unknown -> never overall auto
    assert payload["backing"]["verdict"] == "review"


@pytest.mark.asyncio
async def test_shadow_scores_against_cached_ai_suggestions() -> None:
    # The proactive auto-correct cache exists -> gap coverage is computed from it.
    job_manager = MagicMock()
    job_manager.get_job.return_value = _job({})
    storage = MagicMock()

    corrections = _corrections_fixture()
    cache_payload = {
        "suggestions": [
            {"id": "sug1", "op": "replace", "word_ids": ["w3"], "segment_ids": ["s0"],
             "original_text": "word3", "new_text": "fixed", "confidence": 0.9,
             "consensus": 2, "total_models": 2},
        ],
        "model": "multi",
    }
    storage.list_files.return_value = ["jobs/job1/lyrics/auto_correct_cache/abc123.json"]
    storage.download_json.side_effect = lambda path: (
        cache_payload if "auto_correct_cache" in path else corrections
    )

    await _record_auto_approval_shadow("job1", job_manager, storage, MagicMock())

    payload = job_manager.update_processing_metadata.call_args.args[2]
    signals = payload["lyrics"]["signals"]
    assert signals["ai_suggestions_available"] is True
    assert signals["ai_suggestion_count"] == 1


@pytest.mark.asyncio
async def test_shadow_skips_when_corrections_missing() -> None:
    # Audio-only jobs have no corrections.json — skip quietly, no download attempt.
    job_manager = MagicMock()
    storage = MagicMock()
    storage.file_exists.return_value = False

    await _record_auto_approval_shadow("job1", job_manager, storage, MagicMock())

    storage.download_json.assert_not_called()
    job_manager.update_processing_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_skips_when_corrections_unreadable() -> None:
    # GCS hiccups must not blow up the worker either.
    job_manager = MagicMock()
    storage = MagicMock()
    storage.file_exists.return_value = True
    storage.download_json.side_effect = Exception("503 backend error")

    await _record_auto_approval_shadow("job1", job_manager, storage, MagicMock())

    job_manager.update_processing_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_is_nonfatal_when_metadata_write_fails() -> None:
    job_manager = MagicMock()
    job_manager.get_job.return_value = _job({})
    job_manager.update_processing_metadata.side_effect = Exception("firestore down")
    storage = MagicMock()
    storage.download_json.return_value = _corrections_fixture()
    storage.list_files.return_value = []

    # Must not raise.
    await _record_auto_approval_shadow("job1", job_manager, storage, MagicMock())
