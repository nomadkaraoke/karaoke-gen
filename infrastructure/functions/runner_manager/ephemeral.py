"""
Ephemeral GitHub Actions runner dispatcher.

Creates a single-use GCE VM per `workflow_job.queued` webhook, registers it as
a JIT-config ephemeral runner with the nomadkaraoke org, and lets the VM
self-destruct after the job runs. A scheduled pass (every 5 min) catches VMs
whose job died before the runner could de-register itself, and re-dispatches
self-hosted jobs left `queued` with no runner coming (dropped webhook, VM that
never registered or was preempted).

This module is invoked from main.py when RUNNER_MODE=ephemeral.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from google.cloud import compute_v1

PROJECT_ID = os.environ.get("GCP_PROJECT", "nomadkaraoke")
PRIMARY_ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
FALLBACK_ZONE = os.environ.get("GCP_FALLBACK_ZONE", "us-east4-c")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "nomadkaraoke")
RUNNER_GROUP_ID = int(os.environ.get("RUNNER_GROUP_ID", "1"))
RUNNER_SERVICE_ACCOUNT = os.environ.get(
    "RUNNER_SERVICE_ACCOUNT", "github-runner@nomadkaraoke.iam.gserviceaccount.com"
)

# Orphan cleanup thresholds
ORPHAN_GRACE_MINUTES = int(os.environ.get("ORPHAN_GRACE_MINUTES", "30"))
MAX_VM_LIFETIME_MINUTES = int(os.environ.get("MAX_VM_LIFETIME_MINUTES", "120"))

# Stalled-job re-dispatch: a self-hosted job still `queued` this long after
# creation, with no spare runner capacity for its family, gets a fresh VM.
# Covers dropped webhooks (Cloud Run 429), VMs that never register, and VMs
# preempted before picking up their job. Kill switch: REDISPATCH_ENABLED=false.
REDISPATCH_ENABLED = os.environ.get("REDISPATCH_ENABLED", "true").lower() != "false"
REDISPATCH_AFTER_MINUTES = int(os.environ.get("REDISPATCH_AFTER_MINUTES", "5"))
MAX_REDISPATCH_PER_TICK = int(os.environ.get("MAX_REDISPATCH_PER_TICK", "5"))
# Stop retrying a job queued this long: if runners are broken fleet-wide (e.g.
# a deprecated baked runner version — every VM registers, gets rejected, halts)
# re-dispatching would launch a doomed VM per job every tick. Past this age a
# human needs to look; the existing dispatcher alerts cover that.
REDISPATCH_GIVE_UP_MINUTES = int(os.environ.get("REDISPATCH_GIVE_UP_MINUTES", "60"))

# Max seconds to block confirming the VM *insert operation* succeeded (NOT VM
# boot — just that the API accepted and fulfilled instance creation). A timeout
# here means "still provisioning" and is treated as success; only an explicit
# operation error is a failure. Kept well under the dispatcher's function
# timeout so we never get killed mid-wait.
INSERT_CONFIRM_TIMEOUT_SECONDS = int(os.environ.get("INSERT_CONFIRM_TIMEOUT_SECONDS", "90"))

VM_PURPOSE_LABEL = "gha-ephemeral-runner"

GITHUB_API = "https://api.github.com"


@dataclass(frozen=True)
class FamilySpec:
    """Per-image-family runtime configuration."""

    name: str
    machine_type: str
    disk_size_gb: int
    image_family: str  # GCE image family that the dispatcher selects
    extra_runner_labels: tuple[str, ...]
    has_gpu: bool
    needs_external_ip_in_fallback_zone: bool
    # Advertised OS label; also selects the startup-script metadata key —
    # Windows VMs only execute scripts under `windows-startup-script-ps1`
    # and silently ignore `startup-script`.
    os_label: str = "linux"


FAMILIES: dict[str, FamilySpec] = {
    "general": FamilySpec(
        name="general",
        machine_type="e2-standard-4",
        disk_size_gb=100,
        image_family="gha-runner-general",
        extra_runner_labels=("x64", "gcp", "large-disk"),
        has_gpu=False,
        needs_external_ip_in_fallback_zone=False,
    ),
    "build": FamilySpec(
        name="build",
        machine_type="e2-standard-8",
        disk_size_gb=100,
        image_family="gha-runner-build",
        extra_runner_labels=("x64", "gcp", "large-disk", "docker-build"),
        has_gpu=False,
        needs_external_ip_in_fallback_zone=False,
    ),
    "gpu": FamilySpec(
        name="gpu",
        machine_type="n1-standard-4",
        # GPU image bakes ~14GB of audio-separator models and is built on a
        # 200GB boot disk, so the disk we create here must be >= 200GB.
        disk_size_gb=200,
        image_family="gha-runner-gpu",
        extra_runner_labels=("x64", "gcp", "gpu"),
        has_gpu=True,
        # No Cloud NAT in us-east4 yet; rely on an ephemeral external IP for the
        # rare fallback case rather than provisioning region-wide NAT.
        needs_external_ip_in_fallback_zone=True,
    ),
    "gpu-windows": FamilySpec(
        name="gpu-windows",
        machine_type="n1-standard-4",
        # Windows Server base image is 50GB; the bake uses a 100GB disk
        # (models + drivers), so the disk we create here must be >= 100GB.
        disk_size_gb=100,
        image_family="gha-runner-gpu-windows",
        extra_runner_labels=("x64", "gcp", "gpu"),
        has_gpu=True,
        needs_external_ip_in_fallback_zone=True,
        os_label="windows",
    ),
}


# Inline startup script kept tiny — the heavy lifting is baked into the image.
# JIT config is fetched from instance metadata so it stays out of public logs.
STARTUP_SCRIPT = r"""#!/bin/bash
set -uo pipefail
# Always shut down on exit so the auto-delete boot disk goes away with us.
trap 'shutdown -h +1' EXIT

JIT=$(curl -s -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/attributes/jit-config")
if [[ -z "$JIT" ]]; then
    echo "ERROR: jit-config metadata missing" >&2
    exit 1
fi

cd /home/runner/actions-runner
# --jitconfig implies --ephemeral: runs one job, deregisters, exits.
sudo -u runner ./run.sh --jitconfig "$JIT"
"""


# Windows equivalent, delivered via the `windows-startup-script-ps1` metadata
# key. Runs as SYSTEM on every boot — fine for a single-use VM. The finally
# block guarantees shutdown even if the runner crashes, so the orphan-cleanup
# pass can delete the stopped VM.
WINDOWS_STARTUP_SCRIPT_PS1 = r"""$ErrorActionPreference = "Stop"
try {
    $jit = Invoke-RestMethod -Headers @{ "Metadata-Flavor" = "Google" } `
        -Uri "http://metadata.google.internal/computeMetadata/v1/instance/attributes/jit-config"
    if (-not $jit) { throw "jit-config metadata missing" }
    Set-Location "C:\actions-runner"
    # --jitconfig implies --ephemeral: runs one job, deregisters, exits.
    & .\run.cmd --jitconfig "$jit"
} finally {
    shutdown /s /t 30
}
"""


# ---------------------------------------------------------------------------
# Lazy GCE / HTTP clients (so unit tests can mock them)
# ---------------------------------------------------------------------------

_compute_client: compute_v1.InstancesClient | None = None


def get_compute_client() -> compute_v1.InstancesClient:
    global _compute_client
    if _compute_client is None:
        _compute_client = compute_v1.InstancesClient()
    return _compute_client


# ---------------------------------------------------------------------------
# Label / family resolution
# ---------------------------------------------------------------------------


def resolve_family(labels: Iterable[str]) -> FamilySpec:
    """Pick the right image family for a queued job's labels.

    Precedence: windows → gpu → build → general. A job that asks for both
    ``gpu`` and ``docker-build`` shouldn't exist today; if it ever does we'd
    want a separate image, not a coin-flip.

    Any ``windows`` job routes to the (only) Windows family — gpu-windows —
    even without a ``gpu`` label, so it runs rather than queueing forever.
    """
    label_set = {label.lower() for label in labels}
    if "windows" in label_set:
        return FAMILIES["gpu-windows"]
    if "gpu" in label_set:
        return FAMILIES["gpu"]
    if "docker-build" in label_set:
        return FAMILIES["build"]
    return FAMILIES["general"]


def runner_labels_for(family: FamilySpec) -> list[str]:
    """Compose the labels advertised to GitHub by this runner."""
    return ["self-hosted", family.os_label, *family.extra_runner_labels]


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------


def _github_request(method: str, path: str, pat: str, body: dict | None = None) -> dict | list | None:
    """Make a GitHub REST API call. Returns parsed JSON or None for 204."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{GITHUB_API}{path}",
        method=method,
        data=data,
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.status == 204:
            return None
        return json.loads(response.read().decode("utf-8"))


def mint_jit_config(
    pat: str,
    runner_name: str,
    family: FamilySpec,
) -> str:
    """Mint a just-in-time runner config for org-level ephemeral registration."""
    payload = {
        "name": runner_name,
        "runner_group_id": RUNNER_GROUP_ID,
        "labels": runner_labels_for(family),
        "work_folder": "_work",
    }
    result = _github_request(
        "POST",
        f"/orgs/{GITHUB_ORG}/actions/runners/generate-jitconfig",
        pat,
        payload,
    )
    if not isinstance(result, dict) or "encoded_jit_config" not in result:
        raise RuntimeError(f"JIT config response missing encoded_jit_config: {result!r}")
    return result["encoded_jit_config"]


def list_org_runners(pat: str) -> list[dict]:
    """List all org-level self-hosted runners (paginated, up to 1000)."""
    runners: list[dict] = []
    per_page = 100
    page = 1
    while page <= 10:
        result = _github_request(
            "GET",
            f"/orgs/{GITHUB_ORG}/actions/runners?per_page={per_page}&page={page}",
            pat,
        )
        if not isinstance(result, dict):
            break
        chunk = result.get("runners", [])
        runners.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1
    return runners


def deregister_runner(pat: str, runner_id: int) -> None:
    """Forcefully de-register a runner from the org (zombie cleanup)."""
    try:
        _github_request(
            "DELETE",
            f"/orgs/{GITHUB_ORG}/actions/runners/{runner_id}",
            pat,
        )
    except urllib.error.HTTPError as exc:
        # 404 = runner already gone; treat as success.
        if exc.code != 404:
            raise


# ---------------------------------------------------------------------------
# GCE VM creation
# ---------------------------------------------------------------------------


def _image_family_url(family: FamilySpec) -> str:
    """Return the GCE image-family URL for use as a disk source_image.

    Using the family URL means GCE selects the newest non-deprecated image
    automatically, and we don't need ``compute.images.get`` on the dispatcher
    SA — instanceAdmin's ``compute.images.useReadOnly`` is sufficient.
    """
    return f"projects/{PROJECT_ID}/global/images/family/{family.image_family}"


def _build_instance(
    *,
    name: str,
    family: FamilySpec,
    zone: str,
    jit_config: str,
    image_self_link: str,
    use_external_ip: bool,
) -> compute_v1.Instance:
    """Assemble the GCE Instance proto for an ephemeral runner."""
    boot_disk = compute_v1.AttachedDisk(
        auto_delete=True,
        boot=True,
        initialize_params=compute_v1.AttachedDiskInitializeParams(
            disk_size_gb=family.disk_size_gb,
            disk_type=f"projects/{PROJECT_ID}/zones/{zone}/diskTypes/pd-balanced",
            source_image=image_self_link,
        ),
    )

    network_interface = compute_v1.NetworkInterface(
        network=f"projects/{PROJECT_ID}/global/networks/default",
    )
    if use_external_ip:
        # Ephemeral external IP — costs nothing while the VM is running with
        # one attached; not provisioned in advance.
        network_interface.access_configs = [
            compute_v1.AccessConfig(name="External NAT", type_="ONE_TO_ONE_NAT"),
        ]

    if family.os_label == "windows":
        startup_item = compute_v1.Items(
            key="windows-startup-script-ps1", value=WINDOWS_STARTUP_SCRIPT_PS1
        )
    else:
        startup_item = compute_v1.Items(key="startup-script", value=STARTUP_SCRIPT)

    metadata = compute_v1.Metadata(
        items=[
            compute_v1.Items(key="jit-config", value=jit_config),
            startup_item,
            compute_v1.Items(key="enable-oslogin", value="TRUE"),
        ]
    )

    # GPU VMs cannot live-migrate, so they must use TERMINATE. e2 machine types
    # reject TERMINATE unless preemptible, so we leave on_host_maintenance unset
    # (GCE defaults to MIGRATE) for the non-GPU e2 families.
    scheduling_kwargs = {"automatic_restart": False, "preemptible": False}
    if family.has_gpu:
        scheduling_kwargs["on_host_maintenance"] = "TERMINATE"
    scheduling = compute_v1.Scheduling(**scheduling_kwargs)

    instance = compute_v1.Instance(
        name=name,
        machine_type=f"projects/{PROJECT_ID}/zones/{zone}/machineTypes/{family.machine_type}",
        disks=[boot_disk],
        network_interfaces=[network_interface],
        metadata=metadata,
        scheduling=scheduling,
        service_accounts=[
            compute_v1.ServiceAccount(
                email=RUNNER_SERVICE_ACCOUNT,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        ],
        labels={
            "purpose": VM_PURPOSE_LABEL,
            "family": family.name,
            "managed-by": "runner-dispatcher",
        },
        tags=compute_v1.Tags(items=["gha-runner", "github-runner"]),
        # Secure Boot is incompatible with GPU VMs: the NVIDIA kernel module is
        # built locally via DKMS (from the upstream CUDA repo, not Debian's
        # signed nvidia-driver) and is therefore unsigned. With Secure Boot on,
        # the kernel is in lockdown=integrity mode and rejects unsigned modules
        # ("Key was rejected by service"), so /dev/nvidia* never appears and
        # nvidia-persistenced fails. Legacy GPU runners ran without Secure Boot
        # for the same reason. Keep it on for general/build where there's no
        # unsigned-module requirement.
        shielded_instance_config=compute_v1.ShieldedInstanceConfig(
            enable_secure_boot=not family.has_gpu,
            enable_vtpm=True,
            enable_integrity_monitoring=True,
        ),
    )

    if family.has_gpu:
        instance.guest_accelerators = [
            compute_v1.AcceleratorConfig(
                accelerator_count=1,
                accelerator_type=f"projects/{PROJECT_ID}/zones/{zone}/acceleratorTypes/nvidia-tesla-t4",
            )
        ]

    return instance


_ZONE_EXHAUSTED_TOKENS = (
    "ZONE_RESOURCE_POOL_EXHAUSTED",
    "ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS",
    "QUOTA_EXCEEDED",
    "stockout",
)


def _is_zone_exhausted(exc: Exception) -> bool:
    msg = str(exc)
    return any(tok in msg for tok in _ZONE_EXHAUSTED_TOKENS)


def _confirm_insert_succeeded(operation, *, zone: str, runner_name: str) -> None:
    """Block briefly on the VM insert operation and raise if it actually failed.

    Previously the dispatcher fired the insert and returned immediately
    ("fire and forget"). The failure mode: the API *accepts* the insert but the
    operation then fails (zone stockout surfaced on the operation rather than
    the initial call, quota, transient INTERNAL_ERROR, …). With fire-and-forget
    that left the queued CI job with no runner and NO signal until the 15-min
    orphan sweep / GitHub webhook redelivery — the "stuck queued" pain.

    Confirming the operation lets the caller:
      * fall back to the secondary zone on a stockout that shows up late, and
      * re-raise a genuine failure so the webhook returns non-200 and GitHub
        redelivers the ``workflow_job.queued`` event promptly.

    ``ExtendedOperation.result()`` blocks until the zonal insert operation
    resolves and raises on error. A polling *timeout* is NOT a failure — the
    instance is still being created and will register once it boots — so we
    swallow it and let the VM come up asynchronously.
    """
    result_fn = getattr(operation, "result", None)
    if not callable(result_fn):
        # Older compute client without ExtendedOperation.result(); nothing to
        # wait on — preserve prior fire-and-forget behavior rather than break.
        return
    try:
        operation.result(timeout=INSERT_CONFIRM_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        print(
            f"Insert op for {runner_name} in {zone} still provisioning after "
            f"{INSERT_CONFIRM_TIMEOUT_SECONDS}s — continuing; the VM will "
            f"register once it finishes booting."
        )


def create_ephemeral_runner(labels: Iterable[str], pat: str) -> dict:
    """Mint a JIT config and launch a fresh GCE VM to run a single CI job.

    Tries the primary zone first; on capacity-exhaustion errors falls back to
    the secondary zone (with ephemeral external IP for variants that need it).
    """
    family = resolve_family(labels)
    runner_name = f"gha-{family.name}-{uuid.uuid4().hex[:12]}"

    jit_config = mint_jit_config(pat, runner_name, family)

    image_url = _image_family_url(family)

    zones = [PRIMARY_ZONE]
    if FALLBACK_ZONE and FALLBACK_ZONE != PRIMARY_ZONE:
        zones.append(FALLBACK_ZONE)

    last_error: Exception | None = None
    for zone in zones:
        # Always attach an ephemeral external IP so runner egress (Docker
        # pulls, dependency downloads) leaves via the VM's own IP and bypasses
        # the Cloud NAT data-processing charge (~$26/mo — runners were the
        # NAT's only user). Ephemeral IPs are free while attached to a running
        # VM, runners are short-lived, and us-central1 has ample IN_USE_ADDRESSES
        # quota (limit 69). Supersedes the prior fallback-zone-only logic;
        # `needs_external_ip_in_fallback_zone` is retained for documentation.
        use_external_ip = True
        instance = _build_instance(
            name=runner_name,
            family=family,
            zone=zone,
            jit_config=jit_config,
            image_self_link=image_url,
            use_external_ip=use_external_ip,
        )
        try:
            operation = get_compute_client().insert(
                project=PROJECT_ID,
                zone=zone,
                instance_resource=instance,
            )
            # Confirm the insert operation actually succeeded instead of
            # fire-and-forget. A failed insert that surfaces on the operation
            # (late stockout, quota, transient error) would otherwise silently
            # strand the queued job; raising here triggers the zone-fallback
            # below or webhook redelivery. VM *boot* still happens async.
            _confirm_insert_succeeded(operation, zone=zone, runner_name=runner_name)
            print(
                f"Dispatched ephemeral runner: name={runner_name} "
                f"family={family.name} zone={zone} image={image_url} "
                f"operation={operation.name if hasattr(operation, 'name') else '<unknown>'}"
            )
            return {
                "runner_name": runner_name,
                "family": family.name,
                "zone": zone,
                "external_ip": use_external_ip,
            }
        except Exception as exc:  # noqa: BLE001 — surfacing to caller is the point
            last_error = exc
            if _is_zone_exhausted(exc) and zone != zones[-1]:
                print(
                    f"Zone {zone} exhausted for {family.name}; trying fallback. err={exc!r}"
                )
                continue
            raise

    raise RuntimeError(
        f"Failed to create ephemeral runner {runner_name} in any zone: {last_error!r}"
    )


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------


def _list_all_ephemeral_vms() -> list[tuple[str, compute_v1.Instance]]:
    """Return (zone, instance) for every ephemeral runner VM in the project.

    Uses ``aggregatedList`` so the cleanup pass picks up VMs in any zone —
    not just the currently-configured primary/fallback. Without this,
    reconfiguring ``GCP_ZONE`` or ``GCP_FALLBACK_ZONE`` would orphan any VMs
    still running in the old zones.
    """
    client = get_compute_client()
    request = compute_v1.AggregatedListInstancesRequest(
        project=PROJECT_ID,
        filter=f'labels.purpose="{VM_PURPOSE_LABEL}"',
    )
    pairs: list[tuple[str, compute_v1.Instance]] = []
    for zone_path, scoped in client.aggregated_list(request=request):
        # zone_path is like "zones/us-central1-a"; aggregated_list also yields
        # "global" / "regions/..." entries that don't contain instances.
        if not zone_path.startswith("zones/"):
            continue
        zone = zone_path.removeprefix("zones/")
        for instance in getattr(scoped, "instances", []) or []:
            pairs.append((zone, instance))
    return pairs


def _vm_age_minutes(instance: compute_v1.Instance) -> float:
    if not instance.creation_timestamp:
        return float("inf")
    created = datetime.fromisoformat(instance.creation_timestamp.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 60


# Serial output captured before delete is capped — long boot logs from
# multiple failed VMs could blow up Cloud Logging volume otherwise.
_SERIAL_TAIL_BYTES = 50_000


def _log_serial_tail(zone: str, name: str) -> None:
    """Fetch and print the tail of the VM's serial console.

    Called only when we're about to delete a VM that never registered as a
    GHA runner — gives us a one-shot look at why STARTUP_SCRIPT failed
    (`./run.sh --jitconfig` exit reason, network errors, etc.) without
    requiring an Ops Agent on the image.
    """
    try:
        # No port kwarg: the deployed google-cloud-compute client rejects it
        # (TypeError: unexpected keyword argument 'port', seen 2026-07-20 in
        # prod logs — it silently disabled ALL serial capture). Port 1 is the
        # API default anyway.
        out = get_compute_client().get_serial_port_output(
            project=PROJECT_ID, zone=zone, instance=name
        )
        contents = out.contents or ""
        tail = contents[-_SERIAL_TAIL_BYTES:]
        print(f"--- serial console (last {len(tail)} bytes) for {name} ---")
        print(tail)
        print(f"--- end serial console for {name} ---")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not fetch serial output for {name}: {exc!r}")


def _delete_vm(zone: str, name: str) -> tuple[str, str]:
    try:
        get_compute_client().delete(project=PROJECT_ID, zone=zone, instance=name)
        return name, "deleted"
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to delete {name} in {zone}: {exc!r}")
        return name, "delete_failed"


def cleanup_orphans(pat: str) -> dict:
    """Reconcile GCE VMs with org-runner registrations.

    Deletes:
      - VMs whose GHA runner has already de-registered and that are older than
        ORPHAN_GRACE_MINUTES (catches startup failures + finished jobs whose
        self-shutdown didn't fire).
      - VMs older than MAX_VM_LIFETIME_MINUTES regardless (hung jobs).

    De-registers:
      - GHA runners with the dispatcher's name prefix that have no live VM
        (zombie registrations).
    """
    result = {
        "deleted_vms": [],
        "kept_vms": [],
        "delete_failed": [],
        "deregistered_runners": [],
        "deregister_failed": [],
    }

    vms = _list_all_ephemeral_vms()
    runners = list_org_runners(pat)
    runner_by_name = {r["name"]: r for r in runners}
    vm_names = {instance.name for _, instance in vms}

    to_delete: list[tuple[str, str]] = []
    for zone, instance in vms:
        age = _vm_age_minutes(instance)
        registered = instance.name in runner_by_name
        status = getattr(instance, "status", "")

        if status == "TERMINATED":
            # Ephemeral runners self-shutdown after their single job — a
            # stopped VM will never work again, but its attached GPU still
            # counts against NVIDIA_T4_GPUS quota until the VM is DELETED.
            # Leaving corpses for the age-based rules below meant back-to-back
            # CI waves exhausted quota and the (fire-and-forget) dispatcher
            # silently stranded queued jobs. Reap immediately.
            print(f"{instance.name}: TERMINATED — deleting immediately to release GPU quota")
            if not registered:
                _log_serial_tail(zone, instance.name)
            to_delete.append((zone, instance.name))
        elif age >= MAX_VM_LIFETIME_MINUTES:
            print(f"{instance.name}: age={age:.1f}min > max, deleting")
            to_delete.append((zone, instance.name))
        elif not registered and age >= ORPHAN_GRACE_MINUTES:
            print(
                f"{instance.name}: age={age:.1f}min, no GHA runner registration — deleting"
            )
            # Capture serial console output before deletion — the boot disk
            # is auto-delete, so this is the only chance to see why
            # STARTUP_SCRIPT exited without registering.
            _log_serial_tail(zone, instance.name)
            to_delete.append((zone, instance.name))
        else:
            result["kept_vms"].append(instance.name)

    if to_delete:
        with ThreadPoolExecutor(max_workers=min(len(to_delete), 8)) as ex:
            futures = {
                ex.submit(_delete_vm, zone, name): (zone, name)
                for zone, name in to_delete
            }
            for fut in as_completed(futures):
                name, outcome = fut.result()
                if outcome == "deleted":
                    result["deleted_vms"].append(name)
                else:
                    result["delete_failed"].append(name)

    deleted = set(result["deleted_vms"])
    live_vms = [(zone, inst) for zone, inst in vms if inst.name not in deleted]

    # Zombie runners: ephemeral-named runners with no live VM.
    for runner in runners:
        name = runner["name"]
        if not name.startswith("gha-"):
            continue
        if name in vm_names:
            continue
        # Allow GitHub a beat to garbage-collect a runner whose VM just died;
        # only kill ones that are offline AND have no VM.
        if runner.get("status") == "online":
            continue
        try:
            deregister_runner(pat, runner["id"])
            result["deregistered_runners"].append(name)
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to deregister {name}: {exc!r}")
            result["deregister_failed"].append(name)

    if REDISPATCH_ENABLED:
        # Never let a re-dispatch failure mask the cleanup result.
        try:
            result["redispatch"] = redispatch_stalled_jobs(pat, live_vms, runner_by_name)
        except Exception as exc:  # noqa: BLE001
            print(f"redispatch_stalled_jobs failed: {exc!r}")
            result["redispatch"] = {"error": str(exc)}

    return result


# ---------------------------------------------------------------------------
# Stalled-job re-dispatch
# ---------------------------------------------------------------------------


def _parse_github_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def list_org_repos(pat: str) -> list[str]:
    """Names of the org's non-archived repos (paginated, up to 1000)."""
    names: list[str] = []
    page = 1
    while page <= 10:
        result = _github_request(
            "GET", f"/orgs/{GITHUB_ORG}/repos?type=all&per_page=100&page={page}", pat
        )
        if not isinstance(result, list):
            break
        names.extend(r["name"] for r in result if not r.get("archived"))
        if len(result) < 100:
            break
        page += 1
    return names


def list_queued_self_hosted_jobs(pat: str) -> list[dict]:
    """Every org job still waiting for a self-hosted runner.

    GitHub has no org-wide "queued jobs" endpoint, so walk each repo's queued
    and in-progress runs (a deploy job can sit queued inside an in-progress
    run) and collect their queued jobs. A failure on one repo (e.g. a PAT
    without access) is logged and skipped rather than aborting the pass.
    """
    jobs: list[dict] = []
    for repo in list_org_repos(pat):
        try:
            run_ids: set[int] = set()
            for status in ("queued", "in_progress"):
                runs = _github_request(
                    "GET",
                    f"/repos/{GITHUB_ORG}/{repo}/actions/runs?status={status}&per_page=50",
                    pat,
                )
                if isinstance(runs, dict):
                    run_ids.update(r["id"] for r in runs.get("workflow_runs", []))
            for run_id in sorted(run_ids):
                run_jobs = _github_request(
                    "GET",
                    f"/repos/{GITHUB_ORG}/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
                    pat,
                )
                if not isinstance(run_jobs, dict):
                    continue
                for job in run_jobs.get("jobs", []):
                    if job.get("status") == "queued" and "self-hosted" in job.get("labels", []):
                        jobs.append({**job, "repo": repo})
        except Exception as exc:  # noqa: BLE001
            print(f"Skipping {repo} in stalled-job scan: {exc!r}")
    return jobs


def _family_of_runner_name(name: str) -> str | None:
    """``gha-gpu-windows-abc123`` → ``gpu-windows``; None if not ours."""
    if not name.startswith("gha-") or "-" not in name[len("gha-"):]:
        return None
    return name[len("gha-"):].rsplit("-", 1)[0]


def _spare_capacity_by_family(
    vms: list[tuple[str, compute_v1.Instance]], runner_by_name: dict[str, dict]
) -> dict[str, int]:
    """Count runners that could still pick up a queued job, per family.

    A VM counts if it is still booting inside the orphan grace window, or
    registered, online and idle. "Booting" includes a registered-but-offline
    runner: ``generate-jitconfig`` registers the runner (offline) before the VM
    even exists, so a fresh dispatch normally shows up that way. Busy, stopped
    and past-grace VMs don't count — they will never take a new job.
    """
    spare: dict[str, int] = {}
    for _, instance in vms:
        family = _family_of_runner_name(instance.name)
        if family is None or getattr(instance, "status", "") in ("TERMINATED", "STOPPING", "SUSPENDED"):
            continue
        runner = runner_by_name.get(instance.name)
        if runner is None or runner.get("status") != "online":
            available = _vm_age_minutes(instance) < ORPHAN_GRACE_MINUTES
        else:
            available = not runner.get("busy")
        if available:
            spare[family] = spare.get(family, 0) + 1
    return spare


def redispatch_stalled_jobs(
    pat: str,
    vms: list[tuple[str, compute_v1.Instance]],
    runner_by_name: dict[str, dict],
) -> dict:
    """Launch a fresh runner for each stalled job not covered by spare capacity.

    JIT runners aren't bound to a job — any queued job with matching labels
    takes the first free runner — so we compare per-family demand (all queued
    jobs) against spare supply, and only act when some job has been queued
    longer than REDISPATCH_AFTER_MINUTES (and less than
    REDISPATCH_GIVE_UP_MINUTES). Launches per family are capped at the number
    of stalled jobs, and per tick at MAX_REDISPATCH_PER_TICK.
    """
    result: dict = {"stalled_jobs": [], "given_up": [], "dispatched": [], "dispatch_failed": []}
    queued = list_queued_self_hosted_jobs(pat)
    if not queued:
        return result

    now = datetime.now(timezone.utc)
    demand: dict[str, int] = {}
    stalled: dict[str, list[dict]] = {}
    for job in queued:
        family = resolve_family(job["labels"]).name
        created = job.get("created_at")
        age = (now - _parse_github_time(created)).total_seconds() / 60 if created else 0
        if age >= REDISPATCH_GIVE_UP_MINUTES:
            # Excluded from demand too, so a job we've abandoned doesn't make
            # every later stalled job in its family look under-supplied.
            print(f"Not re-dispatching {job['repo']} job {job.get('id')}: queued {age:.0f}min (> give-up)")
            result["given_up"].append(job.get("id"))
            continue
        demand[family] = demand.get(family, 0) + 1
        if age >= REDISPATCH_AFTER_MINUTES:
            stalled.setdefault(family, []).append(job)
            result["stalled_jobs"].append(
                f"{job['repo']}#{job.get('run_id')}/{job.get('name')} ({age:.0f}min, {family})"
            )

    spare = _spare_capacity_by_family(vms, runner_by_name)
    budget = MAX_REDISPATCH_PER_TICK
    to_launch: list[dict] = []
    for family, jobs in stalled.items():
        deficit = demand[family] - spare.get(family, 0)
        count = min(max(deficit, 0), len(jobs), budget)
        print(
            f"Re-dispatch {family}: queued={demand[family]} stalled={len(jobs)} "
            f"spare={spare.get(family, 0)} launching={count}"
        )
        to_launch.extend(jobs[:count])
        budget -= count
        if budget <= 0:
            break

    if not to_launch:
        return result

    # Each launch blocks up to INSERT_CONFIRM_TIMEOUT_SECONDS; run them in
    # parallel so a full budget stays well inside the function timeout.
    with ThreadPoolExecutor(max_workers=len(to_launch)) as ex:
        futures = {ex.submit(create_ephemeral_runner, job["labels"], pat): job for job in to_launch}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                result["dispatched"].append(fut.result()["runner_name"])
            except Exception as exc:  # noqa: BLE001
                print(f"Re-dispatch for {job['repo']} job {job.get('id')} failed: {exc!r}")
                result["dispatch_failed"].append(job.get("id"))
    return result
