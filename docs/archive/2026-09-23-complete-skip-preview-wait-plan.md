# Review "Complete" must not wait on the preview video — investigation + plan

**Date:** 2026-09-23 · **Branch:** `feat/sess-20260923-1911-complete-skip-preview-wait`

## Andrew's report (verbatim)

> when i'm reviewing a track, sometimes i'm in a hurry and I don't care to wait for the
> preview video to render before i hit complete - e.g. if i'm already confident the lyrics
> timings are right, and i'm used the audio-only backing vocal preview to make the backing
> vocals decision, i'll hit complete anyway despite the preview not having loaded yet
> but unfortunately it doesn't actually work to skip waiting for the preview video, it
> waits until that loads before it actually submits the completed job. why? can we easily
> fix that without any compromises or negative side effects? ideally if i click complete
> before the preview has loaded it would immediately submit and terminate the preview
> encoding to avoid wasted encoder resources

## What actually happens (prod logs, job e80caa57, 2026-09-23 22:36 UTC)

The frontend does NOT gate Complete on the preview — `ReviewChangesModal` only disables the
button on `isSubmitting`. The wait is entirely server-side and is caused by **synchronous GCE
VM-start calls running on the API's asyncio event loop**, which freezes every in-flight
request on that Cloud Run instance:

| Time (UTC) | Event | Effect |
|---|---|---|
| 22:36:29 | Preview background task submits to encoder VM (stopped → TCP hang) | — |
| 22:36:59 | 30 s aiohttp timeout → `_warmup_encoding_worker_fallback` → `ensure_any_running()` **sync** | event loop frozen 14 s |
| 22:36:59 | Andrew clicks Complete → `POST /jobs/{id}/corrections` | handler finished 22:37:00, response not written until 22:37:13 (**13.9 s**) |
| 22:37:15 | `POST /review/{id}/complete` → `trigger_render_video_worker` → `_warmup_encoding_worker()` **sync** (`ensure_primary_running`, 17 s) | request took **18.6 s**; a concurrent status poll took 17.7 s, `GET /jobs` took 5.4 s |
| 22:37:56 | Preview finished on a *fallback VM that was cold-started just for it* | wasted |

14-day sample: 62/142 `POST /review/*/complete` calls took > 3 s (max 35 s); only 9 took < 1 s.
"Encoding worker unreachable — started VM" fires from the API service 2–6×/day — each one
is a preview cold start freezing an API instance for ~10–15 s for *every* user on it.

## Root causes

1. `WorkerService._warmup_encoding_worker` is documented as "fire-and-forget" but is called
   synchronously inside the awaited `trigger_render_video_worker` / `trigger_video_worker`
   → blocks the response AND the event loop while GCE starts the VM.
2. `EncodingService._warmup_encoding_worker_fallback` calls the sync
   `ensure_any_running` / `ensure_primary_running` from async code → same freeze, triggered
   by the preview's background encode task.
3. Nothing stops a preview encode once the job leaves review — it keeps polling, and can
   cold-start a VM, for a video nobody can watch.

## Fix (no behaviour compromises)

1. **worker_service**: run the warmup via `asyncio.to_thread` in a tracked background task;
   dispatch the render job without awaiting it. Correctness is unaffected — the render
   worker already self-heals with its own warmup fallback if the VM isn't up.
2. **encoding_service / encoding_worker_manager**: `await asyncio.to_thread(...)` around the
   sync VM-start calls and the per-poll `get_vm_status` in `wait_for_worker_ready`.
3. **review.py**: the background preview encode gets a watcher that re-reads job status
   every few seconds and cancels the encode (no warmup, no submission, no polling, no local
   fallback, no error marker) once the job is no longer `awaiting_review`/`in_review`.

Not done (and why): killing an already-running ffmpeg on the GCE worker. Preview encodes run
~10 s in the worker's light lane; a worker-side cancel endpoint would need a Popen registry
plus a worker restart to deploy, for negligible savings. The expensive waste — cold-starting
a VM and API-side polling for a discarded preview — is what the watcher prevents.

## Tests

- `backend/tests/test_services.py`: trigger returns without waiting on a slow warmup; warmup
  still runs.
- `backend/tests/test_encoding_service.py`: warmup fallback no longer blocks the event loop.
- `backend/tests/test_routes_review.py`: preview encode is abandoned when the job leaves
  review; continues while in review.

## Follow-up (out of scope)

- The preview submission's 30 s aiohttp timeout against a stopped VM (TCP SYN black-hole)
  delays cold previews by ~25 s; a short `connect` timeout would fix that.
