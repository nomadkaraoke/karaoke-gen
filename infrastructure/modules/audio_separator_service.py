"""
Audio Separator Cloud Run GPU Service.

Deploys the audio-separator API as a Cloud Run service with L4 GPU acceleration.
Replaces the Modal deployment for audio stem separation.

Resources created:
- Artifact Registry repository for audio-separator Docker images
- Cloud Run GPU service (L4, scale-to-zero)
- Service account with minimal permissions
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import artifactregistry, cloudrunv2, serviceaccount
from pulumi_gcp.cloudrunv2 import ServiceTemplateNodeSelectorArgs, ServiceTemplateContainerPortsArgs

from config import PROJECT_ID, REGION

# Audio separator uses us-east4 due to L4 GPU quota availability.
# Can be moved to us-central1 once quota is approved there.
AUDIO_SEPARATOR_REGION = "us-east4"


def create_audio_separator_artifact_repo() -> artifactregistry.Repository:
    """Create Artifact Registry repo for audio-separator Docker images."""
    return artifactregistry.Repository(
        "audio-separator-artifact-repo",
        repository_id="audio-separator",
        location=AUDIO_SEPARATOR_REGION,
        format="DOCKER",
        description="Docker repository for audio-separator GPU service",
    )


def create_service_account() -> serviceaccount.Account:
    """Create service account for the audio separator Cloud Run service."""
    return serviceaccount.Account(
        "audio-separator-sa",
        account_id="audio-separator",
        display_name="Audio Separator Service Account",
        description="Service account for audio separator Cloud Run GPU service",
    )


def create_output_bucket() -> gcp.storage.Bucket:
    """Create GCS bucket for separation output files with auto-cleanup."""
    bucket = gcp.storage.Bucket(
        "audio-separator-output-bucket",
        name="nomadkaraoke-audio-separator-outputs",
        location="US",
        force_destroy=True,
        uniform_bucket_level_access=True,
        lifecycle_rules=[
            gcp.storage.BucketLifecycleRuleArgs(
                action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=gcp.storage.BucketLifecycleRuleConditionArgs(
                    age=1,  # Delete after 1 day (minimum GCS lifecycle granularity)
                ),
            ),
        ],
    )
    return bucket


def grant_permissions(
    sa: serviceaccount.Account,
) -> dict:
    """Grant permissions to the audio separator service account."""
    bindings = {}

    # Read admin-tokens secret for API auth
    bindings["secrets_access"] = gcp.projects.IAMMember(
        "audio-separator-secrets-access",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Write logs
    bindings["logging"] = gcp.projects.IAMMember(
        "audio-separator-logging",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Write metrics
    bindings["monitoring"] = gcp.projects.IAMMember(
        "audio-separator-monitoring",
        project=PROJECT_ID,
        role="roles/monitoring.metricWriter",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Read/write Firestore for async job status
    bindings["firestore"] = gcp.projects.IAMMember(
        "audio-separator-firestore-access",
        project=PROJECT_ID,
        role="roles/datastore.user",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return bindings


def create_service(
    sa: serviceaccount.Account,
    artifact_repo: artifactregistry.Repository,
) -> cloudrunv2.Service:
    """
    Create the Cloud Run GPU service for audio separation.

    Uses L4 GPU for fast inference. Scales to zero when idle.
    Models are baked into the Docker image (no GCS download needed).
    """
    service = cloudrunv2.Service(
        "audio-separator-service",
        name="audio-separator",
        location=AUDIO_SEPARATOR_REGION,
        ingress="INGRESS_TRAFFIC_ALL",
        template=cloudrunv2.ServiceTemplateArgs(
            scaling=cloudrunv2.ServiceTemplateScalingArgs(
                min_instance_count=0,
                max_instance_count=5,  # Cloud Run GPU services limited to 5 max instances
            ),
            max_instance_request_concurrency=1,  # One GPU job per instance; forces Cloud Run to scale to new instances for concurrent jobs
            gpu_zonal_redundancy_disabled=True,  # Not needed for batch workloads
            # Keep instances warm for 600s (10 min) between Stage 1 → Stage 2
            session_affinity=True,
            timeout="1800s",  # 30 min max per request
            service_account=sa.email,
            containers=[
                cloudrunv2.ServiceTemplateContainerArgs(
                    # Initial placeholder image - will be updated by CI/CD
                    image=artifact_repo.name.apply(
                        lambda name: f"{AUDIO_SEPARATOR_REGION}-docker.pkg.dev/{PROJECT_ID}/audio-separator/api:latest"
                    ),
                    ports=ServiceTemplateContainerPortsArgs(
                        container_port=8080,
                    ),
                    resources=cloudrunv2.ServiceTemplateContainerResourcesArgs(
                        limits={
                            "cpu": "4",
                            "memory": "16Gi",
                            "nvidia.com/gpu": "1",
                        },
                        startup_cpu_boost=True,
                    ),
                    envs=[
                        cloudrunv2.ServiceTemplateContainerEnvArgs(
                            name="MODEL_DIR",
                            value="/models",
                        ),
                        cloudrunv2.ServiceTemplateContainerEnvArgs(
                            name="STORAGE_DIR",
                            value="/tmp/storage",
                        ),
                        cloudrunv2.ServiceTemplateContainerEnvArgs(
                            name="OUTPUT_BUCKET",
                            value="nomadkaraoke-audio-separator-outputs",
                        ),
                        cloudrunv2.ServiceTemplateContainerEnvArgs(
                            name="GCP_PROJECT",
                            value=PROJECT_ID,
                        ),
                    ],
                    startup_probe=cloudrunv2.ServiceTemplateContainerStartupProbeArgs(
                        http_get=cloudrunv2.ServiceTemplateContainerStartupProbeHttpGetArgs(
                            path="/health",
                            port=8080,
                        ),
                        initial_delay_seconds=5,
                        period_seconds=5,
                        timeout_seconds=5,
                        failure_threshold=12,  # Models baked in, startup is fast
                    ),
                    liveness_probe=cloudrunv2.ServiceTemplateContainerLivenessProbeArgs(
                        http_get=cloudrunv2.ServiceTemplateContainerLivenessProbeHttpGetArgs(
                            path="/health",
                            port=8080,
                        ),
                        period_seconds=30,
                        timeout_seconds=5,
                        failure_threshold=3,
                    ),
                ),
            ],
            # L4 GPU node selector
            node_selector=ServiceTemplateNodeSelectorArgs(
                accelerator="nvidia-l4",
            ),
        ),
    )

    return service


def allow_unauthenticated_access(service: cloudrunv2.Service) -> cloudrunv2.ServiceIamMember:
    """
    Allow unauthenticated access to the audio separator service.

    Auth is handled at the application level via admin-tokens,
    matching the pattern used by the encoding worker.
    """
    return cloudrunv2.ServiceIamMember(
        "audio-separator-allow-unauthenticated",
        project=PROJECT_ID,
        location=AUDIO_SEPARATOR_REGION,
        name=service.name,
        role="roles/run.invoker",
        member="allUsers",
    )


def create_all_resources() -> dict:
    """
    Create all audio separator infrastructure resources.

    Returns dict of all created resources for export.
    """
    artifact_repo = create_audio_separator_artifact_repo()
    sa = create_service_account()
    output_bucket = create_output_bucket()
    iam_bindings = grant_permissions(sa)

    # Output bucket IAM (separate from grant_permissions since it needs the bucket)
    output_bucket_iam = gcp.storage.BucketIAMMember(
        "audio-separator-output-bucket-access",
        bucket=output_bucket.name,
        role="roles/storage.objectAdmin",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    service = create_service(sa, artifact_repo)
    unauthenticated_access = allow_unauthenticated_access(service)

    return {
        "artifact_repo": artifact_repo,
        "service_account": sa,
        "output_bucket": output_bucket,
        "service": service,
        "iam_bindings": iam_bindings,
    }
