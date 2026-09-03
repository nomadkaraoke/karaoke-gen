"""Unit tests for the requests-board publish fan-out in the YouTube queue processor."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.song_request import SongRequest
from backend.workers import youtube_queue_processor as yqp


def _req(**over):
    base = dict(
        id="req1", artist="A", title="T", artist_raw="A", title_raw="T",
        dedupe_key="a|t", submitted_by="owner@x.com", source="human",
        status="in_progress", vote_count=3, job_id="j1", owner_email="owner@x.com",
    )
    base.update(over)
    return SongRequest(**base)


def _ctx(svc, notifier):
    return patch.multiple(
        "backend.services.song_request_service",
        get_song_request_service=MagicMock(return_value=svc),
    ), patch(
        "backend.services.job_notification_service.get_job_notification_service",
        MagicMock(return_value=notifier),
    )


@pytest.mark.asyncio
async def test_non_community_job_is_ignored():
    svc = MagicMock()
    svc.get_by_job_id.return_value = None
    notifier = MagicMock()
    p1, p2 = _ctx(svc, notifier)
    with p1, p2:
        await yqp._notify_community_voters("j1", {}, "https://youtu.be/x")
    svc.mark_published.assert_not_called()


@pytest.mark.asyncio
async def test_publishes_and_fans_out_excluding_owner():
    svc = MagicMock()
    svc.get_by_job_id.return_value = _req(voters_notified=False)
    svc.list_upvoters.return_value = ["owner@x.com", "v2@x.com", "v3@x.com"]
    notifier = MagicMock()
    notifier.send_community_track_live_email = AsyncMock(return_value=True)
    p1, p2 = _ctx(svc, notifier)
    with p1, p2:
        await yqp._notify_community_voters("j1", {}, "https://youtu.be/x")
    svc.mark_published.assert_called_once_with("req1", "https://youtu.be/x")
    # Owner excluded (already got the completion email); two voters emailed.
    emailed = {c.kwargs["to_email"] for c in notifier.send_community_track_live_email.call_args_list}
    assert emailed == {"v2@x.com", "v3@x.com"}
    # Each success is recorded immediately (crash-safe), then the all-done flag.
    assert [c.args for c in svc.add_notified_voters.call_args_list] == [
        ("req1", ["v2@x.com"]), ("req1", ["v3@x.com"]),
    ]
    svc.mark_voters_notified.assert_called_once_with("req1")


@pytest.mark.asyncio
async def test_skips_already_notified_voters_and_retries_failures():
    # v2 already notified; v3 fails this run → not marked fully-notified, v3 retried later.
    svc = MagicMock()
    svc.get_by_job_id.return_value = _req(voters_notified=False, notified_voters=["v2@x.com"])
    svc.list_upvoters.return_value = ["owner@x.com", "v2@x.com", "v3@x.com"]
    notifier = MagicMock()
    notifier.send_community_track_live_email = AsyncMock(return_value=False)  # v3 send fails
    p1, p2 = _ctx(svc, notifier)
    with p1, p2:
        await yqp._notify_community_voters("j1", {}, "https://youtu.be/x")
    # Only the un-notified voter (v3) is attempted; v2 skipped.
    emailed = {c.kwargs["to_email"] for c in notifier.send_community_track_live_email.call_args_list}
    assert emailed == {"v3@x.com"}
    svc.add_notified_voters.assert_not_called()  # v3 send failed → nothing recorded
    svc.mark_voters_notified.assert_not_called()  # partial → leave flag unset for retry


@pytest.mark.asyncio
async def test_already_notified_does_not_resend():
    svc = MagicMock()
    svc.get_by_job_id.return_value = _req(voters_notified=True)
    notifier = MagicMock()
    notifier.send_community_track_live_email = AsyncMock(return_value=True)
    p1, p2 = _ctx(svc, notifier)
    with p1, p2:
        await yqp._notify_community_voters("j1", {}, "https://youtu.be/x")
    svc.mark_published.assert_called_once()  # republish (mark) is safe/idempotent
    notifier.send_community_track_live_email.assert_not_called()
    svc.mark_voters_notified.assert_not_called()
