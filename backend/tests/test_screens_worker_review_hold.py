"""Screens worker + review gate: a job whose stems are still separating is held
in GENERATING_SCREENS (no review link, no notification) instead of opening
review, whatever the lyrics verdict. See backend/services/auto_approval/review_gate.py."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus


async def _run_screens(auto_result, *, audio_complete):
    from backend.workers import screens_worker as sw

    job = Job(
        job_id="j1", artist="A", title="T", status=JobStatus.DOWNLOADING,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        state_data={"lyrics_complete": True, "audio_complete": audio_complete},
    )
    jm = MagicMock()
    jm.get_job.return_value = job
    jm.transition_to_state.return_value = True
    style_config = MagicMock(include_title_screen=False, include_end_screen=False)

    async def load_style(*args, **kwargs):
        return style_config

    async def noop(*args, **kwargs):
        return {}

    async def auto(*args, **kwargs):
        return auto_result

    pre_apply = MagicMock(side_effect=noop)
    with patch.object(sw, "JobManager", return_value=jm), \
         patch.object(sw, "StorageService"), \
         patch.object(sw, "get_settings"), \
         patch.object(sw, "create_job_logger"), \
         patch.object(sw, "setup_job_logging"), \
         patch.object(sw, "_validate_prerequisites", return_value=True), \
         patch("backend.services.stems_restore.maybe_start_stems_restore", return_value="not_needed"), \
         patch.object(sw, "load_style_config", side_effect=load_style), \
         patch.object(sw, "_create_video_generator"), \
         patch.object(sw, "_upload_screens", side_effect=noop), \
         patch.object(sw, "_apply_countdown_padding_if_needed", side_effect=noop), \
         patch.object(sw, "_transcode_review_audio", side_effect=noop), \
         patch("backend.services.auto_approval.executor.maybe_auto_complete_review", side_effect=auto), \
         patch("backend.services.auto_approval.pre_apply.ensure_and_pre_apply", pre_apply), \
         patch("backend.services.review_assets.prepare_review_assets", side_effect=noop):
        assert await sw.generate_screens("j1") is True, jm.mark_job_failed.call_args
    return jm, pre_apply


def _review_transitions(jm):
    return [c for c in jm.transition_to_state.call_args_list
            if c.kwargs.get("new_status") == JobStatus.AWAITING_REVIEW]


@pytest.mark.asyncio
async def test_held_job_does_not_open_review():
    jm, pre_apply = await _run_screens(
        {"outcome": "review", "hold_for_audio": True}, audio_complete=False)
    jm.park_review_for_auto_check.assert_called_once_with("j1")
    assert _review_transitions(jm) == []
    pre_apply.assert_not_called()


@pytest.mark.asyncio
async def test_job_needing_human_review_is_also_held_until_stems_land():
    jm, pre_apply = await _run_screens(
        {"outcome": "review", "hold_for_audio": False, "ai_suggestions_available": True},
        audio_complete=False)
    jm.park_review_for_auto_check.assert_called_once_with("j1")
    assert _review_transitions(jm) == []
    pre_apply.assert_not_called()


@pytest.mark.asyncio
async def test_job_with_stems_ready_opens_review():
    jm, pre_apply = await _run_screens({"outcome": "review"}, audio_complete=True)
    jm.park_review_for_auto_check.assert_not_called()
    assert len(_review_transitions(jm)) == 1
    pre_apply.assert_called_once()
