# Edit Input Audio — Phase 1: State Machine & Backend API

**Date:** 2026-03-08
**Parent:** [Master Plan](2026-03-08-edit-input-audio-master-plan.md)
**Status:** Planning

## Goal

Add the backend infrastructure for the "edit input audio" feature: new job states, audio processing endpoints (trim, cut, mute, join), waveform data generation for input audio, and the submit/finalize flow.

---

## 1. New Job States

### State Enum Additions

Add to `backend/models/job.py` `JobStatus`:

```python
AWAITING_AUDIO_EDIT = "awaiting_audio_edit"      # Blocking: waiting for user to edit audio
IN_AUDIO_EDIT = "in_audio_edit"                   # User is actively editing
AUDIO_EDIT_COMPLETE = "audio_edit_complete"        # User submitted edited audio
```

### State Transitions

Add to `STATE_TRANSITIONS` in `backend/models/job.py`:

```python
# After download, can optionally enter audio edit phase
JobStatus.DOWNLOADING: [
    ...,  # existing transitions
    JobStatus.AWAITING_AUDIO_EDIT,   # NEW: if requires_audio_edit
],

# Audio edit phase
JobStatus.AWAITING_AUDIO_EDIT: [
    JobStatus.IN_AUDIO_EDIT,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
],
JobStatus.IN_AUDIO_EDIT: [
    JobStatus.AWAITING_AUDIO_EDIT,    # User leaves without submitting
    JobStatus.AUDIO_EDIT_COMPLETE,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
],
JobStatus.AUDIO_EDIT_COMPLETE: [
    JobStatus.SEPARATING_STAGE1,      # Continue to normal processing
    JobStatus.TRANSCRIBING,
    JobStatus.GENERATING_SCREENS,
    JobStatus.FAILED,
],
```

### Audio Download Worker Changes

In `backend/workers/audio_download_worker.py`, after audio download completes:

```python
# Check if audio editing was requested
if job.state_data.get('requires_audio_edit'):
    # Transition to blocking audio edit state instead of triggering workers
    job_manager.transition_to_state(
        job_id=job_id,
        new_status=JobStatus.AWAITING_AUDIO_EDIT,
        progress=15,
        message="Audio downloaded. Please review and edit the audio before processing."
    )
    # Don't trigger workers yet — wait for user to complete audio edit
    return True

# Existing flow: trigger workers immediately
await _trigger_workers_parallel(job_id)
```

### Review Token Generation

In `job_manager.py` `transition_to_state()`, add `AWAITING_AUDIO_EDIT` to the review token generation check:

```python
if new_status in (JobStatus.AWAITING_REVIEW, JobStatus.AWAITING_AUDIO_EDIT):
    review_token = generate_review_token()
    updates['review_token'] = review_token
```

### Notification

In `job_notification_service.py`, add email notification for `AWAITING_AUDIO_EDIT`:

```python
async def send_audio_edit_email(self, job_id, user_email, artist, title):
    edit_url = f"{self.frontend_url}/app/jobs#/{job_id}/audio-edit"
    # Use existing email template pattern with new template
```

---

## 2. Audio Processing API Endpoints

All endpoints under the existing review router, authenticated via review token.

### `GET /api/review/{job_id}/input-audio-info`

Returns metadata about the input audio for the editor UI.

**Response:**
```json
{
    "duration_seconds": 245.3,
    "sample_rate": 44100,
    "channels": 2,
    "format": "flac",
    "file_size_bytes": 35000000,
    "waveform_data": {
        "amplitudes": [0.1, 0.3, 0.8, ...],   // 1000 points
        "duration": 245.3
    },
    "playback_url": "https://storage.googleapis.com/...",  // Signed URL (OGG Opus)
    "has_edits": false,
    "edit_count": 0
}
```

**Implementation:**
1. Read audio metadata from GCS (or cache in state_data after download)
2. Generate waveform data using existing `audio_analysis_service.get_waveform_data()`
3. Generate signed URL for playback (transcode to OGG if not already done)

### `POST /api/review/{job_id}/audio-edit/apply`

Apply an edit operation to the current audio. Returns updated metadata + waveform.

**Request body:**
```json
{
    "operation": "trim",
    "params": {
        "start_seconds": 0,
        "end_seconds": 32.5
    },
    "edit_id": "uuid-from-frontend"
}
```

**Supported operations:**

| Operation | Params | Description |
|-----------|--------|-------------|
| `trim_start` | `{ end_seconds: float }` | Remove audio from 0 to end_seconds |
| `trim_end` | `{ start_seconds: float }` | Remove audio from start_seconds to end |
| `cut` | `{ start_seconds: float, end_seconds: float }` | Remove a region, join remaining |
| `mute` | `{ start_seconds: float, end_seconds: float }` | Silence a region (preserve duration) |
| `join_start` | `{ upload_id: string }` | Prepend uploaded audio |
| `join_end` | `{ upload_id: string }` | Append uploaded audio |

**Response:**
```json
{
    "status": "success",
    "edit_id": "uuid",
    "edited_audio": {
        "duration_seconds": 212.8,
        "waveform_data": {
            "amplitudes": [...],
            "duration": 212.8
        },
        "playback_url": "https://...",  // Signed URL for edited audio
    },
    "edit_stack_size": 3,
    "can_undo": true,
    "can_redo": false
}
```

**Implementation (backend, server-side FFmpeg):**
1. Download current audio from GCS (original or latest edit)
2. Apply operation using FFmpeg:
   - `trim_start`: `ffmpeg -ss {end_seconds} -i input.flac output.flac`
   - `trim_end`: `ffmpeg -t {start_seconds} -i input.flac output.flac`
   - `cut`: `ffmpeg -filter_complex "[0]atrim=0:{start}[a];[0]atrim={end}[b];[a][b]concat=n=2:v=0:a=1"`
   - `mute`: `ffmpeg -af "volume=enable='between(t,{start},{end})':volume=0"`
   - `join_start/end`: `ffmpeg -filter_complex "[0][1]concat=n=2:v=0:a=1"`
3. Upload edited audio to GCS at `jobs/{job_id}/audio_edit/edit_{edit_id}.flac`
4. Transcode to OGG Opus for playback
5. Generate waveform data for edited audio
6. Update job state_data with edit stack

### `POST /api/review/{job_id}/audio-edit/undo`

Undo the last edit operation.

**Response:** Same as apply — returns the state after undo (previous edit or original).

**Implementation:**
- Maintain an edit stack in job `state_data`:
  ```python
  state_data['audio_edit_stack'] = [
      {"edit_id": "uuid1", "gcs_path": "jobs/.../edit_uuid1.flac", "operation": "trim_start", ...},
      {"edit_id": "uuid2", "gcs_path": "jobs/.../edit_uuid2.flac", "operation": "mute", ...},
  ]
  state_data['audio_edit_redo_stack'] = []
  ```
- Undo pops from edit_stack, pushes to redo_stack
- Returns waveform + playback URL for the now-current version

### `POST /api/review/{job_id}/audio-edit/redo`

Redo a previously undone edit.

**Implementation:** Pop from redo_stack, push to edit_stack.

### `POST /api/review/{job_id}/audio-edit/upload`

Upload additional audio for join operations.

**Request:** Multipart file upload (FLAC, WAV, MP3, M4A, OGG)

**Response:**
```json
{
    "upload_id": "uuid",
    "duration_seconds": 15.2,
    "waveform_data": { "amplitudes": [...], "duration": 15.2 }
}
```

**Implementation:**
1. Save to GCS at `jobs/{job_id}/audio_edit/upload_{upload_id}.flac`
2. Transcode to FLAC if not already
3. Return metadata + waveform

### `POST /api/review/{job_id}/audio-edit/submit`

Finalize the audio edit and continue processing.

**Request body:**
```json
{
    "edit_log": {
        "session_id": "uuid",
        "entries": [...],
        "started_at": "2026-03-08T12:00:00Z"
    }
}
```

**Response:**
```json
{
    "status": "success",
    "message": "Audio edit saved. Processing will continue with edited audio.",
    "job_id": "..."
}
```

**Implementation:**
1. Take the current top-of-stack audio (or original if no edits)
2. Copy to `jobs/{job_id}/input/edited.flac`
3. Update `job.input_media_gcs_path` to point to edited file
4. Save edit log to GCS
5. Transition state: `IN_AUDIO_EDIT` → `AUDIO_EDIT_COMPLETE`
6. Trigger parallel workers (audio separation + lyrics transcription)
7. Clean up intermediate edit files (optional, can defer)

---

## 3. Audio Processing Service

New service: `backend/services/audio_edit_service.py`

Encapsulates all FFmpeg operations for audio editing:

```python
class AudioEditService:
    def __init__(self, storage_service: StorageService):
        self.storage = storage_service

    def trim_start(self, input_path: str, end_seconds: float, output_path: str) -> AudioMetadata:
        """Remove audio from 0 to end_seconds."""

    def trim_end(self, input_path: str, start_seconds: float, output_path: str) -> AudioMetadata:
        """Remove audio from start_seconds to end."""

    def cut_region(self, input_path: str, start: float, end: float, output_path: str) -> AudioMetadata:
        """Remove a region and join remaining parts."""

    def mute_region(self, input_path: str, start: float, end: float, output_path: str) -> AudioMetadata:
        """Silence a region (preserve duration)."""

    def join_audio(self, input_path: str, other_path: str, position: str, output_path: str) -> AudioMetadata:
        """Join two audio files (position: 'start' or 'end')."""

    def get_metadata(self, audio_path: str) -> AudioMetadata:
        """Get duration, sample rate, channels, format."""
```

Each method returns `AudioMetadata(duration_seconds, sample_rate, channels, format, file_size_bytes)`.

---

## 4. State Data Schema

The job's `state_data` dict gets these new fields:

```python
{
    "requires_audio_edit": bool,           # Set during job creation
    "audio_edit_stack": [                  # Edit history for undo/redo
        {
            "edit_id": str,
            "operation": str,
            "params": dict,
            "gcs_path": str,               # Path to audio file after this edit
            "duration_seconds": float,
            "timestamp": str,
        }
    ],
    "audio_edit_redo_stack": [...],         # Undone edits available for redo
    "audio_edit_uploads": {                 # Uploaded audio for join operations
        "upload_id": { "gcs_path": str, "duration_seconds": float }
    },
    "original_input_media_gcs_path": str,  # Preserved original before edit
}
```

---

## 5. AutoProcessor Integration

Update `frontend/components/AutoProcessor.tsx` to handle `awaiting_audio_edit`:

For `non_interactive=true` jobs, auto-skip the audio edit phase (submit with no edits):

```typescript
case 'awaiting_audio_edit':
    processJob(job, 'audio_edit')  // Auto-submit with no edits
    break
```

---

## 6. Job Status Display

Update `frontend/lib/job-status.ts` to include new states:

```typescript
awaiting_audio_edit: {
    label: "Edit Audio",
    description: "Review and edit the input audio",
    color: "yellow",
    actionRequired: true,
},
in_audio_edit: {
    label: "Editing Audio",
    description: "Audio is being edited",
    color: "blue",
},
audio_edit_complete: {
    label: "Audio Edited",
    description: "Audio edit complete, starting processing",
    color: "green",
},
```

---

## Files Changed

| File | Type | Changes |
|------|------|---------|
| `backend/models/job.py` | Edit | Add 3 new states, update STATE_TRANSITIONS |
| `backend/services/job_manager.py` | Edit | Review token for AWAITING_AUDIO_EDIT, notification trigger |
| `backend/services/audio_edit_service.py` | New | FFmpeg audio editing operations |
| `backend/api/routes/review.py` | Edit | Add 6 new endpoints for audio editing |
| `backend/workers/audio_download_worker.py` | Edit | Check requires_audio_edit, conditional state transition |
| `backend/services/job_notification_service.py` | Edit | Email notification for awaiting_audio_edit |
| `frontend/lib/job-status.ts` | Edit | New state display config |
| `frontend/components/AutoProcessor.tsx` | Edit | Handle awaiting_audio_edit for non-interactive |

## Testing Strategy

| Test | Type | What |
|------|------|------|
| `test_audio_edit_service.py` | Unit | FFmpeg operations (trim, cut, mute, join) with real audio files |
| `test_audio_edit_endpoints.py` | Integration | API endpoint tests with auth, state validation |
| `test_audio_edit_state_transitions.py` | Unit | State machine transitions for new states |
| `test_audio_download_edit_flag.py` | Unit | Download worker respects requires_audio_edit flag |

## Open Questions

1. **Edit stack size limit?** — Should we cap the number of edits before requiring the user to submit? Probably not; let them edit freely. But we should clean up intermediate GCS files after submission.

2. **Real-time preview vs apply-then-preview?** — For MVP, each operation applies server-side and returns the result (apply-then-preview). Real-time client-side preview could come later using Web Audio API for simple operations like mute.

3. **Concurrent editing protection?** — Should we prevent multiple browser tabs from editing the same job? The `in_audio_edit` state transition provides some protection, but we should also use optimistic locking on the edit stack.

4. **Audio format for edits?** — Keep everything in FLAC for lossless quality. Transcode to OGG Opus for all playback URLs (same pattern as existing review audio transcoding). Users should never wait for large FLAC files to stream.
