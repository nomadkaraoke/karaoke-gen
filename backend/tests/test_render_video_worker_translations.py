"""Render worker: translated lyrics are prepared before render and passed to the encoder."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.encoding_errors import EncodingWorkerCapacityError


def _job(translation_language):
    job = MagicMock()
    job.artist = "Test Artist"
    job.title = "Test Title"
    job.input_media_gcs_path = "jobs/test/audio.flac"
    job.style_assets = {}
    job.style_params_gcs_path = None
    job.subtitle_offset_ms = 0
    job.prep_only = False
    job.state_data = {}
    job.file_urls = {}
    job.translation_language = translation_language
    return job


async def _render_config(translation_language, prepared_path):
    from backend.workers import render_video_worker as rvw

    job_manager = MagicMock()
    job_manager.get_job.return_value = _job(translation_language)
    job_manager.transition_to_state.return_value = True
    encoding_service = MagicMock()
    encoding_service.is_enabled = True
    # Stop right after the render is submitted
    encoding_service.render_video_on_gce = AsyncMock(
        side_effect=EncodingWorkerCapacityError("full", vm_name="vm", zone="z", code="ZONE_RESOURCE_POOL_EXHAUSTED")
    )
    storage = MagicMock()
    storage.file_exists.return_value = False
    settings = MagicMock()
    settings.gcs_bucket_name = "bucket"

    with patch.object(rvw, "JobManager", return_value=job_manager), \
         patch.object(rvw, "StorageService", return_value=storage), \
         patch.object(rvw, "get_settings", return_value=settings), \
         patch.object(rvw, "create_job_logger", return_value=MagicMock()), \
         patch.object(rvw, "setup_job_logging", return_value=MagicMock()), \
         patch.object(rvw, "validate_worker_can_run", return_value=None), \
         patch.object(rvw, "get_encoding_service", return_value=encoding_service), \
         patch.object(rvw, "prepare_job_translations", return_value=prepared_path) as prepare:
        await rvw.process_render_video("test-job-id")

    prepare.assert_called_once()
    return encoding_service.render_video_on_gce.call_args[0][1]


@pytest.mark.asyncio
async def test_translations_path_passed_to_encoder():
    config = await _render_config("es", "jobs/test-job-id/lyrics/translations.json")
    assert config["translations_gcs_path"] == "gs://bucket/jobs/test-job-id/lyrics/translations.json"


@pytest.mark.asyncio
async def test_no_translations_when_not_prepared():
    config = await _render_config(None, None)
    assert "translations_gcs_path" not in config
