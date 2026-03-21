"""
KaraokeNerds Data Sync infrastructure module.

Creates:
- GCS bucket for raw JSON exports
- Service account with BigQuery + GCS + Secret Manager permissions
- Cloud Function v2 for fetching KN data
- Two Cloud Scheduler jobs (full catalog + community tracks)
- BigQuery table for community tracks
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import (
    cloudfunctionsv2,
    cloudscheduler,
    serviceaccount,
    storage,
    bigquery,
)

from config import PROJECT_ID, REGION, get_project_number

BIGQUERY_DATASET = "karaoke_decide"


def create_kn_data_sync_resources(all_secrets: dict) -> dict:
    """Create all KN data sync infrastructure resources."""
    resources = {}

    # ==================== Service Account ====================

    sa = serviceaccount.Account(
        "kn-data-sync-sa",
        account_id="kn-data-sync",
        display_name="KaraokeNerds Data Sync Function",
        description="Service account for the KN data sync Cloud Function",
    )
    resources["service_account"] = sa

    # BigQuery Data Editor
    resources["bq_access"] = gcp.projects.IAMMember(
        "kn-data-sync-bigquery-access",
        project=PROJECT_ID,
        role="roles/bigquery.dataEditor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # BigQuery Job User
    resources["bq_job_user"] = gcp.projects.IAMMember(
        "kn-data-sync-bigquery-job-user",
        project=PROJECT_ID,
        role="roles/bigquery.jobUser",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Secret Manager Accessor (for KN API key)
    resources["secrets_access"] = gcp.projects.IAMMember(
        "kn-data-sync-secrets-access",
        project=PROJECT_ID,
        role="roles/secretmanager.secretAccessor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Logging Writer
    resources["logging_access"] = gcp.projects.IAMMember(
        "kn-data-sync-logging-access",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== GCS Bucket for exports ====================

    data_bucket = storage.Bucket(
        "kn-data-bucket",
        name="nomadkaraoke-kn-data",
        location="US-CENTRAL1",
        force_destroy=False,  # Keep historical exports
        uniform_bucket_level_access=True,
        lifecycle_rules=[
            storage.BucketLifecycleRuleArgs(
                action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
                condition=storage.BucketLifecycleRuleConditionArgs(
                    age=365,  # Keep 1 year of exports
                    matches_prefixes=["full/full-data-2", "community/community-data-2"],
                ),
            ),
        ],
    )
    resources["data_bucket"] = data_bucket

    # Grant SA write access to the data bucket
    resources["bucket_access"] = storage.BucketIAMMember(
        "kn-data-sync-bucket-access",
        bucket=data_bucket.name,
        role="roles/storage.objectAdmin",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Function Source Bucket ====================

    source_bucket = storage.Bucket(
        "kn-data-sync-function-source",
        name=f"kn-data-sync-source-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=True,
        uniform_bucket_level_access=True,
    )
    resources["source_bucket"] = source_bucket

    # ==================== BigQuery: Community Table ====================

    # The karaokenerds_raw table already exists (managed by karaoke-decide).
    # We add a new community table for the community tracks with YouTube URLs.
    community_table = bigquery.Table(
        "karaokenerds-community-table",
        dataset_id=BIGQUERY_DATASET,
        table_id="karaokenerds_community",
        project=PROJECT_ID,
        schema="""[
            {"name": "Artist", "type": "STRING", "mode": "REQUIRED"},
            {"name": "Title", "type": "STRING", "mode": "REQUIRED"},
            {"name": "Brand", "type": "STRING", "mode": "REQUIRED"},
            {"name": "Watch", "type": "STRING", "mode": "NULLABLE"},
            {"name": "Created", "type": "STRING", "mode": "NULLABLE"},
            {"name": "Id", "type": "INTEGER", "mode": "NULLABLE"}
        ]""",
        deletion_protection=False,
    )
    resources["community_table"] = community_table

    # ==================== Cloud Function ====================

    function = cloudfunctionsv2.Function(
        "kn-data-sync-function",
        name="kn-data-sync",
        location=REGION,
        description="Syncs KaraokeNerds catalog data to BigQuery and GCS",
        build_config=cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="sync_kn_data",
            source=cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=source_bucket.name,
                    object="kn-data-sync-source.zip",
                ),
            ),
        ),
        service_config=cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory="512M",
            timeout_seconds=300,  # 5 minutes
            min_instance_count=0,
            max_instance_count=1,
            service_account_email=sa.email,
            environment_variables={
                "GCP_PROJECT_ID": PROJECT_ID,
                "GCS_BUCKET": "nomadkaraoke-kn-data",
            },
            secret_environment_variables=[
                cloudfunctionsv2.FunctionServiceConfigSecretEnvironmentVariableArgs(
                    key="KARAOKENERDS_API_KEY",
                    project_id=PROJECT_ID,
                    secret=all_secrets["karaokenerds-api-key"].secret_id,
                    version="latest",
                ),
            ],
        ),
    )
    resources["function"] = function

    # ==================== IAM: Cloud Scheduler → Function ====================

    resources["sa_run_invoker"] = gcp.cloudrunv2.ServiceIamMember(
        "kn-data-sync-sa-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    resources["sa_cf_invoker"] = cloudfunctionsv2.FunctionIamMember(
        "kn-data-sync-sa-cf-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Cloud Scheduler Jobs ====================

    # Full catalog sync — daily at 4:30 AM ET
    resources["full_scheduler"] = cloudscheduler.Job(
        "kn-data-sync-full-scheduler",
        name="kn-data-sync-full-daily",
        description="Daily sync of full KaraokeNerds song catalog to BigQuery",
        region=REGION,
        schedule="30 4 * * *",
        time_zone="America/New_York",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=function.url,
            http_method="POST",
            body=pulumi.Output.from_input(
                '{"mode": "full"}'
            ).apply(lambda b: __import__("base64").b64encode(b.encode()).decode()),
            headers={"Content-Type": "application/json"},
            oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=sa.email,
            ),
        ),
        retry_config=cloudscheduler.JobRetryConfigArgs(
            retry_count=2,
            min_backoff_duration="60s",
            max_backoff_duration="300s",
        ),
    )

    # Community sync — daily at 3:05 AM ET
    resources["community_scheduler"] = cloudscheduler.Job(
        "kn-data-sync-community-scheduler",
        name="kn-data-sync-community-daily",
        description="Daily sync of KaraokeNerds community tracks to BigQuery",
        region=REGION,
        schedule="5 3 * * *",
        time_zone="America/New_York",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=function.url,
            http_method="POST",
            body=pulumi.Output.from_input(
                '{"mode": "community"}'
            ).apply(lambda b: __import__("base64").b64encode(b.encode()).decode()),
            headers={"Content-Type": "application/json"},
            oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=sa.email,
            ),
        ),
        retry_config=cloudscheduler.JobRetryConfigArgs(
            retry_count=2,
            min_backoff_duration="60s",
            max_backoff_duration="300s",
        ),
    )

    return resources
