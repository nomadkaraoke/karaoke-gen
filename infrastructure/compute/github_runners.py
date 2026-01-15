"""
GitHub Actions self-hosted runner VMs.

Manages multiple VM instances that serve as self-hosted runners
for GitHub Actions CI/CD workflows.
"""

import pulumi
from pulumi_gcp import compute, serviceaccount, secretmanager

from config import ZONE, MachineTypes, DiskSizes, NUM_GITHUB_RUNNERS
from .startup_scripts import read_script


def create_github_runners(
    service_account: serviceaccount.Account,
    runner_pat_secret: secretmanager.Secret,
) -> list[compute.Instance]:
    """
    Create GitHub Actions self-hosted runner VM instances.

    Creates NUM_GITHUB_RUNNERS VMs, each configured as a GitHub Actions
    self-hosted runner for the nomadkaraoke organization (available to all repos).

    Args:
        service_account: The GitHub runner service account.
        runner_pat_secret: The Secret Manager secret containing the GitHub PAT.

    Returns:
        list[compute.Instance]: List of runner VM instances.
    """
    startup_script = read_script("github_runner.sh")

    runner_vms = []
    for i in range(1, NUM_GITHUB_RUNNERS + 1):
        vm = compute.Instance(
            f"github-runner-{i}",
            name=f"github-runner-{i}",
            machine_type=MachineTypes.GITHUB_RUNNER,
            zone=ZONE,
            boot_disk=compute.InstanceBootDiskArgs(
                initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                    image="debian-cloud/debian-12",
                    size=DiskSizes.GITHUB_RUNNER,
                    type="pd-ssd",
                ),
            ),
            network_interfaces=[compute.InstanceNetworkInterfaceArgs(
                network="default",
                access_configs=[compute.InstanceNetworkInterfaceAccessConfigArgs()],
            )],
            service_account=compute.InstanceServiceAccountArgs(
                email=service_account.email,
                scopes=["cloud-platform"],
            ),
            metadata_startup_script=startup_script,
            tags=["github-runner"],
            allow_stopping_for_update=True,
            opts=pulumi.ResourceOptions(
                depends_on=[runner_pat_secret],
            ),
        )
        runner_vms.append(vm)

    return runner_vms
