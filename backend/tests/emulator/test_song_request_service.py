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
