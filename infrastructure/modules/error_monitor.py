"""
Error Monitor Cloud Run Job and Cloud Scheduler resources.

Deploys the error monitor as a Cloud Run Job triggered by Cloud Scheduler
every 15 minutes (error checks) and daily at 08:00 UTC (digest report).
"""

import base64
import json

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import cloudrunv2, cloudscheduler, serviceaccount

from config import PROJECT_ID, REGION, ErrorMonitorConfig

# Empty body for the monitor trigger (Cloud Run Jobs API requires POST body)
_EMPTY_BODY_B64 = base64.b64encode(b"{}").decode()

# Digest trigger body — sets DIGEST_MODE=true via Cloud Run Job override
_DIGEST_BODY_B64 = base64.b64encode(
    json.dumps({
        "overrides": {
            "containerOverrides": [
                {
                    "env": [
                        {"name": "DIGEST_MODE", "value": "true"},
                    ]
                }
            ]
        }
    }).encode()
).decode()


def create_error_monitor(
    backend_image: pulumi.Input[str],
    backend_sa_email: pulumi.Input[str],
    depends_on: list = None,
) -> dict:
    """
    Create the error monitor Cloud Run Job and Cloud Scheduler triggers.

    The error monitor runs as the backend SA (same permissions: Firestore,
    Secrets, Logging). A dedicated scheduler SA is created for Cloud Scheduler
    OAuth authentication.

    Args:
        backend_image: Docker image URL for the backend (Artifact Registry).
        backend_sa_email: Email of the backend service account (runs the job).
        depends_on: Optional list of Pulumi resources to depend on.

    Returns:
        dict with keys: 'job', 'scheduler_sa', 'monitor_scheduler', 'digest_scheduler'.
    """
    job_opts = pulumi.ResourceOptions(depends_on=depends_on) if depends_on else None

    # ---- Service Account for Cloud Scheduler ----
    # Scheduler SA authenticates when invoking the Cloud Run Job via OAuth token.
    scheduler_sa = serviceaccount.Account(
        "error-monitor-scheduler-sa",
        account_id=ErrorMonitorConfig.SCHEDULER_SA_NAME,
        display_name="Error Monitor Scheduler Service Account",
    )

    # ---- Cloud Run Job ----
    error_monitor_job = cloudrunv2.Job(
        "error-monitor-job",
        name=ErrorMonitorConfig.JOB_NAME,
        location=REGION,
        template=cloudrunv2.JobTemplateArgs(
            template=cloudrunv2.JobTemplateTemplateArgs(
                containers=[
                    cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=backend_image,
                        args=["python", "-m", "backend.services.error_monitor.monitor"],
                        resources=cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "cpu": ErrorMonitorConfig.CPU,
                                "memory": ErrorMonitorConfig.MEMORY,
                            },
                        ),
                        envs=[
                            # GCP project for Firestore, Logging, etc.
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_PROJECT_ID",
                                value=PROJECT_ID,
                            ),
                            # Environment tag for log filtering
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            # Discord webhook for alert notifications
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DISCORD_WEBHOOK_URL",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/discord-alert-webhook",
                                        version="latest",
                                    ),
                                ),
                            ),
                        ],
                    )
                ],
                service_account=backend_sa_email,
                timeout=ErrorMonitorConfig.TIMEOUT,
                max_retries=ErrorMonitorConfig.MAX_RETRIES,
            ),
        ),
        opts=job_opts,
    )

    # ---- IAM: grant scheduler SA permission to invoke (run) the job ----
    error_monitor_job_invoker = cloudrunv2.JobIamMember(
        "error-monitor-scheduler-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=error_monitor_job.name,
        role="roles/run.invoker",
        member=scheduler_sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    # Cloud Run Jobs API endpoint — Cloud Scheduler calls this to trigger a run.
    # Format: https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run
    job_run_uri = error_monitor_job.name.apply(
        lambda name: (
            f"https://run.googleapis.com/v2/projects/{PROJECT_ID}"
            f"/locations/{REGION}/jobs/{name}:run"
        )
    )

    # Shared opts: schedulers depend on IAM binding being in place first
    scheduler_opts = pulumi.ResourceOptions(depends_on=[error_monitor_job_invoker])

    # ---- Cloud Scheduler: every-15-min monitor ----
    monitor_scheduler = cloudscheduler.Job(
        "error-monitor-scheduler",
        name="error-monitor-trigger",
        description="Trigger error monitor Cloud Run Job every 15 minutes",
        region=REGION,
        schedule=ErrorMonitorConfig.MONITOR_SCHEDULE,
        time_zone="UTC",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=job_run_uri,
            http_method="POST",
            body=_EMPTY_BODY_B64,
            oauth_token=cloudscheduler.JobHttpTargetOauthTokenArgs(
                service_account_email=scheduler_sa.email,
            ),
        ),
        retry_config=cloudscheduler.JobRetryConfigArgs(
            retry_count=0,
        ),
        opts=scheduler_opts,
    )

    # ---- Cloud Scheduler: daily digest at 08:00 UTC ----
    # Passes DIGEST_MODE=true in the request body as a Cloud Run Job override.
    digest_scheduler = cloudscheduler.Job(
        "error-monitor-digest-scheduler",
        name="error-monitor-daily-digest",
        description="Trigger error monitor Cloud Run Job daily at 08:00 UTC for digest report",
        region=REGION,
        schedule=ErrorMonitorConfig.DIGEST_SCHEDULE,
        time_zone="UTC",
        http_target=cloudscheduler.JobHttpTargetArgs(
            uri=job_run_uri,
            http_method="POST",
            body=_DIGEST_BODY_B64,
            oauth_token=cloudscheduler.JobHttpTargetOauthTokenArgs(
                service_account_email=scheduler_sa.email,
            ),
        ),
        retry_config=cloudscheduler.JobRetryConfigArgs(
            retry_count=0,
        ),
        opts=scheduler_opts,
    )

    return {
        "job": error_monitor_job,
        "scheduler_sa": scheduler_sa,
        "monitor_scheduler": monitor_scheduler,
        "digest_scheduler": digest_scheduler,
    }
