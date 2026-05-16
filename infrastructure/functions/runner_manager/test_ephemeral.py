"""Tests for ephemeral.py — the new JIT/ephemeral-VM dispatcher.

Mocks google-cloud-compute and the GitHub REST API. No GCP calls.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# Stub google.cloud.compute_v1 before importing ephemeral.py.
# We attach attribute-style proto stubs so `compute_v1.AttachedDisk(...)` etc. work.
_compute_stub = MagicMock(name="google.cloud.compute_v1")
for proto in (
    "AttachedDisk",
    "AttachedDiskInitializeParams",
    "NetworkInterface",
    "AccessConfig",
    "Metadata",
    "Items",
    "Scheduling",
    "Instance",
    "ServiceAccount",
    "Tags",
    "ShieldedInstanceConfig",
    "AcceleratorConfig",
    "ListInstancesRequest",
    "InstancesClient",
    "ImagesClient",
):
    setattr(_compute_stub, proto, MagicMock(name=proto))

sys.modules.setdefault("google.cloud", MagicMock(name="google.cloud"))
sys.modules["google.cloud.compute_v1"] = _compute_stub


sys.path.insert(0, os.path.dirname(__file__))


def _fresh_module(**env):
    """Reload ephemeral.py with isolated env + mocked clients."""
    for mod in ("ephemeral",):
        sys.modules.pop(mod, None)
    base_env = {
        "GCP_PROJECT": "test-project",
        "GCP_ZONE": "us-central1-a",
        "GCP_FALLBACK_ZONE": "us-east4-c",
        "GITHUB_ORG": "test-org",
        "RUNNER_GROUP_ID": "1",
        "ORPHAN_GRACE_MINUTES": "30",
        "MAX_VM_LIFETIME_MINUTES": "120",
        **env,
    }
    with patch.dict(os.environ, base_env, clear=False):
        import ephemeral

        ephemeral._compute_client = None
        return ephemeral


class TestResolveFamily:
    def test_gpu_label_wins(self):
        ep = _fresh_module()
        fam = ep.resolve_family(["self-hosted", "linux", "gpu"])
        assert fam.name == "gpu"

    def test_docker_build_label(self):
        ep = _fresh_module()
        fam = ep.resolve_family(["self-hosted", "linux", "gcp", "docker-build"])
        assert fam.name == "build"

    def test_default_is_general(self):
        ep = _fresh_module()
        fam = ep.resolve_family(["self-hosted", "linux", "gcp"])
        assert fam.name == "general"

    def test_gpu_precedence_over_docker_build(self):
        ep = _fresh_module()
        fam = ep.resolve_family(["self-hosted", "gpu", "docker-build"])
        # GPU wins — image is fundamentally different
        assert fam.name == "gpu"

    def test_case_insensitive(self):
        ep = _fresh_module()
        fam = ep.resolve_family(["Self-Hosted", "Linux", "GPU"])
        assert fam.name == "gpu"


class TestRunnerLabelsFor:
    def test_general_labels_include_existing_set(self):
        ep = _fresh_module()
        labels = ep.runner_labels_for(ep.FAMILIES["general"])
        assert "self-hosted" in labels
        assert "linux" in labels
        assert "gcp" in labels
        # x64 and large-disk preserved from existing config so jobs that
        # still ask for those don't break.
        assert "x64" in labels
        assert "large-disk" in labels

    def test_build_labels_include_docker_build(self):
        ep = _fresh_module()
        labels = ep.runner_labels_for(ep.FAMILIES["build"])
        assert "docker-build" in labels

    def test_gpu_labels_include_gpu(self):
        ep = _fresh_module()
        labels = ep.runner_labels_for(ep.FAMILIES["gpu"])
        assert "gpu" in labels


class TestJitMint:
    def test_mints_with_correct_payload(self):
        ep = _fresh_module()
        with patch.object(ep, "_github_request") as gh:
            gh.return_value = {"encoded_jit_config": "ZW5jb2RlZA=="}
            token = ep.mint_jit_config(
                "ghp_test", "gha-general-abc123", ep.FAMILIES["general"]
            )
            assert token == "ZW5jb2RlZA=="
            method, path, pat, payload = gh.call_args[0]
            assert method == "POST"
            assert path == "/orgs/test-org/actions/runners/generate-jitconfig"
            assert pat == "ghp_test"
            assert payload["name"] == "gha-general-abc123"
            assert payload["runner_group_id"] == 1
            assert "self-hosted" in payload["labels"]
            assert payload["work_folder"] == "_work"

    def test_raises_on_missing_field(self):
        ep = _fresh_module()
        import pytest

        with patch.object(ep, "_github_request") as gh:
            gh.return_value = {"unexpected": "shape"}
            with pytest.raises(RuntimeError):
                ep.mint_jit_config("pat", "name", ep.FAMILIES["general"])


class TestCreateEphemeralRunner:
    def test_primary_zone_success(self):
        ep = _fresh_module()

        op = MagicMock(name="op")
        op.name = "operation-123"

        compute_client = MagicMock()
        compute_client.insert.return_value = op
        ep._compute_client = compute_client

        with patch.object(ep, "mint_jit_config", return_value="JIT_TOKEN"):
            result = ep.create_ephemeral_runner(
                ["self-hosted", "linux", "gcp"], "ghp_test"
            )

        assert result["family"] == "general"
        assert result["zone"] == "us-central1-a"
        assert result["external_ip"] is False
        assert result["runner_name"].startswith("gha-general-")
        compute_client.insert.assert_called_once()

    def test_zone_exhausted_falls_back(self):
        ep = _fresh_module()

        primary_failure = Exception("ZONE_RESOURCE_POOL_EXHAUSTED in us-central1-a")
        op = MagicMock()
        op.name = "operation-fallback"

        compute_client = MagicMock()
        compute_client.insert.side_effect = [primary_failure, op]
        ep._compute_client = compute_client

        with patch.object(ep, "mint_jit_config", return_value="JIT_TOKEN"):
            result = ep.create_ephemeral_runner(["self-hosted", "gpu"], "ghp_test")

        assert result["family"] == "gpu"
        assert result["zone"] == "us-east4-c"
        # GPU variant in fallback zone uses ephemeral external IP (no NAT in us-east4)
        assert result["external_ip"] is True
        assert compute_client.insert.call_count == 2

    def test_unrelated_failure_does_not_fall_back(self):
        ep = _fresh_module()
        import pytest

        compute_client = MagicMock()
        compute_client.insert.side_effect = Exception("Permission denied")
        ep._compute_client = compute_client

        with patch.object(ep, "mint_jit_config", return_value="JIT_TOKEN"):
            with pytest.raises(Exception, match="Permission denied"):
                ep.create_ephemeral_runner(
                    ["self-hosted", "linux", "gcp"], "ghp_test"
                )
        # Should NOT have retried in fallback zone
        assert compute_client.insert.call_count == 1


def _make_instance(name, age_minutes, zone="us-central1-a"):
    inst = MagicMock()
    inst.name = name
    created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    inst.creation_timestamp = created.isoformat().replace("+00:00", "Z")
    return inst


class TestCleanupOrphans:
    """Test orphan-cleanup by patching the VM-listing helper directly.

    The GCE listing call uses ListInstancesRequest which is fully mocked, so we
    bypass that layer and patch _list_all_ephemeral_vms / _delete_vm to keep the
    tests focused on the reconciliation logic.
    """

    def _patch_listing(self, ep, vms):
        return patch.object(ep, "_list_all_ephemeral_vms", return_value=vms)

    def test_keeps_vms_within_grace_window(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-young", age_minutes=5))]
        runners = []

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(ep, "_delete_vm") as delete_mock:
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-young" in result["kept_vms"]
        delete_mock.assert_not_called()

    def test_deletes_unregistered_past_grace(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-stuck", age_minutes=45))]
        runners = []

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(ep, "_delete_vm", return_value=("gha-general-stuck", "deleted")):
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-stuck" in result["deleted_vms"]

    def test_deletes_hung_vm_even_when_registered(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-hung", age_minutes=180))]
        runners = [{"name": "gha-general-hung", "id": 7, "status": "online"}]

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(ep, "_delete_vm", return_value=("gha-general-hung", "deleted")):
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-hung" in result["deleted_vms"]

    def test_keeps_active_running_vm(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-running", age_minutes=10))]
        runners = [{"name": "gha-general-running", "id": 8, "status": "online"}]

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(ep, "_delete_vm") as delete_mock:
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-running" in result["kept_vms"]
        delete_mock.assert_not_called()

    def test_deregisters_offline_zombie_runner(self):
        ep = _fresh_module()
        vms = []
        runners = [
            # Zombie: dispatcher-named, no live VM, offline
            {"name": "gha-general-zombie", "id": 99, "status": "offline"},
            # Non-dispatcher (legacy pool) — leave alone
            {"name": "github-runner-1", "id": 1, "status": "offline"},
            # Online runner with no VM — skip this pass (transient list lag)
            {"name": "gha-build-recent", "id": 100, "status": "online"},
        ]

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(ep, "deregister_runner") as dereg:
            result = ep.cleanup_orphans("ghp_test")

        dereg.assert_called_once_with("ghp_test", 99)
        assert result["deregistered_runners"] == ["gha-general-zombie"]

    def test_delete_failure_is_recorded(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-stuck", age_minutes=60))]
        runners = []

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(
            ep, "_delete_vm", return_value=("gha-general-stuck", "delete_failed")
        ):
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-stuck" in result["delete_failed"]
        assert "gha-general-stuck" not in result["deleted_vms"]


class TestStartupScript:
    """Sanity-check the inline startup script — it's tiny but failure modes are nasty."""

    def test_startup_script_uses_jitconfig(self):
        ep = _fresh_module()
        assert "--jitconfig" in ep.STARTUP_SCRIPT
        assert "shutdown -h" in ep.STARTUP_SCRIPT
        # Should NOT use the PAT-based registration flow
        assert "config.sh" not in ep.STARTUP_SCRIPT
        # Should pull the JIT config from instance metadata
        assert "metadata.google.internal" in ep.STARTUP_SCRIPT
