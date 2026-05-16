"""
GitHub Actions Runner Manager Cloud Function.

Two operating modes, selected by ``RUNNER_MODE``:

* ``legacy`` (default) — starts/stops the fixed pool of long-lived runner VMs
  managed by Pulumi. Webhook ``workflow_job.queued`` starts them; Cloud
  Scheduler ``check_idle`` stops them after ``IDLE_TIMEOUT_HOURS``.

* ``ephemeral`` — creates a fresh single-use GCE VM per webhook via JIT
  registration; orphan cleanup tears down whatever's left at each scheduler
  tick. Implemented in ``ephemeral.py``.

Environment variables (legacy):
- GCP_PROJECT: GCP project ID
- GCP_ZONE: Default zone where runner VMs are located
- WEBHOOK_SECRET_NAME: Secret Manager secret name for webhook verification
- RUNNER_PAT_SECRET_NAME: Secret Manager secret name for GitHub PAT
- IDLE_TIMEOUT_HOURS: Hours of inactivity before stopping runners (default: 1)
- RUNNER_NAMES: Comma-separated list of runner VM names to manage
- GPU_RUNNER_NAMES: Subset of RUNNER_NAMES started only on ``gpu`` jobs

Environment variables (ephemeral): see ``ephemeral.py``.
"""

import hashlib
import hmac
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import functions_framework
from flask import Request
from google.cloud import compute_v1, secretmanager

# Configuration from environment
PROJECT_ID = os.environ.get("GCP_PROJECT", "nomadkaraoke")
ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
WEBHOOK_SECRET_NAME = os.environ.get("WEBHOOK_SECRET_NAME", "github-webhook-secret")
RUNNER_PAT_SECRET_NAME = os.environ.get("RUNNER_PAT_SECRET_NAME", "github-runner-pat")
IDLE_TIMEOUT_HOURS = float(os.environ.get("IDLE_TIMEOUT_HOURS", "1"))
RUNNER_MODE = os.environ.get("RUNNER_MODE", "legacy").lower()
if RUNNER_MODE not in ("legacy", "ephemeral"):
    print(f"WARNING: unknown RUNNER_MODE={RUNNER_MODE!r}, defaulting to 'legacy'")
    RUNNER_MODE = "legacy"
RUNNER_NAMES = os.environ.get(
    "RUNNER_NAMES",
    "github-runner-1,github-runner-2,github-runner-3,github-build-runner",
).split(",")
# GPU runners are only started when the job explicitly requests the "gpu" label.
# This avoids waking ~$1.62/hr of GPU VMs for every CI run.
GPU_RUNNER_NAMES = set(
    os.environ.get("GPU_RUNNER_NAMES", "").split(",")
) - {""}  # filter empty strings

# Max time to wait for STOPPING VMs to become TERMINATED before starting them
_STOPPING_WAIT_TIMEOUT = 90
_STOPPING_POLL_INTERVAL = 5

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
    """Get all GitHub runner instances by name."""
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

    print(f"Found {len(instances)} runner instances")
    return instances


def _start_single_runner(instance_name: str) -> tuple[str, str]:
    """Start a single runner VM. Returns (instance_name, outcome)."""
    client = get_compute_client()
    try:
        operation = client.start(
            project=PROJECT_ID,
            zone=ZONE,
            instance=instance_name,
        )
        operation.result(timeout=300)
        print(f"{instance_name} started successfully")
        return instance_name, "started"
    except Exception as e:
        print(f"Start failed for {instance_name}: {e}")
        return instance_name, "failed"


def start_runners(include_gpu: bool = False) -> dict:
    """
    Start any TERMINATED runner VMs, waiting for STOPPING VMs first.

    If VMs are in STOPPING state (e.g. from an idle check that overlaps with
    new work), waits for them to finish stopping then starts them. This prevents
    a scenario where all runners are STOPPING and no webhook restarts them.

    Args:
        include_gpu: If False (default), skip GPU runners to save costs.
                     Only set True when the job explicitly requests GPU labels.

    Returns a dict with lists of started, already_running, and failed instances.
    """
    client = get_compute_client()
    result = {"started": [], "already_running": [], "failed": []}

    instances = get_runner_instances()

    # Filter out GPU runners unless explicitly requested
    if not include_gpu and GPU_RUNNER_NAMES:
        skipped = [i.name for i in instances if i.name in GPU_RUNNER_NAMES]
        if skipped:
            print(f"Skipping GPU runners (not requested): {skipped}")
        instances = [i for i in instances if i.name not in GPU_RUNNER_NAMES]

    stopping_instances = []
    to_start = []

    for instance in instances:
        if instance.status == "TERMINATED":
            to_start.append(instance.name)
        elif instance.status in ("STOPPING", "SUSPENDING"):
            stopping_instances.append(instance.name)
        elif instance.status == "RUNNING":
            result["already_running"].append(instance.name)
        else:
            print(f"{instance.name} is in state {instance.status}")
            result["already_running"].append(instance.name)

    # Wait for STOPPING VMs to finish, then add them to the start list
    if stopping_instances:
        print(f"Waiting for {len(stopping_instances)} STOPPING VMs: {stopping_instances}")
        deadline = time.monotonic() + _STOPPING_WAIT_TIMEOUT
        while stopping_instances and time.monotonic() < deadline:
            time.sleep(_STOPPING_POLL_INTERVAL)
            still_stopping = []
            for name in stopping_instances:
                try:
                    inst = client.get(project=PROJECT_ID, zone=ZONE, instance=name)
                    if inst.status == "TERMINATED":
                        to_start.append(name)
                    elif inst.status == "RUNNING":
                        result["already_running"].append(name)
                    else:
                        still_stopping.append(name)
                except Exception as e:
                    print(f"Error polling {name}: {e}")
                    still_stopping.append(name)
            stopping_instances = still_stopping

        # Any VMs still STOPPING after timeout — log and move on
        for name in stopping_instances:
            print(f"{name}: still STOPPING after {_STOPPING_WAIT_TIMEOUT}s, skipping")
            result["failed"].append(name)

    if not to_start:
        return result

    # Start all TERMINATED VMs in parallel
    with ThreadPoolExecutor(max_workers=len(to_start)) as executor:
        futures = {executor.submit(_start_single_runner, name): name for name in to_start}
        for future in as_completed(futures):
            name, outcome = future.result()
            result[outcome if outcome == "started" else "failed"].append(name)

    return result


def update_instance_metadata(instance_name: str, key: str, value: str) -> None:
    """Update a single metadata key on an instance.

    Awaits the operation result to ensure it completes and surfaces errors
    (e.g. fingerprint conflicts from concurrent writes).
    """
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

    operation = client.set_metadata(
        project=PROJECT_ID,
        zone=ZONE,
        instance=instance_name,
        metadata_resource=metadata,
    )
    # Wait for the operation to complete — without this, the operation can
    # silently fail (e.g., fingerprint conflict) and we'd never know
    operation.result(timeout=60)


def get_instance_metadata(instance: compute_v1.Instance, key: str) -> str | None:
    """Get a metadata value from an instance."""
    if not instance.metadata or not instance.metadata.items:
        return None

    for item in instance.metadata.items:
        if item.key == key:
            return item.value

    return None


def _get_instance_age_hours(instance: compute_v1.Instance) -> float:
    """Get how long an instance has been running, in hours.

    Uses creationTimestamp as the baseline — this is always available
    and doesn't depend on metadata writes succeeding.
    """
    now = datetime.now(timezone.utc)
    creation_str = instance.creation_timestamp
    if creation_str:
        creation_time = datetime.fromisoformat(creation_str.replace("Z", "+00:00"))
        return (now - creation_time).total_seconds() / 3600
    # Fallback: if somehow no creation timestamp, assume very old
    return float("inf")


def check_github_for_pending_jobs() -> bool:
    """
    Check GitHub API for any pending/queued/in-progress workflow runs
    across all org repos that use self-hosted runners.

    Returns True if there are runs that might need our self-hosted runners.
    """
    import urllib.request

    pat = get_github_pat()
    org = "nomadkaraoke"

    # All repos that have workflows using self-hosted runners
    repos = ["karaoke-gen", "python-audio-separator"]

    for repo in repos:
        for status in ["queued", "in_progress"]:
            url = f"https://api.github.com/repos/{org}/{repo}/actions/runs?status={status}"
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
                        print(f"Found {data['total_count']} {status} runs in {repo}")
                        return True
            except Exception as e:
                print(f"Error checking GitHub API ({repo}/{status}): {e}")

    return False


def _stop_single_runner(instance_name: str) -> tuple[str, str]:
    """Stop a single runner VM. Returns (instance_name, outcome)."""
    client = get_compute_client()
    try:
        operation = client.stop(
            project=PROJECT_ID,
            zone=ZONE,
            instance=instance_name,
        )
        operation.result(timeout=120)
        print(f"{instance_name}: Stopped successfully")
        return instance_name, "stopped"
    except Exception as e:
        print(f"Error stopping {instance_name}: {e}")
        return instance_name, "errors"


def check_and_stop_idle_runners() -> dict:
    """
    Check for idle runners and stop them if idle for IDLE_TIMEOUT_HOURS.

    Stops are executed in parallel to minimize the window where new webhooks
    could race with an in-progress shutdown.

    A runner is considered idle if:
    1. It's RUNNING
    2. No jobs are pending/queued/in-progress on GitHub
    3. last-activity metadata is older than IDLE_TIMEOUT_HOURS
       OR no last-activity metadata and instance is older than IDLE_TIMEOUT_HOURS

    Returns a dict with stopped and kept instances.
    """
    result = {"stopped": [], "kept": [], "errors": []}

    # First check if there are any pending/in-progress jobs.
    # If so, keep all runners alive but DON'T refresh timestamps —
    # that would reset idle tracking and prevent eventual shutdown.
    if check_github_for_pending_jobs():
        print("Jobs are pending/in-progress — keeping all runners, not updating timestamps")
        for instance in get_runner_instances():
            if instance.status == "RUNNING":
                result["kept"].append(instance.name)
        return result

    # No pending jobs — check each instance for idle timeout
    now = datetime.now(timezone.utc)
    to_stop = []

    for instance in get_runner_instances():
        if instance.status != "RUNNING":
            continue

        # Get last-activity timestamp from metadata
        last_activity_str = get_instance_metadata(instance, "last-activity")

        if not last_activity_str:
            # No activity recorded — use creation time as fallback.
            # This ensures VMs without metadata are stopped after IDLE_TIMEOUT_HOURS
            # rather than staying alive forever in a "set now and keep" loop.
            idle_hours = _get_instance_age_hours(instance)
            print(f"{instance.name}: No last-activity metadata, using creation time (age: {idle_hours:.1f}h)")
        else:
            try:
                last_activity = datetime.fromisoformat(last_activity_str.replace("Z", "+00:00"))
                idle_hours = (now - last_activity).total_seconds() / 3600
            except (ValueError, TypeError) as e:
                print(f"{instance.name}: Invalid last-activity value '{last_activity_str}': {e}")
                idle_hours = _get_instance_age_hours(instance)

        if idle_hours >= IDLE_TIMEOUT_HOURS:
            print(f"{instance.name}: Idle for {idle_hours:.1f} hours, queuing for stop...")
            to_stop.append(instance.name)
        else:
            print(f"{instance.name}: Idle for {idle_hours:.1f} hours, keeping...")
            result["kept"].append(instance.name)

    if not to_stop:
        return result

    # Stop all idle VMs in parallel to minimize the race window
    print(f"Stopping {len(to_stop)} idle runners in parallel...")
    with ThreadPoolExecutor(max_workers=len(to_stop)) as executor:
        futures = {executor.submit(_stop_single_runner, name): name for name in to_stop}
        for future in as_completed(futures):
            name, outcome = future.result()
            result[outcome].append(name)

    return result


def _handle_scheduler_tick() -> tuple[str, int, dict]:
    """Run the idle-check (legacy) or orphan-cleanup (ephemeral) pass."""
    if RUNNER_MODE == "ephemeral":
        from ephemeral import cleanup_orphans

        print("Scheduler trigger: ephemeral mode orphan cleanup")
        result = cleanup_orphans(get_github_pat())
    else:
        print("Scheduler trigger: legacy idle check")
        result = check_and_stop_idle_runners()
    return json.dumps(result), 200, {"Content-Type": "application/json"}


def _handle_workflow_job_queued(labels: list[str]) -> tuple[str, int, dict]:
    """Dispatch a queued workflow job to the right runner backend."""
    if RUNNER_MODE == "ephemeral":
        from ephemeral import create_ephemeral_runner

        try:
            result = create_ephemeral_runner(labels, get_github_pat())
            return json.dumps(result), 200, {"Content-Type": "application/json"}
        except Exception as exc:  # noqa: BLE001
            # Returning 503 keeps the queued job around for the next webhook
            # (action=in_progress / cancelled) and for orphan-cleanup retries.
            print(f"create_ephemeral_runner failed: {exc!r}")
            return json.dumps({"error": str(exc)}), 503, {"Content-Type": "application/json"}

    needs_gpu = "gpu" in labels
    print(f"Legacy: starting pool runners (gpu={'yes' if needs_gpu else 'no'})")
    result = start_runners(include_gpu=needs_gpu)
    return json.dumps(result), 200, {"Content-Type": "application/json"}


def _looks_like_oidc_request(request: Request) -> bool:
    """Cheap auth gate for the scheduler entry point.

    The Cloud Scheduler job is configured with an OIDC token and sends
    ``Authorization: Bearer <jwt>`` on every call; an external caller hitting
    the public function URL without credentials does not. We don't verify the
    JWT signature here (would need google-auth), but requiring a Bearer header
    is enough to keep random internet callers out of the scheduler path —
    which now performs destructive actions (VM deletions, runner
    de-registrations) when RUNNER_MODE=ephemeral.
    """
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and len(auth) > len("Bearer ") + 20


@functions_framework.http
def handle_request(request: Request):
    """
    Main entry point for the Cloud Function.

    Handles:
    1. Cloud Scheduler triggers (action=check_idle parameter)
    2. GitHub webhook events (workflow_job.queued/completed)
    """
    # Check if this is a scheduler trigger (idle check / orphan cleanup).
    # In ephemeral mode the orphan-cleanup pass deletes VMs and de-registers
    # runners — never let an unauthenticated caller reach it.
    action = request.args.get("action")
    if action in ("check_idle", "cleanup_orphans"):
        if not _looks_like_oidc_request(request):
            print(f"Scheduler action {action!r} rejected: missing Bearer token")
            return "Forbidden", 403
        return _handle_scheduler_tick()

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

    print(f"Received workflow_job.{action} event (mode={RUNNER_MODE})")
    print(f"Job labels: {labels}")

    # Check if this job requires our self-hosted runners
    if "self-hosted" not in labels:
        print("Job doesn't require self-hosted runners, ignoring")
        return "OK - not for self-hosted runners", 200

    # Handle different actions
    if action == "queued":
        return _handle_workflow_job_queued(labels)

    if action == "completed":
        if RUNNER_MODE == "ephemeral":
            # Ephemeral runners self-shutdown via --jitconfig + startup-script
            # trap; nothing to record here. Orphan cleanup handles stragglers.
            return "OK - ephemeral runner self-cleans", 200

        # Legacy: refresh last-activity on the specific runner that ran the job.
        runner_name = job.get("runner_name", "")
        if runner_name:
            print(f"Job completed on {runner_name} - updating activity timestamp")
            try:
                now = datetime.now(timezone.utc).isoformat()
                update_instance_metadata(runner_name, "last-activity", now)
                print(f"Updated last-activity on {runner_name}")
            except Exception as e:
                print(f"Error updating metadata for {runner_name}: {e}")
        else:
            print("Job completed but no runner_name in payload")
        return "OK", 200

    return "OK", 200
