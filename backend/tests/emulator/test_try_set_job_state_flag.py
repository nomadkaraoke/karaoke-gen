"""
FirestoreService.try_set_job_state_flag is an atomic test-and-set: of several
concurrent callers exactly one wins. change_to_public relies on it to claim
visibility_change_in_progress before deleting anything.

Run with: scripts/run-emulator-tests.sh
"""

import threading
import uuid
from datetime import datetime, UTC

import pytest

from backend.tests.emulator.conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh"
)

if emulators_running():
    from backend.models.job import Job, JobStatus
    from backend.services.firestore_service import FirestoreService


def _create_job(service, state_data):
    job_id = f"tas-{uuid.uuid4().hex[:8]}"
    service.create_job(Job(
        job_id=job_id,
        status=JobStatus.COMPLETE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test",
        title="Test",
        state_data=state_data,
    ))
    return job_id


def test_sets_flag_once():
    service = FirestoreService()
    job_id = _create_job(service, {})
    assert service.try_set_job_state_flag(job_id, "visibility_change_in_progress") is True
    assert service.try_set_job_state_flag(job_id, "visibility_change_in_progress") is False
    assert service.get_job(job_id).state_data["visibility_change_in_progress"] is True


def test_already_set_flag_is_not_claimed():
    service = FirestoreService()
    job_id = _create_job(service, {"visibility_change_in_progress": True})
    assert service.try_set_job_state_flag(job_id, "visibility_change_in_progress") is False


def test_missing_job_is_not_claimed():
    assert FirestoreService().try_set_job_state_flag("no-such-job", "visibility_change_in_progress") is False


def test_exactly_one_concurrent_caller_wins():
    service = FirestoreService()
    job_id = _create_job(service, {})
    barrier = threading.Barrier(4)
    results = []

    def attempt():
        barrier.wait()
        try:
            results.append(FirestoreService().try_set_job_state_flag(job_id, "visibility_change_in_progress"))
        except Exception as e:  # contention abort after retries: a loss, never a second winner
            results.append(e)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4
    assert [r for r in results if r is True] == [True]
