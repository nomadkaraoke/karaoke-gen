"""
GitHub Actions Runner Manager Cloud Function.

Handles two types of triggers:
1. GitHub Webhook (workflow_job.queued) - Starts runner VMs when CI jobs are queued
2. Cloud Scheduler (every 15 min) - Stops idle runner VMs after timeout

Environment variables:
- GCP_PROJECT: GCP project ID
- GCP_ZONE: Zone where runner VMs are located
- WEBHOOK_SECRET_NAME: Secret Manager secret name for webhook verification
- RUNNER_PAT_SECRET_NAME: Secret Manager secret name for GitHub PAT
- IDLE_TIMEOUT_HOURS: Hours of inactivity before stopping runners (default: 1)
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

import functions_framework
from flask import Request
from google.cloud import compute_v1, secretmanager

# Configuration from environment
PROJECT_ID = os.environ.get("GCP_PROJECT", "nomadkaraoke")
ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
WEBHOOK_SECRET_NAME = os.environ.get("WEBHOOK_SECRET_NAME", "github-webhook-secret")
RUNNER_PAT_SECRET_NAME = os.environ.get("RUNNER_PAT_SECRET_NAME", "github-runner-pat")
IDLE_TIMEOUT_HOURS = int(os.environ.get("IDLE_TIMEOUT_HOURS", "1"))
RUNNER_NAMES = os.environ.get(
    "RUNNER_NAMES",
    "github-runner-1,github-runner-2,github-runner-3,github-build-runner",
).split(",")

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

    # Get the raw body for signature verification
    body = request.get_data()
    secret = get_webhook_secret()

    # Compute expected signature
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


def get_runner_instances() -> list[compute_v1.Instance]:
    """Get all GitHub runner instances."""
    client = get_compute_client()
    instances = []

    for instance_name in RUNNER_NAMES:
        try:
            instance = client.get(
                project=PROJECT_ID,
                zone=ZONE,
                instance=instance_name,
            )
            instances.append(instance)
        except Exception as e:
            print(f"Error getting instance {instance_name}: {e}")

    return instances


def start_runners() -> dict:
    """
    Start any TERMINATED runner VMs.

    Returns a dict with lists of started, already_running, and failed instances.
    """
    client = get_compute_client()
    result = {"started": [], "already_running": [], "failed": []}

    for instance_name in RUNNER_NAMES:
        try:
            instance = client.get(
                project=PROJECT_ID,
                zone=ZONE,
                instance=instance_name,
            )

            if instance.status == "TERMINATED":
                print(f"Starting {instance_name}...")
                operation = client.start(
                    project=PROJECT_ID,
                    zone=ZONE,
                    instance=instance_name,
                )
                # Wait for the operation to complete (timeout after 5 min)
                try:
                    operation.result(timeout=300)
                    print(f"{instance_name} started successfully")
                    result["started"].append(instance_name)
                except Exception as op_error:
                    print(f"Start operation failed for {instance_name}: {op_error}")
                    result["failed"].append(instance_name)
            elif instance.status == "RUNNING":
                result["already_running"].append(instance_name)
            else:
                print(f"{instance_name} is in state {instance.status}")
                result["already_running"].append(instance_name)

        except Exception as e:
            print(f"Error with {instance_name}: {e}")
            result["failed"].append(instance_name)

    return result


def update_instance_metadata(instance_name: str, key: str, value: str) -> None:
    """Update a single metadata key on an instance."""
    client = get_compute_client()

    # Get current instance to get fingerprint
    instance = client.get(project=PROJECT_ID, zone=ZONE, instance=instance_name)
    current_metadata = instance.metadata

    # Build new items list, replacing or adding the key
    new_items = []
    found = False
    for item in current_metadata.items:
        if item.key == key:
            new_items.append(compute_v1.Items(key=key, value=value))
            found = True
        else:
            new_items.append(item)

    if not found:
        new_items.append(compute_v1.Items(key=key, value=value))

    # Update metadata
    metadata = compute_v1.Metadata(
        fingerprint=current_metadata.fingerprint,
        items=new_items,
    )

    client.set_metadata(
        project=PROJECT_ID,
        zone=ZONE,
        instance=instance_name,
        metadata_resource=metadata,
    )


def get_instance_metadata(instance: compute_v1.Instance, key: str) -> str | None:
    """Get a metadata value from an instance."""
    if not instance.metadata or not instance.metadata.items:
        return None

    for item in instance.metadata.items:
        if item.key == key:
            return item.value

    return None


def check_github_for_pending_jobs() -> bool:
    """
    Check GitHub API for any pending/queued jobs for our runners.

    Returns True if there are jobs waiting for our self-hosted runners.
    """
    import urllib.request

    pat = get_github_pat()
    org = "nomadkaraoke"

    # Check for queued workflow runs in karaoke-gen repo
    url = f"https://api.github.com/repos/{org}/karaoke-gen/actions/runs?status=queued"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("total_count", 0) > 0:
                print(f"Found {data['total_count']} queued runs in karaoke-gen")
                return True
    except Exception as e:
        print(f"Error checking GitHub API: {e}")

    return False


def check_and_stop_idle_runners() -> dict:
    """
    Check for idle runners and stop them if idle for IDLE_TIMEOUT_HOURS.

    A runner is considered idle if:
    1. It's RUNNING
    2. No jobs are pending/queued on GitHub
    3. last-activity metadata is older than IDLE_TIMEOUT_HOURS

    Returns a dict with stopped and kept instances.
    """
    result = {"stopped": [], "kept": [], "errors": []}

    # First check if there are any pending jobs
    if check_github_for_pending_jobs():
        print("Jobs are pending - keeping all runners active")
        # Update last-activity on all running instances
        for instance in get_runner_instances():
            if instance.status == "RUNNING":
                result["kept"].append(instance.name)
                # Refresh activity timestamp since there are pending jobs
                try:
                    update_instance_metadata(
                        instance.name,
                        "last-activity",
                        datetime.now(timezone.utc).isoformat(),
                    )
                except Exception as e:
                    print(f"Error updating metadata for {instance.name}: {e}")
        return result

    # No pending jobs - check each instance for idle timeout
    client = get_compute_client()
    now = datetime.now(timezone.utc)

    for instance in get_runner_instances():
        if instance.status != "RUNNING":
            continue

        # Get last-activity timestamp
        last_activity_str = get_instance_metadata(instance, "last-activity")

        if not last_activity_str:
            # No activity recorded - set it now and don't stop
            print(f"{instance.name}: No last-activity metadata, setting now")
            try:
                update_instance_metadata(
                    instance.name,
                    "last-activity",
                    now.isoformat(),
                )
            except Exception as e:
                print(f"Error setting metadata for {instance.name}: {e}")
            result["kept"].append(instance.name)
            continue

        try:
            last_activity = datetime.fromisoformat(last_activity_str.replace("Z", "+00:00"))
            idle_hours = (now - last_activity).total_seconds() / 3600

            if idle_hours >= IDLE_TIMEOUT_HOURS:
                print(f"{instance.name}: Idle for {idle_hours:.1f} hours, stopping...")
                client.stop(
                    project=PROJECT_ID,
                    zone=ZONE,
                    instance=instance.name,
                )
                result["stopped"].append(instance.name)
            else:
                print(f"{instance.name}: Idle for {idle_hours:.1f} hours, keeping...")
                result["kept"].append(instance.name)

        except Exception as e:
            print(f"Error processing {instance.name}: {e}")
            result["errors"].append(instance.name)

    return result


@functions_framework.http
def handle_request(request: Request):
    """
    Main entry point for the Cloud Function.

    Handles:
    1. Cloud Scheduler triggers (action=check_idle parameter)
    2. GitHub webhook events (workflow_job.queued)
    """
    # Check if this is a scheduler trigger (idle check)
    action = request.args.get("action")
    if action == "check_idle":
        print("Scheduler trigger: checking for idle runners")
        result = check_and_stop_idle_runners()
        return json.dumps(result), 200, {"Content-Type": "application/json"}

    # Otherwise, this should be a GitHub webhook
    # Verify the webhook signature
    if not verify_webhook_signature(request):
        print("Webhook signature verification failed")
        return "Unauthorized", 401

    # Get the event type
    event = request.headers.get("X-GitHub-Event")
    if not event:
        return "Missing X-GitHub-Event header", 400

    # We only care about workflow_job events
    if event != "workflow_job":
        return "OK - event ignored", 200

    # Parse the payload
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

    # Check if this job requires our self-hosted runners
    if "self-hosted" not in labels:
        print("Job doesn't require self-hosted runners, ignoring")
        return "OK - not for self-hosted runners", 200

    # Handle different actions
    if action == "queued":
        # Job is queued - ensure runners are started
        print("Job queued - starting runners if needed")
        result = start_runners()
        return json.dumps(result), 200, {"Content-Type": "application/json"}

    elif action == "in_progress":
        # Job started - update activity timestamp on all running instances
        print("Job in progress - updating activity timestamps")
        now = datetime.now(timezone.utc).isoformat()
        for instance in get_runner_instances():
            if instance.status == "RUNNING":
                try:
                    update_instance_metadata(instance.name, "last-activity", now)
                except Exception as e:
                    print(f"Error updating {instance.name}: {e}")
        return "OK", 200

    elif action == "completed":
        # Job completed - update activity timestamp
        print("Job completed - updating activity timestamps")
        now = datetime.now(timezone.utc).isoformat()
        for instance in get_runner_instances():
            if instance.status == "RUNNING":
                try:
                    update_instance_metadata(instance.name, "last-activity", now)
                except Exception as e:
                    print(f"Error updating {instance.name}: {e}")
        return "OK", 200

    return "OK", 200
