"""
Encoding Worker VM resources — blue-green deployment pair.

Manages two identical VMs (a/b) for zero-downtime deployments.
Only one is active (primary) at a time; the other is stopped (secondary).
Both auto-shutdown when idle to minimize cost.

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
