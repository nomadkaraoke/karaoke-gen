"""
Cloud Run resources.

Manages Cloud Run domain mapping and Cloud Run Jobs.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import cloudrun, cloudrunv2

from config import PROJECT_ID, REGION


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
                            # Basic configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=REGION,
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
    Create the Cloud Run Job for audio separation.

    Cloud Run Jobs run to completion without HTTP request lifecycle concerns,
    avoiding the instance termination issue where Cloud Run would shut down
    instances mid-processing when using BackgroundTasks.

    Typical duration: 10-20 minutes (Modal API for GPU separation)

    Args:
        bucket: The GCS bucket for job artifacts.
        service_account: The service account to run the job.

    Returns:
        cloudrunv2.Job: The Cloud Run Job resource.
    """
    audio_separation_job = cloudrunv2.Job(
        "audio-separation-job",
        name="audio-separation-job",
        location=REGION,
        template=cloudrunv2.JobTemplateArgs(
            template=cloudrunv2.JobTemplateTemplateArgs(
                containers=[
                    cloudrunv2.JobTemplateTemplateContainerArgs(
                        image=f"{REGION}-docker.pkg.dev/{PROJECT_ID}/karaoke-repo/karaoke-backend:latest",
                        args=["python", "-m", "backend.workers.audio_worker"],
                        resources=cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                            limits={
                                "cpu": "2",
                                "memory": "4Gi",
                            },
                        ),
                        envs=[
                            # Basic configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=REGION,
                            ),
                            # Modal API URL for GPU audio separation
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="AUDIO_SEPARATOR_API_URL",
                                value="https://nomadkaraoke--audio-separator-api.modal.run",
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
                        ],
                    )
                ],
                service_account=service_account.email,
                timeout="2400s",  # 40 minutes max per task (Modal can be slow)
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
                            # Basic configuration
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="ENVIRONMENT",
                                value="production",
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCP_REGION",
                                value=REGION,
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

    Cloud Run Jobs provide up to 24-hour execution time for long-running video encoding.
    This is used when video encoding might exceed the 30-minute Cloud Tasks timeout.

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
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GOOGLE_CLOUD_PROJECT",
                                value=PROJECT_ID,
                            ),
                            cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                name="GCS_BUCKET_NAME",
                                value=bucket.name,
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
