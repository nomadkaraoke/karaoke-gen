"""
Ephemeral GitHub Actions runner dispatcher.

Creates a single-use GCE VM per `workflow_job.queued` webhook, registers it as
a JIT-config ephemeral runner with the nomadkaraoke org, and lets the VM
self-destruct after the job runs. An orphan-cleanup pass runs every 15 min to
catch VMs whose job died before the runner could de-register itself.

This module is invoked from main.py when RUNNER_MODE=ephemeral.
"""

from __future__ import annotations

import base64
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

    Precedence: gpu → build → general. A job that asks for both ``gpu`` and
    ``docker-build`` shouldn't exist today; if it ever does we'd want a separate
    image, not a coin-flip.
    """
    label_set = {label.lower() for label in labels}
    if "gpu" in label_set:
        return FAMILIES["gpu"]
    if "docker-build" in label_set:
        return FAMILIES["build"]
    return FAMILIES["general"]


def runner_labels_for(family: FamilySpec) -> list[str]:
    """Compose the labels advertised to GitHub by this runner."""
    return ["self-hosted", "linux", *family.extra_runner_labels]


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

    metadata = compute_v1.Metadata(
        items=[
            compute_v1.Items(key="jit-config", value=jit_config),
            compute_v1.Items(key="startup-script", value=STARTUP_SCRIPT),
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
        shielded_instance_config=compute_v1.ShieldedInstanceConfig(
            enable_secure_boot=True,
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
        use_external_ip = (
            family.needs_external_ip_in_fallback_zone and zone != PRIMARY_ZONE
        )
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
            # Don't wait for the operation to fully resolve — fire and forget,
            # the VM will boot in the background and pick up its queued job.
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

        if age >= MAX_VM_LIFETIME_MINUTES:
            print(f"{instance.name}: age={age:.1f}min > max, deleting")
            to_delete.append((zone, instance.name))
        elif not registered and age >= ORPHAN_GRACE_MINUTES:
            print(
                f"{instance.name}: age={age:.1f}min, no GHA runner registration — deleting"
            )
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

    return result
