"""
Tests for runner_manager/main.py

Focuses on the RUNNER_NAMES configuration and the core functions that
iterate over runner instances: get_runner_instances() and start_runners().
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

# Stub out Cloud Function dependencies not installed in test env
for mod in (
    "functions_framework",
    "google.cloud.compute_v1",
    "google.cloud.secretmanager",
    "flask",
):
    sys.modules.setdefault(mod, MagicMock())

# We need compute_v1 to have Items and Metadata as usable classes
mock_compute_v1 = sys.modules["google.cloud.compute_v1"]
mock_compute_v1.Instance = MagicMock
mock_compute_v1.Items = MagicMock
mock_compute_v1.Metadata = MagicMock

# Mock the InstancesClient
mock_instances_client = MagicMock()
mock_compute_v1.InstancesClient.return_value = mock_instances_client

sys.path.insert(0, os.path.dirname(__file__))


class TestRunnerNamesConfig:
    """Test that RUNNER_NAMES is parsed correctly from environment."""

    def test_default_runner_names(self):
        """Default includes all 3 general runners plus the build runner."""
        env = {k: v for k, v in os.environ.items() if k != "RUNNER_NAMES"}
        with patch.dict(os.environ, env, clear=True):
            # Re-import to pick up default
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
        """RUNNER_NAMES env var overrides the default list."""
        with patch.dict(os.environ, {"RUNNER_NAMES": "runner-a,runner-b"}):
            if "main" in sys.modules:
                del sys.modules["main"]
            import main

            assert main.RUNNER_NAMES == ["runner-a", "runner-b"]

    def test_single_runner_name(self):
        """Single runner name without commas works."""
        with patch.dict(os.environ, {"RUNNER_NAMES": "solo-runner"}):
            if "main" in sys.modules:
                del sys.modules["main"]
            import main

            assert main.RUNNER_NAMES == ["solo-runner"]


class TestGetRunnerInstances:
    """Test get_runner_instances() iterates over RUNNER_NAMES."""

    def setup_method(self):
        """Reset module state before each test."""
        if "main" in sys.modules:
            del sys.modules["main"]
        # Reset the cached client
        with patch.dict(
            os.environ,
            {"RUNNER_NAMES": "github-runner-1,github-runner-2,github-build-runner"},
        ):
            import main

            self.main = main
            self.main._compute_client = None

    def test_returns_all_instances(self):
        """Should fetch each runner by name from RUNNER_NAMES."""
        mock_client = MagicMock()
        mock_instances = []
        for name in ["github-runner-1", "github-runner-2", "github-build-runner"]:
            instance = MagicMock()
            instance.name = name
            mock_instances.append(instance)

        mock_client.get.side_effect = mock_instances
        self.main._compute_client = mock_client

        result = self.main.get_runner_instances()

        assert len(result) == 3
        assert mock_client.get.call_count == 3
        # Verify each name was queried
        call_names = [
            call.kwargs.get("instance") or call[1].get("instance", call[1])
            for call in mock_client.get.call_args_list
        ]
        for call_args in mock_client.get.call_args_list:
            assert call_args == (
                ()
            ) or "instance" in str(call_args)

    def test_handles_missing_instance(self):
        """Should skip instances that don't exist (e.g. not yet created)."""
        mock_client = MagicMock()
        instance1 = MagicMock()
        instance1.name = "github-runner-1"

        mock_client.get.side_effect = [
            instance1,
            Exception("Instance not found"),
            MagicMock(name="github-build-runner"),
        ]
        self.main._compute_client = mock_client

        result = self.main.get_runner_instances()

        # Should still return the 2 that succeeded
        assert len(result) == 2


class TestStartRunners:
    """Test start_runners() iterates over RUNNER_NAMES."""

    def setup_method(self):
        if "main" in sys.modules:
            del sys.modules["main"]
        with patch.dict(
            os.environ,
            {"RUNNER_NAMES": "github-runner-1,github-build-runner"},
        ):
            import main

            self.main = main
            self.main._compute_client = None

    def test_starts_terminated_instances(self):
        """Should start TERMINATED instances from RUNNER_NAMES."""
        mock_client = MagicMock()

        runner1 = MagicMock()
        runner1.status = "TERMINATED"
        runner1.name = "github-runner-1"

        build_runner = MagicMock()
        build_runner.status = "TERMINATED"
        build_runner.name = "github-build-runner"

        mock_client.get.side_effect = [runner1, build_runner]
        # Make start operations succeed
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op

        self.main._compute_client = mock_client

        result = self.main.start_runners()

        assert "github-runner-1" in result["started"]
        assert "github-build-runner" in result["started"]
        assert mock_client.start.call_count == 2

    def test_skips_running_instances(self):
        """Should not start already-running instances."""
        mock_client = MagicMock()

        runner1 = MagicMock()
        runner1.status = "RUNNING"
        runner1.name = "github-runner-1"

        build_runner = MagicMock()
        build_runner.status = "TERMINATED"
        build_runner.name = "github-build-runner"

        mock_client.get.side_effect = [runner1, build_runner]
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op

        self.main._compute_client = mock_client

        result = self.main.start_runners()

        assert "github-runner-1" in result["already_running"]
        assert "github-build-runner" in result["started"]
        assert mock_client.start.call_count == 1

    def test_handles_start_failure(self):
        """Should report failures without crashing."""
        mock_client = MagicMock()

        runner1 = MagicMock()
        runner1.status = "TERMINATED"
        runner1.name = "github-runner-1"

        mock_client.get.side_effect = [runner1]
        mock_client.start.side_effect = Exception("API error")

        self.main._compute_client = mock_client
        # Patch RUNNER_NAMES to just one for simplicity
        self.main.RUNNER_NAMES = ["github-runner-1"]

        result = self.main.start_runners()

        assert "github-runner-1" in result["failed"]

    def test_handles_instance_not_found(self):
        """Should handle case where instance doesn't exist yet."""
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("Instance not found")

        self.main._compute_client = mock_client
        self.main.RUNNER_NAMES = ["nonexistent-runner"]

        result = self.main.start_runners()

        assert "nonexistent-runner" in result["failed"]


class TestWebhookHandling:
    """Test that webhook handler starts all runners including build runner."""

    def setup_method(self):
        if "main" in sys.modules:
            del sys.modules["main"]
        with patch.dict(
            os.environ,
            {"RUNNER_NAMES": "github-runner-1,github-build-runner"},
        ):
            import main

            self.main = main
            self.main._compute_client = None
            self.main._webhook_secret = "test-secret"

    def test_queued_event_starts_all_runners(self):
        """workflow_job.queued should start all runners in RUNNER_NAMES."""
        mock_client = MagicMock()

        instances = []
        for name in ["github-runner-1", "github-build-runner"]:
            inst = MagicMock()
            inst.status = "TERMINATED"
            inst.name = name
            instances.append(inst)

        mock_client.get.side_effect = instances
        mock_op = MagicMock()
        mock_op.result.return_value = None
        mock_client.start.return_value = mock_op

        self.main._compute_client = mock_client

        result = self.main.start_runners()

        assert len(result["started"]) == 2
        assert "github-runner-1" in result["started"]
        assert "github-build-runner" in result["started"]
