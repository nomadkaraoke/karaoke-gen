"""
Storage retention: purge regenerable files from old completed jobs.

Policy (docs/archive/2026-10-03-gen-storage-retention-plan.md, approved by
Andrew 2026-10-03). For jobs completed more than ``min_age_days`` (30) ago:

KEEP forever
  - ``input/*`` (incl. ``input/edited.flac``), ``lyrics/**``, ``style/**``,
    ``review_sessions/**``, ``audio_edit*/**``, ``uploads/**``
  - the 720p final (``finals/*(Final Karaoke Lossy 720p).mp4`` and the
    canonical ``finals/lossy_720p_mp4.mp4`` copy) — the one ready-made download
  - ``packages/*`` (CDG/TXT zips), ``screens/*.png|jpg``, ``analysis/*``
  - user-made instrumentals: ``stems/custom_instrumental.*`` and the staged
    copies at the job root (``custom_instrumental.*`` / ``existing_instrumental.*``)
    — they hold user mute edits / uploads, which aren't stored anywhere else
  - ``stems/vocals_derived.*`` (cheap waveform helper of bring-your-own-
    instrumental jobs; separation doesn't recreate it)
  - anything not explicitly on the purge list (unknown files are kept)

PURGE (regenerated on demand by backend/services/regenerate_service.py)
  - every other ``finals/*``, ``videos/*``, ``previews/*``, ``encoded/*``,
    ``quick/*.mp4``, ``screens/*.mov``, ``review-audio/*``
  - separated stems (``stems/*`` apart from the kept ones above) — only when the
    input audio is still in ``jobs/{id}/`` so separation can be re-run

Never touched
  - finalise-only jobs (their videos/screens/stems are user-uploaded SOURCES and
    they can't be regenerated), jobs whose input audio is gone (nothing could be
    re-rendered), jobs mid-edit / re-render / visibility change, jobs with a
    pending deferred YouTube upload, excluded tenants
  - anything outside ``jobs/{job_id}/``

The job walks Firestore (it knows each job's type) rather than using a bucket
lifecycle rule. It is DRY-RUN by default (settings.storage_retention_dry_run):
it then only writes a report (GCS JSON + log summary). Real runs record
``renders_purged_at`` / ``stems_purged_at`` + a ``storage_purge`` manifest on the
job and drop the purged entries from ``file_urls`` so the UI offers
"Regenerate video". Idempotent, batched (``max_jobs``) with a resumable cursor.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

POLICY_VERSION = 1
REPORT_PREFIX = "storage-retention/reports"
STATE_COLLECTION = "storage_retention"
STATE_DOC = "state"
SCAN_PAGE_SIZE = 200
# A purge claims the job for this long; a regenerate/edit started meanwhile waits.
PURGE_IN_PROGRESS_KEY = "storage_purge_in_progress"
PURGE_CLAIM_STALE_SECONDS = 30 * 60

# Purge categories that count as "renders" (vs stems).
RENDER_CATEGORIES = frozenset({
    "finals", "videos", "previews", "encoded", "quick", "screens_mov", "review_audio",
})
STEM_CATEGORIES = frozenset({"stems"})

KEEP = "keep"
PURGE = "purge"

_KEEP_PREFIXES = (
    "input/", "lyrics/", "style/", "review_sessions/", "audio_edit", "uploads/",
    "packages/", "analysis/",
)
_KEPT_STEM_STEMS = ("custom_instrumental", "vocals_derived")
_ROOT_INSTRUMENTAL_STEMS = ("custom_instrumental", "existing_instrumental")


# --------------------------------------------------------------------------------
# Policy (pure functions)
# --------------------------------------------------------------------------------

def classify_file(rel_path: str, finalise_only: bool = False, stems_purgeable: bool = True) -> Tuple[str, str]:
    """Return ``(KEEP|PURGE, category)`` for a path relative to ``jobs/{job_id}/``."""
    rel = rel_path.lstrip("/")
    name = os.path.basename(rel)
    stem = os.path.splitext(name)[0].lower()
    ext = os.path.splitext(name)[1].lower()

    if "/" not in rel:
        if stem in _ROOT_INSTRUMENTAL_STEMS:
            return KEEP, "user_instrumental"
        return KEEP, "other"

    top = rel.split("/", 1)[0]
    if rel.startswith(_KEEP_PREFIXES):
        return KEEP, top

    if top == "finals":
        from backend.services.encoding_interface import classify_encoded_output
        if classify_encoded_output(name) == "mp4_720p":
            return KEEP, "final_720p"
        return PURGE, "finals"
    if top == "videos":
        # Finalise-only: the user uploaded videos/with_vocals.* (a source file).
        return (KEEP, "source_video") if finalise_only else (PURGE, "videos")
    if top == "previews":
        return PURGE, "previews"
    if top == "encoded":
        return PURGE, "encoded"
    if top == "quick":
        return (PURGE, "quick") if ext == ".mp4" else (KEEP, "quick_other")
    if top == "review-audio":
        return PURGE, "review_audio"
    if top == "screens":
        if ext == ".mov":
            return (KEEP, "source_screen") if finalise_only else (PURGE, "screens_mov")
        return KEEP, "screens_image"
    if top == "stems":
        if stem.startswith(_KEPT_STEM_STEMS):
            return KEEP, "user_instrumental" if stem.startswith("custom_instrumental") else "stems_kept"
        if finalise_only:
            return KEEP, "source_stem"
        if not stems_purgeable:
            return KEEP, "stems_unrecoverable"
        return PURGE, "stems"
    return KEEP, "other"


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def completed_at(job: Dict[str, Any]) -> Optional[datetime]:
    """When the job last completed: the newest ``complete`` timeline entry.

    Every re-render / regenerate / edit appends a new one, so the 30-day window
    restarts whenever fresh finals are made. Falls back to ``updated_at``.
    """
    latest = None
    for entry in job.get("timeline") or []:
        if not isinstance(entry, dict) or entry.get("status") != "complete":
            continue
        ts = _parse_ts(entry.get("timestamp"))
        if ts and (latest is None or ts > latest):
            latest = ts
    return latest or _parse_ts(job.get("updated_at"))


def _input_path(job: Dict[str, Any]) -> Optional[str]:
    # Only input_media_gcs_path: it's what separation, render and regenerate read.
    path = job.get("input_media_gcs_path")
    if not isinstance(path, str) or not path:
        return None
    if path.startswith("gs://"):
        path = path.split("/", 3)[-1]
    return path


def regenerable_reason(job: Dict[str, Any]) -> Optional[str]:
    """Why a regenerate of this job couldn't run (so its renders must be kept)."""
    state_data = job.get("state_data") or {}
    if not state_data.get("instrumental_selection"):
        return "no_instrumental_selection"
    if not ((job.get("file_urls") or {}).get("lyrics") or {}).get("corrections"):
        return "no_reviewed_lyrics"
    if not job.get("theme_id"):
        return "no_theme"
    return None


def job_skip_reason(
    job: Dict[str, Any],
    now: datetime,
    min_age_days: int,
    excluded_tenants: Iterable[str] = (),
) -> Optional[str]:
    """Why this job must not be purged (or None if it's a candidate)."""
    if job.get("status") != "complete":
        return "not_complete"
    if job.get("finalise_only"):
        return "finalise_only"
    if job.get("prep_only"):
        return "prep_only"
    if job.get("outputs_deleted_at"):
        return "outputs_deleted"
    tenant = job.get("tenant_id") or ""
    if tenant and tenant in set(excluded_tenants):
        return "excluded_tenant"
    state_data = job.get("state_data") or {}
    for marker in ("visibility_change_in_progress", "admin_rerender", "theme_rerender", "regenerate"):
        if state_data.get(marker):
            return f"active_{marker}"
    if state_data.get("youtube_upload_queued"):
        return "youtube_upload_queued"
    claim = _parse_ts(state_data.get(PURGE_IN_PROGRESS_KEY))
    if claim and (now - claim).total_seconds() < PURGE_CLAIM_STALE_SECONDS:
        return "purge_in_progress"
    done = completed_at(job)
    if done is None:
        return "no_completion_time"
    if now - done < timedelta(days=min_age_days):
        return "too_recent"
    # Only purge what a regenerate could rebuild.
    reason = regenerable_reason(job)
    if reason:
        return f"not_regenerable_{reason}"
    return None


def purge_in_progress(job) -> bool:
    """True while the retention job is deleting this job's files (Job model or dict)."""
    state_data = job.get("state_data") if isinstance(job, dict) else getattr(job, "state_data", None)
    claim = _parse_ts((state_data or {}).get(PURGE_IN_PROGRESS_KEY)) if isinstance(state_data, dict) else None
    return bool(claim) and (datetime.now(timezone.utc) - claim).total_seconds() < PURGE_CLAIM_STALE_SECONDS


PURGE_IN_PROGRESS_MESSAGE = "This track's files are being archived right now. Try again in a few minutes."


@dataclass
class JobPlan:
    job_id: str
    purge: List[Tuple[str, int, str]] = field(default_factory=list)  # (blob path, bytes, category)
    kept_bytes: int = 0
    stems_purgeable: bool = True
    stems_note: Optional[str] = None
    skip_reason: Optional[str] = None

    @property
    def purge_bytes(self) -> int:
        return sum(size for _, size, _ in self.purge)

    def bytes_by_category(self) -> Dict[str, int]:
        out: Counter = Counter()
        for _, size, category in self.purge:
            out[category] += size
        return dict(out)

    @property
    def purges_renders(self) -> bool:
        return any(c in RENDER_CATEGORIES for _, _, c in self.purge)

    @property
    def purges_stems(self) -> bool:
        return any(c in STEM_CATEGORIES for _, _, c in self.purge)


def plan_job(job: Dict[str, Any], listing: List[Tuple[str, int]]) -> JobPlan:
    """Decide what to purge for one (already eligible) job from its GCS listing."""
    job_id = job["job_id"]
    prefix = f"jobs/{job_id}/"
    plan = JobPlan(job_id=job_id)
    names = {name for name, _ in listing}

    # Re-rendering needs the input audio (render worker + separation). If it's
    # gone (e.g. pre-persistence jobs whose input expired from uploads/), nothing
    # could ever be regenerated: keep everything.
    input_path = _input_path(job)
    if not input_path or not input_path.startswith(prefix) or input_path not in names:
        plan.skip_reason = "input_unavailable"
        return plan

    finalise_only = bool(job.get("finalise_only"))
    # Bring-your-own-instrumental jobs never ran separation, so "re-separate"
    # isn't part of their pipeline — leave whatever stems they have.
    if job.get("existing_instrumental_gcs_path"):
        plan.stems_purgeable = False
        plan.stems_note = "existing_instrumental"

    for name, size in listing:
        if not name.startswith(prefix):  # defence in depth: never outside jobs/{id}/
            continue
        action, category = classify_file(
            name[len(prefix):], finalise_only=finalise_only, stems_purgeable=plan.stems_purgeable,
        )
        if action == PURGE:
            plan.purge.append((name, size, category))
        else:
            plan.kept_bytes += size
    return plan


def file_url_deletions(file_urls: Dict[str, Any], purged_paths: Iterable[str]) -> List[str]:
    """Firestore field paths under ``file_urls`` that reference purged blobs."""
    purged = set(purged_paths)

    def norm(value: str) -> str:
        return value.split("/", 3)[-1] if value.startswith("gs://") else value

    out: List[str] = []
    for category, entries in (file_urls or {}).items():
        if isinstance(entries, dict):
            for key, value in entries.items():
                if isinstance(value, str) and norm(value) in purged and _safe_key(category) and _safe_key(key):
                    out.append(f"file_urls.{category}.{key}")
        elif isinstance(entries, str) and norm(entries) in purged and _safe_key(category):
            out.append(f"file_urls.{category}")
    return out


_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_key(key: str) -> bool:
    return bool(_SAFE_KEY.match(str(key)))


# --------------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------------

def _run_in_transaction(db, fn):
    """Run ``fn(transaction)`` in a Firestore transaction (retried on contention)."""
    from google.cloud import firestore
    return firestore.transactional(fn)(db.transaction())


_PROJECTION = [
    "job_id", "status", "timeline", "updated_at", "finalise_only", "prep_only", "theme_id",
    "storage_purge.status", "state_data.instrumental_selection",
    "outputs_deleted_at", "tenant_id", "input_media_gcs_path", "file_urls",
    "existing_instrumental_gcs_path", "renders_purged_at", "stems_purged_at",
    "state_data.visibility_change_in_progress", "state_data.admin_rerender",
    "state_data.theme_rerender", "state_data.regenerate",
    "state_data.youtube_upload_queued", f"state_data.{PURGE_IN_PROGRESS_KEY}",
]


class StorageRetentionService:
    def __init__(self, db=None, storage=None, settings=None, job_manager=None):
        from backend.config import get_settings
        self.settings = settings or get_settings()
        if storage is None:
            from backend.services.storage_service import StorageService
            storage = StorageService()
        self.storage = storage
        if db is None:
            from backend.services.firestore_service import FirestoreService
            db = FirestoreService().db
        self.db = db
        self._job_manager = job_manager

    @property
    def jobs_collection(self):
        return self.db.collection(self.settings.firestore_collection)

    # --- run ----------------------------------------------------------------------

    def run(
        self,
        dry_run: Optional[bool] = None,
        job_ids: Optional[List[str]] = None,
        max_jobs: Optional[int] = None,
        min_age_days: Optional[int] = None,
        include_orphans: bool = False,
        report_path: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Plan (and unless dry-run, execute) one retention pass. Returns the report.

        - ``dry_run`` defaults to settings.storage_retention_dry_run (True).
        - ``job_ids`` scopes the pass to those jobs (no cursor).
        - Dry runs without ``max_jobs`` cover every completed job (full report);
          real runs purge at most ``max_jobs`` (settings default) per pass,
          resuming from the stored cursor.
        """
        started = time.time()
        now = now or datetime.now(timezone.utc)
        dry_run = self.settings.storage_retention_dry_run if dry_run is None else bool(dry_run)
        min_age = self.settings.storage_retention_min_age_days if min_age_days is None else int(min_age_days)
        excluded = [t.strip() for t in (self.settings.storage_retention_excluded_tenants or "").split(",") if t.strip()]
        if max_jobs is None and not dry_run and not job_ids:
            max_jobs = self.settings.storage_retention_max_jobs_per_run
        use_cursor = not job_ids and not dry_run

        report: Dict[str, Any] = {
            "policy_version": POLICY_VERSION,
            "dry_run": dry_run,
            "started_at": now.isoformat(),
            "min_age_days": min_age,
            "max_jobs": max_jobs,
            "excluded_tenants": excluded,
            "scoped_job_ids": job_ids or None,
            "jobs": [],
            "skipped": Counter(),
            "errors": [],
        }
        totals: Counter = Counter()
        processed = 0
        scanned = 0
        last_id = None
        exhausted = True

        for job in self._iter_jobs(job_ids, use_cursor):
            if max_jobs is not None and processed >= max_jobs:
                exhausted = False
                break
            scanned += 1
            last_id = job.get("job_id")
            reason = job_skip_reason(job, now, min_age, excluded)
            if reason is None and self._already_purged(job):
                reason = "already_purged"
            if reason:
                report["skipped"][reason] += 1
                continue
            try:
                listing = self.storage.list_files_with_sizes(f"jobs/{job['job_id']}/")
                plan = plan_job(job, listing)
            except Exception as e:  # noqa: BLE001 - one bad job never stops the pass
                logger.exception(f"[job:{job.get('job_id')}] storage retention: planning failed")
                report["errors"].append({"job_id": job.get("job_id"), "error": str(e)})
                continue
            if plan.skip_reason:
                report["skipped"][plan.skip_reason] += 1
                continue
            if not plan.purge:
                report["skipped"]["nothing_to_purge"] += 1
                continue

            entry = {
                "job_id": plan.job_id,
                "tenant_id": job.get("tenant_id") or None,
                "completed_at": (completed_at(job) or now).isoformat(),
                "purge_bytes": plan.purge_bytes,
                "kept_bytes": plan.kept_bytes,
                "bytes_by_category": plan.bytes_by_category(),
                "stems_kept_reason": plan.stems_note,
                "files": [{"path": p, "bytes": s, "category": c} for p, s, c in plan.purge],
            }
            if not dry_run:
                try:
                    entry["result"] = self._execute(job["job_id"], plan, now, min_age, excluded)
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"[job:{plan.job_id}] storage retention: purge failed")
                    entry["result"] = {"status": "error", "error": str(e)}
                    report["errors"].append({"job_id": plan.job_id, "error": str(e)})
            report["jobs"].append(entry)
            if not dry_run and entry["result"].get("status") != "purged":
                continue
            processed += 1
            for category, size in plan.bytes_by_category().items():
                totals[category] += size

        if use_cursor:
            self._save_cursor(None if exhausted else last_id)

        report["skipped"] = dict(report["skipped"])
        report["summary"] = {
            "jobs_scanned": scanned,
            "jobs_to_purge" if dry_run else "jobs_purged": processed,
            "bytes_total": sum(totals.values()),
            "gib_total": round(sum(totals.values()) / 2**30, 2),
            "gib_by_category": {k: round(v / 2**30, 2) for k, v in sorted(totals.items())},
            "bytes_by_category": dict(totals),
            "duration_seconds": round(time.time() - started, 1),
        }
        if include_orphans:
            try:
                report["orphans"] = self.orphan_job_folders()
            except Exception as e:  # noqa: BLE001
                report["orphans"] = {"error": str(e)}

        report["report_path"] = report_path or self.default_report_path(now, dry_run)
        try:
            self.storage.upload_json(report["report_path"], report)
        except Exception as e:  # noqa: BLE001
            logger.error(f"storage retention: failed to write report {report['report_path']}: {e}")
            report["report_write_error"] = str(e)
        s = report["summary"]
        logger.info(
            f"STORAGE_RETENTION {'DRY-RUN' if dry_run else 'RUN'} complete: scanned={scanned} "
            f"{'would_purge' if dry_run else 'purged'}={processed} jobs, {s['gib_total']} GiB "
            f"by_category={s['gib_by_category']} skipped={report['skipped']} "
            f"errors={len(report['errors'])} report=gs://{self.settings.gcs_bucket_name}/{report['report_path']}"
        )
        return report

    @staticmethod
    def default_report_path(now: datetime, dry_run: bool) -> str:
        return f"{REPORT_PREFIX}/{now.strftime('%Y%m%dT%H%M%SZ')}-{'dry-run' if dry_run else 'run'}.json"

    # --- helpers ------------------------------------------------------------------

    def _already_purged(self, job: Dict[str, Any]) -> bool:
        """Skip the GCS listing for jobs already purged since their last completion."""
        if ((job.get("storage_purge") or {}).get("status")) == "pending":
            return False  # an interrupted purge: finish it
        renders = _parse_ts(job.get("renders_purged_at"))
        if not renders:
            return False
        done = completed_at(job)
        if done and done > renders:
            return False  # regenerated since — eligible again after its own window
        stems = _parse_ts(job.get("stems_purged_at"))
        stems_done = stems is not None or bool(job.get("existing_instrumental_gcs_path"))
        return stems_done

    def _iter_jobs(self, job_ids: Optional[List[str]], use_cursor: bool):
        if job_ids:
            for job_id in job_ids:
                snap = self.jobs_collection.document(job_id).get()
                if snap.exists:
                    data = snap.to_dict() or {}
                    data.setdefault("job_id", snap.id)
                    yield data
            return

        from google.cloud.firestore_v1.base_query import FieldFilter

        cursor = self._load_cursor() if use_cursor else None
        while True:
            query = (
                self.jobs_collection
                .where(filter=FieldFilter("status", "==", "complete"))
                .order_by("__name__")
                .select(_PROJECTION)
                .limit(SCAN_PAGE_SIZE)
            )
            if cursor:
                query = query.start_after({"__name__": self.jobs_collection.document(cursor)})
            docs = list(query.stream())
            for snap in docs:
                data = snap.to_dict() or {}
                data.setdefault("job_id", snap.id)
                data["job_id"] = data.get("job_id") or snap.id
                yield data
            if len(docs) < SCAN_PAGE_SIZE:
                return
            cursor = docs[-1].id

    def _state_ref(self):
        return self.db.collection(STATE_COLLECTION).document(STATE_DOC)

    def _load_cursor(self) -> Optional[str]:
        try:
            snap = self._state_ref().get()
            return (snap.to_dict() or {}).get("cursor_job_id") if snap.exists else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"storage retention: couldn't load cursor: {e}")
            return None

    def _save_cursor(self, job_id: Optional[str]) -> None:
        try:
            self._state_ref().set(
                {"cursor_job_id": job_id, "updated_at": datetime.now(timezone.utc)}, merge=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"storage retention: couldn't save cursor: {e}")

    def _claim(self, job_id: str, plan: "JobPlan", now: datetime, min_age: int, excluded: List[str]) -> bool:
        """Atomically re-check eligibility on the live doc and mark the purge in progress.

        The purge markers (``renders_purged_at`` / ``stems_purged_at``) and a
        PENDING manifest are written HERE, before anything is deleted: if the
        run dies mid-way, regenerate still knows to rebuild (and re-separate),
        and the next pass finishes the job (``storage_purge.status == pending``).
        """
        ref = self.jobs_collection.document(job_id)

        def claim(transaction):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return False
            data = snap.to_dict() or {}
            data.setdefault("job_id", job_id)
            if job_skip_reason(data, now, min_age, excluded):
                return False
            update: Dict[str, Any] = {
                f"state_data.{PURGE_IN_PROGRESS_KEY}": now.isoformat(),
                "storage_purge": {
                    "status": "pending",
                    "started_at": now.isoformat(),
                    "policy_version": POLICY_VERSION,
                    "planned_bytes": plan.purge_bytes,
                    "planned_files": len(plan.purge),
                },
            }
            if plan.purges_renders:
                update["renders_purged_at"] = now
            if plan.purges_stems:
                update["stems_purged_at"] = now
            transaction.update(ref, update)
            return True

        return _run_in_transaction(self.db, claim)

    def _execute(self, job_id: str, plan: JobPlan, now: datetime, min_age: int, excluded: List[str]) -> Dict[str, Any]:
        from google.cloud.firestore_v1 import DELETE_FIELD

        if not self._claim(job_id, plan, now, min_age, excluded):
            return {"status": "skipped", "reason": "no_longer_eligible"}

        ref = self.jobs_collection.document(job_id)
        deleted: List[Tuple[str, int, str]] = []
        failures: List[Dict[str, str]] = []
        try:
            for path, size, category in plan.purge:
                if not path.startswith(f"jobs/{job_id}/"):
                    continue
                try:
                    self.storage.delete_file(path, ignore_missing=True)
                    deleted.append((path, size, category))
                except Exception as e:  # noqa: BLE001
                    failures.append({"path": path, "error": str(e)})

            snap = ref.get()
            file_urls = (snap.to_dict() or {}).get("file_urls") or {} if snap.exists else {}
            update: Dict[str, Any] = {f"state_data.{PURGE_IN_PROGRESS_KEY}": DELETE_FIELD}
            for field_path in file_url_deletions(file_urls, [p for p, _, _ in deleted]):
                update[field_path] = DELETE_FIELD
            by_category: Counter = Counter()
            for _, size, category in deleted:
                by_category[category] += size
            if any(c in RENDER_CATEGORIES for _, _, c in deleted):
                update["renders_purged_at"] = now
            if any(c in STEM_CATEGORIES for _, _, c in deleted):
                update["stems_purged_at"] = now
            update["storage_purge"] = {
                "status": "complete",
                "purged_at": now.isoformat(),
                "policy_version": POLICY_VERSION,
                "bytes": sum(by_category.values()),
                "bytes_by_category": dict(by_category),
                "files": [{"path": p, "bytes": s, "category": c} for p, s, c in deleted],
                "failures": failures,
            }
            ref.update(update)
        except Exception:
            try:
                ref.update({f"state_data.{PURGE_IN_PROGRESS_KEY}": DELETE_FIELD})
            except Exception:  # noqa: BLE001
                pass
            raise

        try:
            from backend.services.firestore_service import log_to_job
            log_to_job(
                job_id, "storage-retention", "INFO",
                f"Storage retention purged {len(deleted)} regenerable files "
                f"({sum(by_category.values()) / 2**20:.0f} MiB); regenerate on demand",
                {"bytes_by_category": dict(by_category), "failures": failures},
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "purged" if deleted else "skipped",
            "deleted": len(deleted),
            "failures": failures,
        }

    # --- orphans (report only, never deleted) -------------------------------------

    def orphan_job_folders(self) -> Dict[str, Any]:
        """``jobs/{id}/`` folders with no Firestore job doc (report only)."""
        known = {snap.id for snap in self.jobs_collection.select(["status"]).stream()}
        sizes: Counter = Counter()
        for name, size in self.storage.list_files_with_sizes("jobs/"):
            parts = name.split("/")
            if len(parts) > 2 and parts[1] not in known:
                sizes[parts[1]] += size
        total = sum(sizes.values())
        return {
            "folders": len(sizes),
            "gib_total": round(total / 2**30, 2),
            "largest": [{"job_id": k, "gib": round(v / 2**30, 2)} for k, v in sizes.most_common(20)],
        }
