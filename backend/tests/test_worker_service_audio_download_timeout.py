"""audio-download-job gets a per-execution timeout sized to the torrent stall budget.

The worker waits up to ``torrent_wait_timeout_seconds(keep_trying)`` for flacfetch;
the Cloud Run execution timeout override (``task_timeout_seconds``) must exceed it
so the worker fails gracefully instead of being SIGKILLed. A user's "Keep trying"
retry needs the longer (1 hour stall) budget.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services import audio_download_limits


def _settings(monkeypatch):
    monkeypatch.setenv("ENABLE_CLOUD_TASKS", "true")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("CPU_JOBS_REGION", raising=False)
    from backend.config import Settings
    return Settings()


async def _trigger(monkeypatch, **kwargs) -> MagicMock:
    """Trigger the audio download worker against a mocked run_v2; return the mock."""
    from backend.services.worker_service import WorkerService

    mock_run_v2 = MagicMock()
    mock_run_v2.JobsClient.return_value.run_job.return_value = MagicMock(metadata="m")

    import google.cloud
    with patch("backend.services.worker_service.get_settings", return_value=_settings(monkeypatch)), \
         patch.dict("sys.modules", {"google.cloud.run_v2": mock_run_v2}), \
         patch.object(google.cloud, "run_v2", mock_run_v2, create=True):
        assert await WorkerService().trigger_audio_download_worker("job1", **kwargs) is True
    return mock_run_v2


def _timeout_seconds(mock_run_v2: MagicMock) -> int:
    overrides = mock_run_v2.RunJobRequest.call_args.kwargs["overrides"]
    return overrides.timeout.seconds


def _container_args(mock_run_v2: MagicMock) -> list[str]:
    return mock_run_v2.RunJobRequest.Overrides.ContainerOverride.call_args.kwargs["args"]


@pytest.mark.asyncio
async def test_default_timeout_override(monkeypatch) -> None:
    mock_run_v2 = await _trigger(monkeypatch)
    assert _timeout_seconds(mock_run_v2) == audio_download_limits.task_timeout_seconds(False)


@pytest.mark.asyncio
async def test_keep_trying_timeout_override(monkeypatch) -> None:
    mock_run_v2 = await _trigger(monkeypatch, keep_trying=True)
    assert _timeout_seconds(mock_run_v2) == audio_download_limits.task_timeout_seconds(True)
    assert _timeout_seconds(mock_run_v2) > audio_download_limits.task_timeout_seconds(False)


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_trying", [False, True])
async def test_container_args_still_set(monkeypatch, keep_trying) -> None:
    mock_run_v2 = await _trigger(monkeypatch, keep_trying=keep_trying)
    assert _container_args(mock_run_v2) == [
        "python", "-m", "backend.workers.audio_download_worker", "--job-id", "job1",
    ]
    assert mock_run_v2.RunJobRequest.call_args.kwargs["name"].endswith("/jobs/audio-download-job")


@pytest.mark.asyncio
async def test_other_jobs_get_no_timeout_override(monkeypatch) -> None:
    """Only audio-download-job overrides the timeout; other jobs keep their configured one."""
    from backend.services.worker_service import WorkerService

    mock_run_v2 = MagicMock()
    mock_run_v2.JobsClient.return_value.run_job.return_value = MagicMock(metadata="m")
    overrides = MagicMock(spec=["container_overrides"])
    mock_run_v2.RunJobRequest.Overrides.return_value = overrides

    import google.cloud
    with patch("backend.services.worker_service.get_settings", return_value=_settings(monkeypatch)), \
         patch.dict("sys.modules", {"google.cloud.run_v2": mock_run_v2}), \
         patch.object(google.cloud, "run_v2", mock_run_v2, create=True):
        assert await WorkerService().trigger_lyrics_worker("job1") is True

    assert "timeout" not in vars(overrides)
