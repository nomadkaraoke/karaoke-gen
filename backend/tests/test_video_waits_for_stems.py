"""Final encode waits for stem separation instead of failing (2026-10-19).

Review can finish before separation does. The video worker used to fail its
prerequisites ("Missing instrumental: instrumental_clean") 3x and leave the job at
instrumental_selected forever. Now it parks the job, the audio worker resumes it
when the stems land, and an atomic claim on the flag makes the park/resume race
safe.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus
from backend.services.job_health_service import selected_stem_pending
from backend.services.job_manager import JobManager
from backend.workers.video_worker import _park_until_stems_ready


def _job(*, status=JobStatus.INSTRUMENTAL_SELECTED, selection="clean", audio_complete=False,
         stems=None, waiting=None):
    sd = {"instrumental_selection": selection, "audio_complete": audio_complete}
    if waiting is not None:
        sd["video_waiting_for_stems"] = waiting
    return Job(
        job_id="j1", artist="A", title="B", status=status,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        state_data=sd, file_urls={"stems": stems or {}},
    )


READY = {"instrumental_clean": "gs://b/c.flac", "instrumental_with_backing": "gs://b/w.flac"}


class TestSelectedStemPending:
    def test_pending_when_stem_missing_and_separation_running(self):
        assert selected_stem_pending(_job()) is True

    def test_with_backing_selection_checks_its_own_stem(self):
        assert selected_stem_pending(_job(selection="with_backing",
                                          stems={"instrumental_clean": "x"})) is True

    def test_not_pending_once_stem_exists(self):
        assert selected_stem_pending(_job(stems=READY)) is False

    def test_not_pending_when_separation_finished_without_stem(self):
        """audio_complete but no stem = a real failure; let prerequisites reject it."""
        assert selected_stem_pending(_job(audio_complete=True)) is False

    def test_custom_instrumental_never_waits(self):
        assert selected_stem_pending(_job(selection="custom")) is False

    @pytest.mark.parametrize("status", [JobStatus.GENERATING_VIDEO, JobStatus.COMPLETE, JobStatus.FAILED])
    def test_wrong_status_never_parks(self, status):
        assert selected_stem_pending(_job(status=status)) is False


class TestParkUntilStemsReady:
    def _jm(self, refreshed, claim=False):
        jm = MagicMock()
        jm.get_job.return_value = refreshed
        jm.claim_video_stems_wait.return_value = claim
        return jm

    def test_not_pending_continues_without_touching_the_flag(self):
        jm = self._jm(None)
        parked, job = _park_until_stems_ready("j1", _job(stems=READY), jm, MagicMock())
        assert parked is False and job.file_urls["stems"] == READY
        jm.park_video_for_stems.assert_not_called()

    def test_still_pending_after_refresh_parks(self):
        jm = self._jm(_job())
        parked, _ = _park_until_stems_ready("j1", _job(), jm, MagicMock())
        assert parked is True
        jm.park_video_for_stems.assert_called_once_with("j1")
        jm.claim_video_stems_wait.assert_not_called()

    def test_stems_landed_while_parking_and_we_win_the_claim_continues(self):
        jm = self._jm(_job(stems=READY, audio_complete=True), claim=True)
        parked, job = _park_until_stems_ready("j1", _job(), jm, MagicMock())
        assert parked is False
        assert job.file_urls["stems"] == READY  # continues with the refreshed job

    def test_stems_landed_but_audio_worker_already_resumed_exits(self):
        jm = self._jm(_job(stems=READY, audio_complete=True), claim=False)
        parked, _ = _park_until_stems_ready("j1", _job(), jm, MagicMock())
        assert parked is True  # the audio worker dispatched a fresh run


class TestResumeVideoIfWaitingForStems:
    def _jm(self, *, claimed, job):
        jm = JobManager.__new__(JobManager)
        jm.firestore = MagicMock()
        jm.firestore.claim_job_state_flag.return_value = claimed
        jm.get_job = MagicMock(return_value=job)
        return jm

    @pytest.mark.asyncio
    async def test_resumes_parked_job(self):
        jm = self._jm(claimed=True, job=_job(stems=READY, audio_complete=True))
        ws = MagicMock(trigger_video_worker=AsyncMock(return_value=True))
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            assert await jm.resume_video_if_waiting_for_stems("j1") is True
        jm.firestore.claim_job_state_flag.assert_called_once_with("j1", "video_waiting_for_stems")
        ws.trigger_video_worker.assert_awaited_once_with("j1")

    @pytest.mark.asyncio
    async def test_no_flag_no_trigger(self):
        """Normal ordering (stems before review): nothing parked, nothing to do."""
        jm = self._jm(claimed=False, job=_job())
        ws = MagicMock(trigger_video_worker=AsyncMock(return_value=True))
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            assert await jm.resume_video_if_waiting_for_stems("j1") is False
        ws.trigger_video_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_moved_on_is_not_resumed(self):
        jm = self._jm(claimed=True, job=_job(status=JobStatus.FAILED))
        ws = MagicMock(trigger_video_worker=AsyncMock(return_value=True))
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            assert await jm.resume_video_if_waiting_for_stems("j1") is False
        ws.trigger_video_worker.assert_not_awaited()


def test_park_sets_flag_and_user_facing_message():
    jm = JobManager.__new__(JobManager)
    jm.update_job = MagicMock()
    jm.park_video_for_stems("j1")
    updates = jm.update_job.call_args.args[1]
    assert updates["state_data.video_waiting_for_stems"] is True
    assert "starts automatically" in updates["message"]
