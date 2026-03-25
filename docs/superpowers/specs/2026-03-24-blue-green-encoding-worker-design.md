# Blue-Green Encoding Worker Deployment

**Created:** 2026-03-24
**Branch:** feat/sess-20260324-1603-blue-green-encoding-deploy
**Status:** Approved

## Problem Statement

The karaoke-gen encoding worker runs as a single GCE VM (c4d-highcpu-32). Any deployment that breaks this instance causes a full encoding outage with no automatic recovery. PR #587 demonstrated this risk: a pip resolution bug crashed the worker for 17 hours because there was no fallback.

Additionally, the single VM runs 24/7 (~$800/mo) despite sporadic, unpredictable usage patterns.

## Goals

1. **Zero-downtime deployments** — broken code never reaches users
2. **Cost efficiency** — VMs shut down when not in use (both idle most of the time)
3. **Deep health verification** — real encode test before traffic switch, not just a `/health` ping
4. **Just-in-time startup** — primary VM starts when a user enters lyrics review, ready by the time they need encoding

## Architecture Overview

**Approach: Backend-Orchestrated Lifecycle + CI-Driven Blue-Green Swap**

```
                                    ┌──────────────────┐
                                    │  Cloud Scheduler  │
                                    │  (every 5 min)    │
                                    └────────┬─────────┘
                                             │ triggers
                                             ▼
┌──────────┐     ┌──────────────┐   ┌──────────────────┐
│ Frontend │────▶│   Backend    │   │  Idle Shutdown    │
│ (Next.js)│     │ (Cloud Run)  │   │  Cloud Function   │
└──────────┘     └──────┬───────┘   └────────┬─────────┘
                        │                     │
                   reads/writes          checks & stops
                        │                     │
                        ▼                     ▼
                ┌───────────────┐    ┌────────────────┐
                │   Firestore   │    │  Compute API   │
                │ config/       │    │  (start/stop)  │
                │ encoding-     │    └───────┬────────┘
                │ worker        │            │
                └───────────────┘     ┌──────┴──────┐
                                      │             │
                                      ▼             ▼
                               ┌──────────┐  ┌──────────┐
                               │  VM A    │  │  VM B    │
                               │ (primary │  │(secondary│
                               │  or off) │  │  or off) │
                               └──────────┘  └──────────┘
                                      ▲
                                      │
                               ┌──────┴──────┐
                               │ CI Workflow  │
                               │ (GitHub      │
                               │  Actions)    │
                               └─────────────┘
```

Six components, each with one clear responsibility:

| Component | Responsibility |
|-----------|---------------|
| **Pulumi (IaC)** | Two named VMs (a/b), two static IPs, firewall, IAM |
| **Backend (Cloud Run)** | Warmup trigger, activity heartbeat, encoding with retry, reads primary from Firestore |
| **CI workflow (GitHub Actions)** | Blue-green deploy: start secondary, deploy, deep health check, swap Firestore, drain & stop old |
| **Cloud Function** | Idle auto-shutdown every 5 min based on activity + active jobs |
| **Firestore doc** | Single source of truth for primary/secondary routing + metadata |
| **Frontend (Next.js)** | Warmup signal on lyrics review, heartbeat on activity, "warming up" UI state |

---

## Section 1: Infrastructure — Two Named VMs

Two permanent GCE VMs with identical specs, differentiated only by name:

| Property | VM A | VM B |
|----------|------|------|
| Name | `encoding-worker-a` | `encoding-worker-b` |
| Machine type | c4d-highcpu-32 | c4d-highcpu-32 |
| Zone | us-central1-c | us-central1-c |
| Static IP | `encoding-worker-ip-a` | `encoding-worker-ip-b` |
| Image | `encoding-worker` family | `encoding-worker` family |
| Disk | 100 GB hyperdisk-balanced | 100 GB hyperdisk-balanced |
| Default state | **Stopped** | **Stopped** |

Both use the same Packer image, same startup script, same service account. The existing single `encoding-worker` VM and its static IP are replaced by these two. Pulumi handles the transition.

### Cost

- **Idle (both stopped):** ~$10-15/mo (disk storage only; static IPs are free while attached to a VM, even if stopped)
- **One VM running:** ~$800/mo (same as today, but only while active)
- **Both running (during deploy):** ~$1,600/mo prorated for the few minutes of overlap

### Firestore Config Document

`config/encoding-worker` stores the active routing state:

```json
{
  "primary_vm": "encoding-worker-a",
  "primary_ip": "34.x.x.x",
  "primary_version": "0.152.0",
  "primary_deployed_at": "2026-03-24T16:00:00Z",
  "secondary_vm": "encoding-worker-b",
  "secondary_ip": "35.x.x.x",
  "secondary_version": "0.151.0",
  "secondary_deployed_at": "2026-03-23T12:00:00Z",
  "last_swap_at": "2026-03-24T16:00:00Z",
  "last_activity_at": "2026-03-24T15:45:00Z",
  "deploy_in_progress": false,
  "deploy_in_progress_since": null
}
```

The backend reads `primary_ip` to route encoding requests. CI updates this document atomically (Firestore transaction) to swap roles after a successful deploy.

---

## Section 2: Backend VM Lifecycle Management

Three new responsibilities for the backend:

### 2a. Warmup Trigger

When a user opens the lyrics review page, the frontend calls:

```
POST /api/internal/encoding-worker/warmup
```

The backend:
1. Reads `config/encoding-worker` from Firestore to get the primary VM name
2. Checks VM status via Compute API (`instances.get`)
3. If stopped, calls `instances.start`
4. Updates `last_activity_at` in the Firestore doc
5. Returns immediately (fire-and-forget, doesn't wait for boot)

### 2b. Activity Heartbeat

The frontend sends a heartbeat to keep the VM alive during active sessions:

```
POST /api/internal/encoding-worker/heartbeat
```

Called on:
- Lyrics review page load (same as warmup)
- Each lyrics edit autosave
- Preview button click
- While encoding is in progress (periodic)

Updates `last_activity_at` in Firestore. The idle shutdown Cloud Function (Section 4) checks this timestamp.

### 2c. Encoding Request with Warmup Fallback

The existing `EncodingService.submit_encoding_job()` gains warmup-aware retry logic:

1. Try to submit to primary VM
2. If connection refused / VM unreachable:
   - Start the VM via Compute API
   - Poll `/health` with backoff until healthy (up to ~90 seconds)
   - Retry the encoding submission
3. If VM is starting, return a structured response so the frontend can show a "warming up" indicator

**Important: URL resolution must be dynamic.** The current `EncodingService` loads the worker URL once at init time and caches it for the Cloud Run instance lifetime. This must change to read `primary_ip` from Firestore on each encoding request (or with a short TTL cache of ~30 seconds). Without this, the backend would never pick up a blue-green swap until the Cloud Run instance cold-starts.

**Authentication:** The warmup and heartbeat endpoints (`/api/internal/encoding-worker/*`) use the same `X-Admin-Token` header as other internal endpoints. The frontend includes this token in requests.

**IAM permissions needed:** The backend Cloud Run service account needs `compute.instances.get` and `compute.instances.start` on the two encoding worker VMs. This is a narrow IAM binding scoped to specific resources, not broad compute admin.

---

## Section 3: CI Blue-Green Deployment Flow

**Trigger:** A merge to `main` that changes files affecting the encoding worker (backend code, pyproject.toml, etc.).

### Deployment Sequence

```
 1. Build wheel (existing step)
 2. Upload wheel + startup.sh + version.txt to GCS (existing step)
 3. Read Firestore config. If deploy_in_progress=true, wait (poll every 30s,
    timeout after 10 min) or fail — this serializes concurrent deploys.
 4. Set deploy_in_progress=true and deploy_in_progress_since=now in Firestore
 5. Identify secondary VM name and IP from Firestore config
 6. Start secondary VM (compute.instances.start)
 7. Wait for VM to boot + service to come online (poll /health, up to 3 min)
 8. Verify wheel_version in /health response matches expected version
 9. Run deep health check: submit a real 10-second preview encode test
10. Wait for encode to complete, verify output file exists and is valid size
11. If health check FAILS:
    - Stop secondary VM
    - Set deploy_in_progress=false, deploy_in_progress_since=null
    - Fail CI with detailed error (logs, health response, etc.)
12. If health check PASSES → swap roles in Firestore (atomic transaction):
    - secondary_vm becomes primary_vm (and vice versa)
    - Update IPs, versions, timestamps
    - Set deploy_in_progress=false, deploy_in_progress_since=null
13. Check if old primary (now secondary) has active_jobs > 0
    - If yes → wait for drain (up to 10 min)
    - If no → stop it immediately
14. Stop old primary (now secondary) VM
```

### Key Properties

- **Zero-downtime:** Old primary keeps serving throughout steps 3-10. Traffic only switches at step 11.
- **Broken deploys never reach users:** If the encode test fails at step 9, the secondary is stopped and CI fails. Primary is untouched.
- **Atomic swap:** Firestore transaction in step 11 means the backend sees either old state or new state, never partial.
- **Rollback is trivial:** Swap the Firestore doc back. The old primary still has the previous version installed.

### Deep Health Check Details

The encode test validates that the full encoding pipeline works, not just that the HTTP server responds:

1. CI uploads a small test ASS subtitle file + short audio clip to a known GCS test path
2. Submits an encode request directly to the secondary VM's IP (bypasses Firestore routing)
3. Waits for the job to complete (~10-15 seconds for a minimal preview)
4. Verifies the output video file exists and has a valid size (> 0 bytes)
5. Cleans up test artifacts from GCS

This catches issues like:
- pip install failures (PR #587)
- Missing system dependencies (FFmpeg, fonts)
- Python import errors
- GCS permission issues
- Broken encoding logic

### CI Authentication

The GitHub Actions service account needs:
- `compute.instances.start`, `compute.instances.stop`, `compute.instances.get` on the two VMs
- Firestore read/write access to `config/encoding-worker`
- GCS read/write for test artifacts (already has this)

---

## Section 4: Idle Auto-Shutdown

A Cloud Function triggered by Cloud Scheduler every 5 minutes.

### Logic

```python
for each VM in [primary, secondary]:
    if VM is not running:
        continue

    # Check all "keep alive" signals
    health = GET VM:8080/health
    if health.active_jobs > 0:
        continue  # Job in flight, don't stop

    config = read Firestore config/encoding-worker
    if config.last_activity_at > (now - 15 minutes):
        continue  # User in active session

    if config.deploy_in_progress:
        # Check for stale flag (CI might have crashed)
        if config.deploy_in_progress_since < (now - 20 minutes):
            # Stale flag (deploy started over 20 min ago), clear it
            update config: deploy_in_progress = false, deploy_in_progress_since = null
        else:
            continue  # Deploy is actively running, leave it alone

    # No active jobs, no recent activity, no deploy in progress
    compute.instances.stop(VM)
```

### Why a Cloud Function (Not Self-Shutdown on the VM)

- The VM process might crash or hang — an external watcher is more reliable
- If encoding worker code has a bug preventing self-shutdown, you pay ~$800/mo until someone notices
- Cloud Function cost: effectively $0/mo (free tier covers this volume)

### Idle Timeout Summary

| Signal | Keeps VM alive? |
|--------|----------------|
| `active_jobs > 0` (from /health) | Yes |
| `last_activity_at` < 15 min ago (from Firestore) | Yes |
| `deploy_in_progress == true` (from Firestore) | Yes |
| None of the above | Stop the VM |

---

## Section 5: Frontend Changes

Two small changes to the frontend:

### 5a. Warmup Signal on Lyrics Review

When the user navigates to the lyrics review page (`/app/jobs#/{jobId}/review`), the frontend fires a warmup request:

```typescript
// Fire-and-forget on page load, no loading state needed
fetch('/api/internal/encoding-worker/warmup', { method: 'POST' })
```

And sends a debounced heartbeat on interaction:

```typescript
// At most once per 60 seconds, on autosave or user interaction
fetch('/api/internal/encoding-worker/heartbeat', { method: 'POST' })
```

### 5b. "Warming Up" State in Preview UI

When the user clicks the preview button and the encoding worker is still booting, the backend returns a response indicating the worker is starting. The frontend shows a message in the preview area:

> "Starting encoding worker... This usually takes about a minute."

Once the backend confirms the worker is ready and the encode job is submitted, this transitions to the normal encoding progress UI.

The existing encoding submission endpoint returns a new status value `worker_starting` (alongside the existing `pending`, `running`, etc.). The frontend preview component handles this state with the warmup message.

---

## Rollback Plan

### Immediate Rollback (Bad Deploy)

If a deployment passes the health check but causes issues in production:
1. Swap the Firestore config doc back (old primary becomes primary again)
2. Start the old primary VM if it's stopped
3. Can be done via a manual script, admin endpoint, or `gcloud` + Firestore console

### Full Rollback (Infrastructure)

If the two-VM architecture itself has issues:
1. Revert the backend code to read `encoding-worker-url` from Secret Manager (code revert, not automatic fallback)
2. Pulumi can recreate the original single-VM setup
3. CI reverts to the current deploy flow

### Versioned Wheels

Previous versioned wheels remain in GCS (`karaoke_gen-{version}.whl`) for manual rollback to any prior version.

---

## Migration Plan

The transition from one VM to two requires careful ordering:

1. **Seed Firestore config doc** — initial state with VM A's IP set to the current encoding worker's static IP, so there's no gap where the backend reads from Firestore but no VM exists yet
2. **Deploy backend changes** (read primary from Firestore, warmup/heartbeat endpoints)
3. **Deploy Pulumi changes** (create VMs A and B, remove old VM)
4. **Deploy CI workflow changes** (blue-green deploy flow)
5. **Deploy Cloud Function** (idle auto-shutdown)
6. **Deploy frontend changes** (warmup signal, warming up UI)

Steps 1-3 must happen in order. Steps 4-6 can happen in parallel after step 3.

---

## Open Questions

None — design is complete and approved.
