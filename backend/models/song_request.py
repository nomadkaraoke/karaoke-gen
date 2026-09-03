"""
Models for the public song-request voting board (requests.nomadkaraoke.com).

A minimal, Hacker-News-style board where anyone (email magic-link auth) can:
  - submit a song request (artist + title, auto-corrected via match_judge)
  - cast one vote per calendar day, up or down, on a single request

Phase 1 (this file) covers the board itself. Phase 2 adds the daily auto-picker,
free-credit grant, ownership handoff, and voter publish emails — see
docs/archive/2026-09-02-requests-voting-board-plan.md.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Request lifecycle. Phase 1 only ever sets/reads "open"; the later states are
# reserved so the daily picker (Phase 2) can advance a request without a schema change.
RequestStatus = Literal["open", "queued", "in_progress", "published", "rejected"]

# Where a request came from. Phase 2's trending-karaoke agent submits with
# source="trending_agent"; everything a human submits is "human".
RequestSource = Literal["human", "trending_agent"]


class SongRequest(BaseModel):
    """A single song request on the public board (Firestore: song_requests)."""

    id: str
    # Canonical (post match_judge) artist/title shown on the board.
    artist: str
    title: str
    # Exactly what the user typed, kept for audit / "did we correct this right?".
    artist_raw: str
    title_raw: str
    # Normalized artist+title used to dedupe re-submissions of the same song.
    dedupe_key: str

    submitted_by: str  # lowercased email
    source: RequestSource = "human"
    status: RequestStatus = "open"

    # Denormalized net score (sum of vote values). Kept in sync via Increment.
    vote_count: int = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Phase-2 placeholders (never set in Phase 1).
    job_id: Optional[str] = None
    youtube_url: Optional[str] = None
    picked_at: Optional[datetime] = None


class Vote(BaseModel):
    """A single daily vote (Firestore: song_request_votes, doc id = {email}__{date}).

    The doc id encodes the one-vote-per-person-per-day rule structurally: a user can
    only ever have one vote document for a given UTC date.
    """

    voter_email: str  # lowercased
    voted_date: str  # UTC "YYYY-MM-DD"
    request_id: str
    value: int  # +1 (up) or -1 (down)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# --- API request/response models ---


class SubmitRequestBody(BaseModel):
    artist: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=200)

    @field_validator("artist", "title")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # min_length=1 accepts " " — strip first so whitespace-only input is rejected
        # (the service also strips, but this stops a blank request at the API boundary).
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class VoteBody(BaseModel):
    direction: Literal["up", "down"]


class SongRequestPublic(BaseModel):
    """Board item as exposed to the frontend. Never includes voter emails."""

    id: str
    artist: str
    title: str
    status: RequestStatus
    source: RequestSource
    vote_count: int
    created_at: str  # ISO
    youtube_url: Optional[str] = None
    # Whether the submitter's raw input differed from the canonical form we stored.
    was_corrected: bool = False
    # Per-viewer annotation (only set when the caller is authenticated).
    your_vote: Optional[int] = None  # +1 / -1 / None


class SubmitResponse(BaseModel):
    status: str  # "created" | "already_exists"
    request: SongRequestPublic
    # match_judge canonicalization surfaced so the UI can say "we tidied that to …".
    canonical_artist: str
    canonical_title: str
    was_corrected: bool


class BoardResponse(BaseModel):
    """The board payload: active (votable) requests + recently published ones."""

    requests: list[SongRequestPublic]
    published: list[SongRequestPublic] = []
    # The authenticated caller's daily-vote state (null when signed out).
    voted_today: Optional[bool] = None
    your_vote_request_id: Optional[str] = None


class DailyVoteStatus(BaseModel):
    voted_today: bool
    request_id: Optional[str] = None
    value: Optional[int] = None
