# Edit Input Audio — Phase 2: Session Persistence

**Date:** 2026-03-08
**Parent:** [Master Plan](2026-03-08-edit-input-audio-master-plan.md)
**Status:** Planning
**Depends on:** Phase 1

## Goal

Persist every audio edit operation so users can resume editing after closing their browser, and admins can review/replay audio edit sessions in the dashboard. Follows the same architectural pattern as lyrics review session persistence.

---

## 1. Data Model

### Firestore Subcollection

`jobs/{job_id}/audio_edit_sessions/{session_id}`

Follows the existing `review_sessions` subcollection pattern.

```python
# backend/models/audio_edit_session.py

@dataclass
class AudioEditSession:
    session_id: str                          # UUID
    job_id: str                              # Parent job ID (denormalized)
    user_email: str                          # Who created this session
    created_at: datetime                     # First save timestamp
    updated_at: datetime                     # Last modification
    edit_count: int                          # Number of edit operations
    trigger: str                             # "auto" | "manual" | "submit"
    audio_duration_seconds: float | None     # Current edited audio duration
    original_duration_seconds: float | None  # Original audio duration
    artist: str | None                       # Denormalized from job
    title: str | None                        # Denormalized from job
    data_hash: str                           # SHA256 of edit_data for deduplication

    # Summary for list views
    summary: AudioEditSessionSummary

    # Full edit data stored in GCS (NOT in Firestore doc)
    edit_data_gcs_path: str                  # "jobs/{jobId}/audio_edit_sessions/{sessionId}.json"


@dataclass
class AudioEditSessionSummary:
    total_operations: int                    # Number of edit operations
    operations_breakdown: dict[str, int]     # {"trim_start": 2, "mute": 1, ...}
    duration_change_seconds: float           # How much duration changed
    net_duration_seconds: float              # Current audio duration
```

### GCS Edit Data

Stored at `jobs/{job_id}/audio_edit_sessions/{session_id}.json`:

```json
{
    "session_id": "uuid",
    "job_id": "abc123",
    "started_at": "2026-03-08T12:00:00Z",
    "entries": [
        {
            "id": "uuid-entry-1",
            "timestamp": "2026-03-08T12:01:30Z",
            "operation": "trim_start",
            "params": { "end_seconds": 32.5 },
            "duration_before": 245.3,
            "duration_after": 212.8,
            "audio_gcs_path": "jobs/abc123/audio_edit/edit_uuid1.flac"
        },
        {
            "id": "uuid-entry-2",
            "timestamp": "2026-03-08T12:03:15Z",
            "operation": "mute",
            "params": { "start_seconds": 45.0, "end_seconds": 48.2 },
            "duration_before": 212.8,
            "duration_after": 212.8,
            "audio_gcs_path": "jobs/abc123/audio_edit/edit_uuid2.flac"
        }
    ],
    "undo_count": 0,
    "redo_count": 0
}
```

---

## 2. API Endpoints

### `POST /api/review/{job_id}/audio-edit-sessions`

Save an audio edit session snapshot.

**Request body:**
```json
{
    "edit_data": {
        "entries": [...],
        "started_at": "..."
    },
    "edit_count": 5,
    "trigger": "auto",
    "summary": {
        "total_operations": 5,
        "operations_breakdown": { "trim_start": 1, "mute": 2, "cut": 2 },
        "duration_change_seconds": -32.5,
        "net_duration_seconds": 212.8
    }
}
```

**Response:**
```json
{
    "status": "saved" | "skipped",
    "session_id": "uuid",
    "created_at": "...",
    "reason": "identical_data"  // Only if skipped
}
```

**Implementation:**
1. Compute SHA256 hash of `edit_data`
2. Compare with latest session's `data_hash` — skip if identical
3. Upload `edit_data` to GCS
4. Save metadata to Firestore subcollection

### `GET /api/review/{job_id}/audio-edit-sessions`

List all audio edit sessions for a job (metadata only).

**Response:**
```json
{
    "sessions": [
        {
            "session_id": "...",
            "created_at": "...",
            "updated_at": "...",
            "edit_count": 5,
            "trigger": "auto",
            "summary": { ... }
        }
    ]
}
```

Ordered by `updated_at` descending.

### `GET /api/review/{job_id}/audio-edit-sessions/{session_id}`

Get full session with edit_data loaded from GCS.

### `DELETE /api/review/{job_id}/audio-edit-sessions/{session_id}`

Delete a session (admin or session owner).

---

## 3. Firestore Service Methods

Add to `backend/services/firestore_service.py`:

```python
def save_audio_edit_session(self, job_id: str, session: AudioEditSession) -> None
def list_audio_edit_sessions(self, job_id: str) -> list[AudioEditSession]
def get_audio_edit_session(self, job_id: str, session_id: str) -> AudioEditSession | None
def delete_audio_edit_session(self, job_id: str, session_id: str) -> None
def delete_audio_edit_sessions_subcollection(self, job_id: str) -> None
def get_latest_audio_edit_session_hash(self, job_id: str) -> str | None
```

These follow the exact same pattern as the existing `review_sessions` methods.

---

## 4. Deduplication Strategy

Same as lyrics review sessions:
- Compute SHA256 hash of the `edit_data` JSON (sorted keys)
- Skip save if hash matches the most recent session's `data_hash`
- Prevents duplicate saves during rapid edits or undo/redo cycles

---

## 5. Job Deletion Cascade

When a job is deleted, cascade delete:
- `audio_edit_sessions` subcollection (all Firestore docs)
- `jobs/{job_id}/audio_edit_sessions/` GCS prefix (all session files)
- `jobs/{job_id}/audio_edit/` GCS prefix (all intermediate edit files)

Add to existing job deletion logic in `job_manager.py`.

---

## 6. Retention Policy

| Session Type | Retention |
|-------------|-----------|
| Auto-save (`trigger: "auto"`) | 90 days TTL |
| Manual save (`trigger: "manual"`) | Indefinite |
| Submit save (`trigger: "submit"`) | Indefinite |

Follow the same TTL approach as lyrics review sessions.

---

## Files Changed

| File | Type | Changes |
|------|------|---------|
| `backend/models/audio_edit_session.py` | New | AudioEditSession dataclass |
| `backend/services/firestore_service.py` | Edit | Add CRUD methods for audio_edit_sessions subcollection |
| `backend/api/routes/review.py` | Edit | Add session save/list/get/delete endpoints |
| `backend/services/job_manager.py` | Edit | Cascade delete audio_edit_sessions on job deletion |

## Testing Strategy

| Test | Type | What |
|------|------|------|
| `test_audio_edit_session_crud.py` | Unit | Create, list, get, delete sessions |
| `test_audio_edit_session_dedup.py` | Unit | Hash-based deduplication |
| `test_audio_edit_session_endpoints.py` | Integration | API endpoints with auth |
| `test_audio_edit_session_cascade.py` | Unit | Job deletion cleans up sessions |
