"""Runtime knobs for the fake workers, stored in Firestore so every API process sees them.

Tests change them through ``PUT /api/test/fake-config`` (see
``backend/api/routes/test_support.py``) and reset them with ``POST /api/test/reset``.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_COLLECTION = "local_stack"
CONFIG_DOC = "fake_config"

# Worker kinds the fakes implement. ``fail_at`` must be one of these.
WORKER_KINDS = ("audio_download", "audio", "lyrics", "screens", "render_video", "video")
# Values of ``search_confidence`` (fake search shapes → UI tier 1 / 2 / 3).
SEARCH_CONFIDENCES = ("perfect", "recommended", "unsure")
# Values of ``judge_kind`` (the fake match judge / free-text resolve verdict kind).
JUDGE_KINDS = ("none", "cosmetic", "content", "ambiguous")
LARGE_FIXTURE_MIN_SECONDS = 305

# Values of ``review_fixture`` (the shape of the fake lyrics the review UI opens on).
REVIEW_FIXTURES = ("default", "corrections", "duet", "rtl", "large")
# Values of ``backing_vocals`` (silent stem vs one with an audible 10-20 s window).
BACKING_VOCALS = ("silent", "audible")
# Values of ``review_mode`` (what the seeded job asks for) and ``auto_approval``
# (which stored auto-approval verdict the fake screens worker writes).
REVIEW_MODES = ("always_review", "auto")
AUTO_APPROVALS = ("none", "lyrics_confident", "backing_confident", "both_unsure")


@dataclass
class FakeConfig:
    # Seconds each fake worker waits before doing its work, so a UI (or test) can
    # observe the intermediate states. 0 makes the whole pipeline near-instant.
    step_delay_seconds: float = 0.5
    # True: the fake screens worker auto-approves the review (skips straight to
    # render) unless the job asked for review_mode="always_review". False: every job
    # parks at AWAITING_REVIEW for a human (the default, exercises the review UI).
    auto_approve: bool = False
    # Fail the job inside this worker (one of WORKER_KINDS), or None.
    fail_at: Optional[str] = None
    # Length of the generated song audio. Longer than the credits charged for the
    # job sends it to AWAITING_DURATION_CONFIRM (the real duration reconciliation).
    audio_duration_seconds: int = 30
    # Number of results the fake audio search returns (0 = "no results").
    search_result_count: int = 3
    # Shapes the fake audio search into the web UI's confidence tiers
    # (frontend/lib/audio-search-utils.ts getSearchConfidence): "perfect" = tier 1
    # (well-seeded lossless, right track), "recommended" = tier 2 (lossless, right
    # track, few seeders), "unsure" = tier 3 (wrong track names, almost no seeders).
    search_confidence: str = "perfect"
    # True: the fake video worker sets state_data.youtube_url (FAKE_YOUTUBE_URL) for
    # non-private jobs, like a real YouTube upload. False: tests the "no link" path.
    youtube_url_enabled: bool = True
    # Fake match judge (POST /api/catalog/match-judge, per-user config) and fake free-text
    # resolve (POST /api/kjbox/catalog/resolve, global config: partners have no user).
    # judge_kind picks the verdict kind; the canonical values fill it in (defaults if empty).
    judge_kind: str = "none"
    judge_canonical_artist: str = ""
    judge_canonical_title: str = ""
    # Review-UI fixtures (per-user, for the mobile review Maestro flows). Defaults keep
    # today's behaviour. See docs/TESTING.md § Local stack.
    # Shape of the fake lyrics: default | corrections | duet | rtl | large.
    review_fixture: str = "default"
    # silent: the backing stem is silence (analysis recommends clean). audible: a tone in
    # the 10-20 s window, so the analysis recommends with_backing / review.
    backing_vocals: str = "silent"
    # The fake audio worker uploads its stems this many seconds after it starts, so the
    # job reaches awaiting_review first (instrumental-urls audio_complete is false until then).
    stems_delay_seconds: float = 0
    # What the job scenario asks for: always_review (every screen) or auto (a confident
    # stored verdict lets the UI skip a screen).
    review_mode: str = "always_review"
    # Stored processing_metadata.auto_approval verdict written when the job parks at
    # awaiting_review: none | lyrics_confident | backing_confident | both_unsure.
    auto_approval: str = "none"
    # True: the next POST /api/review/{id}/complete answers 503 once, then this resets to False.
    review_complete_fail_once: bool = False

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "FakeConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.fail_at is not None and self.fail_at not in WORKER_KINDS:
            raise ValueError(f"fail_at must be one of {WORKER_KINDS} or null")
        if self.step_delay_seconds < 0 or self.step_delay_seconds > 60:
            raise ValueError("step_delay_seconds must be between 0 and 60")
        if not 5 <= self.audio_duration_seconds <= 1800:
            raise ValueError("audio_duration_seconds must be between 5 and 1800")
        if not 0 <= self.search_result_count <= 3:
            raise ValueError("search_result_count must be between 0 and 3")
        if self.search_confidence not in SEARCH_CONFIDENCES:
            raise ValueError(f"search_confidence must be one of {SEARCH_CONFIDENCES}")
        if self.judge_kind not in JUDGE_KINDS:
            raise ValueError(f"judge_kind must be one of {JUDGE_KINDS}")
        if not isinstance(self.judge_canonical_artist, str) or not isinstance(self.judge_canonical_title, str):
            raise ValueError("judge_canonical_artist and judge_canonical_title must be strings")
        if self.review_fixture not in REVIEW_FIXTURES:
            raise ValueError(f"review_fixture must be one of {REVIEW_FIXTURES}")
        if self.review_fixture == "large" and self.audio_duration_seconds < LARGE_FIXTURE_MIN_SECONDS:
            # 300 lines at >= 1 s each: shorter audio leaves most lyrics past the end of the song.
            raise ValueError(f"review_fixture 'large' needs audio_duration_seconds >= {LARGE_FIXTURE_MIN_SECONDS}")
        if self.backing_vocals not in BACKING_VOCALS:
            raise ValueError(f"backing_vocals must be one of {BACKING_VOCALS}")
        if (
            isinstance(self.stems_delay_seconds, bool)
            or not isinstance(self.stems_delay_seconds, (int, float))
            or not 0 <= self.stems_delay_seconds <= 600
        ):
            raise ValueError("stems_delay_seconds must be a number between 0 and 600")
        if self.review_mode not in REVIEW_MODES:
            raise ValueError(f"review_mode must be one of {REVIEW_MODES}")
        if self.auto_approval not in AUTO_APPROVALS:
            raise ValueError(f"auto_approval must be one of {AUTO_APPROVALS}")
        if not isinstance(self.review_complete_fail_once, bool):
            raise ValueError("review_complete_fail_once must be a boolean")


USER_CONFIG_COLLECTION = "local_stack_user_config"


def _default_db():
    from backend.services.firestore_service import FirestoreService

    return FirestoreService().db


def _doc(db, email: Optional[str] = None):
    if email:
        return db.collection(USER_CONFIG_COLLECTION).document(email.strip().lower())
    return db.collection(CONFIG_COLLECTION).document(CONFIG_DOC)


def _read(db, email: Optional[str] = None) -> Dict[str, Any]:
    snap = _doc(db, email).get()
    return (snap.to_dict() or {}) if snap.exists else {}


def get_fake_config(db=None, email: Optional[str] = None) -> FakeConfig:
    """Defaults, overlaid with the global config, overlaid with ``email``'s overrides.

    Per-user overrides let tests that each use their own user run in parallel
    against one stack without fighting over the global knobs.
    """
    db = db or _default_db()
    try:
        merged = _read(db)
        if email:
            merged.update(_read(db, email))
        return FakeConfig.from_dict(merged)
    except Exception as e:  # emulator hiccup: fall back to defaults, never stall a job
        logger.warning(f"fake_workers: could not read config, using defaults: {e}")
        return FakeConfig()


def set_fake_config(updates: Dict[str, Any], db=None, email: Optional[str] = None) -> FakeConfig:
    """Merge ``updates`` into the global config (or ``email``'s overrides) and return the effective config."""
    db = db or _default_db()
    known = {f.name for f in fields(FakeConfig)}
    unknown = set(updates) - known
    if unknown:
        raise ValueError(f"Unknown fake config keys: {sorted(unknown)}")
    stored = _read(db, email)
    stored.update(updates)
    base = get_fake_config(db).to_dict() if email else {}
    FakeConfig.from_dict({**base, **stored}).validate()
    _doc(db, email).set(stored)
    return get_fake_config(db, email)


def clear_fake_config(db=None) -> None:
    """Drop the global config and every per-user override (POST /api/test/reset)."""
    db = db or _default_db()
    _doc(db).delete()
    for snap in db.collection(USER_CONFIG_COLLECTION).stream():
        snap.reference.delete()


def consume_review_complete_failure(email: Optional[str] = None, db=None) -> bool:
    """True once when ``review_complete_fail_once`` is set for ``email``, then clear it.

    Backs the one-shot 503 on ``POST /api/review/{id}/complete`` (finish-step failure/retry flows).
    """
    db = db or _default_db()
    if not get_fake_config(db, email=email).review_complete_fail_once:
        return False
    set_fake_config({"review_complete_fail_once": False}, db=db, email=email)
    return True
