# Encoding Worker Capacity Resilience — Session Summary (May 5–6 2026)

This session shipped the 3-phase capacity-resilience plan from
[2026-05-05-encoding-worker-capacity-resilience-plan.md](2026-05-05-encoding-worker-capacity-resilience-plan.md)
plus four follow-up fixes that surfaced once the system was exercised under
real production load.

This document records the **full arc** — the bugs we *thought* we were fixing,
the bugs we *actually* hit, and the final state — so future sessions can
understand why each piece exists.

## The trigger

Two real-user jobs failed with empty error messages: `Video render failed: ` (literally nothing after the colon).

- `fbc651be` — The Corrs / Breathless — `arnoldespinojr@gmail.com`
- `bee150fd` — Singing Melody / Want You Back — `nivekson@gmail.com`

Investigation found **`ZONE_RESOURCE_POOL_EXHAUSTED`** in `us-central1-c` (out of `c4d-highcpu-32` capacity). The application's fire-and-forget `compute.instances.start()` call swallowed the GCE error completely; the readiness gate then timed out 120 s later; the HTTP retry loop ran for 7 more minutes and re-raised a bare `TimeoutError()` whose `str()` is empty.

## What shipped

### PR #748 — Capacity resilience (Phases 1-3)

- New typed exception hierarchy: `EncodingWorkerStartError` (base), `EncodingWorkerCapacityError` (subclass for `ZONE_RESOURCE_POOL_EXHAUSTED` / `STOCKOUT` / `QUOTA_EXCEEDED`)
- `EncodingWorkerManager.start_vm` waits for the operation result and raises typed errors instead of fire-and-forget
- New `RENDER_PENDING_CAPACITY` job state — transient capacity issues park here instead of going to `FAILED`
- `repr(e)` fallback in render worker's exception handler so empty-message errors no longer produce blank user messages
- New `/api/internal/retry-pending-render-jobs` endpoint + Cloud Scheduler firing every 5 min, 24 h hard timeout
- Multi-zone failover: `EncodingWorkerCandidate` list iterated in `ensure_any_running`, capacity errors fall through to next candidate, success on a non-primary sets `active_override_*` on the Firestore config doc
- IaC: 2 fallback VMs added (originally `-a` and `-f`) — provisioned stopped, ~$10/mo each idle

### PR #749 — Switch fallback `-f` → `-b`

`pulumi up` failed creating the `-f` VM with — fittingly — `ZONE_RESOURCE_POOL_EXHAUSTED`. Capacity in `us-central1-f` for `c4d-highcpu-32` is too tight to be useful as a fallback. `-b` is reliable.

### PR #750 — Three follow-up fixes

1. **503 SERVICE_UNAVAILABLE didn't trigger fallback.** Multi-zone fallback only iterated on `EncodingWorkerCapacityError`. When GCE returned a transient `503` from `instances.start`, the typed exception was the parent `EncodingWorkerStartError` and the loop bailed instead of trying the next zone. Broadened to catch the parent class.
2. **Fallback VMs failed with `SSL CERTIFICATE_VERIFY_FAILED`** on metadata-server access. `google-auth ≥ 2.40` uses mTLS-over-HTTPS to the metadata server when `/run/google-mds-mtls/` certs exist on the VM, which is the case on newer GCE images. Set `GCE_METADATA_MTLS_MODE=none` in the systemd EnvironmentFile to force HTTP.
3. **`/retry` endpoint screens-existence check used wrong key.** Looked for `file_urls.screens.title` but the actual structure has `title_jpg`/`title_png`. Routed completed-review jobs back to `awaiting_review` forcing user re-work.

Plus: progress callback was calling `transition_to_state(RENDERING_VIDEO)` while already in that state, polluting the timeline + error-monitor with `Invalid state transition` log spam.

### PR #751 — Three more

1. **`wait_for_worker_ready` looked in the wrong zone.** Multi-zone fallback successfully started a VM in `us-central1-a` but the readiness wait polled `us-central1-c` (the manager's default zone), got 404, gave up. Threaded the candidate's zone through.
2. **`/retry-pending-render-jobs` returned 500 — composite index needed.** Query was `where(status) + order_by(updated_at)`. Fix: stream by status alone, sort in Python (small result set).
3. **Cloud Run JOB (video-encoding-job) was missing the `ENCODING_WORKER_FALLBACK_VMS` env var.** Cloud Run service had it; the JOB instantiates its own `EncodingService` so it needed the secret too. Without it the final encoding step couldn't fall back when `-c` was exhausted.

### PR #752 — URL re-resolve

`_request_with_retry` captured the URL once before the retry loop. When the warmup-fallback updated the active URL override (switching from a dead primary to a live fallback), the retry loop kept hitting the original (dead) URL for all 8 attempts and raised a generic `TimeoutError` not caught by the typed-error path → job hard-failed instead of being parked. Threaded the relative `path` through and re-resolve `_get_worker_url() + path` on retries after warmup has fired.

This was the bug that surfaced on `fae3eadc` after PR #751 deployed — multi-zone fallback engaged correctly, set the override, but the URL never updated mid-loop.

## The recovery flow today

```
Render fails → typed error classification
  ├─ EncodingWorkerCapacityError or EncodingWorkerStartError
  │      ↓
  │   ensure_any_running iterates candidates: [primary -c, fallback -a, fallback -b]
  │      ↓
  │      ├─ Any candidate succeeds → set active_override, invalidate URL cache,
  │      │                            URL re-resolves on retry → request hits live VM ✓
  │      └─ All candidates rejected → typed error propagates to render worker
  │              ↓
  │           render worker parks job in RENDER_PENDING_CAPACITY (NOT failed)
  │              ↓
  │           Cloud Scheduler retries every 5 min via /retry-pending-render-jobs
  │              ↓
  │           24 h hard timeout → transitions to FAILED with permanent message
  │
  └─ Other Exception → repr(e) fallback so message is informative → fail_job
```

## Hardening opportunities

The 3 items below were identified during the May 5–6 session. Status updated as each ships.

1. **Render worker doesn't handle SIGTERM gracefully.** ✅ **Shipped in v0.174.4** — render worker registers with `worker_registry`; FastAPI shutdown waits up to 480s, then calls `park_active_render_jobs_for_shutdown()` which transitions any still-active render jobs to `RENDER_PENDING_CAPACITY` (last_code: `WORKER_SHUTDOWN`) before exit. Auto-retry scheduler picks them up within 5 min. Reduced wait from 600s → 480s leaves 120s for cleanup before SIGKILL.
2. **Encoding worker loses in-memory job state on systemctl restart.** ✅ **Shipped in v0.174.6** — `JobStatePersister` mirrors `jobs[]` to Firestore collection `encoding_worker_jobs`, tagged with `vm_name` from `ENCODING_WORKER_VM_NAME` env var (set by startup.sh from GCE metadata). FastAPI lifespan startup loads any in-progress jobs and marks them `failed` with `restart_failure_code: encoding_worker_restart` (work_dir is gone, can't resume). Polls after restart now return clear failure instead of 404. Datastore TTL set to 7 days for cleanup. Best-effort writes — Firestore failures log a warning but don't fail encoding. Self-disables on PermissionDenied so log noise stays bounded if IAM lags the deploy. Requires `roles/datastore.user` on `encoding-worker-sa` (added in IAM module).
3. **Concurrent renders against a single fallback VM cause `Connection reset by peer`.** ✅ **Shipped in v0.174.5** — `EncodingService._submission_slot()` adds a per-URL `asyncio.Semaphore` (default cap 3, configurable via `ENCODING_SUBMISSION_CONCURRENCY` env var). Wraps `render_video_on_gce` and `encode_videos` for the full submit + wait cycle so the worker isn't asked to encode more than 3 jobs in parallel from one Cloud Run instance. Different URLs (primary vs fallback) have separate semaphores so multi-zone fallback isn't serialized. Cap leaves 1 slot of the worker's 4-thread pool free for status polls / previews. **Caveat:** this is per-Cloud-Run-instance, not global — with N instances each contributing 3, a single worker VM can still see 3N concurrent. Acceptable for current scale; revisit if multi-instance fallback storms recur.

## Operational reference

**VM topology (all idle by default):**
```
encoding-worker-a            us-central1-c   primary blue
encoding-worker-b            us-central1-c   primary green
encoding-worker-fallback-a   us-central1-a   capacity fallback
encoding-worker-fallback-b   us-central1-b   capacity fallback
```

**Static IPs:** see Pulumi outputs `encoding_worker_*_ip`.

**Configuration:**
- `ENCODING_WORKER_FALLBACK_VMS` env (Cloud Run service + video-encoding-job): JSON list `[{"vm","zone","ip"}, ...]`. Stored in Secret Manager (`encoding-worker-fallback-vms`).
- `GCE_METADATA_MTLS_MODE=none` in encoding worker `/opt/encoding-worker/env` (written by `infrastructure/encoding-worker/startup.sh` on every VM boot)
- Cloud Scheduler `retry-pending-render-jobs` runs `*/5 * * * *`
- Cloud Scheduler `recover-stuck-downloads` runs `*/5 * * * *` (existing; covers a different stage)

**Jobs that hit and recovered from these bugs (May 5-6 2026):**

The 2 originals (`fbc651be`, `bee150fd`) recovered organically. 9 others were explicitly retried during the session: `6b0bf198`, `052b94ab`, `0aefabf9`, `693c2254`, `ea83ccc6`, `549cdf90`, `3722ed7e`, `faafd5ad`, `9561402a`, `c84fbd76`, `fae3eadc`. All ended at `complete`.
