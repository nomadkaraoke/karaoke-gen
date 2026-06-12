"""
Divebar Lookup API infrastructure module.

Creates:
- Cloud Function v2 serving the search/lookup/xref API
- BigQuery cross-reference table (kn_divebar_xref)
- Service account with BigQuery read/write permissions
- Cloud Scheduler to rebuild xref daily (after mirror + KN sync complete)
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


def create_divebar_lookup_resources(all_secrets: dict) -> dict:
    """Create all Divebar lookup API infrastructure resources."""
    resources = {}

    # ==================== Service Account ====================

    sa = serviceaccount.Account(
        "divebar-lookup-sa",
        account_id="divebar-lookup",
        display_name="Divebar Lookup API Function",
        description="Service account for the Divebar search/lookup/xref Cloud Function",
    )
    resources["service_account"] = sa

    # BigQuery Data Editor (for creating xref table)
    resources["bq_access"] = gcp.projects.IAMMember(
        "divebar-lookup-bigquery-access",
        project=PROJECT_ID,
        role="roles/bigquery.dataEditor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # BigQuery Job User (for running queries)
    resources["bq_job_user"] = gcp.projects.IAMMember(
        "divebar-lookup-bigquery-job-user",
        project=PROJECT_ID,
        role="roles/bigquery.jobUser",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Logging Writer
    resources["logging_access"] = gcp.projects.IAMMember(
        "divebar-lookup-logging-access",
        project=PROJECT_ID,
        role="roles/logging.logWriter",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Function Source Bucket ====================

    source_bucket = storage.Bucket(
        "divebar-lookup-function-source",
        name=f"divebar-lookup-source-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=True,
        uniform_bucket_level_access=True,
    )
    resources["source_bucket"] = source_bucket

    # ==================== BigQuery: Cross-Reference Table ====================

    xref_table = bigquery.Table(
        "kn-divebar-xref-table",
        dataset_id=BIGQUERY_DATASET,
        table_id="kn_divebar_xref",
        project=PROJECT_ID,
        schema="""[
            {"name": "kn_id", "type": "INT64", "mode": "REQUIRED"},
            {"name": "divebar_file_id", "type": "STRING", "mode": "REQUIRED"},
            {"name": "match_type", "type": "STRING", "mode": "REQUIRED"},
            {"name": "confidence", "type": "FLOAT64", "mode": "REQUIRED"},
            {"name": "matched_at", "type": "TIMESTAMP", "mode": "REQUIRED"}
        ]""",
        deletion_protection=False,
    )
    resources["xref_table"] = xref_table

    # ==================== Cloud Function ====================

    function = cloudfunctionsv2.Function(
        "divebar-lookup-function",
        name="divebar-lookup",
        location=REGION,
        description="Divebar search, KN cross-reference lookup, and download URL API",
        build_config=cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="divebar_lookup",
            source=cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=source_bucket.name,
                    object="divebar-lookup-source.zip",
                ),
            ),
        ),
        service_config=cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory="256M",
            timeout_seconds=120,
            min_instance_count=0,
            max_instance_count=3,  # Allow concurrent requests from KJ Controller
            service_account_email=sa.email,
            environment_variables={
                "GCP_PROJECT_ID": PROJECT_ID,
                # Region the divebar scheduler jobs live in — the `refresh`
                # action builds job paths from it to run them on demand.
                "GCP_REGION": REGION,
                # Secret holding the `refresh` bearer token. Read at runtime
                # (not injected as a secret env var) so the function deploys
                # cleanly before the secret has a version — it just returns 403
                # until one is added. Avoids a deploy-ordering gotcha.
                "DIVEBAR_REFRESH_SECRET_ID": all_secrets["divebar-refresh-token"].secret_id,
            },
        ),
    )
    resources["function"] = function

    # Allow the function SA to read the refresh-token secret at runtime.
    resources["refresh_token_accessor"] = gcp.secretmanager.SecretIamMember(
        "divebar-lookup-refresh-token-accessor",
        secret_id=all_secrets["divebar-refresh-token"].secret_id,
        role="roles/secretmanager.secretAccessor",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== IAM: Allow unauthenticated access ====================
    # The KJ Controller calls this from a LAN device without GCP credentials.
    # The function only reads from BigQuery (no write to external resources).

    resources["public_invoker"] = cloudfunctionsv2.FunctionIamMember(
        "divebar-lookup-public-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member="allUsers",
    )

    resources["public_run_invoker"] = gcp.cloudrunv2.ServiceIamMember(
        "divebar-lookup-public-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member="allUsers",
    )

    # Also allow SA to invoke (for scheduler xref rebuild)
    resources["sa_run_invoker"] = gcp.cloudrunv2.ServiceIamMember(
        "divebar-lookup-sa-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    resources["sa_cf_invoker"] = cloudfunctionsv2.FunctionIamMember(
        "divebar-lookup-sa-cf-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # ==================== Cloud Scheduler: Daily xref rebuild ====================
    # Runs after both mirror (2 AM) and KN sync (4:30 AM) complete

    resources["xref_scheduler"] = cloudscheduler.Job(
        "divebar-xref-rebuild-scheduler",
        name="divebar-xref-rebuild-daily",
        description="Daily rebuild of KN ↔ Divebar cross-reference index",
        region=REGION,
        schedule="0 6 * * *",  # 6:00 AM ET (after mirror at 2AM and KN at 4:30AM)
        time_zone="America/New_York",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=function.url,
            http_method="POST",
            body=pulumi.Output.from_input(
                '{"action": "xref_rebuild"}'
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

    # ==================== IAM: on-demand scheduler-job runner ====================
    # The `refresh` action forces the existing divebar scheduler jobs
    # (mirror index, file-sync VM start, xref rebuild) to run immediately,
    # reusing their own OIDC identities and targets. The function SA therefore
    # only needs permission to *run* those jobs — not to invoke the mirror
    # function or start the VM directly. Least-privilege custom role rather than
    # the broad roles/cloudscheduler.admin.

    scheduler_runner_role = gcp.projects.IAMCustomRole(
        "divebar-scheduler-runner-role",
        role_id="divebarSchedulerRunner",
        title="Divebar Scheduler Job Runner",
        description="Run (force-trigger) Divebar Cloud Scheduler jobs on demand",
        permissions=[
            "cloudscheduler.jobs.run",
            "cloudscheduler.jobs.get",
        ],
    )
    resources["scheduler_runner_role"] = scheduler_runner_role

    resources["scheduler_runner_binding"] = gcp.projects.IAMMember(
        "divebar-lookup-scheduler-runner",
        project=PROJECT_ID,
        role=scheduler_runner_role.id,
        member=sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return resources
