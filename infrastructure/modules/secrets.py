"""
Secret Manager resources.

Manages all secrets for the application. Secret values are added manually
via gcloud or the Cloud Console after resources are created.
"""

import pulumi
import pulumi_gcp as gcp
from pulumi_gcp import secretmanager, vpcaccess


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


def create_flacfetch_internal_url_version(
    secret: secretmanager.Secret,
    flacfetch_instance: gcp.compute.Instance,
    vpc_connector: vpcaccess.Connector,
) -> secretmanager.SecretVersion:
    """
    Set flacfetch-api-url secret to use internal IP.

    This allows Cloud Run to reach flacfetch via the VPC connector,
    bypassing the external firewall that restricts access to Cloudflare IPs.

    Args:
        secret: The flacfetch-api-url secret.
        flacfetch_instance: The flacfetch VM instance.
        vpc_connector: The VPC connector (ensures it exists before setting URL).

    Returns:
        secretmanager.SecretVersion: The new secret version with internal URL.
    """
    internal_url = flacfetch_instance.network_interfaces[0].network_ip.apply(
        lambda ip: f"http://{ip}:8080"
    )

    return secretmanager.SecretVersion(
        "flacfetch-api-url-internal",
        secret=secret.id,
        secret_data=internal_url,
        opts=pulumi.ResourceOptions(depends_on=[vpc_connector]),
    )
