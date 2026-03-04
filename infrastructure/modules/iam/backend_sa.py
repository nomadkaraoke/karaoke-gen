"""
Backend service account and IAM bindings.

Manages the karaoke-backend service account used by Cloud Run and its associated
IAM roles for accessing Firestore, GCS, Secret Manager, and other services.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import serviceaccount

from config import PROJECT_ID, get_project_number


def create_backend_service_account() -> serviceaccount.Account:
    """
    Create the main backend service account.

    Returns:
        serviceaccount.Account: The service account resource.
    """
    return serviceaccount.Account(
        "karaoke-backend-sa",
        account_id="karaoke-backend",
        display_name="Karaoke Backend Service Account",
    )


def grant_backend_permissions(
    service_account: serviceaccount.Account,
) -> dict:
    """
    Grant all necessary permissions to the backend service account.

    Args:
        service_account: The backend service account.

    Returns:
        dict: Dictionary of IAM binding resources.
    """
    bindings = {}

    # Grant Firestore permissions
    bindings["firestore_iam"] = gcp.projects.IAMMember(
        "karaoke-backend-firestore-access",
        project=PROJECT_ID,
        role="roles/datastore.user",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Storage permissions
    bindings["storage_iam"] = gcp.projects.IAMMember(
        "karaoke-backend-storage-access",
        project=PROJECT_ID,
        role="roles/storage.objectAdmin",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Secret Manager read permissions
    bindings["secrets_read_iam"] = gcp.projects.IAMMember(
        "karaoke-backend-secrets-access",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Secret Manager write permissions (for OAuth token refresh/device auth)
    bindings["secrets_write_iam"] = gcp.projects.IAMMember(
        "karaoke-backend-secrets-write",
        project=PROJECT_ID,
        role="roles/secretmanager.secretVersionManager",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Cloud Trace write permissions (for OpenTelemetry tracing)
    bindings["cloud_trace_iam"] = gcp.projects.IAMMember(
        "karaoke-backend-cloudtrace-agent",
        project=PROJECT_ID,
        role="roles/cloudtrace.agent",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Vertex AI User permissions (for Gemini agentic AI correction)
    bindings["vertex_ai_iam"] = gcp.projects.IAMMember(
        "karaoke-backend-vertexai-user",
        project=PROJECT_ID,
        role="roles/aiplatform.user",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant backend service account permission to enqueue Cloud Tasks
    bindings["cloud_tasks_enqueuer"] = gcp.projects.IAMMember(
        "karaoke-backend-cloudtasks-enqueuer",
        project=PROJECT_ID,
        role="roles/cloudtasks.enqueuer",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Service Account User - to act as itself for Cloud Tasks OIDC
    bindings["act_as_self"] = serviceaccount.IAMBinding(
        "karaoke-backend-act-as-self",
        service_account_id=service_account.name,
        role="roles/iam.serviceAccountUser",
        members=[service_account.email.apply(lambda email: f"serviceAccount:{email}")],
    )

    # Cloud Run Jobs Developer - run encoding jobs
    bindings["run_jobs_developer"] = gcp.projects.IAMMember(
        "karaoke-backend-run-jobs-developer",
        project=PROJECT_ID,
        role="roles/run.developer",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Cloud Monitoring Viewer - read quota metrics for YouTube API quota tracking
    bindings["monitoring_viewer"] = gcp.projects.IAMMember(
        "karaoke-backend-monitoring-viewer",
        project=PROJECT_ID,
        role="roles/monitoring.viewer",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return bindings


def grant_cloud_tasks_invoker_permission() -> gcp.projects.IAMMember:
    """
    Grant Cloud Tasks service agent permission to invoke Cloud Run.

    This allows Cloud Tasks to authenticate when calling Cloud Run endpoints.

    Returns:
        gcp.projects.IAMMember: The IAM binding resource.
    """
    return gcp.projects.IAMMember(
        "cloud-tasks-invoker",
        project=PROJECT_ID,
        role="roles/run.invoker",
        member=f"serviceAccount:service-{get_project_number()}@gcp-sa-cloudtasks.iam.gserviceaccount.com",
    )
