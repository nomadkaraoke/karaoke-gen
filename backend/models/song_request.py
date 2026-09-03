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
# advanced by the daily picker (Phase 2):
#   open -> queued (picked, job being created) -> in_progress (job created, owner reviewing)
#   -> published (live on YouTube) | rejected (manually killed)
#   -> stalled (handoff exhausted its voter cap without anyone completing the review)
RequestStatus = Literal[
    "open", "queued", "in_progress", "published", "rejected", "stalled"
]

# Where a request came from. Phase 2's trending-karaoke agent submits with
# source="trending_agent"; everything a human submits is "human".
RequestSource = Literal["human", "trending_agent"]

# Existing-community-version review state (set when the daily picker's KaraokeNerds
# check finds an existing community karaoke version for a would-be pick):
#   pending  -> flagged, awaiting Andrew's keep/make/reject decision (admin queue)
#   snoozed  -> Andrew chose "keep on board"; not re-flagged until review_snoozed_until
# None means never flagged. The request stays votable/visible on the board throughout;
# the picker just won't auto-make a flagged (pending, or still-snoozed) request.
ReviewState = Literal["pending", "snoozed"]


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

    # Phase-2 fields (never set in Phase 1).
    job_id: Optional[str] = None
    youtube_url: Optional[str] = None
    picked_at: Optional[datetime] = None

    # Current owner of the generated job (starts as the requester; the 24h handoff
    # reassigns it to successive up-voters). owner_assigned_at gates the 24h clock.
    owner_email: Optional[str] = None
    owner_assigned_at: Optional[datetime] = None
    # Emails already made owner (submitter first, then handoff targets) — never retry one.
    attempted_owners: list[str] = Field(default_factory=list)
    handoff_attempts: int = 0

    # Idempotency guards for the daily-pick side effects.
    community_credit_granted: bool = False  # the free credit was granted for this pick
    voters_notified: bool = False  # publish fan-out fully delivered to every up-voter
    # Voters already successfully emailed on publish — lets a re-run retry only the
    # failures instead of re-emailing everyone (voters_notified is the all-done flag).
    notified_voters: list[str] = Field(default_factory=list)

    # Existing-community-version review (set by the daily picker's KaraokeNerds check).
    review_state: Optional[ReviewState] = None
    # The community versions we found, stored so the admin queue + reject email can
    # show/link them: {"best_youtube_url": str|None, "tracks": [{"brand_name","youtube_url"}]}.
    community_versions: Optional[dict] = None
    community_checked_at: Optional[datetime] = None
    review_snoozed_until: Optional[datetime] = None


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


class DailyCommunityPick(BaseModel):
    """Per-UTC-day lock/ledger for the daily free-track picker (Firestore:
    daily_community_pick, doc id = "YYYY-MM-DD").

    Created create-only so only one run per day can proceed (enforces "one free
    track per day, total"). ``phase`` lets a crashed run resume the same day
    without double-granting credits or creating a second job.
    """

    date: str  # UTC "YYYY-MM-DD" (also the doc id)
    # claimed  -> credit_granted -> job_created -> done
    # empty    -> board had nothing eligible (no track made today)
    # skipped  -> disabled / dry-run
    phase: Literal[
        "claimed", "credit_granted", "job_created", "done", "empty", "skipped"
    ] = "claimed"
    request_id: Optional[str] = None
    job_id: Optional[str] = None
    owner_email: Optional[str] = None
    note: Optional[str] = None
    claimed_at: datetime = Field(default_factory=datetime.utcnow)
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
