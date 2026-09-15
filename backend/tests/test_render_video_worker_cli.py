"""
Tests for the render video worker CLI entry point (Cloud Run Jobs execution).

The render worker runs as a Cloud Run Job (video-encoding-job with an args
override) so it survives Cloud Run Service deployment rollouts — as a
BackgroundTask it was killed ~10s after SIGTERM whenever a deploy landed
mid-render (incident 2026-09-13, job 41e06b90).

Exit-code contract (deliberately different from video_worker): the Cloud Run
Job template has max_retries=2 and process_render_video handles every expected
failure internally (park / fail_job / supersede-discard). A clean return —
True OR False — must exit 0 so Cloud Run never re-runs the worker against a
job that already moved to a non-render state. Only an escaped exception
(crash before state was handled) exits 1 to request a Cloud Run retry.
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.workers.render_video_worker import main


def _run_main_with_args(job_id="job123"):
    return patch.object(sys, "argv", ["render_video_worker", "--job-id", job_id])


class TestRenderVideoWorkerCli:
    def test_main_exits_zero_on_success(self):
        with _run_main_with_args(), \
             patch("backend.workers.render_video_worker.process_render_video",
                   new=AsyncMock(return_value=True)):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_main_exits_zero_on_handled_failure(self):
        """False means the worker already parked/failed/superseded the job —
        a Cloud Run retry would re-enter a job in a non-render state."""
        with _run_main_with_args(), \
             patch("backend.workers.render_video_worker.process_render_video",
                   new=AsyncMock(return_value=False)):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_main_exits_nonzero_on_crash(self):
        """An escaped exception means job state was NOT handled — exit 1 so
        Cloud Run's max_retries can re-run the worker."""
        with _run_main_with_args(), \
             patch("backend.workers.render_video_worker.process_render_video",
                   new=AsyncMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_passes_job_id(self):
        mock_worker = AsyncMock(return_value=True)
        with _run_main_with_args("abc999"), \
             patch("backend.workers.render_video_worker.process_render_video",
                   new=mock_worker):
            with pytest.raises(SystemExit):
                main()
        mock_worker.assert_awaited_once_with("abc999")

    def test_main_requires_job_id(self):
        with patch.object(sys, "argv", ["render_video_worker"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse exits 2 on missing required argument
            assert exc_info.value.code == 2
