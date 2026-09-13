# Investigation: Tenant E2E #107 failure — deploy killed render poll, job froze at `rendering_video`

**Date:** 2026-09-13
**Incident:** Tenant Portals E2E run #107 (`singa`) failed `job reached complete before render timeout` (expected `complete`, got `rendering_video`). Job `41e06b90`.
**Fix:** v0.224.0 — render worker migrated to Cloud Run Jobs (`USE_CLOUD_RUN_JOBS_FOR_RENDER`).

## Timeline (all UTC, 2026-09-13)

| Time | Event |
|------|-------|
| 04:58:44 | New backend revision `karaoke-backend-00973-xac` starts (deploy in progress) |
| 04:58:52 | Review complete → Cloud Task created for render-video worker (`dispatch_deadline=1800s`) |
| 04:58:53 | Cloud Task POST hits **old** revision `00971-xad`; endpoint returns 200 immediately, `process_render_video` continues as FastAPI BackgroundTask; job → `rendering_video`; render submitted to GCE `encoding-worker-a` |
| 04:59:03 | Backend records "Encoding progress: 55%" (last Firestore write, `updated_at` freezes here) |
| 04:59:13.867 | Old revision gets SIGTERM: uvicorn logs `Shutting down` then `Waiting for background tasks to complete.` — **its last-ever log lines** |
| ~04:59:24 | SIGKILL (Cloud Run gives ~10s, not the 600s the code comment claimed). Lifespan shutdown hook (worker-registry wait + `park_active_render_jobs_for_shutdown`) **never ran** |
| 04:59:57 | GHA runner logs "Deploy - Backend (Cloud Run) completed: Succeeded" |
| 05:00:05 | **GCE worker completes the render** and uploads `with_vocals.mkv` + karaoke.ass/lrc/corrected.txt to GCS. Nobody polls it again; result never recorded |
| 05:00, 05:05, … | `RECOVER_STUCK_JOBS` runs every 5 min on the new revision: `render_retriggered=0` — `rendering_video_stuck` needs `updated_at` stale > **45 min** |
| 05:15:10 | `encoding-worker-idle-shutdown` correctly stops the now-idle VM (confirmed `active_jobs=0` first — its fail-safe worked; red herring) |
| 05:30:06 | E2E 30-min render deadline expires; test cleanup deletes job, refunds credit; run #107 fails |

Recovery *would* have fired at ~05:44 (45-min staleness) and re-rendered from scratch. Real-customer impact of this class: ~50-60 min silent stall + a wasted re-render, on every deploy that lands mid-render (deploys go out on every merge to main).

## Root cause chain (4 independent layers, each structurally broken for this case)

1. **BackgroundTask execution** — the render endpoint returns 200 immediately and polls the GCE worker post-response. Cloud Run kills old-revision instances ~10s after SIGTERM on every deploy; a mid-render poll loop cannot survive.
2. **Unreachable shutdown parking** — uvicorn's shutdown sequence waits for background tasks *before* running FastAPI lifespan shutdown. With a long render task in flight, SIGKILL always arrives first, so `park_active_render_jobs_for_shutdown()` is dead code in exactly the scenario it was written for. The comment justifying it ("600s grace, wait 480s") was factually wrong.
3. **Cloud Tasks retry defeated** — the queue got its 200 at 04:58:53; `dispatch_deadline` protects nothing when the handler is fire-and-forget.
4. **Slow watchdog** — `rendering_video_stuck` = `updated_at` stale > 45 min. Correct as a last-ditch net, but the only net that was actually reachable, and slower than both the E2E budget and any reasonable customer expectation.

## Why this was a known-class bug

`docs/archive/2026-03-08-fix-encoding-deploy-survival.md` diagnosed the **identical** failure for the *video worker* (final encode) — same BackgroundTask pattern, same deploy-kill, same conclusion — and fixed it by routing through the `video-encoding-job` Cloud Run Job (`USE_CLOUD_RUN_JOBS_FOR_VIDEO=true`, live since March). The render worker — one pipeline stage earlier, same shape of work — was never migrated. Related-but-distinct prior fixes that did NOT cover this: GCE-worker drain on deploy (protects the *encoder*, not the orchestrator), poll pinning v0.184.2 (protects against *worker* primary swaps), `recover-stuck-jobs` re-park v0.192.3 (the 45-min net that was too slow here).

## Fix (v0.224.0)

- `trigger_render_video_worker` routes to a Cloud Run Job execution — reusing `video-encoding-job` with args override `python -m backend.workers.render_video_worker --job-id <id>` — when `USE_CLOUD_RUN_JOBS_FOR_RENDER=true` and Cloud Tasks enabled. Job executions run to completion; deploys update the job template image but never touch running executions.
- New CLI entrypoint in `render_video_worker.py`. Exit-code contract differs from video_worker deliberately: clean returns (True *and* False) exit 0 because `process_render_video` already parked/failed/superseded the job internally; only an escaped crash exits 1 (job template has `max_retries=2`).
- Pulumi: `video-encoding-job` env gains `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true` + `USE_CLOUD_RUN_JOBS_FOR_RENDER=true` so the render execution's in-process `trigger_video_worker` also dispatches a Cloud Run Job instead of regressing to the vulnerable path.
- ci.yml: service deploy env gains `USE_CLOUD_RUN_JOBS_FOR_RENDER=true`.
- `main.py` lifespan comment corrected (10s reality, uvicorn ordering) and the pointless 480s wait shrunk to 5s; the parking hook remains as a legacy/flag-off net.
- 45-min `rendering_video_stuck` sweep unchanged — still the net for execution crash/OOM/timeout and lost-VM cases.

**Rollback:** remove `USE_CLOUD_RUN_JOBS_FOR_RENDER=true` from the service env (falls back to the legacy Cloud Tasks → BackgroundTask path).

## Deliberately not done (and why)

- **Atomic (transactional) REVIEW_COMPLETE → RENDERING_VIDEO claim** (CodeRabbit finding on PR #994): two executions starting within the same sub-second window could both pass the worker-side gate and double-render. The identical read-then-write window exists in the legacy endpoint's `_check_worker_idempotency` (check stage → mark 'running' is not transactional), the worker-generation fence guarantees the stale result is discarded so final state is always correct, and the consequence is bounded wasted compute on a rare² race (requires two dispatches for one job AND <1s start skew). A conditional-write claim belongs in `JobManager`/`FirestoreService.update_job_status` — shared machinery for every worker — and deserves its own change, not a rider on this one.

- **Adopt-completed-render on recovery** (query the pinned worker's `/status` during the stuck sweep and harvest finished outputs instead of re-rendering): meaningful only for the deploy-kill case this PR eliminates at the source; remaining orphan causes mostly kill the GCE worker too. Skipped to keep the change focused.
- **Render-progress heartbeats + faster stuck threshold**: same reasoning — with the poller in a Cloud Run Job, poller death becomes rare enough that the 45-min net is acceptable.
- **uvicorn `--timeout-graceful-shutdown` + CancelledError parking**: would make the lifespan hook genuinely reachable, but it's belt-and-braces once renders no longer run on the service; adds signal-handling risk to every request path.
- **Same audit for `lyrics`/`screens`/`audio` BackgroundTasks**: lyrics + separation + audio-download already run as Cloud Run Jobs; screens/preview tasks are short. Noted in LESSONS-LEARNED as the pattern to check when touching those workers.
