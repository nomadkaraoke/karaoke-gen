"""Unit tests for the daily free-community-track picker orchestration.

Mocks the service/user/job/KaraokeNerds layers so we exercise run_daily_pick's
control flow: kill-switch shadowing, empty board, day-lock idempotency, credit
grant guards, and the existing-community-version candidate loop (flag dups →
make the first clean one). Firestore-level pieces are covered by emulator tests.
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


def _make_service(candidates=None, lock=None, claimed_new=True, registry=None):
    """Build a mock SongRequestService.

    candidates: what list_pick_candidates returns (ranked).
    registry:   id -> SongRequest for get_request lookups (defaults to candidates).
    """
    candidates = candidates or []
    reg = dict(registry) if registry else {r.id: r for r in candidates}
    svc = MagicMock()
    svc.claim_day.return_value = (lock or DailyCommunityPick(date="2026-09-03"), claimed_new)
    svc.list_pick_candidates.return_value = candidates
    svc.get_request.side_effect = lambda rid: reg.get(rid)
    svc.set_review_pending.return_value = True  # newly flagged
    return svc


def _settings(enabled=True):
    return SimpleNamespace(
        community_daily_pick_enabled=enabled,
        default_enable_cdg=True, default_enable_txt=True, default_brand_prefix="NOMAD",
        default_enable_youtube_upload=True, default_youtube_description="desc",
        default_discord_webhook_url=None, default_dropbox_path=None,
        default_gdrive_folder_id=None,
    )


def _community(has_map=None):
    """AsyncMock for check_community_versions. has_map: artist->bool has_community."""
    has_map = has_map or {}

    async def _check(artist, title):
        if has_map.get(artist):
            return {"has_community": True, "best_youtube_url": "https://youtu.be/x",
                    "songs": [{"community_tracks": [{"brand_name": "Foo", "youtube_url": "https://youtu.be/x"}]}]}
        return {"has_community": False, "songs": [], "best_youtube_url": None}

    return AsyncMock(side_effect=_check)


def _run(svc, settings, community=None, user=None, made=True):
    """Context-managed patches; returns the coroutine to await."""
    community = community or _community()
    user = user or MagicMock()
    if user.add_credits.return_value is None or not isinstance(user.add_credits.return_value, tuple):
        user.add_credits.return_value = (True, 5, "ok")
    stack = [
        patch.object(cdp, "get_song_request_service", return_value=svc),
        patch.object(cdp, "get_settings", return_value=settings),
        patch.object(cdp, "get_user_service", return_value=user),
        patch("backend.services.karaokenerds_service.check_community_versions", community),
        patch.object(cdp, "_email_admin_flagged", MagicMock()),
    ]
    if made:
        stack.append(patch.object(cdp, "_create_community_job", return_value="job123"))
        stack.append(patch.object(cdp, "_search_and_download", new=AsyncMock(return_value=True)))
    return stack


async def _exec(stack, dry_run=False):
    for p in stack:
        p.start()
    try:
        return await cdp.run_daily_pick(dry_run=dry_run)
    finally:
        for p in reversed(stack):
            p.stop()


@pytest.mark.asyncio
async def test_kill_switch_off_shadows_no_side_effects():
    svc = _make_service(candidates=[_request()])
    user = MagicMock()
    result = await _exec(_run(svc, _settings(enabled=False), user=user, made=False))
    assert result["status"] == "shadow" and result["reason"] == "kill_switch_off"
    user.add_credits.assert_not_called()
    svc.claim_day.assert_not_called()
    svc.set_review_pending.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_shadows_even_when_enabled():
    svc = _make_service(candidates=[_request()])
    result = await _exec(_run(svc, _settings(enabled=True), made=False), dry_run=True)
    assert result["status"] == "shadow" and result["reason"] == "dry_run"
    assert result["request_id"] == "req1"


@pytest.mark.asyncio
async def test_empty_board_makes_nothing():
    svc = _make_service(candidates=[])
    result = await _exec(_run(svc, _settings(enabled=True), made=False))
    assert result["status"] == "empty"


@pytest.mark.asyncio
async def test_already_resolved_day_is_noop():
    lock = DailyCommunityPick(date="2026-09-03", phase="done", request_id="req1", job_id="j1")
    svc = _make_service(candidates=[_request()], lock=lock, claimed_new=False)
    result = await _exec(_run(svc, _settings(enabled=True), made=False))
    assert result["status"] == "already_done"
    svc.list_pick_candidates.assert_not_called()


@pytest.mark.asyncio
async def test_make_success_grants_credit_and_creates_job():
    req = _request()
    svc = _make_service(candidates=[req])
    user = MagicMock()
    user.add_credits.return_value = (True, 5, "ok")
    result = await _exec(_run(svc, _settings(enabled=True), user=user))
    assert result["status"] == "made" and result["job_id"] == "job123"
    user.add_credits.assert_called_once()
    svc.mark_credit_granted.assert_called_once_with("req1")
    svc.set_job_id.assert_called_once_with("req1", "job123")
    svc.assign_owner.assert_called_once_with("req1", "owner@x.com")


@pytest.mark.asyncio
async def test_flags_dup_and_makes_next_clean():
    dup = _request(id="dup", artist="DupArtist", title="Has Version", vote_count=9)
    clean = _request(id="clean", artist="CleanArtist", title="No Version", vote_count=3)
    svc = _make_service(candidates=[dup, clean])
    community = _community({"DupArtist": True})  # only the dup has a community version
    result = await _exec(_run(svc, _settings(enabled=True), community=community))
    assert result["status"] == "made" and result["request_id"] == "clean"
    # The dup was flagged pending; the clean one was made.
    svc.set_review_pending.assert_called_once()
    assert svc.set_review_pending.call_args.args[0] == "dup"
    assert result["flagged"] == ["dup"]


@pytest.mark.asyncio
async def test_all_dups_makes_nothing_but_flags():
    a = _request(id="a", artist="A1", title="t", vote_count=9)
    b = _request(id="b", artist="B1", title="t", vote_count=3)
    svc = _make_service(candidates=[a, b])
    community = _community({"A1": True, "B1": True})
    result = await _exec(_run(svc, _settings(enabled=True), community=community, made=False))
    assert result["status"] == "empty"
    assert set(result["flagged"]) == {"a", "b"}
    assert svc.set_review_pending.call_count == 2


@pytest.mark.asyncio
async def test_credit_grant_is_idempotent_on_resume():
    req = _request(community_credit_granted=True, status="queued")
    svc = _make_service(candidates=[req])
    user = MagicMock()
    await _exec(_run(svc, _settings(enabled=True), user=user))
    user.add_credits.assert_not_called()


@pytest.mark.asyncio
async def test_resume_finishes_locked_request_without_recheck():
    # Lock already recorded request_id → resume making it, skip candidate loop.
    req = _request(id="locked", community_credit_granted=True, job_id="existing", status="in_progress")
    lock = DailyCommunityPick(date="2026-09-03", phase="job_created", request_id="locked")
    svc = _make_service(candidates=[], lock=lock, claimed_new=False, registry={"locked": req})
    community = _community()
    result = await _exec(_run(svc, _settings(enabled=True), community=community))
    assert result["status"] == "made" and result["job_id"] == "existing"
    svc.list_pick_candidates.assert_not_called()
    community.assert_not_awaited()


@pytest.mark.asyncio
async def test_credit_grant_failure_aborts_before_job():
    req = _request()
    svc = _make_service(candidates=[req])
    user = MagicMock()
    user.add_credits.return_value = (False, 0, "denied")
    with patch.object(cdp, "_create_community_job", return_value="job123") as mk:
        stack = _run(svc, _settings(enabled=True), user=user, made=False)
        result = await _exec(stack)
    assert result["status"] == "error" and result["step"] == "grant_credit"
    mk.assert_not_called()
