"""
Flacfetch VM resources.

Manages the VM instance for the flacfetch audio download service,
including static IP and firewall rules.
"""

import pulumi
from pulumi_gcp import compute, storage, serviceaccount

from config import REGION, ZONE, MachineTypes, DiskSizes
from .startup_scripts import read_script

# Cloudflare IP ranges for firewall rules
# Source: https://www.cloudflare.com/ips-v4
CLOUDFLARE_IP_RANGES = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]


def create_flacfetch_ip() -> compute.Address:
    """
    Create static IP for flacfetch.

    This IP is whitelisted on private trackers, so it must remain stable.

    Returns:
        compute.Address: The static IP resource.
    """
    return compute.Address(
        "flacfetch-ip",
        name="flacfetch-static-ip",
        region=REGION,
        address_type="EXTERNAL",
        description="Static IP for flacfetch service (torrent downloads)",
    )


def create_flacfetch_vm(
    ip: compute.Address,
    service_account: serviceaccount.Account,
    bucket: storage.Bucket,
) -> compute.Instance:
    """
    Create the flacfetch VM instance.

    Args:
        ip: The static IP address for the VM.
        service_account: The flacfetch service account.
        bucket: The GCS bucket for storing downloaded files.

    Returns:
        compute.Instance: The VM instance resource.
    """
    startup_script = read_script("flacfetch.sh")

    return compute.Instance(
        "flacfetch-service",
        name="flacfetch-service",
        machine_type=MachineTypes.FLACFETCH,
        zone=ZONE,
        boot_disk=compute.InstanceBootDiskArgs(
            initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                image="debian-cloud/debian-12",
                size=DiskSizes.FLACFETCH,
                type="pd-ssd",
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
        metadata={
            "gcs-bucket": bucket.name,
        },
        tags=["flacfetch-service"],
        allow_stopping_for_update=True,
    )


def create_flacfetch_firewall() -> tuple[compute.Firewall, compute.Firewall]:
    """
    Create firewall rules for flacfetch.

    Creates two separate rules:
    - BitTorrent (51413 TCP/UDP): Open to all for peer connections
    - HTTP API (8080 TCP): Restricted to Cloudflare IPs only

    Returns:
        tuple: (bittorrent_firewall, api_firewall) resources.
    """
    bittorrent_firewall = compute.Firewall(
        "flacfetch-bittorrent-firewall",
        name="flacfetch-bittorrent",
        network="default",
        allows=[
            compute.FirewallAllowArgs(protocol="tcp", ports=["51413"]),
            compute.FirewallAllowArgs(protocol="udp", ports=["51413"]),
        ],
        source_ranges=["0.0.0.0/0"],
        target_tags=["flacfetch-service"],
        description="BitTorrent peer connections for flacfetch (open to all)",
    )

    api_firewall = compute.Firewall(
        "flacfetch-api-firewall",
        name="flacfetch-api",
        network="default",
        allows=[
            compute.FirewallAllowArgs(protocol="tcp", ports=["8080"]),
        ],
        source_ranges=CLOUDFLARE_IP_RANGES,
        target_tags=["flacfetch-service"],
        description="HTTP API for flacfetch (Cloudflare IPs only)",
    )

    return bittorrent_firewall, api_firewall
