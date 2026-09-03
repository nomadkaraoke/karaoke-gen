"""Unit tests for the daily free-community-track picker orchestration.

Mocks the service/user/job layers so we exercise run_daily_pick's control flow:
kill-switch shadowing, empty board, day-lock idempotency, credit grant, and the
grant/job-create idempotency guards. The Firestore-level pieces (claim_day, the
transactional transitions) are covered by the emulator tests.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.song_request import DailyCommunityPick, SongRequest
from backend.workers import community_daily_pick as cdp


def _request(**over):
    base = dict(
        id="req1", artist="A", title="T", artist_raw="A", title_raw="T",
        dedupe_key="a|t", submitted_by="owner@x.com", source="human",
        status="open", vote_count=5,
    )
    base.update(over)
    return SongRequest(**base)


def _make_service(pick=None, lock=None, claimed_new=True, get_request=None):
    svc = MagicMock()
    svc.claim_day.return_value = (lock or DailyCommunityPick(date="2026-09-03"), claimed_new)
    svc.pick_eligible.return_value = pick
    svc.get_request.side_effect = (lambda rid: get_request) if get_request is not None else (lambda rid: pick)
    return svc


def _settings(enabled=True):
    return SimpleNamespace(
        community_daily_pick_enabled=enabled,
        default_enable_cdg=True, default_enable_txt=True, default_brand_prefix="NOMAD",
        default_enable_youtube_upload=True, default_youtube_description="desc",
        default_discord_webhook_url=None, default_dropbox_path=None,
        default_gdrive_folder_id=None,
    )


@pytest.mark.asyncio
async def test_kill_switch_off_shadows_no_side_effects():
    svc = _make_service(pick=_request())
    user = MagicMock()
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=False)), \
         patch.object(cdp, "get_user_service", return_value=user):
        result = await cdp.run_daily_pick(dry_run=False)
    assert result["status"] == "shadow"
    assert result["reason"] == "kill_switch_off"
    user.add_credits.assert_not_called()
    svc.transition_status.assert_not_called()
    # Records a skipped lock for observability.
    assert any(c.kwargs.get("phase") == "skipped" for c in svc.update_lock.call_args_list)


@pytest.mark.asyncio
async def test_dry_run_shadows_even_when_enabled():
    svc = _make_service(pick=_request())
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=True)), \
         patch.object(cdp, "get_user_service", return_value=MagicMock()):
        result = await cdp.run_daily_pick(dry_run=True)
    assert result["status"] == "shadow" and result["reason"] == "dry_run"


@pytest.mark.asyncio
async def test_empty_board_makes_nothing():
    svc = _make_service(pick=None)
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=True)), \
         patch.object(cdp, "get_user_service", return_value=MagicMock()):
        result = await cdp.run_daily_pick()
    assert result["status"] == "empty"
    assert any(c.kwargs.get("phase") == "empty" for c in svc.update_lock.call_args_list)


@pytest.mark.asyncio
async def test_already_resolved_day_is_noop():
    lock = DailyCommunityPick(date="2026-09-03", phase="done", request_id="req1", job_id="j1")
    svc = _make_service(lock=lock, claimed_new=False)
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=True)), \
         patch.object(cdp, "get_user_service", return_value=MagicMock()):
        result = await cdp.run_daily_pick()
    assert result["status"] == "already_done"
    svc.pick_eligible.assert_not_called()


@pytest.mark.asyncio
async def test_make_success_grants_credit_and_creates_job():
    req = _request()
    svc = _make_service(pick=req, get_request=req)
    user = MagicMock()
    user.add_credits.return_value = (True, 5, "ok")
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=True)), \
         patch.object(cdp, "get_user_service", return_value=user), \
         patch.object(cdp, "_create_community_job", return_value="job123") as mk, \
         patch.object(cdp, "_search_and_download", new=AsyncMock(return_value=True)):
        result = await cdp.run_daily_pick()
    assert result["status"] == "made"
    assert result["job_id"] == "job123"
    user.add_credits.assert_called_once()
    svc.mark_credit_granted.assert_called_once_with("req1")
    svc.set_job_id.assert_called_once_with("req1", "job123")
    svc.assign_owner.assert_called_once_with("req1", "owner@x.com")
    mk.assert_called_once()
    assert any(c.kwargs.get("phase") == "done" for c in svc.update_lock.call_args_list)


@pytest.mark.asyncio
async def test_credit_grant_is_idempotent_on_resume():
    # Resume: request already flagged as granted → don't grant again.
    req = _request(community_credit_granted=True, status="queued")
    svc = _make_service(pick=req, get_request=req)
    user = MagicMock()
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=True)), \
         patch.object(cdp, "get_user_service", return_value=user), \
         patch.object(cdp, "_create_community_job", return_value="job123"), \
         patch.object(cdp, "_search_and_download", new=AsyncMock(return_value=True)):
        await cdp.run_daily_pick()
    user.add_credits.assert_not_called()


@pytest.mark.asyncio
async def test_existing_job_not_recreated():
    req = _request(community_credit_granted=True, job_id="existing", status="in_progress")
    svc = _make_service(pick=req, get_request=req)
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=True)), \
         patch.object(cdp, "get_user_service", return_value=MagicMock()), \
         patch.object(cdp, "_create_community_job", return_value="SHOULD_NOT") as mk, \
         patch.object(cdp, "_search_and_download", new=AsyncMock(return_value=True)):
        result = await cdp.run_daily_pick()
    mk.assert_not_called()
    assert result["job_id"] == "existing"


@pytest.mark.asyncio
async def test_credit_grant_failure_aborts_before_job():
    req = _request()
    svc = _make_service(pick=req, get_request=req)
    user = MagicMock()
    user.add_credits.return_value = (False, 0, "denied")
    with patch.object(cdp, "get_song_request_service", return_value=svc), \
         patch.object(cdp, "get_settings", return_value=_settings(enabled=True)), \
         patch.object(cdp, "get_user_service", return_value=user), \
         patch.object(cdp, "_create_community_job", return_value="job123") as mk:
        result = await cdp.run_daily_pick()
    assert result["status"] == "error" and result["step"] == "grant_credit"
    mk.assert_not_called()
