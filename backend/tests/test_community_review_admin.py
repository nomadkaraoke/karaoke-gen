"""Unit tests for the admin community-review action endpoint (make/reject/keep)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.api.routes.admin import (
    CommunityReviewActionBody,
    action_community_review,
    list_community_requests,
)
from backend.models.song_request import SongRequest


def _req(**over):
    base = dict(
        id="req1", artist="A", title="T", artist_raw="A", title_raw="T",
        dedupe_key="a|t", submitted_by="owner@x.com", source="human",
        status="open", vote_count=3, review_state="pending",
        community_versions={"best_youtube_url": "https://youtu.be/x", "tracks": []},
    )
    base.update(over)
    return SongRequest(**base)


def _settings():
    return SimpleNamespace(community_review_snooze_days=30)


def _patches(svc, notifier=None, provision=None):
    ps = [
        patch("backend.services.song_request_service.get_song_request_service", MagicMock(return_value=svc)),
        patch("backend.config.get_settings", MagicMock(return_value=_settings())),
    ]
    if notifier is not None:
        ps.append(patch("backend.services.job_notification_service.get_job_notification_service",
                        MagicMock(return_value=notifier)))
    if provision is not None:
        ps.append(patch("backend.workers.community_daily_pick._provision_and_start", provision))
    return ps


async def _call(ps, request_id, action):
    for p in ps:
        p.start()
    try:
        return await action_community_review(
            request_id, CommunityReviewActionBody(action=action), auth_data=("admin", None, 0)
        )
    finally:
        for p in reversed(ps):
            p.stop()


@pytest.mark.asyncio
async def test_keep_snoozes():
    svc = MagicMock()
    svc.get_request.return_value = _req()
    res = await _call(_patches(svc), "req1", "keep")
    assert res.status == "snoozed"
    svc.snooze_review.assert_called_once()
    assert svc.snooze_review.call_args.args[0] == "req1"


@pytest.mark.asyncio
async def test_reject_notifies_upvoters():
    svc = MagicMock()
    svc.get_request.return_value = _req()
    svc.list_upvoters.return_value = ["v1@x.com", "v2@x.com"]
    notifier = MagicMock()
    notifier.send_community_existing_version_email = AsyncMock(return_value=True)
    res = await _call(_patches(svc, notifier=notifier), "req1", "reject")
    assert res.status == "rejected"
    svc.reject_request.assert_called_once_with("req1")
    urls = {c.kwargs["youtube_url"] for c in notifier.send_community_existing_version_email.call_args_list}
    assert urls == {"https://youtu.be/x"}
    assert notifier.send_community_existing_version_email.await_count == 2


@pytest.mark.asyncio
async def test_make_provisions_job():
    svc = MagicMock()
    svc.get_request.return_value = _req()
    provision = AsyncMock(return_value={"status": "made", "job_id": "j9"})
    res = await _call(_patches(svc, provision=provision), "req1", "make")
    assert res.status == "made"
    svc.clear_review.assert_called_once_with("req1")
    provision.assert_awaited_once()


@pytest.mark.asyncio
async def test_not_pending_returns_404():
    svc = MagicMock()
    svc.get_request.return_value = _req(review_state=None)
    with pytest.raises(HTTPException) as exc:
        await _call(_patches(svc), "req1", "keep")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_make_failure_raises_500():
    svc = MagicMock()
    svc.get_request.return_value = _req()
    provision = AsyncMock(return_value={"status": "error", "step": "grant_credit", "message": "denied"})
    with pytest.raises(HTTPException) as exc:
        await _call(_patches(svc, provision=provision), "req1", "make")
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_list_community_requests_maps_all_statuses():
    svc = MagicMock()
    published = _req(
        id="pub", status="published", review_state=None,
        job_id="j-abc12345", youtube_url="https://youtu.be/pub",
        owner_email="voter@x.com",
    )
    open_req = _req(id="op", status="open", review_state=None, job_id=None, youtube_url=None)
    svc.list_all.return_value = [published, open_req]
    with patch("backend.services.song_request_service.get_song_request_service",
               MagicMock(return_value=svc)):
        res = await list_community_requests(auth_data=("admin", None, 0))
    assert [r.id for r in res.requests] == ["pub", "op"]
    pub = res.requests[0]
    assert pub.status == "published"
    assert pub.youtube_url == "https://youtu.be/pub"
    assert pub.owner_email == "voter@x.com"
    assert pub.created_at is not None  # datetime serialized to iso string
    # Open request carries no job/video links.
    assert res.requests[1].job_id is None and res.requests[1].youtube_url is None
