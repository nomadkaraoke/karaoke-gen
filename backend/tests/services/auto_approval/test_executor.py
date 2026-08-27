"""Tests for the auto-approval executor.

The executor must (a) record a verdict on every scored job, (b) complete the
review end-to-end ONLY when fully confident and eligible, and (c) fail safe —
any anomaly or error leaves the job on the normal human-review path.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import JobStatus
from backend.services.auto_approval.executor import maybe_auto_complete_review


def _confident_corrections() -> Dict[str, Any]:
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
        "corrections": [],
        "corrections_made": 0,
        "reference_lyrics": {"spotify": {"metadata": {"is_synced": True}, "segments": []}},
        "anchor_sequences": [{"id": "a0", "transcribed_word_ids": [w["id"] for w in words]}],
        "gap_sequences": [],
        "metadata": {"total_words": 20, "agentic_routing": "disabled", "correction_type": "none"},
    }


def _clean_backing() -> Dict[str, Any]:
    return {
        "has_audible_content": False,
        "audible_percentage": 0.0,
        "audible_segments": [],
        "recommended_selection": "clean",
    }


def _job(
    status: JobStatus = JobStatus.GENERATING_SCREENS,
    review_mode: str = "auto",
    made_for_you: bool = False,
    audio_complete: bool = True,
    backing: Optional[Dict[str, Any]] = None,
    existing_instrumental: Optional[str] = None,
    state_extra: Optional[Dict[str, Any]] = None,
) -> SimpleNamespace:
    state: Dict[str, Any] = {"audio_complete": audio_complete}
    if backing is not None:
        state["backing_vocals_analysis"] = backing
    state.update(state_extra or {})
    return SimpleNamespace(
        status=status,
        state_data=state,
        review_mode=review_mode,
        made_for_you=made_for_you,
        existing_instrumental_gcs_path=existing_instrumental,
        file_urls={"stems": {"instrumental_clean": "jobs/j1/stems/clean.flac"}},
    )


def _settings(enforce: bool = True) -> SimpleNamespace:
    return SimpleNamespace(auto_approval_enforce_enabled=enforce)


async def _run(job, *, corrections=None, suggestions=None, settings=None):
    """Drive maybe_auto_complete_review with everything mocked; returns
    (result, job_manager_mock, storage_mock, worker_service_mock)."""
    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    storage = MagicMock()
    corrections = corrections if corrections is not None else _confident_corrections()
    storage.file_exists.return_value = True
    cache = {"suggestions": suggestions if suggestions is not None else []}
    storage.list_files.return_value = ["jobs/j1/lyrics/auto_correct_cache/x.json"]

    def _download(path: str):
        if "auto_correct_cache" in path:
            return cache
        return corrections

    storage.download_json.side_effect = _download

    worker_service = MagicMock()
    worker_service.trigger_render_video_worker = AsyncMock(return_value=True)

    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService", return_value=storage), \
         patch("backend.config.get_settings", return_value=settings or _settings()), \
         patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
        result = await maybe_auto_complete_review("j1", trigger="screens_worker")
    return result, job_manager, storage, worker_service


@pytest.mark.asyncio
async def test_confident_job_is_auto_completed() -> None:
    result, jm, storage, ws = await _run(_job(backing=_clean_backing()))
    assert result["outcome"] == "auto_completed"

    # corrections_updated.json saved with the auto-approval marker
    upload_path, uploaded = storage.upload_json.call_args.args
    assert upload_path == "jobs/j1/lyrics/corrections_updated.json"
    assert uploaded["metadata"]["auto_approval"]["auto_approved"] is True

    # instrumental selection stored, progress keys cleared, transitioned, render triggered
    jm.update_state_data.assert_any_call("j1", "instrumental_selection", "clean")
    jm.delete_state_data_keys.assert_called_once()
    transition_kwargs = jm.transition_to_state.call_args.kwargs
    assert transition_kwargs["new_status"] == JobStatus.REVIEW_COMPLETE
    ws.trigger_render_video_worker.assert_awaited_once_with("j1")

    # verdict recorded with outcome
    section, payload = jm.update_processing_metadata.call_args.args[1:3]
    assert section == "auto_approval"
    assert payload["outcome"] == "auto_completed"
    assert payload["mode"] == "enforce"


@pytest.mark.asyncio
async def test_ai_suggestions_are_applied_in_saved_corrections() -> None:
    suggestions = [{
        "id": "sugA", "op": "replace", "word_ids": ["w3"], "segment_ids": ["s0"],
        "original_text": "word3", "new_text": "fixed", "confidence": 0.9,
        "consensus": 2, "total_models": 2, "conflict_group": None,
    }]
    result, jm, storage, _ = await _run(_job(backing=_clean_backing()), suggestions=suggestions)
    assert result["outcome"] == "auto_completed"
    uploaded = storage.upload_json.call_args.args[1]
    texts = [w["text"] for w in uploaded["corrected_segments"][0]["words"]]
    assert "fixed" in texts and "word3" not in texts
    assert uploaded["metadata"]["auto_approval"]["applied_suggestion_ids"] == ["sugA"]


@pytest.mark.asyncio
async def test_unconfident_job_goes_to_review_with_verdict_recorded() -> None:
    audible = {"has_audible_content": True, "audible_percentage": 40.0,
               "audible_segments": [{"avg_amplitude_db": -10.0}],
               "recommended_selection": "with_backing"}
    result, jm, storage, ws = await _run(_job(backing=audible))
    assert result["outcome"] == "review"
    jm.transition_to_state.assert_not_called()
    storage.upload_json.assert_not_called()
    ws.trigger_render_video_worker.assert_not_awaited()
    payload = jm.update_processing_metadata.call_args.args[2]
    assert payload["outcome"] == "review"
    assert payload["mode"] == "shadow"


@pytest.mark.asyncio
async def test_always_review_mode_blocks_enforcement() -> None:
    result, jm, _, _ = await _run(_job(review_mode="always_review", backing=_clean_backing()))
    assert result["outcome"] == "review"
    assert "review_mode:always_review" in result["blockers"]
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_made_for_you_blocks_enforcement() -> None:
    result, jm, _, _ = await _run(_job(made_for_you=True, backing=_clean_backing()))
    assert result["outcome"] == "review"
    assert "made_for_you" in result["blockers"]
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_flag_disabled_blocks_enforcement() -> None:
    result, jm, _, _ = await _run(
        _job(backing=_clean_backing()), settings=_settings(enforce=False)
    )
    assert result["outcome"] == "review"
    assert "flag_disabled" in result["blockers"]
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_incomplete_audio_blocks_enforcement() -> None:
    result, jm, _, _ = await _run(_job(audio_complete=False, backing=None))
    assert result["outcome"] == "review"
    assert "audio_incomplete" in result["blockers"]
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_existing_instrumental_blocks_enforcement() -> None:
    result, jm, _, _ = await _run(
        _job(backing=_clean_backing(), existing_instrumental="jobs/j1/custom.flac")
    )
    assert result["outcome"] == "review"
    assert "custom_instrumental" in result["blockers"]


@pytest.mark.asyncio
async def test_wrong_status_is_noop() -> None:
    result, jm, _, _ = await _run(_job(status=JobStatus.IN_REVIEW, backing=_clean_backing()))
    assert result["outcome"] == "wrong_status"
    jm.update_processing_metadata.assert_not_called()
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_existing_selection_is_noop() -> None:
    result, jm, _, _ = await _run(
        _job(backing=_clean_backing(), state_extra={"instrumental_selection": "clean"})
    )
    assert result["outcome"] == "already_selected"
    jm.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_stale_suggestion_aborts_to_review() -> None:
    suggestions = [{
        "id": "sugA", "op": "replace", "word_ids": ["not-a-word"], "segment_ids": ["s0"],
        "original_text": "x", "new_text": "y", "confidence": 0.9,
        "consensus": 2, "total_models": 2, "conflict_group": None,
    }]
    result, jm, storage, ws = await _run(_job(backing=_clean_backing()), suggestions=suggestions)
    assert result["outcome"] == "aborted"
    assert "stale_suggestions" in result["reason"]
    jm.transition_to_state.assert_not_called()
    storage.upload_json.assert_not_called()
    ws.trigger_render_video_worker.assert_not_awaited()
    payload = jm.update_processing_metadata.call_args.args[2]
    assert payload["outcome"] == "aborted"


@pytest.mark.asyncio
async def test_duplicate_word_artifact_aborts_to_review() -> None:
    # P1 self-conflict: two overlapping suggestions both add the same token.
    suggestions = [
        {"id": "a", "op": "insert_after", "word_ids": ["w3"], "segment_ids": ["s0"],
         "original_text": "", "new_text": "extra", "confidence": 0.75,
         "consensus": 1, "total_models": 2, "conflict_group": None},
        {"id": "b", "op": "replace", "word_ids": ["w4"], "segment_ids": ["s0"],
         "original_text": "word4", "new_text": "extra word4", "confidence": 0.95,
         "consensus": 1, "total_models": 2, "conflict_group": None},
    ]
    result, jm, storage, _ = await _run(_job(backing=_clean_backing()), suggestions=suggestions)
    assert result["outcome"] == "aborted"
    assert "duplicate_words" in result["reason"]
    jm.transition_to_state.assert_not_called()
    storage.upload_json.assert_not_called()


@pytest.mark.asyncio
async def test_state_change_during_enforce_aborts() -> None:
    # A human opening the editor between the snapshot read and the enforce
    # writes (AWAITING_REVIEW -> IN_REVIEW) must abort — IN_REVIEW ->
    # REVIEW_COMPLETE is a legal transition, so validation alone won't stop it.
    eligible = _job(backing=_clean_backing())
    editing = _job(status=JobStatus.IN_REVIEW, backing=_clean_backing())
    job_manager = MagicMock()
    job_manager.get_job.side_effect = [eligible, editing]
    storage = MagicMock()
    storage.file_exists.return_value = True
    storage.list_files.return_value = []
    storage.download_json.return_value = _confident_corrections()
    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService", return_value=storage), \
         patch("backend.config.get_settings", return_value=_settings()):
        result = await maybe_auto_complete_review("j1", trigger="screens_worker")
    assert result["outcome"] == "aborted"
    assert result["reason"] == "state_changed_during_enforce"
    job_manager.transition_to_state.assert_not_called()
    storage.upload_json.assert_not_called()


@pytest.mark.asyncio
async def test_missing_corrections_is_noop() -> None:
    job_manager = MagicMock()
    job_manager.get_job.return_value = _job(backing=_clean_backing())
    storage = MagicMock()
    storage.file_exists.return_value = False
    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService", return_value=storage), \
         patch("backend.config.get_settings", return_value=_settings()):
        result = await maybe_auto_complete_review("j1", trigger="screens_worker")
    assert result["outcome"] == "no_corrections"
    job_manager.transition_to_state.assert_not_called()


@pytest.mark.asyncio
async def test_any_exception_is_nonfatal() -> None:
    job_manager = MagicMock()
    job_manager.get_job.side_effect = Exception("firestore down")
    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService", MagicMock()), \
         patch("backend.config.get_settings", return_value=_settings()):
        result = await maybe_auto_complete_review("j1", trigger="screens_worker")
    assert result["outcome"] == "error"
