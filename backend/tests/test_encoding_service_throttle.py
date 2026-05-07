"""Tests for the per-URL submission throttle on EncodingService.

Why this exists
---------------
The encoding worker has a 4-thread ThreadPoolExecutor. Submitting 7 renders
simultaneously to fallback-a (May 6 incident) caused `Connection reset by
peer` mid-encode. The throttle caps each Cloud Run instance's concurrent
submissions to a single worker URL at `_submission_concurrency` (default 3).

These tests cover the contract:
  - Submissions to different URLs don't block each other
  - Submissions to the same URL beyond the cap wait until a slot frees
  - The slot is held through the entire submit + wait_for_completion cycle
    (the worker is doing CPU work for that job until the poll returns
    complete/failed)
  - Slot is released even when the operation fails
  - The cap is configurable via ENCODING_SUBMISSION_CONCURRENCY env var
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from backend.services.encoding_service import EncodingService


@pytest.fixture
def service():
    s = EncodingService()
    s._url = "http://primary:8080"
    s._api_key = "test-key"
    s._initialized = True
    # Tighten the cap so tests stay fast
    s._submission_concurrency = 2
    return s


@pytest.mark.asyncio
async def test_slot_allows_concurrent_submissions_to_different_urls(service):
    """Different URLs must not contend on the same semaphore — otherwise
    multi-zone fallback would serialize across VMs unnecessarily."""
    enter_count = {"primary": 0, "fallback-a": 0}

    async def hold_slot(url, key):
        async with service._submission_slot(url, "test-job"):
            enter_count[key] += 1
            await asyncio.sleep(0.05)

    await asyncio.gather(
        hold_slot("http://primary:8080", "primary"),
        hold_slot("http://primary:8080", "primary"),
        hold_slot("http://fallback-a:8080", "fallback-a"),
        hold_slot("http://fallback-a:8080", "fallback-a"),
    )

    # All 4 entered cleanly — no inter-URL contention
    assert enter_count == {"primary": 2, "fallback-a": 2}


@pytest.mark.asyncio
async def test_slot_caps_concurrent_submissions_per_url(service):
    """When N+1 submissions to the same URL race, only N are in-flight at
    any moment."""
    in_flight = 0
    peak_in_flight = 0
    proceed = asyncio.Event()
    counts_lock = asyncio.Lock()

    async def hold_slot():
        nonlocal in_flight, peak_in_flight
        async with service._submission_slot("http://primary:8080", "j"):
            async with counts_lock:
                in_flight += 1
                peak_in_flight = max(peak_in_flight, in_flight)
            await proceed.wait()
            async with counts_lock:
                in_flight -= 1

    # Kick off 5 submissions; cap is 2 → peak should be 2
    tasks = [asyncio.create_task(hold_slot()) for _ in range(5)]
    await asyncio.sleep(0.05)  # let them queue up
    assert peak_in_flight == 2, (
        f"Throttle should cap at 2; saw {peak_in_flight} concurrent"
    )
    proceed.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_slot_released_on_exception(service):
    """If the wrapped operation raises, the slot must still release —
    otherwise a transient failure would leak slots until the process
    restart."""
    async def failing_op():
        async with service._submission_slot("http://primary:8080", "j"):
            raise RuntimeError("simulated failure")

    # If slot leaked, second call would deadlock. Run twice the cap to prove.
    for _ in range(4):
        with pytest.raises(RuntimeError, match="simulated failure"):
            await failing_op()

    # Semaphore should be back to full capacity
    sem = service._url_semaphores["http://primary:8080"]
    # asyncio.Semaphore exposes _value (cpython internals); use locked() as
    # a public-ish proxy
    assert not sem.locked()


@pytest.mark.asyncio
async def test_render_video_on_gce_holds_slot_through_submit_and_wait(service):
    """The slot must wrap submit + wait_for_completion, not just submit —
    otherwise we'd accept N submissions, release immediately, then poll
    all N in parallel while the worker is still encoding all N (still
    overloaded)."""
    # Inject a wait_for_completion that we can control
    completion_proceed = asyncio.Event()
    completed = AsyncMock(return_value={"status": "complete", "output_files": []})

    async def slow_wait(*args, **kwargs):
        await completion_proceed.wait()
        return await completed(*args, **kwargs)

    submit_mock = AsyncMock(return_value={"status": "accepted", "job_id": "x"})

    # Verify the slot is held during wait_for_completion: launch 3 ops
    # against the same URL with cap=2; the 3rd should be blocked until
    # we release one of the first two.
    started = []

    async def render(jid):
        started.append(jid)
        return await service.render_video_on_gce(jid, {"foo": "bar"})

    with patch.object(service, "submit_render_video_job", new=submit_mock), \
         patch.object(service, "wait_for_completion", new=slow_wait), \
         patch.object(service, "_get_worker_url", return_value="http://primary:8080"):

        tasks = [
            asyncio.create_task(render("a")),
            asyncio.create_task(render("b")),
            asyncio.create_task(render("c")),
        ]
        # All 3 tasks started, but only 2 should be in the wait stage
        await asyncio.sleep(0.05)
        assert submit_mock.await_count == 2, (
            f"Only 2 submissions should be in flight (cap=2); "
            f"got {submit_mock.await_count}"
        )

        completion_proceed.set()
        await asyncio.gather(*tasks)

        # Eventually all 3 must have submitted
        assert submit_mock.await_count == 3


@pytest.mark.asyncio
async def test_encode_videos_holds_slot_through_submit_and_wait(service):
    """Same contract as render_video_on_gce but for the final encode path."""
    completion_proceed = asyncio.Event()

    async def slow_wait(*args, **kwargs):
        await completion_proceed.wait()
        return {"status": "complete", "output_files": []}

    submit_mock = AsyncMock(return_value={"status": "accepted", "job_id": "x"})

    async def encode(jid):
        return await service.encode_videos(jid, "gs://in", "gs://out", {})

    with patch.object(service, "submit_encoding_job", new=submit_mock), \
         patch.object(service, "wait_for_completion", new=slow_wait), \
         patch.object(service, "_get_worker_url", return_value="http://primary:8080"):

        tasks = [asyncio.create_task(encode(j)) for j in ("a", "b", "c")]
        await asyncio.sleep(0.05)
        assert submit_mock.await_count == 2, (
            "Encode path must use the same per-URL throttle as render"
        )
        completion_proceed.set()
        await asyncio.gather(*tasks)
        assert submit_mock.await_count == 3


@pytest.mark.asyncio
async def test_cached_submission_releases_slot_quickly(service):
    """When the worker returns 'cached', we should return immediately and
    free the slot — otherwise idempotent re-submission of completed jobs
    would needlessly serialize."""
    submit_mock = AsyncMock(return_value={
        "status": "cached", "job_id": "x", "output_files": ["a.mp4"],
    })

    completed = []

    async def render(jid):
        result = await service.render_video_on_gce(jid, {"foo": "bar"})
        completed.append(jid)
        return result

    with patch.object(service, "submit_render_video_job", new=submit_mock), \
         patch.object(service, "wait_for_completion") as wait_mock, \
         patch.object(service, "_get_worker_url", return_value="http://primary:8080"):

        # 5 cached submissions should all complete, even though cap=2,
        # because each releases the slot before the next acquires
        await asyncio.gather(*(render(j) for j in "abcde"))

    # wait_for_completion should NEVER have been called for cached results
    wait_mock.assert_not_called()
    assert sorted(completed) == ["a", "b", "c", "d", "e"]


def test_submission_concurrency_configurable_via_env(monkeypatch):
    """Operators must be able to tune the cap without a code change —
    e.g., raise it temporarily during a backfill, or lower it if a
    smaller worker VM gets deployed."""
    monkeypatch.setenv("ENCODING_SUBMISSION_CONCURRENCY", "7")
    # Re-import module so the env-driven default is re-evaluated
    import importlib
    import backend.services.encoding_service as es_module
    importlib.reload(es_module)
    try:
        s = es_module.EncodingService()
        assert s._submission_concurrency == 7
    finally:
        # Reset for other tests in the suite
        monkeypatch.delenv("ENCODING_SUBMISSION_CONCURRENCY", raising=False)
        importlib.reload(es_module)
