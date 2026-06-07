"""
Tests for duration reconciliation wired into start_job_processing (Task 12).

Covers:
- start_job_processing pauses when reconcile_and_maybe_pause returns True
  → workers are NOT triggered
- start_job_processing proceeds when reconcile_and_maybe_pause returns False
  → both workers ARE triggered
- reconcile is called AFTER input_media_gcs_path is set (upload's exact file)
- reconcile is bypassed for existing_instrumental jobs (they only trigger lyrics worker)
  — reconcile is still called but the no-instrumental-present path is covered
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call

import pytest
import sys

# Prevent real Firestore/GCS imports from being resolved at module load.
sys.modules.setdefault("google.cloud.firestore", MagicMock())
sys.modules.setdefault("google.cloud.storage", MagicMock())

from backend.models.job import Job, JobStatus
from backend.exceptions import InvalidStateTransitionError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)


def _pending_job(
    job_id="upload-job-1",
    gcs_path="uploads/upload-job-1/audio/song.flac",
    existing_instrumental=None,
):
    return Job(
        job_id=job_id,
        status=JobStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
        input_media_gcs_path=gcs_path,
        existing_instrumental_gcs_path=existing_instrumental,
        state_data={"credits_charged": 1},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_firestore():
    with patch("backend.services.job_manager.FirestoreService") as cls:
        svc = Mock()
        # get_job returns a PENDING job with input_media_gcs_path set
        svc.get_job.return_value = _pending_job()
        cls.return_value = svc
        yield svc


@pytest.fixture()
def job_manager(mock_firestore):
    from backend.services.job_manager import JobManager
    return JobManager()


@pytest.fixture()
def mock_worker_service():
    ws = Mock()
    ws.trigger_audio_worker = AsyncMock(return_value=True)
    ws.trigger_lyrics_worker = AsyncMock(return_value=True)
    return ws


# ---------------------------------------------------------------------------
# Tests: reconcile wired into start_job_processing
# ---------------------------------------------------------------------------


class TestStartJobProcessingReconcile:
    """Duration reconciliation is invoked from start_job_processing."""

    @pytest.mark.asyncio
    async def test_reconcile_called_before_workers(self, job_manager, mock_firestore, mock_worker_service):
        """reconcile_and_maybe_pause is called before triggering workers."""
        mock_reconcile = AsyncMock(return_value=False)
        with (
            patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service),
            patch("backend.services.job_manager.reconcile_and_maybe_pause", mock_reconcile, create=True),
        ):
            await job_manager.start_job_processing("upload-job-1")

        mock_reconcile.assert_awaited_once_with("upload-job-1")

    @pytest.mark.asyncio
    async def test_workers_not_triggered_when_reconcile_pauses(
        self, job_manager, mock_firestore, mock_worker_service
    ):
        """When reconcile_and_maybe_pause returns True, no workers must be started."""
        mock_reconcile = AsyncMock(return_value=True)
        with (
            patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service),
            patch("backend.services.job_manager.reconcile_and_maybe_pause", mock_reconcile, create=True),
        ):
            await job_manager.start_job_processing("upload-job-1")

        mock_worker_service.trigger_audio_worker.assert_not_called()
        mock_worker_service.trigger_lyrics_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_both_workers_triggered_when_reconcile_proceeds(
        self, job_manager, mock_firestore, mock_worker_service
    ):
        """When reconcile_and_maybe_pause returns False, both workers ARE triggered."""
        mock_reconcile = AsyncMock(return_value=False)
        with (
            patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service),
            patch("backend.services.job_manager.reconcile_and_maybe_pause", mock_reconcile, create=True),
        ):
            await job_manager.start_job_processing("upload-job-1")

        mock_worker_service.trigger_audio_worker.assert_awaited_once_with("upload-job-1")
        mock_worker_service.trigger_lyrics_worker.assert_awaited_once_with("upload-job-1")

    @pytest.mark.asyncio
    async def test_reconcile_is_called_after_transition_to_downloading(
        self, job_manager, mock_firestore, mock_worker_service
    ):
        """Reconcile must run AFTER the job has transitioned to DOWNLOADING.

        input_media_gcs_path is already set by the upload handler before
        start_job_processing is called; the job is transitioned to DOWNLOADING
        first, then reconcile probes it.  This test confirms the ordering by
        checking that transition_to_state is called before reconcile.
        """
        call_log: list[str] = []

        orig_transition = job_manager.transition_to_state

        def tracking_transition(*a, **kw):
            call_log.append("transition")
            return orig_transition(*a, **kw)

        async def tracking_reconcile(job_id):
            call_log.append("reconcile")
            return False

        with (
            patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service),
            patch("backend.services.job_manager.reconcile_and_maybe_pause", side_effect=tracking_reconcile, create=True),
            patch.object(job_manager, "transition_to_state", side_effect=tracking_transition),
        ):
            await job_manager.start_job_processing("upload-job-1")

        assert call_log.index("transition") < call_log.index("reconcile"), (
            "transition_to_state must precede reconcile_and_maybe_pause; "
            f"got order: {call_log}"
        )

    @pytest.mark.asyncio
    async def test_existing_instrumental_job_reconcile_then_lyrics_only(
        self, job_manager, mock_firestore, mock_worker_service
    ):
        """Jobs with existing_instrumental_gcs_path: reconcile is still called,
        then only lyrics worker is triggered (audio separation is skipped)."""
        mock_firestore.get_job.return_value = _pending_job(
            existing_instrumental="uploads/upload-job-1/audio/existing_instrumental.mp3"
        )

        mock_reconcile = AsyncMock(return_value=False)
        with (
            patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service),
            patch("backend.services.job_manager.reconcile_and_maybe_pause", mock_reconcile, create=True),
        ):
            await job_manager.start_job_processing("upload-job-1")

        # Reconcile must still be called
        mock_reconcile.assert_awaited_once_with("upload-job-1")
        # Audio worker must NOT be called (separation skipped)
        mock_worker_service.trigger_audio_worker.assert_not_called()
        # Lyrics worker IS called
        mock_worker_service.trigger_lyrics_worker.assert_awaited_once_with("upload-job-1")

    @pytest.mark.asyncio
    async def test_existing_instrumental_paused_by_reconcile(
        self, job_manager, mock_firestore, mock_worker_service
    ):
        """When reconcile returns True for existing-instrumental job,
        even the lyrics worker is NOT triggered."""
        mock_firestore.get_job.return_value = _pending_job(
            existing_instrumental="uploads/upload-job-1/audio/existing_instrumental.mp3"
        )

        mock_reconcile = AsyncMock(return_value=True)
        with (
            patch("backend.services.worker_service.get_worker_service", return_value=mock_worker_service),
            patch("backend.services.job_manager.reconcile_and_maybe_pause", mock_reconcile, create=True),
        ):
            await job_manager.start_job_processing("upload-job-1")

        mock_worker_service.trigger_audio_worker.assert_not_called()
        mock_worker_service.trigger_lyrics_worker.assert_not_called()
