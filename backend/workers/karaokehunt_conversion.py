"""Convert retired-KaraokeHunt-app song requests into Nomad Karaoke users + jobs.

The old KaraokeHunt mobile app (unpublished from both stores, backend GCP project
dead) still has live installs whose "Request Now" flow POSTs to
create.karaokehunt.com and then tells the user "you should receive an email in
5-10 minutes with a link to your new karaoke video". A Cloudflare Worker on that
domain forwards the payload to POST /api/karaokehunt/request, which lands here.

Per request we:
  1. Log an intake doc to ``karaokehunt_requests`` (audit + dedup + retry anchor).
  2. Auto-convert per Andrew's spec: brand-new emails get an account + 1 free
     credit and the job is created AS THEM (non-admin, consumes the credit) using
     the same search → conservative auto-select → download primitives as the web
     flow; existing users with credits get the job on their own credit; existing
     users without credits get the song submitted to the free community requests
     board instead.
  3. Send them the promised email (one-click sign-in link included).

Mirrors ``community_daily_pick`` (the established grant-credit + create-job-as-user
precedent) but uses the conservative ``pick_auto_selection`` instead of
``select_best``: app inputs include garbage, and a low-confidence match must park
in AWAITING_AUDIO_SELECTION (user picks via the emailed link) rather than publish
a wrong track to YouTube.

Durability: any failure leaves the intake doc with ``outcome="error"``; the
admin-only POST /api/karaokehunt/internal/reprocess/{doc_id} retries it. The
app's own Pushbullet push to Andrew (client-side, untouched) is the human backstop.
"""
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.config import get_settings
from backend.models.job import JobCreate, JobStatus
from backend.services.audio_search_service import (
    AudioSearchError,
    AudioSearchService,
    NoResultsError,
    pick_auto_selection,
)
from backend.services.job_manager import JobManager
from backend.services.match_judge.classifier import normalize_for_match
from backend.services.song_request_service import (
    SubmissionRateLimited,
    get_song_request_service,
)
from backend.services.theme_service import get_theme_service
from backend.services.user_service import get_user_service
from backend.services.worker_service import get_worker_service

logger = logging.getLogger(__name__)

COLLECTION = "karaokehunt_requests"
CREDIT_REASON = "karaokehunt_app_conversion"
DEDUP_WINDOW_DAYS = 14
LOGIN_LINK_EXPIRY_HOURS = 168  # 7 days, the maximum

# Outcomes that count as "this request was handled" for dedup purposes.
TERMINAL_OUTCOMES = {"job_created", "job_parked", "board_submitted", "duplicate"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_db_singleton = None


def _get_db():
    global _db_singleton
    if _db_singleton is None:
        from google.cloud import firestore  # type: ignore[import]

        _db_singleton = firestore.Client(project="nomadkaraoke")
    return _db_singleton


def _dedupe_key(artist: str, title: str) -> str:
    return f"{normalize_for_match(artist)}|{normalize_for_match(title)}"


def valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 320 and bool(_EMAIL_RE.match(email))


def create_intake(
    payload: Dict[str, Any],
    client_ip: str = "",
    user_agent: str = "",
) -> Dict[str, Any]:
    """Persist an intake doc for a raw app payload and return it (with ``id``).

    Always succeeds in classifying: docs with unusable email/artist/title get
    ``outcome="invalid"`` immediately (audit trail without side effects).
    """
    email = str(payload.get("email") or "").strip().lower()
    artist = str(payload.get("artist") or "").strip()
    title = str(payload.get("title") or "").strip()
    input_url = str(payload.get("input_url") or "").strip()

    # Cap the raw payload so a malicious body can't bloat the doc.
    raw = {str(k)[:40]: str(v)[:500] for k, v in list(payload.items())[:20]}

    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "artist": artist,
        "title": title,
        "input_url": input_url[:2048],
        "raw": raw,
        "client_ip": client_ip[:64],
        "user_agent": user_agent[:512],
        "dedupe_key": _dedupe_key(artist, title) if (artist or title) else "",
        "outcome": None,
        "job_id": None,
        "board_request_id": None,
        "new_user": False,
        "credit_granted": False,
        "email_sent": False,
        "error": None,
        "created_at": datetime.now(timezone.utc),
        "processed_at": None,
    }

    if not valid_email(email) or not artist or not title:
        doc["outcome"] = "invalid"

    _get_db().collection(COLLECTION).document(doc["id"]).set(doc)
    logger.info(
        "karaokehunt: intake %s email=%s artist=%r title=%r outcome=%s",
        doc["id"], email, artist, title, doc["outcome"],
    )
    return doc


async def process_intake(doc_id: str, force: bool = False) -> Dict[str, Any]:
    """Run the conversion for an intake doc. Idempotent: terminal docs are
    skipped unless ``force`` (which still never double-grants credits or
    double-creates jobs thanks to the doc's durable markers)."""
    db = _get_db()
    ref = db.collection(COLLECTION).document(doc_id)
    snap = ref.get()
    if not snap.exists:
        return {"status": "not_found", "doc_id": doc_id}
    doc = snap.to_dict()

    if doc.get("outcome") == "invalid":
        return {"status": "invalid", "doc_id": doc_id}
    if doc.get("outcome") in TERMINAL_OUTCOMES and not force:
        return {"status": "already_processed", "doc_id": doc_id,
                "outcome": doc.get("outcome")}

    email = doc["email"]
    artist = doc["artist"]
    title = doc["title"]

    def finish(outcome: str, **fields: Any) -> Dict[str, Any]:
        updates = {"outcome": outcome,
                   "processed_at": datetime.now(timezone.utc), **fields}
        ref.update(updates)
        logger.info("karaokehunt: %s -> %s %s", doc_id, outcome, fields or "")
        return {"status": outcome, "doc_id": doc_id, **fields}

    # ---- Dedup: same email + song already handled recently → do nothing. ----
    if _recent_duplicate_exists(db, doc):
        return finish("duplicate")

    user_service = get_user_service()
    settings = get_settings()

    # ---- Ensure the account exists; grant the conversion credit to new users. ----
    existing_user = user_service.get_user(email)
    new_user = existing_user is None
    if new_user:
        user_service.get_or_create_user(email)
    ref.update({"new_user": new_user})
    doc["new_user"] = new_user

    if new_user and not doc.get("credit_granted"):
        ok, balance, msg = user_service.add_credits(
            email, amount=1, reason=CREDIT_REASON,
        )
        if not ok:
            return finish("error", error=f"credit grant failed: {msg}")
        ref.update({"credit_granted": True})
        doc["credit_granted"] = True
        logger.info("karaokehunt: granted 1 credit to new user %s (balance=%s)",
                    email, balance)
        # NOTE: welcome_credits_granted is deliberately left unset — their first
        # sign-in still awards the normal welcome credit, covering a second song.

    # ---- Bonus: does a community karaoke version already exist? ----
    community_url = await _community_version_url(artist, title)

    # ---- Route: job (has credits + under the daily cap) or requests board. ----
    credits = user_service.check_credits(email)
    under_cap = _jobs_today(db) < max(0, settings.karaokehunt_daily_job_cap)

    if credits >= 1 and under_cap:
        result = await _make_job(ref, doc, settings)
    else:
        reason = "no_credits" if credits < 1 else "daily_cap"
        result = await _submit_to_board(email, artist, title, reason)
        if result["status"] == "error":
            return finish("error", error=result["error"])

    # ---- The promised email, with a one-click sign-in link. ----
    email_sent = _send_conversion_email(
        email=email, artist=artist, title=title,
        variant=result["variant"], job_id=result.get("job_id"),
        community_url=community_url,
        used_existing_credit=(not new_user and result["variant"] in ("job", "job_parked")),
    )

    return finish(
        result["outcome"],
        job_id=result.get("job_id"),
        board_request_id=result.get("board_request_id"),
        board_reason=result.get("board_reason"),
        email_sent=email_sent,
    )


def _recent_duplicate_exists(db, doc: Dict[str, Any]) -> bool:
    """True if another intake for the same email+song reached a terminal outcome
    within the dedup window. Equality-only query; window filtered in Python."""
    if not doc.get("dedupe_key"):
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)
    try:
        from google.cloud.firestore_v1 import FieldFilter

        candidates = (
            db.collection(COLLECTION)
            .where(filter=FieldFilter("email", "==", doc["email"]))
            .where(filter=FieldFilter("dedupe_key", "==", doc["dedupe_key"]))
            .stream()
        )
        for snap in candidates:
            other = snap.to_dict()
            if other.get("id") == doc.get("id"):
                continue
            created = other.get("created_at")
            if (other.get("outcome") in TERMINAL_OUTCOMES
                    and created is not None and created >= cutoff):
                return True
    except Exception:  # noqa: BLE001 — dedup is best-effort, never block conversion
        logger.exception("karaokehunt: dedup query failed (continuing)")
    return False


def _jobs_today(db) -> int:
    """How many conversion jobs were created today (UTC) — the daily spend cap."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        from google.cloud.firestore_v1 import FieldFilter

        snaps = (
            db.collection(COLLECTION)
            .where(filter=FieldFilter("created_at", ">=", start))
            .stream()
        )
        return sum(1 for s in snaps
                   if s.to_dict().get("outcome") in ("job_created", "job_parked"))
    except Exception:  # noqa: BLE001 — fail closed: treat as over-cap on error
        logger.exception("karaokehunt: daily-cap query failed (failing closed)")
        return 10**6


async def _community_version_url(artist: str, title: str) -> Optional[str]:
    try:
        from backend.services.karaokenerds_service import check_community_versions

        info = await check_community_versions(artist, title)
        return info.get("best_youtube_url") or None
    except Exception:  # noqa: BLE001 — a bonus link, never worth failing over
        logger.exception("karaokehunt: KN community check failed (continuing)")
        return None


async def _make_job(ref, doc: Dict[str, Any], settings) -> Dict[str, Any]:
    """Create the job as the requester (consumes their credit) and kick off
    search + conservative auto-select + download. Mirrors community_daily_pick."""
    email = doc["email"]
    artist = doc["artist"]
    title = doc["title"]

    job_id = doc.get("job_id")
    if not job_id:
        theme_id = get_theme_service().get_default_theme_id()
        job_create = JobCreate(
            artist=artist,
            title=title,
            theme_id=theme_id,
            enable_cdg=settings.default_enable_cdg,
            enable_txt=settings.default_enable_txt,
            brand_prefix=settings.default_brand_prefix,
            enable_youtube_upload=settings.default_enable_youtube_upload,
            youtube_description=settings.default_youtube_description,
            youtube_description_template=settings.default_youtube_description,
            discord_webhook_url=settings.default_discord_webhook_url,
            dropbox_path=settings.default_dropbox_path,
            gdrive_folder_id=settings.default_gdrive_folder_id,
            user_email=email,
            audio_search_artist=artist,
            audio_search_title=title,
            auto_download=True,
            review_mode="auto",
            backing_preference="auto",
        )
        job_manager = JobManager()
        job = job_manager.create_job(job_create, is_admin=False)
        job_id = job.job_id
        job_manager.update_job(job_id, {
            "audio_search_artist": artist,
            "audio_search_title": title,
            "auto_download": True,
            "state_data.karaokehunt_request_id": doc["id"],
        })
        ref.update({"job_id": job_id})

    started = await _search_and_start(job_id, artist, title)
    if started:
        return {"status": "ok", "outcome": "job_created", "variant": "job",
                "job_id": job_id}
    return {"status": "ok", "outcome": "job_parked", "variant": "job_parked",
            "job_id": job_id}


async def _search_and_start(job_id: str, artist: str, title: str) -> bool:
    """Search + auto-select + trigger download. Returns True when the download
    started; False parks the job in AWAITING_AUDIO_SELECTION for the owner.

    Differs from the daily picker in one deliberate way: ``pick_auto_selection``
    (BEST-CHOICE lossless + filename-verified) instead of ``select_best`` — an
    unattended pick from app-quality input must be confident or defer to the user.
    """
    job_manager = JobManager()
    job = job_manager.get_job(job_id)
    if job and job.status not in (
        JobStatus.PENDING, JobStatus.SEARCHING_AUDIO, JobStatus.AWAITING_AUDIO_SELECTION,
    ):
        return True  # already progressed (idempotent re-entry)

    from backend.workers.bulk_search_worker import _prepare_theme
    if job:
        _prepare_theme(job, job_manager)

    def park(message: str) -> bool:
        job_manager.transition_to_state(
            job_id=job_id, new_status=JobStatus.AWAITING_AUDIO_SELECTION, progress=10,
            message=message, raise_on_invalid=False,
        )
        return False

    job_manager.transition_to_state(
        job_id=job_id, new_status=JobStatus.SEARCHING_AUDIO, progress=5,
        message=f"Searching for: {artist} - {title}", raise_on_invalid=False,
    )

    audio_search_service = AudioSearchService()
    try:
        results = await audio_search_service.search_async(artist, title)
    except (NoResultsError, AudioSearchError) as e:
        logger.warning("karaokehunt: search failed for job %s (%s); parking", job_id, e)
        return park("No automatic audio sources found. Please choose a source.")

    results_dicts = [r.to_dict() for r in results]
    store: Dict[str, Any] = {
        "state_data.audio_search_results": results_dicts,
        "state_data.audio_search_count": len(results_dicts),
    }
    remote_id = getattr(audio_search_service, "last_remote_search_id", None)
    if remote_id:
        store["state_data.remote_search_id"] = remote_id
    job_manager.update_job(job_id, store)

    best_index = pick_auto_selection(results_dicts, search_title=title)
    if best_index is None:
        logger.info("karaokehunt: no confident auto-pick for job %s; parking", job_id)
        return park("Please choose an audio source.")

    from backend.api.routes.audio_search import _validate_and_prepare_selection
    try:
        _validate_and_prepare_selection(job_id=job_id, selection_index=best_index)
    except Exception as e:  # noqa: BLE001
        logger.warning("karaokehunt: validate/prepare failed for job %s: %s", job_id, e)
        return park("Please choose an audio source.")

    return await get_worker_service().trigger_audio_download_worker(job_id)


async def _submit_to_board(email: str, artist: str, title: str, reason: str) -> Dict[str, Any]:
    """Submit to the free community requests board (dedup → up-vote)."""
    try:
        request, already_existed, _, _ = await get_song_request_service().submit_request(
            email, artist, title,
        )
        return {"status": "ok", "outcome": "board_submitted", "variant": "board",
                "board_request_id": request.id, "board_reason": reason,
                "board_already_existed": already_existed}
    except SubmissionRateLimited:
        return {"status": "error",
                "error": f"board submission rate-limited for {email}"}
    except Exception as e:  # noqa: BLE001
        logger.exception("karaokehunt: board submission failed for %s", email)
        return {"status": "error", "error": f"board submission failed: {e}"}


def _send_conversion_email(
    email: str,
    artist: str,
    title: str,
    variant: str,
    job_id: Optional[str],
    community_url: Optional[str],
    used_existing_credit: bool,
) -> bool:
    """Mint a one-click sign-in link and send the conversion email. Best-effort:
    a send failure downgrades to a log line (outcome still records email_sent)."""
    try:
        token = get_user_service().create_admin_login_token(
            email, expiry_hours=LOGIN_LINK_EXPIRY_HOURS,
            purpose=f"karaokehunt_conversion:{job_id or 'board'}",
        )
        frontend_url = os.getenv("FRONTEND_URL", "https://gen.nomadkaraoke.com")
        login_url = f"{frontend_url}/auth/verify?token={token.token}"

        from backend.services.email_service import get_email_service

        return get_email_service().send_karaokehunt_conversion(
            email=email, artist=artist, title=title, variant=variant,
            login_url=login_url, community_url=community_url,
            used_existing_credit=used_existing_credit,
        )
    except Exception:  # noqa: BLE001
        logger.exception("karaokehunt: conversion email failed for %s", email)
        return False
