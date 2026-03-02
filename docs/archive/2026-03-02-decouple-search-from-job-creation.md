# Decouple Audio Search from Job Creation

**Created:** 2026-03-02
**Branch:** feat/sess-20260302-1716-guided-submission-flow
**Status:** Implemented (v0.122.4)

---

## Background

### Phase 1 UX Plan (2026-02-28)

The Phase 1 UX overhaul plan (`docs/archive/2026-02-28-ux-phase1-job-creation.md`) identified this
issue and noted two options:

> **Option 1 (simpler):** Still create the job early but hide that from the user (keep current backend flow, change frontend presentation)
>
> **Option 2 (proper):** Defer job creation until the user confirms everything (would require backend changes)
>
> Option 1 is probably simpler and lower-risk.

Option 1 was implemented. But it turned out to cause real UX problems in production, so we're now
implementing Option 2 properly.

---

## Problem

The guided job creation flow (3-step wizard: Song Info → Find Audio → Customize & Create) creates a
backend job as soon as the user reaches Step 2 (audio search), before they have:
- Set the private flag
- Set display overrides (display_artist, display_title for the title card)
- Actually confirmed they want a job at all

**Problems caused:**

**A) Orphan job records** — Abandoned searches (user goes back or closes tab) leave job records in
Firestore. The "back" button calls `api.deleteJob()` as best-effort cleanup, but this fails silently
and the browser may close before the call completes.

**B) Wrong field values at creation** — The job is created with `is_private: false` and no display
overrides, because the user hasn't reached Step 3 yet. These are then patched via `selectAudioResult`
overrides. The job briefly exists with incorrect values.

**C) Jobs appear in Recent Jobs list** — Despite being in `AWAITING_AUDIO_SELECTION` state, these
jobs appear in the job list (`isInteractive` includes this status). Users see a confusing "ghost job"
before they've confirmed creation.

**D) Notification chime fires** — State transitions on the temporary job trigger the in-browser
action-required chime during what the user thinks is just a search step.

---

## Solution: Standalone Search Endpoint + Search Sessions

Add a stateless `POST /api/audio-search/search-standalone` endpoint that runs audio search without
creating a job. Store results temporarily in a `search_sessions` Firestore collection with 30-minute
TTL. Only create the job at Step 3 ("Customize & Create"), when the user has made all their choices
and explicitly clicks "Create Karaoke Video".

The old `POST /api/audio-search/search` endpoint remains **unchanged** for CLI backward compatibility.

---

## Architecture

### Current Flow (problematic)

```
Step 1: Enter artist/title
Step 2: POST /audio-search/search → Creates job → Polls for results → User picks result
Step 3: POST /audio-search/{job_id}/select → Triggers download
Problem: Job exists from Step 2 onward with incomplete data
```

### New Flow (correct)

```
Step 1: Enter artist/title
Step 2: POST /audio-search/search-standalone → Returns results + search_session_id (NO job)
Step 3: POST /api/jobs/create-from-search → Creates job + selects audio + triggers download
Better: Job only exists after user explicitly creates it, with all correct values from the start
```

### Key Design Decisions

1. **Synchronous search** — The standalone endpoint runs the search synchronously and returns
   results directly (~5–15s response time). No polling loop needed. Simpler frontend code than the
   current create-then-poll pattern.

2. **Firestore `search_sessions` collection** — Required because Cloud Run has multiple instances:
   a search on instance A must be retrievable when the job creation hits instance B. In-memory cache
   won't work.

3. **30-minute TTL** — Firestore's built-in TTL feature auto-deletes stale sessions. Same pattern as
   worker logs TTL in `infrastructure/modules/database.py`. No cleanup code needed.

4. **Security** — Frontend sends `search_session_id + selection_index`, not raw download URLs.
   Backend retrieves the trusted search results from Firestore. This prevents URL injection.

5. **`remote_search_id` preserved** — The flacfetch torrent download service uses this for
   concurrent-safe request routing. It's stored in the session doc and copied to the job at creation.

6. **No-results returns `[]`** — Empty search results return `{ results: [], results_count: 0 }`
   rather than a 404 error. This naturally removes the need for any special 404 error handling in the
   frontend `NoResultsSection` component.

---

## Implementation

### Phase 1: Backend — Search Session Storage

**Modify `backend/services/firestore_service.py`**

Add session CRUD methods (stays in the existing service, no new file needed):
```python
def create_search_session(self, session_data: dict) -> str
    # Stores in `search_sessions` collection, returns session_id
def get_search_session(self, session_id: str) -> Optional[dict]
    # Returns None if expired or not found
def delete_search_session(self, session_id: str) -> None
```

Session document schema:
```json
{
  "session_id": "<uuid>",
  "user_email": "user@example.com",
  "tenant_id": null,
  "artist": "The Beatles",
  "title": "Come Together",
  "results": [...],
  "remote_search_id": "...",
  "created_at": "<timestamp>",
  "ttl_expiry": "<timestamp + 30min>"
}
```

**Modify `infrastructure/modules/database.py`**

Add TTL policy (same pattern as existing worker logs TTL):
```python
resources["firestore_field_search_sessions_ttl"] = firestore.Field(
    "firestore-field-search-sessions-ttl",
    project=PROJECT_ID,
    database=firestore_db.name,
    collection="search_sessions",
    field="ttl_expiry",
    ttl_config={},   # Empty block enables TTL on this field
    index_config={}, # Disable indexing on TTL field
    opts=pulumi.ResourceOptions(depends_on=[firestore_db]),
)
```

### Phase 2: Backend — Standalone Search Endpoint

**Modify `backend/routers/audio_search.py`**

Add `POST /api/audio-search/search-standalone`:
```
Request:  { artist: str, title: str }
Auth:     Required (same tenant/auth handling as existing /search endpoint)
Response: { status, search_session_id, results: AudioSearchResultResponse[], results_count }

- Runs audio_search_service.search_async(artist, title) synchronously (await inline)
- On no results: returns { results: [], results_count: 0 }  — NOT a 404 error
- On results: creates search session in Firestore with ttl_expiry = now + 30min
- Returns results directly in response (no polling needed)
```

Credit check: verify user has credits but do NOT deduct yet (deduction happens at job creation).

### Phase 3: Backend — Create Job from Search Session

**Modify `backend/routers/audio_search.py`** (or `backend/routers/jobs.py`)

Add `POST /api/jobs/create-from-search`:
```
Request: {
  search_session_id: str,
  selection_index: int,
  artist: str,
  title: str,
  display_artist?: str,
  display_title?: str,
  is_private?: bool,
  theme_id?: str   (default: tenant/system default)
}
Auth:    Required (owner check — must match session's user_email)

- Retrieves search session from Firestore (404 if expired/not found)
- Validates selection_index is in range
- Creates job via job_manager.create_job() with ALL final values (no overrides needed later)
  - Job starts with correct is_private, display_artist, display_title from the beginning
  - Copies remote_search_id and results to job.state_data
  - Sets audio_source_type, source_name, source_id, etc.
- Triggers _download_audio_and_trigger_workers() as background task
  - Job goes directly to DOWNLOADING_AUDIO (skips AWAITING_AUDIO_SELECTION entirely)
- Deletes the search session (consumed)
- Returns { status, job_id }
- Error: 404 with "Search session expired — please search again" if session missing
```

### Phase 4: Frontend — API Client

**Modify `frontend/lib/api.ts`**

Add new methods (keep old `searchAudio`, `getAudioSearchResults`, `selectAudioResult` for backward compat):
```typescript
async searchStandalone(
  artist: string,
  title: string
): Promise<{ search_session_id: string; results: AudioSearchResult[]; results_count: number }>

async createJobFromSearch(params: {
  search_session_id: string
  selection_index: number
  artist: string
  title: string
  display_artist?: string
  display_title?: string
  is_private?: boolean
}): Promise<{ job_id: string }>
```

### Phase 5: Frontend — AudioSourceStep

**Modify `frontend/components/job/steps/AudioSourceStep.tsx`**

- Replace `api.searchAudio()` → `api.searchStandalone()`
- Remove `pollForResults()` — results returned synchronously in initial response
- Remove `jobId` local state (no job created during search step)
- Change `onJobCreated` callback → `onSearchCompleted(searchSessionId: string)` for search path
- Back button: remove `jobIdToCleanup` parameter — no job to clean up, sessions expire naturally
- NoResultsSection renders naturally when `results.length === 0 && !isSearching && !searchError`
  (no special 404 error code handling needed)
- Upload/URL fallback tabs: **unchanged** — still create jobs immediately in Step 2 (correct)

### Phase 6: Frontend — GuidedJobFlow

**Modify `frontend/components/job/GuidedJobFlow.tsx`**

- Add `searchSessionId: string | null` state (alongside existing `selectedResultIndex`)
- Add `handleSearchCompleted(sessionId: string)` callback
- Update `handleConfirm()`: call `api.createJobFromSearch({ search_session_id: searchSessionId, selection_index: selectedResultIndex, artist, title, display_artist, display_title, is_private })` to get `job_id`
- Simplify `handleBackFromAudio()`: just reset `searchSessionId` to null — no `api.deleteJob()` call
- Upload/URL `onFallbackComplete` callbacks: unchanged

### Phase 7: Testing

**Backend unit tests:**
- `search-standalone`: happy path, no results (returns `[]`), auth required
- `create-from-search`: happy path, expired session returns 404, invalid index, credit deduction
- Session Firestore CRUD (create, get, delete)
- Owner validation (can't access another user's session)

**Frontend unit tests (Jest):**
- `AudioSourceStep`: mock `api.searchStandalone`, verify no job ID, no polling
- `GuidedJobFlow`: mock `api.createJobFromSearch`, verify job created only on confirm
- NoResultsSection renders when `results.length === 0` (no 404 error handling)

**E2E regression (Playwright):**
- Full guided flow: verify 0 jobs created until "Create Karaoke Video" clicked
- Back at Step 2: confirm no job in Firestore
- Created job: verify `is_private`, `display_artist`, `display_title` correct from creation (not patched)

---

## Files Changed

| File | Change |
|------|--------|
| `backend/services/firestore_service.py` | Add search session CRUD methods |
| `backend/routers/audio_search.py` | Add `POST /api/audio-search/search-standalone` |
| `backend/routers/jobs.py` | Add `POST /api/jobs/create-from-search` |
| `infrastructure/modules/database.py` | Add TTL policy for `search_sessions` collection |
| `frontend/lib/api.ts` | Add `searchStandalone`, `createJobFromSearch` |
| `frontend/components/job/steps/AudioSourceStep.tsx` | Use standalone search, remove polling/job |
| `frontend/components/job/GuidedJobFlow.tsx` | Add session state, defer job creation |

**Unchanged:** Old `POST /api/audio-search/search` (CLI compat), upload/URL paths, `AWAITING_AUDIO_SELECTION` status (still used by CLI).

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Search session expires before user confirms | Return 404 with "Search expired — please search again", frontend shows retry prompt |
| Firestore TTL takes up to 72h to actually delete | Acceptable — documents are small, TTL is courtesy cleanup |
| Old browser cache hits old endpoints | Old endpoints remain functional — fully backward compatible |
| `search-standalone` blocks for 5–15s | Same latency as current `/search`, just no polling loop |

## Rollback Plan

- Old endpoints remain functional — no breaking changes to existing API
- If issues arise, revert `AudioSourceStep` to use `api.searchAudio()` (old endpoint)
- `search_sessions` Firestore collection can be dropped without any impact
