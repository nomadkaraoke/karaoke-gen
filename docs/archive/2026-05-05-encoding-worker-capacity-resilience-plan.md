# Encoding Worker Capacity Resilience — Plan

**Date:** 2026-05-05
**Worktree:** `karaoke-gen-investigate-job-failures`
**Branch:** `feat/sess-20260505-1805-investigate-job-failures`
**Trigger:** Jobs `fbc651be` and `bee150fd` failed with empty error message `Video render failed: `

## Background

Investigation of two failed jobs found:

1. Both jobs failed at the GCE encoding-worker render stage with no usable error message
2. Audit logs show the underlying cause was `ZONE_RESOURCE_POOL_EXHAUSTED` (`c4d-highcpu-32` in `us-central1-c`) — GCP could not provision the VM at the time of request
3. The current code in `EncodingWorkerManager.start_vm` is fire-and-forget (does not inspect the operation result), so the GCP error is invisible
4. The cold-start readiness gate then waits 120 s for the VM to reach `RUNNING`, gives up, raises a `TimeoutError` whose message is silently swallowed by `_warmup_encoding_worker_fallback`
5. The HTTP retry loop (8 × 45 s ≈ 7 min) eventually re-raises an unrelated `asyncio.TimeoutError()` with no message → user sees `"Video render failed: "` (blank)
6. 10 such failures over the previous 10 days, all `encoding-worker-b` in `us-central1-c`. The capacity issue is intermittent — the same VM starts successfully in surrounding hours

## Goals

1. **Phase 1 — Surface real errors**: capture GCP operation results, classify capacity vs other failures, propagate a user-friendly message that explicitly says "this is likely a temporary capacity issue, retrying later will probably succeed"
2. **Phase 2 — Periodic auto-retry**: jobs blocked on capacity should be parked in a recoverable state and automatically retried on a schedule until they succeed (or hit a long deadline like 24 h)
3. **Phase 3 — Multi-zone failover**: when one zone is exhausted, try other `us-central1` zones (`a`, `b`, `f` — confirmed all support `c4d-highcpu-32`)

## Non-goals

- Switching the machine type (premature optimization without data on whether other families have better availability)
- Migrating to a Managed Instance Group (large architectural change; bigger blast radius)
- Cross-region failover (out of scope; latency + data egress concerns)

---

## Phase 1 — Surface real errors

### 1A. `EncodingWorkerManager` — wait for operation, classify errors

`backend/services/encoding_worker_manager.py`

- Add typed exceptions in this file (or new `encoding_errors.py`):
  ```python
  class EncodingWorkerStartError(Exception): pass
  class EncodingWorkerCapacityError(EncodingWorkerStartError):
      """ZONE_RESOURCE_POOL_EXHAUSTED or similar capacity-related GCE error."""
  ```
- `start_vm()` and `ensure_primary_running()`:
  - Capture the `Operation` returned by `instances().start()`
  - Poll the operation (or use the SDK's `wait_for_operation` helper) up to ~30 s
  - On completion, inspect `operation.error.errors[*].code`:
    - `ZONE_RESOURCE_POOL_EXHAUSTED`, `STOCKOUT`, `QUOTA_EXCEEDED` → raise `EncodingWorkerCapacityError("zone <zone> exhausted for <type>")`
    - any other error → raise `EncodingWorkerStartError(error_message)`
  - Update `ensure_primary_running()`'s return contract (or add separate method) so callers know whether the start succeeded vs failed

### 1B. `_warmup_encoding_worker_fallback` — propagate capacity errors

`backend/services/encoding_service.py`

- Stop blanket-suppressing `Exception` in the warmup fallback
- Re-raise `EncodingWorkerCapacityError` so the caller can react
- Log other start errors with `_format_exception` (already used elsewhere) and continue retrying (existing behavior)

### 1C. `_request_with_retry` — fail fast on capacity errors

- If `_warmup_encoding_worker_fallback` raised `EncodingWorkerCapacityError`, bail out of the retry loop immediately and re-raise (no point retrying the HTTP call 7 more times to a VM that will never come up)

### 1D. New job status + render worker handling

`backend/models/job.py`

- Add `RENDER_PENDING_CAPACITY = "render_pending_capacity"` to `JobStatus`
- Add allowed transitions:
  - `RENDERING_VIDEO → RENDER_PENDING_CAPACITY`
  - `REVIEW_COMPLETE → RENDER_PENDING_CAPACITY`
  - `RENDER_PENDING_CAPACITY → RENDERING_VIDEO` (auto-retry succeeded)
  - `RENDER_PENDING_CAPACITY → FAILED` (long timeout)
  - `RENDER_PENDING_CAPACITY → CANCELLED` (user cancelled)

`backend/workers/render_video_worker.py`

- Wrap the GCE call. On `EncodingWorkerCapacityError`:
  - Do NOT call `fail_job`
  - Transition to `RENDER_PENDING_CAPACITY` with message *"Encoding capacity is temporarily unavailable. Your job will retry automatically — no action needed. Most jobs recover within 5–30 minutes."*
  - Persist `state_data.render_pending_capacity = { first_seen_at, last_attempt_at, attempt_count }` so phase 2 can track and time-out

### 1E. Frontend / API surface

- `GET /api/jobs/<id>` already returns `status` + `error_message` — frontend will pick up the new state via existing polling. Confirm the dashboard shows the message clearly (status row + a non-error styling so it doesn't look like a hard failure)
- Confirm the user-facing job page surfaces the message under the existing "Status" badge

### 1F. Tests

- Unit: `EncodingWorkerManager.ensure_primary_running` raises `EncodingWorkerCapacityError` when the operation completes with `ZONE_RESOURCE_POOL_EXHAUSTED`
- Unit: `_warmup_encoding_worker_fallback` re-raises `EncodingWorkerCapacityError`
- Unit: `_request_with_retry` aborts immediately when warmup raises capacity error
- Unit: render worker transitions to `RENDER_PENDING_CAPACITY` with the user-friendly message and does NOT call `fail_job`

---

## Phase 2 — Periodic auto-retry

### 2A. New internal endpoint

`backend/api/routes/internal.py`

- `POST /api/internal/retry-pending-render-jobs` (admin-auth, used by Cloud Scheduler)
- Logic:
  1. Query `jobs` collection where `status == "render_pending_capacity"`
  2. For each job:
     - If `now - state_data.render_pending_capacity.first_seen_at > MAX_CAPACITY_WAIT (24h)`: transition to `FAILED` with permanent-failure message, increment `error_details.permanent_capacity_timeout`, send the operator a Discord ping
     - Else: increment `attempt_count`, update `last_attempt_at`, kick `worker_service.trigger_render_video_worker(job_id)` (the render worker already idempotently no-ops on `render_progress.stage == 'complete'`, and starts the GCE VM via `_warmup_encoding_worker_fallback` when needed)
- Avoid the thundering herd: if there are many jobs pending, process one at a time per scheduler tick (the GCE worker is single-tenant per render). Process up to 1 job per tick; the scheduler runs every 5 min so worst case is 12 jobs/hour throughput, plenty for current volume

### 2B. Cloud Scheduler

`infrastructure/__main__.py`

- Add new `cloudscheduler.Job` (mirror `recover-stuck-downloads-scheduler`):
  - `schedule = "*/5 * * * *"`
  - `uri = "https://api.nomadkaraoke.com/api/internal/retry-pending-render-jobs"`
  - OIDC token from `backend_service_account`
  - retry config: 1 retry, 60 s min / 300 s max backoff

### 2C. Tests

- Unit: endpoint queries Firestore correctly, processes 1 job, schedules render
- Unit: endpoint times out a job after 24 h (transition to FAILED with permanent message)
- Unit: empty queue → no-op

---

## Phase 3 — Multi-zone failover

`c4d-highcpu-32` is available in all four `us-central1` zones (`a`, `b`, `c`, `f`). Pre-creating fallback VMs in alternate zones gives us a safety pool for capacity exhaustion.

### 3A. Infrastructure — fallback VMs

`infrastructure/config.py`

- Extend `EncodingWorkerConfig`:
  ```python
  VM_NAMES = ["encoding-worker-a", "encoding-worker-b"]
  IP_NAMES = ["encoding-worker-ip-a", "encoding-worker-ip-b"]
  FALLBACK_VM_NAMES = ["encoding-worker-fallback-a", "encoding-worker-fallback-f"]
  FALLBACK_IP_NAMES = ["encoding-worker-fallback-ip-a", "encoding-worker-fallback-ip-f"]
  FALLBACK_ZONES = [f"{REGION}-a", f"{REGION}-f"]
  ```

`infrastructure/compute/encoding_worker_vm.py`

- New helper `create_encoding_worker_fallback_vms(...)` analogous to `create_encoding_worker_vms` but iterating zones
- Same custom image, same SA, same firewall tag — these reuse all existing IaC plumbing
- Cost note: VMs stopped at idle cost only the boot disk (100GB hyperdisk-balanced ≈ $10/mo each → +$20/mo total). Acceptable insurance

### 3B. Encoding worker manager — multi-VM iteration

`backend/services/encoding_worker_manager.py`

- Replace `ensure_primary_running()` with `ensure_any_running()`:
  - Tries primary VM (`config.primary_vm`) first
  - On `EncodingWorkerCapacityError`, tries fallback VMs in order
  - Returns `(vm_name, primary_url)` of whichever started successfully, OR raises `EncodingWorkerCapacityError` if all zones are exhausted
- Update Firestore config doc to track `active_vm_name` so subsequent calls hit the right URL until idle-shutdown brings everything back to base state

### 3C. Idle shutdown

- Existing `encoding-worker-idle@nomadkaraoke.iam.gserviceaccount.com` stops VMs after idle. Confirm it covers fallback VMs (probably uses a tag-based filter; if not, expand)

### 3D. Tests

- Unit: `ensure_any_running` falls through to fallback when primary raises capacity error
- Unit: `ensure_any_running` raises capacity error when ALL zones exhausted
- Integration: simulate primary capacity exhaustion → fallback VM is selected

---

## Rollout

- Phase 1 ships first as a single PR — purely backend, low-risk; recovers visibility on the failure mode immediately
- Phase 2 ships as a second PR after Phase 1 is in prod (because Phase 2 depends on the new `RENDER_PENDING_CAPACITY` state existing in prod data)
- Phase 3 ships as a separate PR — touches IaC; requires `pulumi up` and one-time provisioning. Higher blast radius. Sequenced after Phase 2

## Operational notes for Phase 3

After this PR merges:

1. Run `pulumi up` from `infrastructure/`. This provisions:
   - `encoding-worker-fallback-a` (us-central1-a) + static IP `encoding-worker-fallback-ip-a`
   - `encoding-worker-fallback-f` (us-central1-f) + static IP `encoding-worker-fallback-ip-f`
   - Both VMs created in TERMINATED state. Cost when idle: ~$10/mo each (boot disk only).
2. Capture the Pulumi-output IPs (`encoding_worker_fallback_a_ip`, `encoding_worker_fallback_f_ip`).
3. Add an env var to the `karaoke-backend` Cloud Run service:
   ```
   ENCODING_WORKER_FALLBACK_VMS=[
     {"vm":"encoding-worker-fallback-a","zone":"us-central1-a","ip":"<ip-a>"},
     {"vm":"encoding-worker-fallback-f","zone":"us-central1-f","ip":"<ip-f>"}
   ]
   ```
   (single-line JSON; brackets shown above for clarity).
4. Verify the encoding-worker custom image (`projects/nomadkaraoke/global/images/family/encoding-worker`) is up to date so the fallback VMs run the same code as primary. Future deploys will need to update fallback VMs too — track this in the deploy script as a follow-up.
5. Until the env var is set the application uses single-zone behavior (Phase 1+2 only). The fallback VMs are inert until configured.

## Open questions (will use best judgment for these)

- Should the user be allowed to cancel a `RENDER_PENDING_CAPACITY` job? **Yes** — same UX as cancelling any other in-progress job; treat the state as "in progress, blocked" not "stuck".
- Should we send a Discord notification to the operator when a job hits capacity? **No** for the first time (too noisy if there's a 30-min capacity outage with N jobs). **Yes** when a job hits the 24h permanent-failure threshold.
- What's the retry cadence — should it back off (1m, 5m, 10m, 30m)? **No** — a flat 5-min cadence keeps the implementation simple and is fine for the current volume. Easy to revisit if needed.
- Should we increment `retry_count`? **No** — `retry_count` reflects user-initiated retries. Auto-retries should not count against any user-facing retry budget.
