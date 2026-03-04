"""
GitHub Actions Runner Manager - Auto-start and auto-stop self-hosted runners.

Creates infrastructure to automatically manage runner lifecycle:
1. Cloud Function (Gen2) - Handles GitHub webhooks and idle checks
2. Cloud Scheduler - Triggers idle checks every 15 minutes
3. IAM bindings - Permissions to start/stop Compute Engine instances

Architecture:
- GitHub sends workflow_job webhooks to the Cloud Function
- When a job is queued, the function starts any TERMINATED runner VMs
- Cloud Scheduler triggers idle checks every 15 minutes
- If no jobs are pending and runners have been idle, they're stopped

Manual Setup Required:
1. Create a webhook secret:
   python -c "import secrets; print(secrets.token_hex(32))" | \
     gcloud secrets versions add github-webhook-secret --data-file=-

2. Configure GitHub org webhook at:
   https://github.com/organizations/nomadkaraoke/settings/hooks
   - Payload URL: <Cloud Function URL from pulumi output>
   - Content type: application/json
   - Secret: <value from step 1>
   - Events: Workflow jobs
"""

import base64
import json
from pathlib import Path

import pulumi
import pulumi_gcp as gcp
from config import (
    PROJECT_ID,
    REGION,
    ZONE,
    RunnerManagerConfig,
    SecretNames,
)


def create_runner_manager_service_account() -> gcp.serviceaccount.Account:
    """Create service account for the runner manager Cloud Function."""
    return gcp.serviceaccount.Account(
        "runner-manager-sa",
        account_id="runner-manager",
        display_name="GitHub Runner Manager",
        description="Service account for the GitHub Actions runner manager Cloud Function",
    )


def grant_runner_manager_permissions(
    service_account: gcp.serviceaccount.Account,
    webhook_secret: gcp.secretmanager.Secret,
    pat_secret: gcp.secretmanager.Secret,
) -> dict:
    """Grant permissions to the runner manager service account."""
    bindings = {}

    # Grant Compute Instance Admin - needed to start/stop VMs
    bindings["compute_admin"] = gcp.projects.IAMMember(
        "runner-manager-compute-admin",
        project=PROJECT_ID,
        role="roles/compute.instanceAdmin.v1",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Secret Manager accessor for webhook secret
    bindings["webhook_secret_accessor"] = gcp.secretmanager.SecretIamMember(
        "runner-manager-webhook-secret-access",
        secret_id=webhook_secret.id,
        role="roles/secretmanager.secretAccessor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant Secret Manager accessor for GitHub PAT (for checking pending jobs)
    bindings["pat_secret_accessor"] = gcp.secretmanager.SecretIamMember(
        "runner-manager-pat-secret-access",
        secret_id=pat_secret.id,
        role="roles/secretmanager.secretAccessor",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Grant logging writer
    bindings["logging_writer"] = gcp.projects.IAMMember(
        "runner-manager-logging-writer",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return bindings


def create_function_storage_bucket() -> gcp.storage.Bucket:
    """Create a GCS bucket for Cloud Function source code."""
    return gcp.storage.Bucket(
        "runner-manager-source-bucket",
        name=f"{PROJECT_ID}-runner-manager-source",
        location=REGION,
        uniform_bucket_level_access=True,
        lifecycle_rules=[
            gcp.storage.BucketLifecycleRuleArgs(
                action=gcp.storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=gcp.storage.BucketLifecycleRuleConditionArgs(age=7),
            )
        ],
    )


def create_function_source_archive(bucket: gcp.storage.Bucket) -> gcp.storage.BucketObject:
    """Create a GCS object with the Cloud Function source code."""
    source_dir = Path(__file__).parent.parent / "functions" / "runner_manager"

    return gcp.storage.BucketObject(
        "runner-manager-source",
        bucket=bucket.name,
        name="runner-manager-source.zip",
        source=pulumi.FileArchive(str(source_dir)),
    )


def create_cloud_function(
    service_account: gcp.serviceaccount.Account,
    bucket: gcp.storage.Bucket,
    source_archive: gcp.storage.BucketObject,
    permissions: dict,
    runner_names: list[pulumi.Output] | None = None,
) -> gcp.cloudfunctionsv2.Function:
    """Create the Cloud Function (Gen2) for runner management."""
    env_vars = {
        "GCP_PROJECT": PROJECT_ID,
        "GCP_ZONE": ZONE,
        "WEBHOOK_SECRET_NAME": SecretNames.GITHUB_WEBHOOK_SECRET,
        "RUNNER_PAT_SECRET_NAME": SecretNames.GITHUB_RUNNER_PAT,
        "IDLE_TIMEOUT_HOURS": str(RunnerManagerConfig.IDLE_TIMEOUT_HOURS),
    }

    if runner_names:
        env_vars["RUNNER_NAMES"] = pulumi.Output.all(*runner_names).apply(
            lambda names: ",".join(names)
        )

    return gcp.cloudfunctionsv2.Function(
        "runner-manager-function",
        name=RunnerManagerConfig.FUNCTION_NAME,
        location=REGION,
        description="Manages GitHub Actions runner VM lifecycle (auto-start/stop)",
        build_config=gcp.cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="handle_request",
            source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=bucket.name,
                    object=source_archive.name,
                ),
            ),
        ),
        service_config=gcp.cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory=RunnerManagerConfig.FUNCTION_MEMORY,
            timeout_seconds=RunnerManagerConfig.FUNCTION_TIMEOUT,
            service_account_email=service_account.email,
            environment_variables=env_vars,
            min_instance_count=0,  # Scale to zero when not in use
            max_instance_count=5,  # Allow some concurrency for multiple webhooks
        ),
        opts=pulumi.ResourceOptions(
            depends_on=list(permissions.values()),
        ),
    )


def allow_unauthenticated_invocation(
    function: gcp.cloudfunctionsv2.Function,
) -> gcp.cloudfunctionsv2.FunctionIamMember:
    """
    Allow unauthenticated invocations of the Cloud Function.

    Required for GitHub webhooks since GitHub doesn't support GCP auth.
    Security is provided by webhook signature verification.
    """
    return gcp.cloudfunctionsv2.FunctionIamMember(
        "runner-manager-public-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member="allUsers",
    )


def create_scheduler_service_account() -> gcp.serviceaccount.Account:
    """Create service account for Cloud Scheduler to invoke the function."""
    return gcp.serviceaccount.Account(
        "runner-manager-scheduler-sa",
        account_id="runner-manager-scheduler",
        display_name="Runner Manager Scheduler",
        description="Service account for Cloud Scheduler to trigger runner idle checks",
    )


def grant_scheduler_invoker_permission(
    scheduler_sa: gcp.serviceaccount.Account,
    function: gcp.cloudfunctionsv2.Function,
) -> gcp.cloudfunctionsv2.FunctionIamMember:
    """Grant the scheduler service account permission to invoke the function."""
    return gcp.cloudfunctionsv2.FunctionIamMember(
        "runner-manager-scheduler-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=scheduler_sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )


def create_idle_check_scheduler(
    scheduler_sa: gcp.serviceaccount.Account,
    function: gcp.cloudfunctionsv2.Function,
) -> gcp.cloudscheduler.Job:
    """Create Cloud Scheduler job for idle runner checks."""
    return gcp.cloudscheduler.Job(
        "runner-manager-idle-check",
        name="runner-manager-idle-check",
        description="Triggers idle check for GitHub Actions runners every 15 minutes",
        schedule=RunnerManagerConfig.IDLE_CHECK_SCHEDULE,
        time_zone="UTC",
        region=REGION,
        http_target=gcp.cloudscheduler.JobHttpTargetArgs(
            uri=function.url.apply(lambda url: f"{url}?action=check_idle"),
            http_method="POST",
            headers={
                "Content-Type": "application/json",
            },
            body=base64.b64encode(json.dumps({"trigger": "scheduler"}).encode()).decode(),
            oidc_token=gcp.cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=scheduler_sa.email,
                audience=function.url,
            ),
        ),
        retry_config=gcp.cloudscheduler.JobRetryConfigArgs(
            retry_count=2,
            max_retry_duration="60s",
        ),
    )


def create_runner_manager_resources(
    webhook_secret: gcp.secretmanager.Secret,
    pat_secret: gcp.secretmanager.Secret,
    runner_names: list[pulumi.Output] | None = None,
) -> dict:
    """
    Create all resources for the GitHub Actions runner manager.

    Args:
        webhook_secret: The webhook secret for signature verification.
        pat_secret: The GitHub PAT secret for API calls.
        runner_names: List of Pulumi Output VM names for the runner manager to track.

    Returns:
        dict: Dictionary of all created resources.
    """
    # Create service accounts
    function_sa = create_runner_manager_service_account()
    scheduler_sa = create_scheduler_service_account()

    # Grant permissions to function service account
    permissions = grant_runner_manager_permissions(
        function_sa,
        webhook_secret,
        pat_secret,
    )

    # Create source storage bucket and archive
    bucket = create_function_storage_bucket()
    source_archive = create_function_source_archive(bucket)

    # Create the Cloud Function
    function = create_cloud_function(
        function_sa,
        bucket,
        source_archive,
        permissions,
        runner_names,
    )

    # Allow unauthenticated invocation (GitHub webhooks)
    public_access = allow_unauthenticated_invocation(function)

    # Grant scheduler permission to invoke function
    scheduler_invoker = grant_scheduler_invoker_permission(scheduler_sa, function)

    # Create the idle check scheduler job
    scheduler_job = create_idle_check_scheduler(scheduler_sa, function)

    return {
        "function_service_account": function_sa,
        "scheduler_service_account": scheduler_sa,
        "permissions": permissions,
        "source_bucket": bucket,
        "source_archive": source_archive,
        "function": function,
        "public_access": public_access,
        "scheduler_invoker": scheduler_invoker,
        "scheduler_job": scheduler_job,
    }
