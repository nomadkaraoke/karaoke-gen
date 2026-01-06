"""
Pulumi infrastructure for Karaoke Generator backend on Google Cloud Platform.

This replaces the manual setup scripts with proper Infrastructure as Code.

Infrastructure includes:
- Firestore database for job state
- Cloud Storage bucket for files
- Artifact Registry for Docker images
- Service accounts and IAM
- GitHub Actions Workload Identity Federation
- Cloud Tasks queues for worker coordination (Phase 1 scalability)
- Secret Manager secrets
- Cloud Run domain mapping
- Cloud Function for Google Drive validation (daily scheduled)
"""
import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import storage, firestore, secretmanager, artifactregistry, cloudrun, serviceaccount, cloudtasks, cloudfunctions, cloudscheduler

# Get the current GCP project
project = gcp.organizations.get_project()
project_id = project.project_id

# Create Firestore Database
firestore_db = firestore.Database(
    "karaoke-firestore",
    project=project_id,
    name="(default)",
    location_id="us-central1",
    type="FIRESTORE_NATIVE",
    concurrency_mode="PESSIMISTIC",
    app_engine_integration_mode="DISABLED",
)

# =============================================================================
# Firestore Composite Indexes
# These are required for queries that filter/sort on multiple fields.
# Created manually via console, now managed here for reproducibility.
# =============================================================================

# Jobs: query by user_email, order by created_at (for user job lists)
firestore_index_jobs_user_email = firestore.Index(
    "firestore-index-jobs-user-email",
    project=project_id,
    database=firestore_db.name,
    collection="jobs",
    fields=[
        firestore.IndexFieldArgs(field_path="user_email", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Jobs: query by status, order by created_at (for admin/system job lists)
firestore_index_jobs_status = firestore.Index(
    "firestore-index-jobs-status",
    project=project_id,
    database=firestore_db.name,
    collection="jobs",
    fields=[
        firestore.IndexFieldArgs(field_path="status", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Sessions: query by user_email + is_active, order by created_at (for active session lists)
firestore_index_sessions_active = firestore.Index(
    "firestore-index-sessions-active",
    project=project_id,
    database=firestore_db.name,
    collection="sessions",
    fields=[
        firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="user_email", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Magic links: query by email, order by created_at (for user magic link history)
firestore_index_magic_links = firestore.Index(
    "firestore-index-magic-links",
    project=project_id,
    database=firestore_db.name,
    collection="magic_links",
    fields=[
        firestore.IndexFieldArgs(field_path="email", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Gen Users: query by is_active, order by created_at (for admin user list)
firestore_index_gen_users_active_created = firestore.Index(
    "firestore-index-gen-users-active-created",
    project=project_id,
    database=firestore_db.name,
    collection="gen_users",
    fields=[
        firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="created_at", order="DESCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Gen Users: query by is_active, order by last_login_at (for admin user list sorting)
firestore_index_gen_users_active_login = firestore.Index(
    "firestore-index-gen-users-active-login",
    project=project_id,
    database=firestore_db.name,
    collection="gen_users",
    fields=[
        firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="last_login_at", order="DESCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Gen Users: query by is_active, order by credits (for admin user list sorting)
firestore_index_gen_users_active_credits = firestore.Index(
    "firestore-index-gen-users-active-credits",
    project=project_id,
    database=firestore_db.name,
    collection="gen_users",
    fields=[
        firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="credits", order="DESCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Gen Users: query by is_active, order by email (for admin user list sorting)
firestore_index_gen_users_active_email = firestore.Index(
    "firestore-index-gen-users-active-email",
    project=project_id,
    database=firestore_db.name,
    collection="gen_users",
    fields=[
        firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="email", order="ASCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# =============================================================================
# Firestore TTL Field Configuration
# TTL (Time-to-Live) policies automatically delete documents after expiration.
# =============================================================================

# Worker logs TTL: logs subcollection documents expire after 30 days
# Collection path for subcollections: "jobs/{job_id}/logs"
# When using subcollection groups, use just the subcollection name "logs"
firestore_field_logs_ttl = firestore.Field(
    "firestore-field-logs-ttl",
    project=project_id,
    database=firestore_db.name,
    collection="logs",  # Subcollection name (applies to all jobs/{job_id}/logs)
    field="ttl_expiry",
    ttl_config={},  # Empty block enables TTL based on ttl_expiry field
    index_config={},  # Disable indexing on TTL field
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# =============================================================================
# Firestore Composite Indexes for Logs Subcollection
# =============================================================================

# Logs: query by worker, order by timestamp (for filtering logs by worker type)
firestore_index_logs_worker_timestamp = firestore.Index(
    "firestore-index-logs-worker-timestamp",
    project=project_id,
    database=firestore_db.name,
    collection="logs",  # Subcollection name
    fields=[
        firestore.IndexFieldArgs(field_path="worker", order="ASCENDING"),
        firestore.IndexFieldArgs(field_path="timestamp", order="ASCENDING"),
    ],
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)

# Create Cloud Storage Bucket
bucket = storage.Bucket(
    "karaoke-storage",
    name=f"karaoke-gen-storage-{project_id}",
    location="US-CENTRAL1",
    force_destroy=False,  # Prevent accidental deletion
    uniform_bucket_level_access=True,
    lifecycle_rules=[
        storage.BucketLifecycleRuleArgs(
            action=storage.BucketLifecycleRuleActionArgs(type="Delete"),
            condition=storage.BucketLifecycleRuleConditionArgs(
                age=7,
                matches_prefixes=["temp/", "uploads/"]
            ),
        ),
    ],
)

# Create Artifact Registry Repository for Docker images
artifact_repo = artifactregistry.Repository(
    "karaoke-artifact-repo",
    repository_id="karaoke-repo",
    location="us-central1",
    format="DOCKER",
    description="Docker repository for karaoke backend images",
)

# Create Service Account for Cloud Run
service_account = serviceaccount.Account(
    "karaoke-backend-sa",
    account_id="karaoke-backend",
    display_name="Karaoke Backend Service Account",
)

# Grant Firestore permissions
firestore_iam = gcp.projects.IAMMember(
    "karaoke-backend-firestore-access",
    project=project_id,
    role="roles/datastore.user",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Storage permissions
storage_iam = gcp.projects.IAMMember(
    "karaoke-backend-storage-access",
    project=project_id,
    role="roles/storage.objectAdmin",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Secret Manager read permissions
secrets_read_iam = gcp.projects.IAMMember(
    "karaoke-backend-secrets-access",
    project=project_id,
    role="roles/secretmanager.secretAccessor",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Secret Manager write permissions (for OAuth token refresh/device auth)
secrets_write_iam = gcp.projects.IAMMember(
    "karaoke-backend-secrets-write",
    project=project_id,
    role="roles/secretmanager.secretVersionManager",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Cloud Trace write permissions (for OpenTelemetry tracing)
cloud_trace_iam = gcp.projects.IAMMember(
    "karaoke-backend-cloudtrace-agent",
    project=project_id,
    role="roles/cloudtrace.agent",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Vertex AI User permissions (for Gemini agentic AI correction)
vertex_ai_iam = gcp.projects.IAMMember(
    "karaoke-backend-vertexai-user",
    project=project_id,
    role="roles/aiplatform.user",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Cloud Build service accounts access to Artifact Registry
# Cloud Build uses multiple service accounts depending on the context
cloudbuild_service_accounts = [
    f"serviceAccount:{project.number}@cloudbuild.gserviceaccount.com",  # Default Cloud Build SA
    f"serviceAccount:service-{project.number}@gcp-sa-cloudbuild.iam.gserviceaccount.com",  # Cloud Build service agent
    f"serviceAccount:{project.number}-compute@developer.gserviceaccount.com",  # Compute service account
]

artifact_registry_iam = artifactregistry.RepositoryIamBinding(
    "cloudbuild-artifact-registry-access",
    repository=artifact_repo.name,
    location=artifact_repo.location,
    role="roles/artifactregistry.writer",
    members=cloudbuild_service_accounts,
)

# Grant Cloud Build compute service account logging permissions
# This fixes the "does not have permission to write logs" warning
cloudbuild_logging_iam = gcp.projects.IAMMember(
    "cloudbuild-logging-access",
    project=project_id,
    role="roles/logging.logWriter",
    member=f"serviceAccount:{project.number}-compute@developer.gserviceaccount.com",
)

# Grant Cloud Build compute service account permission to deploy as karaoke-backend SA
# This is required for Cloud Build to deploy Cloud Run services that use the backend service account
# Without this, gcloud run deploy fails with: PERMISSION_DENIED: Permission 'iam.serviceaccounts.actAs' denied
cloudbuild_deploy_as_backend = gcp.serviceaccount.IAMMember(
    "cloudbuild-deploy-as-backend",
    service_account_id=service_account.name,
    role="roles/iam.serviceAccountUser",
    member=f"serviceAccount:{project.number}-compute@developer.gserviceaccount.com",
)

# ==================== GitHub Actions Deployer Service Account ====================
# This service account is used by GitHub Actions CI/CD via Workload Identity Federation

github_actions_sa = serviceaccount.Account(
    "github-actions-deployer-sa",
    account_id="github-actions-deployer",
    display_name="GitHub Actions Deployer",
    description="Service account for GitHub Actions CD via Workload Identity",
)

# Grant Cloud Run admin permissions
github_actions_run_admin = gcp.projects.IAMMember(
    "github-actions-run-admin",
    project=project_id,
    role="roles/run.admin",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant service account user permissions (to deploy Cloud Run with other SA)
github_actions_sa_user = gcp.projects.IAMMember(
    "github-actions-sa-user",
    project=project_id,
    role="roles/iam.serviceAccountUser",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Artifact Registry writer permissions
github_actions_artifact_writer = gcp.projects.IAMMember(
    "github-actions-artifact-writer",
    project=project_id,
    role="roles/artifactregistry.writer",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Cloud Build permissions - REQUIRED for gcloud builds submit
github_actions_cloudbuild = gcp.projects.IAMMember(
    "github-actions-cloudbuild",
    project=project_id,
    role="roles/cloudbuild.builds.editor",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Storage Admin for Cloud Build bucket uploads
github_actions_storage = gcp.projects.IAMMember(
    "github-actions-storage-admin",
    project=project_id,
    role="roles/storage.admin",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Logging write permissions for Cloud Build logs
github_actions_logging = gcp.projects.IAMMember(
    "github-actions-logging",
    project=project_id,
    role="roles/logging.logWriter",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Cloud Build logs viewer - needed for `gcloud builds log` to read build output
# The cloudbuild-logs bucket is Google-managed, so we use project-level logging.viewer
# which allows reading logs from Cloud Logging where build logs are also stored
github_actions_logging_viewer = gcp.projects.IAMMember(
    "github-actions-logging-viewer",
    project=project_id,
    role="roles/logging.viewer",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Service Usage Consumer - required for serviceusage.services.use permission
github_actions_service_usage = gcp.projects.IAMMember(
    "github-actions-service-usage",
    project=project_id,
    role="roles/serviceusage.serviceUsageConsumer",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Project IAM Admin - required for Pulumi to refresh IAM binding state
github_actions_iam_admin = gcp.projects.IAMMember(
    "github-actions-iam-admin",
    project=project_id,
    role="roles/resourcemanager.projectIamAdmin",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Secret Manager Admin - required for Pulumi to refresh secret state
github_actions_secretmanager = gcp.projects.IAMMember(
    "github-actions-secretmanager",
    project=project_id,
    role="roles/secretmanager.admin",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# ==================== Workload Identity Federation ====================
# Allows GitHub Actions to authenticate without service account keys

workload_identity_pool = gcp.iam.WorkloadIdentityPool(
    "github-actions-pool",
    workload_identity_pool_id="github-actions-pool",
    display_name="GitHub Actions Pool",
    description="Workload Identity Pool for GitHub Actions",
    disabled=False,
)

workload_identity_provider = gcp.iam.WorkloadIdentityPoolProvider(
    "github-actions-provider",
    workload_identity_pool_id=workload_identity_pool.workload_identity_pool_id,
    workload_identity_pool_provider_id="github-actions-provider",
    display_name="GitHub Actions Provider",
    attribute_mapping={
        "google.subject": "assertion.sub",
        "attribute.actor": "assertion.actor",
        "attribute.repository": "assertion.repository",
        "attribute.repository_owner": "assertion.repository_owner",
    },
    attribute_condition="assertion.repository_owner == 'nomadkaraoke'",
    oidc=gcp.iam.WorkloadIdentityPoolProviderOidcArgs(
        issuer_uri="https://token.actions.githubusercontent.com",
    ),
)

# Allow GitHub Actions to impersonate the service account
# All nomadkaraoke repos that deploy to GCP need access
github_actions_wif_binding = gcp.serviceaccount.IAMBinding(
    "github-actions-wif-binding",
    service_account_id=github_actions_sa.name,
    role="roles/iam.workloadIdentityUser",
    members=[
        workload_identity_pool.name.apply(
            lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/nomadkaraoke/karaoke-gen"
        ),
        workload_identity_pool.name.apply(
            lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/nomadkaraoke/flacfetch"
        ),
        workload_identity_pool.name.apply(
            lambda pool_name: f"principalSet://iam.googleapis.com/{pool_name}/attribute.repository/nomadkaraoke/karaoke-decide"
        ),
    ],
)

# Grant Compute Engine permissions for flacfetch VM deployment
# Needed for gcloud compute ssh to update the VM
github_actions_compute_admin = gcp.projects.IAMMember(
    "github-actions-compute-admin",
    project=project_id,
    role="roles/compute.instanceAdmin.v1",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant OS Login permissions for SSH access
github_actions_os_login = gcp.projects.IAMMember(
    "github-actions-os-login",
    project=project_id,
    role="roles/compute.osLogin",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant IAP tunnel user for secure SSH (used by gcloud compute ssh)
github_actions_iap_tunnel = gcp.projects.IAMMember(
    "github-actions-iap-tunnel",
    project=project_id,
    role="roles/iap.tunnelResourceAccessor",
    member=github_actions_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# ==================== Claude Code Automation Service Account ====================
# This service account provides long-lived read-only access for Claude Code CLI
# to monitor deployments, view logs, and check build status without requiring
# frequent OAuth re-authentication.

claude_automation_sa = serviceaccount.Account(
    "claude-automation-sa",
    account_id="claude-automation",
    display_name="Claude Code Automation",
    description="Service account for Claude Code CLI with read-only GCP access",
)

# Grant Cloud Run viewer permissions (view services, revisions, status)
claude_run_viewer = gcp.projects.IAMMember(
    "claude-automation-run-viewer",
    project=project_id,
    role="roles/run.viewer",
    member=claude_automation_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Logging viewer permissions (read logs)
claude_logging_viewer = gcp.projects.IAMMember(
    "claude-automation-logging-viewer",
    project=project_id,
    role="roles/logging.viewer",
    member=claude_automation_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Monitoring viewer permissions (read metrics, dashboards)
claude_monitoring_viewer = gcp.projects.IAMMember(
    "claude-automation-monitoring-viewer",
    project=project_id,
    role="roles/monitoring.viewer",
    member=claude_automation_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Cloud Build viewer permissions (view build status, logs)
claude_cloudbuild_viewer = gcp.projects.IAMMember(
    "claude-automation-cloudbuild-viewer",
    project=project_id,
    role="roles/cloudbuild.builds.viewer",
    member=claude_automation_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Cloud Trace viewer permissions (view distributed traces)
claude_cloudtrace_viewer = gcp.projects.IAMMember(
    "claude-automation-cloudtrace-viewer",
    project=project_id,
    role="roles/cloudtrace.user",
    member=claude_automation_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Cloud Tasks viewer permissions (view queue status)
claude_cloudtasks_viewer = gcp.projects.IAMMember(
    "claude-automation-cloudtasks-viewer",
    project=project_id,
    role="roles/cloudtasks.viewer",
    member=claude_automation_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant Storage viewer permissions (list buckets, view metadata - not object content)
claude_storage_viewer = gcp.projects.IAMMember(
    "claude-automation-storage-viewer",
    project=project_id,
    role="roles/storage.objectViewer",
    member=claude_automation_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Allow users to impersonate this service account
# This enables using `gcloud auth application-default login --impersonate-service-account`
# for long-lived credentials without downloadable keys
claude_automation_impersonation = gcp.serviceaccount.IAMBinding(
    "claude-automation-impersonation",
    service_account_id=claude_automation_sa.name,
    role="roles/iam.serviceAccountTokenCreator",
    members=[
        "user:andrew@beveridge.uk",
        "user:admin@nomadkaraoke.com",
    ],
)

# ==================== Cloud Tasks Queues (Phase 1 Scalability) ====================
# These queues provide guaranteed delivery and horizontal scaling for worker tasks.
# Workers are triggered via Cloud Tasks instead of BackgroundTasks, ensuring:
# - Tasks survive container restarts
# - Each task gets dedicated resources
# - Automatic retries on failure
# - Rate limiting to protect external APIs

# Audio worker queue - calls Modal API for audio separation
audio_worker_queue = cloudtasks.Queue(
    "audio-worker-queue",
    name="audio-worker-queue",
    location="us-central1",
    rate_limits=cloudtasks.QueueRateLimitsArgs(
        max_dispatches_per_second=10,   # Protect Modal API
        max_concurrent_dispatches=50,    # Max parallel audio workers
    ),
    retry_config=cloudtasks.QueueRetryConfigArgs(
        max_attempts=3,
        min_backoff="10s",
        max_backoff="300s",
        max_retry_duration="1800s",     # 30 min total retry window
    ),
)

# Lyrics worker queue - calls AudioShake API for transcription
lyrics_worker_queue = cloudtasks.Queue(
    "lyrics-worker-queue",
    name="lyrics-worker-queue",
    location="us-central1",
    rate_limits=cloudtasks.QueueRateLimitsArgs(
        max_dispatches_per_second=10,
        max_concurrent_dispatches=50,
    ),
    retry_config=cloudtasks.QueueRetryConfigArgs(
        max_attempts=3,
        min_backoff="10s",
        max_backoff="300s",
        max_retry_duration="1200s",     # 20 min total retry window
    ),
)

# Screens worker queue - generates title/end screens (fast, CPU-light)
screens_worker_queue = cloudtasks.Queue(
    "screens-worker-queue",
    name="screens-worker-queue",
    location="us-central1",
    rate_limits=cloudtasks.QueueRateLimitsArgs(
        max_dispatches_per_second=50,   # Fast operations
        max_concurrent_dispatches=100,
    ),
    retry_config=cloudtasks.QueueRetryConfigArgs(
        max_attempts=3,
        min_backoff="5s",
        max_backoff="60s",
        max_retry_duration="300s",      # 5 min total retry window
    ),
)

# Render worker queue - LyricsTranscriber + FFmpeg (CPU-intensive)
render_worker_queue = cloudtasks.Queue(
    "render-worker-queue",
    name="render-worker-queue",
    location="us-central1",
    rate_limits=cloudtasks.QueueRateLimitsArgs(
        max_dispatches_per_second=5,    # CPU-intensive
        max_concurrent_dispatches=20,
    ),
    retry_config=cloudtasks.QueueRetryConfigArgs(
        max_attempts=2,
        min_backoff="30s",
        max_backoff="300s",
        max_retry_duration="3600s",     # 60 min for render
    ),
)

# Video worker queue - final encoding (very CPU-intensive, longest running)
# Note: For tasks >30 min, consider Cloud Run Jobs instead
video_worker_queue = cloudtasks.Queue(
    "video-worker-queue",
    name="video-worker-queue",
    location="us-central1",
    rate_limits=cloudtasks.QueueRateLimitsArgs(
        max_dispatches_per_second=3,    # Very CPU-intensive
        max_concurrent_dispatches=10,
    ),
    retry_config=cloudtasks.QueueRetryConfigArgs(
        max_attempts=2,
        min_backoff="60s",
        max_backoff="600s",
        max_retry_duration="7200s",     # 2 hour total (video is long)
    ),
)

# Grant Cloud Tasks permission to invoke Cloud Run service
# Cloud Tasks service agent needs to authenticate to Cloud Run
cloud_tasks_invoker = gcp.projects.IAMMember(
    "cloud-tasks-invoker",
    project=project_id,
    role="roles/run.invoker",
    member=f"serviceAccount:service-{project.number}@gcp-sa-cloudtasks.iam.gserviceaccount.com",
)

# Grant backend service account permission to enqueue Cloud Tasks
cloud_tasks_enqueuer = gcp.projects.IAMMember(
    "karaoke-backend-cloudtasks-enqueuer",
    project=project_id,
    role="roles/cloudtasks.enqueuer",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant backend service account permission to "act as" itself for Cloud Tasks OIDC authentication
# This is required when creating Cloud Tasks with oidc_token that specifies this service account
backend_sa_act_as_self = gcp.serviceaccount.IAMBinding(
    "karaoke-backend-act-as-self",
    service_account_id=service_account.name,
    role="roles/iam.serviceAccountUser",
    members=[service_account.email.apply(lambda email: f"serviceAccount:{email}")],
)

# ==================== Cloud Run Job for Video Encoding (Phase 2 Scalability) ====================
# Cloud Run Jobs provide up to 24-hour execution time for long-running video encoding.
# This is used when video encoding might exceed the 30-minute Cloud Tasks timeout.
# Triggered programmatically via WorkerService when ENABLE_CLOUD_TASKS=true and
# USE_CLOUD_RUN_JOBS_FOR_VIDEO=true

video_encoding_job = gcp.cloudrunv2.Job(
    "video-encoding-job",
    name="video-encoding-job",
    location="us-central1",
    template=gcp.cloudrunv2.JobTemplateArgs(
        template=gcp.cloudrunv2.JobTemplateTemplateArgs(
            # Use the same container as the backend service
            # The job runs with --job-id argument to process a specific job
            containers=[gcp.cloudrunv2.JobTemplateTemplateContainerArgs(
                image=f"us-central1-docker.pkg.dev/{project_id}/karaoke-repo/karaoke-backend:latest",
                args=["python", "-m", "backend.workers.video_worker"],
                resources=gcp.cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                    limits={
                        "cpu": "4",      # 4 vCPUs for encoding
                        "memory": "8Gi",  # 8GB RAM
                    },
                ),
                envs=[
                    gcp.cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                        name="GOOGLE_CLOUD_PROJECT",
                        value=project_id,
                    ),
                    gcp.cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                        name="GCS_BUCKET_NAME",
                        value=bucket.name,
                    ),
                ],
            )],
            service_account=service_account.email,
            timeout="3600s",  # 1 hour max per task
            max_retries=2,
        ),
    ),
)

# Grant backend service account permission to run Cloud Run Jobs
cloud_run_jobs_developer = gcp.projects.IAMMember(
    "karaoke-backend-run-jobs-developer",
    project=project_id,
    role="roles/run.developer",
    member=service_account.email.apply(lambda email: f"serviceAccount:{email}"),
)

# ==================== Secrets ====================
# Create secrets (you'll need to add the actual secret values manually or via pulumi config)
config = pulumi.Config()

# AudioShake API Key Secret
audioshake_secret = secretmanager.Secret(
    "audioshake-api-key",
    secret_id="audioshake-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Genius API Key Secret
genius_secret = secretmanager.Secret(
    "genius-api-key",
    secret_id="genius-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Audio Separator API URL Secret
audio_separator_secret = secretmanager.Secret(
    "audio-separator-api-url",
    secret_id="audio-separator-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# RED API Key Secret (for flacfetch audio search)
red_api_key_secret = secretmanager.Secret(
    "red-api-key",
    secret_id="red-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# RED API URL Secret (for flacfetch audio search)
red_api_url_secret = secretmanager.Secret(
    "red-api-url",
    secret_id="red-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# OPS API Key Secret (for flacfetch audio search)
ops_api_key_secret = secretmanager.Secret(
    "ops-api-key",
    secret_id="ops-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# OPS API URL Secret (for flacfetch audio search)
ops_api_url_secret = secretmanager.Secret(
    "ops-api-url",
    secret_id="ops-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# ==================== Payment & Email Secrets ====================
# Stripe API Secret Key (for checkout sessions and webhooks)
stripe_secret_key = secretmanager.Secret(
    "stripe-secret-key",
    secret_id="stripe-secret-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Stripe Webhook Secret (for verifying webhook signatures)
stripe_webhook_secret = secretmanager.Secret(
    "stripe-webhook-secret",
    secret_id="stripe-webhook-secret",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# SendGrid API Key (for transactional emails: magic links, confirmations)
sendgrid_api_key = secretmanager.Secret(
    "sendgrid-api-key",
    secret_id="sendgrid-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# ==================== Langfuse Observability Secrets ====================
# Langfuse provides LLM observability for the agentic AI correction workflow.
# These secrets enable tracing of all Vertex AI/LLM calls for debugging and monitoring.

# Langfuse Public Key (for client identification)
langfuse_public_key = secretmanager.Secret(
    "langfuse-public-key",
    secret_id="langfuse-public-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Langfuse Secret Key (for authentication)
langfuse_secret_key = secretmanager.Secret(
    "langfuse-secret-key",
    secret_id="langfuse-secret-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Langfuse Host (API endpoint - US region)
langfuse_host = secretmanager.Secret(
    "langfuse-host",
    secret_id="langfuse-host",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Create Cloud Run Domain Mapping
# Maps api.nomadkaraoke.com to the karaoke-backend service
domain_mapping = cloudrun.DomainMapping(
    "karaoke-backend-domain",
    location="us-central1",
    name="api.nomadkaraoke.com",
    metadata=cloudrun.DomainMappingMetadataArgs(
        namespace=project_id,
    ),
    spec=cloudrun.DomainMappingSpecArgs(
        route_name="karaoke-backend",
    ),
)

# Export important values
pulumi.export("project_id", project_id)
pulumi.export("bucket_name", bucket.name)
pulumi.export("firestore_database", firestore_db.name)
pulumi.export("firestore_index_jobs_user_email", firestore_index_jobs_user_email.name)
pulumi.export("firestore_index_jobs_status", firestore_index_jobs_status.name)
pulumi.export("firestore_index_sessions_active", firestore_index_sessions_active.name)
pulumi.export("firestore_index_magic_links", firestore_index_magic_links.name)
pulumi.export("firestore_index_gen_users_active_created", firestore_index_gen_users_active_created.name)
pulumi.export("firestore_index_gen_users_active_login", firestore_index_gen_users_active_login.name)
pulumi.export("firestore_index_gen_users_active_credits", firestore_index_gen_users_active_credits.name)
pulumi.export("firestore_index_gen_users_active_email", firestore_index_gen_users_active_email.name)
pulumi.export("firestore_field_logs_ttl", firestore_field_logs_ttl.name)
pulumi.export("firestore_index_logs_worker_timestamp", firestore_index_logs_worker_timestamp.name)
pulumi.export("service_account_email", service_account.email)
pulumi.export("artifact_repo_url", artifact_repo.name.apply(
    lambda name: f"us-central1-docker.pkg.dev/{project_id}/karaoke-repo"
))
pulumi.export("backend_url", "https://api.nomadkaraoke.com")
pulumi.export("backend_default_url", "https://karaoke-backend-ipzqd2k4yq-uc.a.run.app")

# GitHub Actions CD exports
pulumi.export("github_actions_service_account", github_actions_sa.email)
pulumi.export("workload_identity_provider", workload_identity_provider.name)

# Claude Code automation exports
pulumi.export("claude_automation_service_account", claude_automation_sa.email)

# Cloud Tasks queue exports (Phase 1 scalability)
pulumi.export("audio_worker_queue", audio_worker_queue.name)
pulumi.export("lyrics_worker_queue", lyrics_worker_queue.name)
pulumi.export("screens_worker_queue", screens_worker_queue.name)
pulumi.export("render_worker_queue", render_worker_queue.name)
pulumi.export("video_worker_queue", video_worker_queue.name)

# Cloud Run Job export (Phase 2 scalability)
pulumi.export("video_encoding_job", video_encoding_job.name)

# Export DNS configuration needed for Cloudflare
# These are the records to add to your Cloudflare DNS
domain_mapping.statuses.apply(
    lambda statuses: pulumi.export("dns_records", {
        "type": "CNAME",
        "name": "api",
        "value": "ghs.googlehosted.com",
        "ttl": 300,
        "proxied": False  # Must be false for Cloud Run domain verification
    })
)

# ==================== Flacfetch Service (Torrent Downloads) ====================
# A dedicated VM running flacfetch with Transmission daemon for BitTorrent downloads.
# This is required because Cloud Run doesn't support inbound peer connections for torrents.
# The VM has a static IP for tracker whitelist and runs indefinitely for seeding.

# Static IP for flacfetch (for tracker account whitelist)
flacfetch_ip = gcp.compute.Address(
    "flacfetch-ip",
    name="flacfetch-static-ip",
    region="us-central1",
    address_type="EXTERNAL",
    description="Static IP for flacfetch service (torrent downloads)",
)

# Service account for flacfetch VM
flacfetch_sa = serviceaccount.Account(
    "flacfetch-sa",
    account_id="flacfetch-service",
    display_name="Flacfetch Service Account",
    description="Service account for flacfetch torrent/audio download VM",
)

# Grant flacfetch service account GCS write permissions
flacfetch_storage_iam = gcp.storage.BucketIAMMember(
    "flacfetch-storage-writer",
    bucket=bucket.name,
    role="roles/storage.objectCreator",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant flacfetch service account GCS read permissions (for uploads folder)
flacfetch_storage_reader_iam = gcp.storage.BucketIAMMember(
    "flacfetch-storage-reader",
    bucket=bucket.name,
    role="roles/storage.objectViewer",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant flacfetch service account Secret Manager access
flacfetch_secrets_iam = gcp.projects.IAMMember(
    "flacfetch-secrets-access",
    project=project_id,
    role="roles/secretmanager.secretAccessor",
    member=flacfetch_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Flacfetch API key secret (for authenticating API requests)
flacfetch_api_key_secret = secretmanager.Secret(
    "flacfetch-api-key",
    secret_id="flacfetch-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Flacfetch API URL secret (the static IP address of the flacfetch VM)
# This is stored as a secret to avoid committing infrastructure IPs to the repo
flacfetch_api_url_secret = secretmanager.Secret(
    "flacfetch-api-url",
    secret_id="flacfetch-api-url",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Startup script for the flacfetch VM
# This installs and configures Transmission daemon and the flacfetch service
FLACFETCH_STARTUP_SCRIPT = """#!/bin/bash
set -e

# Log to a file for debugging
exec > >(tee /var/log/flacfetch-startup.log) 2>&1
echo "Starting flacfetch setup at $(date)"

# Install dependencies
apt-get update
apt-get install -y python3-pip python3-venv transmission-daemon ffmpeg git curl

# Configure Transmission
echo "Configuring Transmission daemon..."
systemctl stop transmission-daemon || true

# Create transmission directories
mkdir -p /var/lib/transmission-daemon/downloads
mkdir -p /var/lib/transmission-daemon/.config/transmission-daemon
chown -R debian-transmission:debian-transmission /var/lib/transmission-daemon

# Configure transmission settings
cat > /var/lib/transmission-daemon/.config/transmission-daemon/settings.json << 'SETTINGS'
{
    "download-dir": "/var/lib/transmission-daemon/downloads",
    "incomplete-dir": "/var/lib/transmission-daemon/.incomplete",
    "incomplete-dir-enabled": true,
    "rpc-authentication-required": false,
    "rpc-bind-address": "127.0.0.1",
    "rpc-enabled": true,
    "rpc-port": 9091,
    "rpc-whitelist-enabled": false,
    "peer-port": 51413,
    "port-forwarding-enabled": false,
    "speed-limit-down": 0,
    "speed-limit-down-enabled": false,
    "speed-limit-up": 0,
    "speed-limit-up-enabled": false,
    "ratio-limit-enabled": false,
    "umask": 2,
    "encryption": 1
}
SETTINGS

chown debian-transmission:debian-transmission /var/lib/transmission-daemon/.config/transmission-daemon/settings.json
systemctl start transmission-daemon
echo "Transmission daemon started"

# Install flacfetch
echo "Installing flacfetch..."
cd /opt

# Clone flacfetch (we'll use the repo from GitHub)
if [ ! -d "flacfetch" ]; then
    git clone https://github.com/nomadkaraoke/flacfetch.git
fi
cd flacfetch

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install flacfetch with API dependencies
pip install -e ".[api]"

# Get secrets from Secret Manager
echo "Fetching secrets from Secret Manager..."
FLACFETCH_API_KEY=$(gcloud secrets versions access latest --secret=flacfetch-api-key 2>/dev/null || echo "")
RED_API_KEY=$(gcloud secrets versions access latest --secret=red-api-key 2>/dev/null || echo "")
RED_API_URL=$(gcloud secrets versions access latest --secret=red-api-url 2>/dev/null || echo "")
OPS_API_KEY=$(gcloud secrets versions access latest --secret=ops-api-key 2>/dev/null || echo "")
OPS_API_URL=$(gcloud secrets versions access latest --secret=ops-api-url 2>/dev/null || echo "")

# Get bucket name from project metadata
GCS_BUCKET=$(gcloud compute project-info describe --format='value(commonInstanceMetadata.items.gcs-bucket)' 2>/dev/null || echo "")
if [ -z "$GCS_BUCKET" ]; then
    # Fallback: construct from project ID
    PROJECT_ID=$(curl -s "http://metadata.google.internal/computeMetadata/v1/project/project-id" -H "Metadata-Flavor: Google")
    GCS_BUCKET="karaoke-gen-storage-${PROJECT_ID}"
fi

echo "Using GCS bucket: $GCS_BUCKET"

# Create systemd service for flacfetch
echo "Creating systemd service..."
cat > /etc/systemd/system/flacfetch.service << SYSTEMD
[Unit]
Description=Flacfetch HTTP API Service
After=network.target transmission-daemon.service
Requires=transmission-daemon.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/flacfetch
Environment="FLACFETCH_API_KEY=${FLACFETCH_API_KEY}"
Environment="RED_API_KEY=${RED_API_KEY}"
Environment="RED_API_URL=${RED_API_URL}"
Environment="OPS_API_KEY=${OPS_API_KEY}"
Environment="OPS_API_URL=${OPS_API_URL}"
Environment="GCS_BUCKET=${GCS_BUCKET}"
Environment="FLACFETCH_KEEP_SEEDING=true"
Environment="FLACFETCH_MIN_FREE_GB=5"
Environment="FLACFETCH_DOWNLOAD_DIR=/var/lib/transmission-daemon/downloads"
Environment="TRANSMISSION_HOST=localhost"
Environment="TRANSMISSION_PORT=9091"
ExecStart=/opt/flacfetch/venv/bin/flacfetch serve --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable flacfetch
systemctl start flacfetch

echo "Flacfetch service started at $(date)"
echo "Setup complete!"
"""

# Flacfetch VM instance
flacfetch_vm = gcp.compute.Instance(
    "flacfetch-service",
    name="flacfetch-service",
    machine_type="e2-small",  # 0.5 vCPU, 2GB RAM - sufficient for I/O-bound workload
    zone="us-central1-a",
    boot_disk=gcp.compute.InstanceBootDiskArgs(
        initialize_params=gcp.compute.InstanceBootDiskInitializeParamsArgs(
            image="debian-cloud/debian-12",
            size=30,  # GB - for torrent storage + headroom
            type="pd-ssd",  # SSD for faster I/O
        ),
    ),
    network_interfaces=[gcp.compute.InstanceNetworkInterfaceArgs(
        network="default",
        access_configs=[gcp.compute.InstanceNetworkInterfaceAccessConfigArgs(
            nat_ip=flacfetch_ip.address,  # Static IP
        )],
    )],
    service_account=gcp.compute.InstanceServiceAccountArgs(
        email=flacfetch_sa.email,
        scopes=["cloud-platform"],
    ),
    metadata_startup_script=FLACFETCH_STARTUP_SCRIPT,
    metadata={
        "gcs-bucket": bucket.name,  # Pass bucket name to startup script
    },
    tags=["flacfetch-service"],
    allow_stopping_for_update=True,
)

# Firewall rule for flacfetch
# - Port 51413 TCP/UDP: BitTorrent peer connections
# - Port 8080 TCP: Flacfetch HTTP API (auth required at app level)
flacfetch_firewall = gcp.compute.Firewall(
    "flacfetch-firewall",
    name="flacfetch-firewall",
    network="default",
    allows=[
        # BitTorrent peer connections
        gcp.compute.FirewallAllowArgs(protocol="tcp", ports=["51413"]),
        gcp.compute.FirewallAllowArgs(protocol="udp", ports=["51413"]),
        # Flacfetch HTTP API (auth required at app level)
        gcp.compute.FirewallAllowArgs(protocol="tcp", ports=["8080"]),
    ],
    source_ranges=["0.0.0.0/0"],  # Permissive, auth enforced at app level
    target_tags=["flacfetch-service"],
    description="Firewall rules for flacfetch torrent/API service",
)

# Export flacfetch values
pulumi.export("flacfetch_static_ip", flacfetch_ip.address)
pulumi.export("flacfetch_service_url", flacfetch_ip.address.apply(
    lambda ip: f"http://{ip}:8080"
))
pulumi.export("flacfetch_service_account", flacfetch_sa.email)

# ==================== High-Performance Encoding Worker ====================
# A dedicated C4-standard-96 VM with Intel Granite Rapids CPU for fast video encoding.
# This offloads CPU-intensive FFmpeg libx264 encoding from Cloud Run to achieve
# 2-3x faster encoding times with dedicated high-frequency cores.
# Cloud Run dispatches encoding jobs to this worker via HTTP.

# Service account for encoding worker VM
encoding_worker_sa = serviceaccount.Account(
    "encoding-worker-sa",
    account_id="encoding-worker",
    display_name="Encoding Worker Service Account",
    description="Service account for high-performance video encoding VM",
)

# Grant encoding worker service account GCS read/write permissions
encoding_worker_storage_iam = gcp.storage.BucketIAMMember(
    "encoding-worker-storage-admin",
    bucket=bucket.name,
    role="roles/storage.objectAdmin",
    member=encoding_worker_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant encoding worker service account Secret Manager access
encoding_worker_secrets_iam = gcp.projects.IAMMember(
    "encoding-worker-secrets-access",
    project=project_id,
    role="roles/secretmanager.secretAccessor",
    member=encoding_worker_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant encoding worker logging permissions
encoding_worker_logging_iam = gcp.projects.IAMMember(
    "encoding-worker-logging-access",
    project=project_id,
    role="roles/logging.logWriter",
    member=encoding_worker_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant encoding worker monitoring permissions (for Ops Agent metrics)
encoding_worker_monitoring_iam = gcp.projects.IAMMember(
    "encoding-worker-monitoring-access",
    project=project_id,
    role="roles/monitoring.metricWriter",
    member=encoding_worker_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Static external IP for encoding worker (Cloud Run doesn't have VPC access by default)
encoding_worker_ip = gcp.compute.Address(
    "encoding-worker-ip",
    name="encoding-worker-static-ip",
    region="us-central1",
    address_type="EXTERNAL",
    description="Static external IP for encoding worker service",
)

# Startup script for the encoding worker VM
ENCODING_WORKER_STARTUP_SCRIPT = """#!/bin/bash
set -e

# Log to a file for debugging
exec > >(tee /var/log/encoding-worker-startup.log) 2>&1
echo "Starting encoding worker setup at $(date)"

# Install dependencies
apt-get update
apt-get install -y python3-pip python3-venv docker.io curl git xz-utils

# Install fonts for ASS subtitle rendering (libass uses fontconfig)
# - fonts-noto: Noto Sans (default karaoke font)
# - fonts-noto-cjk: CJK character support
# - fontconfig: Font configuration system
apt-get install -y fonts-noto fonts-noto-cjk fontconfig
fc-cache -fv  # Rebuild font cache

# Enable and start Docker
systemctl enable docker
systemctl start docker

# Install optimized static FFmpeg build (John Van Sickle)
echo "Installing optimized FFmpeg..."
curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz
tar -xf /tmp/ffmpeg.tar.xz -C /tmp
cp /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/
cp /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/
chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
rm -rf /tmp/ffmpeg*
ffmpeg -version

# Create working directory
mkdir -p /opt/encoding-worker
cd /opt/encoding-worker

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install fastapi uvicorn google-cloud-storage aiofiles aiohttp

# Pre-install karaoke-gen wheel from GCS (for LocalEncodingService)
echo "Installing karaoke-gen wheel from GCS..."
gsutil cp "gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-*.whl" /tmp/ 2>/dev/null || true
WHEEL_FILE=$(ls -t /tmp/karaoke_gen-*.whl 2>/dev/null | head -1)
if [ -n "$WHEEL_FILE" ]; then
    echo "Installing wheel: $WHEEL_FILE"
    pip install --upgrade "$WHEEL_FILE" || echo "WARNING: Failed to install wheel, will retry at job start"
else
    echo "No wheel found in GCS, will install at job start"
fi

# Get API key from Secret Manager
echo "Fetching API key from Secret Manager..."
ENCODING_API_KEY=$(gcloud secrets versions access latest --secret=encoding-worker-api-key 2>/dev/null || echo "")

if [ -z "$ENCODING_API_KEY" ]; then
    echo "WARNING: No API key found in Secret Manager, generating temporary key"
    ENCODING_API_KEY=$(openssl rand -hex 32)
    echo "Temporary API key: $ENCODING_API_KEY"
fi

# Create the encoding worker service
cat > /opt/encoding-worker/main.py << 'PYTHON_CODE'
import asyncio
import glob as glob_module
import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Depends
from google.cloud import storage
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Encoding Worker", version="1.0.0")

# API key authentication
API_KEY = os.environ.get("ENCODING_API_KEY", "")


async def verify_api_key(x_api_key: str = Header(None)):
    # Verify API key for authentication
    if not API_KEY:
        logger.warning("No API key configured - authentication disabled")
        return True
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True

# Job tracking
jobs: dict[str, dict] = {}
executor = ThreadPoolExecutor(max_workers=4)  # 4 parallel encoding jobs

# GCS client
storage_client = storage.Client()

class EncodeRequest(BaseModel):
    job_id: str
    input_gcs_path: str  # gs://bucket/path/to/inputs/
    output_gcs_path: str  # gs://bucket/path/to/outputs/
    encoding_config: dict  # Video formats to generate


class EncodePreviewRequest(BaseModel):
    job_id: str
    ass_gcs_path: str      # gs://bucket/path/to/subtitles.ass
    audio_gcs_path: str    # gs://bucket/path/to/audio.flac
    output_gcs_path: str   # gs://bucket/path/to/output.mp4
    background_color: str = "black"
    background_image_gcs_path: Optional[str] = None
    font_gcs_path: Optional[str] = None  # gs://bucket/path/to/custom-font.ttf


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending, running, complete, failed
    progress: int  # 0-100
    error: Optional[str] = None
    output_files: Optional[list[str]] = None


def download_from_gcs(gcs_uri: str, local_path: Path):
    # Download a file or folder from GCS
    # Parse gs://bucket/path
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=prefix))

    for blob in blobs:
        # Get relative path from prefix
        rel_path = blob.name[len(prefix):].lstrip("/")
        if not rel_path:
            continue
        dest = local_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest))
        logger.info(f"Downloaded: {blob.name} -> {dest}")


def upload_to_gcs(local_path: Path, gcs_uri: str):
    # Upload a file or folder to GCS
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1].rstrip("/") if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)

    if local_path.is_file():
        blob_name = f"{prefix}/{local_path.name}" if prefix else local_path.name
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path))
        logger.info(f"Uploaded: {local_path} -> gs://{bucket_name}/{blob_name}")
    else:
        for file in local_path.rglob("*"):
            if file.is_file():
                rel_path = file.relative_to(local_path)
                blob_name = f"{prefix}/{rel_path}" if prefix else str(rel_path)
                blob = bucket.blob(blob_name)
                blob.upload_from_filename(str(file))
                logger.info(f"Uploaded: {file} -> gs://{bucket_name}/{blob_name}")


def download_single_file_from_gcs(gcs_uri: str, local_path: Path):
    # Download a single file from GCS
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(local_path))
    logger.info(f"Downloaded: {gcs_uri} -> {local_path}")


def upload_single_file_to_gcs(local_path: Path, gcs_uri: str):
    # Upload a single file to a specific GCS path
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info(f"Uploaded: {local_path} -> {gcs_uri}")


def run_preview_encoding(job_id: str, work_dir: Path, request: "EncodePreviewRequest"):
    # Run FFmpeg encoding for preview video (480x270, fast settings)
    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 10

    try:
        # Download input files
        ass_path = work_dir / "subtitles.ass"
        audio_path = work_dir / "audio.flac"

        download_single_file_from_gcs(request.ass_gcs_path, ass_path)
        jobs[job_id]["progress"] = 20

        download_single_file_from_gcs(request.audio_gcs_path, audio_path)
        jobs[job_id]["progress"] = 30

        # Download background image if provided
        bg_image_path = None
        if request.background_image_gcs_path:
            bg_image_path = work_dir / "background.png"
            download_single_file_from_gcs(request.background_image_gcs_path, bg_image_path)

        # Download custom font if provided and register with fontconfig
        if request.font_gcs_path:
            # Use standard fontconfig location that's already in the search path
            fonts_dir = Path("/usr/local/share/fonts/custom")
            fonts_dir.mkdir(parents=True, exist_ok=True)
            font_filename = request.font_gcs_path.split("/")[-1]
            font_path = fonts_dir / font_filename
            download_single_file_from_gcs(request.font_gcs_path, font_path)
            logger.info(f"Downloaded custom font: {font_path}")
            # Update fontconfig cache so libass can find the font
            subprocess.run(["fc-cache", "-fv"], capture_output=True)
            logger.info(f"Updated fontconfig cache with custom font: {font_filename}")

        # Build FFmpeg command
        output_path = work_dir / "preview.mp4"

        # Escape special characters in path for FFmpeg filter syntax
        # FFmpeg filter parsing requires escaping: \\ : , [ ] ;
        def escape_ffmpeg_filter_path(path: str) -> str:
            # Note: Extra escaping needed since this is inside a triple-quoted string in Pulumi
            return path.replace("\\\\", "\\\\\\\\").replace(":", "\\\\:").replace(",", "\\\\,").replace("[", "\\\\[").replace("]", "\\\\]").replace(";", "\\\\;")

        escaped_ass_path = escape_ffmpeg_filter_path(str(ass_path))

        # Base command with frame rate
        cmd = ["ffmpeg", "-y", "-r", "24"]

        # Video input: background image or solid color
        if bg_image_path and bg_image_path.exists():
            cmd.extend(["-loop", "1", "-i", str(bg_image_path)])
            # Scale and pad background to 480x270
            vf = f"scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,ass={escaped_ass_path}"
        else:
            # Solid color background
            color = request.background_color or "black"
            cmd.extend(["-f", "lavfi", "-i", f"color=c={color}:s=480x270:r=24"])
            vf = f"ass={escaped_ass_path}"

        # Audio input
        cmd.extend(["-i", str(audio_path)])

        # Video filter and encoding settings
        cmd.extend([
            "-vf", vf,
            "-c:a", "aac", "-b:a", "96k",
            "-c:v", "libx264",
            "-preset", "superfast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-threads", "8",
            "-shortest",
            str(output_path)
        ])

        jobs[job_id]["progress"] = 40
        logger.info(f"Running preview encoding: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"FFmpeg failed: {result.stderr}")
            raise RuntimeError(f"FFmpeg preview encoding failed: {result.stderr[-500:]}")

        jobs[job_id]["progress"] = 80
        logger.info(f"Preview encoded: {output_path}")

        # Upload output to GCS
        upload_single_file_to_gcs(output_path, request.output_gcs_path)
        jobs[job_id]["progress"] = 95

        jobs[job_id]["output_files"] = [request.output_gcs_path]
        return output_path

    except Exception as e:
        logger.error(f"Preview encoding failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        raise


def ensure_latest_wheel():
    '''Download and install latest karaoke-gen wheel from GCS.

    Called at the start of each job to enable hot code updates without restart.
    In-progress jobs continue with their version, new jobs get latest code.
    '''
    try:
        logger.info("Checking for latest karaoke-gen wheel in GCS...")

        # Download latest wheel
        result = subprocess.run(
            ["gsutil", "cp", "gs://karaoke-gen-storage-nomadkaraoke/wheels/karaoke_gen-*.whl", "/tmp/"],
            capture_output=True, text=True, timeout=60
        )

        # Find the downloaded wheel (get the latest by version sorting)
        wheels = glob_module.glob("/tmp/karaoke_gen-*.whl")
        if not wheels:
            logger.warning("No wheel found in GCS, using fallback encoding logic")
            return False

        # Sort to get latest version
        wheel_path = sorted(wheels)[-1]
        logger.info(f"Installing wheel: {wheel_path}")

        # Install (or upgrade) the wheel
        # Use 5-minute timeout - first install at job start may need to resolve dependencies
        # Subsequent installs are faster since dependencies are cached
        install_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", wheel_path],
            capture_output=True, text=True, timeout=300
        )

        if install_result.returncode != 0:
            logger.warning(f"Wheel installation failed: {install_result.stderr}")
            return False

        logger.info(f"Successfully installed wheel: {wheel_path}")
        return True

    except subprocess.TimeoutExpired:
        logger.warning("Wheel download/install timed out, using fallback")
        return False
    except Exception as e:
        logger.warning(f"Failed to ensure latest wheel: {e}")
        return False


def find_file(work_dir: Path, *patterns):
    '''Find a file matching any of the given glob patterns.'''
    for pattern in patterns:
        matches = list(work_dir.glob(f"**/{pattern}"))
        if matches:
            return matches[0]
    return None


def run_encoding(job_id: str, work_dir: Path, config: dict):
    '''Run encoding using LocalEncodingService (single source of truth).

    Uses LocalEncodingService from the installed karaoke-gen wheel to ensure
    output files match local CLI exactly:
    - Proper names like "Artist - Title (Final Karaoke Lossless 4k).mp4"
    - Concatenated title + karaoke + end screens
    - All formats: lossless 4K MP4, lossy 4K MP4, lossless MKV, 720p MP4

    Requires the karaoke-gen wheel to be installed (done by ensure_latest_wheel).
    '''
    jobs[job_id]["status"] = "running"
    jobs[job_id]["progress"] = 10

    try:
        # Import LocalEncodingService from installed wheel (required, no fallback)
        from backend.services.local_encoding_service import LocalEncodingService, EncodingConfig
        logger.info("Using LocalEncodingService from installed wheel")

        # Get artist/title from config for proper naming
        artist = config.get("artist", "Unknown Artist")
        title = config.get("title", "Unknown Title")
        base_name = f"{artist} - {title}"
        logger.info(f"Encoding for: {base_name}")

        # Find input files in work_dir
        # Title/end screens are in screens/ subdirectory
        title_video = find_file(work_dir, "screens/title.mov", "*Title*.mov", "*title*.mov")
        end_video = find_file(work_dir, "screens/end.mov", "*End*.mov", "*end*.mov")

        # Karaoke video - search for With Vocals or main karaoke video
        karaoke_video = find_file(
            work_dir,
            "*With Vocals*.mov", "*With Vocals*.mkv",
            "*Vocals*.mov", "*Vocals*.mkv",
            "*.mkv", "*.mov"
        )
        # Exclude title/end/output videos
        if karaoke_video:
            name_lower = karaoke_video.name.lower()
            if "title" in name_lower or "end" in name_lower or "outputs" in str(karaoke_video):
                # Search more specifically for karaoke video
                karaoke_video = find_file(work_dir, "*Karaoke*.mkv", "*Karaoke*.mov", "*vocals*.mkv")

        # Instrumental audio
        instrumental = find_file(
            work_dir,
            "*instrumental_clean*.flac", "*Instrumental Clean*.flac",
            "*instrumental*.flac", "*Instrumental*.flac",
            "*instrumental*.wav"
        )

        logger.info(f"Found files:")
        logger.info(f"  Title video: {title_video}")
        logger.info(f"  Karaoke video: {karaoke_video}")
        logger.info(f"  End video: {end_video}")
        logger.info(f"  Instrumental: {instrumental}")

        # Validate required files
        if not title_video:
            raise ValueError(f"No title video found in {work_dir}. Check screens/ subdirectory.")
        if not karaoke_video:
            raise ValueError(f"No karaoke video found in {work_dir}")
        if not instrumental:
            raise ValueError(f"No instrumental audio found in {work_dir}")

        output_dir = work_dir / "outputs"
        output_dir.mkdir(exist_ok=True)

        jobs[job_id]["progress"] = 20

        # Build encoding config with proper file names
        encoding_config = EncodingConfig(
            title_video=str(title_video),
            karaoke_video=str(karaoke_video),
            instrumental_audio=str(instrumental),
            end_video=str(end_video) if end_video else None,
            output_karaoke_mp4=str(output_dir / f"{base_name} (Karaoke).mp4"),
            output_with_vocals_mp4=str(output_dir / f"{base_name} (With Vocals).mp4"),
            output_lossless_4k_mp4=str(output_dir / f"{base_name} (Final Karaoke Lossless 4k).mp4"),
            output_lossy_4k_mp4=str(output_dir / f"{base_name} (Final Karaoke Lossy 4k).mp4"),
            output_lossless_mkv=str(output_dir / f"{base_name} (Final Karaoke Lossless 4k).mkv"),
            output_720p_mp4=str(output_dir / f"{base_name} (Final Karaoke Lossy 720p).mp4"),
        )

        # Create service and run encoding
        service = LocalEncodingService(logger=logger)

        jobs[job_id]["progress"] = 30
        logger.info("Starting LocalEncodingService.encode_all_formats()")

        result = service.encode_all_formats(encoding_config)

        if not result.success:
            raise RuntimeError(f"Encoding failed: {result.error}")

        jobs[job_id]["progress"] = 90

        # Collect output files
        output_files = [str(f) for f in output_dir.glob("*") if f.is_file()]
        jobs[job_id]["output_files"] = output_files

        logger.info(f"Encoding complete. Output files: {output_files}")
        return output_dir

    except ImportError as e:
        # No fallback - wheel must be installed
        error_msg = (
            f"LocalEncodingService not available: {e}. "
            "The karaoke-gen wheel must be installed. "
            "Check that ensure_latest_wheel() succeeded and wheel exists in GCS."
        )
        logger.error(error_msg)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = error_msg
        raise RuntimeError(error_msg) from e

    except Exception as e:
        logger.error(f"Encoding failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        raise


async def process_job(job_id: str, request: EncodeRequest):
    # Process an encoding job asynchronously
    try:
        # Download and install latest wheel at job start (allows hot updates without restart)
        # This means in-progress jobs continue with their version, new jobs get latest code
        ensure_latest_wheel()

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            # Download input files
            jobs[job_id]["progress"] = 5
            logger.info(f"Downloading from {request.input_gcs_path}")
            download_from_gcs(request.input_gcs_path, work_dir)

            # Run encoding in thread pool (CPU-bound)
            loop = asyncio.get_event_loop()
            output_dir = await loop.run_in_executor(
                executor,
                run_encoding,
                job_id,
                work_dir,
                request.encoding_config
            )

            # Upload outputs
            jobs[job_id]["progress"] = 95
            logger.info(f"Uploading to {request.output_gcs_path}")
            upload_to_gcs(output_dir, request.output_gcs_path)

            # Convert local paths to blob paths (backend expects blob paths, not full gs:// URIs)
            # output_gcs_path is like "gs://bucket/jobs/id/encoded/"
            # We need paths like "jobs/id/encoded/Artist - Title (Final Karaoke Lossless 4k).mp4"
            gcs_path = request.output_gcs_path.replace("gs://", "")
            parts = gcs_path.split("/", 1)
            prefix = parts[1].rstrip("/") if len(parts) > 1 else ""
            local_output_files = jobs[job_id].get("output_files", [])
            blob_paths = []
            for local_path in local_output_files:
                filename = Path(local_path).name
                blob_path = f"{prefix}/{filename}" if prefix else filename
                blob_paths.append(blob_path)
            jobs[job_id]["output_files"] = blob_paths
            logger.info(f"Output files (blob paths): {blob_paths}")

            jobs[job_id]["status"] = "complete"
            jobs[job_id]["progress"] = 100
            logger.info(f"Job {job_id} complete")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


async def process_preview_job(job_id: str, request: EncodePreviewRequest):
    # Process a preview encoding job asynchronously
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir) / "work"
            work_dir.mkdir()

            # Run preview encoding in thread pool (CPU-bound)
            # Note: run_preview_encoding handles download/upload internally
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                executor,
                run_preview_encoding,
                job_id,
                work_dir,
                request
            )

            jobs[job_id]["status"] = "complete"
            jobs[job_id]["progress"] = 100
            logger.info(f"Preview job {job_id} complete")

    except Exception as e:
        logger.error(f"Preview job {job_id} failed: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


@app.post("/encode-preview")
async def submit_preview_encode_job(request: EncodePreviewRequest, background_tasks: BackgroundTasks, _auth: bool = Depends(verify_api_key)):
    # Submit a preview encoding job
    job_id = request.job_id

    # If job already exists, return cached result or current status
    if job_id in jobs:
        existing_job = jobs[job_id]
        if existing_job["status"] == "complete":
            # Return cached result - preview already encoded
            return {"status": "cached", "job_id": job_id, "output_path": existing_job.get("output_path")}
        elif existing_job["status"] == "failed":
            # Previous attempt failed, allow retry by replacing the job
            pass
        else:
            # Job is still in progress
            return {"status": "in_progress", "job_id": job_id}

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "output_files": None,
    }

    background_tasks.add_task(process_preview_job, job_id, request)

    return {"status": "accepted", "job_id": job_id}


@app.post("/encode")
async def submit_encode_job(request: EncodeRequest, background_tasks: BackgroundTasks, _auth: bool = Depends(verify_api_key)):
    # Submit an encoding job
    job_id = request.job_id

    if job_id in jobs:
        raise HTTPException(status_code=409, detail=f"Job {job_id} already exists")

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "error": None,
        "output_files": None,
    }

    background_tasks.add_task(process_job, job_id, request)

    return {"status": "accepted", "job_id": job_id}


@app.get("/status/{job_id}")
async def get_job_status(job_id: str, _auth: bool = Depends(verify_api_key)) -> JobStatus:
    # Get the status of an encoding job
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobStatus(**jobs[job_id])


@app.get("/health")
async def health_check():
    # Health check endpoint
    active_jobs = sum(1 for j in jobs.values() if j["status"] == "running")
    return {
        "status": "ok",
        "active_jobs": active_jobs,
        "queue_length": sum(1 for j in jobs.values() if j["status"] == "pending"),
        "ffmpeg_version": subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\\n")[0],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
PYTHON_CODE

# Create systemd service (use heredoc without quotes to expand ENCODING_API_KEY)
cat > /etc/systemd/system/encoding-worker.service << SYSTEMD
[Unit]
Description=Encoding Worker HTTP API Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/encoding-worker
ExecStart=/opt/encoding-worker/venv/bin/python main.py
Restart=always
RestartSec=10
Environment="GOOGLE_CLOUD_PROJECT=nomadkaraoke"
Environment="ENCODING_API_KEY=${ENCODING_API_KEY}"

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable encoding-worker
systemctl start encoding-worker

echo "Encoding worker service started at $(date)"
echo "Service listening on port 8080"
"""

# Encoding worker VM instance (C4-standard-96 for maximum performance)
encoding_worker_vm = gcp.compute.Instance(
    "encoding-worker",
    name="encoding-worker",
    machine_type="c4-standard-8",  # 8 vCPUs, Intel Granite Rapids 3.9 GHz (quota limit: 24 CPUs)
    zone="us-central1-a",
    boot_disk=gcp.compute.InstanceBootDiskArgs(
        initialize_params=gcp.compute.InstanceBootDiskInitializeParamsArgs(
            image="debian-cloud/debian-12",
            size=100,  # GB - for temp video files during encoding
            type="hyperdisk-balanced",  # C4 machines require Hyperdisk
        ),
    ),
    network_interfaces=[gcp.compute.InstanceNetworkInterfaceArgs(
        network="default",
        access_configs=[gcp.compute.InstanceNetworkInterfaceAccessConfigArgs(
            nat_ip=encoding_worker_ip.address,  # Static external IP
        )],
    )],
    service_account=gcp.compute.InstanceServiceAccountArgs(
        email=encoding_worker_sa.email,
        scopes=["cloud-platform"],
    ),
    metadata_startup_script=ENCODING_WORKER_STARTUP_SCRIPT,
    tags=["encoding-worker"],
    allow_stopping_for_update=True,
    # Enable ALL_CORE_MAX for maximum sustained frequency
    advanced_machine_features=gcp.compute.InstanceAdvancedMachineFeaturesArgs(
        threads_per_core=2,  # Enable hyperthreading
    ),
)

# Firewall rule for encoding worker
# Note: Cloud Run doesn't have VPC access by default, so we allow external access
# with API key authentication at the application level
encoding_worker_firewall = gcp.compute.Firewall(
    "encoding-worker-firewall",
    name="encoding-worker-allow-http",
    network="default",
    allows=[
        # HTTP API
        gcp.compute.FirewallAllowArgs(protocol="tcp", ports=["8080"]),
    ],
    # Allow from anywhere - auth is handled at application level via API key
    source_ranges=["0.0.0.0/0"],
    target_tags=["encoding-worker"],
    description="Allow HTTP access to encoding worker (auth required)",
)

# API key secret for encoding worker authentication
encoding_worker_api_key_secret = secretmanager.Secret(
    "encoding-worker-api-key",
    secret_id="encoding-worker-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Export encoding worker values
pulumi.export("encoding_worker_external_ip", encoding_worker_ip.address)
pulumi.export("encoding_worker_service_url", encoding_worker_ip.address.apply(
    lambda ip: f"http://{ip}:8080"
))
pulumi.export("encoding_worker_service_account", encoding_worker_sa.email)
pulumi.export("encoding_worker_vm_name", encoding_worker_vm.name)

# ==================== Cloud Monitoring Alerting ====================
# Alert policies for proactive monitoring and incident response

# Discord notification channel (webhook-based)
# Note: You'll need to manually create this in Cloud Console and reference it,
# or use the Discord webhook URL via Cloud Functions

# Alert: High Error Rate (>10% of requests returning errors)
error_rate_alert = gcp.monitoring.AlertPolicy(
    "high-error-rate-alert",
    display_name="Karaoke Backend - High Error Rate",
    combiner="OR",
    conditions=[
        gcp.monitoring.AlertPolicyConditionArgs(
            display_name="Error rate > 10%",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter='resource.type="cloud_run_revision" AND resource.labels.service_name="karaoke-backend" AND metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class!="2xx"',
                comparison="COMPARISON_GT",
                threshold_value=0.1,
                duration="300s",  # Must be above threshold for 5 minutes
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="60s",
                        per_series_aligner="ALIGN_RATE",
                        cross_series_reducer="REDUCE_SUM",
                    ),
                ],
                trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                    count=1,
                ),
            ),
        ),
    ],
    alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
        auto_close="3600s",  # Auto-close after 1 hour if resolved
    ),
    documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
        content="High error rate detected on karaoke-backend. Check Cloud Run logs for details.\n\nDashboard: https://console.cloud.google.com/run/detail/us-central1/karaoke-backend/logs?project=nomadkaraoke",
        mime_type="text/markdown",
    ),
    enabled=True,
)

# Alert: Cloud Tasks Queue Backlog (tasks piling up)
queue_backlog_alert = gcp.monitoring.AlertPolicy(
    "queue-backlog-alert",
    display_name="Karaoke Backend - Queue Backlog",
    combiner="OR",
    conditions=[
        gcp.monitoring.AlertPolicyConditionArgs(
            display_name="Queue depth > 50 tasks",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter='resource.type="cloud_tasks_queue" AND metric.type="cloudtasks.googleapis.com/queue/depth"',
                comparison="COMPARISON_GT",
                threshold_value=50,
                duration="600s",  # Must be above threshold for 10 minutes
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="60s",
                        per_series_aligner="ALIGN_MEAN",
                        cross_series_reducer="REDUCE_MAX",
                    ),
                ],
                trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                    count=1,
                ),
            ),
        ),
    ],
    alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
        auto_close="1800s",  # Auto-close after 30 min if resolved
    ),
    documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
        content="Cloud Tasks queue backlog detected. Tasks are piling up faster than workers can process.\n\nPossible causes:\n- Worker errors (check logs)\n- External API issues (Modal, AudioShake)\n- Cloud Run scaling limits\n\nDashboard: https://console.cloud.google.com/cloudtasks?project=nomadkaraoke",
        mime_type="text/markdown",
    ),
    enabled=True,
)

# Alert: High Memory Utilization (workers may be crashing)
memory_alert = gcp.monitoring.AlertPolicy(
    "high-memory-alert",
    display_name="Karaoke Backend - High Memory Usage",
    combiner="OR",
    conditions=[
        gcp.monitoring.AlertPolicyConditionArgs(
            display_name="Memory > 85%",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter='resource.type="cloud_run_revision" AND resource.labels.service_name="karaoke-backend" AND metric.type="run.googleapis.com/container/memory/utilizations"',
                comparison="COMPARISON_GT",
                threshold_value=0.85,
                duration="300s",
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="60s",
                        per_series_aligner="ALIGN_PERCENTILE_95",
                        cross_series_reducer="REDUCE_MAX",
                    ),
                ],
                trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                    count=1,
                ),
            ),
        ),
    ],
    alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
        auto_close="1800s",
    ),
    documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
        content="High memory utilization on karaoke-backend. Workers may be running out of memory.\n\nActions:\n- Check for memory leaks in worker code\n- Consider increasing Cloud Run memory limits\n- Review temp file cleanup",
        mime_type="text/markdown",
    ),
    enabled=True,
)

# Alert: Cloud Run Service Unavailable
service_unavailable_alert = gcp.monitoring.AlertPolicy(
    "service-unavailable-alert",
    display_name="Karaoke Backend - Service Unavailable",
    combiner="OR",
    conditions=[
        gcp.monitoring.AlertPolicyConditionArgs(
            display_name="No healthy instances",
            condition_absent=gcp.monitoring.AlertPolicyConditionConditionAbsentArgs(
                filter='resource.type="cloud_run_revision" AND resource.labels.service_name="karaoke-backend" AND metric.type="run.googleapis.com/container/instance_count"',
                duration="300s",  # Alert if no instances for 5 minutes
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionAbsentAggregationArgs(
                        alignment_period="60s",
                        per_series_aligner="ALIGN_MEAN",
                        cross_series_reducer="REDUCE_SUM",
                    ),
                ],
            ),
        ),
    ],
    alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
        auto_close="1800s",  # Minimum 30 minutes required by GCP
    ),
    documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
        content="Karaoke backend service appears to be down - no healthy Cloud Run instances detected.\n\nImmediate actions:\n1. Check Cloud Run logs for crash reasons\n2. Verify recent deployments\n3. Check external dependencies (Modal, AudioShake)\n\nService URL: https://api.nomadkaraoke.com/api/health",
        mime_type="text/markdown",
    ),
    enabled=True,
)

# Export alert policy IDs
pulumi.export("error_rate_alert_id", error_rate_alert.name)
pulumi.export("queue_backlog_alert_id", queue_backlog_alert.name)
pulumi.export("memory_alert_id", memory_alert.name)
pulumi.export("service_unavailable_alert_id", service_unavailable_alert.name)

# ==================== Google Drive Validator Cloud Function ====================
# A lightweight Cloud Function that runs daily to validate the public Google Drive
# folder for duplicate sequence numbers, invalid filenames, and gaps.
# Sends Pushbullet notification if issues are detected.

# Pushbullet API key secret (for sending notifications)
pushbullet_api_key_secret = secretmanager.Secret(
    "pushbullet-api-key",
    secret_id="pushbullet-api-key",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Service account for the validator Cloud Function
gdrive_validator_sa = serviceaccount.Account(
    "gdrive-validator-sa",
    account_id="gdrive-validator",
    display_name="Google Drive Validator Function",
    description="Service account for the GDrive validation Cloud Function",
)

# Grant the function access to Secret Manager (for Pushbullet API key)
gdrive_validator_secrets_iam = gcp.projects.IAMMember(
    "gdrive-validator-secrets-access",
    project=project_id,
    role="roles/secretmanager.secretAccessor",
    member=gdrive_validator_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Cloud Storage bucket for function source code
function_bucket = storage.Bucket(
    "gdrive-validator-function-source",
    name=f"gdrive-validator-source-{project_id}",
    location="US-CENTRAL1",
    force_destroy=True,  # OK to delete since it just holds function code
    uniform_bucket_level_access=True,
)

# Upload function source code as a ZIP archive
# Note: The actual upload is done via gcloud or Pulumi's archive resource
# For now, we reference the local directory - actual deployment uses gcloud
gdrive_validator_function = gcp.cloudfunctionsv2.Function(
    "gdrive-validator-function",
    name="gdrive-validator",
    location="us-central1",
    description="Validates Nomad Karaoke Google Drive folder for duplicates and issues",
    build_config=gcp.cloudfunctionsv2.FunctionBuildConfigArgs(
        runtime="python312",
        entry_point="validate_gdrive",
        source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceArgs(
            storage_source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                bucket=function_bucket.name,
                object="gdrive-validator-source.zip",
            ),
        ),
    ),
    service_config=gcp.cloudfunctionsv2.FunctionServiceConfigArgs(
        available_memory="256M",
        timeout_seconds=300,  # 5 minutes should be plenty for listing files
        min_instance_count=0,
        max_instance_count=1,
        service_account_email=gdrive_validator_sa.email,
        environment_variables={
            "GDRIVE_FOLDER_ID": "1laRKAyxo0v817SstfM5XkpbWiNKNAMSX",
            # Set to "true" for daily summary notifications, "false" for issues-only
            "NOTIFY_ON_SUCCESS": "true",
        },
        secret_environment_variables=[
            gcp.cloudfunctionsv2.FunctionServiceConfigSecretEnvironmentVariableArgs(
                key="PUSHBULLET_API_KEY",
                project_id=project_id,
                secret=pushbullet_api_key_secret.secret_id,
                version="latest",
            ),
        ],
    ),
)

# Allow Cloud Scheduler to invoke the function
gdrive_validator_invoker = gcp.cloudfunctionsv2.FunctionIamMember(
    "gdrive-validator-scheduler-invoker",
    project=project_id,
    location="us-central1",
    cloud_function=gdrive_validator_function.name,
    role="roles/cloudfunctions.invoker",
    member=f"serviceAccount:service-{project.number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com",
)

# Also allow the function's service account to invoke itself (for Cloud Scheduler)
gdrive_validator_sa_invoker = gcp.cloudfunctionsv2.FunctionIamMember(
    "gdrive-validator-sa-invoker",
    project=project_id,
    location="us-central1",
    cloud_function=gdrive_validator_function.name,
    role="roles/cloudfunctions.invoker",
    member=gdrive_validator_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Cloud Scheduler job to trigger validation daily at 6 AM Pacific
gdrive_validator_scheduler = cloudscheduler.Job(
    "gdrive-validator-scheduler",
    name="gdrive-validator-daily",
    description="Daily validation of Nomad Karaoke Google Drive folder",
    region="us-central1",
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

# Export GDrive validator resources
pulumi.export("gdrive_validator_function_url", gdrive_validator_function.url)
pulumi.export("gdrive_validator_function_name", gdrive_validator_function.name)
pulumi.export("gdrive_validator_scheduler_name", gdrive_validator_scheduler.name)
pulumi.export("gdrive_validator_service_account", gdrive_validator_sa.email)

# ==================== Self-Hosted GitHub Actions Runner ====================
# A dedicated VM running GitHub Actions runner for CI/CD jobs that need more
# disk space than GitHub-hosted runners provide (14GB vs 200GB here).
# This solves "No space left on device" errors during Docker builds.

# Service account for GitHub runner VM
github_runner_sa = serviceaccount.Account(
    "github-runner-sa",
    account_id="github-runner",
    display_name="GitHub Actions Runner",
    description="Service account for self-hosted GitHub Actions runner VM",
)

# Grant runner service account Artifact Registry reader (for pulling base images)
github_runner_artifact_reader = gcp.projects.IAMMember(
    "github-runner-artifact-reader",
    project=project_id,
    role="roles/artifactregistry.reader",
    member=github_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant runner service account Secret Manager access (for PAT)
github_runner_secrets_iam = gcp.projects.IAMMember(
    "github-runner-secrets-access",
    project=project_id,
    role="roles/secretmanager.secretAccessor",
    member=github_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant runner service account logging permissions
github_runner_logging_iam = gcp.projects.IAMMember(
    "github-runner-logging-access",
    project=project_id,
    role="roles/logging.logWriter",
    member=github_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# Grant runner service account storage admin permissions (for uploading/overwriting wheels in GCS)
github_runner_storage_iam = gcp.projects.IAMMember(
    "github-runner-storage-write",
    project=project_id,
    role="roles/storage.objectAdmin",
    member=github_runner_sa.email.apply(lambda email: f"serviceAccount:{email}"),
)

# GitHub PAT secret for runner registration
# The PAT needs 'repo' scope to register a runner
github_runner_pat_secret = secretmanager.Secret(
    "github-runner-pat",
    secret_id="github-runner-pat",
    replication=secretmanager.SecretReplicationArgs(
        auto=secretmanager.SecretReplicationAutoArgs(),
    ),
)

# Startup script for the GitHub runner VM
GITHUB_RUNNER_STARTUP_SCRIPT = """#!/bin/bash
set -e

# Log to a file for debugging
exec > >(tee /var/log/github-runner-startup.log) 2>&1
echo "Starting GitHub Actions runner setup at $(date)"

# System updates and core dependencies
apt-get update
apt-get install -y \\
    curl \\
    git \\
    jq \\
    unzip \\
    build-essential \\
    libssl-dev \\
    libffi-dev \\
    software-properties-common \\
    apt-transport-https \\
    ca-certificates \\
    gnupg \\
    lsb-release

# ==================== Docker ====================
echo "Installing Docker..."
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --batch --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Configure Docker for the runner user
systemctl enable docker
systemctl start docker

# ==================== Python 3.13 via pyenv ====================
# setup-python action on self-hosted runners needs Python in the tool cache
echo "Installing Python 3.13 via pyenv..."

# Install pyenv dependencies
apt-get install -y make build-essential libssl-dev zlib1g-dev \\
  libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \\
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

# Install pyenv system-wide
export PYENV_ROOT=/opt/pyenv
curl https://pyenv.run | bash

# Add to system profile for future sessions
cat > /etc/profile.d/pyenv.sh << 'PYENV_PROFILE'
export PYENV_ROOT=/opt/pyenv
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
PYENV_PROFILE

# Source pyenv for current session
export PATH="/opt/pyenv/bin:$PATH"
eval "$(/opt/pyenv/bin/pyenv init -)"

# Install Python 3.13
/opt/pyenv/bin/pyenv install 3.13.0
/opt/pyenv/bin/pyenv global 3.13.0

# Create symlinks for system-wide access
ln -sf /opt/pyenv/shims/python3.13 /usr/local/bin/python3.13
ln -sf /opt/pyenv/shims/python3 /usr/local/bin/python3
ln -sf /opt/pyenv/shims/pip3 /usr/local/bin/pip3

# ==================== Node.js 20 ====================
echo "Installing Node.js 20..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

# ==================== Java 21 (for Firestore emulator) ====================
echo "Installing Java 21 via Temurin..."
# Use Temurin (Adoptium) - more reliable than Debian repos
curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --batch --dearmor -o /usr/share/keyrings/adoptium.gpg
echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" > /etc/apt/sources.list.d/adoptium.list
apt-get update
apt-get install -y temurin-21-jdk

# ==================== FFmpeg ====================
echo "Installing FFmpeg..."
apt-get install -y ffmpeg

# ==================== Poetry ====================
echo "Installing Poetry..."
curl -sSL https://install.python-poetry.org | python3 -
ln -sf /root/.local/bin/poetry /usr/local/bin/poetry

# ==================== Google Cloud SDK ====================
echo "Installing Google Cloud SDK..."
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --batch --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list
apt-get update
apt-get install -y google-cloud-cli google-cloud-cli-firestore-emulator

# ==================== Create runner user ====================
echo "Creating runner user..."
useradd -m -s /bin/bash runner || true
usermod -aG docker runner

# ==================== GitHub Actions Runner ====================
echo "Setting up GitHub Actions Runner..."
RUNNER_VERSION="2.321.0"
RUNNER_DIR="/home/runner/actions-runner"

mkdir -p $RUNNER_DIR
cd $RUNNER_DIR

# Download runner
curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L \\
    https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
tar xzf actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz
rm actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz

chown -R runner:runner $RUNNER_DIR

# Get GitHub PAT from Secret Manager
echo "Fetching GitHub PAT from Secret Manager..."
GITHUB_PAT=$(gcloud secrets versions access latest --secret=github-runner-pat)

if [ -z "$GITHUB_PAT" ]; then
    echo "ERROR: GitHub PAT not found in Secret Manager"
    exit 1
fi

# Get runner registration token using PAT
echo "Getting runner registration token..."
REGISTRATION_TOKEN=$(curl -s -X POST \\
    -H "Authorization: token $GITHUB_PAT" \\
    -H "Accept: application/vnd.github.v3+json" \\
    https://api.github.com/repos/nomadkaraoke/karaoke-gen/actions/runners/registration-token | jq -r .token)

if [ -z "$REGISTRATION_TOKEN" ] || [ "$REGISTRATION_TOKEN" = "null" ]; then
    echo "ERROR: Failed to get registration token"
    exit 1
fi

# Configure runner (non-interactive)
echo "Configuring runner..."
cd $RUNNER_DIR
sudo -u runner ./config.sh \\
    --url https://github.com/nomadkaraoke/karaoke-gen \\
    --token "$REGISTRATION_TOKEN" \\
    --name "gcp-runner-$(hostname)" \\
    --labels "self-hosted,linux,x64,gcp,large-disk" \\
    --work "_work" \\
    --unattended \\
    --replace

# Install and start runner as a service
echo "Installing runner service..."
./svc.sh install runner
./svc.sh start

echo "GitHub Actions runner setup complete at $(date)"
echo "Runner registered with labels: self-hosted,linux,x64,gcp,large-disk"

# ==================== Setup Python in tool cache ====================
# setup-python action looks for Python in RUNNER_TOOL_CACHE/_tool/Python
echo "Setting up Python 3.13 in tool cache for setup-python action..."
TOOL_CACHE="/home/runner/actions-runner/_work/_tool"
mkdir -p $TOOL_CACHE/Python/3.13.0/x64

# Copy pyenv Python to tool cache
cp -r /opt/pyenv/versions/3.13.0/* $TOOL_CACHE/Python/3.13.0/x64/

# Create marker file that setup-python looks for
touch $TOOL_CACHE/Python/3.13.0/x64.complete

# Fix permissions - must own entire _work directory, not just _tool
chown -R runner:runner /home/runner/actions-runner/_work

echo "Python 3.13 added to tool cache"

# ==================== Docker cleanup cron ====================
# Prevent disk from filling up with old Docker images
# Run hourly instead of daily, with threshold-based cleanup
echo "Setting up Docker cleanup cron..."
cat > /etc/cron.hourly/docker-cleanup << 'CRON'
#!/bin/bash
# Docker disk space cleanup for GitHub Actions runners
# Runs hourly, with aggressive cleanup when disk usage exceeds threshold

# Log to syslog
exec 1> >(logger -t docker-cleanup) 2>&1

THRESHOLD=70  # Cleanup when disk usage exceeds this percentage

# Get current disk usage percentage (remove % sign)
USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')

echo "Current disk usage: ${USAGE}%"

if [ "$USAGE" -gt "$THRESHOLD" ]; then
    echo "Disk usage ${USAGE}% exceeds threshold ${THRESHOLD}%, running cleanup..."

    # Remove ALL unused images (not just old ones) - this is the key fix
    # The previous 168h filter let recent images accumulate
    docker system prune -af

    # Also clean builder cache which can grow large
    docker builder prune -af

    # Report new usage
    NEW_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
    echo "Cleanup complete. Disk usage: ${NEW_USAGE}%"
else
    echo "Disk usage ${USAGE}% is below threshold ${THRESHOLD}%, skipping cleanup"
fi
CRON
chmod +x /etc/cron.hourly/docker-cleanup

# Remove old daily cleanup if it exists (migrating to hourly)
rm -f /etc/cron.daily/docker-cleanup

# ==================== Docker pre-warm cron ====================
# Keep base images fresh by pulling hourly
echo "Setting up Docker pre-warm cron..."
cat > /etc/cron.hourly/docker-prewarm << 'CRON'
#!/bin/bash
# Pre-warm Docker images used by CI/CD
# This ensures runners always have the latest base images cached

# Log to syslog
exec 1> >(logger -t docker-prewarm) 2>&1

echo "Starting Docker image pre-warm..."

# Configure Docker auth (uses instance service account)
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet 2>/dev/null

# Pull latest images (|| true to not fail if network issues)
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend-base:latest || true
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend:latest || true

echo "Docker image pre-warm complete"
CRON
chmod +x /etc/cron.hourly/docker-prewarm

# ==================== Pre-warm Docker images ====================
# Pre-fetch commonly used Docker images to speed up first CI runs
echo "Pre-warming Docker images for faster CI builds..."

# Configure Docker to authenticate with Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet

# Pull base images used by deploy-backend job (largest layers, most benefit from caching)
echo "Pulling karaoke-backend-base image..."
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend-base:latest || true

echo "Pulling karaoke-backend image..."
docker pull us-central1-docker.pkg.dev/nomadkaraoke/karaoke-repo/karaoke-backend:latest || true

# Pull GCS emulator image used by backend-emulator-tests job
echo "Pulling fake-gcs-server image..."
docker pull fsouza/fake-gcs-server:latest || true

echo "Docker images pre-warmed"

echo "Setup complete!"
"""

# GitHub runner VM instances (multiple runners for parallel job execution)
NUM_RUNNERS = 20
github_runner_vms = []

for i in range(1, NUM_RUNNERS + 1):
    vm = gcp.compute.Instance(
        f"github-runner-{i}",
        name=f"github-runner-{i}",
        machine_type="e2-standard-4",  # 4 vCPU, 16GB RAM - good for Docker builds
        zone="us-central1-a",
        boot_disk=gcp.compute.InstanceBootDiskArgs(
            initialize_params=gcp.compute.InstanceBootDiskInitializeParamsArgs(
                image="debian-cloud/debian-12",
                size=200,  # GB - plenty for Docker builds and caches
                type="pd-ssd",  # SSD for faster I/O
            ),
        ),
        network_interfaces=[gcp.compute.InstanceNetworkInterfaceArgs(
            network="default",
            access_configs=[gcp.compute.InstanceNetworkInterfaceAccessConfigArgs()],  # Ephemeral IP is fine
        )],
        service_account=gcp.compute.InstanceServiceAccountArgs(
            email=github_runner_sa.email,
            scopes=["cloud-platform"],
        ),
        metadata_startup_script=GITHUB_RUNNER_STARTUP_SCRIPT,
        tags=["github-runner"],
        allow_stopping_for_update=True,
        # Ensure the secret exists before creating the VM
        # For existing runners (1-10), ignore startup script changes to avoid replacement
        opts=pulumi.ResourceOptions(
            depends_on=[github_runner_pat_secret],
            ignore_changes=["metadataStartupScript"] if i <= 10 else None,
        ),
    )
    github_runner_vms.append(vm)

# Export GitHub runner resources
pulumi.export("github_runner_vm_names", [vm.name for vm in github_runner_vms])
pulumi.export("github_runner_service_account", github_runner_sa.email)

