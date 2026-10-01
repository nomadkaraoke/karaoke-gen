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

import google.api_core.exceptions as gexc  # noqa: E402


class _FakeBlob:
    def __init__(self, bucket, path):
        self.bucket, self.path = bucket, path
        self.generation = None

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        current = self.bucket.objects.get(self.path)
        current_gen = current[0] if current else 0
        if if_generation_match is not None and if_generation_match != current_gen:
            raise gexc.PreconditionFailed("generation mismatch")
        self.bucket.next_gen += 1
        self.generation = self.bucket.next_gen
        self.bucket.objects[self.path] = (self.generation, data)

    def download_as_bytes(self):
        return self.bucket.objects[self.path][1].encode()

    def delete(self, if_generation_match=None):
        current = self.bucket.objects.get(self.path)
        if current is None:
            raise gexc.NotFound("gone")
        if if_generation_match is not None and if_generation_match != current[0]:
            raise gexc.PreconditionFailed("generation mismatch")
        del self.bucket.objects[self.path]


class _FakeBucket:
    def __init__(self):
        self.objects: dict = {}
        self.next_gen = 100

    def blob(self, path):
        return _FakeBlob(self, path)

    def get_blob(self, path):
        if path not in self.objects:
            return None
        b = _FakeBlob(self, path)
        b.generation = self.objects[path][0]
        return b

    def put_lease(self, job_id, acquired_at):
        import json as _json
        self.next_gen += 1
        self.objects[f"jobs/{job_id}/lyrics/auto_correct_inflight.json"] = (
            self.next_gen, _json.dumps({"acquired_at": acquired_at}))


def _patch_with_bucket(service):
    p_set, p_st, p_jm, _p_svc, storage = _patch(service=service)
    bucket = _FakeBucket()
    storage.bucket = bucket
    storage.file_exists.side_effect = lambda p: p.endswith("corrections.json") or p in bucket.objects
    return p_set, p_st, p_jm, storage, bucket


@pytest.mark.asyncio
async def test_concurrent_calls_on_same_instance_share_one_run() -> None:
    """Regression (job 96cf1100): the lyrics trigger and the screens pre-apply
    both called this within seconds and each ran the multi-model suggest."""
    import asyncio as _asyncio
    import threading

    release = threading.Event()
    svc = MagicMock()

    def _slow_suggest(**_kw):
        release.wait(5)
        return SimpleNamespace(suggestions=[1, 2, 3], model="m", elapsed_seconds=1.0)

    svc.suggest.side_effect = _slow_suggest
    p_set, p_st, p_jm, storage, bucket = _patch_with_bucket(svc)
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        first = _asyncio.create_task(process_proactive_auto_correct("job-dup"))
        await _asyncio.sleep(0.05)
        assert "jobs/job-dup/lyrics/auto_correct_inflight.json" in bucket.objects  # lease held
        second = _asyncio.create_task(process_proactive_auto_correct("job-dup"))
        await _asyncio.sleep(0.05)
        release.set()
        r1, r2 = await _asyncio.gather(first, second)
        assert svc.suggest.call_count == 1
        assert r1 == r2 == {"status": "generated", "suggestions": 3}
        assert bucket.objects == {}  # our lease released

        # A later (non-concurrent) call isn't blocked by a leftover in-flight entry.
        await process_proactive_auto_correct("job-dup")
    assert svc.suggest.call_count == 2  # in prod this hits the service's own GCS cache


@pytest.mark.asyncio
async def test_peer_instance_holding_lease_is_awaited_not_duplicated(monkeypatch) -> None:
    import time as _time
    from backend.workers import auto_correct_worker as w

    monkeypatch.setattr(w, "PEER_POLL_SECONDS", 0)
    svc = MagicMock()
    p_set, p_st, p_jm, storage, bucket = _patch_with_bucket(svc)
    bucket.put_lease("job-peer", _time.time())  # fresh lease held by another instance
    peer_cache = iter([None, None, [{"id": "s1"}, {"id": "s2"}]])
    with p_set, p_st, p_jm, \
            patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc), \
            patch("backend.services.auto_approval.executor._load_ai_suggestions",
                  side_effect=lambda *_a: next(peer_cache)):
        result = await process_proactive_auto_correct("job-peer")
    assert result == {"status": "deduped", "suggestions": 2}
    svc.suggest.assert_not_called()
    assert "jobs/job-peer/lyrics/auto_correct_inflight.json" in bucket.objects  # never released theirs


@pytest.mark.asyncio
async def test_stale_peer_lease_is_taken_over_with_generation_fence() -> None:
    svc = MagicMock()
    svc.suggest.return_value = SimpleNamespace(suggestions=[1], model="m", elapsed_seconds=1.0)
    p_set, p_st, p_jm, storage, bucket = _patch_with_bucket(svc)
    bucket.put_lease("job-stale", 0)  # leftover from a crashed instance
    with p_set, p_st, p_jm, patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc):
        result = await process_proactive_auto_correct("job-stale")
    assert result["status"] == "generated"
    svc.suggest.assert_called_once()
    assert bucket.objects == {}


def test_stale_takeover_loses_to_a_concurrent_takeover() -> None:
    from backend.workers import auto_correct_worker as w

    bucket = _FakeBucket()
    bucket.put_lease("j", 0)
    storage = SimpleNamespace(bucket=bucket)
    real_get_blob = bucket.get_blob

    def _get_blob_then_race(path):
        observed = real_get_blob(path)
        bucket.put_lease("j", 0)  # another instance takes over after our read
        return observed

    bucket.get_blob = _get_blob_then_race
    assert w._acquire_lease(storage, "j") == (False, None)


def test_own_lease_after_lost_create_response_counts_as_held(monkeypatch) -> None:
    """Upload retry after a lost response 412s on our own write: still ours."""
    import json as _json
    from backend.workers import auto_correct_worker as w

    bucket = _FakeBucket()
    storage = SimpleNamespace(bucket=bucket)
    monkeypatch.setattr(w.uuid, "uuid4", lambda: SimpleNamespace(hex="mytoken"))
    real_blob = bucket.blob

    def _blob_lost_response(path):
        b = real_blob(path)
        orig = b.upload_from_string

        def _upload(data, content_type=None, if_generation_match=None):
            orig(data, content_type=content_type, if_generation_match=if_generation_match)
            raise gexc.PreconditionFailed("retry of a create that already landed")

        b.upload_from_string = _upload
        return b

    bucket.blob = _blob_lost_response
    held, gen = w._acquire_lease(storage, "j")
    assert held and gen == bucket.objects["jobs/j/lyrics/auto_correct_inflight.json"][0]
    assert _json.loads(bucket.objects["jobs/j/lyrics/auto_correct_inflight.json"][1])["token"] == "mytoken"


def test_release_never_deletes_a_lease_someone_else_took_over() -> None:
    import time as _time
    from backend.workers import auto_correct_worker as w

    bucket = _FakeBucket()
    storage = SimpleNamespace(bucket=bucket)
    held, gen = w._acquire_lease(storage, "j")
    assert held and gen is not None
    bucket.put_lease("j", _time.time())  # superseded (e.g. our run outlived the TTL)
    w._release_lease(storage, "j", gen)
    assert "jobs/j/lyrics/auto_correct_inflight.json" in bucket.objects


@pytest.mark.asyncio
async def test_peer_failure_does_not_regenerate(monkeypatch) -> None:
    import time as _time
    from backend.workers import auto_correct_worker as w

    monkeypatch.setattr(w, "PEER_POLL_SECONDS", 0)
    svc = MagicMock()
    p_set, p_st, p_jm, storage, bucket = _patch_with_bucket(svc)
    bucket.put_lease("job-peer-fail", _time.time())
    lease = "jobs/job-peer-fail/lyrics/auto_correct_inflight.json"

    def _no_cache(*_a):
        bucket.objects.pop(lease, None)  # peer finishes (fails) without writing a cache
        return None

    with p_set, p_st, p_jm, \
            patch("backend.services.auto_correct.get_auto_correct_service", return_value=svc), \
            patch("backend.services.auto_approval.executor._load_ai_suggestions", side_effect=_no_cache):
        result = await process_proactive_auto_correct("job-peer-fail")
    assert result == {"status": "skipped", "reason": "peer_failed"}
    svc.suggest.assert_not_called()


@pytest.mark.asyncio
async def test_timed_out_run_keeps_its_lease(monkeypatch) -> None:
    from backend.workers import auto_correct_worker as w

    svc = MagicMock()
    p_set, p_st, p_jm, storage, bucket = _patch_with_bucket(svc)

    async def _timeout(*_a, **_k):
        raise TimeoutError()

    monkeypatch.setattr(w, "_run_suggest", _timeout)
    with p_set, p_st, p_jm:
        result = await process_proactive_auto_correct("job-slow")
    assert result["status"] == "error"
    # suggest() may still be running in its thread: the lease must stay until TTL.
    assert "jobs/job-slow/lyrics/auto_correct_inflight.json" in bucket.objects
