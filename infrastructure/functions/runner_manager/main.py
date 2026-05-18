"""
GitHub Actions Runner Manager Cloud Function.

Webhook handler + scheduler entry point for the ephemeral GHA runner
dispatcher. The heavy lifting lives in ``ephemeral.py``:

* ``workflow_job.queued`` webhook → ``create_ephemeral_runner`` (mints a JIT
  config, creates a fresh GCE VM, returns).
* Cloud Scheduler tick (every 15 min) → ``cleanup_orphans`` (reconciles GCE
  VMs against org-runner registrations, deletes stragglers, de-registers
  zombies).

Environment variables: see ``ephemeral.py``.

Until 2026-05-18 this function also supported a ``legacy`` mode that
start/stopped a fixed pool of long-lived runner VMs. Phase 4 of the
ephemeral-runners rollout (PR #780) deleted those VMs; the legacy code was
dropped here.
"""

import hashlib
import hmac
import json
import os

import functions_framework
from flask import Request
from google.cloud import compute_v1, secretmanager

# Configuration from environment
PROJECT_ID = os.environ.get("GCP_PROJECT", "nomadkaraoke")
ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
WEBHOOK_SECRET_NAME = os.environ.get("WEBHOOK_SECRET_NAME", "github-webhook-secret")
RUNNER_PAT_SECRET_NAME = os.environ.get("RUNNER_PAT_SECRET_NAME", "github-runner-pat")

# Lazy-loaded clients and secrets
_compute_client = None
_secret_client = None
_webhook_secret = None
_github_pat = None


def get_compute_client() -> compute_v1.InstancesClient:
    """Get or create the Compute Engine client."""
    global _compute_client
    if _compute_client is None:
        _compute_client = compute_v1.InstancesClient()
    return _compute_client


def get_secret_client() -> secretmanager.SecretManagerServiceClient:
    """Get or create the Secret Manager client."""
    global _secret_client
    if _secret_client is None:
        _secret_client = secretmanager.SecretManagerServiceClient()
    return _secret_client


def get_webhook_secret() -> str:
    """Get the webhook secret from Secret Manager (cached)."""
    global _webhook_secret
    if _webhook_secret is None:
        client = get_secret_client()
        name = f"projects/{PROJECT_ID}/secrets/{WEBHOOK_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        _webhook_secret = response.payload.data.decode("utf-8").strip()
    return _webhook_secret


def get_github_pat() -> str:
    """Get the GitHub PAT from Secret Manager (cached)."""
    global _github_pat
    if _github_pat is None:
        client = get_secret_client()
        name = f"projects/{PROJECT_ID}/secrets/{RUNNER_PAT_SECRET_NAME}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        _github_pat = response.payload.data.decode("utf-8").strip()
    return _github_pat


def verify_webhook_signature(request: Request) -> bool:
    """
    Verify the GitHub webhook signature.

    GitHub sends a signature in the X-Hub-Signature-256 header.
    We compute the expected signature and compare.
    """
    signature_header = request.headers.get("X-Hub-Signature-256")
    if not signature_header:
        print("Missing X-Hub-Signature-256 header")
        return False

    body = request.get_data()
    secret = get_webhook_secret()

    expected_signature = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
    )

    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, signature_header)


def _handle_scheduler_tick() -> tuple[str, int, dict]:
    """Run the orphan-cleanup pass."""
    from ephemeral import cleanup_orphans

    print("Scheduler trigger: orphan cleanup")
    result = cleanup_orphans(get_github_pat())
    return json.dumps(result), 200, {"Content-Type": "application/json"}


def _handle_workflow_job_queued(labels: list[str]) -> tuple[str, int, dict]:
    """Dispatch a queued workflow job by creating a fresh ephemeral VM."""
    from ephemeral import create_ephemeral_runner

    try:
        result = create_ephemeral_runner(labels, get_github_pat())
        return json.dumps(result), 200, {"Content-Type": "application/json"}
    except Exception as exc:  # noqa: BLE001
        # Returning 503 keeps the queued job around for the next webhook
        # (action=in_progress / cancelled) and for orphan-cleanup retries.
        print(f"create_ephemeral_runner failed: {exc!r}")
        return json.dumps({"error": str(exc)}), 503, {"Content-Type": "application/json"}


def _looks_like_oidc_request(request: Request) -> bool:
    """Cheap auth gate for the scheduler entry point.

    The Cloud Scheduler job is configured with an OIDC token and sends
    ``Authorization: Bearer <jwt>`` on every call; an external caller hitting
    the public function URL without credentials does not. We don't verify the
    JWT signature here (would need google-auth), but requiring a Bearer header
    is enough to keep random internet callers out of the scheduler path —
    which performs destructive actions (VM deletions, runner de-registrations).
    """
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and len(auth) > len("Bearer ") + 20


@functions_framework.http
def handle_request(request: Request):
    """
    Main entry point for the Cloud Function.

    Handles:
    1. Cloud Scheduler triggers (action=check_idle or cleanup_orphans).
       Both action names route to orphan cleanup — ``check_idle`` is retained
       so the existing Cloud Scheduler job keeps working without a recreate.
    2. GitHub webhook events (workflow_job.queued/completed).
    """
    # Scheduler triggers run destructive actions (VM deletions, runner
    # de-registrations) — never let an unauthenticated caller reach them.
    action = request.args.get("action")
    if action in ("check_idle", "cleanup_orphans"):
        if not _looks_like_oidc_request(request):
            print(f"Scheduler action {action!r} rejected: missing Bearer token")
            return "Forbidden", 403
        return _handle_scheduler_tick()

    # Otherwise, this should be a GitHub webhook
    if not verify_webhook_signature(request):
        print("Webhook signature verification failed")
        return "Unauthorized", 401

    event = request.headers.get("X-GitHub-Event")
    if not event:
        return "Missing X-GitHub-Event header", 400

    # We only care about workflow_job events
    if event != "workflow_job":
        return "OK - event ignored", 200

    try:
        payload = request.get_json()
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        return "Invalid JSON", 400

    action = payload.get("action")
    job = payload.get("workflow_job", {})
    labels = job.get("labels", [])

    print(f"Received workflow_job.{action} event")
    print(f"Job labels: {labels}")

    if "self-hosted" not in labels:
        print("Job doesn't require self-hosted runners, ignoring")
        return "OK - not for self-hosted runners", 200

    if action == "queued":
        return _handle_workflow_job_queued(labels)

    if action == "completed":
        # Ephemeral runners self-shutdown via --jitconfig + startup-script
        # trap; nothing to record here. Orphan cleanup handles stragglers.
        return "OK - ephemeral runner self-cleans", 200

    return "OK", 200
