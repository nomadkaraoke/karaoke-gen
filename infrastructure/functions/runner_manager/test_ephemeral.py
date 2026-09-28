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

# Stub the rest of main.py's runtime deps so we can import it without the
# Cloud Function runtime installed locally.
for mod_name in ("functions_framework", "google.cloud.secretmanager", "flask"):
    sys.modules.setdefault(mod_name, MagicMock(name=mod_name))
sys.modules["functions_framework"].http = lambda f: f


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

    def test_windows_gpu_routes_to_windows_family(self):
        ep = _fresh_module()
        fam = ep.resolve_family(["self-hosted", "windows", "gpu"])
        assert fam.name == "gpu-windows"

    def test_windows_without_gpu_still_routes_to_windows_family(self):
        # Only one Windows family exists; better to run the job on it than
        # let a [self-hosted, windows] job queue forever.
        ep = _fresh_module()
        fam = ep.resolve_family(["self-hosted", "windows"])
        assert fam.name == "gpu-windows"

    def test_linux_gpu_does_not_route_to_windows(self):
        ep = _fresh_module()
        fam = ep.resolve_family(["self-hosted", "linux", "gpu"])
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
        assert "linux" in labels

    def test_windows_labels_advertise_windows_not_linux(self):
        ep = _fresh_module()
        labels = ep.runner_labels_for(ep.FAMILIES["gpu-windows"])
        assert "windows" in labels
        assert "linux" not in labels
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
        # All runners now get an ephemeral external IP to bypass the Cloud NAT
        # data-processing charge (runners were the NAT's only user).
        assert result["external_ip"] is True
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

    def test_failed_insert_operation_raises_not_silently_strands(self):
        # Fire-and-forget regression guard: the API accepted the insert but the
        # operation then FAILED. This must surface (raise) so the webhook
        # redelivers, not silently strand the queued job for 15 min.
        ep = _fresh_module()
        import pytest

        op = MagicMock(name="op")
        op.name = "operation-bad"
        op.result.side_effect = RuntimeError("INTERNAL_ERROR creating instance")

        compute_client = MagicMock()
        compute_client.insert.return_value = op
        ep._compute_client = compute_client

        with patch.object(ep, "mint_jit_config", return_value="JIT_TOKEN"):
            with pytest.raises(Exception, match="INTERNAL_ERROR"):
                ep.create_ephemeral_runner(["self-hosted", "linux", "gcp"], "ghp_test")
        # The insert op was confirmed (result() awaited).
        op.result.assert_called_once()

    def test_insert_operation_stockout_falls_back_to_secondary_zone(self):
        # Stockout can surface on the operation (not the initial insert call).
        # Confirming the op lets us still fall back to the secondary zone.
        ep = _fresh_module()

        primary_op = MagicMock(name="primary_op")
        primary_op.name = "operation-primary"
        primary_op.result.side_effect = Exception("ZONE_RESOURCE_POOL_EXHAUSTED")

        fallback_op = MagicMock(name="fallback_op")
        fallback_op.name = "operation-fallback"

        compute_client = MagicMock()
        compute_client.insert.side_effect = [primary_op, fallback_op]
        ep._compute_client = compute_client

        with patch.object(ep, "mint_jit_config", return_value="JIT_TOKEN"):
            result = ep.create_ephemeral_runner(["self-hosted", "gpu"], "ghp_test")

        assert result["zone"] == "us-east4-c"
        assert compute_client.insert.call_count == 2

    def test_insert_confirm_timeout_is_not_a_failure(self):
        # A polling timeout means "still provisioning" — the VM will register on
        # boot; treating it as a failure would wrongly reject a good launch.
        import concurrent.futures

        ep = _fresh_module()

        op = MagicMock(name="op")
        op.name = "operation-slow"
        op.result.side_effect = concurrent.futures.TimeoutError()

        compute_client = MagicMock()
        compute_client.insert.return_value = op
        ep._compute_client = compute_client

        with patch.object(ep, "mint_jit_config", return_value="JIT_TOKEN"):
            result = ep.create_ephemeral_runner(["self-hosted", "linux", "gcp"], "ghp_test")

        assert result["family"] == "general"
        assert result["zone"] == "us-central1-a"


class TestSchedulingPerFamily:
    """e2 instances reject on_host_maintenance=TERMINATE unless preemptible.

    Regression test for the 2026-05-17 cutover bug: dispatcher set TERMINATE
    unconditionally, causing every general/build VM create to fail with
    `BadRequest('e2 instances do not support onHostMaintenance=TERMINATE
    unless they are preemptible.')`.
    """

    def _build_for(self, family_name):
        ep = _fresh_module()
        ep._build_instance(
            name=f"gha-{family_name}-test",
            family=ep.FAMILIES[family_name],
            zone="us-central1-a",
            jit_config="JIT",
            image_self_link="projects/p/global/images/family/x",
            use_external_ip=False,
        )
        return _compute_stub.Scheduling.call_args.kwargs

    def test_e2_general_omits_terminate(self):
        kwargs = self._build_for("general")
        assert "on_host_maintenance" not in kwargs

    def test_e2_build_omits_terminate(self):
        kwargs = self._build_for("build")
        assert "on_host_maintenance" not in kwargs

    def test_gpu_keeps_terminate(self):
        kwargs = self._build_for("gpu")
        assert kwargs.get("on_host_maintenance") == "TERMINATE"

    def test_gpu_windows_keeps_terminate(self):
        kwargs = self._build_for("gpu-windows")
        assert kwargs.get("on_host_maintenance") == "TERMINATE"


class TestSecureBootPerFamily:
    """Secure Boot blocks unsigned DKMS-built NVIDIA modules.

    With Secure Boot on, the kernel is in lockdown=integrity mode and rejects
    unsigned modules ("Key was rejected by service"). The NVIDIA kernel module
    we install from the upstream CUDA repo is built by DKMS and unsigned, so
    Secure Boot must be off on GPU VMs. Non-GPU families don't have this
    constraint and keep Secure Boot on for defense in depth.
    """

    def _build_for(self, family_name):
        ep = _fresh_module()
        ep._build_instance(
            name=f"gha-{family_name}-test",
            family=ep.FAMILIES[family_name],
            zone="us-central1-a",
            jit_config="JIT",
            image_self_link="projects/p/global/images/family/x",
            use_external_ip=False,
        )
        return _compute_stub.ShieldedInstanceConfig.call_args.kwargs

    def test_general_has_secure_boot_on(self):
        assert self._build_for("general")["enable_secure_boot"] is True

    def test_build_has_secure_boot_on(self):
        assert self._build_for("build")["enable_secure_boot"] is True

    def test_gpu_has_secure_boot_off(self):
        assert self._build_for("gpu")["enable_secure_boot"] is False

    def test_vtpm_and_integrity_stay_on_for_gpu(self):
        kwargs = self._build_for("gpu")
        assert kwargs["enable_vtpm"] is True
        assert kwargs["enable_integrity_monitoring"] is True


def _make_instance(name, age_minutes, zone="us-central1-a", status="RUNNING"):
    inst = MagicMock()
    inst.name = name
    inst.status = status
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
        ), patch.object(
            ep, "_delete_vm", return_value=("gha-general-stuck", "deleted")
        ), patch.object(ep, "_log_serial_tail") as serial_mock:
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-stuck" in result["deleted_vms"]
        # Registration-failure deletes must capture serial output so we can
        # diagnose why STARTUP_SCRIPT exited without registering.
        serial_mock.assert_called_once_with("us-central1-a", "gha-general-stuck")

    def test_deletes_hung_vm_even_when_registered(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-hung", age_minutes=180))]
        runners = [{"name": "gha-general-hung", "id": 7, "status": "online"}]

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(
            ep, "_delete_vm", return_value=("gha-general-hung", "deleted")
        ), patch.object(ep, "_log_serial_tail") as serial_mock:
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-hung" in result["deleted_vms"]
        # Hung-after-registration is a different failure mode; serial dump
        # would mostly be runner job output, which is already in GHA logs.
        serial_mock.assert_not_called()

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

    def test_terminated_vm_deleted_immediately_even_when_young_and_registered(self):
        # A self-shutdown ephemeral VM still holds GPU quota until deleted;
        # it must be reaped on the next pass regardless of age/registration.
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-gpu-done", age_minutes=3, status="TERMINATED"))]
        runners = [{"name": "gha-gpu-done", "id": 12, "status": "offline"}]

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(
            ep, "_delete_vm", return_value=("gha-gpu-done", "deleted")
        ), patch.object(ep, "_log_serial_tail") as serial_mock:
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-gpu-done" in result["deleted_vms"]
        # Registered VM ran its job normally — no serial dump needed.
        serial_mock.assert_not_called()

    def test_terminated_unregistered_vm_dumps_serial_before_delete(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-gpu-neverran", age_minutes=5, status="TERMINATED"))]
        runners = []

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(
            ep, "_delete_vm", return_value=("gha-gpu-neverran", "deleted")
        ), patch.object(ep, "_log_serial_tail") as serial_mock:
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-gpu-neverran" in result["deleted_vms"]
        serial_mock.assert_called_once_with("us-central1-a", "gha-gpu-neverran")

    def test_delete_failure_is_recorded(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-stuck", age_minutes=60))]
        runners = []

        with self._patch_listing(ep, vms), patch.object(
            ep, "list_org_runners", return_value=runners
        ), patch.object(
            ep, "_delete_vm", return_value=("gha-general-stuck", "delete_failed")
        ), patch.object(ep, "_log_serial_tail"):
            result = ep.cleanup_orphans("ghp_test")

        assert "gha-general-stuck" in result["delete_failed"]
        assert "gha-general-stuck" not in result["deleted_vms"]


def _queued_job(job_id, labels, age_minutes, repo="karaoke-gen", run_id=1, name="Deploy"):
    created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return {
        "id": job_id,
        "run_id": run_id,
        "name": name,
        "repo": repo,
        "status": "queued",
        "labels": labels,
        "created_at": created.isoformat().replace("+00:00", "Z"),
    }


BUILD_LABELS = ["self-hosted", "linux", "gcp", "docker-build"]
GPU_LABELS = ["self-hosted", "linux", "gcp", "gpu"]


class TestRedispatchStalledJobs:
    """Stalled `queued` jobs get a fresh VM only when no spare runner is coming."""

    def _run(self, ep, jobs, vms=(), runners=None):
        with patch.object(ep, "list_queued_self_hosted_jobs", return_value=jobs), patch.object(
            ep,
            "create_ephemeral_runner",
            side_effect=lambda labels, pat: {"runner_name": f"gha-{ep.resolve_family(labels).name}-new"},
        ) as create:
            result = ep.redispatch_stalled_jobs("ghp_test", list(vms), runners or {})
        return result, create

    def test_dropped_webhook_job_is_redispatched(self):
        # The 2026-09-27 incident: deploy job queued, its webhook got a 429,
        # so no VM exists for it at all.
        ep = _fresh_module()
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=12)])

        create.assert_called_once_with(BUILD_LABELS, "ghp_test")
        assert result["dispatched"] == ["gha-build-new"]
        assert len(result["stalled_jobs"]) == 1

    def test_young_job_is_left_alone(self):
        ep = _fresh_module()
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=2)])

        create.assert_not_called()
        assert result["stalled_jobs"] == []

    def test_booting_vm_covers_the_stalled_job(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-build-booting", age_minutes=3))]
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=8)], vms=vms)

        create.assert_not_called()

    def test_registered_offline_booting_vm_covers_the_stalled_job(self):
        # generate-jitconfig registers the runner (offline) before the VM boots,
        # so this is what a fresh dispatch actually looks like.
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-gpu-booting", age_minutes=4))]
        runners = {"gha-gpu-booting": {"name": "gha-gpu-booting", "status": "offline", "busy": False}}
        result, create = self._run(ep, [_queued_job(1, GPU_LABELS, age_minutes=7)], vms=vms, runners=runners)

        create.assert_not_called()

    def test_given_up_job_does_not_consume_spare_capacity(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-build-idle", age_minutes=10))]
        runners = {"gha-build-idle": {"name": "gha-build-idle", "status": "online", "busy": False}}
        jobs = [_queued_job(1, BUILD_LABELS, age_minutes=90), _queued_job(2, BUILD_LABELS, age_minutes=8)]
        result, create = self._run(ep, jobs, vms=vms, runners=runners)

        create.assert_not_called()
        assert result["given_up"] == [1]

    def test_idle_registered_runner_covers_the_stalled_job(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-build-idle", age_minutes=40))]
        runners = {"gha-build-idle": {"name": "gha-build-idle", "status": "online", "busy": False}}
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=8)], vms=vms, runners=runners)

        create.assert_not_called()

    def test_busy_runner_does_not_count_as_spare(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-build-busy", age_minutes=10))]
        runners = {"gha-build-busy": {"name": "gha-build-busy", "status": "online", "busy": True}}
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=8)], vms=vms, runners=runners)

        create.assert_called_once()

    def test_never_registered_vm_past_grace_does_not_count_as_spare(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-build-dud", age_minutes=45))]
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=40)], vms=vms)

        create.assert_called_once()

    def test_terminated_vm_does_not_count_as_spare(self):
        # GPU-preempted runner (2026-09-25 incident shape).
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-gpu-preempted", age_minutes=10, status="TERMINATED"))]
        result, create = self._run(ep, [_queued_job(1, GPU_LABELS, age_minutes=20)], vms=vms)

        create.assert_called_once_with(GPU_LABELS, "ghp_test")

    def test_spare_capacity_is_per_family(self):
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-general-booting", age_minutes=2))]
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=8)], vms=vms)

        create.assert_called_once_with(BUILD_LABELS, "ghp_test")

    def test_young_jobs_consume_spare_before_stalled_ones_are_covered(self):
        # One booting VM, one young + one stalled job: the booting VM can only
        # take one of them, so exactly one extra VM is launched.
        ep = _fresh_module()
        vms = [("us-central1-a", _make_instance("gha-build-booting", age_minutes=1))]
        jobs = [_queued_job(1, BUILD_LABELS, age_minutes=1), _queued_job(2, BUILD_LABELS, age_minutes=9)]
        result, create = self._run(ep, jobs, vms=vms)

        assert create.call_count == 1

    def test_gives_up_on_jobs_queued_past_limit(self):
        # Fleet-wide runner breakage must not become a VM-launch loop.
        ep = _fresh_module()
        result, create = self._run(ep, [_queued_job(1, BUILD_LABELS, age_minutes=90)])

        create.assert_not_called()
        assert result["given_up"] == [1]

    def test_launches_capped_per_tick(self):
        ep = _fresh_module(MAX_REDISPATCH_PER_TICK="2")
        jobs = [_queued_job(i, BUILD_LABELS, age_minutes=10) for i in range(4)]
        result, create = self._run(ep, jobs)

        assert create.call_count == 2

    def test_dispatch_failure_is_recorded_and_others_continue(self):
        ep = _fresh_module()
        jobs = [_queued_job(1, BUILD_LABELS, age_minutes=10), _queued_job(2, BUILD_LABELS, age_minutes=10)]
        outcomes = iter([RuntimeError("stockout"), {"runner_name": "gha-build-ok"}])

        def fake_create(labels, pat):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(ep, "list_queued_self_hosted_jobs", return_value=jobs), patch.object(
            ep, "create_ephemeral_runner", side_effect=fake_create
        ):
            result = ep.redispatch_stalled_jobs("ghp_test", [], {})

        assert result["dispatched"] == ["gha-build-ok"]
        assert len(result["dispatch_failed"]) == 1


class TestFamilyOfRunnerName:
    def test_parses_families(self):
        ep = _fresh_module()
        assert ep._family_of_runner_name("gha-build-ff3b555e0491") == "build"
        assert ep._family_of_runner_name("gha-gpu-windows-abc123") == "gpu-windows"
        assert ep._family_of_runner_name("github-runner-1") is None


class TestListQueuedSelfHostedJobs:
    def test_collects_queued_self_hosted_jobs_across_repos(self):
        ep = _fresh_module()

        def fake_request(method, path, pat, body=None):
            if path.startswith("/orgs/test-org/repos"):
                return [{"name": "karaoke-gen"}, {"name": "old-repo", "archived": True}, {"name": "flacfetch"}]
            if path.startswith("/repos/test-org/karaoke-gen/actions/runs?status=in_progress"):
                return {"workflow_runs": [{"id": 11}]}
            if path.startswith("/repos/test-org/karaoke-gen/actions/runs?status=queued"):
                return {"workflow_runs": [{"id": 11}]}  # duplicate across statuses
            if path.startswith("/repos/test-org/karaoke-gen/actions/runs/11/jobs"):
                return {
                    "jobs": [
                        {"id": 1, "status": "queued", "labels": BUILD_LABELS},
                        {"id": 2, "status": "queued", "labels": ["ubuntu-latest"]},
                        {"id": 3, "status": "completed", "labels": BUILD_LABELS},
                    ]
                }
            if path.startswith("/repos/test-org/flacfetch/"):
                raise RuntimeError("403 no access")
            if path.startswith("/repos/test-org/old-repo/"):
                raise AssertionError("archived repos must be skipped")
            return {"workflow_runs": []}

        with patch.object(ep, "_github_request", side_effect=fake_request) as req:
            jobs = ep.list_queued_self_hosted_jobs("ghp_test")

        assert [(j["repo"], j["id"]) for j in jobs] == [("karaoke-gen", 1)]
        # Run 11 appeared under both statuses but its jobs are fetched once.
        job_calls = [c for c in req.call_args_list if "/runs/11/jobs" in c.args[1]]
        assert len(job_calls) == 1


class TestCleanupTriggersRedispatch:
    def test_redispatch_sees_only_surviving_vms(self):
        ep = _fresh_module()
        vms = [
            ("us-central1-a", _make_instance("gha-build-dead", age_minutes=3, status="TERMINATED")),
            ("us-central1-a", _make_instance("gha-build-booting", age_minutes=2)),
        ]
        with patch.object(ep, "_list_all_ephemeral_vms", return_value=vms), patch.object(
            ep, "list_org_runners", return_value=[]
        ), patch.object(ep, "_delete_vm", return_value=("gha-build-dead", "deleted")), patch.object(
            ep, "_log_serial_tail"
        ), patch.object(ep, "redispatch_stalled_jobs", return_value={"dispatched": []}) as redispatch:
            result = ep.cleanup_orphans("ghp_test")

        live = [inst.name for _, inst in redispatch.call_args.args[1]]
        assert live == ["gha-build-booting"]
        assert result["redispatch"] == {"dispatched": []}

    def test_redispatch_error_does_not_break_cleanup(self):
        ep = _fresh_module()
        with patch.object(ep, "_list_all_ephemeral_vms", return_value=[]), patch.object(
            ep, "list_org_runners", return_value=[]
        ), patch.object(ep, "redispatch_stalled_jobs", side_effect=RuntimeError("github down")):
            result = ep.cleanup_orphans("ghp_test")

        assert result["redispatch"] == {"error": "github down"}
        assert result["deleted_vms"] == []

    def test_kill_switch_disables_redispatch(self):
        ep = _fresh_module(REDISPATCH_ENABLED="false")
        with patch.object(ep, "_list_all_ephemeral_vms", return_value=[]), patch.object(
            ep, "list_org_runners", return_value=[]
        ), patch.object(ep, "redispatch_stalled_jobs") as redispatch:
            result = ep.cleanup_orphans("ghp_test")

        redispatch.assert_not_called()
        assert "redispatch" not in result


class TestLogSerialTail:
    """Serial console capture is best-effort and must never block the delete."""

    def test_prints_tail_of_serial_output(self, capsys):
        ep = _fresh_module()
        long_log = "x" * 200_000  # bigger than the 50_000-byte cap
        fake_output = MagicMock(contents=long_log)
        fake_client = MagicMock()
        fake_client.get_serial_port_output.return_value = fake_output

        with patch.object(ep, "get_compute_client", return_value=fake_client):
            ep._log_serial_tail("us-central1-a", "gha-gpu-abc")

        captured = capsys.readouterr().out
        assert "serial console (last 50000 bytes) for gha-gpu-abc" in captured
        assert "end serial console for gha-gpu-abc" in captured
        # Tail was truncated, not the full log
        assert len(captured) < 200_000

    def test_swallows_fetch_errors(self, capsys):
        ep = _fresh_module()
        fake_client = MagicMock()
        fake_client.get_serial_port_output.side_effect = Exception("permission denied")

        with patch.object(ep, "get_compute_client", return_value=fake_client):
            # Must not raise — cleanup path depends on this being best-effort
            ep._log_serial_tail("us-central1-a", "gha-gpu-xyz")

        assert "Could not fetch serial output for gha-gpu-xyz" in capsys.readouterr().out


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

    def test_windows_startup_script_uses_jitconfig_and_shuts_down(self):
        ep = _fresh_module()
        ps1 = ep.WINDOWS_STARTUP_SCRIPT_PS1
        assert "--jitconfig" in ps1
        assert "run.cmd" in ps1
        assert "shutdown /s" in ps1
        assert "metadata.google.internal" in ps1
        # finally-block shutdown must survive a runner crash
        assert "finally" in ps1


class TestStartupMetadataKeyPerFamily:
    """Windows VMs only execute `windows-startup-script-ps1`; Linux VMs only
    execute `startup-script`. Passing the wrong key silently does nothing and
    the VM never registers."""

    def _metadata_keys_for(self, family_name):
        ep = _fresh_module()
        _compute_stub.Items.reset_mock()
        ep._build_instance(
            name=f"gha-{family_name}-test",
            family=ep.FAMILIES[family_name],
            zone="us-central1-a",
            jit_config="JIT",
            image_self_link="projects/p/global/images/family/x",
            use_external_ip=False,
        )
        return {c.kwargs.get("key"): c.kwargs.get("value") for c in _compute_stub.Items.call_args_list}

    def test_linux_families_use_startup_script(self):
        for fam in ("general", "build", "gpu"):
            keys = self._metadata_keys_for(fam)
            assert "startup-script" in keys, fam
            assert "windows-startup-script-ps1" not in keys, fam

    def test_windows_family_uses_ps1_key(self):
        keys = self._metadata_keys_for("gpu-windows")
        assert "windows-startup-script-ps1" in keys
        assert "startup-script" not in keys
        assert "run.cmd" in keys["windows-startup-script-ps1"]

    def test_jit_config_present_for_all_families(self):
        for fam in ("general", "build", "gpu", "gpu-windows"):
            keys = self._metadata_keys_for(fam)
            assert keys.get("jit-config") == "JIT", fam


class TestSchedulerAuthGate:
    """The scheduler entry point in main.py must reject unauthenticated callers."""

    def _import_main(self, **env):
        for mod in ("main", "ephemeral"):
            sys.modules.pop(mod, None)
        base_env = {
            "GCP_PROJECT": "test-project",
            "GCP_ZONE": "us-central1-a",
            "GCP_FALLBACK_ZONE": "us-east4-c",
            "GITHUB_ORG": "test-org",
            **env,
        }
        with patch.dict(os.environ, base_env, clear=False):
            import main

            main._compute_client = None
            main._secret_client = None
            main._webhook_secret = "test-secret"
            main._github_pat = "ghp_test"
            return main

    def _request(self, *, action, headers=None):
        req = MagicMock()
        req.args = {"action": action} if action else {}
        req.args = MagicMock(get=lambda key, default=None: ({"action": action} if action else {}).get(key, default))
        req.headers = MagicMock(get=lambda key, default=None: (headers or {}).get(key, default))
        return req

    def test_scheduler_without_bearer_token_returns_403(self):
        main = self._import_main()
        req = self._request(action="check_idle", headers={})
        body, status = main.handle_request(req)
        assert status == 403

    def test_scheduler_with_short_bearer_token_returns_403(self):
        main = self._import_main()
        # "Bearer x" is too short to be a real JWT — defense against trivial spoof.
        req = self._request(action="check_idle", headers={"Authorization": "Bearer x"})
        body, status = main.handle_request(req)
        assert status == 403

    def test_scheduler_with_bearer_token_dispatches_orphan_cleanup(self):
        main = self._import_main()
        req = self._request(
            action="check_idle",
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYwoxMjM"},
        )
        # The scheduler tick now always routes to orphan cleanup — verify
        # ephemeral.cleanup_orphans is the call target. Patching by import
        # path because main imports ephemeral lazily inside the handler.
        import ephemeral

        with patch.object(ephemeral, "cleanup_orphans", return_value={"deleted_vms": [], "kept_vms": []}) as fn:
            result = main.handle_request(req)
        fn.assert_called_once()
        # response shape: (body, status, headers)
        assert result[1] == 200
