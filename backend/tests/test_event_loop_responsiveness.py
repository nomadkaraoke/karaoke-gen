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
