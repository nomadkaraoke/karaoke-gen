"""
Emulator integration tests for AccountDeletionService.

Real Firestore and GCS emulators, so the parts a fake can't prove are real:
dotted-path DELETE_FIELD updates on job docs, the ``targets.kjbox`` query behind
the kjbox queue, the two-equality made-for-you query, the deleted_users hash
lookup, real object deletes, and the deletion_id transaction under concurrency.
Only decide, RevenueCat, Apple, Dropbox and job cancellation are faked.
Run with: scripts/run-emulator-tests.sh
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.cloud import firestore, storage

from backend.services.account_deletion_service import (
    AccountDeletionService,
    email_sha256,
    is_deleted_email,
)

from .conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh",
)

JOBS = "account-deletion-test-jobs"


@pytest.fixture
def db():
    return firestore.Client()


@pytest.fixture
def bucket():
    client = storage.Client()
    try:
        client.create_bucket("test-bucket")
    except Exception:
        pass  # already exists
    return client.bucket("test-bucket")


def _service(db, bucket, cancelled=None):
    decide = MagicMock()
    decide.delete_user.return_value = {"deleted": {}}

    def cancel(job_id):
        (cancelled if cancelled is not None else []).append(job_id)
        db.collection(JOBS).document(job_id).update({"status": "cancelled"})
        return True

    return AccountDeletionService(
        db,
        jobs_collection=JOBS,
        cancel_job=cancel,
        bucket=bucket,
        dropbox=MagicMock(),
        decide=decide,
        apple_revoke=lambda code: True,
        settings=SimpleNamespace(revenuecat_secret_api_key="", firestore_collection=JOBS),
    )


def _seed(db, bucket):
    email = f"del-{uuid.uuid4().hex[:8]}@example.com"
    db.collection("gen_users").document(email).set({
        "email": email, "role": "user", "credits": 2,
        "credit_transactions": [{"id": "t1", "amount": 2, "reason": "purchase"}],
    })
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    db.collection(JOBS).document(job_id).set({
        "job_id": job_id, "status": "awaiting_review", "user_email": email,
        "creation_ip": "203.0.113.9",
        "request_metadata": {"client_ip": "203.0.113.9", "environment": "test"},
        "state_data": {"customer_name": "Sam", "notification_email": email, "brand_code": "NOMAD-1"},
        "output_files": {"mp4": f"jobs/{job_id}/finals/out.mp4"},
    })
    mfy_id = f"mfy-{uuid.uuid4().hex[:8]}"
    db.collection(JOBS).document(mfy_id).set({
        "job_id": mfy_id, "status": "complete", "user_email": "admin@nomadkaraoke.com",
        "made_for_you": True, "customer_email": email, "customer_notes": "invented note", "state_data": {},
    })
    for name in (f"uploads/{job_id}/audio/mix.flac", f"jobs/{job_id}/input/audio/mix.flac",
                 f"jobs/{job_id}/finals/out.mp4", f"jobs/{job_id}/stems/vocals.flac"):
        bucket.blob(name).upload_from_string(b"x")
    return email, job_id, mfy_id


def _names(bucket, prefix):
    return {b.name for b in bucket.list_blobs(prefix=prefix)}


def test_full_deletion_against_emulators(db, bucket):
    cancelled = []
    svc = _service(db, bucket, cancelled)
    email, job_id, mfy_id = _seed(db, bucket)
    session_id = f"sess-{uuid.uuid4().hex}"
    db.collection("sessions").document(session_id).set({"user_email": email, "is_active": True})

    assert svc.preview(email) == {"credits": 2, "jobs_in_progress": 1, "blocked": None}
    summary = svc.delete(email)
    deletion_id = summary["deletion_id"]

    assert cancelled == [job_id]
    assert not db.collection("gen_users").document(email).get().exists
    assert not db.collection("sessions").document(session_id).get().exists

    job = db.collection(JOBS).document(job_id).get().to_dict()
    assert job["user_email"] == f"deleted:{deletion_id}"
    assert job["state_data"] == {"brand_code": "NOMAD-1"}
    assert job["request_metadata"] == {"environment": "test"}
    assert job["creation_ip"] is None
    assert job["output_files"] == {"mp4": f"jobs/{job_id}/finals/out.mp4"}

    mfy = db.collection(JOBS).document(mfy_id).get().to_dict()
    assert mfy["customer_email"] == f"deleted:{deletion_id}" and mfy["customer_notes"] is None

    assert _names(bucket, f"uploads/{job_id}/") == set()
    assert _names(bucket, f"jobs/{job_id}/input/") == set()
    assert _names(bucket, f"jobs/{job_id}/") == {f"jobs/{job_id}/finals/out.mp4", f"jobs/{job_id}/stems/vocals.flac"}

    tomb = db.collection("deleted_users").document(deletion_id).get().to_dict()
    assert tomb["email_sha256"] == email_sha256(email) and email not in repr(tomb)
    assert is_deleted_email(db, email) and not is_deleted_email(db, "never@example.com")

    pending = [e["id"] for e in svc.list_kjbox_pending()]
    assert email_sha256(email) in pending
    assert svc.ack_kjbox(email_sha256(email)) is True
    assert email_sha256(email) not in [e["id"] for e in svc.list_kjbox_pending()]
    queued = db.collection("account_deletions").document(email_sha256(email)).get().to_dict()
    assert queued["targets"] == {"kjbox": "done"}


def test_in_progress_made_for_you_order_refuses(db, bucket):
    svc = _service(db, bucket)
    email, _, mfy_id = _seed(db, bucket)
    db.collection(JOBS).document(mfy_id).update({"status": "rendering_video"})
    assert svc.preview(email)["blocked"] == "order_in_progress"


def test_concurrent_attempts_share_one_deletion_id(db, bucket):
    svc = _service(db, bucket)
    email, _, _ = _seed(db, bucket)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: svc._ensure_deletion_id(email), range(4)))
    assert len(set(ids)) == 1
    assert db.collection("gen_users").document(email).get().to_dict()["deletion_id"] == ids[0]
