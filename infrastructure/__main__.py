"""
Pulumi Infrastructure for Karaoke Generator Backend.

This is the entry point that wires together all infrastructure modules.
See individual modules for resource definitions.

Structure:
- config.py: Shared configuration and constants
- modules/: Core GCP resources (database, storage, IAM, etc.)
- compute/: VM-based resources (Phase 2)
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import (
    artifactregistry,
    cloudrun,
    cloudrunv2,
    cloudscheduler,
    cloudtasks,
    cloudfunctionsv2,
    compute,
    firestore,
    secretmanager,
    serviceaccount,
    storage,
)

from config import (
    PROJECT_ID,
    get_project_number,
    REGION,
    ZONE,
    GDRIVE_FOLDER_ID,
    NUM_GITHUB_RUNNERS,
    MachineTypes,
    DiskSizes,
)
from modules import database, storage as storage_module, artifact_registry, secrets
from modules import cloud_tasks, cloud_run, monitoring, networking, runner_manager
from modules import divebar_mirror, kn_data_sync, divebar_lookup
from modules import audio_separator_service
from modules import encoding_worker_manager
from modules.iam import backend_sa, github_actions_sa, claude_automation_sa, worker_sas
from compute import encoding_worker_vm, github_runners, divebar_sync_vm

# ==================== Core Infrastructure ====================

# Firestore database and indexes
db_resources = database.create_database()

# Cloud Storage bucket
bucket = storage_module.create_bucket()

# Artifact Registry for Docker images
artifact_repo = artifact_registry.create_repository()

# Secret Manager secrets
all_secrets = secrets.create_secrets()

# Cloud Tasks queues
queues = cloud_tasks.create_queues()

# ==================== Networking ====================
# VPC connector for Cloud Run to access internal resources (e.g., flacfetch VM)
vpc_access_api = networking.enable_vpc_access_api()
vpc_connector = networking.create_vpc_connector(vpc_access_api)

# ==================== Cloud Build IAM ====================
# These bindings allow Cloud Build to access Artifact Registry and deploy as the backend SA

# List of Cloud Build service accounts that need Artifact Registry access
cloudbuild_service_accounts = [
    f"serviceAccount:{get_project_number()}@cloudbuild.gserviceaccount.com",  # Default Cloud Build SA
    f"serviceAccount:service-{get_project_number()}@gcp-sa-cloudbuild.iam.gserviceaccount.com",  # Cloud Build service agent
    f"serviceAccount:{get_project_number()}-compute@developer.gserviceaccount.com",  # Compute service account
]

# Grant Cloud Build service accounts access to Artifact Registry
artifact_registry_iam = artifactregistry.RepositoryIamBinding(
    "cloudbuild-artifact-registry-access",
    repository=artifact_repo.name,
    location=artifact_repo.location,
    role="roles/artifactregistry.writer",
    members=cloudbuild_service_accounts,
)

# Grant Cloud Build compute service account logging permissions
cloudbuild_logging_iam = gcp.projects.IAMMember(
    "cloudbuild-logging-access",
    project=PROJECT_ID,
    role="roles/logging.logWriter",
    member=f"serviceAccount:{get_project_number()}-compute@developer.gserviceaccount.com",
)

# ==================== Service Accounts ====================

# Backend service account (Cloud Run)
backend_service_account = backend_sa.create_backend_service_account()
backend_iam_bindings = backend_sa.grant_backend_permissions(backend_service_account)
cloud_tasks_invoker = backend_sa.grant_cloud_tasks_invoker_permission()

# NOTE: CI uses GitHub Actions for deployments, not Cloud Build.
# If Cloud Build is ever needed, grant roles/iam.serviceAccountUser to
# {project_number}-compute@ on the karaoke-backend service account.

# GitHub Actions service account with Workload Identity Federation
github_actions_service_account = github_actions_sa.create_github_actions_service_account()
wif_pool = github_actions_sa.create_workload_identity_pool()
wif_provider = github_actions_sa.create_workload_identity_provider(wif_pool)
github_actions_iam_bindings = github_actions_sa.grant_github_actions_permissions(
    github_actions_service_account, wif_pool
)

# Claude Code automation service account
claude_automation_service_account = claude_automation_sa.create_claude_automation_service_account()
claude_automation_iam_bindings = claude_automation_sa.grant_claude_automation_permissions(
    claude_automation_service_account
)
claude_impersonation = claude_automation_sa.grant_impersonation_permission(
    claude_automation_service_account
)

# Worker service accounts (VMs)
encoding_worker_sa = worker_sas.create_encoding_worker_service_account()
encoding_worker_iam_bindings = worker_sas.grant_encoding_worker_permissions(
    encoding_worker_sa, bucket
)

gdrive_validator_sa = worker_sas.create_gdrive_validator_service_account()
gdrive_validator_iam_bindings = worker_sas.grant_gdrive_validator_permissions(
    gdrive_validator_sa
)

github_runner_sa = worker_sas.create_github_runner_service_account()
github_runner_iam_bindings = worker_sas.grant_github_runner_permissions(github_runner_sa)

# ==================== Cloud Run ====================

# Domain mapping for api.nomadkaraoke.com
domain_mapping = cloud_run.create_domain_mapping()

# Cloud Run Jobs for batch processing
# These use Cloud Run Jobs instead of Cloud Tasks to avoid instance termination
# during long-running processing (the BackgroundTasks issue)
video_encoding_job = cloud_run.create_video_encoding_job(bucket, backend_service_account)
lyrics_transcription_job = cloud_run.create_lyrics_transcription_job(bucket, backend_service_account)
audio_separation_job = cloud_run.create_audio_separation_job(bucket, backend_service_account)
audio_download_job = cloud_run.create_audio_download_job(bucket, backend_service_account, vpc_connector)

# ==================== Monitoring ====================

# Alert policies
alert_policies = monitoring.create_alert_policies()

# ==================== Cloud Function: GDrive Validator ====================

# Cloud Storage bucket for function source code
function_bucket = storage.Bucket(
    "gdrive-validator-function-source",
    name=f"gdrive-validator-source-{PROJECT_ID}",
    location="US-CENTRAL1",
    force_destroy=True,
    uniform_bucket_level_access=True,
)

# GDrive validator Cloud Function
gdrive_validator_function = cloudfunctionsv2.Function(
    "gdrive-validator-function",
    name="gdrive-validator",
    location=REGION,
    description="Validates Nomad Karaoke Google Drive folder for duplicates and issues",
    build_config=cloudfunctionsv2.FunctionBuildConfigArgs(
        runtime="python312",
        entry_point="validate_gdrive",
        source=cloudfunctionsv2.FunctionBuildConfigSourceArgs(
            storage_source=cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                bucket=function_bucket.name,
                object="gdrive-validator-source.zip",
            ),
        ),
    ),
    service_config=cloudfunctionsv2.FunctionServiceConfigArgs(
        available_memory="256M",
        timeout_seconds=300,
        min_instance_count=0,
        max_instance_count=1,
        service_account_email=gdrive_validator_sa.email,
        environment_variables={
            "GDRIVE_FOLDER_ID": GDRIVE_FOLDER_ID,
            "NOTIFY_ON_SUCCESS": "false",
            "EMAIL_TO": "gen@nomadkaraoke.com",
            "EMAIL_FROM": "gen@nomadkaraoke.com",
        },
        secret_environment_variables=[
            cloudfunctionsv2.FunctionServiceConfigSecretEnvironmentVariableArgs(
                key="PUSHBULLET_API_KEY",
                project_id=PROJECT_ID,
                secret=all_secrets["pushbullet-api-key"].secret_id,
                version="latest",
            ),
            cloudfunctionsv2.FunctionServiceConfigSecretEnvironmentVariableArgs(
                key="SENDGRID_API_KEY",
                project_id=PROJECT_ID,
                secret=all_secrets["sendgrid-api-key"].secret_id,
                version="latest",
            ),
        ],
    ),
)

# Allow Cloud Scheduler to invoke the function
gdrive_validator_invoker = cloudfunctionsv2.FunctionIamMember(
    "gdrive-validator-scheduler-invoker",
    project=PROJECT_ID,
    location=REGION,
    cloud_function=gdrive_validator_function.name,
    role="roles/cloudfunctions.invoker",
    member=f"serviceAccount:service-{get_project_number()}@gcp-sa-cloudscheduler.iam.gserviceaccount.com",
)

# Allow function's service account to invoke itself (for Cloud Scheduler)
gdrive_validator_sa_invoker = cloudfunctionsv2.FunctionIamMember(
    "gdrive-validator-sa-invoker",
    project=PROJECT_ID,
    location=REGION,
    cloud_function=gdrive_validator_function.name,
    role="roles/cloudfunctions.invoker",
    member=gdrive_validator_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Gen2 functions need Cloud Run invoker for the validator's own SA too
# (Cloud Scheduler OIDC token hits Cloud Run, not Cloud Functions IAM)
gdrive_validator_sa_run_invoker = gcp.cloudrunv2.ServiceIamMember(
    "gdrive-validator-sa-run-invoker",
    project=PROJECT_ID,
    location=REGION,
    name=gdrive_validator_function.name,
    role="roles/run.invoker",
    member=gdrive_validator_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Allow backend service account to invoke the validator (post-job trigger)
gdrive_validator_backend_invoker = cloudfunctionsv2.FunctionIamMember(
    "gdrive-validator-backend-invoker",
    project=PROJECT_ID,
    location=REGION,
    cloud_function=gdrive_validator_function.name,
    role="roles/cloudfunctions.invoker",
    member=backend_service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# For Gen2 functions, also need Cloud Run invoker on the underlying service
gdrive_validator_backend_run_invoker = gcp.cloudrunv2.ServiceIamMember(
    "gdrive-validator-backend-run-invoker",
    project=PROJECT_ID,
    location=REGION,
    name=gdrive_validator_function.name,  # Gen2 Cloud Run service matches function name
    role="roles/run.invoker",
    member=backend_service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Cloud Scheduler job to trigger validation daily
gdrive_validator_scheduler = cloudscheduler.Job(
    "gdrive-validator-scheduler",
    name="gdrive-validator-daily",
    description="Daily validation of Nomad Karaoke Google Drive folder",
    region=REGION,
    schedule="0 21 * * *",  # 9:00 PM every day
    time_zone="America/New_York",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri=gdrive_validator_function.url,
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=gdrive_validator_sa.email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=2,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)

# ==================== YouTube Upload Queue Processor ====================

# Cloud Scheduler job to process deferred YouTube uploads hourly
youtube_queue_scheduler = cloudscheduler.Job(
    "youtube-queue-scheduler",
    name="youtube-queue-hourly",
    description="Process deferred YouTube uploads when API quota is available",
    region=REGION,
    schedule="0 * * * *",  # Every hour on the hour
    time_zone="America/Los_Angeles",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri="https://api.nomadkaraoke.com/api/internal/youtube-queue/process",
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=backend_service_account.email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=1,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)

# ==================== Stale Review Processor ====================

# Cloud Scheduler job to send review reminders and expire stale review jobs
stale_review_scheduler = cloudscheduler.Job(
    "stale-review-scheduler",
    name="stale-review-hourly",
    description="Send review reminders and expire stale review jobs",
    region=REGION,
    schedule="0 * * * *",  # Every hour on the hour
    time_zone="America/Los_Angeles",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri="https://api.nomadkaraoke.com/api/internal/process-stale-reviews",
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=backend_service_account.email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=1,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)

# ==================== Disposable Domain Blocklist Sync ====================

# Cloud Scheduler job to sync disposable email domain blocklist daily
disposable_domains_sync_scheduler = cloudscheduler.Job(
    "disposable-domains-sync-scheduler",
    name="disposable-domains-sync-daily",
    description="Daily sync of disposable email domain blocklist from GitHub",
    region=REGION,
    schedule="0 3 * * *",  # 3:00 AM UTC daily
    time_zone="UTC",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri="https://api.nomadkaraoke.com/api/internal/sync-disposable-domains",
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=backend_service_account.email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=2,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)

# ==================== Stuck Job Recovery ====================

# Cloud Scheduler job to detect and recover stuck audio downloads
recover_stuck_downloads_scheduler = cloudscheduler.Job(
    "recover-stuck-downloads-scheduler",
    name="recover-stuck-downloads",
    description="Detect and recover jobs stuck in downloading_audio status",
    region=REGION,
    schedule="*/5 * * * *",  # Every 5 minutes
    time_zone="America/Los_Angeles",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri="https://api.nomadkaraoke.com/api/internal/recover-stuck-jobs",
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=backend_service_account.email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=1,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)

# ==================== Divebar Mirror (Phase 1) ====================
# Index diveBar Karaoke Google Drive files into BigQuery for search

divebar_mirror_resources = divebar_mirror.create_divebar_mirror_resources(all_secrets)

# ==================== KaraokeNerds Data Sync (Phase 2) ====================
# Daily sync of KN catalog and community data to BigQuery/GCS

kn_data_sync_resources = kn_data_sync.create_kn_data_sync_resources(all_secrets)

# ==================== Divebar Lookup API (Phase 3) ====================
# Search/lookup API for KJ Controller + KN cross-reference index

divebar_lookup_resources = divebar_lookup.create_divebar_lookup_resources(all_secrets)

# ==================== Divebar File Sync VM ====================
# Divebar File Sync VM (downloads karaoke files from Drive to GCS)
divebar_sync_instance = divebar_sync_vm.create_divebar_sync_vm(
    divebar_mirror_resources["service_account"]
)

# Cloud Scheduler to start the sync VM daily (uses compute.instances.start API)
divebar_sync_scheduler = cloudscheduler.Job(
    "divebar-sync-vm-scheduler",  # Must match Pulumi state
    name="divebar-sync-vm-daily",
    description="Start Divebar file sync VM daily to download new files from Drive to GCS",
    region=REGION,
    schedule="0 3 * * *",  # 3:00 AM ET (after index refresh at 2 AM)
    time_zone="America/New_York",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri=divebar_sync_instance.self_link.apply(
            lambda link: f"https://compute.googleapis.com/compute/v1/{link}/start"
        ),
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=divebar_mirror_resources["service_account"].email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=1,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)

# Grant the SA permission to start/stop its own VM
divebar_sync_compute_admin = gcp.projects.IAMMember(
    "divebar-mirror-compute-instance-admin",  # Must match Pulumi state
    project=PROJECT_ID,
    role="roles/compute.instanceAdmin.v1",
    member=divebar_mirror_resources["service_account"].email.apply(
        lambda email: f"serviceAccount:{email}"
    ),
)

# ==================== Audio Separator GPU Service ====================
# Cloud Run GPU (L4) service for audio stem separation — replaces Modal
audio_separator_resources = audio_separator_service.create_all_resources()

# ==================== Compute VMs ====================

# Encoding Worker VMs (blue-green pair for zero-downtime deployments)
encoding_worker_ips = encoding_worker_vm.create_encoding_worker_ips()
encoding_worker_instances = encoding_worker_vm.create_encoding_worker_vms(
    encoding_worker_ips, encoding_worker_sa
)
encoding_worker_firewall = encoding_worker_vm.create_encoding_worker_firewall()

# Grant backend SA permission to start encoding worker VMs
backend_compute_perms = worker_sas.grant_backend_compute_permissions(backend_service_account)

# Encoding Worker Idle Shutdown (auto-stop after 15 min idle)
encoding_worker_idle_resources = encoding_worker_manager.create_idle_shutdown_resources()

# GitHub Runners (CI/CD self-hosted runners)
# Create Cloud NAT for outbound internet access (no external IPs needed)
github_runners_router, github_runners_nat = github_runners.create_cloud_nat()

# Create runner VMs (Spot instances for 60-91% cost savings)
github_runner_vms = github_runners.create_github_runners(
    github_runner_sa, all_secrets["github-runner-pat"], github_runners_nat
)

# Create dedicated build runner (on-demand, for Docker deploys)
github_build_runner = github_runners.create_build_runner(
    github_runner_sa, all_secrets["github-runner-pat"], github_runners_nat
)

# Create GPU runners (spot, for audio-separator integration tests)
github_gpu_runner_vms = github_runners.create_gpu_runners(
    github_runner_sa, all_secrets["github-runner-pat"], github_runners_nat
)

# All runner VM names for the runner manager to track
all_runner_names = (
    [vm.name for vm in github_runner_vms]
    + [github_build_runner.name]
    + [vm.name for vm in github_gpu_runner_vms]
)

# GPU runner names (only started when job requests "gpu" label)
gpu_runner_names = [vm.name for vm in github_gpu_runner_vms]

# Create runner manager (auto-start/stop VMs based on CI activity)
runner_manager_resources = runner_manager.create_runner_manager_resources(
    all_secrets["github-webhook-secret"],
    all_secrets["github-runner-pat"],
    all_runner_names,
    gpu_runner_names=gpu_runner_names,
    runner_service_account=github_runner_sa,
)


# ==================== Exports ====================

# Core infrastructure
pulumi.export("project_id", PROJECT_ID)
pulumi.export("bucket_name", bucket.name)
pulumi.export("firestore_database", db_resources["firestore_db"].name)

# Firestore indexes
pulumi.export("firestore_index_jobs_user_email", db_resources["firestore_index_jobs_user_email"].name)
pulumi.export("firestore_index_jobs_status", db_resources["firestore_index_jobs_status"].name)
pulumi.export("firestore_index_jobs_tenant", db_resources["firestore_index_jobs_tenant"].name)
pulumi.export("firestore_index_jobs_user_tenant", db_resources["firestore_index_jobs_user_tenant"].name)
pulumi.export("firestore_index_sessions_active", db_resources["firestore_index_sessions_active"].name)
pulumi.export("firestore_index_magic_links", db_resources["firestore_index_magic_links"].name)
pulumi.export("firestore_index_gen_users_active_created", db_resources["firestore_index_gen_users_active_created"].name)
pulumi.export("firestore_index_gen_users_active_login", db_resources["firestore_index_gen_users_active_login"].name)
pulumi.export("firestore_index_gen_users_active_credits", db_resources["firestore_index_gen_users_active_credits"].name)
pulumi.export("firestore_index_gen_users_active_email", db_resources["firestore_index_gen_users_active_email"].name)
pulumi.export("firestore_field_logs_ttl", db_resources["firestore_field_logs_ttl"].name)
pulumi.export("firestore_index_logs_worker_timestamp", db_resources["firestore_index_logs_worker_timestamp"].name)

# Service accounts
pulumi.export("service_account_email", backend_service_account.email)
pulumi.export("github_actions_service_account", github_actions_service_account.email)
pulumi.export("claude_automation_service_account", claude_automation_service_account.email)

# Artifact Registry
pulumi.export("artifact_repo_url", artifact_registry.get_repo_url(artifact_repo))

# Workload Identity Federation
pulumi.export("workload_identity_provider", wif_provider.name)

# Cloud Tasks queues
pulumi.export("audio_worker_queue", queues["audio-worker-queue"].name)
pulumi.export("lyrics_worker_queue", queues["lyrics-worker-queue"].name)
pulumi.export("screens_worker_queue", queues["screens-worker-queue"].name)
pulumi.export("render_worker_queue", queues["render-worker-queue"].name)
pulumi.export("video_worker_queue", queues["video-worker-queue"].name)

# Cloud Run Jobs
pulumi.export("video_encoding_job", video_encoding_job.name)
pulumi.export("lyrics_transcription_job", lyrics_transcription_job.name)
pulumi.export("audio_separation_job", audio_separation_job.name)
pulumi.export("audio_download_job", audio_download_job.name)
pulumi.export("backend_url", "https://api.nomadkaraoke.com")
pulumi.export("backend_default_url", "https://karaoke-backend-ipzqd2k4yq-uc.a.run.app")

# DNS configuration for Cloudflare
domain_mapping.statuses.apply(
    lambda statuses: pulumi.export("dns_records", {
        "type": "CNAME",
        "name": "api",
        "value": "ghs.googlehosted.com",
        "ttl": 300,
        "proxied": False,
    })
)

# Alert policies
pulumi.export("error_rate_alert_id", alert_policies["error_rate"].name)
pulumi.export("queue_backlog_alert_id", alert_policies["queue_backlog"].name)
pulumi.export("memory_alert_id", alert_policies["memory"].name)
pulumi.export("service_unavailable_alert_id", alert_policies["service_unavailable"].name)

# YouTube upload queue
pulumi.export("youtube_upload_queue", queues["youtube-upload-queue"].name)
pulumi.export("youtube_queue_scheduler_name", youtube_queue_scheduler.name)

# GDrive validator
pulumi.export("gdrive_validator_function_url", gdrive_validator_function.url)
pulumi.export("gdrive_validator_function_name", gdrive_validator_function.name)
pulumi.export("gdrive_validator_scheduler_name", gdrive_validator_scheduler.name)
pulumi.export("recover_stuck_downloads_scheduler_name", recover_stuck_downloads_scheduler.name)
pulumi.export("disposable_domains_sync_scheduler_name", disposable_domains_sync_scheduler.name)
pulumi.export("gdrive_validator_service_account", gdrive_validator_sa.email)

# VPC networking
pulumi.export("vpc_connector_name", vpc_connector.name)
pulumi.export("vpc_connector_self_link", vpc_connector.self_link)

# Encoding workers (blue-green pair)
pulumi.export("encoding_worker_a_ip", encoding_worker_ips[0].address)
pulumi.export("encoding_worker_b_ip", encoding_worker_ips[1].address)
pulumi.export("encoding_worker_a_url", encoding_worker_ips[0].address.apply(lambda ip: f"http://{ip}:8080"))
pulumi.export("encoding_worker_b_url", encoding_worker_ips[1].address.apply(lambda ip: f"http://{ip}:8080"))
pulumi.export("encoding_worker_service_account", encoding_worker_sa.email)
pulumi.export("encoding_worker_a_name", encoding_worker_instances[0].name)
pulumi.export("encoding_worker_b_name", encoding_worker_instances[1].name)

# GitHub runners
pulumi.export("github_runner_vm_names", [vm.name for vm in github_runner_vms])
pulumi.export("github_build_runner_name", github_build_runner.name)
pulumi.export("github_gpu_runner_names", [vm.name for vm in github_gpu_runner_vms])
pulumi.export("github_runner_service_account", github_runner_sa.email)
pulumi.export("github_runners_nat", github_runners_nat.name)

# GitHub runner manager (auto-start/stop)
pulumi.export("runner_manager_function_url", runner_manager_resources["function"].url)
pulumi.export("runner_manager_scheduler_job", runner_manager_resources["scheduler_job"].name)

# Divebar mirror (Phase 1)
pulumi.export("divebar_mirror_function_url", divebar_mirror_resources["function"].url)
pulumi.export("divebar_mirror_scheduler_name", divebar_mirror_resources["scheduler"].name)
pulumi.export("divebar_files_bucket", divebar_mirror_resources["files_bucket"].name)

# KaraokeNerds data sync (Phase 2)
pulumi.export("kn_data_sync_function_url", kn_data_sync_resources["function"].url)
pulumi.export("kn_data_bucket", kn_data_sync_resources["data_bucket"].name)

# Divebar lookup API (Phase 3)
pulumi.export("divebar_lookup_function_url", divebar_lookup_resources["function"].url)

# Divebar file sync VM
pulumi.export("divebar_sync_vm_name", divebar_sync_instance.name)
pulumi.export("divebar_sync_scheduler_name", divebar_sync_scheduler.name)

# Audio separator GPU service
pulumi.export("audio_separator_service_url", audio_separator_resources["service"].uri)
pulumi.export("audio_separator_service_account", audio_separator_resources["service_account"].email)

# Encoding worker idle shutdown
pulumi.export("encoding_worker_idle_function_url", encoding_worker_idle_resources["function"].url)
pulumi.export("encoding_worker_idle_scheduler", encoding_worker_idle_resources["scheduler"].name)
