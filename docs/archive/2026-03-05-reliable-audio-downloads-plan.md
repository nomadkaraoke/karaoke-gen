# Plan: Reliable Audio Downloads

**Date:** 2026-03-05
**Branch:** feat/sess-20260305-0125-investigate-stuck-downloads
**Status:** Implemented

## Problem Statement

Jobs that download audio via the "guided flow" (create-from-search, select audio source) use FastAPI `BackgroundTasks` to run the download + worker trigger logic. When Cloud Run scales down the instance before the background task completes, the task is silently killed. The job stays stuck at `downloading_audio` forever — no error is recorded, no workers are triggered, and there's no automatic recovery.

This affected jobs `51b8231d` and `89e497b1` on 2026-03-05. Cloud Run instances were shut down ~1-2 minutes after job creation.

### Why This Happens

The download flow is:
1. HTTP endpoint returns 200 immediately (job created)
2. `background_tasks.add_task(_download_audio_and_trigger_workers, ...)` schedules the download
3. BackgroundTask calls flacfetch API, polls for completion (can take 30s-5min for Spotify)
4. On success: updates Firestore, transitions to DOWNLOADING, triggers audio+lyrics workers
5. On failure: calls `fail_job()` to mark as failed

Cloud Run terminates "idle" instances (no active HTTP requests). Since the endpoint already returned, the instance appears idle despite the background task running. When terminated, step 3/4/5 never completes.

### Existing Pattern That Works

Audio separation and lyrics transcription workers already solved this exact problem by using **Cloud Run Jobs** (`trigger_audio_worker`, `trigger_lyrics_worker` in `worker_service.py`). Cloud Run Jobs run to completion without HTTP request lifecycle concerns.

## Plan

### Part 1: Move Audio Download to Cloud Run Job

**Goal:** Make audio downloads survive Cloud Run instance shutdowns.

#### 1a. Create `backend/workers/audio_download_worker.py`

New standalone worker module (like `audio_worker.py` and `lyrics_worker.py`) that:
- Accepts `--job-id` CLI argument
- Reads job from Firestore to get download params (source_name, source_id, selection_index, etc.)
- Downloads audio via flacfetch client or YouTube download service
- Uploads to GCS at `uploads/{job_id}/audio/`
- Updates Firestore with `input_media_gcs_path` and `filename`
- Transitions job from `DOWNLOADING_AUDIO` to `DOWNLOADING`
- Triggers audio+lyrics workers (via Cloud Run Jobs, same as today)
- On failure: calls `fail_job()` with error details

This is essentially extracting `_download_audio_and_trigger_workers()` from `audio_search.py` into a standalone entrypoint.

#### 1b. Create Cloud Run Job infrastructure

Add to `infrastructure/modules/cloud_run.py`:
- `create_audio_download_job()` — new Cloud Run Job resource `audio-download-job`
- Same pattern as `create_audio_separation_job()` and `create_lyrics_transcription_job()`
- Timeout: 10 minutes (Spotify downloads typically < 5 min, YouTube < 30s)
- Max retries: 2

Add to `infrastructure/__main__.py`:
- Wire up the new job resource

#### 1c. Add `trigger_audio_download_worker()` to WorkerService

Add method to `backend/services/worker_service.py`:
```python
async def trigger_audio_download_worker(self, job_id: str) -> bool:
    return await self._trigger_worker_cloud_run_job(
        job_id=job_id,
        cloud_run_job_name="audio-download-job",
        worker_module="audio_download_worker",
    )
```

#### 1d. Replace BackgroundTasks calls with Cloud Run Job triggers

In these 3 call sites, replace `background_tasks.add_task(_download_audio_and_trigger_workers, ...)` with `await worker_service.trigger_audio_download_worker(job_id)`:

1. `backend/api/routes/audio_search.py` — `select_audio_source()` (line ~1160)
2. `backend/api/routes/audio_search.py` — `search_with_auto_download()` (line ~838)
3. `backend/api/routes/jobs.py` — `create_from_search()` (line ~2067)

The endpoints still return immediately — Cloud Run Job creation is fast (~100ms). The actual download runs in the separate Cloud Run Job instance.

#### 1e. Update admin retry endpoint

In `backend/api/routes/jobs.py`, the retry endpoint's audio download path (line ~1470-1605):
- Currently runs the download synchronously in the HTTP handler (problematic for long downloads)
- Refactor to trigger the same `audio_download_worker` Cloud Run Job
- Fix the state transition bug: add `DOWNLOADING_AUDIO` to valid transitions from `FAILED`

### Part 2: Stuck Job Detection & Auto-Recovery (Defense in Depth)

**Goal:** Automatically detect and recover jobs stuck in transient states.

#### 2a. Add `downloading_audio_stuck` check to JobHealthService

In `backend/services/job_health_service.py`, add a consistency check:
- Jobs in `DOWNLOADING_AUDIO` status for >10 minutes are stuck
- Check `updated_at` age, similar to existing `encoding_stuck` check

#### 2b. Create stuck job recovery endpoint

New internal endpoint `POST /api/internal/recover-stuck-jobs`:
- Queries Firestore for jobs in `DOWNLOADING_AUDIO` status with `updated_at` > 10 minutes ago
- For each stuck job:
  - If `input_media_gcs_path` is set (file exists): transition to DOWNLOADING, trigger workers
  - If `input_media_gcs_path` is null: re-trigger audio download worker
  - If retry_count > 3: fail the job with timeout error
- Increment `retry_count` on each recovery attempt
- Log all recovery actions for observability

#### 2c. Create Cloud Scheduler job

Add to infrastructure:
- Cloud Scheduler job `recover-stuck-downloads` that runs every 5 minutes
- Calls `POST /api/internal/recover-stuck-jobs`
- Uses the existing service account OIDC auth pattern

This catches any future edge cases where jobs get stuck, regardless of the cause.

### Part 3: Fix State Machine Gap

**Goal:** Allow retry from `FAILED` to `DOWNLOADING_AUDIO`.

In `backend/models/job.py`, add `JobStatus.DOWNLOADING_AUDIO` to the `FAILED` transitions:
```python
JobStatus.FAILED: [
    JobStatus.DOWNLOADING,
    JobStatus.DOWNLOADING_AUDIO,    # <-- ADD THIS
    JobStatus.INSTRUMENTAL_SELECTED,
    JobStatus.REVIEW_COMPLETE,
    JobStatus.LYRICS_COMPLETE,
    JobStatus.AWAITING_REVIEW,
]
```

This allows the admin retry endpoint to re-download audio for failed jobs that came from the audio search flow.

## Scope & Complexity

| Part | Files Changed | Complexity | Risk |
|------|--------------|------------|------|
| 1a | 1 new file | Medium | Low — extracts existing logic |
| 1b | 2 infra files | Low | Low — follows existing pattern |
| 1c | 1 file | Low | Low — one new method |
| 1d | 2 files | Low | Low — replace 3 call sites |
| 1e | 1 file | Medium | Medium — refactor retry logic |
| 2a | 1 file | Low | Low — add one check |
| 2b | 1 file | Medium | Low — new endpoint |
| 2c | 2 infra files | Low | Low — follows existing pattern |
| 3 | 1 file | Low | Low — add one enum value |

**Total: ~6 files changed, 2 new files, 1 new infra resource**

## Testing Strategy

- **Unit tests:** Test `audio_download_worker.py` with mocked flacfetch/YouTube services
- **Integration tests:** Test the full flow from `create_from_search` through Cloud Run Job trigger
- **Existing tests:** Update mocks in `test_audio_search.py` and `test_standalone_search.py` to verify Cloud Run Job is triggered instead of BackgroundTasks
- **Manual verification:** Deploy to prod and create a test job via guided flow; verify it completes even if Cloud Run scales down

## Implementation Order

1. Part 3 (state machine fix) — smallest, unblocks retry
2. Part 1a-1c (new worker + infra) — core fix
3. Part 1d (replace call sites) — activate the fix
4. Part 1e (retry endpoint) — improve admin tooling
5. Part 2 (stuck job detection) — defense in depth
