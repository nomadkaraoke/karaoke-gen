"""
Backup infrastructure module.

Creates:
- GCS staging bucket for backup exports
- Service account for backup Cloud Function
- Cloud Function v2 (Gen2) for nightly backups
- Cloud Scheduler job for daily trigger
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import (
    cloudfunctionsv2,
    cloudscheduler,
    serviceaccount,
    storage,
)

from config import PROJECT_ID, REGION, get_project_number


def create_backup_resources(all_secrets: dict) -> dict:
    """Create all backup infrastructure resources."""
    resources = {}

    # ==================== Service Account ====================

    sa = serviceaccount.Account(
        "backup-to-aws-sa",
        account_id="backup-to-aws",
        display_name="Backup to AWS Function",
        description="Service account for nightly backup Cloud Function",
    )
    resources["service_account"] = sa

    # Firestore export permission
    resources["firestore_export"] = gcp.projects.IAMMember(
        "backup-firestore-export",
        project=PROJECT_ID,
        role="roles/datastore.importExportAdmin",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # BigQuery read access
    resources["bq_viewer"] = gcp.projects.IAMMember(
        "backup-bigquery-viewer",
        project=PROJECT_ID,
        role="roles/bigquery.dataViewer",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    resources["bq_job_user"] = gcp.projects.IAMMember(
        "backup-bigquery-job-user",
        project=PROJECT_ID,
        role="roles/bigquery.jobUser",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Secret Manager access (for AWS credentials + nightly secrets backup)
    resources["secret_accessor"] = gcp.projects.IAMMember(
        "backup-secret-accessor",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # secretmanager.viewer is required to LIST secrets — accessor only allows reading
    # a known name. Backup needs to enumerate every secret in the project.
    resources["secret_viewer"] = gcp.projects.IAMMember(
        "backup-secret-viewer",
        project=PROJECT_ID,
        role="roles/secretmanager.viewer",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Logging
    resources["logging"] = gcp.projects.IAMMember(
        "backup-logging",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Staging Bucket ====================

    staging_bucket = storage.Bucket(
        "backup-staging-bucket",
        name="nomadkaraoke-backup-staging",
        location="US-CENTRAL1",
        force_destroy=True,  # Staging only — OK to destroy
        uniform_bucket_level_access=True,
        lifecycle_rules=[
            storage.BucketLifecycleRuleArgs(
                action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=storage.BucketLifecycleRuleConditionArgs(age=7),
            ),
        ],
    )
    resources["staging_bucket"] = staging_bucket

    # SA needs objectAdmin on staging bucket (read + write + delete)
    resources["staging_bucket_access"] = storage.BucketIAMMember(
        "backup-staging-bucket-access",
        bucket=staging_bucket.name,
        role="roles/storage.objectAdmin",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Firestore service agent needs write access to staging bucket for exports
    resources["firestore_agent_staging_access"] = storage.BucketIAMMember(
        "backup-firestore-agent-staging-access",
        bucket=staging_bucket.name,
        role="roles/storage.objectAdmin",
        member=f"serviceAccount:service-{get_project_number()}@gcp-sa-firestore.iam.gserviceaccount.com",
    )

    # SA needs objectViewer on source buckets
    source_buckets = [
        "karaoke-gen-storage-nomadkaraoke",
        "nomadkaraoke-kn-data",
    ]
    for i, bucket_name in enumerate(source_buckets):
        resources[f"source_bucket_access_{i}"] = storage.BucketIAMMember(
            f"backup-source-bucket-access-{i}",
            bucket=bucket_name,
            role="roles/storage.objectViewer",
            member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
        )

    # ==================== Function Source Bucket ====================

    function_source_bucket = storage.Bucket(
        "backup-function-source",
        name=f"backup-to-aws-source-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=True,
        uniform_bucket_level_access=True,
    )
    resources["function_source_bucket"] = function_source_bucket

    # ==================== Cloud Function ====================

    function = cloudfunctionsv2.Function(
        "backup-to-aws-function",
        name="backup-to-aws",
        location=REGION,
        description="Nightly backup of Firestore, BigQuery, and GCS to AWS S3",
        build_config=cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="backup_to_aws",
            source=cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=function_source_bucket.name,
                    object="backup-to-aws-source.zip",
                ),
            ),
        ),
        service_config=cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory="2Gi",
            available_cpu="1",
            timeout_seconds=3600,
            min_instance_count=0,
            max_instance_count=1,
            service_account_email=sa.email,
            environment_variables={
                "GCP_PROJECT": PROJECT_ID,
                "STAGING_BUCKET": "nomadkaraoke-backup-staging",
                "S3_BUCKET": "nomadkaraoke-backup",
                # Curve25519 public key (hex) for sealed-box encryption of secrets backup.
                # The matching private key lives only in 1Password — function cannot decrypt.
                # Set via: pulumi config set backupEncryptionPubkey <64-char-hex>
                "BACKUP_ENCRYPTION_PUBKEY": pulumi.Config().require("backupEncryptionPubkey"),
            },
            secret_environment_variables=[
                cloudfunctionsv2.FunctionServiceConfigSecretEnvironmentVariableArgs(
                    key="DISCORD_WEBHOOK_URL",
                    project_id=PROJECT_ID,
                    secret=all_secrets["discord-alert-webhook"].secret_id,
                    version="latest",
                ),
            ],
        ),
    )
    resources["function"] = function

    # Allow Cloud Scheduler to invoke the function
    resources["scheduler_invoker"] = cloudfunctionsv2.FunctionIamMember(
        "backup-scheduler-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Gen2 functions need Cloud Run invoker too
    resources["run_invoker"] = gcp.cloudrunv2.ServiceIamMember(
        "backup-sa-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Cloud Scheduler ====================

    scheduler = cloudscheduler.Job(
        "backup-scheduler",
        name="backup-to-aws-nightly",
        description="Trigger nightly backup to AWS S3",
        region=REGION,
        schedule="0 1 * * *",
        time_zone="America/New_York",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=function.url,
            http_method="POST",
            oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=sa.email,
            ),
        ),
        retry_config=cloudscheduler.JobRetryConfigArgs(
            retry_count=2,
            min_backoff_duration="300s",
            max_backoff_duration="1800s",
        ),
    )
    resources["scheduler"] = scheduler

    # Quarterly DR restore drill reminder — posts a Discord nudge so the human
    # remembers to run the drill in docs/DISASTER-RECOVERY.md § "Quarterly restore drill".
    # Hits the same backup function with ?mode=drill_reminder, which short-circuits
    # the pipeline and only posts to Discord.
    drill_scheduler = cloudscheduler.Job(
        "dr-drill-scheduler",
        name="dr-restore-drill-reminder",
        description="Quarterly nudge to run the DR restore drill",
        region=REGION,
        # 15:00 UTC on the 1st of Jan/Apr/Jul/Oct
        schedule="0 15 1 1,4,7,10 *",
        time_zone="UTC",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=function.url.apply(lambda u: f"{u}?mode=drill_reminder"),
            http_method="POST",
            oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=sa.email,
                # OIDC audience must match the function URL without the query string
                audience=function.url,
            ),
        ),
        retry_config=cloudscheduler.JobRetryConfigArgs(
            retry_count=1,
            min_backoff_duration="60s",
            max_backoff_duration="300s",
        ),
    )
    resources["drill_scheduler"] = drill_scheduler

    return resources
