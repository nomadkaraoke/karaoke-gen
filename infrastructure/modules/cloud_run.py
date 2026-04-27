"""
Cloud Run resources.

Manages Cloud Run domain mapping and Cloud Run Jobs.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import cloudrun, cloudrunv2

from config import PROJECT_ID, REGION

AUDIO_WORKER_GPU_REGION = "us-east4"  # L4 GPU quota available here


def create_domain_mapping() -> cloudrun.DomainMapping:
    """
    Create the Cloud Run domain mapping for api.nomadkaraoke.com.

    Returns:
        cloudrun.DomainMapping: The domain mapping resource.
    """
    domain_mapping = cloudrun.DomainMapping(
        "karaoke-backend-domain",
        location=REGION,
        name="api.nomadkaraoke.com",
        metadata=cloudrun.DomainMappingMetadataArgs(
            namespace=PROJECT_ID,
        ),
        spec=cloudrun.DomainMappingSpecArgs(
            route_name="karaoke-backend",
        ),
    )

    return domain_mapping


def create_lyrics_transcription_job(
    bucket: gcp.storage.Bucket,
    service_account: gcp.serviceaccount.Account,
) -> cloudrunv2.Job:
    """
    Create the Cloud Run Job for lyrics transcription.

    Cloud Run Jobs run to completion without HTTP request lifecycle concerns,
    avoiding the instance termination issue where Cloud Run would shut down
    instances mid-processing when using BackgroundTasks (e.g., job c94cc9d6).

    Typical duration: 5-15 minutes (AudioShake + agentic AI correction)

    Args:
        bucket: The GCS bucket for job artifacts.
        service_account: The service account to run the job.

    Returns:
        cloudrunv2.Job: The Cloud Run Job resource.
    """
    lyrics_transcription_job = cloudrunv2.Job(
        "lyrics-transcription-job",
        name="lyrics-transcription-job",
        location=REGION,
        template=cloudrunv2.JobTemplateArgs(
            template=cloudrunv2.JobTemplateTemplateArgs(
                containers=[
                    cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=f"{REGION}-docker.pkg.dev/{PROJECT_ID}/karaoke-repo/karaoke-backend:latest",
                        args=["python", "-m", "backend.workers.lyrics_worker"],
                        resources=cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "cpu": "2",
                                "memory": "4Gi",
                            },
                        ),
                        envs=[
                            # Admin token for Cloud Tasks auth header
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ADMIN_TOKENS",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/admin-tokens",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # AudioShake API token (required for transcription)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="AUDIOSHAKE_API_TOKEN",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/audioshake-api-key",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Cloud Run service URL for Cloud Tasks targeting
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="CLOUD_RUN_SERVICE_URL",
                                value="https://api.nomadkaraoke.com",
                            ),
                            # Enable Cloud Tasks mode (vs direct HTTP)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENABLE_CLOUD_TASKS",
                                value="true",
                            ),
                            # Basic configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=REGION,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            # Genius API token (for lyrics fetching)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GENIUS_API_TOKEN",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/genius-api-key",
                                        version="latest",
                                    ),
                                ),
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                            # RapidAPI key (for Musixmatch lyrics)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="RAPIDAPI_KEY",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/rapidapi-key",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Spotify cookie (for Spotify lyrics)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="SPOTIFY_COOKIE_SP_DC",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/spotify-cookie",
                                        version="latest",
                                    ),
                                ),
                            ),
                        ],
                    )
                ],
                service_account=service_account.email,
                timeout="1800s",  # 30 minutes max per task
                max_retries=2,
            ),
        ),
    )

    return lyrics_transcription_job


def create_audio_separation_job(
    bucket: gcp.storage.Bucket,
    service_account: gcp.serviceaccount.Account,
) -> cloudrunv2.Job:
    """
    Create the Cloud Run Job for audio separation with L4 GPU.

    Runs the Separator class directly with GPU acceleration, eliminating
    the HTTP call to the separate audio-separator service.

    Deployed in us-east4 where L4 GPU quota is available.

    Typical duration: 5-10 minutes (local GPU separation)

    Args:
        bucket: The GCS bucket for job artifacts.
        service_account: The service account to run the job.

    Returns:
        cloudrunv2.Job: The Cloud Run Job resource.
    """
    audio_separation_job = cloudrunv2.Job(
        "audio-separation-job",
        name="audio-separation-job",
        location=AUDIO_WORKER_GPU_REGION,
        deletion_protection=False,  # Allow Pulumi to replace (region change requires delete+create)
        template=cloudrunv2.JobTemplateArgs(
            template=cloudrunv2.JobTemplateTemplateArgs(
                containers=[
                    cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=f"{AUDIO_WORKER_GPU_REGION}-docker.pkg.dev/{PROJECT_ID}/karaoke-backend-gpu/karaoke-backend:latest",
                        args=["python", "-m", "backend.workers.audio_worker"],
                        resources=cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "cpu": "4",
                                "memory": "16Gi",
                                "nvidia.com/gpu": "1",
                            },
                        ),
                        envs=[
                            # Admin token for Cloud Tasks auth header
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ADMIN_TOKENS",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/admin-tokens",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Model directory (baked into GPU image, triggers local GPU mode)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="MODEL_DIR",
                                value="/models",
                            ),
                            # Cloud Run service URL for Cloud Tasks targeting
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="CLOUD_RUN_SERVICE_URL",
                                value="https://api.nomadkaraoke.com",
                            ),
                            # Enable Cloud Tasks mode (vs direct HTTP)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENABLE_CLOUD_TASKS",
                                value="true",
                            ),
                            # Basic configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=AUDIO_WORKER_GPU_REGION,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                        ],
                    )
                ],
                node_selector=cloudrunv2.JobTemplateTemplateNodeSelectorArgs(
                    accelerator="nvidia-l4",
                ),
                gpu_zonal_redundancy_disabled=True,  # Required for GPU Jobs (capacity limitation)
                service_account=service_account.email,
                timeout="1800s",  # 30 minutes (local GPU is faster than remote API)
                max_retries=2,
            ),
        ),
    )

    return audio_separation_job


def create_audio_download_job(
    bucket: gcp.storage.Bucket,
    service_account: gcp.serviceaccount.Account,
    vpc_connector: object = None,
) -> cloudrunv2.Job:
    """
    Create the Cloud Run Job for audio downloading.

    Downloads audio from Spotify/YouTube/RED/OPS via the flacfetch VM,
    then triggers audio separation and lyrics workers.

    Requires VPC access to reach the flacfetch VM on its internal IP.

    Typical duration: 30s-5 minutes (depending on source)

    Args:
        bucket: The GCS bucket for job artifacts.
        service_account: The service account to run the job.
        vpc_connector: VPC connector for accessing flacfetch VM (optional).

    Returns:
        cloudrunv2.Job: The Cloud Run Job resource.
    """
    # Build VPC access config if connector provided
    vpc_access = None
    if vpc_connector:
        vpc_access = cloudrunv2.JobTemplateTemplateVpcAccessArgs(
            connector=vpc_connector.id,
            egress="PRIVATE_RANGES_ONLY",
        )

    audio_download_job = cloudrunv2.Job(
        "audio-download-job",
        name="audio-download-job",
        location=REGION,
        template=cloudrunv2.JobTemplateArgs(
            template=cloudrunv2.JobTemplateTemplateArgs(
                containers=[
                    cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=f"{REGION}-docker.pkg.dev/{PROJECT_ID}/karaoke-repo/karaoke-backend:latest",
                        args=["python", "-m", "backend.workers.audio_download_worker"],
                        resources=cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "cpu": "1",
                                "memory": "1Gi",
                            },
                        ),
                        envs=[
                            # Admin token for worker auth
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ADMIN_TOKENS",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/admin-tokens",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Cloud Run service URL for triggering workers
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="CLOUD_RUN_SERVICE_URL",
                                value="https://api.nomadkaraoke.com",
                            ),
                            # Enable Cloud Tasks mode for worker triggers
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENABLE_CLOUD_TASKS",
                                value="true",
                            ),
                            # Basic configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            # Flacfetch API key
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="FLACFETCH_API_KEY",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/flacfetch-api-key",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Flacfetch API URL (internal VPC address)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="FLACFETCH_API_URL",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/flacfetch-api-url",
                                        version="latest",
                                    ),
                                ),
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=REGION,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                        ],
                    )
                ],
                service_account=service_account.email,
                timeout="600s",  # 10 minutes max (downloads typically < 5 min)
                max_retries=2,
                vpc_access=vpc_access,
            ),
        ),
    )

    return audio_download_job


def create_video_encoding_job(
    bucket: gcp.storage.Bucket,
    service_account: gcp.serviceaccount.Account,
) -> cloudrunv2.Job:
    """
    Create the Cloud Run Job for video encoding.

    Cloud Run Jobs run to completion (up to 1 hour), immune to Cloud Run Service
    deployment rollouts. This prevents the BackgroundTask termination issue where
    a deployment kills the encoding orchestrator mid-flight (see incident 2026-03-08).

    The video worker orchestrates: CDG/TXT packaging, GCE encoding submission/polling,
    file download from GCS, YouTube/Dropbox/GDrive upload, and Discord notification.
    It needs the same env vars and secrets as the Cloud Run Service for these features.

    Args:
        bucket: The GCS bucket for job artifacts.
        service_account: The service account to run the job.

    Returns:
        cloudrunv2.Job: The Cloud Run Job resource.
    """
    video_encoding_job = cloudrunv2.Job(
        "video-encoding-job",
        name="video-encoding-job",
        location=REGION,
        template=cloudrunv2.JobTemplateArgs(
            template=cloudrunv2.JobTemplateTemplateArgs(
                containers=[
                    cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=f"{REGION}-docker.pkg.dev/{PROJECT_ID}/karaoke-repo/karaoke-backend:latest",
                        args=["python", "-m", "backend.workers.video_worker"],
                        resources=cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "cpu": "4",
                                "memory": "8Gi",
                            },
                        ),
                        envs=[
                            # Admin token for worker-to-service auth
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ADMIN_TOKENS",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/admin-tokens",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Cloud Run service URL for triggering downstream workers
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="CLOUD_RUN_SERVICE_URL",
                                value="https://api.nomadkaraoke.com",
                            ),
                            # Distribution defaults
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DEFAULT_BRAND_PREFIX",
                                value="NOMAD",
                            ),
                            # Discord webhook for release notifications
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DEFAULT_DISCORD_WEBHOOK_URL",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/discord-releases-webhook",
                                        version="latest",
                                    ),
                                ),
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DEFAULT_DROPBOX_PATH",
                                value="/MediaUnsynced/Karaoke/Tracks-Organized",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DEFAULT_ENABLE_YOUTUBE_UPLOAD",
                                value="true",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DEFAULT_GDRIVE_FOLDER_ID",
                                value="1laRKAyxo0v817SstfM5XkpbWiNKNAMSX",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DEFAULT_PRIVATE_BRAND_PREFIX",
                                value="NOMADNP",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="DEFAULT_PRIVATE_DROPBOX_PATH",
                                value="/MediaUnsynced/Karaoke/Tracks-NonPublished",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENABLE_CLOUD_TASKS",
                                value="true",
                            ),
                            # GCE encoding worker
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENCODING_WORKER_API_KEY",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/encoding-worker-api-key",
                                        version="latest",
                                    ),
                                ),
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENCODING_WORKER_URL",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/encoding-worker-url",
                                        version="latest",
                                    ),
                                ),
                            ),
                            # Core configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=REGION,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                            # Email provider for completion emails (Postmark primary, SendGrid fallback)
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="POSTMARK_SERVER_TOKEN",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/postmark-server-token",
                                        version="latest",
                                    ),
                                ),
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="SENDGRID_API_KEY",
                                value_source=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                    secret_key_ref=cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                        secret=f"projects/{PROJECT_ID}/secrets/sendgrid-api-key",
                                        version="latest",
                                    ),
                                ),
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="USE_GCE_ENCODING",
                                value="true",
                            ),
                        ],
                    )
                ],
                service_account=service_account.email,
                timeout="3600s",  # 1 hour max per task
                max_retries=2,
            ),
        ),
    )

    return video_encoding_job
