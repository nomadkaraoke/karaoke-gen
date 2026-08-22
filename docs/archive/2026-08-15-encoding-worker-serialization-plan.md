# Plan: Serialize encoding-worker jobs (OOM fix) + resubmit lost renders

## Context

**Incident (2026-08-15 ~17:57 UTC):** Three Arctic Monkeys renders (`a3f340a2`, `374dec26`,
`590630c0`) all failed within ~2 min with `lost contact with worker after 5 consecutive poll
failures: Encoding job … not found`.

**Root cause (confirmed from live forensics):**
- Both c4d primary encoding workers (`encoding-worker-a/b`) are TERMINATED (c4d Spot stockout in
  us-central1 — a recurring known issue). All render traffic funnels onto the single running
  fallback `encoding-worker-fallback-n2f` (n2-highcpu-32, **32 GB** — half the c4d's 60 GB).
- The worker runs `ThreadPoolExecutor(max_workers=4)` → up to 4 heavy ffmpeg encodes in parallel.
  The Arctic Monkeys batch put **3 concurrent 4K encodes** on n2f (~17.9 + ~10.6 + ~2 GB ≈ 30 GB).
- **17:57:36 kernel OOM-killer fired** (`global_oom, task=ffmpeg`) → systemd restarted
  `encoding-worker.service` (`NRestarts=1`) → the in-memory `jobs` dict was wiped → every poll
  returned `404 not found` → all three renders failed. No resubmit happened.

**Why the existing safety nets didn't catch it:**
- `ThreadPoolExecutor(max_workers=4)` is the only concurrency gate; nothing bounds it by memory.
  ffmpeg already saturates all cores for one job, so parallel encodes add **no throughput** — only
  multiplied memory. (active_url points at a single worker at a time, so the fleet was already
  1-worker-wide; serializing to 1-at-a-time costs no real throughput today.)
- The worker's restart-recovery (`persistence.mark_orphans_failed_on_startup`) is meant to convert a
  post-restart poll into a clean `failed` with `restart_failure_code`, but it relies on Firestore
  persistence that is likely disabled on the fallback VM (IAM) — so polls got a raw 404 instead.
- The client (`wait_for_completion`) treats a 404 the same whether the job never existed or vanished
  mid-run; after 5 polls it gives up with "lost contact" and **never resubmits**.

**Desired outcome (per user):** the encoding worker processes **one heavy job at a time** (queue the
rest), and the orchestrator **retries on error** so a worker restart/OOM/deploy no longer loses a
render. Scope: worker serialization + client resubmit now; multi-worker load-balancing is a
follow-up.

## Key files & current behavior

- `backend/services/gce_encoding/main.py` — the GCE worker FastAPI app.
  `executor = ThreadPoolExecutor(max_workers=4)` (line 32). `process_job` (encode, ~1075),
  `process_render_video_job` (render, ~1184), `process_preview_job` (preview, ~1154) all call
  `run_in_executor(executor, …)`. `/encode`, `/render-video`, `/encode-preview`, `/status`,
  `/health`. Jobs tracked in in-memory `jobs` dict; `queue_length`/`active_jobs` already derived
  from `pending`/`running` status.
- `backend/services/gce_encoding/persistence.py` — `JobStatePersister`;
  `mark_orphans_failed_on_startup` sets `restart_failure_code = "encoding_worker_restart"`.
- `backend/services/encoding_service.py` — client. `wait_for_completion` (~595) with
  `MAX_CONSECUTIVE_POLL_FAILURES` tolerance; `get_job_status` raises `RuntimeError("… not found")`
  on 404 (589); `encode_videos` (~693) submit+wait.
- `backend/workers/video_worker_orchestrator.py` — `encoding_backend.encode()` call (~498) and the
  existing **stale-cache resubmit** block (521-548) that generates `{job_id}_retry_{hex8}` — the
  pattern to generalize.

## Approach

### 1. Worker: split into a serialized heavy lane + a light lane (the OOM fix)
In `main.py`, replace the single pool with two:
- `HEAVY_CONCURRENCY = int(os.getenv("ENCODING_HEAVY_CONCURRENCY", "1"))`;
  `heavy_executor = ThreadPoolExecutor(max_workers=HEAVY_CONCURRENCY)` — used **only** for the
  heavy ffmpeg work: `run_encoding` (in `process_job`) and `render_video` (in
  `process_render_video_job`). Default 1 = one heavy job at a time on every machine type → OOM-proof.
- `light_executor = ThreadPoolExecutor(max_workers=2)` — used for `ensure_latest_wheel` (pip, light)
  and `run_preview_encoding` (interactive previews). Keeps review snappy and never blocks wheel
  installs behind a long encode. (1 heavy ~18 GB + a preview ~2 GB + base ≈ 22 GB < 32 GB.)

Submitted-but-not-yet-started heavy jobs remain `status="pending"` (existing behavior) → the
`heavy_executor` work queue **is** the encode queue; `/health` `queue_length` already surfaces it.
Add `queue_position` to the `/status` response (cheap: index among `pending`+`running` heavy jobs)
so clients can wait through the queue without tripping the encode timeout (see #3).

### 2. Worker: expose the restart code so the client can act on it
Add optional `restart_failure_code: Optional[str]` (and `queue_position: Optional[int]`) to the
`JobStatus` pydantic model (line 115) and include them in the `/status` payload. `persistence` already
writes `restart_failure_code` into the job dict on restart-recovery.

### 3. Client: detect a *lost* job vs a transient blip; wait through the queue
In `encoding_service.py` `wait_for_completion`:
- Track "have we seen this job run" (any successful poll, or `status in {running, complete}` /
  `progress > 0`). If a `404 not found` arrives **after** the job was seen, raise a new
  `EncodingJobLostError` **immediately** (the job was wiped — don't burn 5 polls, don't mislabel as
  unrecoverable). A 404 *before* the job is ever seen keeps the current 5-poll tolerance (submit/poll
  race).
- If a terminal `status=="failed"` carries `restart_failure_code=="encoding_worker_restart"`, raise
  `EncodingJobLostError` too.
- While `status=="pending"` (queued, not yet started), keep polling without counting toward the
  encode `timeout` — only start the encode clock once the job is `running`. This makes deep queues
  safe under concurrency=1 without an enormous fixed timeout (cap total queue wait separately, e.g.
  a generous `queue_timeout`).

### 4. Orchestrator: bounded resubmit on a lost job
In `video_worker_orchestrator.py`, wrap the `encoding_backend.encode()` call (~498) in a bounded loop
(`ENCODING_RESUBMIT_MAX`, default 2 extra attempts): on `EncodingJobLostError`, mint a fresh
`{job_id}_retry_{hex8}` (reuse the 525-527 pattern), log, and resubmit. Generalize the existing
stale-cache block (521-548) so the lost/restart and stale-cache cases share one resubmit path. This
is the "retry on error" safety net and works **even if worker persistence is disabled** (the client
detects the vanished job itself).

### 5. Infra (supporting, verify-then-fix): fallback-VM Firestore IAM
Confirm whether `encoding_worker_jobs` writes are actually failing on `encoding-worker-fallback-n2f`
(persister self-disables on IAM error). If so, grant the fallback VMs' service account Firestore
write via Pulumi so restart-recovery produces a clean `failed`+`restart_failure_code` there too.
Belt-and-suspenders behind #3/#4. Read-only verify first; only a Pulumi PR if broken.

### 6. Step 0 — immediate operational recovery (run on approval, before/independent of the ship)
Re-render the three stuck jobs via `POST /api/jobs/{id}/retry` (admin token; resumes from the render
stage since review is complete), **serialized** — one at a time, waiting for each to reach a terminal
state and checking worker load between — because the deployed worker still runs concurrency=4 until
the fix ships. Order: `a3f340a2`, `374dec26`, `590630c0`.

## Tests
- **Worker** (`backend/tests/test_gce_encoding_*` / new): with `run_encoding` stubbed to block,
  submit 3 `/encode` jobs and assert only 1 is `running` while the rest are `pending` (serialized);
  assert a `/encode-preview` runs concurrently with a heavy job (light lane); assert `/status`
  returns `queue_position`.
- **Client** (`backend/tests/test_encoding_service.py`): `wait_for_completion` raises
  `EncodingJobLostError` on 404-after-seen and on `restart_failure_code`; still tolerates 404s
  before first success (keep existing poll-failure test green); pending status doesn't trip the
  encode timeout.
- **Orchestrator** (`backend/tests/test_video_worker_orchestrator*`): a lost job triggers a resubmit
  with a new `_retry_` id and then succeeds; resubmit attempts are bounded.

## Verification (end-to-end)
1. `make test 2>&1 | tail -n 500` — all green.
2. Deploy the worker wheel; `curl /health` shows the new version and `queue_length`.
3. **Real-world repro:** submit the 3 Arctic Monkeys renders **concurrently** (the exact OOM
   trigger). Expect: one `running`, others `pending`; peak RSS bounded (~1 heavy ffmpeg);
   `journalctl -k` shows **no OOM**; capture `systemctl show encoding-worker -p NRestarts`
   *before* the run and assert it does **not increase** (the worker already carries a
   nonzero `NRestarts` from the incident); all three complete. This doubles as Step 0
   recovery once the fix is live.
4. Simulate a mid-render worker restart (`systemctl restart encoding-worker` during an encode) and
   confirm the orchestrator resubmits (`_retry_` id in logs) and the job completes instead of failing.

## Out of scope (follow-ups)
- Fleet-level load-balancing / dispatching queued jobs across multiple running workers (the throughput
  lever once c4d capacity or additional n2 workers are added).
- Restoring c4d primary capacity / machine-family strategy (tracked separately —
  `project_gen_encoding_machine_family_diversity`).
