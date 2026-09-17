"""Unit tests for the requests-board publish fan-out (backend.services.community_publish).

The fan-out fires from BOTH publish paths (youtube_queue_processor and the direct
video_worker distribution), so it lives in a shared module and is tested here once.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.song_request import SongRequest
from backend.services import community_publish as cp


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
        result = await cp.notify_community_publish("j1", "https://youtu.be/x")
    assert result is None
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
        result = await cp.notify_community_publish("j1", "https://youtu.be/x")
    assert result == "req1"
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
        await cp.notify_community_publish("j1", "https://youtu.be/x")
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
        await cp.notify_community_publish("j1", "https://youtu.be/x")
    svc.mark_published.assert_called_once()  # republish (mark) is safe/idempotent
    notifier.send_community_track_live_email.assert_not_called()
    svc.mark_voters_notified.assert_not_called()


# --- youtube-url resolution ---------------------------------------------------

def test_youtube_url_prefers_state_data():
    job = MagicMock()
    job.state_data = {"youtube_url": "https://youtu.be/state"}
    job.processing_metadata = {"distribution": {"youtube_video_url": "https://youtu.be/dist"}}
    assert cp._youtube_url_for_job(job) == "https://youtu.be/state"


def test_youtube_url_falls_back_to_distribution_metadata():
    job = MagicMock()
    job.state_data = {}
    job.processing_metadata = {"distribution": {"youtube_video_url": "https://youtu.be/dist"}}
    assert cp._youtube_url_for_job(job) == "https://youtu.be/dist"


def test_youtube_url_none_when_unpublished():
    job = MagicMock()
    job.state_data = {}
    job.processing_metadata = {"distribution": {}}
    assert cp._youtube_url_for_job(job) is None
    assert cp._youtube_url_for_job(None) is None


# --- reconcile / backfill -----------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_publishes_live_in_progress_requests():
    svc = MagicMock()
    live = _req(id="r-live", job_id="j-live", voters_notified=True)
    unpub = _req(id="r-unpub", job_id="j-unpub", voters_notified=True)
    nojob = _req(id="r-nojob", job_id=None)
    svc.list_in_progress.return_value = [live, unpub, nojob]
    # get_by_job_id (used inside notify_community_publish) resolves the live request.
    svc.get_by_job_id.side_effect = lambda jid: {"j-live": live}.get(jid)

    live_job = MagicMock(state_data={"youtube_url": "https://youtu.be/live"}, processing_metadata={})
    unpub_job = MagicMock(state_data={}, processing_metadata={"distribution": {}})

    jm = MagicMock()
    jm.get_job.side_effect = lambda jid: {"j-live": live_job, "j-unpub": unpub_job}.get(jid)

    with patch.multiple(
        "backend.services.song_request_service",
        get_song_request_service=MagicMock(return_value=svc),
    ), patch("backend.services.job_manager.JobManager", MagicMock(return_value=jm)):
        result = await cp.reconcile_community_publishes()

    assert result["scanned"] == 3
    published_ids = {p["request_id"] for p in result["published"]}
    assert published_ids == {"r-live"}
    svc.mark_published.assert_called_once_with("r-live", "https://youtu.be/live")
