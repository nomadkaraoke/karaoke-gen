"""
Encoding Worker VM resources.

Manages the high-performance VM instance for video encoding,
including static IP and firewall rules.
"""

import pulumi
from pulumi_gcp import compute, serviceaccount

from config import REGION, ZONE, PROJECT_ID, MachineTypes, DiskSizes
from .startup_scripts import read_script


def create_encoding_worker_ip() -> compute.Address:
    """
    Create static IP for the encoding worker.

    Returns:
        compute.Address: The static IP resource.
    """
    return compute.Address(
        "encoding-worker-ip",
        name="encoding-worker-static-ip",
        region=REGION,
        address_type="EXTERNAL",
        description="Static external IP for encoding worker service",
    )


def create_encoding_worker_vm(
    ip: compute.Address,
    service_account: serviceaccount.Account,
) -> compute.Instance:
    """
    Create the encoding worker VM instance.

    This VM runs video encoding jobs with high CPU/memory resources.
    Uses hyperdisk-balanced for fast I/O during encoding.

    Uses a custom Packer-built image with Python 3.13, FFmpeg, and fonts
    pre-installed to reduce startup time from ~10 minutes to ~30 seconds.
    See infrastructure/packer/README.md for image build instructions.

    Args:
        ip: The static IP address for the VM.
        service_account: The encoding worker service account.

    Returns:
        compute.Instance: The VM instance resource.
    """
    startup_script = read_script("encoding_worker.sh")

    # Use custom Packer image with dependencies pre-installed
    # Image family returns latest image in the family
    # Fallback: "debian-cloud/debian-12" if image doesn't exist yet
    custom_image = f"projects/{PROJECT_ID}/global/images/family/encoding-worker"

    return compute.Instance(
        "encoding-worker",
        name="encoding-worker",
        machine_type=MachineTypes.ENCODING_WORKER,
        zone=ZONE,
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


def create_encoding_worker_firewall() -> compute.Firewall:
    """
    Create firewall rules for the encoding worker.

    Opens port 8080 for the HTTP API (authentication required).

    Returns:
        compute.Firewall: The firewall resource.
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
        description="Allow HTTP access to encoding worker (auth required)",
    )
