"""Tests for proactive auto-correct: the service-side worker + the lyrics-worker trigger.

The work runs on the API service (process_proactive_auto_correct); the lyrics
worker only fires an HTTP trigger. Both must be best-effort: gated by a flag,
never raise, never block, and use the multi-model default settings that line up
with the review UI's request.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.workers.auto_correct_worker import process_proactive_auto_correct
from backend.workers.lyrics_worker import _trigger_proactive_auto_correct


CORRECTIONS = {
    "corrected_segments": [
        {"id": "seg-1", "words": [{"id": "w0", "text": "glory"}]},
    ],
    "reference_lyrics": {"genius": {"segments": [{"text": "chlorine"}]}},
}


def _settings(enabled: bool):
    return SimpleNamespace(auto_correct_proactive_enabled=enabled)


def _patch(enabled=True, corrections=CORRECTIONS, exists=True, service=None, job=None):
    """Patch the dependencies process_proactive_auto_correct imports lazily."""
    storage = MagicMock()
    storage.file_exists.return_value = exists
    storage.download_json.return_value = corrections
    jm = MagicMock()
    jm.return_value.get_job.return_value = job or SimpleNamespace(artist="A", title="T")
    return (
        patch("backend.config.get_settings", return_value=_settings(enabled)),
        patch("backend.services.storage_service.StorageService", return_value=storage),
        patch("backend.services.job_manager.JobManager", jm),
        patch("backend.services.auto_correct.get_auto_correct_service",
              return_value=service or MagicMock()),
        storage,
    )


# ---- service-side worker ----

@pytest.mark.asyncio
async def test_disabled_flag_short_circuits() -> None:
    p_set, p_st, p_jm, p_svc, storage = _patch(enabled=False)
    with p_set, p_st, p_jm, p_svc:
        result = await process_proactive_auto_correct("job-1")
    assert result == {"status": "disabled"}
    storage.file_exists.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_no_references() -> None:
    corr = {"corrected_segments": [{"id": "s", "words": []}], "reference_lyrics": {}}
    p_set, p_st, p_jm, p_svc, storage = _patch(corrections=corr)
    svc = MagicMock()
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        result = await process_proactive_auto_correct("job-1")
    assert result["status"] == "skipped"
    svc.suggest.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_no_corrections_file() -> None:
    p_set, p_st, p_jm, p_svc, storage = _patch(exists=False)
    with p_set, p_st, p_jm, p_svc:
        result = await process_proactive_auto_correct("job-1")
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_happy_path_runs_multi_model_and_caches() -> None:
    svc = MagicMock()
    svc.suggest.return_value = SimpleNamespace(
        suggestions=[1, 2], model="claude-fable-5, gemini-3.1-pro-preview",
        elapsed_seconds=12.3,
    )
    p_set, p_st, p_jm, _p_svc, storage = _patch(service=svc)
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        result = await process_proactive_auto_correct("job-1")
    assert result == {"status": "generated", "suggestions": 2}
    kwargs = svc.suggest.call_args.kwargs
    assert kwargs["job_id"] == "job-1"
    assert kwargs["settings"].compare_models is True
    assert kwargs["artist"] == "A"
    assert kwargs["segments"] == CORRECTIONS["corrected_segments"]


@pytest.mark.asyncio
async def test_swallows_service_errors() -> None:
    svc = MagicMock()
    svc.suggest.side_effect = RuntimeError("anthropic down / out of credits")
    p_set, p_st, p_jm, _p_svc, storage = _patch(service=svc)
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        result = await process_proactive_auto_correct("job-1")  # must not raise
    assert result["status"] == "error"


# ---- lyrics-worker trigger ----

@pytest.mark.asyncio
async def test_lyrics_worker_fires_trigger() -> None:
    ws = MagicMock()
    ws.trigger_auto_correct = AsyncMock(return_value=True)
    with patch("backend.services.worker_service.WorkerService", return_value=ws):
        await _trigger_proactive_auto_correct("job-1", MagicMock())
    ws.trigger_auto_correct.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_lyrics_worker_trigger_swallows_errors() -> None:
    ws = MagicMock()
    ws.trigger_auto_correct = AsyncMock(side_effect=RuntimeError("network down"))
    job_log = MagicMock()
    with patch("backend.services.worker_service.WorkerService", return_value=ws):
        await _trigger_proactive_auto_correct("job-1", job_log)  # must not raise
    job_log.warning.assert_called()
