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
async def test_existing_instrumental_completes_with_custom_selection() -> None:
    # Tenant/bulk jobs with a user-supplied instrumental have NO instrumental
    # decision — confident lyrics alone auto-complete with selection="custom",
    # even with NO backing analysis at all (the backing verdict is moot).
    result, jm, _, _ = await _run(
        _job(backing=None, existing_instrumental="jobs/j1/custom.flac")
    )
    assert result["outcome"] == "auto_completed"
    jm.update_state_data.assert_any_call("j1", "instrumental_selection", "custom")
    payload = jm.update_processing_metadata.call_args.args[2]
    assert payload["enforcement_basis"] == "lyrics_only_custom_instrumental"
    assert payload["custom_instrumental"] is True


@pytest.mark.asyncio
async def test_custom_instrumental_stem_also_selects_custom() -> None:
    job = _job(backing=None)
    job.file_urls["stems"]["custom_instrumental"] = "jobs/j1/stems/custom.flac"
    # No separated instrumental required for custom jobs.
    del job.file_urls["stems"]["instrumental_clean"]
    result, jm, _, _ = await _run(job)
    assert result["outcome"] == "auto_completed"
    jm.update_state_data.assert_any_call("j1", "instrumental_selection", "custom")


@pytest.mark.asyncio
async def test_existing_instrumental_still_requires_confident_lyrics() -> None:
    corrections = _confident_corrections()
    corrections["reference_lyrics"] = {}  # no references -> lyrics review
    result, jm, _, _ = await _run(
        _job(backing=None, existing_instrumental="jobs/j1/custom.flac"),
        corrections=corrections,
    )
    assert result["outcome"] == "review"
    jm.transition_to_state.assert_not_called()


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


def _audible_backing() -> Dict[str, Any]:
    # Pink backing present, no 3-stem comparison -> scorer verdict REVIEW.
    return {
        "has_audible_content": True,
        "audible_percentage": 40.0,
        "audible_segments": [{"avg_amplitude_db": -10.0}],
        "recommended_selection": "with_backing",
    }


@pytest.mark.asyncio
async def test_backing_preference_clean_forces_clean_completion() -> None:
    # Backing is subjective (would normally gate), but the user chose "clean"
    # up-front -> auto-complete on lyrics alone with a clean instrumental.
    job = _job(backing=_audible_backing())
    job.backing_preference = "clean"
    result, jm, storage, ws = await _run(job)
    assert result["outcome"] == "auto_completed"
    jm.update_state_data.assert_any_call("j1", "instrumental_selection", "clean")
    ws.trigger_render_video_worker.assert_awaited_once_with("j1")
    payload = jm.update_processing_metadata.call_args.args[2]
    assert payload["enforcement_basis"] == "lyrics_auto_backing_pref:clean"


@pytest.mark.asyncio
async def test_backing_preference_review_blocks_even_confident_clean() -> None:
    # User wants to always pick the instrumental -> never auto-skip the backing half.
    job = _job(backing=_clean_backing())
    job.backing_preference = "review"
    result, jm, _, ws = await _run(job)
    assert result["outcome"] == "review"
    jm.transition_to_state.assert_not_called()
    ws.trigger_render_video_worker.assert_not_awaited()


# ---- timing-plausibility gate wiring ----------------------------------------

def _timing_signals(fired):
    from backend.services.auto_approval.timing_check import TimingSignals

    return TimingSignals(
        n_words=100,
        pct_start_inactive=40.0 if fired else 0.0,
        n_suspect_bad=30 if fired else 0,
        max_unclaimed_run_s=5.0 if fired else 0.5,
        fired=["start-silence", "suspect-mistimed"] if fired else [],
    )


def _job_with_lead_stem(**kwargs):
    kwargs.setdefault("backing", _clean_backing())
    job = _job(**kwargs)
    job.file_urls["stems"]["lead_vocals"] = "jobs/j1/stems/lead_vocals.flac"
    return job


@pytest.mark.asyncio
async def test_fired_timing_gate_demotes_to_review() -> None:
    with patch(
        "backend.services.auto_approval.executor._compute_timing_signals",
        return_value=_timing_signals(fired=True),
    ):
        result, job_manager, _, worker = await _run(_job_with_lead_stem())
    assert result["outcome"] == "review"
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["timing"]["status"] == "checked"
    assert payload["timing"]["fired"] == ["start-silence", "suspect-mistimed"]
    assert payload["lyrics"]["tier"] == "timing-gate"
    worker.trigger_render_video_worker.assert_not_called()


@pytest.mark.asyncio
async def test_clean_timing_signals_allow_auto_completion() -> None:
    with patch(
        "backend.services.auto_approval.executor._compute_timing_signals",
        return_value=_timing_signals(fired=False),
    ):
        result, job_manager, _, _ = await _run(_job_with_lead_stem())
    assert result["outcome"] == "auto_completed"
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["timing"]["status"] == "checked"
    assert payload["timing"]["fired"] == []


@pytest.mark.asyncio
async def test_incomplete_audio_records_timing_pending() -> None:
    result, job_manager, _, _ = await _run(_job_with_lead_stem(audio_complete=False))
    assert result["outcome"] == "review"  # blocked by audio_incomplete
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["timing"]["status"] == "pending_audio"


@pytest.mark.asyncio
async def test_timing_gate_flag_disabled_fails_open() -> None:
    settings = SimpleNamespace(
        auto_approval_enforce_enabled=True,
        auto_approval_timing_gate_enabled=False,
    )
    result, job_manager, _, _ = await _run(_job_with_lead_stem(), settings=settings)
    assert result["outcome"] == "auto_completed"
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["timing"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_missing_lead_stem_fails_open() -> None:
    with patch(
        "backend.services.auto_approval.executor._compute_timing_signals",
        return_value=None,
    ):
        result, job_manager, _, _ = await _run(_job_with_lead_stem())
    assert result["outcome"] == "auto_completed"
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["timing"]["status"] == "no_lead_stem"


@pytest.mark.asyncio
async def test_timing_analysis_error_fails_open() -> None:
    from backend.services.auto_approval.timing_check import TimingSignals

    with patch(
        "backend.services.auto_approval.executor._compute_timing_signals",
        return_value=TimingSignals(error="boom"),
    ):
        result, job_manager, _, _ = await _run(_job_with_lead_stem())
    assert result["outcome"] == "auto_completed"
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["timing"]["status"] == "error"
    assert payload["timing"]["error"] == "boom"


@pytest.mark.asyncio
async def test_gated_lyrics_skip_timing_compute() -> None:
    # Lyrics already gated (no reference sources) -> no audio work needed.
    data = _confident_corrections()
    data["reference_lyrics"] = {}
    with patch(
        "backend.services.auto_approval.executor._compute_timing_signals"
    ) as compute:
        result, job_manager, _, _ = await _run(_job_with_lead_stem(), corrections=data)
    compute.assert_not_called()
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["timing"]["status"] == "not_needed"


# ---------------------------------------------------------------- vocal gaps (shadow)

def _vocal_gaps_result(suspect_count=1, error=None, evidenced=False):
    from backend.services.auto_approval.vocal_gaps import VocalGap, VocalGapsResult

    gaps = [VocalGap(start=20.66, end=32.4, duration=11.74, active_fraction=0.96,
                     longest_run_s=11.76, suspect=True, evidenced=evidenced,
                     reference_lines={"genius": ["line one", "line two"]})] if suspect_count else []
    return VocalGapsResult(gaps=gaps, suspect_count=suspect_count,
                           evidenced_count=1 if (evidenced and suspect_count) else 0,
                           max_suspect_run_s=11.76 if suspect_count else 0.0, error=error)


def _stored_vocal_gaps(job_manager):
    calls = [c for c in job_manager.update_state_data.call_args_list if c[0][1] == "vocal_gaps"]
    return calls[0][0][2] if calls else None


@pytest.mark.asyncio
async def test_vocal_gaps_analyzed_and_stored_without_changing_outcome() -> None:
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=_vocal_gaps_result()) as analyze, \
         patch("backend.services.auto_approval.executor._compute_timing_signals",
               return_value=_timing_signals(fired=False)):
        result, job_manager, _, _ = await _run(_job_with_lead_stem())
    analyze.assert_called_once()
    # Shadow only: a suspect gap must not demote a confident job (yet)
    assert result["outcome"] == "auto_completed"
    stored = _stored_vocal_gaps(job_manager)
    assert stored["suspect_count"] == 1
    assert stored["gaps"][0]["reference_lines"] == {"genius": ["line one", "line two"]}
    assert stored["source"] == "auto_approval" and "analyzed_at" in stored
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["vocal_gaps"] == {"status": "checked", "suspect_count": 1, "evidenced_count": 0,
                                     "max_suspect_run_s": 11.76}


@pytest.mark.asyncio
async def test_evidenced_missing_lyrics_block_auto_approval() -> None:
    """Singing with no words where the reference lyrics have lines = dropped lyrics:
    a confident job must go to review with an explicit reason."""
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=_vocal_gaps_result(evidenced=True)), \
         patch("backend.services.auto_approval.executor._compute_timing_signals") as timing:
        result, job_manager, _, worker = await _run(_job_with_lead_stem())
    assert result["outcome"] == "review"
    worker.trigger_render_video_worker.assert_not_called()
    timing.assert_not_called()  # lyrics no longer AUTO, so the timing check is skipped
    payload = job_manager.update_processing_metadata.call_args[0][2]
    assert payload["lyrics"]["tier"] == "missing-lyrics-gate"
    assert any("possible missing lyrics" in r and "0:20-0:32" in r and "2 line(s)" in r
               for r in payload["lyrics"]["reasons"])
    assert payload["vocal_gaps"]["evidenced_count"] == 1


@pytest.mark.asyncio
async def test_cached_evidenced_gaps_still_gate() -> None:
    job = _job_with_lead_stem()
    gaps = _vocal_gaps_result(evidenced=True).to_dict()
    job.state_data["vocal_gaps"] = {**gaps, "input_key": _input_key_for(job)}
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps") as analyze:
        result, job_manager, _, _ = await _run(job)
    analyze.assert_not_called()
    assert result["outcome"] == "review"
    assert job_manager.update_processing_metadata.call_args[0][2]["lyrics"]["tier"] == "missing-lyrics-gate"


@pytest.mark.asyncio
async def test_vocal_gaps_analyzed_for_review_bound_jobs_too() -> None:
    """Unlike the timing gate, gaps are analyzed whatever the lyrics verdict."""
    corrections = _confident_corrections()
    corrections["gap_sequences"] = [{"id": "g0", "transcribed_word_ids": ["w1", "w2", "w3"]}]
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=_vocal_gaps_result()) as analyze:
        result, job_manager, _, _ = await _run(_job_with_lead_stem(), corrections=corrections)
    assert result["outcome"] == "review"
    analyze.assert_called_once()
    assert _stored_vocal_gaps(job_manager)["suspect_count"] == 1


def _input_key_for(job, corrections=None):
    from backend.services.auto_approval.executor import vocal_gaps_input_key

    corrections = corrections or _confident_corrections()
    return vocal_gaps_input_key(corrections["corrected_segments"], job.file_urls["stems"])


@pytest.mark.asyncio
async def test_vocal_gaps_not_recomputed_when_cached_for_same_inputs() -> None:
    job = _job_with_lead_stem()
    job.state_data["vocal_gaps"] = {"suspect_count": 0, "max_suspect_run_s": 0.0,
                                    "input_key": _input_key_for(job)}
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps") as analyze, \
         patch("backend.services.auto_approval.executor._compute_timing_signals",
               return_value=_timing_signals(fired=False)):
        _, job_manager, _, _ = await _run(job)
    analyze.assert_not_called()
    assert _stored_vocal_gaps(job_manager) is None
    assert job_manager.update_processing_metadata.call_args[0][2]["vocal_gaps"]["status"] == "cached"


@pytest.mark.asyncio
async def test_stale_vocal_gaps_recomputed_when_lyrics_changed() -> None:
    """A stored result from different lyrics/stem (reset, edit, re-transcription) is
    not reused — it would corrupt the calibration data."""
    job = _job_with_lead_stem()
    job.state_data["vocal_gaps"] = {"suspect_count": 5, "max_suspect_run_s": 9.0, "input_key": "stale"}
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=_vocal_gaps_result(suspect_count=0)) as analyze, \
         patch("backend.services.auto_approval.executor._compute_timing_signals",
               return_value=_timing_signals(fired=False)):
        _, job_manager, _, _ = await _run(job)
    analyze.assert_called_once()
    stored = _stored_vocal_gaps(job_manager)
    assert stored["suspect_count"] == 0 and stored["input_key"] == _input_key_for(job)


def test_input_key_changes_with_word_timing_and_stem() -> None:
    from backend.services.auto_approval.executor import vocal_gaps_input_key

    segs = _confident_corrections()["corrected_segments"]
    stems = {"lead_vocals": "a.flac"}
    key = vocal_gaps_input_key(segs, stems)
    moved = [{**segs[0], "words": [{**segs[0]["words"][0], "start_time": 9.0}] + segs[0]["words"][1:]}]
    assert vocal_gaps_input_key(moved, stems) != key
    assert vocal_gaps_input_key(segs, {"lead_vocals": "b.flac"}) != key
    assert vocal_gaps_input_key(segs, stems) == key


@pytest.mark.asyncio
async def test_vocal_gaps_wait_for_audio() -> None:
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps") as analyze:
        _, job_manager, _, _ = await _run(_job_with_lead_stem(audio_complete=False))
    analyze.assert_not_called()
    assert job_manager.update_processing_metadata.call_args[0][2]["vocal_gaps"] == {"status": "pending_audio"}


@pytest.mark.asyncio
async def test_vocal_gaps_without_lead_stem() -> None:
    with patch("backend.services.auto_approval.executor._compute_timing_signals", return_value=None):
        _, job_manager, _, _ = await _run(_job(backing=_clean_backing()))
    assert job_manager.update_processing_metadata.call_args[0][2]["vocal_gaps"] == {"status": "no_lead_stem"}


@pytest.mark.asyncio
async def test_vocal_gaps_error_is_nonfatal_and_not_stored() -> None:
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=_vocal_gaps_result(error="decode failed")), \
         patch("backend.services.auto_approval.executor._compute_timing_signals",
               return_value=_timing_signals(fired=False)):
        result, job_manager, _, _ = await _run(_job_with_lead_stem())
    assert result["outcome"] == "auto_completed"
    assert _stored_vocal_gaps(job_manager) is None
    assert job_manager.update_processing_metadata.call_args[0][2]["vocal_gaps"]["status"] == "error"


@pytest.mark.asyncio
async def test_vocal_gaps_exception_is_nonfatal() -> None:
    with patch("backend.services.auto_approval.executor.analyze_vocal_gaps", side_effect=RuntimeError("boom")), \
         patch("backend.services.auto_approval.executor._compute_timing_signals",
               return_value=_timing_signals(fired=False)):
        result, job_manager, _, _ = await _run(_job_with_lead_stem())
    assert result["outcome"] == "auto_completed"
    assert job_manager.update_processing_metadata.call_args[0][2]["vocal_gaps"]["status"] == "error"


# ---------------------------------------------------------------- backfill

def _backfill(job, files: Dict[str, Any], analyze_result):
    from backend.services.auto_approval.executor import analyze_job_vocal_gaps

    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    storage = MagicMock()
    storage.file_exists.side_effect = lambda p: p in files
    storage.download_json.side_effect = lambda p: files[p]
    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService", return_value=storage), \
         patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=analyze_result) as analyze:
        return analyze_job_vocal_gaps, job_manager, analyze


def test_backfill_uses_reviewed_lyrics_and_stores() -> None:
    original = _confident_corrections()
    updated = {"corrected_segments": [{"id": "s9", "words": []}]}
    files = {"jobs/j1/lyrics/corrections.json": original,
             "jobs/j1/lyrics/corrections_updated.json": updated}
    fn, job_manager, analyze = _backfill(_job_with_lead_stem(), files, _vocal_gaps_result())
    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService") as storage_cls, \
         patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=_vocal_gaps_result()) as analyze:
        storage = storage_cls.return_value
        storage.file_exists.side_effect = lambda p: p in files
        storage.download_json.side_effect = lambda p: files[p]
        out = fn("j1", store=True)
    assert out["status"] == "checked" and out["stored"] is True
    corrections_arg, segments_arg = analyze.call_args[0][1], analyze.call_args[0][2]
    assert corrections_arg is original          # reference + anchors from the original
    assert segments_arg == updated["corrected_segments"]  # words as reviewed/rendered
    assert out["vocal_gaps"]["lyrics_source"] == "corrections_updated"
    assert _stored_vocal_gaps(job_manager)["source"] == "backfill"


def test_backfill_dry_run_does_not_store() -> None:
    files = {"jobs/j1/lyrics/corrections.json": _confident_corrections()}
    fn, job_manager, _ = _backfill(_job_with_lead_stem(), files, _vocal_gaps_result())
    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService") as storage_cls, \
         patch("backend.services.auto_approval.executor.analyze_vocal_gaps",
               return_value=_vocal_gaps_result()):
        storage_cls.return_value.file_exists.side_effect = lambda p: p in files
        storage_cls.return_value.download_json.side_effect = lambda p: files[p]
        out = fn("j1", store=False)
    assert out["status"] == "checked" and out["vocal_gaps"]["lyrics_source"] == "corrections"
    assert _stored_vocal_gaps(job_manager) is None


@pytest.mark.parametrize("files,job,expected", [
    ({}, "job", "no_corrections"),
    ({"jobs/j1/lyrics/corrections.json": {}}, None, "no_job"),
])
def test_backfill_missing_inputs(files, job, expected) -> None:
    from backend.services.auto_approval.executor import analyze_job_vocal_gaps

    job_manager = MagicMock()
    job_manager.get_job.return_value = _job_with_lead_stem() if job else None
    with patch("backend.services.job_manager.JobManager", return_value=job_manager), \
         patch("backend.services.storage_service.StorageService") as storage_cls:
        storage_cls.return_value.file_exists.side_effect = lambda p: p in files
        assert analyze_job_vocal_gaps("j1")["status"] == expected
