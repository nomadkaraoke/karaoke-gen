"""
credits_charged must survive the state_data writes that follow create_job.

create-from-search (and sibling create paths) used to replace the whole
state_data map with the search results right after create_job, wiping
credits_charged / payment_bypassed. They now write dotted ``state_data.<key>``
fields. These run on the real Firestore emulator and assert that dotted updates
merge (keep the sibling billing keys) while a bare ``state_data`` write would not.

Run with: scripts/run-emulator-tests.sh
"""

import pytest
from datetime import datetime, UTC

from backend.tests.emulator.conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh"
)

if emulators_running():
    from backend.models.job import Job, JobStatus
    from backend.services.job_manager import JobManager
    from backend.services.firestore_service import FirestoreService
    from backend.services.job_credits import get_credits_charged


class TestCreditsChargedNoClobber:
    @pytest.fixture
    def job_manager(self):
        return JobManager()

    @pytest.fixture
    def firestore_service(self):
        return FirestoreService()

    def _create_job(self, firestore_service, job_id, state_data):
        firestore_service.create_job(Job(
            job_id=job_id,
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            user_email="buyer@example.com",
            state_data=state_data,
        ))

    def test_dotted_search_results_write_keeps_credits_charged(self, job_manager, firestore_service):
        job_id = f"cc-keep-{datetime.now(UTC).timestamp()}"
        try:
            self._create_job(firestore_service, job_id, {'credits_charged': 1})

            # Exactly the shape create-from-search now writes.
            job_manager.update_job(job_id, {
                'state_data.audio_search_results': [{'index': 0, 'title': 'Waterloo'}],
                'state_data.audio_search_count': 1,
                'state_data.remote_search_id': 'remote-1',
            })

            job = firestore_service.get_job(job_id)
            assert job.state_data['credits_charged'] == 1
            assert job.state_data['audio_search_count'] == 1
            assert get_credits_charged(job) == 1
        finally:
            firestore_service.delete_job(job_id)

    def test_dotted_write_keeps_payment_bypassed(self, job_manager, firestore_service):
        job_id = f"cc-bypass-{datetime.now(UTC).timestamp()}"
        try:
            self._create_job(firestore_service, job_id, {'credits_charged': 0, 'payment_bypassed': True})
            job_manager.update_job(job_id, {'state_data.audio_search_count': 2})

            sd = firestore_service.get_job(job_id).state_data
            assert sd['credits_charged'] == 0
            assert sd['payment_bypassed'] is True
        finally:
            firestore_service.delete_job(job_id)

    def test_bare_state_data_write_would_wipe_it(self, job_manager, firestore_service):
        """Documents why the old whole-map write was the bug."""
        job_id = f"cc-wipe-{datetime.now(UTC).timestamp()}"
        try:
            self._create_job(firestore_service, job_id, {'credits_charged': 1})
            job_manager.update_job(job_id, {'state_data': {'audio_search_count': 1}})

            assert 'credits_charged' not in firestore_service.get_job(job_id).state_data
        finally:
            firestore_service.delete_job(job_id)
