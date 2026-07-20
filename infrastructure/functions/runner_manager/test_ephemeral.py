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
