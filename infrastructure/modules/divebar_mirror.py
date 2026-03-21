"""
Divebar Mirror infrastructure module.

Creates:
- GCS bucket for function source code
- Service account with Drive API + BigQuery permissions
- Cloud Function v2 (Gen2) for indexing Divebar Drive files
- Cloud Scheduler job for daily sync
- BigQuery table for the Divebar catalog index
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

# Divebar shared folder ID (public Google Drive folder)
DIVEBAR_FOLDER_ID = "1zxnSZcE03gzy0YVGOdnTrEIi8It_3Wu8"

# BigQuery dataset (shared with karaoke-decide)
BIGQUERY_DATASET = "karaoke_decide"


def create_divebar_mirror_resources(all_secrets: dict) -> dict:
    """Create all Divebar mirror infrastructure resources."""
    resources = {}

    # ==================== Service Account ====================

    sa = serviceaccount.Account(
        "divebar-mirror-sa",
        account_id="divebar-mirror",
        display_name="Divebar Mirror Function",
        description="Service account for the Divebar Drive mirror/indexer Cloud Function",
    )
    resources["service_account"] = sa

    # BigQuery Data Editor - write to divebar_catalog table
    resources["bq_access"] = gcp.projects.IAMMember(
        "divebar-mirror-bigquery-access",
        project=PROJECT_ID,
        role="roles/bigquery.dataEditor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # BigQuery Job User - run load jobs
    resources["bq_job_user"] = gcp.projects.IAMMember(
        "divebar-mirror-bigquery-job-user",
        project=PROJECT_ID,
        role="roles/bigquery.jobUser",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Logging Writer
    resources["logging_access"] = gcp.projects.IAMMember(
        "divebar-mirror-logging-access",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Function Source Bucket ====================

    source_bucket = storage.Bucket(
        "divebar-mirror-function-source",
        name=f"divebar-mirror-source-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=True,
        uniform_bucket_level_access=True,
    )
    resources["source_bucket"] = source_bucket

    # ==================== BigQuery Table ====================

    # The table is in the karaoke_decide dataset (managed by karaoke-decide Pulumi).
    # We reference it here but don't create the dataset — it already exists.
    divebar_table = bigquery.Table(
        "divebar-catalog-table",
        dataset_id=BIGQUERY_DATASET,
        table_id="divebar_catalog",
        project=PROJECT_ID,
        schema="""[
            {"name": "file_id", "type": "STRING", "mode": "REQUIRED"},
            {"name": "brand", "type": "STRING", "mode": "REQUIRED"},
            {"name": "brand_code", "type": "STRING", "mode": "NULLABLE"},
            {"name": "artist", "type": "STRING", "mode": "NULLABLE"},
            {"name": "title", "type": "STRING", "mode": "NULLABLE"},
            {"name": "disc_id", "type": "STRING", "mode": "NULLABLE"},
            {"name": "filename", "type": "STRING", "mode": "REQUIRED"},
            {"name": "format", "type": "STRING", "mode": "REQUIRED"},
            {"name": "subfolder", "type": "STRING", "mode": "NULLABLE"},
            {"name": "file_size", "type": "INT64", "mode": "NULLABLE"},
            {"name": "drive_path", "type": "STRING", "mode": "REQUIRED"},
            {"name": "drive_md5", "type": "STRING", "mode": "NULLABLE"},
            {"name": "artist_normalized", "type": "STRING", "mode": "NULLABLE"},
            {"name": "title_normalized", "type": "STRING", "mode": "NULLABLE"},
            {"name": "synced_at", "type": "TIMESTAMP", "mode": "REQUIRED"}
        ]""",
        deletion_protection=False,
    )
    resources["bigquery_table"] = divebar_table

    # ==================== Cloud Function ====================

    function = cloudfunctionsv2.Function(
        "divebar-mirror-function",
        name="divebar-mirror",
        location=REGION,
        description="Indexes diveBar Karaoke Google Drive files into BigQuery",
        build_config=cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="sync_divebar_index",
            source=cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=source_bucket.name,
                    object="divebar-mirror-source.zip",
                ),
            ),
        ),
        service_config=cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory="512M",
            timeout_seconds=540,  # 9 minutes (listing 50K+ files takes time)
            min_instance_count=0,
            max_instance_count=1,
            service_account_email=sa.email,
            environment_variables={
                "DIVEBAR_FOLDER_ID": DIVEBAR_FOLDER_ID,
                "GCP_PROJECT_ID": PROJECT_ID,
            },
        ),
    )
    resources["function"] = function

    # ==================== IAM: Cloud Scheduler → Function ====================

    # Allow the SA to invoke itself (Cloud Scheduler uses OIDC with SA)
    resources["sa_run_invoker"] = gcp.cloudrunv2.ServiceIamMember(
        "divebar-mirror-sa-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    resources["sa_cf_invoker"] = cloudfunctionsv2.FunctionIamMember(
        "divebar-mirror-sa-cf-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Cloud Scheduler ====================

    scheduler = cloudscheduler.Job(
        "divebar-mirror-scheduler",
        name="divebar-mirror-daily",
        description="Daily sync of diveBar Karaoke Google Drive index to BigQuery",
        region=REGION,
        schedule="0 2 * * *",  # 2:00 AM ET daily
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
            min_backoff_duration="60s",
            max_backoff_duration="300s",
        ),
    )
    resources["scheduler"] = scheduler

    return resources
