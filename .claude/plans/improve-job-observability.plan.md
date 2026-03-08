# Plan: Improve Job Observability

**Created:** 2026-03-07
**Branch:** feat/sess-20260307-2216-improve-job-observability
**Status:** Draft

## Overview

After shipping the "edit completed tracks" feature, we found it very difficult to trace what actually happened to edited jobs. Timeline events are too sparse (no output details), cleanup operations aren't logged to Cloud Logging or Firestore, state_data gets fully overwritten during re-processing (losing history of previous outputs), and API endpoints don't write to the job's Firestore log subcollection.

The goal is to make it trivially easy for future agents and humans to reconstruct the complete lifecycle of any job by reading its timeline and Firestore logs.

## Problem Analysis

### What we found investigating two edited jobs:

1. **Timeline events only have `status`, `timestamp`, `progress`, `message`** — no output details like YouTube video IDs, brand codes, Dropbox links, or GDrive file IDs
2. **Edit endpoint cleanup results never logged** — the `cleanup_results` dict is built and returned in the HTTP response but never logged to Cloud Logging or the job's Firestore log subcollection
3. **Distribution details incomplete in logs** — YouTube logs the URL but not video_id separately; Dropbox doesn't log folder path; GDrive doesn't log file IDs
4. **`state_data` fully overwritten on re-processing** — `video_worker.py:342-354` does `{**job.state_data, ...}`, so any `DELETE_FIELD` operations from the edit endpoint get lost when old keys are re-merged from the in-memory copy
5. **`outputs_deleted_at` cleared by video worker** — intentionally, but loses audit trail
6. **No Firestore job logs from API endpoints** — edit/admin endpoints only log to Cloud Run stdout, not to `jobs/{job_id}/logs/`

## Requirements

- [ ] Add `metadata` dict to `TimelineEvent` for structured output details
- [ ] Log all cleanup operations (success AND failure) to both Cloud Logging and Firestore job logs
- [ ] Log distribution output details (YouTube video ID, Dropbox path, brand code, GDrive file IDs) in timeline events and Firestore logs
- [ ] Preserve previous output details in timeline when a job is edited (snapshot before cleanup)
- [ ] Add helper to write Firestore job logs from API endpoints (not just workers)
- [ ] Ensure edit/admin delete endpoints write to Firestore job logs
- [ ] No breaking changes to existing timeline format (additive only)

## Technical Approach

### 1. Add `metadata` field to `TimelineEvent`

The `TimelineEvent` model gains an optional `metadata: Optional[Dict[str, Any]]` field. This allows any timeline event to carry structured data without changing the schema. The field is additive — existing events without metadata continue to work.

Key events that gain metadata:
- **`complete`**: `{youtube_url, youtube_video_id, dropbox_link, brand_code, gdrive_file_ids}`
- **Edit initiated**: `{previous_outputs: {youtube_url, dropbox_link, brand_code, gdrive_files}, cleanup_results: {...}}`
- **Outputs deleted**: `{deleted_outputs: {...}, cleanup_results: {...}}`

### 2. Write Firestore job logs from API endpoints

Create a simple helper function that API route handlers can call to append logs to the job's Firestore subcollection. Workers already have `JobLogAdapter` — we just need a lightweight equivalent for API routes:

```python
def log_to_job(job_id: str, worker: str, level: str, message: str, metadata: dict = None):
    """Write a log entry to the job's Firestore log subcollection."""
    entry = WorkerLogEntry.create(job_id, worker, level, message, metadata)
    FirestoreService().append_log_to_subcollection(job_id, entry)
```

### 3. Enhance distribution logging in video_worker_orchestrator

After each distribution upload succeeds, log the details with structured metadata:
- YouTube: video_id, video_url, channel
- Dropbox: folder_path, sharing_link
- GDrive: file_ids dict
- Brand code: code, prefix, allocated vs recycled

### 4. Snapshot previous outputs on edit

Before the edit endpoint clears state_data distribution keys, capture the current values in the timeline event's metadata. This preserves the history permanently in the timeline array (which is append-only via `ArrayUnion`).

### 5. Log cleanup results from edit/admin endpoints

After cleanup runs, log each step's result to both Cloud Logging and the Firestore job log subcollection with structured metadata.

## Implementation Steps

### Step 1: Add `metadata` to `TimelineEvent` model
- [ ] Add `metadata: Optional[Dict[str, Any]] = None` to `TimelineEvent` in `backend/models/job.py`
- [ ] No migration needed — Firestore is schemaless, existing events just won't have the field

### Step 2: Create `log_to_job` helper
- [ ] Add `log_to_job()` function in `backend/services/firestore_service.py` (or a small utility module)
- [ ] Takes `job_id`, `worker`, `level`, `message`, optional `metadata`
- [ ] Wraps `WorkerLogEntry.create()` + `append_log_to_subcollection()`
- [ ] Catches and suppresses errors (logging should never break the caller)

### Step 3: Update `transition_to_state` to accept metadata
- [ ] Add optional `timeline_metadata: Optional[Dict[str, Any]] = None` parameter to `job_manager.transition_to_state()`
- [ ] Pass through to `update_job_status()` → `TimelineEvent` construction
- [ ] Update `firestore_service.update_job_status()` to accept and pass `timeline_metadata`

### Step 4: Enhance edit endpoint logging
- [ ] Before cleanup: snapshot current distribution outputs (`youtube_url`, `dropbox_link`, `brand_code`, `gdrive_files`) into `previous_outputs` dict
- [ ] After each cleanup step: log result to Cloud Logging AND call `log_to_job()`
- [ ] After all cleanup: include `previous_outputs` and `cleanup_results` in the timeline event metadata
- [ ] The "Track edit initiated" timeline event gains metadata: `{initiated_by, previous_outputs, cleanup_results}`

### Step 5: Enhance admin delete-outputs endpoint logging
- [ ] Same pattern as edit endpoint: snapshot outputs, log each cleanup step, include in timeline metadata
- [ ] The "Outputs deleted by admin" timeline event gains metadata: `{deleted_by, deleted_outputs, cleanup_results}`

### Step 6: Enhance distribution logging in video_worker_orchestrator
- [ ] YouTube upload success: log video_id and video_url with metadata `{video_id, video_url}`
- [ ] Dropbox upload success: log folder_path and sharing_link with metadata `{folder_path, sharing_link}`
- [ ] GDrive upload success: log file_ids with metadata `{file_ids: {...}}`
- [ ] Brand code allocation: log with metadata `{brand_code, source: "allocated"|"recycled"}`

### Step 7: Add output details to `complete` timeline event
- [ ] When `video_worker.py` calls `transition_to_state(COMPLETE)`, pass `timeline_metadata` with distribution output details
- [ ] Include: `youtube_url`, `youtube_video_id`, `dropbox_link`, `brand_code`, `gdrive_file_ids`, `edit_count`

### Step 8: Tests
- [ ] Unit test: `TimelineEvent` with metadata serializes correctly
- [ ] Unit test: `log_to_job` writes to subcollection
- [ ] Unit test: edit endpoint includes `previous_outputs` and `cleanup_results` in timeline event
- [ ] Unit test: `complete` transition includes output metadata
- [ ] Verify existing tests still pass

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/models/job.py` | Modify | Add `metadata` field to `TimelineEvent` |
| `backend/services/firestore_service.py` | Modify | Add `log_to_job()` helper, update `update_job_status()` to accept timeline metadata |
| `backend/services/job_manager.py` | Modify | Add `timeline_metadata` param to `transition_to_state()` |
| `backend/api/routes/jobs.py` | Modify | Enhance edit endpoint with cleanup logging, output snapshots, timeline metadata |
| `backend/api/routes/admin.py` | Modify | Enhance delete-outputs endpoint similarly |
| `backend/workers/video_worker_orchestrator.py` | Modify | Add structured metadata to distribution log messages |
| `backend/workers/video_worker.py` | Modify | Pass output metadata in `complete` transition (both orchestrator and legacy paths) |
| `backend/tests/test_edit_completed_track.py` | Modify | Verify timeline metadata in edit tests |
| `backend/tests/test_observability.py` | Create | Tests for TimelineEvent metadata and log_to_job |

## Testing Strategy

- **Unit tests**: TimelineEvent metadata serialization, log_to_job helper, edit endpoint timeline metadata assertions
- **Existing test verification**: `make test` must pass — changes are additive so existing tests should be unaffected
- **No E2E tests needed**: This is purely backend logging/observability — no UI changes

## What This Does NOT Include

- **Dedicated admin UI for viewing job logs** — existing admin page already shows timeline
- **Log aggregation/search UI** — Cloud Logging and Firestore viewer already serve this purpose
- **Changing state_data overwrite behavior** — the `{**job.state_data, ...}` pattern in video_worker.py is intentional; instead we preserve history in timeline events
- **Changing outputs_deleted_at clearing** — video worker clearing it after re-processing is correct behavior; the edit history lives in timeline metadata instead

## Rollback Plan

All changes are additive (new optional field on TimelineEvent, additional log writes). Rolling back just means deploying the previous version — no data migration needed.

## Open Questions

None — the approach is straightforward and low-risk.
