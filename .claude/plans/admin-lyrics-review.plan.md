# Plan: Admin Lyrics Review Dashboard

**Created:** 2026-03-05
**Branch:** feat/sess-20260305-0002-admin-lyrics-review
**Status:** Draft

## Overview

Add an "Edit Reviews" section to the admin dashboard for reviewing completed lyrics reviews. Three goals:

1. **View what users saw** - Jump into a read-only lyrics review UI showing original vs corrected lyrics
2. **Replay user edits** - Step through edit log entries to see exactly what each user changed
3. **Review raw data** - View human edits data to evaluate quality for AudioShake data sharing

## Requirements

- [ ] New admin sidebar item "Edit Reviews" linking to `/admin/edit-reviews`
- [ ] Backend endpoint to list recent jobs that have completed lyrics review (with edit logs)
- [ ] Backend endpoint to serve immutable correction data for a job (read-only, cannot modify job)
- [ ] Admin page showing list of recently reviewed jobs with key metrics
- [ ] Ability to open the existing LyricsAnalyzer in read-only mode with snapshot data
- [ ] Edit log replay/stepper UI to walk through user changes one by one
- [ ] Raw data viewer for edit logs, annotations, and correction diffs

## Technical Approach

### Backend: 2 new admin endpoints

1. **`GET /api/admin/edit-reviews`** - List jobs with completed reviews
   - Query Firestore for jobs with `state_data.last_edit_log_path` set (indicating review was performed)
   - Filter by status (completed jobs, or optionally all post-review statuses)
   - Return summary: job_id, artist, title, user_email, review_completed_at, edit_count, feedback_count
   - Support pagination, search, exclude_test filtering

2. **`GET /api/admin/edit-reviews/{job_id}`** - Get full review snapshot
   - Returns immutable copy of:
     - Original corrections (`corrections.json` from GCS)
     - Updated corrections (`corrections_updated.json` from GCS)
     - Edit log (`edit_log_{session_id}.json` from GCS)
     - Annotations (`annotations.json` from GCS)
     - Job metadata (artist, title, user_email, timestamps)
     - Audio URL (signed, for playback in review UI)
   - This is a **read-only** endpoint - no job state mutations
   - Uses `require_admin` dependency (not `require_review_auth`)
   - Does NOT transition job status or modify any state

### Frontend: New admin page + Read-only review mode

1. **`/admin/edit-reviews/page.tsx`** - List page
   - Table of recently reviewed jobs
   - Columns: Artist/Title, User, Review Date, Edits Made, Feedback Given, Actions
   - "View Review" button → opens read-only lyrics review
   - "View Edit Log" button → opens edit replay view
   - "View Raw Data" button → shows raw JSON

2. **Read-only review viewer** - Reuse LyricsAnalyzer
   - Load data from the new admin endpoint (not the regular review endpoint)
   - Pass `isReadOnly={true}` and `apiClient={null}` to LyricsAnalyzer
   - Header shows "Admin Review - Read Only" indicator
   - No submit/save buttons, no state transitions
   - Audio playback still works (via signed URL)

3. **Edit log replay** - New component
   - Timeline/stepper showing each edit entry
   - For each entry: operation type, text before → text after, timestamp
   - Step forward/backward through edits
   - Show feedback reason if user provided one
   - Summary stats: total edits, edits with feedback, correction types breakdown

4. **Raw data viewer** - JSON viewer tabs
   - Tab 1: Edit Log (formatted JSON with syntax highlighting)
   - Tab 2: Annotations
   - Tab 3: Corrections diff (original vs updated)

### Key safety guarantee

The admin review endpoint serves a **snapshot** of the data. It reads from GCS files that were already written. The LyricsAnalyzer receives `apiClient={null}` so it physically cannot call any mutation endpoints. Even if code changes in the future, the admin endpoint itself never writes to Firestore or GCS.

## Implementation Steps

1. [ ] **Backend: Add `GET /api/admin/edit-reviews` endpoint** - List reviewed jobs with edit log metadata
2. [ ] **Backend: Add `GET /api/admin/edit-reviews/{job_id}` endpoint** - Serve full immutable review snapshot
3. [ ] **Frontend: Add admin API client methods** - `adminApi.listEditReviews()`, `adminApi.getEditReview(jobId)`
4. [ ] **Frontend: Create `/admin/edit-reviews/page.tsx`** - List page with table
5. [ ] **Frontend: Add sidebar nav item** - "Edit Reviews" with BookOpen icon
6. [ ] **Frontend: Create read-only review viewer** - Load snapshot data into LyricsAnalyzer with isReadOnly
7. [ ] **Frontend: Create edit log replay component** - Step-through UI for edit entries
8. [ ] **Frontend: Create raw data viewer** - JSON viewer with tabs
9. [ ] **Tests: Backend unit tests** for new admin endpoints
10. [ ] **Tests: Frontend component tests** for new page

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/api/routes/admin.py` | Modify | Add edit-reviews endpoints |
| `frontend/lib/api.ts` | Modify | Add adminApi methods for edit reviews |
| `frontend/app/admin/edit-reviews/page.tsx` | Create | Main edit reviews list page |
| `frontend/components/admin/edit-review-viewer.tsx` | Create | Read-only review wrapper using LyricsAnalyzer |
| `frontend/components/admin/edit-log-replay.tsx` | Create | Edit log stepper/replay component |
| `frontend/components/admin/raw-data-viewer.tsx` | Create | JSON viewer with tabs |
| `frontend/components/admin/admin-sidebar.tsx` | Modify | Add Edit Reviews nav item |
| `backend/tests/test_admin_edit_reviews.py` | Create | Backend tests |

## Testing Strategy

- Unit tests for backend endpoints (mock Firestore + GCS)
- Verify admin auth is required on all new endpoints
- Verify no write operations occur on read-only endpoints
- Frontend component tests for key interactions

## Open Questions

- [ ] Should we show ALL jobs that went through review, or only ones with edit logs? (Recommendation: only those with edit logs, since those are the interesting ones)
- [ ] Do we need audio playback in the admin review viewer? (Recommendation: yes, it helps understand what the user heard)
- [ ] Should the raw data be downloadable? (Nice-to-have, can add later)

## Rollback Plan

All changes are additive (new endpoints, new pages). Removing the sidebar link effectively hides the feature. No existing behavior is modified.
