"""
Encoding Worker Idle Shutdown — Cloud Function + Cloud Scheduler.

Automatically stops encoding worker VMs after 15 minutes of inactivity.
Checks every 5 minutes via Cloud Scheduler trigger.
"""

import pulumi
import pulumi_gcp as gcp
from config import PROJECT_ID, REGION, ENCODING_WORKER_ZONE, EncodingWorkerConfig


def create_idle_shutdown_service_account():
    """Create service account for the idle shutdown Cloud Function."""
    return gcp.serviceaccount.Account(
        "encoding-worker-idle-sa",
        account_id="encoding-worker-idle",
        display_name="Encoding Worker Idle Shutdown",
        description="Service account for the encoding worker idle auto-shutdown Cloud Function",
    )


def grant_idle_shutdown_permissions(service_account):
    """Grant permissions to the idle shutdown service account."""
    bindings = {}

    bindings["compute_viewer"] = gcp.projects.IAMMember(
        "idle-shutdown-compute-viewer",
        project=PROJECT_ID,
        role="roles/compute.viewer",
        member=service_account.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    stop_role = gcp.projects.IAMCustomRole(
        "encoding-worker-stop-role",
        role_id="encodingWorkerStop",
        title="Encoding Worker Stop",
        description="Stop encoding worker VMs (for idle shutdown)",
        permissions=["compute.instances.stop"],
        project=PROJECT_ID,
    )

    bindings["compute_stop"] = gcp.projects.IAMMember(
        "idle-shutdown-compute-stop",
        project=PROJECT_ID,
        role=stop_role.name,
        member=service_account.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    bindings["firestore"] = gcp.projects.IAMMember(
        "idle-shutdown-firestore",
        project=PROJECT_ID,
        role="roles/datastore.user",
        member=service_account.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    return bindings


def create_idle_shutdown_resources():
    """
    Create all resources for the encoding worker idle shutdown system.

    Returns:
        dict: Dictionary of all created resources.
    """
    sa = create_idle_shutdown_service_account()
    perms = grant_idle_shutdown_permissions(sa)

    source_bucket = gcp.storage.Bucket(
        "encoding-worker-idle-source",
        name=f"encoding-worker-idle-source-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=True,
        uniform_bucket_level_access=True,
    )

    function = gcp.cloudfunctionsv2.Function(
        "encoding-worker-idle-function",
        name=EncodingWorkerConfig.FUNCTION_NAME,
        location=REGION,
        description="Auto-stop idle encoding worker VMs",
        build_config=gcp.cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="idle_shutdown",
            source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=source_bucket.name,
                    object="encoding-worker-idle-source.zip",
                ),
            ),
        ),
        service_config=gcp.cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory=EncodingWorkerConfig.FUNCTION_MEMORY,
            timeout_seconds=EncodingWorkerConfig.FUNCTION_TIMEOUT,
            min_instance_count=0,
            max_instance_count=1,
            service_account_email=sa.email,
            environment_variables={
                "GCP_PROJECT": PROJECT_ID,
                "GCP_ZONE": ENCODING_WORKER_ZONE,
                "IDLE_TIMEOUT_MINUTES": str(EncodingWorkerConfig.IDLE_TIMEOUT_MINUTES),
            },
        ),
        opts=pulumi.ResourceOptions(
            depends_on=list(perms.values()),
        ),
    )

    scheduler_invoker = gcp.cloudfunctionsv2.FunctionIamMember(
        "idle-shutdown-scheduler-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=sa.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    run_invoker = gcp.cloudrunv2.ServiceIamMember(
        "idle-shutdown-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member=sa.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    scheduler = gcp.cloudscheduler.Job(
        "encoding-worker-idle-scheduler",
        name="encoding-worker-idle-check",
        description="Check and stop idle encoding worker VMs",
        region=REGION,
        schedule=EncodingWorkerConfig.IDLE_CHECK_SCHEDULE,
        time_zone="UTC",
        http_target=gcp.cloudscheduler.JobHttpTargetArgs(
            uri=function.url,
            http_method="POST",
            oidc_token=gcp.cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=sa.email,
            ),
        ),
        retry_config=gcp.cloudscheduler.JobRetryConfigArgs(
            retry_count=1,
            min_backoff_duration="60s",
            max_backoff_duration="300s",
        ),
    )

    return {
        "service_account": sa,
        "function": function,
        "scheduler": scheduler,
        "source_bucket": source_bucket,
    }
