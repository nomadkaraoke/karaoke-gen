"""
Secret Manager resources.

Manages all secrets for the application. Secret values are added manually
via gcloud or the Cloud Console after resources are created.
"""

import pulumi
from pulumi_gcp import secretmanager


def create_secrets() -> dict[str, secretmanager.Secret]:
    """
    Create all Secret Manager secrets.

    Returns:
        dict: Dictionary mapping secret names to Secret resources.
    """
    # Define all secret names (matching original __main__.py exactly)
    secret_names = [
        # Audio processing
        "audioshake-api-key",
        "genius-api-key",
        "audio-separator-api-url",

        # Private tracker APIs (for flacfetch audio search)
        "red-api-key",
        "red-api-url",
        "ops-api-key",
        "ops-api-url",

        # Payment and email
        "stripe-secret-key",
        "stripe-webhook-secret",
        "sendgrid-api-key",

        # Observability (Langfuse for LLM tracing)
        "langfuse-public-key",
        "langfuse-secret-key",
        "langfuse-host",

        # Service authentication
        "flacfetch-api-key",
        "flacfetch-api-url",
        "encoding-worker-api-key",

        # Notifications
        "pushbullet-api-key",

        # Web Push (VAPID keys for mobile push notifications)
        "vapid-public-key",
        "vapid-private-key",

        # GitHub
        "github-runner-pat",
    ]

    secrets = {}

    for secret_id in secret_names:
        secrets[secret_id] = secretmanager.Secret(
            secret_id,
            secret_id=secret_id,
            replication=secretmanager.SecretReplicationArgs(
                auto=secretmanager.SecretReplicationAutoArgs(),
            ),
        )

    return secrets
