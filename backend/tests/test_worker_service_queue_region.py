"""Cloud Tasks queue paths must use cloud_tasks_region, not gcp_region.

The GPU audio-separation-job runs with GCP_REGION set to its GPU region, but
every Cloud Tasks queue lives in us-central1 — building queue paths from
gcp_region 404s ("Queue does not exist") from that worker, which silently
dropped the auto-approval render trigger (job b8bda9c2, 2026-08-27).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_enqueue_uses_cloud_tasks_region_not_gcp_region(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_CLOUD_TASKS", "true")
    monkeypatch.setenv("GCP_REGION", "us-east4")  # simulate the GPU job env
    monkeypatch.delenv("CLOUD_TASKS_REGION", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

    from backend.config import Settings
    from backend.services.worker_service import WorkerService

    with patch("backend.services.worker_service.get_settings", return_value=Settings()):
        service = WorkerService()
        tasks_client = MagicMock()
        tasks_client.queue_path.side_effect = (
            lambda project, location, queue: f"projects/{project}/locations/{location}/queues/{queue}"
        )
        service._tasks_client = tasks_client

        await service._enqueue_cloud_task("render-video", "job1")

    args = tasks_client.queue_path.call_args.args
    assert args[1] == "us-central1", f"queue location must be us-central1, got {args[1]}"


def test_settings_cloud_tasks_region_default(monkeypatch) -> None:
    monkeypatch.delenv("CLOUD_TASKS_REGION", raising=False)
    from backend.config import Settings
    assert Settings().cloud_tasks_region == "us-central1"


# --- REVIEW_COMPLETE stall detection (lost render trigger) ---

def _job(minutes_old: float, render_progress: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        state_data={"render_progress": {"stage": "encoding"}} if render_progress else {},
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_old),
    )


def test_review_complete_stalled_after_ten_minutes() -> None:
    from backend.api.routes.internal import _review_complete_stalled
    assert _review_complete_stalled(_job(minutes_old=15)) is True


def test_review_complete_fresh_is_not_stalled() -> None:
    from backend.api.routes.internal import _review_complete_stalled
    assert _review_complete_stalled(_job(minutes_old=2)) is False


def test_review_complete_with_render_underway_is_not_stalled() -> None:
    from backend.api.routes.internal import _review_complete_stalled
    assert _review_complete_stalled(_job(minutes_old=30, render_progress=True)) is False
