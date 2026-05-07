"""Unit tests for encoding worker idle shutdown Cloud Function."""

import json
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Mock external dependencies before importing main
_mock_ff = MagicMock()
# Make the @functions_framework.http decorator pass through the function
_mock_ff.http = lambda f: f
sys.modules["functions_framework"] = _mock_ff
sys.modules["google.cloud"] = MagicMock()
sys.modules["google.cloud.compute_v1"] = MagicMock()
sys.modules["google.cloud.firestore"] = MagicMock()

import main  # noqa: E402


@pytest.fixture(autouse=True)
def reset_clients():
    """Reset cached clients between tests."""
    main._compute_client = None
    main._firestore_client = None


@pytest.fixture
def mock_compute():
    with patch.object(main, "get_compute_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_firestore():
    with patch.object(main, "get_firestore_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def _make_config(
    primary_vm="encoding-worker-a",
    secondary_vm="encoding-worker-b",
    deploy_in_progress=False,
    deploy_in_progress_since=None,
    last_activity_at=None,
):
    return {
        "primary_vm": primary_vm,
        "secondary_vm": secondary_vm,
        "deploy_in_progress": deploy_in_progress,
        "deploy_in_progress_since": deploy_in_progress_since,
        "last_activity_at": last_activity_at,
    }


def _make_instance(status="RUNNING", ip="1.2.3.4"):
    instance = MagicMock()
    instance.status = status
    access_config = MagicMock()
    access_config.nat_i_p = ip
    iface = MagicMock()
    iface.access_configs = [access_config]
    instance.network_interfaces = [iface]
    return instance


def _setup_firestore(mock_firestore, config_dict):
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = config_dict
    mock_firestore.collection.return_value.document.return_value.get.return_value = doc
    return doc


def _setup_firestore_missing(mock_firestore):
    doc = MagicMock()
    doc.exists = False
    mock_firestore.collection.return_value.document.return_value.get.return_value = doc


class TestIdleShutdown:
    """Tests for the idle_shutdown Cloud Function."""

    def test_stops_idle_running_vms(self, mock_compute, mock_firestore):
        """VMs with no active jobs and no recent activity should be stopped."""
        old_activity = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        config = _make_config(last_activity_at=old_activity)
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="RUNNING", ip="1.2.3.4")
        mock_compute.get.return_value = instance

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert status_code == 200
        assert result["status"] == "checked"
        assert result["results"]["encoding-worker-a"] == "stopped"
        assert result["results"]["encoding-worker-b"] == "stopped"
        assert mock_compute.stop.call_count == 2

    def test_skips_deploy_in_progress(self, mock_compute, mock_firestore):
        """Should skip idle check when a deploy is in progress."""
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        config = _make_config(
            deploy_in_progress=True,
            deploy_in_progress_since=recent,
        )
        _setup_firestore(mock_firestore, config)

        response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["status"] == "deploy_in_progress"
        mock_compute.stop.assert_not_called()

    def test_clears_stale_deploy_flag(self, mock_compute, mock_firestore):
        """Deploy flags older than 20 min should be cleared and idle check should proceed."""
        stale = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
        old_activity = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        config = _make_config(
            deploy_in_progress=True,
            deploy_in_progress_since=stale,
            last_activity_at=old_activity,
        )
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="RUNNING", ip="1.2.3.4")
        mock_compute.get.return_value = instance

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["status"] == "checked"
        # Verify deploy flag was cleared
        mock_firestore.collection.return_value.document.return_value.update.assert_called_once_with({
            "deploy_in_progress": False,
            "deploy_in_progress_since": None,
        })

    def test_recent_activity_keeps_primary_alive(self, mock_compute, mock_firestore):
        """Recent activity should keep the primary VM alive but stop the secondary."""
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        config = _make_config(last_activity_at=recent)
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="RUNNING", ip="1.2.3.4")
        mock_compute.get.return_value = instance

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["results"]["encoding-worker-a"] == "active_session"
        assert result["results"]["encoding-worker-b"] == "stopped"
        # Only secondary should be stopped
        assert mock_compute.stop.call_count == 1

    def test_skips_terminated_vms(self, mock_compute, mock_firestore):
        """VMs that are already TERMINATED should be skipped."""
        config = _make_config()
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="TERMINATED")
        mock_compute.get.return_value = instance

        response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["results"]["encoding-worker-a"] == "already_terminated"
        assert result["results"]["encoding-worker-b"] == "already_terminated"
        mock_compute.stop.assert_not_called()

    def test_active_jobs_keep_vm_alive(self, mock_compute, mock_firestore):
        """VMs with active encoding jobs should not be stopped."""
        old_activity = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        config = _make_config(last_activity_at=old_activity)
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="RUNNING", ip="1.2.3.4")
        mock_compute.get.return_value = instance

        with patch("main.check_active_jobs", return_value=2):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["results"]["encoding-worker-a"] == "active_jobs=2"
        assert result["results"]["encoding-worker-b"] == "active_jobs=2"
        mock_compute.stop.assert_not_called()

    def test_no_config_returns_no_config(self, mock_compute, mock_firestore):
        """Should return no_config status when Firestore doc doesn't exist."""
        _setup_firestore_missing(mock_firestore)

        response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["status"] == "no_config"

    def test_vm_without_ip_still_gets_stopped(self, mock_compute, mock_firestore):
        """VMs without an IP (no access config) should still be stopped if idle."""
        old_activity = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        config = _make_config(last_activity_at=old_activity)
        _setup_firestore(mock_firestore, config)

        instance = MagicMock()
        instance.status = "RUNNING"
        instance.network_interfaces = []
        mock_compute.get.return_value = instance

        response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["results"]["encoding-worker-a"] == "stopped"
        assert result["results"]["encoding-worker-b"] == "stopped"
        assert mock_compute.stop.call_count == 2

    def test_no_last_activity_treats_as_idle(self, mock_compute, mock_firestore):
        """If last_activity_at is not set, treat as no recent activity."""
        config = _make_config(last_activity_at=None)
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="RUNNING", ip="1.2.3.4")
        mock_compute.get.return_value = instance

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        # Both should be stopped since no activity recorded
        assert result["results"]["encoding-worker-a"] == "stopped"
        assert result["results"]["encoding-worker-b"] == "stopped"


# Helper for the fallback-aware fixtures below — fallbacks live in alternate
# zones, so the mock compute client needs to dispatch get() by zone.
def _zone_dispatching_get(instances_by_vm):
    """Return a side_effect that returns the right mock per-VM lookup."""

    def fake_get(project, zone, instance):
        try:
            return instances_by_vm[instance]
        except KeyError as e:  # noqa: BLE001
            raise Exception(f"unexpected instance lookup: {instance}") from e

    return fake_get


class TestFallbackVms:
    """Tests for multi-zone fallback VM handling.

    Without these checks, fallback VMs left running after a capacity event
    leak indefinitely (observed 2026-05-07: fallback-a ran 13+ hours idle).
    """

    @pytest.fixture
    def fallback_env(self, monkeypatch):
        """Set ENCODING_WORKER_FALLBACK_VMS env var with two fallbacks."""
        monkeypatch.setenv(
            "ENCODING_WORKER_FALLBACK_VMS",
            json.dumps([
                {"vm": "encoding-worker-fallback-a", "zone": "us-central1-a", "ip": "1.1.1.1"},
                {"vm": "encoding-worker-fallback-b", "zone": "us-central1-b", "ip": "2.2.2.2"},
            ]),
        )

    def test_idle_fallback_gets_stopped(self, mock_compute, mock_firestore, fallback_env):
        """A fallback VM that's RUNNING but idle (no jobs, not active) must be stopped.
        This is the bug the fix addresses — previously fallbacks were never checked."""
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        config = _make_config(last_activity_at=recent)
        _setup_firestore(mock_firestore, config)

        # Primary RUNNING with recent activity → kept alive
        # Secondary TERMINATED → already_terminated
        # Fallback-a RUNNING but idle (active_override is None) → MUST stop
        # Fallback-b TERMINATED → already_terminated
        instances = {
            "encoding-worker-a": _make_instance(status="RUNNING", ip="1.2.3.4"),
            "encoding-worker-b": _make_instance(status="TERMINATED"),
            "encoding-worker-fallback-a": _make_instance(status="RUNNING", ip="1.1.1.1"),
            "encoding-worker-fallback-b": _make_instance(status="TERMINATED"),
        }
        mock_compute.get.side_effect = _zone_dispatching_get(instances)

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["results"]["encoding-worker-a"] == "active_session"
        assert result["results"]["encoding-worker-b"] == "already_terminated"
        assert result["results"]["encoding-worker-fallback-a"] == "stopped"
        assert result["results"]["encoding-worker-fallback-b"] == "already_terminated"

        # The fallback stop must target its own zone, not the default
        stop_calls = mock_compute.stop.call_args_list
        assert len(stop_calls) == 1
        assert stop_calls[0].kwargs["instance"] == "encoding-worker-fallback-a"
        assert stop_calls[0].kwargs["zone"] == "us-central1-a"

    def test_active_override_fallback_kept_alive_on_recent_activity(
        self, mock_compute, mock_firestore, fallback_env,
    ):
        """When a capacity-fallback override is set, that fallback is the
        currently-routed VM and gets the keep-alive treatment (matching
        what primary normally gets). Primary should still stop on idle."""
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        config = {
            **_make_config(last_activity_at=recent),
            "active_override_vm": "encoding-worker-fallback-a",
        }
        _setup_firestore(mock_firestore, config)

        instances = {
            "encoding-worker-a": _make_instance(status="RUNNING", ip="1.2.3.4"),
            "encoding-worker-b": _make_instance(status="TERMINATED"),
            "encoding-worker-fallback-a": _make_instance(status="RUNNING", ip="1.1.1.1"),
            "encoding-worker-fallback-b": _make_instance(status="TERMINATED"),
        }
        mock_compute.get.side_effect = _zone_dispatching_get(instances)

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        # Primary now gets stopped because fallback is the active VM
        assert result["results"]["encoding-worker-a"] == "stopped"
        # Fallback-a is the currently-routed VM, kept alive on recent activity
        assert result["results"]["encoding-worker-fallback-a"] == "active_session"

    def test_fallback_with_active_jobs_kept_alive(
        self, mock_compute, mock_firestore, fallback_env,
    ):
        """Fallback VM mid-encoding (active_jobs>0) must NOT be stopped —
        the encoding job would die mid-run."""
        old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        config = _make_config(last_activity_at=old)
        _setup_firestore(mock_firestore, config)

        instances = {
            "encoding-worker-a": _make_instance(status="TERMINATED"),
            "encoding-worker-b": _make_instance(status="TERMINATED"),
            "encoding-worker-fallback-a": _make_instance(status="RUNNING", ip="1.1.1.1"),
            "encoding-worker-fallback-b": _make_instance(status="TERMINATED"),
        }
        mock_compute.get.side_effect = _zone_dispatching_get(instances)

        with patch("main.check_active_jobs", return_value=3):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["results"]["encoding-worker-fallback-a"] == "active_jobs=3"
        mock_compute.stop.assert_not_called()

    def test_no_fallback_env_var_falls_back_to_primary_secondary_only(
        self, mock_compute, mock_firestore, monkeypatch,
    ):
        """If ENCODING_WORKER_FALLBACK_VMS is missing, function still works
        for primary/secondary — no crash, no fallback iteration."""
        monkeypatch.delenv("ENCODING_WORKER_FALLBACK_VMS", raising=False)
        old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        config = _make_config(last_activity_at=old)
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="RUNNING", ip="1.2.3.4")
        mock_compute.get.return_value = instance

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert "encoding-worker-a" in result["results"]
        assert "encoding-worker-b" in result["results"]
        assert "encoding-worker-fallback-a" not in result["results"]

    def test_malformed_fallback_env_doesnt_crash(
        self, mock_compute, mock_firestore, monkeypatch,
    ):
        """Bad JSON in fallback env must NOT prevent primary/secondary
        idle-shutdown from running. The cost of malformed env is just no
        fallback checking, not a broken function."""
        monkeypatch.setenv("ENCODING_WORKER_FALLBACK_VMS", "not json {{{")
        old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        config = _make_config(last_activity_at=old)
        _setup_firestore(mock_firestore, config)

        instance = _make_instance(status="RUNNING", ip="1.2.3.4")
        mock_compute.get.return_value = instance

        with patch("main.check_active_jobs", return_value=0):
            response, status_code = main.idle_shutdown(MagicMock())

        result = json.loads(response)
        assert result["status"] == "checked"
        assert "encoding-worker-a" in result["results"]


class TestParseFallbackVms:
    """Direct unit tests for the _parse_fallback_vms helper."""

    def test_returns_empty_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("ENCODING_WORKER_FALLBACK_VMS", raising=False)
        assert main._parse_fallback_vms() == []

    def test_returns_empty_on_malformed_json(self, monkeypatch):
        monkeypatch.setenv("ENCODING_WORKER_FALLBACK_VMS", "not json")
        assert main._parse_fallback_vms() == []

    def test_returns_tuples_of_vm_zone(self, monkeypatch):
        monkeypatch.setenv(
            "ENCODING_WORKER_FALLBACK_VMS",
            json.dumps([
                {"vm": "fb-a", "zone": "us-central1-a", "ip": "1.1.1.1"},
                {"vm": "fb-b", "zone": "us-central1-b", "ip": "2.2.2.2"},
            ]),
        )
        assert main._parse_fallback_vms() == [
            ("fb-a", "us-central1-a"),
            ("fb-b", "us-central1-b"),
        ]

    def test_skips_malformed_entries(self, monkeypatch):
        """Entries missing required keys are skipped, valid entries kept."""
        monkeypatch.setenv(
            "ENCODING_WORKER_FALLBACK_VMS",
            json.dumps([
                {"vm": "fb-a", "zone": "us-central1-a"},  # ok, missing ip is fine
                {"vm": "fb-no-zone"},  # malformed: no zone
                {"vm": "fb-c", "zone": "us-central1-c"},  # ok
            ]),
        )
        assert main._parse_fallback_vms() == [
            ("fb-a", "us-central1-a"),
            ("fb-c", "us-central1-c"),
        ]
