"""
Regression test for the file_urls / state_data lost-update race.

Reproduces the real-world failure where a `backing_vocals` stem was uploaded to
GCS but vanished from the job's ``file_urls`` because two workers registered
different stems concurrently, each re-persisting the whole map from a stale
snapshot. The fix writes atomic dot-path fields, which Firestore merges
server-side.

To reproduce the race deterministically we hand the *second* writer a stale
snapshot (as if it had read the job before the first writer's value landed) by
patching ``get_job`` for that one call. Against the old copy-and-rewrite
implementation the second write re-persists the stale map and drops the first
writer's value; against the field-path write it cannot. These run on the real
Firestore emulator.

Run with: scripts/run-emulator-tests.sh
"""

import pytest
from copy import deepcopy
from datetime import datetime, UTC
from unittest.mock import patch

from backend.tests.emulator.conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh"
)

if emulators_running():
    from backend.models.job import Job, JobStatus
    from backend.services.job_manager import JobManager
    from backend.services.firestore_service import FirestoreService


class TestFileUrlsNoClobber:
    @pytest.fixture
    def job_manager(self):
        return JobManager()

    @pytest.fixture
    def firestore_service(self):
        return FirestoreService()

    def _create_job(self, firestore_service, job_id, file_urls=None, state_data=None):
        firestore_service.create_job(Job(
            job_id=job_id,
            status=JobStatus.SEPARATING_STAGE1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test",
            title="Test",
            file_urls=file_urls or {},
            state_data=state_data or {},
        ))

    def test_stale_registration_does_not_drop_sibling_stem(self, job_manager, firestore_service):
        """The backing_vocals-vanishing bug: a second stem registration working
        from a stale snapshot must not clobber a stem written in between."""
        job_id = f"noclobber-stems-{datetime.now(UTC).timestamp()}"
        try:
            self._create_job(
                firestore_service, job_id,
                file_urls={"stems": {"lead_vocals": "jobs/x/stems/lead_vocals.flac"}},
            )
            # Snapshot as it looked BEFORE backing_vocals was registered.
            stale = deepcopy(firestore_service.get_job(job_id))

            # Writer 1 registers backing_vocals (lands in Firestore).
            job_manager.update_file_url(job_id, "stems", "backing_vocals", "jobs/x/stems/backing_vocals.flac")

            # Writer 2 registers a different stem but only "sees" the stale
            # snapshot (missing backing_vocals). The field-path write ignores the
            # snapshot contents, so backing_vocals survives.
            with patch.object(job_manager, "get_job", return_value=stale):
                job_manager.update_file_url(job_id, "stems", "instrumental_with_backing", "jobs/x/stems/instrumental_with_backing.flac")

            stems = firestore_service.get_job(job_id).file_urls.get("stems", {})
            assert stems.get("lead_vocals") == "jobs/x/stems/lead_vocals.flac"
            assert stems.get("backing_vocals") == "jobs/x/stems/backing_vocals.flac"
            assert stems.get("instrumental_with_backing") == "jobs/x/stems/instrumental_with_backing.flac"
        finally:
            try:
                firestore_service.delete_job(job_id)
            except Exception:
                pass

    def test_stale_write_does_not_drop_sibling_state_data_key(self, job_manager, firestore_service):
        """Same lost-update guard for state_data: a stale write must not drop a
        sibling key set in between."""
        job_id = f"noclobber-state-{datetime.now(UTC).timestamp()}"
        try:
            self._create_job(firestore_service, job_id, state_data={"lyrics_complete": True})
            stale = deepcopy(firestore_service.get_job(job_id))

            job_manager.update_state_data(job_id, "backing_vocals_analysis", {"has_audible_content": True})

            with patch.object(job_manager, "get_job", return_value=stale):
                job_manager.update_state_data(job_id, "audio_complete", True)

            state = firestore_service.get_job(job_id).state_data
            assert state.get("lyrics_complete") is True
            assert state.get("backing_vocals_analysis") == {"has_audible_content": True}
            assert state.get("audio_complete") is True
        finally:
            try:
                firestore_service.delete_job(job_id)
            except Exception:
                pass
