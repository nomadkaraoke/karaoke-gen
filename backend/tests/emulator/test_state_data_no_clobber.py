"""
Regression tests for the state_data lost-update race in the distribution path.

The video worker used to write its distribution results by re-persisting the
WHOLE state_data map from a snapshot. That could clobber a concurrent write —
most dangerously reverting the ``worker_generation`` supersession fence (an
atomic Increment) or resurrecting the ``visibility_change_in_progress`` guard,
which is the "visibility-recycle-dataloss" surface. The fix writes atomic
dot-path fields (with DELETE_FIELD for the popped guard). These run on the real
Firestore emulator and assert the invariant the fix guarantees; the final test
demonstrates why the old full-map write was unsafe.

Run with: scripts/run-emulator-tests.sh
"""

import pytest
from copy import deepcopy
from datetime import datetime, UTC

from backend.tests.emulator.conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh"
)

if emulators_running():
    from google.cloud.firestore_v1 import DELETE_FIELD, Increment
    from backend.models.job import Job, JobStatus
    from backend.services.job_manager import JobManager
    from backend.services.firestore_service import FirestoreService


class TestDistributionWriteNoClobber:
    @pytest.fixture
    def job_manager(self):
        return JobManager()

    @pytest.fixture
    def firestore_service(self):
        return FirestoreService()

    def _create_job(self, firestore_service, job_id, state_data):
        firestore_service.create_job(Job(
            job_id=job_id,
            status=JobStatus.RENDERING_VIDEO,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            state_data=state_data,
        ))

    def _distribution_write(self, job_manager, job_id):
        """The dot-path payload the video worker now emits on distribution."""
        job_manager.update_job(job_id, {
            'state_data.brand_code': 'NOMAD-1234',
            'state_data.youtube_url': 'https://youtu.be/abc',
            'state_data.dropbox_link': 'https://db/x',
            'state_data.visibility_change_in_progress': DELETE_FIELD,
        })

    def test_distribution_write_preserves_supersession_fence(self, job_manager, firestore_service):
        """A supersession bump landing during distribution must survive: the
        worker_generation Increment is NOT reverted, and unrelated keys stay."""
        job_id = f"dist-fence-{datetime.now(UTC).timestamp()}"
        try:
            self._create_job(firestore_service, job_id, {
                'worker_generation': 1,
                'keep_me': 'value',
                'visibility_change_in_progress': True,
            })

            # A reset/re-trigger bumps the supersession fence mid-distribution.
            job_manager.firestore.update_job(job_id, {'state_data.worker_generation': Increment(1)})
            # The video worker then writes its distribution results.
            self._distribution_write(job_manager, job_id)

            sd = firestore_service.get_job(job_id).state_data
            assert sd.get('worker_generation') == 2, "supersession fence was reverted by the distribution write"
            assert sd.get('keep_me') == 'value', "unrelated sibling key was clobbered"
            assert sd.get('brand_code') == 'NOMAD-1234'
            assert sd.get('youtube_url') == 'https://youtu.be/abc'
            assert 'visibility_change_in_progress' not in sd, "visibility guard was not cleared"
        finally:
            try:
                firestore_service.delete_job(job_id)
            except Exception:
                pass

    def test_old_full_map_write_would_have_clobbered(self, job_manager, firestore_service):
        """Demonstrates the bug: re-persisting the whole state_data map from a
        stale snapshot reverts the concurrent Increment and resurrects the
        cleared guard — which is exactly what the dot-path fix avoids."""
        job_id = f"dist-oldbug-{datetime.now(UTC).timestamp()}"
        try:
            self._create_job(firestore_service, job_id, {
                'worker_generation': 1,
                'visibility_change_in_progress': True,
            })
            stale = deepcopy(firestore_service.get_job(job_id).state_data)

            # Concurrent supersession bump (fence -> 2).
            job_manager.firestore.update_job(job_id, {'state_data.worker_generation': Increment(1)})

            # OLD pattern: rewrite the whole map from the stale snapshot.
            stale['brand_code'] = 'NOMAD-1234'
            stale.pop('visibility_change_in_progress', None)
            job_manager.update_job(job_id, {'state_data': stale})

            sd = firestore_service.get_job(job_id).state_data
            # The full-map write reverted the fence back to the stale value of 1.
            assert sd.get('worker_generation') == 1
        finally:
            try:
                firestore_service.delete_job(job_id)
            except Exception:
                pass
