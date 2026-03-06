# Edit Completed Tracks — Implementation Plan

> Created: 2026-03-06
> Branch: feat/sess-20260306-1705-edit-completed-tracks

## Overview

Allow users to edit completed karaoke tracks — correcting lyrics, changing instrumental selection, or updating artist/title metadata — without consuming additional credits. The existing outputs are cleaned up (YouTube, Dropbox, GDrive, GCS) before re-opening the review screens, and a fresh brand code is allocated when the edited track is re-distributed.

## Design Decisions

| Decision | Answer |
|----------|--------|
| Brand code on edit | New code allocated (old one recycled) |
| Credit cost | Free (credit already consumed at job creation) |
| Edit limit | None |
| Metadata edits (artist/title) | Supported via confirmation modal |
| UX approach | Reopen review on same job (approach A) |

## User Flow

```
Completed job detail page
  → User clicks "Edit Track"
  → Confirmation modal appears:
      - Explains: existing outputs will be deleted, track re-rendered after review
      - Asks: "Do you need to update the artist or title?"
        - If yes: shows editable artist/title input fields (pre-filled)
        - If no: fields hidden
      - [Cancel] [Confirm & Edit]
  → On confirm:
      - Frontend calls POST /api/jobs/{job_id}/edit
      - Backend: cleanup outputs → reset state → issue review token
      - Frontend redirects to review screen (/app/jobs#/{jobId}/review)
  → User edits lyrics / instrumental as usual
  → User submits review
  → Standard pipeline: render → encode → distribute (new brand code)
  → Job returns to COMPLETE with fresh outputs
```

## Implementation

### Phase 1: Backend — Edit Endpoint

**New endpoint: `POST /api/jobs/{job_id}/edit`**

File: `backend/api/routes/jobs.py` (or new `backend/api/routes/edit.py`)

Request body:
```json
{
  "artist": "Updated Artist",   // optional, only if user wants to change
  "title": "Updated Title"      // optional, only if user wants to change
}
```

Auth: Job owner (via session token) OR admin.

Logic:
1. **Validate** job is in `COMPLETE` state and user owns it
2. **Validate** `outputs_deleted_at` is not set (prevent editing already-in-progress edits)
3. **Cleanup distributed outputs** (reuse logic from admin `delete-outputs`):
   a. Delete YouTube video (if exists)
   b. Delete Dropbox folder (if exists)
   c. Delete Google Drive files (if exists)
   d. Delete GCS `finals/` folder
   e. Recycle brand code (if Dropbox + GDrive both succeeded)
   f. Clear state_data keys: `youtube_url`, `youtube_video_id`, `brand_code`, `dropbox_link`, `gdrive_files`
   g. Set `outputs_deleted_at` + `outputs_deleted_by`
4. **Update metadata** (if provided):
   a. Update `artist` and/or `title` fields on the job
   b. If metadata changed: delete existing screen files from GCS (`screens/` folder)
   c. Clear `screens_progress` from state_data so screens worker re-runs
5. **Reset processing state**:
   a. Clear state_data keys: `render_progress`, `video_progress`, `encoding_progress`, `review_complete`
   b. Keep: `audio_complete`, `lyrics_complete`, `corrected_lyrics`, `instrumental_selection`, `instrumental_options`
   c. Keep all stems and lyrics files in GCS
6. **Transition state**: `COMPLETE` → `AWAITING_REVIEW`
7. **Issue review token**: Generate new `review_token` with expiration
8. **Increment edit counter**: `edit_count += 1` (new field)
9. **Log timeline event**: `"Track edit initiated by {user_email} (edit #{edit_count})"`
10. **Return** review URL + token

Response:
```json
{
  "review_url": "/app/jobs#/{jobId}/review",
  "review_token": "abc123..."
}
```

**Error cases:**
- Job not in COMPLETE state → 400
- User doesn't own job → 403
- Cleanup fails critically (Dropbox folder still exists) → 500, don't reset state
- Job already being edited (status != COMPLETE) → 400

### Phase 2: Backend — State Machine Updates

File: `backend/models/job.py`

1. Add `COMPLETE → AWAITING_REVIEW` to `STATE_TRANSITIONS` dict (conditional on edit endpoint)
2. Add new field to Job model:
   ```python
   edit_count: int = 0
   ```

### Phase 3: Backend — Post-Review Pipeline Adjustments

File: `backend/workers/screens_worker.py`

- When triggered after an edit with metadata changes, screens worker already handles regeneration
- The edit endpoint clears `screens_progress` and deletes screen files, so screens worker will re-run naturally
- No changes needed if screens worker already checks for missing screen files

File: `backend/workers/render_video_worker.py`

- No changes needed — render worker reads current `corrected_lyrics` and `instrumental_selection` from state_data
- Will pick up whatever the user selected during re-review

File: `backend/workers/video_worker_orchestrator.py`

- Already clears `outputs_deleted_at` after successful distribution (lines 564-578)
- Already allocates new brand code via `allocate_brand_code()` during distribution
- No changes needed

### Phase 4: Frontend — Edit Button + Confirmation Modal

**Edit button on completed job view**

File: `frontend/app/app/jobs/[[...slug]]/client.tsx` (or wherever job detail is rendered)

- Show "Edit Track" button only when job status is `complete`
- Button opens the confirmation modal

**Confirmation modal component**

New file: `frontend/components/job/EditTrackModal.tsx`

Content:
- Warning text: "This will remove your current video from YouTube, Dropbox, and Google Drive. After you complete the review again, a new version will be rendered and re-distributed."
- Toggle/checkbox: "I need to update the artist or title"
  - When checked, show:
    - Artist input (pre-filled with current `job.artist`)
    - Title input (pre-filled with current `job.title`)
- [Cancel] button
- [Confirm & Edit] button → calls API, shows loading state, redirects to review on success

**API call**

File: `frontend/lib/api.ts`

Add:
```typescript
editCompletedTrack(jobId: string, updates?: { artist?: string; title?: string }): Promise<{ review_url: string; review_token: string }>
```

### Phase 5: Frontend — Review Screen Adjustments

File: `frontend/app/app/jobs/[[...slug]]/client.tsx` (JobRouterClient)

- Currently only renders review screens for `AWAITING_REVIEW` / `IN_REVIEW` states — this already works
- The edit endpoint transitions the job to `AWAITING_REVIEW`, so existing routing handles it

**One adjustment needed:** If the user edited metadata (artist/title), the screens worker needs to run before or after review. Two options:

- **Option A (simpler):** Edit endpoint triggers screens worker immediately after resetting state. Screens regenerate in background while user reviews lyrics. By the time review is submitted, screens should be ready. The existing coordination logic (screens_progress check) handles this.

- **Option B:** Defer screen regeneration to after review submission, same as initial flow. Slightly slower end-to-end but no parallel coordination concerns.

**Recommendation:** Option A — trigger screens worker during edit if metadata changed. The 30-second screen generation will finish well before the user completes their review edits.

### Phase 6: Testing

**Backend unit tests:**
- `test_edit_completed_track.py`:
  - Happy path: COMPLETE job → edit → state becomes AWAITING_REVIEW
  - With metadata: artist/title updated, screens cleared
  - Without metadata: artist/title unchanged, screens preserved
  - Auth: owner can edit, non-owner gets 403
  - State guard: non-COMPLETE jobs rejected
  - Cleanup: verify YouTube/Dropbox/GDrive/GCS deletion called
  - Brand code: verify recycled during cleanup
  - Edit count: incremented correctly
  - Timeline: event logged

**Frontend tests:**
- EditTrackModal renders correctly
- Toggle shows/hides metadata fields
- Confirm calls API with correct payload
- Loading state during API call
- Redirect to review on success
- Error display on failure

**E2E production test:**
- Create job → complete → edit → re-review → verify new outputs

## Files to Create/Modify

### New Files
- `frontend/components/job/EditTrackModal.tsx` — Confirmation modal
- `backend/tests/test_edit_completed_track.py` — Backend tests

### Modified Files
- `backend/api/routes/jobs.py` — Add edit endpoint (or create `edit.py`)
- `backend/models/job.py` — Add `edit_count` field, update STATE_TRANSITIONS
- `frontend/lib/api.ts` — Add `editCompletedTrack()` method
- `frontend/app/app/jobs/[[...slug]]/client.tsx` — Add Edit button to completed job view

### No Changes Needed
- `backend/workers/render_video_worker.py` — Already reads current state_data
- `backend/workers/video_worker_orchestrator.py` — Already allocates new brand code, clears outputs_deleted_at
- `backend/workers/screens_worker.py` — Already handles missing screens
- `backend/services/brand_code_service.py` — Allocation/recycling already works
- `frontend/components/lyrics-review/LyricsAnalyzer.tsx` — Reused as-is
- `frontend/components/instrumental-review/InstrumentalSelector.tsx` — Reused as-is

## Sequence Diagram

```
User                  Frontend              Backend               Workers
 |                      |                      |                      |
 |-- Click "Edit" ----->|                      |                      |
 |                      |-- Show modal ------->|                      |
 |-- Confirm edit ----->|                      |                      |
 |                      |-- POST /edit ------->|                      |
 |                      |                      |-- Delete YouTube --->|
 |                      |                      |-- Delete Dropbox --->|
 |                      |                      |-- Delete GDrive ---->|
 |                      |                      |-- Delete GCS ------->|
 |                      |                      |-- Recycle brand code |
 |                      |                      |-- Reset state ------>|
 |                      |                      |-- Issue review token |
 |                      |                      |-- (if metadata changed:
 |                      |                      |    trigger screens worker)
 |                      |<-- review_url -------|                      |
 |<-- Redirect to review|                      |                      |
 |                      |                      |                      |
 |-- Edit lyrics ------>|                      |                      |
 |-- Select instrumental|                      |                      |
 |-- Submit review ---->|                      |                      |
 |                      |-- POST /complete --->|                      |
 |                      |                      |-- Trigger render --->|
 |                      |                      |                      |-- Render video
 |                      |                      |                      |-- Encode formats
 |                      |                      |                      |-- Allocate brand code
 |                      |                      |                      |-- Upload YouTube
 |                      |                      |                      |-- Upload Dropbox
 |                      |                      |                      |-- Upload GDrive
 |                      |                      |                      |-- Clear outputs_deleted_at
 |                      |                      |<-- COMPLETE ---------|
 |<-- Job complete -----|                      |                      |
```

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Cleanup fails mid-way (e.g., YouTube deleted but Dropbox fails) | Don't reset job state on critical failure. User sees error, job stays COMPLETE with partial cleanup. Admin can investigate. |
| User abandons review after edit (outputs deleted, never re-submitted) | Job sits in AWAITING_REVIEW. Same as any unreviewed job. Could add reminder email after 24h. |
| Race condition: two edit requests simultaneously | Use Firestore transaction for state transition. Second request fails validation (job no longer COMPLETE). |
| Screens not ready when user submits review quickly | Existing coordination logic in pipeline handles this — render worker waits for screens_complete flag. |
