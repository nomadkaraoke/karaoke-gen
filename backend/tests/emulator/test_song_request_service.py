"""Emulator-backed tests for SongRequestService — exercises REAL Firestore transactions.

Covers the vote arithmetic (create / move / flip / toggle-off), the one-vote-per-day
rule, submit dedupe + auto-upvote, and ranking. Skipped unless the Firestore emulator
is running (see emulator/conftest.py).
"""
import uuid

import pytest
from unittest.mock import AsyncMock, patch

from backend.models.song_request import SongRequest
from backend.services.match_judge.verdict import MatchVerdict, KIND_COSMETIC
from backend.services import song_request_service as srs
from backend.services.song_request_service import SongRequestService


@pytest.fixture
def service(monkeypatch):
    # Isolate each test run in its own collections so parallel/rerun state can't bleed.
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setattr(srs, "REQUESTS_COLLECTION", f"test_song_requests_{suffix}")
    monkeypatch.setattr(srs, "VOTES_COLLECTION", f"test_song_request_votes_{suffix}")
    monkeypatch.setattr(srs, "DAILY_PICK_COLLECTION", f"test_daily_pick_{suffix}")
    return SongRequestService()


def _cosmetic(artist, title):
    return MatchVerdict(
        kind=KIND_COSMETIC, confident=True,
        canonical_artist=artist, canonical_title=title, engine="catalog",
    )


async def _submit(service, email, artist, title, canon_artist=None, canon_title=None):
    verdict = _cosmetic(canon_artist or artist, canon_title or title)
    with patch("backend.services.match_judge.service.judge_match", new=AsyncMock(return_value=verdict)):
        return await service.submit_request(email, artist, title)


@pytest.mark.asyncio
async def test_submit_creates_with_auto_upvote(service):
    req, existed, ca, ct = await _submit(service, "u1@x.com", "beatles", "hey jude", "The Beatles", "Hey Jude")
    assert existed is False
    assert (req.artist, req.title) == ("The Beatles", "Hey Jude")
    assert req.vote_count == 1  # submitter's endorsement
    daily = service.get_daily_vote("u1@x.com")
    assert daily and daily.request_id == req.id and daily.value == 1


@pytest.mark.asyncio
async def test_resubmit_same_song_dedupes_and_upvotes(service):
    req1, _, _, _ = await _submit(service, "u1@x.com", "The Beatles", "Hey Jude")
    # Different user submits the same song (with messier input that canonicalizes equal)
    req2, existed, _, _ = await _submit(service, "u2@x.com", "  beatles ", "hey  jude", "The Beatles", "Hey Jude")
    assert existed is True
    assert req2.id == req1.id
    assert req2.vote_count == 2  # u1 (auto) + u2 (dedupe upvote)


@pytest.mark.asyncio
async def test_resubmit_own_song_is_idempotent_not_toggle_off(service):
    # Submitting a song you already up-voted today must NOT toggle the vote off.
    req1, _, _, _ = await _submit(service, "u1@x.com", "The Beatles", "Hey Jude")
    assert req1.vote_count == 1
    req2, existed, _, _ = await _submit(service, "u1@x.com", "The Beatles", "Hey Jude")
    assert existed is True
    assert req2.id == req1.id
    assert req2.vote_count == 1  # still 1 — the up-vote was preserved, not removed
    assert service.get_daily_vote("u1@x.com").request_id == req1.id


@pytest.mark.asyncio
async def test_one_vote_per_day_moves_not_adds(service):
    a, _, _, _ = await _submit(service, "owner@x.com", "Artist A", "Song A")
    b, _, _, _ = await _submit(service, "owner2@x.com", "Artist B", "Song B")
    voter = "voter@x.com"
    service.cast_vote(voter, a.id, "up")
    assert service.get_request(a.id).vote_count == 2  # owner auto + voter
    # Same voter votes on B the same day → their single daily vote MOVES off A onto B
    service.cast_vote(voter, b.id, "up")
    assert service.get_request(a.id).vote_count == 1  # back to just owner
    assert service.get_request(b.id).vote_count == 2  # owner2 auto + voter
    daily = service.get_daily_vote(voter)
    assert daily.request_id == b.id


@pytest.mark.asyncio
async def test_toggle_off_frees_daily_vote(service):
    a, _, _, _ = await _submit(service, "owner@x.com", "Artist A", "Song A")
    voter = "voter@x.com"
    service.cast_vote(voter, a.id, "up")
    assert service.get_request(a.id).vote_count == 2
    service.cast_vote(voter, a.id, "up")  # same request + direction → toggle off
    assert service.get_request(a.id).vote_count == 1
    assert service.get_daily_vote(voter) is None


@pytest.mark.asyncio
async def test_flip_direction_same_request(service):
    a, _, _, _ = await _submit(service, "owner@x.com", "Artist A", "Song A")
    voter = "voter@x.com"
    service.cast_vote(voter, a.id, "up")
    assert service.get_request(a.id).vote_count == 2  # owner(+1) + up(+1)
    service.cast_vote(voter, a.id, "down")  # flip up→down: net change -2
    assert service.get_request(a.id).vote_count == 0  # owner(+1) + down(-1)
    daily = service.get_daily_vote(voter)
    assert daily.value == -1


@pytest.mark.asyncio
async def test_ranking_orders_by_votes(service):
    low, _, _, _ = await _submit(service, "o1@x.com", "Low", "Song")
    high, _, _, _ = await _submit(service, "o2@x.com", "High", "Song")
    # Push "high" ahead with extra votes from distinct voters
    service.cast_vote("v1@x.com", high.id, "up")
    service.cast_vote("v2@x.com", high.id, "up")
    active = service.list_active()
    ids = [r.id for r in active]
    assert ids.index(high.id) < ids.index(low.id)


@pytest.mark.asyncio
async def test_vote_on_missing_request_raises(service):
    from backend.services.song_request_service import RequestNotFound
    with pytest.raises(RequestNotFound):
        service.cast_vote("v@x.com", "does-not-exist", "up")


# ---------------------------------------------------------------------------
# Phase 2 — daily picker, ownership handoff, publish fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_day_is_single_winner(service):
    lock1, new1 = service.claim_day("2026-09-03")
    lock2, new2 = service.claim_day("2026-09-03")
    assert new1 is True and new2 is False
    assert lock1.date == lock2.date == "2026-09-03"
    # Different day is independently claimable.
    _, new3 = service.claim_day("2026-09-04")
    assert new3 is True


@pytest.mark.asyncio
async def test_update_and_get_lock(service):
    service.claim_day("2026-09-03")
    service.update_lock("2026-09-03", phase="done", request_id="req1", job_id="j1")
    lock = service.get_lock("2026-09-03")
    assert lock.phase == "done" and lock.request_id == "req1" and lock.job_id == "j1"


@pytest.mark.asyncio
async def test_pick_eligible_skips_negative_and_ranks(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    b, _, _, _ = await _submit(service, "o2@x.com", "Artist B", "Song B")
    # Give B more votes → B should rank first.
    service.cast_vote("v1@x.com", b.id, "up")
    assert service.pick_eligible().id == b.id
    # Drive A negative → still eligible-skipping keeps B; then make B negative too.
    service.cast_vote("v2@x.com", a.id, "down")  # A: owner(+1) + down(-1) = 0
    service.cast_vote("v3@x.com", a.id, "down")  # A: -1 now
    assert service.pick_eligible().id == b.id
    assert service.get_request(a.id).vote_count < 0


@pytest.mark.asyncio
async def test_pick_eligible_none_when_all_negative(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    service.cast_vote("v1@x.com", a.id, "down")  # 0
    service.cast_vote("v2@x.com", a.id, "down")  # -1
    assert service.get_request(a.id).vote_count < 0
    assert service.pick_eligible() is None


@pytest.mark.asyncio
async def test_transition_status_guards_on_current(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    assert service.transition_status(a.id, "open", "queued") is True
    assert service.get_request(a.id).status == "queued"
    # Wrong expected_from → no-op.
    assert service.transition_status(a.id, "open", "in_progress") is False
    assert service.get_request(a.id).status == "queued"


@pytest.mark.asyncio
async def test_assign_owner_tracks_attempts(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    service.assign_owner(a.id, "o1@x.com")
    r = service.get_request(a.id)
    assert r.owner_email == "o1@x.com" and r.handoff_attempts == 1
    assert r.attempted_owners == ["o1@x.com"]
    # Re-assigning the SAME owner does not double-count.
    service.assign_owner(a.id, "o1@x.com")
    assert service.get_request(a.id).handoff_attempts == 1
    # A new owner bumps the count.
    service.assign_owner(a.id, "v2@x.com")
    r = service.get_request(a.id)
    assert r.owner_email == "v2@x.com" and r.handoff_attempts == 2
    assert r.attempted_owners == ["o1@x.com", "v2@x.com"]
    # Compare-and-set: a stale handoff expecting the OLD owner is rejected.
    assert service.assign_owner(a.id, "v3@x.com", expected_owner="o1@x.com") is False
    assert service.get_request(a.id).owner_email == "v2@x.com"  # unchanged
    # CAS with the current owner succeeds.
    assert service.assign_owner(a.id, "v3@x.com", expected_owner="v2@x.com") is True
    assert service.get_request(a.id).owner_email == "v3@x.com"


@pytest.mark.asyncio
async def test_list_upvoters_oldest_first_positive_only(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")  # o1 auto-upvote
    service.cast_vote("v2@x.com", a.id, "up")
    service.cast_vote("v3@x.com", a.id, "down")  # negative — excluded
    voters = service.list_upvoters(a.id)
    assert "o1@x.com" in voters and "v2@x.com" in voters
    assert "v3@x.com" not in voters


@pytest.mark.asyncio
async def test_get_by_job_id_and_mark_published(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    service.set_job_id(a.id, "job-42")
    assert service.get_by_job_id("job-42").id == a.id
    service.mark_published(a.id, "https://youtu.be/xyz")
    r = service.get_request(a.id)
    assert r.status == "published" and r.youtube_url == "https://youtu.be/xyz"
    service.add_notified_voters(a.id, ["v1@x.com", "v2@x.com"])
    service.add_notified_voters(a.id, ["v2@x.com", "v3@x.com"])  # ArrayUnion dedupes
    assert set(service.get_request(a.id).notified_voters) == {"v1@x.com", "v2@x.com", "v3@x.com"}
    service.mark_voters_notified(a.id)
    assert service.get_request(a.id).voters_notified is True


@pytest.mark.asyncio
async def test_list_in_progress_and_stalled(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    service.transition_status(a.id, "open", "in_progress")
    assert [r.id for r in service.list_in_progress()] == [a.id]
    service.mark_stalled(a.id)
    assert service.get_request(a.id).status == "stalled"
    assert service.list_in_progress() == []


# ---------------------------------------------------------------------------
# Existing-community-version review flow (A/B follow-up)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pick_candidates_excludes_review_states(service):
    from datetime import datetime, timedelta, timezone
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    b, _, _, _ = await _submit(service, "o2@x.com", "Artist B", "Song B")
    c, _, _, _ = await _submit(service, "o3@x.com", "Artist C", "Song C")
    # Flag A pending; snooze B into the future; C stays clean.
    service.set_review_pending(a.id, {"best_youtube_url": "https://y/x", "tracks": []})
    service.snooze_review(b.id, datetime.now(timezone.utc) + timedelta(days=10))
    ids = [r.id for r in service.list_pick_candidates()]
    assert c.id in ids
    assert a.id not in ids  # pending
    assert b.id not in ids  # snoozed (future)


@pytest.mark.asyncio
async def test_expired_snooze_becomes_pickable_again(service):
    from datetime import datetime, timedelta, timezone
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    service.snooze_review(a.id, datetime.now(timezone.utc) - timedelta(days=1))  # already expired
    assert a.id in [r.id for r in service.list_pick_candidates()]


@pytest.mark.asyncio
async def test_set_review_pending_idempotent(service):
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    versions = {"best_youtube_url": "https://y/x", "tracks": [{"brand_name": "Z", "youtube_url": "https://y/x"}]}
    assert service.set_review_pending(a.id, versions) is True   # newly flagged
    assert service.set_review_pending(a.id, versions) is False  # already pending
    r = service.get_request(a.id)
    assert r.review_state == "pending" and r.community_versions["best_youtube_url"] == "https://y/x"
    assert a.id in [x.id for x in service.list_pending_reviews()]


@pytest.mark.asyncio
async def test_snooze_clear_and_reject(service):
    from datetime import datetime, timedelta, timezone
    a, _, _, _ = await _submit(service, "o1@x.com", "Artist A", "Song A")
    service.set_review_pending(a.id, {"best_youtube_url": None, "tracks": []})
    # keep → snoozed, off the pending queue
    service.snooze_review(a.id, datetime.now(timezone.utc) + timedelta(days=30))
    assert service.get_request(a.id).review_state == "snoozed"
    assert service.list_pending_reviews() == []
    # clear → back to no review flag
    service.clear_review(a.id)
    assert service.get_request(a.id).review_state is None
    # reject → status rejected, off the board
    service.reject_request(a.id)
    r = service.get_request(a.id)
    assert r.status == "rejected" and r.review_state is None
    assert a.id not in [x.id for x in service.list_active()]
