"""Tests for encoding-worker job-state persistence to Firestore.

These tests cover the contract documented in
backend/services/gce_encoding/persistence.py:
  - save() mirrors job state with vm_name + ttl fields
  - save() never raises on Firestore errors (best-effort)
  - load_active_jobs() filters by vm_name and skips terminal-status rows
  - mark_orphans_failed_on_startup() flips in-progress rows to failed and
    persists the change (so a poll after restart returns clear failure
    instead of 404)
  - When Firestore creds are unavailable, persister silently disables
    rather than crashing the worker
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.services.gce_encoding.persistence import (
    JobStatePersister,
    get_vm_name,
    _RESTART_FAIL_CODE,
)


def _fake_firestore_client():
    """Return a mock Firestore client that records writes and supports stream()."""
    client = MagicMock()
    # Make .collection().document().set() work
    docs: dict[str, dict] = {}

    def collection(name):
        coll = MagicMock()

        def document(doc_id):
            doc = MagicMock()

            def set(data):
                docs[doc_id] = data

            doc.set.side_effect = set
            return doc

        coll.document.side_effect = document

        def where(*args, **kwargs):
            # Capture the filter so stream() can apply it
            filter_field = args[0] if args else kwargs.get("field_path")
            filter_value = args[2] if len(args) > 2 else kwargs.get("value")

            query = MagicMock()

            def stream():
                for doc_id, data in docs.items():
                    if filter_field == "vm_name" and data.get("vm_name") != filter_value:
                        continue
                    snapshot = MagicMock()
                    snapshot.id = doc_id
                    snapshot.to_dict.return_value = dict(data)
                    yield snapshot

            query.stream.side_effect = stream
            return query

        coll.where.side_effect = where
        coll._docs = docs  # so tests can inspect
        return coll

    client.collection.side_effect = collection
    client._docs = docs
    return client


def test_get_vm_name_uses_env_var(monkeypatch):
    """ENCODING_WORKER_VM_NAME wins over hostname so we tag rows by GCE
    instance name, not the (similar-but-not-guaranteed-equal) hostname."""
    monkeypatch.setenv("ENCODING_WORKER_VM_NAME", "encoding-worker-fallback-a")
    assert get_vm_name() == "encoding-worker-fallback-a"


def test_get_vm_name_falls_back_to_hostname(monkeypatch):
    monkeypatch.delenv("ENCODING_WORKER_VM_NAME", raising=False)
    name = get_vm_name()
    # Whatever the test machine's hostname is, just confirm we got *something*
    assert name and isinstance(name, str)


def test_save_writes_doc_with_vm_name_and_ttl():
    client = _fake_firestore_client()
    persister = JobStatePersister(vm_name="encoding-worker-a", client=client)

    persister.save({
        "job_id": "abc123",
        "status": "running",
        "progress": 42,
    })

    assert "abc123" in client._docs
    doc = client._docs["abc123"]
    assert doc["job_id"] == "abc123"
    assert doc["status"] == "running"
    assert doc["progress"] == 42
    assert doc["vm_name"] == "encoding-worker-a"
    # TTL field set ~7 days in the future (default)
    assert isinstance(doc["expires_at"], datetime)
    assert doc["expires_at"] > datetime.now(timezone.utc) + timedelta(days=6)


def test_save_swallows_firestore_errors():
    """A Firestore failure must NOT propagate — encoding correctness comes
    first; persistence is best-effort observability/recovery."""
    client = MagicMock()
    client.collection.side_effect = RuntimeError("Firestore down")
    persister = JobStatePersister(vm_name="x", client=client)

    # Should not raise
    persister.save({"job_id": "abc", "status": "running"})


def test_save_skips_when_job_id_missing():
    client = _fake_firestore_client()
    persister = JobStatePersister(vm_name="x", client=client)
    persister.save({"status": "pending"})  # no job_id
    assert client._docs == {}


def test_load_active_jobs_filters_by_vm_and_skips_terminal_statuses():
    client = _fake_firestore_client()
    persister = JobStatePersister(vm_name="encoding-worker-a", client=client)

    # Pre-seed Firestore with mixed-VM, mixed-status rows
    persister.save({"job_id": "running-on-a", "status": "running", "progress": 50})
    persister.save({"job_id": "complete-on-a", "status": "complete", "progress": 100})
    persister.save({"job_id": "failed-on-a", "status": "failed"})

    other_vm = JobStatePersister(vm_name="encoding-worker-b", client=client)
    other_vm.save({"job_id": "running-on-b", "status": "running"})

    active = persister.load_active_jobs()
    # Only the in-progress job for VM A
    assert set(active.keys()) == {"running-on-a"}
    # Persistence-only fields stripped
    assert "vm_name" not in active["running-on-a"]
    assert "expires_at" not in active["running-on-a"]
    assert active["running-on-a"]["status"] == "running"


def test_load_active_jobs_returns_empty_on_firestore_error():
    """An unreadable Firestore must NOT prevent the worker from starting up
    — we just lose the recovery capability for this restart."""
    client = MagicMock()
    client.collection.side_effect = RuntimeError("Firestore unreachable")
    persister = JobStatePersister(vm_name="x", client=client)
    assert persister.load_active_jobs() == {}


def test_mark_orphans_failed_on_startup_flips_in_progress_jobs():
    """On worker restart, any job that was running/pending must be marked
    failed in the in-memory jobs dict AND persisted back to Firestore so
    a subsequent poll sees clear failure (not 404, not still-running)."""
    client = _fake_firestore_client()
    persister = JobStatePersister(vm_name="encoding-worker-a", client=client)

    # Simulate state from before the restart
    persister.save({"job_id": "interrupted", "status": "running", "progress": 75})
    persister.save({"job_id": "pending-not-started", "status": "pending", "progress": 0})
    persister.save({"job_id": "old-completed", "status": "complete", "progress": 100})

    jobs: dict[str, dict] = {}
    marked = persister.mark_orphans_failed_on_startup(jobs)

    assert marked == 2  # interrupted + pending-not-started
    assert set(jobs.keys()) == {"interrupted", "pending-not-started"}

    for job_id in ("interrupted", "pending-not-started"):
        assert jobs[job_id]["status"] == "failed"
        assert jobs[job_id]["restart_failure_code"] == _RESTART_FAIL_CODE
        assert _RESTART_FAIL_CODE in jobs[job_id]["error"]
        # Persisted back to Firestore so polls see the failed state
        assert client._docs[job_id]["status"] == "failed"


def test_mark_orphans_failed_on_startup_returns_zero_when_clean():
    """Clean restart (no in-progress rows) must be a fast no-op."""
    client = _fake_firestore_client()
    persister = JobStatePersister(vm_name="encoding-worker-a", client=client)
    # Only completed rows
    persister.save({"job_id": "old-1", "status": "complete"})
    persister.save({"job_id": "old-2", "status": "failed"})

    jobs: dict[str, dict] = {}
    assert persister.mark_orphans_failed_on_startup(jobs) == 0
    assert jobs == {}


def test_persister_self_disables_on_permission_denied():
    """When IAM hasn't been applied yet, the first save() call hits
    PermissionDenied. The persister must log ONCE and disable, not log a
    warning per save (which would spam during normal job processing
    while waiting for the operator to run `pulumi up`).
    """
    class FakePermissionDenied(Exception):
        """Stand-in matched by name in _looks_like_auth_error — avoids
        importing google.api_core just for the type."""

    FakePermissionDenied.__name__ = "PermissionDenied"

    save_count = 0
    client = MagicMock()

    def collection_factory(name):
        coll = MagicMock()

        def document(doc_id):
            nonlocal save_count
            doc = MagicMock()

            def set(data):
                nonlocal save_count
                save_count += 1
                raise FakePermissionDenied("missing roles/datastore.user")

            doc.set.side_effect = set
            return doc

        coll.document.side_effect = document
        return coll

    client.collection.side_effect = collection_factory
    persister = JobStatePersister(vm_name="x", client=client)

    # First save triggers PermissionDenied → persister disables itself
    persister.save({"job_id": "a", "status": "running"})
    assert save_count == 1
    assert persister.enabled is False

    # Subsequent saves are no-ops — must NOT hit Firestore again
    persister.save({"job_id": "b", "status": "running"})
    persister.save({"job_id": "c", "status": "running"})
    assert save_count == 1


def test_persister_disabled_when_firestore_client_construction_fails(monkeypatch):
    """If Firestore client construction raises (e.g. local dev without creds),
    the persister must silently disable instead of breaking the worker.
    All ops become no-ops.

    Patch via the actual `google.cloud.firestore` module attribute that
    persistence._get_client() resolves at runtime — patching by string
    path is unreliable across CI environments where the import order can
    differ.
    """
    import google.cloud.firestore as fs_module
    monkeypatch.setattr(
        fs_module, "Client",
        MagicMock(side_effect=Exception("no creds")),
    )

    persister = JobStatePersister(vm_name="x")
    # First touch triggers init — should disable, not raise
    assert persister.enabled is False
    # All ops safe to call (no-ops)
    persister.save({"job_id": "abc", "status": "running"})
    assert persister.load_active_jobs() == {}
