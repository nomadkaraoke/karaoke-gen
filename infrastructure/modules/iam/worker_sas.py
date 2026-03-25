"""
Worker service accounts for VM-based services.

Manages service accounts for:
- Encoding worker VM
- GDrive validator Cloud Function
- GitHub Actions self-hosted runners
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import serviceaccount, storage

from config import PROJECT_ID


# ==================== Encoding Worker Service Account ====================

def create_encoding_worker_service_account() -> serviceaccount.Account:
    """Create the encoding worker service account for video encoding."""
    return serviceaccount.Account(
        "encoding-worker-sa",
        account_id="encoding-worker",
        display_name="Encoding Worker Service Account",
        description="Service account for high-performance video encoding VM",
    )


def grant_encoding_worker_permissions(
    service_account: serviceaccount.Account,
    bucket: storage.Bucket,
) -> dict:
    """Grant permissions to the encoding worker service account."""
    bindings = {}

    # Storage Object Admin - read/write video files
    bindings["storage_admin"] = storage.BucketIAMMember(
        "encoding-worker-storage-admin",
        bucket=bucket.name,
        role="roles/storage.objectAdmin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Secret Manager Accessor - read API key
    bindings["secrets_access"] = gcp.projects.IAMMember(
        "encoding-worker-secrets-access",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Logging Writer - write structured logs
    bindings["logging_access"] = gcp.projects.IAMMember(
        "encoding-worker-logging-access",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Monitoring Metric Writer - write metrics for Ops Agent
    bindings["monitoring_access"] = gcp.projects.IAMMember(
        "encoding-worker-monitoring-access",
        project=PROJECT_ID,
        role="roles/monitoring.metricWriter",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return bindings


def grant_backend_compute_permissions(
    backend_sa: serviceaccount.Account,
) -> dict:
    """Grant the backend SA permission to start/get encoding worker VMs.

    Needed for on-demand VM warmup when users open lyrics review.
    """
    custom_role = gcp.projects.IAMCustomRole(
        "encoding-worker-lifecycle-role",
        role_id="encodingWorkerLifecycle",
        title="Encoding Worker Lifecycle",
        description="Start and check status of encoding worker VMs",
        permissions=[
            "compute.instances.get",
            "compute.instances.start",
        ],
        project=PROJECT_ID,
    )

    binding = gcp.projects.IAMMember(
        "backend-encoding-worker-lifecycle",
        project=PROJECT_ID,
        role=custom_role.name,
        member=backend_sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return {"custom_role": custom_role, "binding": binding}


# ==================== GDrive Validator Service Account ====================

def create_gdrive_validator_service_account() -> serviceaccount.Account:
    """Create the GDrive validator service account for Cloud Function."""
    return serviceaccount.Account(
        "gdrive-validator-sa",
        account_id="gdrive-validator",
        display_name="Google Drive Validator Function",
        description="Service account for the GDrive validation Cloud Function",
    )


def grant_gdrive_validator_permissions(
    service_account: serviceaccount.Account,
) -> dict:
    """Grant permissions to the GDrive validator service account."""
    bindings = {}

    # Secret Manager Accessor - read Pushbullet API key
    bindings["secrets_access"] = gcp.projects.IAMMember(
        "gdrive-validator-secrets-access",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return bindings


# ==================== GitHub Runner Service Account ====================

def create_github_runner_service_account() -> serviceaccount.Account:
    """Create the GitHub runner service account for self-hosted runners."""
    return serviceaccount.Account(
        "github-runner-sa",
        account_id="github-runner",
        display_name="GitHub Actions Runner",
        description="Service account for self-hosted GitHub Actions runner VM",
    )


def grant_github_runner_permissions(
    service_account: serviceaccount.Account,
) -> dict:
    """Grant permissions to the GitHub runner service account."""
    bindings = {}

    # Artifact Registry Reader - pull base images
    bindings["artifact_reader"] = gcp.projects.IAMMember(
        "github-runner-artifact-reader",
        project=PROJECT_ID,
        role="roles/artifactregistry.reader",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Secret Manager Accessor - read GitHub PAT
    bindings["secrets_access"] = gcp.projects.IAMMember(
        "github-runner-secrets-access",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Logging Writer - write logs
    bindings["logging_access"] = gcp.projects.IAMMember(
        "github-runner-logging-access",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Storage Object Admin - upload/overwrite wheels in GCS
    bindings["storage_access"] = gcp.projects.IAMMember(
        "github-runner-storage-write",
        project=PROJECT_ID,
        role="roles/storage.objectAdmin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return bindings
