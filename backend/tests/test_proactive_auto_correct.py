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


# ---- de-duplication (lyrics trigger vs screens pre-apply race) ----

@pytest.mark.asyncio
async def test_concurrent_calls_on_same_instance_share_one_run() -> None:
    """Regression (job 96cf1100): the lyrics trigger and the screens pre-apply
    both called this within seconds and each ran the multi-model suggest."""
    import threading
    import asyncio as _asyncio

    release = threading.Event()
    svc = MagicMock()

    def _slow_suggest(**_kw):
        release.wait(5)
        return SimpleNamespace(suggestions=[1, 2, 3], model="m", elapsed_seconds=1.0)

    svc.suggest.side_effect = _slow_suggest
    p_set, p_st, p_jm, _p_svc, storage = _patch(service=svc)
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        first = _asyncio.create_task(process_proactive_auto_correct("job-dup"))
        await _asyncio.sleep(0.05)
        second = _asyncio.create_task(process_proactive_auto_correct("job-dup"))
        await _asyncio.sleep(0.05)
        release.set()
        r1, r2 = await _asyncio.gather(first, second)
    assert svc.suggest.call_count == 1
    assert r1 == r2 == {"status": "generated", "suggestions": 3}
    # Lease taken create-only and released afterwards.
    lease_calls = [c for c in storage.upload_json.call_args_list if "auto_correct_inflight" in c.args[0]]
    assert lease_calls and lease_calls[0].kwargs.get("if_generation_match") == 0
    storage.delete_file.assert_called_once()

    # A later (non-concurrent) call is not blocked by a leftover in-flight entry.
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        await process_proactive_auto_correct("job-dup")
    assert svc.suggest.call_count == 2  # second suggest hits the service's own GCS cache in prod


@pytest.mark.asyncio
async def test_peer_instance_holding_lease_is_awaited_not_duplicated(monkeypatch) -> None:
    import time as _time
    import google.api_core.exceptions as gexc
    from backend.workers import auto_correct_worker as w

    monkeypatch.setattr(w, "PEER_POLL_SECONDS", 0)
    svc = MagicMock()
    p_set, p_st, p_jm, _p_svc, storage = _patch(service=svc)

    def _upload(path, data, if_generation_match=None):
        if "auto_correct_inflight" in path and if_generation_match == 0:
            raise gexc.PreconditionFailed("held by peer")
        return path

    def _download(path):
        if "auto_correct_inflight" in path:
            return {"acquired_at": _time.time()}  # fresh lease
        return CORRECTIONS

    storage.upload_json.side_effect = _upload
    storage.download_json.side_effect = _download
    peer_cache = iter([None, None, [{"id": "s1"}, {"id": "s2"}]])
    with p_set, p_st, p_jm, \
            patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc), \
            patch("backend.services.auto_approval.executor._load_ai_suggestions",
                  side_effect=lambda *_a: next(peer_cache)):
        result = await process_proactive_auto_correct("job-peer")
    assert result == {"status": "deduped", "suggestions": 2}
    svc.suggest.assert_not_called()
    storage.delete_file.assert_not_called()  # never release someone else's lease


@pytest.mark.asyncio
async def test_stale_peer_lease_is_taken_over() -> None:
    import google.api_core.exceptions as gexc

    svc = MagicMock()
    svc.suggest.return_value = SimpleNamespace(suggestions=[1], model="m", elapsed_seconds=1.0)
    p_set, p_st, p_jm, _p_svc, storage = _patch(service=svc)

    def _upload(path, data, if_generation_match=None):
        if "auto_correct_inflight" in path and if_generation_match == 0:
            raise gexc.PreconditionFailed("leftover from crashed instance")
        return path

    storage.upload_json.side_effect = _upload
    storage.download_json.side_effect = lambda p: {"acquired_at": 0} if "inflight" in p else CORRECTIONS
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        result = await process_proactive_auto_correct("job-stale")
    assert result["status"] == "generated"
    svc.suggest.assert_called_once()


@pytest.mark.asyncio
async def test_peer_failure_does_not_regenerate(monkeypatch) -> None:
    import time as _time
    import google.api_core.exceptions as gexc
    from backend.workers import auto_correct_worker as w

    monkeypatch.setattr(w, "PEER_POLL_SECONDS", 0)
    svc = MagicMock()
    p_set, p_st, p_jm, _p_svc, storage = _patch(service=svc)
    storage.upload_json.side_effect = lambda path, data, if_generation_match=None: (
        (_ for _ in ()).throw(gexc.PreconditionFailed("held"))
        if "inflight" in path and if_generation_match == 0 else path
    )
    storage.download_json.side_effect = lambda p: {"acquired_at": _time.time()} if "inflight" in p else CORRECTIONS
    # corrections.json exists; the lease disappears (peer finished) with no cache.
    storage.file_exists.side_effect = lambda p: "inflight" not in p
    with p_set, p_st, p_jm, \
            patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc), \
            patch("backend.services.auto_approval.executor._load_ai_suggestions", return_value=None):
        result = await process_proactive_auto_correct("job-peer-fail")
    assert result == {"status": "skipped", "reason": "peer_failed"}
    svc.suggest.assert_not_called()
