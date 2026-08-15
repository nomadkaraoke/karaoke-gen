"""
Tests for the encoding worker's two-lane execution model (gce_encoding/main.py).

The worker serializes heavy jobs (renders/encodes) into a single lane so it can
never OOM from running multiple 4K ffmpeg encodes at once (incident 2026-08-15:
3 concurrent encodes OOM-killed the 32 GB fallback worker). Light jobs (previews,
wheel installs) run on their own lane. `/status` also exposes a queue position so
the client can wait through a deep queue.

Importing main.py constructs a storage.Client() at module load, so we patch it.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def worker():
    """Import the GCE worker module with GCS creds mocked out."""
    with patch("google.cloud.storage.Client", return_value=MagicMock()):
        sys.modules.pop("backend.services.gce_encoding.main", None)
        import backend.services.gce_encoding.main as m
        yield m
        sys.modules.pop("backend.services.gce_encoding.main", None)


class TestHeavyLaneSerialization:
    def test_heavy_concurrency_defaults_to_one(self, worker):
        # One heavy ffmpeg at a time → OOM-proof on any machine type.
        assert worker.HEAVY_CONCURRENCY == 1
        assert worker.heavy_executor._max_workers == 1

    def test_heavy_and_light_are_separate_lanes(self, worker):
        # Previews / wheel installs must not queue behind a multi-minute encode.
        assert worker.heavy_executor is not worker.light_executor
        assert worker.light_executor._max_workers >= 2


class TestHeavyQueuePosition:
    def _reset_jobs(self, worker, entries):
        worker.jobs.clear()
        for job_id, kind, status in entries:
            worker.jobs[job_id] = {
                "job_id": job_id,
                "status": status,
                "progress": 0,
                "kind": kind,
            }

    def test_orders_heavy_jobs_ignoring_previews_and_terminal(self, worker):
        # Insertion order == submission order (dict is ordered).
        self._reset_jobs(worker, [
            ("e0done", "encode", "complete"),   # terminal — not counted, position None
            ("e1", "encode", "running"),        # front of the lane
            ("e2", "render", "pending"),        # one heavy job (e1) ahead
            ("p1", "preview", "running"),        # light lane — never counted
            ("e3", "encode", "pending"),        # e1 + e2 ahead (preview + terminal excluded)
        ])
        assert worker._heavy_queue_position("e0done") is None
        assert worker._heavy_queue_position("e1") == 0
        assert worker._heavy_queue_position("e2") == 1
        assert worker._heavy_queue_position("p1") is None
        assert worker._heavy_queue_position("e3") == 2

    def test_preview_has_no_queue_position(self, worker):
        self._reset_jobs(worker, [("p1", "preview", "running")])
        assert worker._heavy_queue_position("p1") is None

    def test_unknown_job_is_none(self, worker):
        worker.jobs.clear()
        assert worker._heavy_queue_position("nope") is None


class TestJobStatusModel:
    def test_new_fields_present_and_optional(self, worker):
        # Old-shaped payloads still validate (fields default to None)...
        s = worker.JobStatus(job_id="j", status="running", progress=10)
        assert s.restart_failure_code is None
        assert s.queue_position is None
        # ...and the new fields round-trip.
        s2 = worker.JobStatus(
            job_id="j", status="failed", progress=0,
            restart_failure_code="encoding_worker_restart", queue_position=3,
        )
        assert s2.restart_failure_code == "encoding_worker_restart"
        assert s2.queue_position == 3

    def test_extra_keys_like_kind_are_ignored(self, worker):
        # get_job_status spreads the whole job dict (which now carries "kind").
        s = worker.JobStatus(
            **{"job_id": "j", "status": "pending", "progress": 0, "kind": "encode"},
            queue_position=worker._heavy_queue_position("missing"),
        )
        assert s.status == "pending"
