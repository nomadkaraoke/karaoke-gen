"""Unit tests for the 24h ownership handoff worker."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.models.job import JobStatus
from backend.models.song_request import SongRequest
from backend.workers import community_handoff as ch

OLD = "2020-01-01T00:00:00+00:00"


def _req(**over):
    base = dict(
        id="req1", artist="A", title="T", artist_raw="A", title_raw="T",
        dedupe_key="a|t", submitted_by="owner@x.com", source="human",
        status="in_progress", vote_count=3, job_id="j1",
        owner_email="owner@x.com", owner_assigned_at=OLD,
        attempted_owners=["owner@x.com"], handoff_attempts=1,
    )
    base.update(over)
    return SongRequest(**base)


def _settings():
    return SimpleNamespace(community_handoff_hours=24, community_handoff_max_attempts=5)


def _patched(svc, job_status=JobStatus.AWAITING_REVIEW):
    jm = MagicMock()
    jm.get_job.return_value = SimpleNamespace(status=job_status, job_id="j1")
    email = MagicMock()
    user = MagicMock()
    user.get_user.return_value = SimpleNamespace(locale="en")
    return patch.multiple(
        ch,
        get_song_request_service=MagicMock(return_value=svc),
        JobManager=MagicMock(return_value=jm),
        get_email_service=MagicMock(return_value=email),
        get_user_service=MagicMock(return_value=user),
        get_settings=MagicMock(return_value=_settings()),
    ), jm, email


@pytest.mark.asyncio
async def test_reassigns_to_next_untried_voter():
    svc = MagicMock()
    svc.list_in_progress.return_value = [_req()]
    svc.list_upvoters.return_value = ["owner@x.com", "v2@x.com", "v3@x.com"]
    ctx, jm, email = _patched(svc)
    with ctx:
        result = await ch.process_community_handoffs()
    assert result["handed_off"] == 1 and result["parked"] == 0
    jm.update_job.assert_called_once_with("j1", {"user_email": "v2@x.com"})
    svc.assign_owner.assert_called_once_with("req1", "v2@x.com")
    email.send_review_reminder.assert_called_once()
    svc.mark_stalled.assert_not_called()


@pytest.mark.asyncio
async def test_parks_when_cap_reached():
    svc = MagicMock()
    svc.list_in_progress.return_value = [_req(handoff_attempts=5)]
    svc.list_upvoters.return_value = ["owner@x.com", "v2@x.com", "v3@x.com", "v4@x.com", "v5@x.com", "v6@x.com"]
    ctx, jm, email = _patched(svc)
    with ctx:
        result = await ch.process_community_handoffs()
    assert result["parked"] == 1 and result["handed_off"] == 0
    svc.mark_stalled.assert_called_once_with("req1")
    jm.update_job.assert_not_called()


@pytest.mark.asyncio
async def test_parks_when_no_untried_voters():
    svc = MagicMock()
    svc.list_in_progress.return_value = [_req(attempted_owners=["owner@x.com", "v2@x.com"])]
    svc.list_upvoters.return_value = ["owner@x.com", "v2@x.com"]
    ctx, jm, email = _patched(svc)
    with ctx:
        result = await ch.process_community_handoffs()
    assert result["parked"] == 1
    svc.mark_stalled.assert_called_once_with("req1")


@pytest.mark.asyncio
async def test_skips_when_owner_clock_not_expired():
    svc = MagicMock()
    fresh = datetime.now(timezone.utc).isoformat()
    svc.list_in_progress.return_value = [_req(owner_assigned_at=fresh)]
    svc.list_upvoters.return_value = ["owner@x.com", "v2@x.com"]
    ctx, jm, email = _patched(svc)
    with ctx:
        result = await ch.process_community_handoffs()
    assert result["handed_off"] == 0 and result["parked"] == 0
    jm.update_job.assert_not_called()
    svc.mark_stalled.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_job_not_owner_blocked():
    # Job is still processing (not the owner's fault) → no handoff.
    svc = MagicMock()
    svc.list_in_progress.return_value = [_req()]
    svc.list_upvoters.return_value = ["owner@x.com", "v2@x.com"]
    ctx, jm, email = _patched(svc, job_status=JobStatus.SEPARATING_STAGE1)
    with ctx:
        result = await ch.process_community_handoffs()
    assert result["checked"] == 0 and result["handed_off"] == 0
    jm.update_job.assert_not_called()
