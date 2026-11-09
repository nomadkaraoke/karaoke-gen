"""
Emulator test: deleting a job archives it (doc + logs + audio-edit sessions).

Run with: ./scripts/run-emulator-tests.sh
"""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests


def emulators_running() -> bool:
    try:
        requests.get("http://127.0.0.1:8080", timeout=1)  # Firestore
        requests.get("http://127.0.0.1:4443", timeout=1)  # GCS (same check as conftest)
        return True
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return False


pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh",
)

os.environ["FIRESTORE_EMULATOR_HOST"] = "127.0.0.1:8080"
os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"


def test_archive_job_copies_doc_and_subcollections_without_ttl():
    from google.cloud import firestore
    from backend.services.firestore_service import FirestoreService

    svc = FirestoreService()
    svc.db = firestore.Client(project="test-project")
    svc.collection = f"test-archive-jobs-{int(time.time() * 1000)}"

    job_id = "arch-job-1"
    job_ref = svc.db.collection(svc.collection).document(job_id)
    job_ref.set({"job_id": job_id, "status": "complete", "artist": "Invented Band", "title": "Made Up Song"})
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    for i in range(3):
        job_ref.collection("logs").document(f"log{i}").set(
            {"message": f"line {i}", "ttl_expiry": expiry, "level": "INFO"}
        )
    job_ref.collection("audio_edit_sessions").document("s1").set({"edits": 2})

    assert svc.archive_job(job_id, archived_by="admin@nomadkaraoke.com", reason="admin_delete") is True

    arch = svc.db.collection(svc.archive_collection).document(job_id)
    data = arch.get().to_dict()
    assert data["artist"] == "Invented Band"
    assert data["archived_by"] == "admin@nomadkaraoke.com"
    assert data["archive_reason"] == "admin_delete"
    assert data["archived_at"] is not None

    logs = {d.id: d.to_dict() for d in arch.collection("archived_logs").stream()}
    assert sorted(logs) == ["log0", "log1", "log2"]
    assert all("ttl_expiry" not in entry for entry in logs.values())
    assert [d.id for d in arch.collection("audio_edit_sessions").stream()] == ["s1"]

    assert svc.archive_job("does-not-exist") is False
