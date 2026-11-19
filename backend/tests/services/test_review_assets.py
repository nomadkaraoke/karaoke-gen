"""prepare_review_assets: everything the review page loads is cached before review opens."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services.review_assets import prepare_review_assets


def _job(stems=None):
    return SimpleNamespace(
        job_id="j1",
        input_media_gcs_path="jobs/j1/input/audio/song.flac",
        file_urls={"stems": stems or {}},
    )


async def _run(job):
    jm = MagicMock()
    jm.get_job.return_value = job
    transcoding = MagicMock()
    transcoding.prepare_review_audio_for_job.return_value = ["a.ogg"]
    analysis = MagicMock()
    with patch("backend.services.audio_transcoding_service.AudioTranscodingService",
               return_value=transcoding), \
         patch("backend.services.audio_analysis_service.AudioAnalysisService",
               return_value=analysis):
        await prepare_review_assets("j1", jm)
    return transcoding, analysis


@pytest.mark.asyncio
async def test_with_stems_caches_backing_waveform_and_vocal_peaks():
    transcoding, analysis = await _run(_job({
        "backing_vocals": "jobs/j1/stems/backing_vocals.flac",
        "vocals_clean": "jobs/j1/stems/vocals_clean.flac",
    }))
    transcoding.prepare_review_audio_for_job.assert_called_once()
    assert analysis.get_review_waveform.call_args.args[:3] == (
        "jobs/j1/stems/backing_vocals.flac", "j1", 1000)
    assert analysis.get_vocals_peaks.call_args.args[:3] == (
        "jobs/j1/stems/vocals_clean.flac", "j1", 400)


@pytest.mark.asyncio
async def test_without_stems_uses_input_audio_and_skips_vocal_peaks():
    _, analysis = await _run(_job())
    assert analysis.get_review_waveform.call_args.args[0] == "jobs/j1/input/audio/song.flac"
    analysis.get_vocals_peaks.assert_not_called()


@pytest.mark.asyncio
async def test_failures_are_non_fatal():
    jm = MagicMock()
    jm.get_job.side_effect = RuntimeError("firestore down")
    await prepare_review_assets("j1", jm)  # must not raise


@pytest.mark.asyncio
async def test_transcode_failure_still_caches_waveforms():
    jm = MagicMock()
    jm.get_job.return_value = _job({"backing_vocals": "b.flac", "vocals_clean": "v.flac"})
    transcoding = MagicMock()
    transcoding.prepare_review_audio_for_job.side_effect = RuntimeError("ffmpeg")
    analysis = MagicMock()
    with patch("backend.services.audio_transcoding_service.AudioTranscodingService",
               return_value=transcoding), \
         patch("backend.services.audio_analysis_service.AudioAnalysisService",
               return_value=analysis):
        await prepare_review_assets("j1", jm)
    analysis.get_review_waveform.assert_called_once()
    analysis.get_vocals_peaks.assert_called_once()
