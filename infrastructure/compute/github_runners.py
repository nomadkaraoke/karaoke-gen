"""
GitHub Actions Self-Hosted Runners on GCP.

Creates on-demand VMs that register as self-hosted runners
at the organization level.

Resources created:
- Cloud Router and Cloud NAT (for outbound internet without external IPs)
- N Compute Engine VMs (on-demand instances)
- Each VM runs the GitHub Actions runner agent

The VMs are managed by the runner_manager Cloud Function which:
- Starts VMs when CI jobs are queued (via GitHub webhook)
- Stops VMs when idle for too long (via Cloud Scheduler)
"""

import pulumi
from pulumi_gcp import compute, secretmanager, serviceaccount

from config import (
    REGION,
    ZONE,
    MachineTypes,
    DiskSizes,
    NUM_GITHUB_RUNNERS,
    GENERAL_RUNNER_LABELS,
    BUILD_RUNNER_LABELS,
)
from .startup_scripts import read_script


def create_cloud_nat() -> tuple[compute.Router, compute.RouterNat]:
    """
    Create Cloud Router and Cloud NAT for outbound internet access.

    This allows VMs without external IPs to access the internet,
    which saves costs and improves security.

    Returns:
        tuple: (router, nat) resources
    """
    # Create Cloud Router
    router = compute.Router(
        "github-runners-router",
        name="github-runners-router",
        region=REGION,
        network="default",
        description="Router for GitHub Actions runner NAT",
    )

    # Create Cloud NAT
    nat = compute.RouterNat(
        "github-runners-nat",
        name="github-runners-nat",
        router=router.name,
        region=REGION,
        nat_ip_allocate_option="AUTO_ONLY",
        source_subnetwork_ip_ranges_to_nat="ALL_SUBNETWORKS_ALL_IP_RANGES",
        log_config=compute.RouterNatLogConfigArgs(
            enable=True,
            filter="ERRORS_ONLY",
        ),
        opts=pulumi.ResourceOptions(depends_on=[router]),
    )

    return router, nat


def create_github_runners(
    service_account: serviceaccount.Account,
    runner_pat_secret: secretmanager.Secret,
    nat: compute.RouterNat,
) -> list[compute.Instance]:
    """
    Create GitHub Actions self-hosted runner VM instances.

    Creates NUM_GITHUB_RUNNERS on-demand VMs, each configured as a GitHub Actions
    self-hosted runner for the nomadkaraoke organization (available to all repos).

    VMs are started/stopped by the runner_manager Cloud Function based on CI demand.

    Args:
        service_account: The GitHub runner service account.
        runner_pat_secret: The Secret Manager secret containing the GitHub PAT.
        nat: Cloud NAT for outbound internet access (VMs have no external IPs).

    Returns:
        list[compute.Instance]: List of runner VM instances.
    """
    startup_script = read_script("github_runner.sh")

    runner_vms = []
    for i in range(1, NUM_GITHUB_RUNNERS + 1):
        instance_name = f"github-runner-{i}"

        vm = compute.Instance(
            instance_name,
            name=instance_name,
            machine_type=MachineTypes.GITHUB_RUNNER,
            zone=ZONE,
            # Boot disk configuration
            boot_disk=compute.InstanceBootDiskArgs(
                initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                    image="debian-cloud/debian-12",
                    size=DiskSizes.GITHUB_RUNNER,
                    type="pd-ssd",  # SSD for faster Docker builds
                ),
                auto_delete=True,
            ),
            # Network configuration - no external IP (uses Cloud NAT)
            # This saves ~$3/month per VM and improves security
            network_interfaces=[
                compute.InstanceNetworkInterfaceArgs(
                    network="default",
                    # No access_configs = no external IP
                    # Outbound internet via Cloud NAT
                )
            ],
            # Service account for GCP API access
            service_account=compute.InstanceServiceAccountArgs(
                email=service_account.email,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            ),
            # On-demand scheduling — no preemption risk
            scheduling=compute.InstanceSchedulingArgs(
                preemptible=False,
                automatic_restart=True,
                on_host_maintenance="MIGRATE",
            ),
            # Metadata for startup script
            metadata={
                "github-runner-pat-secret": "github-runner-pat",
                "github-org": "nomadkaraoke",
                "runner-labels": GENERAL_RUNNER_LABELS,
            },
            metadata_startup_script=startup_script,
            # Labels for organization and cost tracking
            labels={
                "purpose": "github-runner",
                "managed-by": "pulumi",
            },
            tags=["github-runner"],
            # Allow stopping for updates
            allow_stopping_for_update=True,
            # Deletion protection disabled (runners are ephemeral)
            deletion_protection=False,
            opts=pulumi.ResourceOptions(
                depends_on=[runner_pat_secret, nat],
                # Runners can be replaced without affecting other infrastructure
                delete_before_replace=True,
            ),
        )

        runner_vms.append(vm)

    return runner_vms


def create_build_runner(
    service_account: serviceaccount.Account,
    runner_pat_secret: secretmanager.Secret,
    nat: compute.RouterNat,
) -> compute.Instance:
    """
    Create a dedicated on-demand build runner for Docker deploys.

    Unlike the general runners (spot/preemptible), this runner uses on-demand
    scheduling to avoid preemption during long Docker builds. It has more
    CPU/RAM (8 vCPU, 32GB) and an additional 'docker-build' label so that
    only deploy-backend jobs are routed to it.

    Args:
        service_account: The GitHub runner service account.
        runner_pat_secret: The Secret Manager secret containing the GitHub PAT.
        nat: Cloud NAT for outbound internet access.

    Returns:
        compute.Instance: The build runner VM instance.
    """
    startup_script = read_script("github_runner.sh")
    instance_name = "github-build-runner"

    return compute.Instance(
        instance_name,
        name=instance_name,
        machine_type=MachineTypes.GITHUB_BUILD_RUNNER,
        zone=ZONE,
        boot_disk=compute.InstanceBootDiskArgs(
            initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                image="debian-cloud/debian-12",
                size=DiskSizes.GITHUB_RUNNER,
                type="pd-ssd",
            ),
            auto_delete=True,
        ),
        network_interfaces=[
            compute.InstanceNetworkInterfaceArgs(
                network="default",
            )
        ],
        service_account=compute.InstanceServiceAccountArgs(
            email=service_account.email,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        ),
        # On-demand scheduling — no preemption risk during Docker builds
        scheduling=compute.InstanceSchedulingArgs(
            preemptible=False,
            automatic_restart=True,
            on_host_maintenance="MIGRATE",
        ),
        metadata={
            "github-runner-pat-secret": "github-runner-pat",
            "github-org": "nomadkaraoke",
            "runner-labels": BUILD_RUNNER_LABELS,
        },
        metadata_startup_script=startup_script,
        labels={
            "purpose": "github-build-runner",
            "managed-by": "pulumi",
        },
        tags=["github-runner"],
        allow_stopping_for_update=True,
        deletion_protection=False,
        opts=pulumi.ResourceOptions(
            depends_on=[runner_pat_secret, nat],
            delete_before_replace=True,
        ),
    )


def create_instance_group_for_restart(
    instances: list[compute.Instance],
) -> compute.InstanceGroup:
    """
    Create an unmanaged instance group for the runner VMs.

    This enables bulk operations like restarting all runners.

    Args:
        instances: List of runner VM instances.

    Returns:
        compute.InstanceGroup: The instance group.
    """
    return compute.InstanceGroup(
        "github-runners-group",
        name="github-runners",
        description="Self-hosted GitHub Actions runners",
        zone=ZONE,
        instances=[instance.self_link for instance in instances],
    )
