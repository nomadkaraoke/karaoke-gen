"""
Encoding Worker VM resources — blue-green deployment pair.

Manages two identical VMs (a/b) for zero-downtime deployments.
Only one is active (primary) at a time; the other is stopped (secondary).
Both auto-shutdown when idle to minimize cost, and both run on SPOT capacity
(preempted → STOPPED, restarted on demand; lost encodes are resubmitted). One
on-demand fallback VM in a different family/zone absorbs Spot stockouts.

See docs/superpowers/specs/2026-03-24-blue-green-encoding-worker-design.md
"""

import pulumi
from pulumi_gcp import compute, serviceaccount

from config import REGION, ENCODING_WORKER_ZONE, PROJECT_ID, MachineTypes, DiskSizes, EncodingWorkerConfig
from .startup_scripts import read_script


def create_encoding_worker_ips() -> list[compute.Address]:
    """Create static IPs for both encoding worker VMs."""
    ips = []
    for name in EncodingWorkerConfig.IP_NAMES:
        ip = compute.Address(
            name,
            name=name,
            region=REGION,
            address_type="EXTERNAL",
            description=f"Static external IP for {name}",
        )
        ips.append(ip)
    return ips


def create_encoding_worker_vms(
    ips: list[compute.Address],
    service_account: serviceaccount.Account,
) -> list[compute.Instance]:
    """Create the blue-green encoding worker VM pair."""
    startup_script = read_script("encoding_worker.sh")
    custom_image = f"projects/{PROJECT_ID}/global/images/family/encoding-worker"

    vms = []
    for vm_name, ip in zip(EncodingWorkerConfig.VM_NAMES, ips):
        vm = compute.Instance(
            vm_name,
            name=vm_name,
            machine_type=MachineTypes.ENCODING_WORKER,
            zone=ENCODING_WORKER_ZONE,
            boot_disk=compute.InstanceBootDiskArgs(
                initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                    image=custom_image,
                    size=DiskSizes.ENCODING_WORKER,
                    type="hyperdisk-balanced",
                    # NOTE: Hyperdisk provisioned IOPS/throughput are deliberately
                    # NOT set here. Specifying them forces a full VM *replacement*
                    # (the provider treats boot-disk init params as immutable), and
                    # recreating a primary depends on us-central1-c capacity — the
                    # exact shortage the fallback fleet exists to absorb. So the
                    # disks are pinned to the Hyperdisk Balanced free baseline
                    # (3000 IOPS / 140 MB/s) live via `gcloud compute disks update`
                    # instead — non-disruptive, and pulumi does not manage this
                    # attribute so there is no drift. They were over-provisioned at
                    # 3600 IOPS / 290 MB/s (~$8.70/disk/mo wasted). Re-apply the
                    # gcloud tune after any worker-image rebuild that recreates the
                    # disks. See docs/archive/2026-06-14-gcp-cost-reduction-plan.md.
                ),
            ),
            network_interfaces=[compute.InstanceNetworkInterfaceArgs(
                network="default",
                access_configs=[compute.InstanceNetworkInterfaceAccessConfigArgs(
                    nat_ip=ip.address,
                )],
            )],
            service_account=compute.InstanceServiceAccountArgs(
                email=service_account.email,
                scopes=["cloud-platform"],
            ),
            metadata_startup_script=startup_script,
            tags=["encoding-worker"],
            allow_stopping_for_update=True,
            advanced_machine_features=compute.InstanceAdvancedMachineFeaturesArgs(
                threads_per_core=2,
            ),
            scheduling=_primary_pair_scheduling(),
        )
        vms.append(vm)
    return vms


def _primary_pair_scheduling() -> compute.InstanceSchedulingArgs:
    """Scheduling for the a/b pair: Spot with STOP-on-preemption.

    STOP (not DELETE) keeps the VM, its disk and static IP, so a preempted
    worker is just a TERMINATED instance that the backend's ensure-running path
    restarts like any idle-stopped worker. automatic_restart must be False and
    on_host_maintenance TERMINATE for Spot.
    """
    if not EncodingWorkerConfig.PRIMARY_PAIR_SPOT:
        return compute.InstanceSchedulingArgs(
            provisioning_model="STANDARD",
            preemptible=False,
            automatic_restart=True,
            on_host_maintenance="MIGRATE",
        )
    return compute.InstanceSchedulingArgs(
        provisioning_model="SPOT",
        preemptible=True,
        automatic_restart=False,
        on_host_maintenance="TERMINATE",
        instance_termination_action="STOP",
    )


def create_encoding_worker_fallback_ips() -> list[compute.Address]:
    """Create static IPs for the capacity-fallback VMs.

    These IPs back the on-demand fallback that absorbs a Spot/c4d stockout of
    the primary pair (alternate zone AND machine family). One IP per entry in
    EncodingWorkerConfig.FALLBACKS (order-aligned).
    """
    ips = []
    for fb in EncodingWorkerConfig.FALLBACKS:
        ip_name = EncodingWorkerConfig.fallback_ip_name(fb["suffix"])
        ip = compute.Address(
            ip_name,
            name=ip_name,
            region=REGION,
            address_type="EXTERNAL",
            # Keep this string byte-identical to the originally-deployed value so
            # `pulumi up` does not diff (and needlessly churn) the existing a/b IPs.
            description=f"Static external IP for {ip_name} (zone-fallback)",
        )
        ips.append(ip)
    return ips


def create_encoding_worker_fallback_vms(
    ips: list[compute.Address],
    service_account: serviceaccount.Account,
) -> list[compute.Instance]:
    """Create capacity-fallback encoding worker VMs.

    Provisioned stopped — only started by the application when the Spot primary
    pair can't start (ZONE_RESOURCE_POOL_EXHAUSTED / Spot stockout). On-demand
    (default STANDARD scheduling — never preempted), in a different machine
    family and zone from the c4d pair (incident 2026-08-12). Each entry's
    machine_type and disk_type come from EncodingWorkerConfig.FALLBACKS — c2d/n2
    use pd-balanced because they don't support hyperdisk-balanced. Cost when
    stopped is just the 50 GB boot disk.
    """
    startup_script = read_script("encoding_worker.sh")
    custom_image = f"projects/{PROJECT_ID}/global/images/family/encoding-worker"

    vms = []
    for fb, ip in zip(EncodingWorkerConfig.FALLBACKS, ips):
        vm_name = EncodingWorkerConfig.fallback_vm_name(fb["suffix"])
        zone = f"{REGION}-{fb['zone_suffix']}"
        # Hyperdisk provisioned IOPS/throughput are deliberately NOT set: setting
        # them forces a full VM replacement (boot-disk init params are immutable),
        # and recreating a c4d VM depends on the exact capacity the fallback fleet
        # exists to absorb. hyperdisk-balanced disks are pinned to the free
        # baseline (3000 IOPS / 140 MB/s) live via `gcloud compute disks update`
        # after any image rebuild that recreates them; pd-balanced (n2) needs no
        # such tuning. See docs/archive/2026-06-14-gcp-cost-reduction-plan.md.
        vm = compute.Instance(
            vm_name,
            name=vm_name,
            machine_type=fb["machine_type"],
            zone=zone,
            boot_disk=compute.InstanceBootDiskArgs(
                initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                    image=custom_image,
                    size=DiskSizes.ENCODING_WORKER,
                    type=fb["disk_type"],
                ),
            ),
            network_interfaces=[compute.InstanceNetworkInterfaceArgs(
                network="default",
                access_configs=[compute.InstanceNetworkInterfaceAccessConfigArgs(
                    nat_ip=ip.address,
                )],
            )],
            service_account=compute.InstanceServiceAccountArgs(
                email=service_account.email,
                scopes=["cloud-platform"],
            ),
            metadata_startup_script=startup_script,
            tags=["encoding-worker"],
            allow_stopping_for_update=True,
            advanced_machine_features=compute.InstanceAdvancedMachineFeaturesArgs(
                threads_per_core=2,
            ),
            # Pulumi default is "running" — we want fallbacks stopped at create.
            desired_status="TERMINATED",
        )
        vms.append(vm)
    return vms


def create_encoding_worker_firewall() -> compute.Firewall:
    """Create firewall rules for encoding worker VMs.

    Opens port 8080 for the HTTP API. Both VMs share the same
    firewall rule via the 'encoding-worker' network tag.
    """
    return compute.Firewall(
        "encoding-worker-firewall",
        name="encoding-worker-allow-http",
        network="default",
        allows=[
            compute.FirewallAllowArgs(protocol="tcp", ports=["8080"]),
        ],
        source_ranges=["0.0.0.0/0"],
        target_tags=["encoding-worker"],
        description="Allow HTTP access to encoding workers (auth required)",
    )
