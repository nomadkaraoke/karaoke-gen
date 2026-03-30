"""
GitHub Actions service account and Workload Identity Federation.

Manages the service account used by GitHub Actions for CI/CD deployments,
including keyless authentication via Workload Identity Federation.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import serviceaccount, iam

from config import PROJECT_ID


def create_github_actions_service_account() -> serviceaccount.Account:
    """
    Create the GitHub Actions service account for CI/CD.

    Returns:
        serviceaccount.Account: The service account resource.
    """
    return serviceaccount.Account(
        "github-actions-deployer-sa",
        account_id="github-actions-deployer",
        display_name="GitHub Actions Deployer",
        description="Service account for GitHub Actions CD via Workload Identity",
    )


def create_workload_identity_pool() -> iam.WorkloadIdentityPool:
    """
    Create the Workload Identity Pool for GitHub OIDC.

    Returns:
        iam.WorkloadIdentityPool: The pool resource.
    """
    return iam.WorkloadIdentityPool(
        "github-actions-pool",
        workload_identity_pool_id="github-actions-pool",
        display_name="GitHub Actions Pool",
        description="Workload Identity Pool for GitHub Actions",
        disabled=False,
    )


def create_workload_identity_provider(
    pool: iam.WorkloadIdentityPool,
) -> iam.WorkloadIdentityPoolProvider:
    """
    Create the Workload Identity Provider for GitHub OIDC.

    Args:
        pool: The Workload Identity Pool.

    Returns:
        iam.WorkloadIdentityPoolProvider: The provider resource.
    """
    return iam.WorkloadIdentityPoolProvider(
        "github-actions-provider",
        workload_identity_pool_id=pool.workload_identity_pool_id,
        workload_identity_pool_provider_id="github-actions-provider",
        display_name="GitHub Actions Provider",
        attribute_mapping={
            "google.subject": "assertion.sub",
            "attribute.actor": "assertion.actor",
            "attribute.repository": "assertion.repository",
            "attribute.repository_owner": "assertion.repository_owner",
        },
        attribute_condition="assertion.repository_owner == 'nomadkaraoke' || assertion.repository_owner == 'beveradb'",
        oidc=iam.WorkloadIdentityPoolProviderOidcArgs(
            issuer_uri="https://token.actions.githubusercontent.com",
        ),
    )


def grant_github_actions_permissions(
    service_account: serviceaccount.Account,
    pool: iam.WorkloadIdentityPool,
) -> dict:
    """
    Grant all necessary permissions to the GitHub Actions service account.

    Args:
        service_account: The GitHub Actions service account.
        pool: The Workload Identity Pool.

    Returns:
        dict: Dictionary of IAM binding resources.
    """
    bindings = {}

    # Grant Cloud Run admin permissions
    bindings["run_admin"] = gcp.projects.IAMMember(
        "github-actions-run-admin",
        project=PROJECT_ID,
        role="roles/run.admin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant service account user permissions (to deploy Cloud Run with other SA)
    bindings["sa_user"] = gcp.projects.IAMMember(
        "github-actions-sa-user",
        project=PROJECT_ID,
        role="roles/iam.serviceAccountUser",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Artifact Registry writer permissions
    bindings["artifact_writer"] = gcp.projects.IAMMember(
        "github-actions-artifact-writer",
        project=PROJECT_ID,
        role="roles/artifactregistry.writer",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Cloud Build permissions - REQUIRED for gcloud builds submit
    bindings["cloudbuild"] = gcp.projects.IAMMember(
        "github-actions-cloudbuild",
        project=PROJECT_ID,
        role="roles/cloudbuild.builds.editor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Storage Admin for Cloud Build bucket uploads
    bindings["storage_admin"] = gcp.projects.IAMMember(
        "github-actions-storage-admin",
        project=PROJECT_ID,
        role="roles/storage.admin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Logging write permissions for Cloud Build logs
    bindings["logging"] = gcp.projects.IAMMember(
        "github-actions-logging",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Cloud Build logs viewer - needed for `gcloud builds log` to read build output
    bindings["logging_viewer"] = gcp.projects.IAMMember(
        "github-actions-logging-viewer",
        project=PROJECT_ID,
        role="roles/logging.viewer",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Service Usage Consumer - required for serviceusage.services.use permission
    bindings["service_usage"] = gcp.projects.IAMMember(
        "github-actions-service-usage",
        project=PROJECT_ID,
        role="roles/serviceusage.serviceUsageConsumer",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Project IAM Admin - required for Pulumi to refresh IAM binding state
    bindings["iam_admin"] = gcp.projects.IAMMember(
        "github-actions-iam-admin",
        project=PROJECT_ID,
        role="roles/resourcemanager.projectIamAdmin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Secret Manager Admin - required for Pulumi to refresh secret state
    bindings["secretmanager"] = gcp.projects.IAMMember(
        "github-actions-secretmanager",
        project=PROJECT_ID,
        role="roles/secretmanager.admin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Allow GitHub Actions to impersonate the service account
    # All repos that deploy to GCP need access (nomadkaraoke org + beveradb personal)
    bindings["wif_binding"] = serviceaccount.IAMBinding(
        "github-actions-wif-binding",
        service_account_id=service_account.name,
        role="roles/iam.workloadIdentityUser",
        members=[
            pool.name.apply(
                lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/nomadkaraoke/karaoke-gen"
            ),
            pool.name.apply(
                lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/nomadkaraoke/flacfetch"
            ),
            pool.name.apply(
                lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/nomadkaraoke/karaoke-decide"
            ),
            pool.name.apply(
                lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/nomadkaraoke/python-audio-separator"
            ),
            pool.name.apply(
                lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/beveradb/cryptotrader"
            ),
        ],
    )

    # Grant Compute Engine permissions for flacfetch VM deployment
    bindings["compute_admin"] = gcp.projects.IAMMember(
        "github-actions-compute-admin",
        project=PROJECT_ID,
        role="roles/compute.instanceAdmin.v1",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant OS Login permissions for SSH access
    bindings["os_login"] = gcp.projects.IAMMember(
        "github-actions-os-login",
        project=PROJECT_ID,
        role="roles/compute.osLogin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant IAP tunnel user for secure SSH (used by gcloud compute ssh)
    bindings["iap_tunnel"] = gcp.projects.IAMMember(
        "github-actions-iap-tunnel",
        project=PROJECT_ID,
        role="roles/iap.tunnelResourceAccessor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant broad Editor access to avoid permission issues when adding new resources
    bindings["editor"] = gcp.projects.IAMMember(
        "github-actions-editor",
        project=PROJECT_ID,
        role="roles/editor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return bindings
