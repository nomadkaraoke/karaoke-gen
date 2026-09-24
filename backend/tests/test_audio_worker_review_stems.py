"""audio_worker pre-transcodes review stems after upload (non-fatal)."""
from unittest.mock import Mock, patch

import pytest


@pytest.mark.asyncio
async def test_transcode_review_stems_calls_service_with_fresh_job():
    from backend.workers.audio_worker import _transcode_review_stems

    job = Mock(job_id="j1")
    job_manager = Mock()
    job_manager.get_job.return_value = job
    storage = Mock()
    job_log = Mock()

    with patch(
        "backend.services.audio_transcoding_service.AudioTranscodingService"
    ) as svc_cls:
        svc_cls.return_value.transcode_review_stems.return_value = ["a.ogg", "b.ogg"]
        await _transcode_review_stems("j1", job_manager, storage, job_log)

    svc_cls.assert_called_once_with(storage_service=storage)
    svc_cls.return_value.transcode_review_stems.assert_called_once_with(job)
    job_log.info.assert_called_once()


@pytest.mark.asyncio
async def test_transcode_review_stems_is_non_fatal():
    from backend.workers.audio_worker import _transcode_review_stems

    job_manager = Mock()
    job_manager.get_job.return_value = Mock(job_id="j1")
    job_log = Mock()
    with patch(
        "backend.services.audio_transcoding_service.AudioTranscodingService"
    ) as svc_cls:
        svc_cls.return_value.transcode_review_stems.side_effect = RuntimeError("boom")
        await _transcode_review_stems("j1", job_manager, Mock(), job_log)  # must not raise
    job_log.warning.assert_called_once()


@pytest.mark.asyncio
async def test_transcode_review_stems_missing_job():
    from backend.workers.audio_worker import _transcode_review_stems

    job_manager = Mock()
    job_manager.get_job.return_value = None
    with patch(
        "backend.services.audio_transcoding_service.AudioTranscodingService"
    ) as svc_cls:
        await _transcode_review_stems("j1", job_manager, Mock(), Mock())
    svc_cls.assert_not_called()
