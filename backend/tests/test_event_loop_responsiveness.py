"""The heavy steps of the screens worker / stale-review cron must not block the loop.

Prod (2026-10-03 analysis): these ran synchronously on the single uvicorn event
loop and froze every request on the instance for 20-45s, surfacing as the
"servers unavailable" banner. Each test patches the heavy sync call with a
``time.sleep`` and asserts a concurrent ticker keeps ticking meanwhile — i.e.
the work really runs in a worker thread.
"""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

BLOCK_S = 0.4


async def _ticks_during(coro) -> int:
    """Run coro while counting 20ms ticks; a blocked loop yields ~0 ticks."""
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        await coro
    finally:
        done.set()
        await t
    return ticks


def _sleep(*_a, **_kw):
    time.sleep(BLOCK_S)
    return {}


# Blocked for BLOCK_S → ~0-1 ticks; offloaded → ~BLOCK_S/0.02 = 20 ticks.
MIN_TICKS = 8


@pytest.mark.asyncio
async def test_upload_screens_runs_off_loop(tmp_path):
    from backend.workers import screens_worker

    title = tmp_path / "t.png"
    end = tmp_path / "e.png"
    title.write_bytes(b"x")
    end.write_bytes(b"x")
    storage = MagicMock()
    storage.upload_file.side_effect = _sleep
    ticks = await _ticks_during(
        screens_worker._upload_screens("j1", MagicMock(), storage, str(title), str(end))
    )
    assert storage.upload_file.call_count == 2
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_review_audio_transcode_runs_off_loop():
    from backend.workers import screens_worker

    jm = MagicMock()
    job = MagicMock()
    job.file_urls = {}
    job.input_media_gcs_path = None
    jm.get_job.return_value = job
    with patch("backend.services.audio_transcoding_service.AudioTranscodingService") as svc, \
         patch("backend.utils.stems.vocals_stem_path", return_value=None):
        svc.return_value.prepare_review_audio_for_job.side_effect = _sleep
        ticks = await _ticks_during(
            screens_worker._transcode_review_audio("j1", jm, MagicMock())
        )
    svc.return_value.prepare_review_audio_for_job.assert_called_once()
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_title_screen_render_runs_off_loop(tmp_path):
    from backend.workers import screens_worker

    gen = MagicMock()
    gen.create_title_video.side_effect = _sleep
    style = MagicMock()
    style.get_intro_format.return_value = {}
    job = MagicMock(artist="A", title="B")
    ticks = await _ticks_during(
        screens_worker._generate_title_screen("j1", job, gen, style, str(tmp_path))
    )
    gen.create_title_video.assert_called_once()
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_style_config_load_runs_off_loop(tmp_path):
    from backend.workers import style_helper

    job = MagicMock(job_id="j1", style_assets={}, style_params_gcs_path=None)

    def slow_load(**_kw):
        time.sleep(BLOCK_S)
        return (None, {})

    with patch.object(style_helper, "load_styles_from_gcs", side_effect=slow_load):
        cfg = style_helper.StyleConfig(job, MagicMock(), str(tmp_path))
        ticks = await _ticks_during(cfg.load())
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_stale_review_cron_runs_off_loop():
    from backend.workers import stale_review_processor

    with patch.object(stale_review_processor, "process_stale_reviews_sync", side_effect=_sleep):
        ticks = await _ticks_during(stale_review_processor.process_stale_reviews())
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_health_encoding_status_builds_service_off_loop():
    """A cold get_encoding_service() (imports compute_v1, ~10s) must not block the loop."""
    from backend.api.routes import health

    def slow_build():
        time.sleep(BLOCK_S)
        svc = MagicMock()
        svc.is_enabled = False
        return svc

    with patch.object(health, "get_encoding_service", side_effect=slow_build):
        ticks = await _ticks_during(health.check_encoding_worker_status())
    assert ticks >= MIN_TICKS


def test_get_encoding_service_builds_once_under_concurrency():
    import threading

    from backend.services import encoding_service as es

    builds = []

    class FakeService:
        def __init__(self):
            builds.append(1)
            time.sleep(0.05)

        def set_worker_manager(self, m):
            pass

    with patch.object(es, "_encoding_service", None), patch.object(es, "EncodingService", FakeService):
        results = []
        threads = [threading.Thread(target=lambda: results.append(es.get_encoding_service())) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(builds) == 1
        assert len({id(r) for r in results}) == 1


# --- 2026-10-08: routes/cron stalls traced by the loop watchdog ---------------
# complete_review's corrections upload (21s), create_job_from_search's theme prep
# (5-9s), recover_stuck_jobs' Firestore scans (~260 stalls/week), the YouTube
# queue processor and upload-duration validation all ran sync I/O on the loop.


def test_run_on_loop_runs_coroutine_on_the_given_loop():
    from backend.utils.loop_bridge import run_on_loop

    async def main():
        loop = asyncio.get_running_loop()
        seen = {}

        async def coro():
            seen["loop"] = asyncio.get_running_loop()
            return 42

        result = await asyncio.to_thread(run_on_loop, loop, coro())
        assert result == 42
        assert seen["loop"] is loop

    asyncio.run(main())


def test_run_on_loop_propagates_exceptions():
    from backend.utils.loop_bridge import run_on_loop

    async def main():
        loop = asyncio.get_running_loop()

        async def boom():
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            await asyncio.to_thread(run_on_loop, loop, boom())

    asyncio.run(main())


@pytest.mark.asyncio
async def test_complete_review_saves_off_loop_and_triggers_render():
    from unittest.mock import AsyncMock

    from backend.api.routes import review
    from backend.models.job import JobStatus

    job = MagicMock()
    job.status = JobStatus.AWAITING_REVIEW
    job.file_urls = {}
    job.existing_instrumental_gcs_path = None
    job.state_data = {}
    jm = MagicMock()
    jm.get_job.return_value = job
    jm.delete_state_data_keys.return_value = []
    storage = MagicMock()
    storage.upload_json.side_effect = _sleep

    with patch.object(review, "JobManager", return_value=jm), \
         patch.object(review, "StorageService", return_value=storage), \
         patch("backend.services.worker_service.get_worker_service") as ws:
        ws.return_value.trigger_render_video_worker = AsyncMock(return_value=True)
        result = {}

        async def call():
            result.update(await review.complete_review(
                job_id="j1",
                updated_data={"corrections": [], "instrumental_selection": "clean"},
                auth_info=("u@example.com", "job_owner"),
            ))

        ticks = await _ticks_during(call())

    storage.upload_json.assert_called_once()
    ws.return_value.trigger_render_video_worker.assert_awaited_once_with("j1")
    assert result == {"status": "success", "instrumental_selection": "clean"}
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_create_job_from_search_runs_off_loop():
    from backend.api.routes import jobs

    with patch.object(jobs, "_create_job_from_search_sync", side_effect=_sleep) as body:
        ticks = await _ticks_during(
            jobs.create_job_from_search(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        )
    body.assert_called_once()
    assert isinstance(body.call_args.args[-1], asyncio.AbstractEventLoop)
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
@pytest.mark.parametrize("route, body", [
    ("recover_stuck_jobs", "_recover_stuck_jobs_sync"),
    ("retry_pending_render_jobs", "_retry_pending_render_jobs_sync"),
])
async def test_internal_crons_run_off_loop(route, body):
    from backend.api.routes import internal

    with patch.object(internal, body, side_effect=_sleep) as mock_body:
        ticks = await _ticks_during(getattr(internal, route)(MagicMock(), MagicMock()))
    mock_body.assert_called_once()
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_upload_duration_validation_runs_off_loop():
    from backend.api.routes import file_upload

    with patch.object(file_upload, "_validate_audio_durations_sync", side_effect=_sleep):
        ticks = await _ticks_during(file_upload._validate_audio_durations(MagicMock(), "a.flac", "i.flac"))
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_youtube_queue_single_upload_runs_off_loop():
    from backend.workers import youtube_queue_processor as qp

    with patch.object(qp, "_process_single_upload_sync", side_effect=_sleep):
        ticks = await _ticks_during(qp._process_single_upload("j1", {}, MagicMock(), MagicMock()))
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_youtube_queue_quota_and_queue_reads_run_off_loop():
    from backend.workers import youtube_queue_processor as qp

    quota = MagicMock()
    quota.check_quota_available.side_effect = lambda: (_sleep(), (True, 100, "ok"))[1]
    queue = MagicMock()
    queue.get_queued_uploads.side_effect = lambda **_kw: (_sleep(), [])[1]
    with patch.object(qp, "get_youtube_quota_service", return_value=quota), \
         patch.object(qp, "get_youtube_upload_queue_service", return_value=queue):
        ticks = await _ticks_during(qp.process_youtube_upload_queue())
    # 2 x BLOCK_S of blocking work; offloaded it ticks through both.
    assert ticks >= 2 * MIN_TICKS


@pytest.mark.asyncio
async def test_cloud_run_job_dispatch_runs_off_loop():
    from backend.services.worker_service import WorkerService

    client = MagicMock()
    client.run_job.side_effect = _sleep
    ws = WorkerService.__new__(WorkerService)
    ticks = await _ticks_during(ws._run_job_with_retry(client, MagicMock(), log_prefix="[t]"))
    client.run_job.assert_called_once()
    assert ticks >= MIN_TICKS


@pytest.mark.asyncio
async def test_render_trigger_bumps_generation_off_loop():
    from unittest.mock import AsyncMock

    from backend.services.worker_service import WorkerService

    ws = WorkerService.__new__(WorkerService)
    ws._use_cloud_tasks = False
    ws.settings = MagicMock(use_cloud_run_jobs_for_render=False)
    with patch.object(WorkerService, "_bump_worker_generation", side_effect=_sleep), \
         patch.object(WorkerService, "_start_encoding_worker_warmup"), \
         patch.object(WorkerService, "trigger_worker", new=AsyncMock(return_value=True)):
        ticks = await _ticks_during(ws.trigger_render_video_worker("j1"))
    assert ticks >= MIN_TICKS
