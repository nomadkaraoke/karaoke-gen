"""
Tests for the encoding worker's two-lane execution model (gce_encoding/main.py).

The worker serializes heavy jobs (renders/encodes) into a single lane so it can
never OOM from running multiple 4K ffmpeg encodes at once (incident 2026-08-15:
3 concurrent encodes OOM-killed the 32 GB fallback worker). Light jobs (previews,
wheel installs) run on their own lane. `/status` also exposes a queue position so
the client can wait through a deep queue.

Importing main.py constructs a storage.Client() at module load, so we patch it.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def worker():
    """Import the GCE worker module with GCS creds mocked out.

    Clear ENCODING_HEAVY_CONCURRENCY during import so the default-behavior
    assertions hold regardless of the CI environment.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ENCODING_HEAVY_CONCURRENCY", None)
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


class TestHeavyConcurrencyParsing:
    @pytest.mark.parametrize("val,expected", [
        ("1", 1), ("2", 2), ("4", 4),   # valid
        ("0", 1), ("-3", 1),            # too low → clamp up to 1
        ("9", 4),                        # too high → clamp down to 4
        ("abc", 1), ("", 1),            # malformed → default 1
    ])
    def test_clamped_to_sane_range(self, worker, val, expected):
        with patch.dict("os.environ", {"ENCODING_HEAVY_CONCURRENCY": val}):
            assert worker._heavy_concurrency() == expected

    def test_missing_env_defaults_to_one(self, worker):
        import os
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENCODING_HEAVY_CONCURRENCY", None)
            assert worker._heavy_concurrency() == 1


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


_LATEST_WHEEL_URI = (
    "gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-0.194.0-py3-none-any.whl"
)


def _fake_subprocess(counter):
    """subprocess.run stub that dispatches by command and counts cp/pip calls."""
    from types import SimpleNamespace

    def _run(cmd, **kwargs):
        head = cmd[:2]
        if head == ["gsutil", "ls"]:
            return SimpleNamespace(returncode=0, stdout=_LATEST_WHEEL_URI + "\n", stderr="")
        if head == ["gsutil", "cp"]:
            counter["cp"] += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "pip" in cmd:
            counter["pip"] += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return _run


class TestEnsureLatestWheelConcurrency:
    """ensure_latest_wheel must be idempotent + race-free: it runs on the light
    lane (>1 thread) at the start of every job, so concurrent calls used to
    clobber the shared /tmp wheel path and venv (incident 2026-08-15, 374dec26)."""

    def test_fast_path_when_already_verified_this_process(self, worker):
        worker._verified_wheel_version = "0.194.0"
        counter = {"cp": 0, "pip": 0}
        with patch.object(worker.subprocess, "run", _fake_subprocess(counter)), \
             patch.object(worker, "verify_wheel_imports", return_value=True) as vwi:
            assert worker.ensure_latest_wheel() is True
        assert counter == {"cp": 0, "pip": 0}   # no download, no install
        vwi.assert_not_called()                  # no reverify either

    def test_installed_version_verifies_without_reinstall(self, worker):
        worker._verified_wheel_version = None
        counter = {"cp": 0, "pip": 0}
        with patch.object(worker.subprocess, "run", _fake_subprocess(counter)), \
             patch.object(worker, "_installed_karaoke_gen_version", return_value="0.194.0"), \
             patch.object(worker, "verify_wheel_imports", return_value=True):
            assert worker.ensure_latest_wheel() is True
        # Right version already present → verify only, no cp/pip.
        assert counter == {"cp": 0, "pip": 0}
        assert worker._verified_wheel_version == "0.194.0"

    def test_installs_when_version_differs(self, worker):
        worker._verified_wheel_version = None
        counter = {"cp": 0, "pip": 0}
        with patch.object(worker.subprocess, "run", _fake_subprocess(counter)), \
             patch.object(worker, "_installed_karaoke_gen_version", return_value="0.193.0"), \
             patch.object(worker, "verify_wheel_imports", return_value=True):
            assert worker.ensure_latest_wheel() is True
        assert counter["cp"] == 1 and counter["pip"] >= 1
        assert worker._verified_wheel_version == "0.194.0"

    def test_concurrent_calls_install_at_most_once(self, worker):
        """The lock + version cache guarantee only ONE thread installs even when
        several race in on a fresh boot; the rest hit the verified fast path."""
        import threading
        import time

        worker._verified_wheel_version = None
        counter = {"cp": 0, "pip": 0}
        base = _fake_subprocess(counter)

        def slow_run(cmd, **kwargs):
            if cmd[:2] == ["gsutil", "cp"]:
                time.sleep(0.2)  # widen the race window
            return base(cmd, **kwargs)

        results = []
        barrier = threading.Barrier(3)

        def call():
            barrier.wait()
            results.append(worker.ensure_latest_wheel())

        with patch.object(worker.subprocess, "run", slow_run), \
             patch.object(worker, "_installed_karaoke_gen_version", return_value="0.193.0"), \
             patch.object(worker, "verify_wheel_imports", return_value=True):
            threads = [threading.Thread(target=call) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert results == [True, True, True]
        assert counter["cp"] == 1   # exactly one download despite 3 concurrent callers
        assert counter["pip"] == 1  # and exactly one install (no concurrent venv writes)
