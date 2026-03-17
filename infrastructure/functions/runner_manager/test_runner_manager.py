"""
Tests for runner_manager/main.py

Covers the critical bug fixes:
1. Metadata writes are awaited (operation.result() called)
2. Missing last-activity uses creationTimestamp fallback (not "set now and keep")
3. Pending jobs don't reset all runner timestamps
4. Stops are parallel (ThreadPoolExecutor)
5. STOPPING state VMs are waited for then started
6. Job completed updates only the specific runner (via runner_name)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

# Stub out Cloud Function dependencies not installed in test env
for mod in (
    "functions_framework",
    "google.cloud.compute_v1",
    "google.cloud.secretmanager",
    "flask",
):
    sys.modules.setdefault(mod, MagicMock())

# Make @functions_framework.http a pass-through decorator so handle_request
# remains a real callable function in tests
sys.modules["functions_framework"].http = lambda f: f

# We need compute_v1 to have Items and Metadata as usable classes
mock_compute_v1 = sys.modules["google.cloud.compute_v1"]
mock_compute_v1.Instance = MagicMock
mock_compute_v1.Items = MagicMock
mock_compute_v1.Metadata = MagicMock

sys.path.insert(0, os.path.dirname(__file__))


def _make_instance(name, status="RUNNING", creation_timestamp=None, metadata_items=None):
    """Create a mock GCE instance."""
    instance = MagicMock()
    instance.name = name
    instance.status = status
    instance.creation_timestamp = creation_timestamp or (
        datetime.now(timezone.utc) - timedelta(hours=5)
    ).isoformat()

    if metadata_items is None:
        metadata_items = []
    items = []
    for k, v in metadata_items:
        item = MagicMock()
        item.key = k
        item.value = v
        items.append(item)
    instance.metadata = MagicMock()
    instance.metadata.items = items
    instance.metadata.fingerprint = "fp123"
    return instance


def _import_main(**env_overrides):
    """Import main module with clean state and optional env overrides."""
    if "main" in sys.modules:
        del sys.modules["main"]
    env = {
        "RUNNER_NAMES": "runner-1,runner-2",
        "GCP_PROJECT": "test-project",
        "GCP_ZONE": "us-central1-a",
        **env_overrides,
    }
    with patch.dict(os.environ, env):
        import main
        main._compute_client = None
        main._secret_client = None
        main._webhook_secret = "test-secret"
        main._github_pat = "ghp_test"
        return main


class TestRunnerNamesConfig:
    """Test that RUNNER_NAMES is parsed correctly from environment."""

    def test_default_runner_names(self):
        env = {k: v for k, v in os.environ.items() if k != "RUNNER_NAMES"}
        with patch.dict(os.environ, env, clear=True):
            if "main" in sys.modules:
                del sys.modules["main"]
            import main
            assert main.RUNNER_NAMES == [
                "github-runner-1",
                "github-runner-2",
                "github-runner-3",
                "github-build-runner",
            ]

    def test_custom_runner_names_from_env(self):
        main = _import_main(RUNNER_NAMES="runner-a,runner-b,runner-c")
        assert main.RUNNER_NAMES == ["runner-a", "runner-b", "runner-c"]

    def test_single_runner_name(self):
        main = _import_main(RUNNER_NAMES="solo-runner")
        assert main.RUNNER_NAMES == ["solo-runner"]


class TestGetRunnerInstances:

    def test_returns_all_instances(self):
        main = _import_main(RUNNER_NAMES="r-1,r-2,r-3")
        mock_client = MagicMock()
        instances = [_make_instance(f"r-{i}") for i in range(1, 4)]
        mock_client.get.side_effect = instances
        main._compute_client = mock_client

        result = main.get_runner_instances()
        assert len(result) == 3
        assert mock_client.get.call_count == 3

    def test_handles_missing_instance(self):
        main = _import_main(RUNNER_NAMES="r-1,r-2")
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _make_instance("r-1"),
            Exception("Instance not found"),
        ]
        main._compute_client = mock_client

        result = main.get_runner_instances()
        assert len(result) == 1


class TestStartRunners:

    def test_starts_terminated_instances_in_parallel(self):
        main = _import_main(RUNNER_NAMES="r-1,r-2")
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _make_instance("r-1", status="TERMINATED"),
            _make_instance("r-2", status="TERMINATED"),
        ]
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op
        main._compute_client = mock_client

        result = main.start_runners()

        assert set(result["started"]) == {"r-1", "r-2"}
        assert mock_client.start.call_count == 2

    def test_skips_gpu_runners_by_default(self):
        """GPU runners should not start unless include_gpu=True."""
        main = _import_main(RUNNER_NAMES="r-1,gpu-1", GPU_RUNNER_NAMES="gpu-1")
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _make_instance("r-1", status="TERMINATED"),
            _make_instance("gpu-1", status="TERMINATED"),
        ]
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op
        main._compute_client = mock_client

        result = main.start_runners(include_gpu=False)

        assert "r-1" in result["started"]
        assert "gpu-1" not in result["started"]
        assert mock_client.start.call_count == 1

    def test_starts_gpu_runners_when_requested(self):
        """GPU runners should start when include_gpu=True."""
        main = _import_main(RUNNER_NAMES="r-1,gpu-1", GPU_RUNNER_NAMES="gpu-1")
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _make_instance("r-1", status="TERMINATED"),
            _make_instance("gpu-1", status="TERMINATED"),
        ]
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op
        main._compute_client = mock_client

        result = main.start_runners(include_gpu=True)

        assert set(result["started"]) == {"r-1", "gpu-1"}

    def test_skips_running_instances(self):
        main = _import_main(RUNNER_NAMES="r-1,r-2")
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _make_instance("r-1", status="RUNNING"),
            _make_instance("r-2", status="TERMINATED"),
        ]
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op
        main._compute_client = mock_client

        result = main.start_runners()

        assert "r-1" in result["already_running"]
        assert "r-2" in result["started"]
        assert mock_client.start.call_count == 1

    def test_waits_for_stopping_vms(self):
        """STOPPING VMs should be polled until TERMINATED, then started."""
        main = _import_main(RUNNER_NAMES="r-1")
        mock_client = MagicMock()

        # First call: get_runner_instances() sees STOPPING
        stopping_inst = _make_instance("r-1", status="STOPPING")
        # Poll calls: first still STOPPING, then TERMINATED
        terminated_inst = _make_instance("r-1", status="TERMINATED")

        mock_client.get.side_effect = [stopping_inst, stopping_inst, terminated_inst]
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op
        main._compute_client = mock_client

        with patch("main.time.sleep"):  # skip actual sleeps
            result = main.start_runners()

        assert "r-1" in result["started"]


class TestUpdateInstanceMetadata:
    """Test that metadata writes are properly awaited."""

    def test_awaits_operation_result(self):
        """The set_metadata operation must be awaited to catch fingerprint conflicts."""
        main = _import_main()
        mock_client = MagicMock()
        instance = _make_instance("r-1", metadata_items=[("last-activity", "old")])
        mock_client.get.return_value = instance
        mock_op = MagicMock()
        mock_client.set_metadata.return_value = mock_op
        main._compute_client = mock_client

        main.update_instance_metadata("r-1", "last-activity", "new-value")

        # Critical: operation.result() must be called
        mock_op.result.assert_called_once_with(timeout=60)

    def test_adds_new_metadata_key(self):
        main = _import_main()
        mock_client = MagicMock()
        instance = _make_instance("r-1", metadata_items=[])
        mock_client.get.return_value = instance
        mock_op = MagicMock()
        mock_client.set_metadata.return_value = mock_op
        main._compute_client = mock_client

        main.update_instance_metadata("r-1", "last-activity", "2026-01-01T00:00:00+00:00")

        mock_client.set_metadata.assert_called_once()
        mock_op.result.assert_called_once()


class TestGetInstanceAgeHours:
    """Test creationTimestamp fallback logic."""

    def test_returns_age_from_creation_timestamp(self):
        main = _import_main()
        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        instance = _make_instance("r-1", creation_timestamp=two_hours_ago)

        age = main._get_instance_age_hours(instance)
        assert 1.9 < age < 2.1

    def test_returns_inf_when_no_timestamp(self):
        main = _import_main()
        instance = _make_instance("r-1")
        instance.creation_timestamp = None

        age = main._get_instance_age_hours(instance)
        assert age == float("inf")


class TestCheckAndStopIdleRunners:
    """Test the idle check logic — the most critical function."""

    def _setup(self, runner_names="r-1,r-2"):
        main = _import_main(RUNNER_NAMES=runner_names)
        mock_client = MagicMock()
        main._compute_client = mock_client
        return main, mock_client

    def test_no_last_activity_uses_creation_timestamp_fallback(self):
        """VMs without last-activity metadata should use creation time,
        NOT set 'now' and keep running (the old bug)."""
        main, mock_client = self._setup(runner_names="r-1")

        # Instance created 3 hours ago, no last-activity metadata
        three_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        instance = _make_instance("r-1", status="RUNNING", creation_timestamp=three_hours_ago, metadata_items=[])
        mock_client.get.side_effect = [instance]

        mock_stop_op = MagicMock()
        mock_stop_op.result.return_value = None
        mock_client.stop.return_value = mock_stop_op

        with patch.object(main, "check_github_for_pending_jobs", return_value=False):
            result = main.check_and_stop_idle_runners()

        assert "r-1" in result["stopped"]

    def test_no_last_activity_keeps_recent_vm(self):
        """VM created <1h ago without metadata should be kept."""
        main, mock_client = self._setup(runner_names="r-1")

        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        instance = _make_instance("r-1", status="RUNNING", creation_timestamp=recent, metadata_items=[])
        mock_client.get.side_effect = [instance]

        with patch.object(main, "check_github_for_pending_jobs", return_value=False):
            result = main.check_and_stop_idle_runners()

        assert "r-1" in result["kept"]
        mock_client.stop.assert_not_called()

    def test_pending_jobs_does_not_refresh_timestamps(self):
        """When jobs are pending, runners stay alive but timestamps are NOT refreshed.
        The old bug: refreshing timestamps prevented runners from ever being idle."""
        main, mock_client = self._setup(runner_names="r-1")

        instance = _make_instance("r-1", status="RUNNING")
        mock_client.get.side_effect = [instance]

        with patch.object(main, "check_github_for_pending_jobs", return_value=True):
            result = main.check_and_stop_idle_runners()

        assert "r-1" in result["kept"]
        # Critical: set_metadata must NOT be called
        mock_client.set_metadata.assert_not_called()

    def test_stops_idle_runners_in_parallel(self):
        """Multiple idle runners should be stopped in parallel."""
        main, mock_client = self._setup(runner_names="r-1,r-2")

        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        instances = [
            _make_instance("r-1", status="RUNNING", metadata_items=[("last-activity", two_hours_ago)]),
            _make_instance("r-2", status="RUNNING", metadata_items=[("last-activity", two_hours_ago)]),
        ]
        mock_client.get.side_effect = instances

        mock_stop_op = MagicMock()
        mock_stop_op.result.return_value = None
        mock_client.stop.return_value = mock_stop_op

        with patch.object(main, "check_github_for_pending_jobs", return_value=False):
            result = main.check_and_stop_idle_runners()

        assert set(result["stopped"]) == {"r-1", "r-2"}

    def test_keeps_recently_active_runners(self):
        main, mock_client = self._setup(runner_names="r-1")

        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        instance = _make_instance("r-1", status="RUNNING", metadata_items=[("last-activity", recent)])
        mock_client.get.side_effect = [instance]

        with patch.object(main, "check_github_for_pending_jobs", return_value=False):
            result = main.check_and_stop_idle_runners()

        assert "r-1" in result["kept"]
        mock_client.stop.assert_not_called()

    def test_skips_non_running_instances(self):
        main, mock_client = self._setup(runner_names="r-1")

        instance = _make_instance("r-1", status="TERMINATED")
        mock_client.get.side_effect = [instance]

        with patch.object(main, "check_github_for_pending_jobs", return_value=False):
            result = main.check_and_stop_idle_runners()

        assert result == {"stopped": [], "kept": [], "errors": []}

    def test_handles_invalid_last_activity_value(self):
        """Invalid timestamp should fall back to creation time."""
        main, mock_client = self._setup(runner_names="r-1")

        old_creation = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        instance = _make_instance(
            "r-1", status="RUNNING",
            creation_timestamp=old_creation,
            metadata_items=[("last-activity", "not-a-date")],
        )
        mock_client.get.side_effect = [instance]

        mock_stop_op = MagicMock()
        mock_stop_op.result.return_value = None
        mock_client.stop.return_value = mock_stop_op

        with patch.object(main, "check_github_for_pending_jobs", return_value=False):
            result = main.check_and_stop_idle_runners()

        assert "r-1" in result["stopped"]


class TestWebhookCompleted:
    """Test that job completion updates only the specific runner."""

    def _make_webhook_request(self, payload):
        """Create a mock request with valid webhook signature."""
        import hmac as hmac_mod
        import hashlib

        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac_mod.new(b"test-secret", body, hashlib.sha256).hexdigest()
        headers = {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "workflow_job",
        }

        request = MagicMock()
        request.args.get.return_value = None
        request.headers.get.side_effect = lambda h, *a: headers.get(h)
        request.get_data.return_value = body
        request.get_json.return_value = payload
        return request

    def test_completed_updates_specific_runner_only(self):
        """Only the runner that ran the job should have its metadata updated."""
        main = _import_main(RUNNER_NAMES="r-1,r-2")
        mock_client = MagicMock()
        instance = _make_instance("r-1", metadata_items=[])
        mock_client.get.return_value = instance
        mock_op = MagicMock()
        mock_client.set_metadata.return_value = mock_op
        main._compute_client = mock_client

        payload = {
            "action": "completed",
            "workflow_job": {
                "labels": ["self-hosted", "linux"],
                "runner_name": "r-1",
            },
        }
        request = self._make_webhook_request(payload)

        # Call the underlying function directly (bypasses decorator)
        main.handle_request(request)

        # Metadata should be updated on r-1 only
        mock_client.set_metadata.assert_called_once()

    def test_completed_without_runner_name_does_nothing(self):
        """If webhook payload has no runner_name, no metadata update should happen."""
        main = _import_main(RUNNER_NAMES="r-1")
        mock_client = MagicMock()
        main._compute_client = mock_client

        payload = {
            "action": "completed",
            "workflow_job": {
                "labels": ["self-hosted"],
            },
        }
        request = self._make_webhook_request(payload)
        main.handle_request(request)

        mock_client.set_metadata.assert_not_called()


class TestNonSelfHostedJobsIgnored:
    """Test that jobs not targeting self-hosted runners are ignored."""

    def test_ubuntu_latest_job_ignored(self):
        main = _import_main()

        import hmac as hmac_mod
        import hashlib

        payload = {
            "action": "queued",
            "workflow_job": {
                "labels": ["ubuntu-latest"],
            },
        }
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac_mod.new(b"test-secret", body, hashlib.sha256).hexdigest()
        headers = {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "workflow_job",
        }

        request = MagicMock()
        request.args.get.return_value = None
        request.headers.get.side_effect = lambda h, *a: headers.get(h)
        request.get_data.return_value = body
        request.get_json.return_value = payload

        response = main.handle_request(request)

        assert "not for self-hosted" in str(response)


class TestCheckGithubForPendingJobs:
    """Test the GitHub API check for pending jobs."""

    def test_checks_both_queued_and_in_progress(self):
        main = _import_main()

        call_count = 0

        def mock_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            response = MagicMock()
            response.read.return_value = json.dumps({"total_count": 0}).encode()
            response.__enter__ = lambda s: response
            response.__exit__ = lambda s, *a: None
            return response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = main.check_github_for_pending_jobs()

        assert result is False
        assert call_count == 2  # queued + in_progress

    def test_returns_true_when_jobs_pending(self):
        main = _import_main()

        def mock_urlopen(req, timeout=None):
            response = MagicMock()
            response.read.return_value = json.dumps({"total_count": 3}).encode()
            response.__enter__ = lambda s: response
            response.__exit__ = lambda s, *a: None
            return response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = main.check_github_for_pending_jobs()

        assert result is True
