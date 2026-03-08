# Lyrics Review Session Backup & Restore

**Date:** 2026-03-07
**Branch:** `feat/sess-20260307-2332-lyrics-review-sessions`
**Status:** Planning

## Problem

The lyrics review process can take 1+ hours for difficult songs. Currently, progress is saved to browser localStorage using a fragile key based on a hash of the first segment's text. This has several issues:

1. **Fragile matching** — hash of first segment text can collide or mismatch across jobs
2. **Browser-local only** — lost on browser clear, different device, or incognito
3. **No history** — only stores the latest state, no way to browse previous sessions
4. **Confusing restore UX** — bare `window.confirm()` dialog with no context about what's being restored
5. **No cross-job restore** — can't apply edits from a previous job to a new one (needed for style-change redeliveries)

## Solution Overview

Server-side review session storage with automatic periodic backup, rich restore UI, and cross-job session search.

### Phases

| Phase | Scope | Description |
|-------|-------|-------------|
| 1 | Backend API + storage | Firestore subcollection, save/list/get endpoints |
| 2 | Auto-save from frontend | Periodic backup (60s interval, on preview, on dirty changes) |
| 3 | Restore UI | Session browser with edit preview, auto-prompt on load |
| 4 | Cross-job restore | Search sessions across jobs, duration mismatch warning |

---

## Phase 1: Backend API + Storage

### Data Model

Store sessions in a Firestore **subcollection**: `jobs/{jobId}/review_sessions/{sessionId}`

This follows the existing pattern used for worker logs (`jobs/{jobId}/logs/{logId}`).

#### `ReviewSession` document schema

```python
{
    "session_id": str,              # Auto-generated document ID
    "job_id": str,                  # Parent job ID (denormalized for cross-job queries)
    "user_email": str,              # Who created this session
    "created_at": datetime,         # First save timestamp
    "updated_at": datetime,         # Last save timestamp
    "edit_count": int,              # Number of edits in this session
    "is_auto_save": bool,           # True for periodic auto-saves, False for manual saves
    "trigger": str,                 # "auto" | "preview" | "manual"
    "audio_duration_seconds": float | None,  # Input audio duration (for cross-job matching)
    "artist": str | None,           # From correction_data.metadata
    "title": str | None,            # From correction_data.metadata

    # Summary for list view (avoid loading full correction_data)
    "summary": {
        "total_segments": int,
        "total_words": int,
        "corrections_made": int,
        "changed_words": [           # First N changed words for preview
            {"original": str, "corrected": str, "segment_index": int}
        ],
    },

    # Full correction data (stored in GCS, NOT in Firestore doc)
    "correction_data_gcs_path": str,  # "jobs/{jobId}/review_sessions/{sessionId}.json"
}
```

**Why GCS for correction_data?** CorrectionData can be large (100KB+). Firestore has a 1MB document limit, and storing large blobs in Firestore is inefficient for reads/writes. The summary field provides enough info for list views without loading full data.

#### Store audio duration on Job

Add `input_audio_duration_seconds: Optional[float]` to the `Job` model. Populate it during job creation (audio worker already has access to ffprobe). This enables cross-job duration comparison without re-computing.

### API Endpoints

All under the existing review router (`/api/review/{job_id}/...`), using the existing `require_review_auth` dependency.

#### `POST /api/review/{job_id}/sessions`

Save a review session snapshot.

**Request body:**
```json
{
    "correction_data": { ... },
    "edit_count": 42,
    "trigger": "auto",
    "summary": {
        "total_segments": 15,
        "total_words": 230,
        "corrections_made": 12,
        "changed_words": [
            {"original": "world", "corrected": "word", "segment_index": 3}
        ]
    }
}
```

**Response:** `{ "session_id": "abc123", "created_at": "..." }`

**Logic:**
1. Upload `correction_data` to GCS at `jobs/{jobId}/review_sessions/{sessionId}.json`
2. Create Firestore subcollection document with metadata + summary
3. Denormalize `job_id`, `artist`, `title`, `audio_duration_seconds` from parent job

#### `GET /api/review/{job_id}/sessions`

List all sessions for a job (metadata only, no correction_data).

**Response:**
```json
{
    "sessions": [
        {
            "session_id": "abc123",
            "created_at": "2026-03-07T12:00:00Z",
            "updated_at": "2026-03-07T12:15:00Z",
            "edit_count": 42,
            "trigger": "auto",
            "summary": { ... },
            "user_email": "andrew@..."
        }
    ]
}
```

Ordered by `updated_at` descending. No pagination needed initially (unlikely to exceed 50 sessions per job).

#### `GET /api/review/{job_id}/sessions/{session_id}`

Get full session data (loads correction_data from GCS).

**Response:** Full session document + `correction_data` from GCS.

#### `DELETE /api/review/{job_id}/sessions/{session_id}`

Delete a session (admin only). Removes both Firestore doc and GCS file.

#### `GET /api/review/sessions/search` (Phase 4)

Cross-job session search. Requires admin auth.

**Query params:** `?q=<search>&limit=20`

Search by artist, title, or job_id across all jobs. Uses Firestore collection group query on `review_sessions`.

**Response:** Same as list endpoint but includes `job_id`, `artist`, `title` per session.

### Backend Implementation Files

| File | Changes |
|------|---------|
| `backend/models/review_session.py` | New: `ReviewSession` dataclass |
| `backend/models/job.py` | Add `input_audio_duration_seconds` field |
| `backend/services/firestore_service.py` | Add session CRUD methods using subcollection pattern |
| `backend/api/routes/review.py` | Add session endpoints |
| `backend/workers/audio_worker.py` | Store audio duration on job after ffprobe |

### Cleanup / Retention

- Auto-save sessions older than 90 days: eligible for cleanup (can add TTL field like worker logs)
- Manual/preview-triggered sessions: kept indefinitely
- When a job is deleted, cascade delete its review_sessions subcollection

---

## Phase 2: Auto-Save from Frontend

### Save Strategy

Replace the current localStorage-on-every-edit approach with a smarter dual strategy:

1. **localStorage** — continues to save on every edit (instant, for crash recovery within same browser)
2. **Server backup** — periodic saves to the backend API

### Server Backup Triggers

| Trigger | Condition | `trigger` field |
|---------|-----------|-----------------|
| Periodic timer | Every 60 seconds, IF at least 1 edit since last backup | `"auto"` |
| Preview video | When user generates a preview video | `"preview"` |
| Page unload | `beforeunload` event, IF dirty since last backup | `"auto"` |
| Manual save | User clicks explicit "Save Progress" button | `"manual"` |

### Dirty Tracking

Track a `lastBackupEditCount` ref. Compare against current `history.length` to determine if there are new edits since last backup.

### Implementation

New custom hook: `useReviewSessionAutoSave()`

```typescript
// frontend/hooks/use-review-session-autosave.ts

function useReviewSessionAutoSave({
  jobId: string,
  data: CorrectionData,
  initialData: CorrectionData,
  historyLength: number,
  isReadOnly: boolean,
  apiClient: ReviewApiClient,
}) {
  // Track last backup state
  const lastBackupEditCount = useRef(0)

  // Compute summary from data diff (changed words, counts)
  const computeSummary = useCallback(...)

  // Save function (called by timer, preview trigger, etc.)
  const saveSession = useCallback(async (trigger: string) => {
    if (historyLength <= 1) return  // No edits yet
    if (historyLength === lastBackupEditCount.current) return  // No new edits

    await apiClient.saveReviewSession(jobId, {
      correction_data: data,
      edit_count: historyLength - 1,
      trigger,
      summary: computeSummary(initialData, data),
    })

    lastBackupEditCount.current = historyLength
  }, [data, historyLength, ...])

  // 60-second interval
  useEffect(() => {
    if (isReadOnly) return
    const timer = setInterval(() => saveSession("auto"), 60_000)
    return () => clearInterval(timer)
  }, [saveSession, isReadOnly])

  // beforeunload
  useEffect(() => {
    const handler = () => saveSession("auto")
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [saveSession])

  return { saveSession }  // Expose for preview trigger
}
```

### Frontend API Client Additions

```typescript
// In frontend/lib/api.ts - lyricsReviewApi

async saveReviewSession(jobId, data): Promise<{ session_id: string }>
async listReviewSessions(jobId): Promise<{ sessions: ReviewSession[] }>
async getReviewSession(jobId, sessionId): Promise<ReviewSessionFull>
async deleteReviewSession(jobId, sessionId): Promise<void>
async searchReviewSessions(query): Promise<{ sessions: ReviewSession[] }>  // Phase 4
```

### Frontend Files

| File | Changes |
|------|---------|
| `frontend/hooks/use-review-session-autosave.ts` | New: auto-save hook |
| `frontend/lib/api.ts` | Add session API methods |
| `frontend/lib/lyrics-review/types.ts` | Add `ReviewSession`, `ReviewSessionSummary` types |
| `frontend/components/lyrics-review/LyricsAnalyzer.tsx` | Wire up auto-save hook, add "Save Progress" button |

---

## Phase 3: Restore UI

### Behavior on Load

When `LyricsAnalyzer` mounts:

1. Call `GET /api/review/{job_id}/sessions` to check for saved sessions
2. **If sessions exist**: show the Session Restore dialog (replaces the old `window.confirm`)
3. **If no sessions**: proceed normally (also check localStorage as fallback for backward compat)

### Session Restore Dialog

A modal dialog with a split-pane layout:

```
┌─────────────────────────────────────────────────────────┐
│  Saved Review Sessions                            [×]   │
├───────────────────────┬─────────────────────────────────┤
│ Session List          │ Edit Preview                    │
│                       │                                 │
│ ▸ Mar 7, 2:15 PM     │ Segment 3:                      │
│   42 edits · preview  │   "hello world" → "hello word"  │
│   [Most recent]       │                                 │
│                       │ Segment 7:                      │
│ ▸ Mar 7, 1:30 PM     │   "singing loud" → "singing out"│
│   38 edits · auto     │                                 │
│                       │ Segment 12:                     │
│ ▸ Mar 6, 11:00 PM    │   (deleted word "um")           │
│   15 edits · auto     │                                 │
│                       │ ... +39 more changes            │
│                       │                                 │
├───────────────────────┴─────────────────────────────────┤
│            [Start Fresh]            [Restore Selected]  │
└─────────────────────────────────────────────────────────┘
```

#### Session List (left pane)

Each session shows:
- Date/time (relative: "2 hours ago", absolute on hover)
- Edit count
- Trigger badge: `auto` / `preview` / `manual`
- Selected state (highlight)

#### Edit Preview (right pane)

When a session is selected, show a compact diff view:
- List of changed words grouped by segment
- Show original → corrected text
- Color-coded: green for additions, red for deletions, yellow for changes
- Scroll if many edits, with a summary count at bottom

#### Actions

- **Start Fresh** — dismiss dialog, use initial data (no restore)
- **Restore Selected** — load the selected session's full `correction_data`, replace current state

### "Save Progress" Button & Session Browser

Add to the LyricsAnalyzer toolbar:
- **Save Progress** button (triggers manual save)
- **Session History** button — opens the same Session Restore dialog at any time (not just on load)

### Frontend Files

| File | Changes |
|------|---------|
| `frontend/components/lyrics-review/SessionRestoreDialog.tsx` | New: the restore modal |
| `frontend/components/lyrics-review/SessionListItem.tsx` | New: session list item component |
| `frontend/components/lyrics-review/SessionEditPreview.tsx` | New: edit diff preview |
| `frontend/components/lyrics-review/LyricsAnalyzer.tsx` | Mount dialog on load, add toolbar buttons |

---

## Phase 4: Cross-Job Session Restore

### Power User Feature

Add a tab/toggle in the Session Restore dialog: **"This Job"** | **"All Jobs"**

When "All Jobs" is selected:
- Show a search box (search by artist, title, or job ID)
- Query `GET /api/review/sessions/search?q=...`
- Results show sessions from other jobs with job context (artist - title, job ID)

### Duration Mismatch Warning

When restoring from a different job:

1. Compare `session.audio_duration_seconds` with current job's `audio_duration_seconds`
2. If durations differ by more than 2 seconds, show a warning:

```
⚠️ Duration Mismatch

The selected session is from a track with a different duration:
  This job: 3:42 (222s)
  Source session: 4:01 (241s)

Timing alignment may not match. Word timestamps from the
source session will be preserved as-is.

[Cancel]  [Restore Anyway]
```

3. If durations match (within 2s), restore silently

### Collection Group Index

Firestore collection group queries require a **composite index** on `review_sessions`:
- Fields: `artist` (ASC), `title` (ASC), `updated_at` (DESC)
- Add via `firestore.indexes.json` or Pulumi

### Backend: Search Implementation

The search endpoint uses Firestore collection group query:

```python
@router.get("/sessions/search")
async def search_sessions(q: str, limit: int = 20, auth=Depends(require_admin)):
    # Collection group query across all jobs' review_sessions
    sessions_ref = db.collection_group("review_sessions")

    # Search by artist OR title containing query (case-insensitive prefix)
    # Firestore doesn't support LIKE queries, so we use range queries
    # for prefix matching, or store lowercase variants for matching
    results = sessions_ref.where("artist_lower", ">=", q.lower())
                          .where("artist_lower", "<=", q.lower() + "\uf8ff")
                          .limit(limit)
                          .get()

    # Also search by title and job_id, merge results
    ...
```

**Note:** Firestore's text search is limited. For the MVP, searching by exact job_id + prefix matching on artist/title should be sufficient. If full-text search is needed later, consider Algolia or a simple in-memory filter on the client after fetching recent sessions.

### Frontend Files

| File | Changes |
|------|---------|
| `frontend/components/lyrics-review/SessionRestoreDialog.tsx` | Add "All Jobs" tab, search input |
| `frontend/components/lyrics-review/DurationWarning.tsx` | New: duration mismatch warning |
| `frontend/lib/api.ts` | Add `searchReviewSessions()` |

---

## Testing Strategy

### Backend Tests

| Test | Type | What |
|------|------|------|
| `test_review_session_crud.py` | Unit | Create, list, get, delete sessions via Firestore service |
| `test_review_session_endpoints.py` | Integration | API endpoint tests with auth |
| `test_review_session_gcs.py` | Unit | GCS upload/download of correction data |
| `test_audio_duration_storage.py` | Unit | Duration stored on job creation |

### Frontend Tests

| Test | Type | What |
|------|------|------|
| `SessionRestoreDialog.test.tsx` | Unit | Renders session list, selection, restore action |
| `SessionEditPreview.test.tsx` | Unit | Diff computation and display |
| `use-review-session-autosave.test.ts` | Unit | Timer behavior, dirty tracking, save triggers |
| `lyrics-review-sessions.spec.ts` | E2E | Full flow: edit → auto-save → reload → restore |

---

## Migration / Backward Compatibility

1. **localStorage fallback** — keep the existing localStorage load/save as a fallback. If no server sessions exist, check localStorage. Gradually phase out once server sessions are proven reliable.
2. **No schema migration needed** — new Firestore subcollection, new GCS paths. No existing data affected.
3. **Feature flag** — not needed; server session save is additive. The restore dialog replaces `window.confirm()` which is strictly better.

---

## Open Questions

1. **Session limit per job?** — Should we cap at e.g. 50 sessions per job and auto-prune oldest auto-saves? Probably yes to avoid unbounded growth.
2. **Session deduplication** — If user makes 0 additional edits between auto-save intervals, we skip the save (dirty tracking handles this). But should we also deduplicate if the correction_data hasn't actually changed (e.g. undo then redo)?
3. **Compression** — CorrectionData JSON can be large. Should we gzip before storing in GCS? Probably not for MVP; GCS handles compression at the storage layer.

---

## Estimated Scope

| Phase | Backend | Frontend | Total |
|-------|---------|----------|-------|
| 1 | ~300 lines | 0 | Medium |
| 2 | 0 | ~150 lines | Small |
| 3 | 0 | ~400 lines | Medium |
| 4 | ~100 lines | ~200 lines | Medium |
| Tests | ~300 lines | ~300 lines | Medium |

Phases 1-3 form the core feature. Phase 4 (cross-job) can be deferred to a follow-up PR if needed.
