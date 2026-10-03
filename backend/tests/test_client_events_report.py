"""Unit tests for scripts/client_events_report.py (summary logic only)."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "client_events_report",
    Path(__file__).resolve().parents[2] / "scripts" / "client_events_report.py",
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

T = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def _ev(type_, **kw):
    return {"type": type_, "created_at": T, "source": "client", **kw}


def test_excludes_admin_internal_test_by_default():
    events = [
        _ev("banner_unavailable", user_email="fan@gmail.com"),
        _ev("banner_unavailable", user_email="andrew@nomadkaraoke.com", is_admin=True, is_internal=True),
        _ev("banner_reconnecting", user_email="x@inbox.testmail.app", is_test=True),
    ]
    out = report.summarise(events, include_noise=False)
    assert "excluded 2" in out
    assert "fan@gmail.com" in out
    assert "andrew@" not in out
    out_all = report.summarise(events, include_noise=True)
    assert "andrew@nomadkaraoke.com" in out_all


def test_episode_durations_and_stall_culprits():
    events = [
        _ev("banner_recovered", detail={"duration_ms": 30000, "peak_status": "unavailable"}),
        _ev("banner_recovered", detail={"duration_ms": 12000, "peak_status": "reconnecting"}),
        {"type": "server_loop_stall", "source": "server", "created_at": T,
         "detail": {"duration_ms": 31000, "culprit": "backend/workers/screens_worker.py:774 in x"}},
    ]
    out = report.summarise(events, include_noise=False)
    assert "2 recovered" in out
    assert "max 30.0s" in out
    assert "Server event-loop stalls >=5s: 1" in out
    assert "screens_worker.py:774" in out


def test_anonymous_devices_grouped_by_fingerprint_and_legacy_tenant():
    events = [
        _ev("banner_unavailable", device_fingerprint="abcdef123456", url="https://randy-vild.nomadkaraoke.com/en/app/"),
        _ev("banner_unavailable", url="https://gen.nomadkaraoke.com/en/app/"),
    ]
    out = report.summarise(events, include_noise=False)
    assert "fp:abcdef1234" in out
    assert "(anonymous)" in out
    assert "randy-vild=1" in out
