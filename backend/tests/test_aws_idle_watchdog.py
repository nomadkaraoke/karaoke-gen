"""Tests for the AWS encoding worker's idle self-stop decision."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location("idle_watchdog", Path(__file__).resolve().parents[2] / "infrastructure" / "encoding-worker" / "aws" / "idle-watchdog.py")
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
VM = "aws-encoding-worker-1"


def _decide(active_jobs=0, config=None, unknown_since=None):
    return wd.decide(now=NOW, active_jobs=active_jobs, config=config, vm_name=VM, unknown_since=unknown_since)[0]


def _routed(minutes_ago):
    return {"active_override_vm": VM, "last_activity_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_busy_worker_keeps_running_even_if_not_routed():
    assert _decide(active_jobs=2, config={"active_override_vm": "encoding-worker-a"}) == "keep"


def test_idle_and_not_routed_stops():
    assert _decide(config={"active_override_vm": None}) == "stop"


def test_routed_with_recent_activity_keeps_running():
    assert _decide(config=_routed(2)) == "keep"


def test_routed_but_idle_past_timeout_stops():
    assert _decide(config=_routed(6)) == "stop"


def test_routed_without_activity_timestamp_stops():
    assert _decide(config={"active_override_vm": VM}) == "stop"


def test_naive_timestamp_treated_as_utc():
    assert _decide(config={"active_override_vm": VM, "last_activity_at": "2026-10-29T11:58:00"}) == "keep"


def test_unreadable_health_starts_grace_then_stops():
    assert _decide(active_jobs=None, config=_routed(1)) == "unknown"
    assert _decide(active_jobs=None, config=_routed(1), unknown_since=NOW - timedelta(minutes=5)) == "unknown"
    assert _decide(active_jobs=None, config=_routed(1), unknown_since=NOW - timedelta(minutes=16)) == "stop"


def test_unreadable_firestore_starts_grace_then_stops():
    assert _decide(config=None) == "unknown"
    assert _decide(config=None, unknown_since=NOW - timedelta(minutes=20)) == "stop"
