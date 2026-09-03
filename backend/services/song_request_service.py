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

from google.api_core import exceptions as gcloud_exceptions

from backend.config import get_settings
from backend.models.song_request import DailyCommunityPick, SongRequest, Vote
from backend.services.match_judge.classifier import normalize_for_match

logger = logging.getLogger(__name__)

REQUESTS_COLLECTION = "song_requests"
VOTES_COLLECTION = "song_request_votes"
# Per-UTC-day lock/ledger for the daily free-track picker (Phase 2).
DAILY_PICK_COLLECTION = "daily_community_pick"

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

    # -------------------------------------------------------------------------
    # Phase 2 — daily picker, ownership handoff, publish fan-out
    # -------------------------------------------------------------------------

    # --- per-day lock / ledger ---

    def claim_day(self, date: str) -> Tuple[DailyCommunityPick, bool]:
        """Atomically claim the day for the daily picker.

        Returns (lock, claimed_new). ``claimed_new`` is True only for the run that
        created the lock; every other (retried/overlapping) run gets the existing
        lock and False. This is what enforces "one free track per day, total".
        """
        ref = self.db.collection(DAILY_PICK_COLLECTION).document(date)
        lock = DailyCommunityPick(date=date, phase="claimed")
        try:
            ref.create(lock.model_dump(mode="json"))
            logger.info("daily-pick: claimed day %s", date)
            return lock, True
        except gcloud_exceptions.AlreadyExists:
            existing = ref.get()
            return DailyCommunityPick(**existing.to_dict()), False

    def get_lock(self, date: str) -> Optional[DailyCommunityPick]:
        doc = self.db.collection(DAILY_PICK_COLLECTION).document(date).get()
        return DailyCommunityPick(**doc.to_dict()) if doc.exists else None

    def update_lock(self, date: str, **fields) -> None:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.db.collection(DAILY_PICK_COLLECTION).document(date).update(fields)

    # --- picking ---

    def pick_eligible(self) -> Optional[SongRequest]:
        """The single highest-priority auto-makeable request (net votes >= 0, oldest
        tiebreak), ignoring the community-version review flow. Kept for callers that
        just want the top request; the daily picker uses list_pick_candidates so it
        can community-check each candidate in rank order."""
        candidates = self.list_pick_candidates()
        return candidates[0] if candidates else None

    def list_pick_candidates(self) -> list[SongRequest]:
        """Ranked auto-make candidates: open, net votes >= 0, oldest-first, EXCLUDING
        requests currently held for existing-community-version review (pending, or
        snoozed and not yet past their cooldown). The daily picker walks these in
        order, community-checks each, and makes the first with no existing version."""
        now = datetime.now(timezone.utc)
        out: list[SongRequest] = []
        for req in self.list_active():  # already ranked -vote_count, created_at asc
            if req.vote_count < 0:
                continue
            if req.review_state == "pending":
                continue
            if req.review_state == "snoozed":
                until = _as_aware(req.review_snoozed_until)
                if until and until > now:
                    continue  # still in its keep-cooldown
            out.append(req)
        return out

    def set_review_pending(self, request_id: str, versions: dict) -> bool:
        """Flag a request as needing existing-version review. Transactional and
        idempotent: returns True only the first time it transitions to pending (so
        the picker emails Andrew about a newly-flagged pick exactly once)."""
        ref = self.db.collection(REQUESTS_COLLECTION).document(request_id)
        transaction = self.db.transaction()
        return _set_review_pending_txn(transaction, ref, versions)

    def list_pending_reviews(self) -> list[SongRequest]:
        """Requests flagged pending existing-version review (the admin queue)."""
        query = self.db.collection(REQUESTS_COLLECTION).where(
            filter=FieldFilter("review_state", "==", "pending")
        ).limit(ACTIVE_FETCH_LIMIT)
        items = [SongRequest(**doc.to_dict()) for doc in query.stream()]
        items.sort(key=lambda r: (-r.vote_count, r.created_at))
        return items

    def snooze_review(self, request_id: str, until: datetime) -> None:
        """'Keep on board': clear the pending flag but snooze re-review until `until`."""
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update({
            "review_state": "snoozed",
            "review_snoozed_until": until.isoformat(),
            "updated_at": _now_iso(),
        })

    def clear_review(self, request_id: str) -> None:
        """Clear any review flag (used when admin chooses to make our version anyway)."""
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"review_state": None, "updated_at": _now_iso()}
        )

    def reject_request(self, request_id: str) -> None:
        """Reject a request (admin declined to make it — e.g. a good community
        version already exists). Removes it from the board and clears review flags."""
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"status": "rejected", "review_state": None, "updated_at": _now_iso()}
        )

    def transition_status(
        self, request_id: str, expected_from: str, new_status: str, **extra
    ) -> bool:
        """Move a request's status inside a transaction, guarding on the current
        value so two runners can't both advance the same request. Extra fields are
        written alongside. Returns True if the transition was applied."""
        ref = self.db.collection(REQUESTS_COLLECTION).document(request_id)
        transaction = self.db.transaction()
        return _transition_status_txn(
            transaction, ref, expected_from, new_status, extra
        )

    def mark_credit_granted(self, request_id: str) -> None:
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"community_credit_granted": True, "updated_at": _now_iso()}
        )

    def set_job_id(self, request_id: str, job_id: str) -> None:
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"job_id": job_id, "updated_at": _now_iso()}
        )

    def assign_owner(
        self, request_id: str, email: str, expected_owner: Optional[str] = None
    ) -> bool:
        """Make ``email`` the current owner and (re)start their 24h review clock.

        Records the email in attempted_owners and bumps handoff_attempts when it is
        a genuinely new owner (so the initial assignment counts as attempt #1).

        ``expected_owner`` is a compare-and-set guard for the handoff: pass the
        owner you observed, and the write is skipped (returns False) if a newer
        run already moved the request to someone else — preventing a stale handoff
        from clobbering a fresher assignment. The picker passes None (initial
        assignment always applies). Returns True if the assignment was written.
        """
        email = email.lower()
        expected = expected_owner.lower() if expected_owner else None
        ref = self.db.collection(REQUESTS_COLLECTION).document(request_id)
        transaction = self.db.transaction()
        return _assign_owner_txn(transaction, ref, email, expected)

    def mark_stalled(self, request_id: str) -> None:
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"status": "stalled", "updated_at": _now_iso()}
        )

    # --- reads for handoff / publish ---

    def get_by_job_id(self, job_id: str) -> Optional[SongRequest]:
        query = self.db.collection(REQUESTS_COLLECTION).where(
            filter=FieldFilter("job_id", "==", job_id)
        ).limit(1)
        for doc in query.stream():
            return SongRequest(**doc.to_dict())
        return None

    def list_in_progress(self) -> list[SongRequest]:
        """Community picks currently owned by someone reviewing (status in_progress)."""
        query = self.db.collection(REQUESTS_COLLECTION).where(
            filter=FieldFilter("status", "==", "in_progress")
        ).limit(ACTIVE_FETCH_LIMIT)
        return [SongRequest(**doc.to_dict()) for doc in query.stream()]

    def list_upvoters(self, request_id: str) -> list[str]:
        """Emails that up-voted this request (value > 0), oldest vote first.

        Votes are stored per-day at doc id {email}__{date}, and a mover's vote row
        reflects only their *latest* request — so a voter appears here iff their
        current daily vote still points at this request with a positive value.
        """
        query = self.db.collection(VOTES_COLLECTION).where(
            filter=FieldFilter("request_id", "==", request_id)
        )
        rows = [doc.to_dict() for doc in query.stream()]
        upvotes = [r for r in rows if int(r.get("value", 0)) > 0]
        # Order by when the voter last committed to THIS request: a moved vote keeps
        # its original created_at but refreshes updated_at, so updated_at reflects the
        # moment they actually backed this request (created_at as a fallback).
        upvotes.sort(key=lambda r: r.get("updated_at") or r.get("created_at", ""))
        # De-dupe by email, preserving earliest.
        seen: set[str] = set()
        emails: list[str] = []
        for r in upvotes:
            e = (r.get("voter_email") or "").lower()
            if e and e not in seen:
                seen.add(e)
                emails.append(e)
        return emails

    def mark_published(self, request_id: str, youtube_url: str) -> None:
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"status": "published", "youtube_url": youtube_url, "updated_at": _now_iso()}
        )

    def add_notified_voters(self, request_id: str, emails: list[str]) -> None:
        """Record voters that were successfully emailed on publish (ArrayUnion so
        concurrent/retried fan-outs can't clobber each other)."""
        if not emails:
            return
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"notified_voters": firestore.ArrayUnion(emails), "updated_at": _now_iso()}
        )

    def mark_voters_notified(self, request_id: str) -> None:
        self.db.collection(REQUESTS_COLLECTION).document(request_id).update(
            {"voters_notified": True, "updated_at": _now_iso()}
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a (possibly naive) datetime to UTC-aware for safe comparison."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@firestore.transactional
def _set_review_pending_txn(transaction, ref, versions):
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return False
    if snap.to_dict().get("review_state") == "pending":
        return False  # already flagged — don't re-notify
    transaction.update(ref, {
        "review_state": "pending",
        "community_versions": versions,
        "community_checked_at": _now_iso(),
        "updated_at": _now_iso(),
    })
    return True


@firestore.transactional
def _transition_status_txn(transaction, ref, expected_from, new_status, extra):
    snap = ref.get(transaction=transaction)
    if not snap.exists or snap.to_dict().get("status") != expected_from:
        return False
    update = {"status": new_status, "updated_at": _now_iso(), **extra}
    transaction.update(ref, update)
    return True


@firestore.transactional
def _assign_owner_txn(transaction, ref, email, expected_owner):
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return False
    data = snap.to_dict()
    # Compare-and-set: a handoff aborts if the request already moved on.
    if expected_owner is not None and (data.get("owner_email") or "").lower() != expected_owner:
        return False
    attempted = list(data.get("attempted_owners") or [])
    is_new = email not in attempted
    if is_new:
        attempted.append(email)
    transaction.update(ref, {
        "owner_email": email,
        "owner_assigned_at": _now_iso(),
        "attempted_owners": attempted,
        "handoff_attempts": int(data.get("handoff_attempts", 0)) + (1 if is_new else 0),
        "updated_at": _now_iso(),
    })
    return True


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
