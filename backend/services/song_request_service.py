"""
Service for the public song-request voting board.

Phase 1 responsibilities:
  - submit a request (auto-correct artist/title via match_judge, dedupe re-submissions)
  - list the ranked board (+ recently published), annotated with the viewer's vote
  - cast one vote per person per UTC calendar day (up/down, movable within the day)

The one-vote-per-day rule is enforced structurally: a user's vote for a given day lives
at a fixed doc id ({email}__{YYYY-MM-DD}), so there can only ever be one.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from backend.config import get_settings
from backend.models.song_request import SongRequest, Vote
from backend.services.match_judge.classifier import normalize_for_match

logger = logging.getLogger(__name__)

REQUESTS_COLLECTION = "song_requests"
VOTES_COLLECTION = "song_request_votes"

# Soft anti-spam cap on how many distinct requests one person can submit per day.
MAX_SUBMISSIONS_PER_DAY = 15
# How many published tracks to surface in the "already made" section.
PUBLISHED_LIMIT = 20
# Hard ceiling on active requests fetched for ranking (board is small; sort in Python).
ACTIVE_FETCH_LIMIT = 500


class SubmissionRateLimited(Exception):
    """Raised when a user exceeds MAX_SUBMISSIONS_PER_DAY."""


class RequestNotFound(Exception):
    """Raised when voting on a non-existent or non-votable request."""


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _dedupe_key(artist: str, title: str) -> str:
    return f"{normalize_for_match(artist)}|{normalize_for_match(title)}"


class SongRequestService:
    """Firestore-backed logic for the requests voting board."""

    def __init__(self):
        self.settings = get_settings()
        self.db = firestore.Client(project=self.settings.google_cloud_project)

    # -------------------------------------------------------------------------
    # Submit
    # -------------------------------------------------------------------------

    async def submit_request(
        self, user_email: str, artist: str, title: str
    ) -> Tuple[SongRequest, bool, str, str]:
        """Create (or resurface) a request for artist/title.

        Returns (request, already_existed, canonical_artist, canonical_title).
        Auto-corrects artist/title via match_judge; a re-submission of the same song
        (by dedupe key) counts as an up-vote for the existing open request.
        """
        email = user_email.lower()
        artist = artist.strip()
        title = title.strip()

        # Auto-correct / canonicalize using the same judge the job-submission flow uses.
        canonical_artist, canonical_title = await self._canonicalize(artist, title)

        key = _dedupe_key(canonical_artist, canonical_title)

        existing = self._find_open_by_key(key)
        if existing is not None:
            # Same song already requested — treat this as an up-vote and resurface it.
            # Idempotent: cast_vote toggles OFF a matching same-request/same-direction
            # vote, so re-submitting a song you already up-voted today would REMOVE the
            # vote. Only cast when the user isn't already up-voting this request today.
            current = self.get_daily_vote(email)
            if not (current and current.request_id == existing.id and current.value == 1):
                self.cast_vote(email, existing.id, "up")
            refreshed = self.get_request(existing.id) or existing
            return refreshed, True, canonical_artist, canonical_title

        # Enforce the per-day submission cap (cheap: equality query + in-Python day filter).
        if self._submissions_today(email) >= MAX_SUBMISSIONS_PER_DAY:
            raise SubmissionRateLimited()

        request = SongRequest(
            id=str(uuid.uuid4()),
            artist=canonical_artist,
            title=canonical_title,
            artist_raw=artist,
            title_raw=title,
            dedupe_key=key,
            submitted_by=email,
            source="human",
            status="open",
            vote_count=0,
        )
        self.db.collection(REQUESTS_COLLECTION).document(request.id).set(
            request.model_dump(mode="json")
        )
        logger.info("song request created id=%s by=%s key=%s", request.id, email, key)

        # The submitter's endorsement counts as their vote for today.
        self.cast_vote(email, request.id, "up")
        return self.get_request(request.id) or request, False, canonical_artist, canonical_title

    async def _canonicalize(self, artist: str, title: str) -> Tuple[str, str]:
        """Best-effort artist/title tidy via match_judge; never blocks submission."""
        try:
            from backend.services.match_judge.service import judge_match

            verdict = await judge_match(artist, title, stage="full")
            # Only adopt the canonical form when the judge is confident and it's a real
            # song (cosmetic/content). Ambiguous/none → keep what the user typed.
            if verdict.confident and verdict.kind in ("cosmetic", "content"):
                ca = verdict.canonical_artist.strip() or artist
                ct = verdict.canonical_title.strip() or title
                return ca, ct
        except Exception:
            logger.warning("match_judge canonicalize failed; using raw input", exc_info=True)
        return artist, title

    def _find_open_by_key(self, key: str) -> Optional[SongRequest]:
        # Single equality filter on dedupe_key (auto single-field index) + filter status
        # in Python — avoids needing a composite index. A dedupe_key maps to at most a
        # handful of docs (usually one open + maybe past published ones).
        query = self.db.collection(REQUESTS_COLLECTION).where(
            filter=FieldFilter("dedupe_key", "==", key)
        )
        for doc in query.stream():
            req = SongRequest(**doc.to_dict())
            if req.status == "open":
                return req
        return None

    def _submissions_today(self, email: str) -> int:
        today = _utc_today()
        query = self.db.collection(REQUESTS_COLLECTION).where(
            filter=FieldFilter("submitted_by", "==", email)
        )
        return sum(
            1
            for doc in query.stream()
            if str(doc.to_dict().get("created_at", "")).startswith(today)
        )

    # -------------------------------------------------------------------------
    # Read
    # -------------------------------------------------------------------------

    def get_request(self, request_id: str) -> Optional[SongRequest]:
        doc = self.db.collection(REQUESTS_COLLECTION).document(request_id).get()
        if doc.exists:
            return SongRequest(**doc.to_dict())
        return None

    def list_active(self) -> list[SongRequest]:
        """Open requests, ranked by net votes desc then oldest-first."""
        query = self.db.collection(REQUESTS_COLLECTION).where(
            filter=FieldFilter("status", "==", "open")
        ).limit(ACTIVE_FETCH_LIMIT)
        items = [SongRequest(**doc.to_dict()) for doc in query.stream()]
        items.sort(key=lambda r: (-r.vote_count, r.created_at))
        return items

    def list_published(self) -> list[SongRequest]:
        query = self.db.collection(REQUESTS_COLLECTION).where(
            filter=FieldFilter("status", "==", "published")
        ).limit(ACTIVE_FETCH_LIMIT)
        items = [SongRequest(**doc.to_dict()) for doc in query.stream()]
        items.sort(key=lambda r: (r.picked_at or r.updated_at), reverse=True)
        return items[:PUBLISHED_LIMIT]

    def get_daily_vote(self, email: str) -> Optional[Vote]:
        """The caller's vote for today, if any."""
        doc = (
            self.db.collection(VOTES_COLLECTION)
            .document(self._vote_doc_id(email.lower(), _utc_today()))
            .get()
        )
        if doc.exists:
            return Vote(**doc.to_dict())
        return None

    # -------------------------------------------------------------------------
    # Vote
    # -------------------------------------------------------------------------

    @staticmethod
    def _vote_doc_id(email: str, day: str) -> str:
        return f"{email}__{day}"

    def cast_vote(self, user_email: str, request_id: str, direction: str) -> Optional[Vote]:
        """Cast/move/undo the caller's single daily vote.

        Rules (one vote total per person per UTC day):
          - No vote yet today  -> record it, apply +/-1 to the request.
          - Same request+dir   -> toggle it OFF (frees today's vote).
          - Different request or direction -> move it (reverse old, apply new).

        Returns the resulting Vote, or None if it was toggled off. Runs in a
        Firestore transaction so the vote doc and denormalized counts never drift.
        """
        email = user_email.lower()
        value = 1 if direction == "up" else -1
        day = _utc_today()
        vote_ref = self.db.collection(VOTES_COLLECTION).document(self._vote_doc_id(email, day))
        target_ref = self.db.collection(REQUESTS_COLLECTION).document(request_id)

        transaction = self.db.transaction()
        return _cast_vote_txn(transaction, self, vote_ref, target_ref, request_id, email, day, value)


@firestore.transactional
def _cast_vote_txn(transaction, service, vote_ref, target_ref, request_id, email, day, value):
    """Transactional body for cast_vote.

    Module-level (not a method) because @firestore.transactional returns a plain
    callable, not a descriptor — as a method it wouldn't bind `self`, shifting args.
    """
    # --- reads first (Firestore transaction requirement) ---
    target_snap = target_ref.get(transaction=transaction)
    if not target_snap.exists or target_snap.to_dict().get("status") != "open":
        raise RequestNotFound(request_id)

    existing_snap = vote_ref.get(transaction=transaction)
    existing = existing_snap.to_dict() if existing_snap.exists else None
    old_value = int(existing.get("value", 0)) if existing else 0
    old_request_id = existing.get("request_id") if existing else None

    # Snapshot registry so each request doc is read once and updated once
    # (multiple bumps to the same doc from a single pre-txn snapshot would clobber).
    snaps = {request_id: target_snap}
    if old_request_id and old_request_id != request_id:
        old_ref = service.db.collection(REQUESTS_COLLECTION).document(old_request_id)
        snaps[old_request_id] = old_ref.get(transaction=transaction)

    now = datetime.now(timezone.utc)

    # --- accumulate net vote-count deltas per request, then write once each ---
    deltas: dict[str, int] = {}
    toggling_off = (
        existing is not None and old_request_id == request_id and old_value == value
    )
    if toggling_off:
        # Remove today's vote and undo its effect — frees the daily vote.
        transaction.delete(vote_ref)
        deltas[request_id] = -value
        vote = None
    else:
        if existing is not None:
            deltas[old_request_id] = deltas.get(old_request_id, 0) - old_value
        deltas[request_id] = deltas.get(request_id, 0) + value
        vote = Vote(
            voter_email=email,
            voted_date=day,
            request_id=request_id,
            value=value,
            created_at=(existing.get("created_at") if existing else now) or now,
            updated_at=now,
        )
        transaction.set(vote_ref, vote.model_dump(mode="json"))

    for rid, delta in deltas.items():
        if delta == 0:
            continue
        snap = snaps.get(rid)
        if snap is None or not snap.exists:
            continue  # request gone (e.g. old vote pointed at a closed request)
        ref = service.db.collection(REQUESTS_COLLECTION).document(rid)
        current = int(snap.to_dict().get("vote_count", 0))
        transaction.update(ref, {
            "vote_count": current + delta,
            "updated_at": now.isoformat(),
        })

    return vote


_song_request_service: Optional[SongRequestService] = None


def get_song_request_service() -> SongRequestService:
    """Get the global song-request service instance."""
    global _song_request_service
    if _song_request_service is None:
        _song_request_service = SongRequestService()
    return _song_request_service
