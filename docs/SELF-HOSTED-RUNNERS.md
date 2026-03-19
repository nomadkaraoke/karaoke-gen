# Self-Hosted GitHub Actions Runners

Single source of truth for how Nomad Karaoke CI/CD runners work — architecture, cost model, auto-scaling, and troubleshooting.

## Why Self-Hosted Runners?

GitHub-hosted runners work but are slower (2 vCPU, 7GB RAM) and costs scale linearly with CI minutes. Self-hosted runners on GCP give us:

- **Faster builds** — persistent Docker caches, more CPU/RAM
- **GPU access** — T4 GPUs for audio separation model testing
- **Scale-to-zero** — runners shut down automatically when idle, so we only pay for active CI time
- **Dedicated build runner** — 8 vCPU for Docker image builds

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  GitHub Webhook │────>│  Cloud Function  │────>│  Start Runner   │
│ (workflow_job)  │     │ (runner-manager) │     │      VMs        │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                        │
                                                        v
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Cloud Scheduler│────>│  Cloud Function  │────>│   Stop Idle     │
│  (every 15 min) │     │ (runner-manager) │     │   Runner VMs    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### Components

| Component | Spec | Purpose |
|-----------|------|---------|
| General runners | 3x `github-runner-{1..3}`, `e2-standard-4`, 200GB SSD | Execute CI jobs (lint, test, type check, build) |
| Build runner | 1x `github-build-runner`, `e2-standard-8`, 200GB SSD | Docker image builds (needs more CPU/RAM) |
| GPU runners | 3x `github-gpu-runner-{1..3}`, `n1-standard-4` + T4 GPU, 200GB SSD | Audio separation model testing |
| Cloud Function | `github-runner-manager`, Gen2, Python 3.12, 5min timeout | Start/stop VMs based on webhooks and idle checks |
| Cloud Scheduler | `runner-manager-idle-check`, every 15 min | Trigger idle runner checks |
| Cloud NAT | `github-runners-nat` + `github-runners-router` | Outbound internet for VMs (no external IPs) |

All runner VMs are in `us-central1-a`.

### Service Accounts

| Account | Purpose | Key Permissions |
|---------|---------|-----------------|
| `github-runner` | Runner VMs | `artifactregistry.reader`, `secretmanager.secretAccessor` |
| `runner-manager` | Cloud Function | `compute.instanceAdmin.v1`, `iam.serviceAccountUser` on `github-runner`, secret access |
| `runner-manager-scheduler` | Cloud Scheduler | `cloudfunctions.invoker` on the function |

### Runner Labels

| Label Set | VMs | Used By |
|-----------|-----|---------|
| `[self-hosted, linux, x64, gcp, large-disk]` | general runners | Most CI jobs |
| `[self-hosted, linux, x64, gcp, large-disk, docker-build]` | build runner | Docker image builds |
| `[self-hosted, linux, x64, gcp, gpu]` | GPU runners | Audio separation tests |

## How Auto-Scaling Works

### Starting Runners (Webhook-Driven)

1. Developer pushes code or opens a PR
2. GitHub Actions queues CI jobs with labels `[self-hosted, linux, gcp]`
3. GitHub sends a `workflow_job.queued` webhook to the Cloud Function
4. The function iterates over all runner VM names (from `RUNNER_NAMES` env var)
5. Any TERMINATED VMs are started in parallel via `ThreadPoolExecutor`
6. If VMs are in STOPPING state (from an overlapping idle shutdown), the function polls until they become TERMINATED (up to 90s), then starts them
7. VMs boot in ~2-3 minutes, run the startup script, register with GitHub, and pick up queued jobs

### Tracking Activity (Metadata-Based)

When a job completes, GitHub sends a `workflow_job.completed` webhook. The function updates a `last-activity` metadata key on the **specific runner** that ran the job (identified by `runner_name` from the webhook payload). Only updating the specific runner avoids fingerprint conflicts from concurrent webhook calls.

### Stopping Idle Runners (Scheduler-Driven)

Every 15 minutes, Cloud Scheduler triggers the function with `?action=check_idle`:

1. **Check for pending work:** Query GitHub API for queued/in-progress runs. If anything is pending, keep all runners alive (but do NOT refresh timestamps — that would reset idle tracking).
2. **Check each runner's idle time:** For each RUNNING VM, read `last-activity` metadata. If missing, fall back to `creationTimestamp` (the VM's creation date).
3. **Stop idle runners:** Any runner idle for 1+ hour is queued for shutdown. All stops execute in parallel to minimize the race window.

### The Race Condition (and How We Handle It)

During active development, new work can arrive while the idle check is shutting down runners:

- **Idle check** fires all stop operations in parallel (~60s window)
- **Webhook** arrives with new `workflow_job.queued` event
- **start_runners** finds some VMs in `STOPPING` state

Solution: `start_runners()` detects STOPPING VMs and **polls them every 5 seconds** (up to 90s) until they become TERMINATED, then starts them immediately.

## Cost Model

### Monthly Estimates

| Scenario | Runner Uptime | Monthly Cost |
|----------|---------------|--------------|
| Active development (8h/day) | ~10-12h/day | ~$100-150 |
| Light usage (solo dev) | ~3-5h/day | ~$50-80 |
| No activity (all stopped) | 0h | ~$35 (NAT + scheduler only) |

### Fixed Costs

| Component | Monthly Cost |
|-----------|-------------|
| Cloud NAT | ~$30 |
| Cloud Scheduler | <$1 |
| Cloud Function | <$5 |
| **Total fixed** | **~$35** |

### Variable Costs

| VM Type | Hourly Cost | 24/7 Monthly |
|---------|-------------|--------------|
| e2-standard-4 (general) × 3 | $0.402 | $289 |
| e2-standard-8 (build) × 1 | $0.268 | $193 |
| n1-standard-4 + T4 (GPU) × 3 | $1.62 | $1,166 |
| **All 7 VMs** | **$2.29/hr** | **$1,648** |

This is why idle shutdown is critical — the difference between working and broken auto-scaling is ~$1,600/month.

## Historical Bugs and Fixes (March 2026)

The initial runner manager implementation had several bugs that prevented idle shutdown. Runners were running 24/7 for weeks, wasting ~$1,600/month of GCP credits.

### Bug 1: Metadata Writes Not Awaited

**Problem:** `client.set_metadata()` returns an Operation object. Without calling `.result()`, the write completes asynchronously and fingerprint conflicts from concurrent calls are swallowed silently. `last-activity` metadata never persisted.

**Fix:** Added `operation.result(timeout=60)` to all metadata writes.

### Bug 2: "Set Now and Keep" Infinite Loop

**Problem:** When `last-activity` was missing, the function set it to "now" and kept the runner. Since the metadata write failed silently (Bug 1), every 15-minute idle check repeated: "No last-activity metadata, setting now" → keeps running → forever.

**Fix:** Use `creationTimestamp` as fallback instead of "now". VMs without metadata are treated as having been idle since creation.

### Bug 3: Pending Jobs Reset All Timestamps

**Problem:** When any queued run existed, the function refreshed `last-activity` on ALL runners. This reset idle tracking so runners could never accumulate enough idle time to be stopped.

**Fix:** When jobs are pending, keep runners alive but do NOT refresh timestamps.

### Bug 4: Missing IAM Permission

**Problem:** The `runner-manager` SA needed `iam.serviceAccountUser` on the `github-runner` SA to set metadata on VMs running as that account. Without it, metadata writes failed with `SERVICE_ACCOUNT_ACCESS_DENIED`.

**Fix:** Added `roles/iam.serviceAccountUser` binding in Pulumi.

### Bug 5: No STOPPING State Handling

**Problem:** VMs in STOPPING state (from an overlapping idle check) were skipped by `start_runners()`, potentially leaving all runners unreachable.

**Fix:** Poll STOPPING VMs until they become TERMINATED (up to 90s), then start them.

### Bug 6: Sequential Stops

**Problem:** Stopping VMs one at a time created a wide race window where new webhooks could overlap.

**Fix:** Stop all idle VMs in parallel using `ThreadPoolExecutor`.

### Bug 7: All Runners Updated on Job Completion

**Problem:** `in_progress` and `completed` webhooks updated metadata on ALL running instances, causing fingerprint conflicts when multiple jobs completed simultaneously.

**Fix:** Only update the specific runner identified by `runner_name` in the webhook payload. Removed the `in_progress` handler entirely.

### Bug 8: Flaky "docker: command not found" (Exit Code 127)

**Problem:** Backend Emulator Integration Tests intermittently failed with `docker: command not found` (exit code 127). Re-running the job always succeeded. Root cause: When a stopped VM restarts, the GHA runner systemd service (installed via `svc.sh install` on a previous boot) auto-starts immediately, accepting CI jobs before the GCE startup script has finished setting up Docker and other dependencies.

**Fix (two-part):**
1. **Startup script:** Stop the runner service at the very top of the script, before any package installations, preventing premature job acceptance.
2. **CI workflow:** Added a Docker readiness check step with 60-second retry loop in `backend-emulator-tests`, providing defense-in-depth.

## Troubleshooting

### Runners Not Starting

```bash
# 1. Check VM status
gcloud compute instances list --filter="name~github-runner" --format="table(name,status)" --project=nomadkaraoke

# 2. Check GitHub registration
gh api /orgs/nomadkaraoke/actions/runners --jq '.runners[] | {name, status, busy}'

# 3. Check Cloud Function logs
gcloud functions logs read github-runner-manager --limit=20 --gen2 --region=us-central1 --project=nomadkaraoke

# 4. Manual start if needed
gcloud compute instances start github-runner-{1..3} github-build-runner --zone=us-central1-a --project=nomadkaraoke
```

### Runners Not Stopping

```bash
# 1. Check last-activity metadata
for i in 1 2 3; do
  echo -n "github-runner-$i: "
  gcloud compute instances describe "github-runner-$i" --zone=us-central1-a --project=nomadkaraoke \
    --format="json(metadata.items)" | python3 -c "
import sys,json; items=json.load(sys.stdin).get('metadata',{}).get('items',[])
[print(i['value']) for i in items if i['key']=='last-activity']" 2>/dev/null || echo "no metadata"
done

# 2. Check for pending jobs
gh run list --limit 5 --json status,name --repo nomadkaraoke/karaoke-gen

# 3. Check function logs for idle check results
gcloud functions logs read github-runner-manager --limit=30 --gen2 --region=us-central1 --project=nomadkaraoke | grep -E "(Scheduler|idle|stop|pending)"
```

**Common causes:**
- `last-activity` metadata not being set (check for IAM errors in function logs)
- Scheduled workflows sending `workflow_job.queued` events
- Function timeout exceeded

### Webhook Not Triggering

1. Check https://github.com/organizations/nomadkaraoke/settings/hooks
2. Look at "Recent Deliveries" for the Cloud Function webhook
3. Common issues: secret mismatch, function URL changed after deploy

## Infrastructure Files

| File | Purpose |
|------|---------|
| `infrastructure/config.py` | `NUM_GITHUB_RUNNERS`, `NUM_GPU_RUNNERS`, `RunnerManagerConfig` |
| `infrastructure/compute/github_runners.py` | VM instances, Cloud NAT |
| `infrastructure/modules/runner_manager.py` | Cloud Function, Cloud Scheduler, IAM bindings |
| `infrastructure/functions/runner_manager/main.py` | Cloud Function code (webhook handler, idle check) |
| `infrastructure/compute/startup_scripts/github_runner.sh` | General runner VM bootstrap |
| `infrastructure/compute/startup_scripts/github_runner_gpu.sh` | GPU runner VM bootstrap |

## Secrets

| Secret | Purpose | Rotation |
|--------|---------|----------|
| `github-runner-pat` | Runner registration (PAT with `admin:org` > `manage_runners:org`) | Create new PAT, update secret, reset VMs |
| `github-webhook-secret` | Webhook signature verification | Generate new hex, update secret + GitHub webhook config |

## Manual Operations

### Starting / Stopping All Runners

```bash
# Start all
gcloud compute instances start github-runner-{1..3} github-build-runner github-gpu-runner-{1..3} --zone=us-central1-a --project=nomadkaraoke

# Stop all
gcloud compute instances stop github-runner-{1..3} github-build-runner github-gpu-runner-{1..3} --zone=us-central1-a --project=nomadkaraoke
```

### Verifying Auto-Stop Works

```bash
# After last CI job completes, wait 1+ hour, then check:
gcloud compute instances list --filter="name~github-runner OR name~github-build OR name~github-gpu" \
  --format="table(name,status)" --project=nomadkaraoke
# Expected: all TERMINATED
```
