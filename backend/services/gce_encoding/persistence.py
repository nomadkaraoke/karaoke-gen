"""Firestore persistence for the encoding worker's in-memory job state.

Why this exists
---------------
The encoding worker holds job state in a process-local `dict`. When the
systemd service restarts (deploy, crash, OOM, manual restart) the dict is
empty, so polls from the Cloud Run side return 404 and the orchestrator
either fails the user's job or triggers wasteful re-renders.

This module mirrors per-job state to Firestore so that:

1. Polls after a restart return the *last known* status (typically `failed`
   with `code: encoding_worker_restart`), not 404 — orchestrator can react
   intelligently instead of guessing.
2. On startup, the worker reads back any jobs it had in flight and marks
   them failed with that restart code, so the in-memory dict and Firestore
   stay in sync after a crash.

Scope
-----
Each row is keyed by `job_id` and tagged with `vm_name`. Loads are filtered
to the current VM — VMs don't see each other's jobs. (We never have two
VMs simultaneously holding the same `job_id` — Cloud Run only submits to
one VM at a time, and on restart that same VM is the only one that owned
the doc.)

A Firestore field-level TTL on `expires_at` cleans completed/failed rows
automatically after 7 days; we set it on every write.

Cost / latency
--------------
Writes are best-effort. A Firestore failure logs a warning but does NOT
fail the encoding job — persistence is observability/recovery, not
correctness. The hot path stays in-memory.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


_RESTART_FAIL_CODE = "encoding_worker_restart"
_TERMINAL_STATUSES = frozenset({"complete", "failed"})
_DEFAULT_TTL_DAYS = 7
_COLLECTION = "encoding_worker_jobs"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_vm_name() -> str:
    """Return the VM's identifier.

    Prefer the GCE instance name (set by infrastructure/encoding-worker/
    startup.sh as `ENCODING_WORKER_VM_NAME`), falling back to the OS
    hostname so this still works in tests/local dev.
    """
    return (
        os.environ.get("ENCODING_WORKER_VM_NAME")
        or socket.gethostname()
        or "unknown"
    )


class JobStatePersister:
    """Mirror in-memory job state to Firestore.

    Lazily initializes the Firestore client so import-time has zero cost
    and tests/local-dev that don't have GCP creds don't blow up.

    All operations are thread-safe — the encoding worker uses a
    ThreadPoolExecutor and may call save() from multiple worker threads.
    """

    def __init__(
        self,
        *,
        vm_name: Optional[str] = None,
        collection: str = _COLLECTION,
        ttl_days: int = _DEFAULT_TTL_DAYS,
        client: Any = None,  # for tests
    ) -> None:
        self.vm_name = vm_name or get_vm_name()
        self.collection = collection
        self.ttl_days = ttl_days
        self._client = client
        self._client_lock = threading.Lock()
        self._enabled: Optional[bool] = None  # None = not yet probed

    def _get_client(self):
        """Return a Firestore client or None if creds are missing."""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                from google.cloud import firestore  # type: ignore
                self._client = firestore.Client()
                self._enabled = True
                logger.info(
                    "JobStatePersister initialized (vm=%s, collection=%s)",
                    self.vm_name, self.collection,
                )
            except Exception as exc:
                # Don't fail the worker if creds aren't available — just
                # disable persistence and log loudly so it's discoverable.
                self._enabled = False
                logger.warning(
                    "JobStatePersister disabled (Firestore unavailable): %s",
                    exc,
                )
            return self._client

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            self._get_client()
        return bool(self._enabled)

    def save(self, job_state: dict) -> None:
        """Persist a single job's state.

        Adds `vm_name`, `updated_at`, and `expires_at` (TTL) fields. Never
        raises — Firestore failures are logged and swallowed because
        encoding is the source of truth and persistence is best-effort.

        On the first PermissionDenied (typically: IAM not yet applied for
        the encoding worker SA), self-disables to avoid logging a warning
        on every save call. Caller can re-enable by re-instantiating after
        IAM lands.
        """
        client = self._get_client()
        if client is None:
            return
        job_id = job_state.get("job_id")
        if not job_id:
            logger.warning("JobStatePersister.save called without job_id; skipping")
            return
        try:
            doc = {
                **job_state,
                "vm_name": self.vm_name,
                "updated_at": _now_utc(),
                "expires_at": _now_utc() + timedelta(days=self.ttl_days),
            }
            client.collection(self.collection).document(job_id).set(doc)
        except Exception as exc:
            # If this is a permanent auth failure (IAM not applied yet),
            # log once and self-disable so we don't spam ERROR per save.
            # Other transient errors are logged at WARNING per occurrence.
            if self._looks_like_auth_error(exc):
                if self._enabled is not False:  # only log on transition
                    logger.warning(
                        "JobStatePersister disabling: Firestore auth failed "
                        "(likely IAM not yet applied to encoding-worker-sa: "
                        "needs roles/datastore.user). Subsequent saves will "
                        "be no-ops. Error: %r",
                        exc,
                    )
                self._enabled = False
                self._client = None
                return
            logger.warning(
                "[job:%s] Failed to persist job state to Firestore: %r",
                job_id, exc,
            )

    @staticmethod
    def _looks_like_auth_error(exc: BaseException) -> bool:
        """Heuristic — google.api_core.exceptions.PermissionDenied or 403/401."""
        type_name = type(exc).__name__
        if type_name in ("PermissionDenied", "Unauthenticated", "Forbidden"):
            return True
        msg = str(exc).lower()
        return any(s in msg for s in ("permission_denied", "permissiondenied",
                                      "403", "401", "unauthenticated",
                                      "missing or insufficient permissions"))

    def load_active_jobs(self) -> dict[str, dict]:
        """Return any non-terminal jobs persisted for this VM.

        Used at startup to recover state from before the restart. Terminal
        jobs (complete/failed) are also persisted but not returned here —
        their docs serve only post-restart status polls.

        Self-disables on auth errors (same pattern as save).
        """
        client = self._get_client()
        if client is None:
            return {}
        try:
            query = (
                client.collection(self.collection)
                .where("vm_name", "==", self.vm_name)
            )
            recovered: dict[str, dict] = {}
            for doc_snapshot in query.stream():
                data = doc_snapshot.to_dict() or {}
                status = data.get("status")
                if status in _TERMINAL_STATUSES:
                    continue
                # Strip the persistence-only fields before handing back
                for k in ("vm_name", "updated_at", "expires_at"):
                    data.pop(k, None)
                recovered[doc_snapshot.id] = data
            return recovered
        except Exception as exc:
            if self._looks_like_auth_error(exc):
                if self._enabled is not False:
                    logger.warning(
                        "JobStatePersister disabling: Firestore auth failed "
                        "during load_active_jobs (likely IAM not applied). "
                        "Error: %r", exc,
                    )
                self._enabled = False
                self._client = None
                return {}
            logger.warning(
                "JobStatePersister.load_active_jobs failed: %r", exc,
            )
            return {}

    def mark_orphans_failed_on_startup(self, jobs: dict[str, dict]) -> int:
        """Mark any in-progress jobs as failed with the restart code.

        Called at worker startup. Mutates `jobs` in place AND persists each
        change so polls from the orchestrator see consistent state.

        The work_dir for an interrupted job is gone (tmpfs / temp dir wiped
        by systemd restart), so we cannot resume — the only safe thing is
        to surface a clear failure so the caller can decide to retry.

        Returns the number of jobs marked failed.
        """
        active = self.load_active_jobs()
        if not active:
            return 0
        marked = 0
        for job_id, job_state in active.items():
            job_state["status"] = "failed"
            job_state["error"] = (
                f"Encoding worker restarted mid-job (code: {_RESTART_FAIL_CODE}). "
                "Worker process restart wiped in-memory state and the temp "
                "work directory; resubmit to retry."
            )
            job_state["restart_failure_code"] = _RESTART_FAIL_CODE
            jobs[job_id] = job_state
            self.save(job_state)
            marked += 1
            logger.warning(
                "[job:%s] Marked failed at startup due to encoding worker restart "
                "(was status=%s, progress=%s)",
                job_id,
                job_state.get("_pre_restart_status", "unknown"),
                job_state.get("progress"),
            )
        return marked
