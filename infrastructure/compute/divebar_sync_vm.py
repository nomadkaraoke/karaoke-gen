"""
Divebar File Sync VM resources.

Creates a small GCE VM that downloads karaoke files from Google Drive
to GCS. The VM starts, runs the sync, and shuts itself down.

Design:
- e2-medium (2 vCPU, 4 GB) — enough for 4 concurrent downloads
- 10 GB boot disk (minimal — files go to GCS, not local disk)
- Preemptible/Spot for cost savings (~$10/mo if running 24/7, but
  it only runs for ~1-2 hours daily for incremental sync)
- Auto-shutdown after sync completes
- Cloud Scheduler starts the VM daily at 3 AM ET (after index refresh at 2 AM)
"""

import pulumi
from pulumi_gcp import compute, serviceaccount

from config import REGION, ZONE, PROJECT_ID
from .startup_scripts import read_script


def create_divebar_sync_vm(
    service_account: serviceaccount.Account,
) -> compute.Instance:
    """Create the Divebar file sync VM instance."""
    startup_script = read_script("divebar_sync.sh")

    return compute.Instance(
        "divebar-sync-vm",
        name="divebar-sync",
        machine_type="e2-medium",
        zone=ZONE,
        boot_disk=compute.InstanceBootDiskArgs(
            initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                image="debian-cloud/debian-12",
                size=10,
                type="pd-standard",
            ),
        ),
        network_interfaces=[compute.InstanceNetworkInterfaceArgs(
            network="default",
            # Needs internet access for Drive API and GCS
            access_configs=[compute.InstanceNetworkInterfaceAccessConfigArgs()],
        )],
        service_account=compute.InstanceServiceAccountArgs(
            email=service_account.email,
            # drive.readonly for downloading files, cloud-platform for GCS + BigQuery
            scopes=[
                "https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/cloud-platform",
            ],
        ),
        metadata_startup_script=startup_script,
        tags=["divebar-sync"],
        allow_stopping_for_update=True,
        scheduling=compute.InstanceSchedulingArgs(
            automatic_restart=False,  # Don't restart after self-shutdown
        ),
        # VM is started daily by Cloud Scheduler; may be RUNNING or TERMINATED
        # depending on sync status. Use RUNNING to avoid Pulumi trying to stop it.
        desired_status="RUNNING",
    )
