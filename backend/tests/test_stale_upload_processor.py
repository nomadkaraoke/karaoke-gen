"""
Unit tests for the stale upload processor — signed-URL upload jobs whose
browser upload never finished get cancelled (refunding the credit).
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())

from backend.models.job import JobStatus
from backend.workers import stale_upload_processor
from backend.workers.stale_upload_processor import CANCEL_REASON, process_stale_uploads


def _make_job(job_id="job-1", hours_ago=3.0, awaiting_upload=True, tenant_id="", naive=False):
    job = Mock()
    job.job_id = job_id
    job.status = JobStatus.PENDING
    job.tenant_id = tenant_id
    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    job.created_at = created.replace(tzinfo=None) if naive else created
    job.state_data = {'awaiting_upload': True} if awaiting_upload else {}
    return job


@pytest.fixture
def services():
    firestore = Mock()
    job_manager = Mock()
    job_manager.cancel_job.return_value = True
    storage = Mock()
    storage.list_files.return_value = []
    with patch.object(stale_upload_processor, 'FirestoreService', return_value=firestore), \
         patch.object(stale_upload_processor, 'JobManager', return_value=job_manager), \
         patch.object(stale_upload_processor, 'StorageService', return_value=storage):
        yield firestore, job_manager, storage


def test_cancels_stale_awaiting_upload_job(services):
    firestore, job_manager, storage = services
    firestore.list_jobs.return_value = [_make_job(hours_ago=3)]

    result = process_stale_uploads()

    firestore.list_jobs.assert_called_once_with(status=JobStatus.PENDING, limit=500)
    storage.list_files.assert_called_once_with("uploads/job-1/")
    job_manager.cancel_job.assert_called_once_with("job-1", reason=CANCEL_REASON)
    assert result["cancelled"] == 1
    assert result["errors"] == []


def test_handles_naive_created_at(services):
    firestore, job_manager, _ = services
    firestore.list_jobs.return_value = [_make_job(hours_ago=3, naive=True)]

    assert process_stale_uploads()["cancelled"] == 1


def test_skips_recent_job_still_uploading(services):
    firestore, job_manager, _ = services
    firestore.list_jobs.return_value = [_make_job(hours_ago=0.5)]

    assert process_stale_uploads()["cancelled"] == 0
    job_manager.cancel_job.assert_not_called()


def test_skips_pending_jobs_not_awaiting_upload(services):
    """URL/search jobs are also PENDING briefly — never touch them."""
    firestore, job_manager, _ = services
    firestore.list_jobs.return_value = [_make_job(hours_ago=10, awaiting_upload=False)]

    process_stale_uploads()
    job_manager.cancel_job.assert_not_called()


def test_skips_tenant_jobs(services):
    """Tenant bulk uploads use resumable sessions resumable for days."""
    firestore, job_manager, _ = services
    firestore.list_jobs.return_value = [_make_job(hours_ago=10, tenant_id="vocalstar")]

    process_stale_uploads()
    job_manager.cancel_job.assert_not_called()


def test_skips_job_whose_files_landed(services):
    firestore, job_manager, storage = services
    firestore.list_jobs.return_value = [_make_job(hours_ago=10)]
    storage.list_files.return_value = ["uploads/job-1/audio/song.wav"]

    result = process_stale_uploads()

    job_manager.cancel_job.assert_not_called()
    assert result["skipped_has_files"] == 1


def test_one_job_error_does_not_stop_sweep(services):
    firestore, job_manager, storage = services
    firestore.list_jobs.return_value = [_make_job("bad", hours_ago=5), _make_job("good", hours_ago=5)]
    storage.list_files.side_effect = [RuntimeError("gcs down"), []]

    result = process_stale_uploads()

    job_manager.cancel_job.assert_called_once_with("good", reason=CANCEL_REASON)
    assert result["cancelled"] == 1
    assert len(result["errors"]) == 1


def test_query_failure_returns_error(services):
    firestore, job_manager, _ = services
    firestore.list_jobs.side_effect = RuntimeError("firestore down")

    result = process_stale_uploads()

    assert result["status"] == "error"
    job_manager.cancel_job.assert_not_called()
