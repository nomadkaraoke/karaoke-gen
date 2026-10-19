"""Worker Cloud Run Jobs must dispatch follow-on video/render work as Cloud Run
Jobs, never via Cloud Tasks -> a BackgroundTask on the API service (which runs
the video worker on the API's event loop: 52s + 81s freezes, 2026-10-19)."""

import importlib

import pytest


def _settings(monkeypatch, **env):
    for key in ("CLOUD_RUN_JOB", "USE_CLOUD_RUN_JOBS_FOR_VIDEO", "USE_CLOUD_RUN_JOBS_FOR_RENDER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import backend.config as config
    return importlib.reload(config).Settings()


@pytest.fixture(autouse=True)
def _restore_config(monkeypatch):
    yield
    monkeypatch.undo()
    import backend.config as config
    importlib.reload(config)


def test_inside_cloud_run_job_defaults_to_cloud_run_jobs(monkeypatch):
    s = _settings(monkeypatch, CLOUD_RUN_JOB="audio-separation-job")
    assert s.use_cloud_run_jobs_for_video is True
    assert s.use_cloud_run_jobs_for_render is True


def test_outside_cloud_run_job_default_unchanged(monkeypatch):
    s = _settings(monkeypatch)
    assert s.use_cloud_run_jobs_for_video is False
    assert s.use_cloud_run_jobs_for_render is False


def test_explicit_env_still_wins_inside_a_job(monkeypatch):
    s = _settings(monkeypatch, CLOUD_RUN_JOB="x", USE_CLOUD_RUN_JOBS_FOR_VIDEO="false",
                  USE_CLOUD_RUN_JOBS_FOR_RENDER="false")
    assert s.use_cloud_run_jobs_for_video is False
    assert s.use_cloud_run_jobs_for_render is False
