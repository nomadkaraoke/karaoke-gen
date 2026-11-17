"""
Scheduled daily worker that rewrites existing YouTube video descriptions on the
Nomad Karaoke channel to match the current template
(`Settings.default_youtube_description`, rendered via
`backend.services.youtube_description`).

Design (built to be re-usable, not a one-shot):
- Each run enumerates the whole channel (cheap, read-only) and computes which
  videos are "pending" by comparing each video's LIVE description to the freshly
  rendered template. A video stops being pending once its description matches.
- A template fingerprint is stored in Firestore. Whenever the template changes,
  the fingerprint changes, a fresh "cycle" starts, and every video becomes
  pending again — so re-running this same worker re-drains the whole channel with
  zero manual reset. That's the reuse path for any future template change.
- Work is capped per run by (a) a hard `youtube_backfill_daily_max_updates` and
  (b) whatever daily quota is left after reserving `youtube_backfill_quota_reserve`
  units for the live upload pipeline (via YouTubeQuotaService).
- The uploads playlist silently drops videos (sometimes for weeks), so every
  video ID ever seen is kept in Firestore (`youtube_backfill/known_video_ids`)
  and looked up directly each run. First run seeds it from the quota ledger's
  `desc-backfill:<id>` entries (every video this job has ever rewritten).
- Every video's metadata is mirrored to the `youtube_videos` catalogue (with a
  per-video change history; vanished videos are soft-deleted, never removed).
  See `backend/services/youtube_video_catalog.py`.
- A Postmark email is sent after each run (progress) and once when the channel is
  fully drained (completion).

Triggered by Cloud Scheduler → POST /api/internal/youtube-backfill/run.
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.config import get_settings
from backend.services import youtube_backfill as bf
from backend.services import youtube_video_catalog as catalog
from backend.services.firestore_service import get_firestore_client
from backend.services.youtube_quota_service import get_youtube_quota_service

logger = logging.getLogger(__name__)

STATE_COLLECTION = "youtube_backfill"
STATE_DOC = "state"
KNOWN_IDS_DOC = "known_video_ids"
QUOTA_LEDGER_COLLECTION = "youtube_quota_pending"
_LEDGER_PREFIX = "desc-backfill:"
_MAX_STORED_ERRORS = 20


def _template_fingerprint(settings) -> str:
    raw = (settings.default_youtube_description or "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _fresh_cycle_state(prev: Dict, fingerprint: str, now: datetime) -> Dict:
    return {
        "template_fingerprint": fingerprint,
        "cycle_index": (prev.get("cycle_index", 0) or 0) + 1,
        "cycle_started_at": now,
        "total_updated_in_cycle": 0,
        "completed": False,
        "completed_notified": False,
        "recent_errors": {},
    }


def _seed_known_ids_from_ledger(db) -> List[str]:
    """Every video this job has ever updated, from the daily quota ledgers."""
    ids: List[str] = []
    for doc in db.collection(QUOTA_LEDGER_COLLECTION).stream():
        for item in (doc.to_dict() or {}).get("pending_uploads") or []:
            job_id = (item or {}).get("job_id") or ""
            if job_id.startswith(_LEDGER_PREFIX):
                ids.append(job_id[len(_LEDGER_PREFIX):])
    return list(dict.fromkeys(ids))


def _load_known_ids(db) -> Optional[List[str]]:
    """Known video IDs, seeding from the quota ledger on first use.

    Returns None if seeding failed: the caller must then NOT save the known-IDs
    doc, or the next run would find it and never retry the seed.
    """
    snap = db.collection(STATE_COLLECTION).document(KNOWN_IDS_DOC).get()
    if snap.exists:
        return list((snap.to_dict() or {}).get("ids") or [])
    try:
        seeded = _seed_known_ids_from_ledger(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"YOUTUBE_BACKFILL: could not seed known IDs from quota ledger (will retry): {exc}")
        return None
    logger.info(f"YOUTUBE_BACKFILL: seeded {len(seeded)} known video IDs from the quota ledger.")
    return seeded


def run_backfill_sync(max_updates: Optional[int] = None, dry_run: bool = False) -> Dict:
    """Run one daily drain pass. Safe to call repeatedly (idempotent-ish).

    Returns a summary dict (also useful for tests / manual invocation).
    """
    settings = get_settings()
    if not settings.youtube_backfill_enabled:
        logger.info("YOUTUBE_BACKFILL disabled; skipping run.")
        return {"status": "disabled"}

    creds = bf.load_credentials_from_secret()
    if not creds:
        logger.error("YOUTUBE_BACKFILL: no YouTube credentials available; skipping.")
        return {"status": "no_credentials"}

    youtube = bf.build_youtube(creds)
    db = get_firestore_client()
    state_ref = db.collection(STATE_COLLECTION).document(STATE_DOC)
    snap = state_ref.get()
    state = snap.to_dict() if snap.exists else {}

    now = datetime.now(timezone.utc)
    fingerprint = _template_fingerprint(settings)
    if state.get("template_fingerprint") != fingerprint:
        logger.info("YOUTUBE_BACKFILL: template changed (or first run) — starting new cycle.")
        state = _fresh_cycle_state(state, fingerprint, now)

    # Enumerate channel + compute pending (read-only). The uploads playlist
    # drops videos, so previously-seen IDs are looked up directly too.
    known_ids = _load_known_ids(db)
    channel = bf.enumerate_channel(youtube, known_ids or [])
    entries, videos = channel.entries, channel.videos
    if channel.recovered_ids:
        logger.warning(
            f"YOUTUBE_BACKFILL: uploads playlist omitted {len(channel.recovered_ids)} known videos "
            f"(checked them directly): {channel.recovered_ids[:20]}"
        )
    total_targets = sum(1 for e in entries if e["target"])
    pending = [e for e in entries if e["will_change"]]
    pending_before = len(pending)
    logger.info(f"YOUTUBE_BACKFILL: {total_targets} targets, {pending_before} pending this run.")

    # Compute today's budget from remaining quota, reserving headroom for uploads.
    quota = get_youtube_quota_service()
    try:
        stats = quota.get_quota_stats()
        remaining_units = int(stats.get("units_remaining", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"YOUTUBE_BACKFILL: could not read quota stats: {exc}")
        stats = {}
        remaining_units = 0
    allowed_units = max(0, remaining_units - settings.youtube_backfill_quota_reserve)
    budget = allowed_units // bf.UPDATE_COST
    budget = min(budget, settings.youtube_backfill_daily_max_updates)
    if max_updates is not None:
        budget = min(budget, max_updates)

    updated = 0
    updated_ids = []
    errors: Dict[str, str] = {}
    if not dry_run and pending_before > 0 and budget > 0:
        for entry in pending[:budget]:
            vid = entry["video_id"]
            current_snippet = videos.get(vid, {}).get("snippet", {})
            body = bf.build_update_snippet(
                current_snippet, entry, enrich_tags=settings.youtube_backfill_enrich_tags
            )
            try:
                youtube.videos().update(
                    part="snippet", body={"id": vid, "snippet": body}
                ).execute()
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                errors[vid] = msg
                if "quotaExceeded" in msg:
                    logger.warning("YOUTUBE_BACKFILL: API quota exceeded; stopping run.")
                    break
                logger.error(f"YOUTUBE_BACKFILL: update failed for {vid}: {msg}")
                continue
            updated += 1
            updated_ids.append(vid)
            # Reflect our own rewrite so the catalogue stores the new
            # description and attributes the change to the backfill.
            if vid in videos:
                videos[vid] = {**videos[vid], "snippet": {**current_snippet, **body}}
            try:
                quota.record_upload(f"desc-backfill:{vid}", units=bf.UPDATE_COST)
            except Exception:  # noqa: BLE001
                pass

    pending_after = pending_before - updated
    completed = pending_after == 0

    state["total_updated_in_cycle"] = (state.get("total_updated_in_cycle", 0) or 0) + updated
    state["total_targets"] = total_targets
    state["last_run_at"] = now
    state["last_run_updated"] = updated
    state["last_run_pending_before"] = pending_before
    state["last_run_pending_after"] = pending_after
    state["last_run_budget"] = budget
    state["completed"] = completed
    # Always refresh (clear when this run had none) so stale errors don't linger.
    state["recent_errors"] = dict(list(errors.items())[:_MAX_STORED_ERRORS])

    summary = {
        "status": "ok",
        "dry_run": dry_run,
        "cycle_index": state.get("cycle_index"),
        "total_targets": total_targets,
        "pending_before": pending_before,
        "updated": updated,
        "pending_after": pending_after,
        "budget": budget,
        "remaining_units": remaining_units,
        "completed": completed,
        "errors": len(errors),
        "listed_in_playlist": len(channel.listed_ids),
        "recovered_from_known": len(channel.recovered_ids),
        "gone": len(channel.gone_ids),
    }

    if dry_run:
        logger.info(f"YOUTUBE_BACKFILL (dry-run): {summary}")
        return summary

    just_completed = completed and not state.get("completed_notified")
    try:
        if just_completed:
            _send_completion_email(settings, state, summary)
            state["completed_notified"] = True
        elif updated > 0:
            # Only email on real progress; a quota-starved 0-update run just logs
            # (avoids a daily "0 updated" email while uploads hog the quota).
            _send_progress_email(settings, state, summary, stats, errors)
        elif pending_before > 0 and budget == 0:
            logger.info(
                "YOUTUBE_BACKFILL: no budget this run (quota reserved for uploads); "
                f"{pending_before} still pending."
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"YOUTUBE_BACKFILL: failed to send report email: {exc}")

    state_ref.set(state)
    # Remember every video ever seen. Gone ones are kept (soft-deleted in the
    # catalogue) and re-checked, so a reappearance is noticed. Skipped when the
    # first-time seed failed, so the next run retries it.
    if known_ids is not None:
        db.collection(STATE_COLLECTION).document(KNOWN_IDS_DOC).set(
            {"ids": sorted(set(videos) | set(known_ids)), "updated_at": now}
        )
    # Best-effort mirror of every video's metadata; never fails the run.
    try:
        catalog_stats = catalog.sync_catalog(
            db,
            videos,
            entries,
            listed_ids=channel.listed_ids,
            gone_ids=channel.gone_ids,
            now=now,
            backfill_updated_ids=updated_ids,
        )
        summary["catalog"] = catalog_stats
        logger.info(f"YOUTUBE_BACKFILL: catalogue synced: {catalog_stats}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"YOUTUBE_BACKFILL: catalogue sync failed: {exc}")
    logger.info(f"YOUTUBE_BACKFILL: run complete: {summary}")
    return summary


# ----------------------------------------------------------------------------
# Email reporting
# ----------------------------------------------------------------------------
def _email_service():
    from backend.services.email_service import EmailService

    return EmailService()


def _send_progress_email(settings, state, summary, stats, errors):
    done = summary["total_targets"] - summary["pending_after"]
    err_html = ""
    if errors:
        rows = "".join(f"<li><code>{vid}</code>: {msg}</li>" for vid, msg in list(errors.items())[:10])
        err_html = f"<p><b>{len(errors)} error(s) this run:</b></p><ul>{rows}</ul>"
    html = f"""
    <h2>YouTube descriptions — daily progress</h2>
    <p>Cycle #{state.get('cycle_index')} · rewriting the channel to the current template.</p>
    <ul>
      <li><b>Updated this run:</b> {summary['updated']}</li>
      <li><b>Remaining (pending):</b> {summary['pending_after']}</li>
      <li><b>Done so far:</b> {done} / {summary['total_targets']}</li>
      <li><b>Run budget:</b> {summary['budget']} updates (quota remaining ~{summary['remaining_units']} units)</li>
      <li><b>Updated in this cycle so far:</b> {state.get('total_updated_in_cycle')}</li>
      <li><b>Missing from YouTube's uploads list (checked directly):</b> {summary.get('recovered_from_known', 0)}</li>
    </ul>
    {err_html}
    <p>Another batch runs tomorrow until the channel is fully drained.</p>
    """
    _email_service().send_email(
        to_email=settings.youtube_backfill_report_email,
        subject=f"[YouTube Descriptions] {summary['updated']} updated, {summary['pending_after']} remaining",
        html_content=html,
    )


def _send_completion_email(settings, state, summary):
    html = f"""
    <h2>✅ YouTube description rewrite complete</h2>
    <p>Cycle #{state.get('cycle_index')} is done — every eligible video on the
    channel now matches the current template.</p>
    <ul>
      <li><b>Total targets:</b> {summary['total_targets']}</li>
      <li><b>Updated in this cycle:</b> {state.get('total_updated_in_cycle')}</li>
      <li><b>Final pending:</b> {summary['pending_after']}</li>
    </ul>
    <p>The daily job will now idle until the template changes again (which will
    automatically kick off a fresh rewrite cycle).</p>
    """
    _email_service().send_email(
        to_email=settings.youtube_backfill_report_email,
        subject="[YouTube Descriptions] ✅ Back-catalogue rewrite complete",
        html_content=html,
    )
