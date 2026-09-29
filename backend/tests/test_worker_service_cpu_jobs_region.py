"""Latency-critical CPU Cloud Run Jobs are triggered in cpu_jobs_region (us-east4).

Cloud Run Jobs in us-central1 queue 2-5 min before the container starts, even for
a tiny sample image (measured 2026-09-28: us-central1 4-5 min, us-east4 8-19s).
audio-download-job, lyrics-transcription-job, bulk-search-job and
video-encoding-job (video + post-review render workers) therefore run in us-east4,
while the jobs and workers keep GCP_REGION=us-central1 for everything else.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _settings(monkeypatch, cpu_jobs_region: str | None = None):
    monkeypatch.setenv("ENABLE_CLOUD_TASKS", "true")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    if cpu_jobs_region is None:
        monkeypatch.delenv("CPU_JOBS_REGION", raising=False)
    else:
        monkeypatch.setenv("CPU_JOBS_REGION", cpu_jobs_region)
    from backend.config import Settings
    return Settings()


async def _triggered_job_name(settings, trigger) -> str:
    """Run ``trigger(service)`` against a mocked run_v2 and return the job path."""
    from backend.services.worker_service import WorkerService

    mock_run_v2 = MagicMock()
    mock_run_v2.JobsClient.return_value.run_job.return_value = MagicMock(metadata="m")

    import google.cloud
    with patch("backend.services.worker_service.get_settings", return_value=settings), \
         patch.dict("sys.modules", {"google.cloud.run_v2": mock_run_v2}), \
         patch.object(google.cloud, "run_v2", mock_run_v2, create=True):
        assert await trigger(WorkerService()) is True

    return mock_run_v2.RunJobRequest.call_args.kwargs["name"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger, job",
    [
        (lambda s: s.trigger_audio_download_worker("job1"), "audio-download-job"),
        (lambda s: s.trigger_lyrics_worker("job1"), "lyrics-transcription-job"),
        (lambda s: s.trigger_bulk_search_worker("batch1"), "bulk-search-job"),
    ],
)
async def test_cpu_jobs_triggered_in_us_east4(monkeypatch, trigger, job) -> None:
    name = await _triggered_job_name(_settings(monkeypatch), trigger)
    assert name == f"projects/test-project/locations/us-east4/jobs/{job}"


@pytest.mark.asyncio
async def test_cpu_jobs_region_env_override_for_rollback(monkeypatch) -> None:
    settings = _settings(monkeypatch, cpu_jobs_region="us-central1")
    name = await _triggered_job_name(settings, lambda s: s.trigger_audio_download_worker("job1"))
    assert name == "projects/test-project/locations/us-central1/jobs/audio-download-job"


@pytest.mark.asyncio
async def test_video_encoding_job_triggered_in_us_east4(monkeypatch) -> None:
    name = await _triggered_job_name(_settings(monkeypatch), lambda s: s._trigger_cloud_run_job("job1"))
    assert name == "projects/test-project/locations/us-east4/jobs/video-encoding-job"


@pytest.mark.asyncio
async def test_render_video_job_triggered_in_us_east4(monkeypatch) -> None:
    monkeypatch.setenv("USE_CLOUD_RUN_JOBS_FOR_RENDER", "true")
    settings = _settings(monkeypatch)

    async def trigger(service):
        with patch.object(service, "_bump_worker_generation"), \
             patch.object(service, "_start_encoding_worker_warmup"):
            return await service.trigger_render_video_worker("job1")

    name = await _triggered_job_name(settings, trigger)
    assert name == "projects/test-project/locations/us-east4/jobs/video-encoding-job"


def test_gcp_region_unaffected_by_cpu_jobs_region(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    assert settings.cpu_jobs_region == "us-east4"
    assert settings.gcp_region == "us-central1"


def test_backend_default_matches_pulumi_cpu_jobs_region(monkeypatch) -> None:
    """The backend triggers jobs where Pulumi creates them; the two must agree."""
    src = (REPO_ROOT / "infrastructure" / "modules" / "cloud_run.py").read_text()
    m = re.search(r'^CPU_JOBS_REGION = "([a-z0-9-]+)"', src, re.MULTILINE)
    assert m, "could not locate CPU_JOBS_REGION in infrastructure/modules/cloud_run.py"
    assert _settings(monkeypatch).cpu_jobs_region == m.group(1)
