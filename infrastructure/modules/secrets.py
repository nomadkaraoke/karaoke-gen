"""
Secret Manager resources.

Manages all secrets for the application. Secret values are added manually
via gcloud or the Cloud Console after resources are created.
"""

from pulumi_gcp import secretmanager


def create_secrets() -> dict[str, secretmanager.Secret]:
    """
    Create all Secret Manager secrets.

    Returns:
        dict: Dictionary mapping secret names to Secret resources.
    """
    # Define all secret names
    # Note: Private tracker secrets (red-*, ops-*) are managed by flacfetch repo
    secret_names = [
        # Audio processing
        "audioshake-api-key",
        "genius-api-key",
        "audio-separator-api-url",

        # Payment and email
        "stripe-secret-key",
        "stripe-webhook-secret",
        "postmark-server-token",

        # Observability (Langfuse for LLM tracing)
        "langfuse-public-key",
        "langfuse-secret-key",
        "langfuse-host",

        # AI auto-correct suggestions (Claude models via Anthropic API)
        "anthropic-api-key",

        # Service authentication
        "flacfetch-api-key",
        "flacfetch-api-url",
        "encoding-worker-api-key",
        # Capacity-fallback VMs config (JSON list of {vm,zone,ip,machine_type?}).
        # machine_type (e.g. "c4-highcpu-32") drives the shared speed-rank
        # ordering; optional for legacy entries (inferred from the vm name).
        # Stored as a secret because --set-env-vars on Cloud Run is
        # comma-delimited and the JSON value contains commas; the value itself is
        # non-secret (just public IPs of fallback VMs).
        "encoding-worker-fallback-vms",

        # Notifications
        "pushbullet-api-key",

        # Internal service URLs
        "gdrive-validator-url",

        # Web Push (VAPID keys for mobile push notifications)
        "vapid-public-key",
        "vapid-private-key",

        # GitHub
        "github-runner-pat",
        "github-webhook-secret",  # For runner manager webhook verification
        # DR: PAT (repo + read:org) the nightly backup uses to clone every repo
        # under github.com/nomadkaraoke + github.com/beveradb into git bundles on
        # S3, so code survives loss of GitHub access. Value added manually.
        "github-backup-token",

        # E2E Testing
        "e2e-bypass-key",

        # KaraokeNerds API
        "karaokenerds-api-key",

        # Notifications (Discord)
        "discord-alert-webhook",

        # Divebar on-demand refresh trigger (shared bearer token: kjbox's
        # "Refresh catalog" button -> divebar-lookup `refresh` action). Gates
        # an otherwise-public endpoint that re-runs the mirror/sync/xref
        # scheduler jobs on demand, so it's anti-abuse, not data-sensitive.
        "divebar-refresh-token",

        # Cloudflare edge origin lock: shared secret Cloudflare injects as the
        # X-Edge-Auth header on proxied requests; the backend edge-auth
        # middleware rejects public requests that lack it (blocks direct-to-
        # origin bypass of the WAF). Value added manually; also provided to
        # Pulumi as `edge:originSecret` for the Cloudflare header transform.
        # See modules/edge_security.py.
        "edge-origin-secret",
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
