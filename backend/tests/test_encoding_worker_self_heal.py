"""Encoding worker self-heal (backend/services/gce_encoding/self_heal.py).

Incident 2026-10-06: the primary booted before its network was up, fell back to a
startup script that omitted ENCODING_API_KEY and GCE_METADATA_MTLS_MODE=none, and
then failed every job with CERTIFICATE_VERIFY_FAILED until restarted by hand.
"""

import threading

import pytest

from backend.services.gce_encoding import self_heal

INFRA_ERROR = (
    "Failed to retrieve https://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/?recursive=true from the Google Compute Engine metadata "
    "service. Compute Engine Metadata server unavailable."
)


@pytest.fixture
def on_worker_vm(monkeypatch):
    monkeypatch.setattr(self_heal, "running_on_worker_vm", lambda: True)


@pytest.fixture(autouse=True)
def reset_restart_flag():
    self_heal._restart_pending = False
    yield
    self_heal._restart_pending = False


class TestRunningOnWorkerVm:
    def test_requires_systemd_and_bootstrap(self, monkeypatch, tmp_path):
        bootstrap = tmp_path / "bootstrap.sh"
        monkeypatch.setattr(self_heal, "BOOTSTRAP_PATH", str(bootstrap))
        monkeypatch.setenv("INVOCATION_ID", "abc")
        assert self_heal.running_on_worker_vm() is False  # no bootstrap.sh (e.g. CI runner)

        bootstrap.touch()
        assert self_heal.running_on_worker_vm() is True

        monkeypatch.delenv("INVOCATION_ID")
        assert self_heal.running_on_worker_vm() is False


class TestProbeCredentials:
    def test_healthy(self):
        assert self_heal.probe_credentials(refresh=lambda: None) is None

    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def refresh():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("blip")

        assert self_heal.probe_credentials(attempts=3, retry_seconds=0, refresh=refresh) is None
        assert calls["n"] == 3

    def test_returns_last_error_when_always_failing(self):
        def refresh():
            raise RuntimeError(INFRA_ERROR)

        assert self_heal.probe_credentials(attempts=2, retry_seconds=0, refresh=refresh) == INFRA_ERROR


class TestBootHealthProblem:
    def test_off_vm_never_reports(self, monkeypatch):
        monkeypatch.setattr(self_heal, "running_on_worker_vm", lambda: False)
        monkeypatch.delenv("ENCODING_API_KEY", raising=False)
        assert self_heal.boot_health_problem(probe=lambda: "broken") is None

    def test_healthy_boot(self, on_worker_vm, monkeypatch):
        monkeypatch.setenv("ENCODING_API_KEY", "k")
        assert self_heal.boot_health_problem(probe=lambda: None) is None

    def test_missing_api_key(self, on_worker_vm, monkeypatch):
        monkeypatch.delenv("ENCODING_API_KEY", raising=False)
        problem = self_heal.boot_health_problem(probe=lambda: None)
        assert "ENCODING_API_KEY" in problem

    def test_credentials_failing(self, on_worker_vm, monkeypatch):
        monkeypatch.setenv("ENCODING_API_KEY", "k")
        problem = self_heal.boot_health_problem(probe=lambda: INFRA_ERROR)
        assert "cannot obtain GCP credentials" in problem


def _wait_for(event, timeout=5):
    assert event.wait(timeout), "self-heal thread did not finish"


class TestScheduleRestart:
    def test_ignores_non_infra_errors(self, on_worker_vm):
        exited = []
        assert not self_heal.schedule_restart_if_unhealthy(
            "ffmpeg: invalid codec", lambda: False, delay_seconds=0,
            probe=lambda: INFRA_ERROR, exit_process=exited.append,
        )
        assert exited == []

    def test_off_vm_does_nothing(self, monkeypatch):
        monkeypatch.setattr(self_heal, "running_on_worker_vm", lambda: False)
        assert not self_heal.schedule_restart_if_unhealthy(
            INFRA_ERROR, lambda: False, delay_seconds=0,
            probe=lambda: INFRA_ERROR, exit_process=lambda code: None,
        )

    def test_exits_when_still_broken_and_idle(self, on_worker_vm):
        done = threading.Event()
        exited = []

        def exit_process(code):
            exited.append(code)
            done.set()

        assert self_heal.schedule_restart_if_unhealthy(
            INFRA_ERROR, lambda: False, delay_seconds=0,
            probe=lambda: INFRA_ERROR, exit_process=exit_process,
        )
        _wait_for(done)
        assert exited == [1]

    def test_no_exit_when_credentials_recover(self, on_worker_vm):
        done = threading.Event()
        exited = []

        def probe():
            done.set()
            return None

        assert self_heal.schedule_restart_if_unhealthy(
            INFRA_ERROR, lambda: False, delay_seconds=0,
            probe=probe, exit_process=exited.append,
        )
        _wait_for(done)
        # Let the thread finish and release the pending flag.
        for _ in range(100):
            if not self_heal._restart_pending:
                break
            threading.Event().wait(0.01)
        assert exited == []
        assert self_heal._restart_pending is False

    def test_waits_for_in_flight_jobs_before_exiting(self, on_worker_vm):
        done = threading.Event()
        exited = []
        active = iter([True, True, False])

        def exit_process(code):
            exited.append(code)
            done.set()

        self_heal.schedule_restart_if_unhealthy(
            INFRA_ERROR, lambda: next(active), delay_seconds=0,
            probe=lambda: INFRA_ERROR, exit_process=exit_process,
        )
        _wait_for(done)
        assert exited == [1]

    def test_only_one_check_at_a_time(self, on_worker_vm):
        release = threading.Event()

        def probe():
            release.wait(5)
            return None

        assert self_heal.schedule_restart_if_unhealthy(
            INFRA_ERROR, lambda: False, delay_seconds=0, probe=probe, exit_process=lambda c: None,
        )
        assert not self_heal.schedule_restart_if_unhealthy(
            INFRA_ERROR, lambda: False, delay_seconds=0, probe=probe, exit_process=lambda c: None,
        )
        release.set()


# ---------------------------------------------------------------------------
# Wiring in gce_encoding/main.py
# ---------------------------------------------------------------------------

import asyncio
import os
import sys
from unittest.mock import MagicMock, patch


@pytest.fixture
def worker_module():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GCE_METADATA_MTLS_MODE", None)
        with patch("google.cloud.storage.Client", return_value=MagicMock()):
            sys.modules.pop("backend.services.gce_encoding.main", None)
            import backend.services.gce_encoding.main as m
            yield m
            sys.modules.pop("backend.services.gce_encoding.main", None)


def test_import_defaults_metadata_mtls_off(worker_module):
    assert os.environ["GCE_METADATA_MTLS_MODE"] == "none"


def test_import_keeps_explicit_mtls_mode():
    with patch.dict(os.environ, {"GCE_METADATA_MTLS_MODE": "strict"}, clear=False):
        with patch("google.cloud.storage.Client", return_value=MagicMock()):
            sys.modules.pop("backend.services.gce_encoding.main", None)
            import backend.services.gce_encoding.main  # noqa: F401
            sys.modules.pop("backend.services.gce_encoding.main", None)
            assert os.environ["GCE_METADATA_MTLS_MODE"] == "strict"


def _run_lifespan(m):
    async def go():
        async with m.lifespan(m.app):
            pass
    asyncio.run(go())


def test_lifespan_refuses_to_start_when_boot_unhealthy(worker_module, monkeypatch):
    monkeypatch.setattr(worker_module.self_heal, "boot_health_problem", lambda: "no API key")
    with pytest.raises(RuntimeError, match="booted unhealthy: no API key"):
        _run_lifespan(worker_module)


def test_lifespan_starts_when_healthy(worker_module, monkeypatch):
    monkeypatch.setattr(worker_module.self_heal, "boot_health_problem", lambda: None)
    monkeypatch.setattr(worker_module.persister, "mark_orphans_failed_on_startup", lambda jobs: 0)
    _run_lifespan(worker_module)


def test_failed_job_schedules_self_heal(worker_module, monkeypatch):
    seen = []
    monkeypatch.setattr(
        worker_module.self_heal, "schedule_restart_if_unhealthy",
        lambda error_text, has_active: seen.append((error_text, has_active())),
    )
    worker_module.jobs.clear()
    worker_module._self_heal_after_failure(RuntimeError(INFRA_ERROR))
    assert seen == [(INFRA_ERROR, False)]


def test_self_heal_scheduling_errors_never_propagate(worker_module, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("thread start failed")
    monkeypatch.setattr(worker_module.self_heal, "schedule_restart_if_unhealthy", boom)
    worker_module._self_heal_after_failure(RuntimeError(INFRA_ERROR))  # must not raise
