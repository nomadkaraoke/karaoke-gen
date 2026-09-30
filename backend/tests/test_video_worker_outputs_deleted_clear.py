"""
Regression: a successful render must clear ``outputs_deleted_at``.

The edit flow (and admin "delete outputs") sets ``outputs_deleted_at``; the
frontend hides every download while it is set. The flag used to be cleared only
when the orchestrator uploaded to YouTube/Dropbox/GDrive, so an edited tenant job
(which never distributes anywhere) re-rendered fresh finals but kept showing no
download buttons forever (job 2579a1ea, randy-vild, 2026-09-29).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.workers import video_worker


def _orchestrator_result(**overrides):
    fields = dict(
        success=True,
        error_message=None,
        brand_code=None,
        youtube_url=None,
        youtube_upload_queued=False,
        dropbox_link=None,
        gdrive_files={},
        distribution_warnings=[],
        final_video=None,
        final_video_mkv=None,
        final_video_lossy=None,
        final_video_720p=None,
        final_with_vocals_mp4=None,
        final_karaoke_cdg_zip=None,
        final_karaoke_txt_zip=None,
        title_mov=None,
        end_mov=None,
        portrait_video=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.asyncio
async def test_successful_render_clears_outputs_deleted_without_distribution():
    job = MagicMock()
    job.job_id = "job123"
    job.artist = "Randy Vild"
    job.title = "What Goes Up"
    job.tenant_id = "randy-vild"
    job.enable_youtube_upload = False
    job.is_private = False
    job.organised_dir_rclone_root = None
    job.edit_count = 1

    job_manager = MagicMock()
    job_manager.get_job.return_value = job

    orchestrator = MagicMock()
    orchestrator.run = AsyncMock(return_value=_orchestrator_result())

    style_config = MagicMock()
    style_config.get_cdg_styles.return_value = None

    with patch.object(video_worker, "JobManager", return_value=job_manager), \
         patch.object(video_worker, "StorageService", return_value=MagicMock()), \
         patch.object(video_worker, "create_job_logger", return_value=MagicMock()), \
         patch.object(video_worker, "setup_job_logging", return_value=MagicMock()), \
         patch.object(video_worker, "_validate_prerequisites", return_value=True), \
         patch("backend.services.job_health_service.validate_worker_can_run", return_value=None), \
         patch.object(video_worker, "_setup_working_directory", new=AsyncMock()), \
         patch.object(video_worker, "load_style_config", new=AsyncMock(return_value=style_config)), \
         patch("backend.workers.video_worker_orchestrator.create_orchestrator_config_from_job", return_value=MagicMock()), \
         patch("backend.workers.video_worker_orchestrator.VideoWorkerOrchestrator", return_value=orchestrator), \
         patch.object(video_worker, "_handle_native_distribution", new=AsyncMock()), \
         patch.object(video_worker, "_upload_results", new=AsyncMock()), \
         patch.object(video_worker, "_store_video_processing_metadata"):
        ok = await video_worker.generate_video_orchestrated("job123")

    assert ok is True
    payloads = [c.args[1] for c in job_manager.update_job.call_args_list]
    cleared = [p for p in payloads if "outputs_deleted_at" in p]
    assert cleared, f"no update cleared outputs_deleted_at; updates were {payloads}"
    assert cleared[0]["outputs_deleted_at"] is None
    assert cleared[0]["outputs_deleted_by"] is None
