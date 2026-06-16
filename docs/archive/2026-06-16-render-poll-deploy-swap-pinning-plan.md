# Render status-poll pinning — fix for deploy-swap orphaning (job d3af33ae)

**Date:** 2026-06-16
**Worktree:** `karaoke-gen-encoding-job-not-found`
**Incident error:** `Encoding job <ID> lost contact with worker after 5 consecutive poll failures: Encoding job <ID> not found`

## What happened (job d3af33ae, 2026-06-16 UTC)

| Time | Event |
|------|-------|
| 04:29:09 | `karaoke-backend@` starts **worker-a** (`34.57.78.246`) — render warmup |
| ~04:31:39 | Render submitted to **worker-a**; encoding begins |
| 04:33:01 | `github-actions-deployer@` starts **worker-b** (`34.10.189.118`) — deploy begins |
| **04:33:46.985** | Firestore `last_swap_at`: primary swapped worker-a → **worker-b** |
| 04:33:53 → 04:34:33 | Backend status polls fail 1/5 → 5/5: "Encoding job d3af33ae not found" |
| 04:34:33 | Render failed; deploy stops worker-a |
| 04:46:35 | User retried → re-rendered on worker-b → **success** |

The 4 polls that failed at 04:33:53–04:34:23 happened **while worker-a was still running and encoding**. The 404s came from worker-b (the just-swapped primary), which never received the job.

## Root cause

`EncodingService.get_job_status()` resolves the worker URL **fresh on every poll** via
`_get_worker_url()` → `config.active_url`. When a blue-green deploy flips `primary_vm` in
Firestore mid-render, in-flight status polls migrate from the worker that owns the job
(old primary) to the new primary, which 404s. A clean 404 is counted as a transient poll
failure; 5 in a row fails the render — even though the original worker finished the encode.

URL re-resolution is **correct for capacity fallback** (primary VM dead → start fallback →
re-route), but **wrong for a deploy swap** (old primary alive, still owns the job).

The drain logic added 2026-06-15 (`ci.yml` "Drain and stop old primary") prevents *stopping*
a busy VM, but the job is orphaned at the **poll layer** the instant the pointer swaps —
47s before the VM is stopped. The drain can't fix this.

## Fix: pin in-flight polls to the worker that accepted the job

Invariant: **once a job is accepted by a worker VM, all status polls for that job target
that same VM until terminal** — the poll follows the job, not the floating primary pointer.

### Changes (`backend/services/encoding_service.py`)

1. **`_request_with_retry(..., allow_failover: bool = True)`** — gate the warmup-on-failure
   block and the post-warmup URL re-resolution on `allow_failover`. Default `True` keeps all
   existing submit callers (and capacity fallback) unchanged.

2. **`get_job_status(job_id, worker_url: Optional[str] = None)`** — when `worker_url` is given,
   poll `f"{worker_url}/status/{job_id}"` with `allow_failover=False` (a pinned poll must never
   re-resolve to active_url nor spin up a fallback VM — a fresh VM can't have the job). When
   omitted, behave exactly as today.

3. **`wait_for_completion(..., worker_url: Optional[str] = None)`** — thread `worker_url` into
   `get_job_status`. Consecutive-failure tolerance unchanged (still rides out a brief restart of
   the *pinned* worker). A pinned 404 is now the *true* "owning worker lost the job" signal.

4. **`encode_videos` / `render_video_on_gce` / `encode_preview_video`** — capture
   `pinned_url = self._get_worker_url()` **after** submit returns, and pass it as `worker_url=`
   to `wait_for_completion`. Capturing post-submit reflects a capacity-fallback re-route during
   submit (warmup invalidates the cache, so `_get_worker_url()` returns the fallback URL) while
   being *before* any later deploy swap.

### Why post-submit capture (not the submit return value)

A capacity-fallback during submit updates `active_override` + invalidates the URL cache, so the
*next* `_get_worker_url()` already returns the worker the job actually landed on. This avoids
threading the URL back through three `submit_*` return contracts.

## Tests (TDD — `backend/tests/test_encoding_service.py`)

- `get_job_status` targets the pinned URL over active_url, with `allow_failover=False`.
- `get_job_status` without a pin still uses active_url (regression).
- `wait_for_completion` threads `worker_url` into each poll.
- `_request_with_retry` does **not** warm up / re-resolve when `allow_failover=False`.
- **End-to-end:** `render_video_on_gce` keeps polling the submission worker even when
  `active_url` has swapped to a worker that 404s — would fail before the fix, passes after.
- Update the 3 existing `wait_for_completion` tolerance mocks to accept `worker_url=None`.

## Scope notes / non-goals

- The single `get_job_status` call inside the `submit_*` 409-conflict handler stays unpinned
  (rare two-request-same-job-during-swap edge); the main poll loop is pinned via the convenience
  methods, which covers the join case.
- No change to the CI drain logic — it remains the correct guard for *stopping* a busy VM.
