# Karaoke Generation Workers

This directory contains background workers that handle long-running karaoke generation tasks.

## Overview

Workers are asynchronous Python functions that process different stages of the karaoke generation workflow. They are triggered via internal HTTP API endpoints and run as FastAPI background tasks.

## Architecture

```
Job Submission
    │
    ├─→ Audio Worker (parallel)
    │   ├─→ Stage 1: Clean instrumental (3-5 min)
    │   ├─→ Stage 2: Backing vocals (2-3 min)
    │   └─→ mark_audio_complete()
    │
    └─→ Lyrics Worker (parallel)
        ├─→ Fetch reference lyrics
        ├─→ Transcribe with AudioShake (1-2 min)
        ├─→ Auto-correct
        └─→ mark_lyrics_complete()
    
    ↓ (when both complete)
    
Screens Worker (auto-triggered)
    ├─→ Generate title screen
    ├─→ Generate end screen
    └─→ AWAITING_INSTRUMENTAL_SELECTION
    
    ↓ (user selects)
    
Video Worker (user-triggered)
    ├─→ Remux with instrumental
    ├─→ Encode multiple formats
    ├─→ Package (CDG, TXT)
    └─→ COMPLETE
```

## Workers

### 1. Audio Worker (`audio_worker.py`)

**Purpose:** Separates audio into stems using GPU-accelerated Modal API

**Stages:**
1. **Stage 1:** Clean instrumental separation
   - Model: `model_bs_roformer_ep_317_sdr_12.9755.ckpt`
   - Also: 6-stem separation (bass, drums, guitar, piano, other, vocals)
   - Time: 3-5 minutes
   
2. **Stage 2:** Backing vocals separation
   - Model: `mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt`
   - Separates lead vocals from backing vocals
   - Time: 2-3 minutes

3. **Post-Processing:**
   - Combine instrumentals
   - Normalize audio levels
   - Upload all stems to GCS

**Triggers:** Automatically on job creation  
**Next Stage:** Coordinates with lyrics worker  
**State Transitions:** `PENDING` → `SEPARATING_STAGE1` → `SEPARATING_STAGE2` → `AUDIO_COMPLETE`

**Integration:** Uses `karaoke_gen.AudioProcessor` with remote Modal API

---

### 2. Lyrics Worker (`lyrics_worker.py`)

**Purpose:** Fetches, transcribes, and corrects lyrics

**Stages:**
1. **Fetch Reference Lyrics:**
   - Sources: Genius, Spotify, Musixmatch (via RapidAPI)
   - Fallback order: Genius → Spotify → Musixmatch
   - Time: <1 minute

2. **Transcribe Audio:**
   - Service: AudioShake API
   - Output: Word-level timestamps with confidence scores
   - Time: 1-2 minutes

3. **Automatic Correction:**
   - Algorithm: `LyricsTranscriber` (ExtendAnchorHandler, SyllablesMatchHandler)
   - Matches transcription to reference lyrics
   - Fixes common errors (homophones, etc.)
   - Generates corrections JSON
   - Time: 30 seconds

4. **Upload for Review:**
   - corrections.json → For review interface
   - audio.flac → For playback
   - reference.txt → For comparison

**Triggers:** Automatically on job creation  
**Next Stage:** `AWAITING_REVIEW` (human interaction required)  
**State Transitions:** `PENDING` → `TRANSCRIBING` → `CORRECTING` → `LYRICS_COMPLETE` → `AWAITING_REVIEW`

**Integration:** Uses `karaoke_gen.LyricsProcessor` and `LyricsTranscriber`

---

### 3. Screens Worker (`screens_worker.py`)

**Purpose:** Generates title and end screen videos

**Stages:**
1. **Generate Title Screen:**
   - Artist and song title
   - Style parameters (font, colors, background)
   - Output: `.mov` file
   - Time: 10-15 seconds

2. **Generate End Screen:**
   - "Thank you for singing!" message
   - Matching style
   - Output: `.mov` file
   - Time: 10-15 seconds

3. **Upload to GCS:**
   - Both screens uploaded
   - URLs stored in job metadata

4. **Countdown Padding (if needed):**
   - Detects if countdown was added to vocals
   - Pads instrumentals to match
   - Re-uploads padded versions

**Triggers:** Automatically when audio + lyrics both complete  
**Next Stage:** `AWAITING_INSTRUMENTAL_SELECTION` (human interaction required)  
**State Transitions:** `AUDIO_COMPLETE + LYRICS_COMPLETE` → `GENERATING_SCREENS` → `AWAITING_INSTRUMENTAL_SELECTION`

**Integration:** Uses `karaoke_gen.VideoGenerator`

---

### 4. Video Worker (`video_worker.py`) - TODO

**Purpose:** Generates final karaoke videos in multiple formats

**Stages:**
1. **Remux:**
   - Combine lyrics video with selected instrumental
   - Add title and end screens
   - Create base karaoke video

2. **Encode Multiple Formats:**
   - Lossless 4K MP4 (PCM audio)
   - Lossless 4K MKV (FLAC audio)
   - Lossy 4K MP4 (AAC audio)
   - Lossy 720p MP4 (AAC audio)
   - Time: 15-20 minutes

3. **Package:**
   - CDG+MP3 ZIP (karaoke machines)
   - TXT+MP3 ZIP (simple apps)

4. **Upload:**
   - All formats to GCS
   - Optional: YouTube upload
   - Optional: Dropbox sync

**Triggers:** User selects instrumental  
**Next Stage:** `COMPLETE` or `UPLOADING`  
**State Transitions:** `INSTRUMENTAL_SELECTED` → `GENERATING_VIDEO` → `ENCODING` → `PACKAGING` → `COMPLETE`

**Integration:** Uses `karaoke_gen.KaraokeFinalise` (Cloud Build recommended for encoding)

**Status:** Not yet implemented

---

## Worker Pattern

### SOLID Principles

Each worker follows SOLID principles:

**Single Responsibility:** Each worker handles exactly one stage
- Audio: Only audio separation
- Lyrics: Only transcription/correction
- Screens: Only screen generation
- Video: Only final video generation

**Open/Closed:** Extensible without modification
- New workers can be added easily
- Style parameters allow customization
- No modification of existing workers needed

**Liskov Substitution:** Interface consistency
- All workers follow same pattern
- Can swap implementations
- `karaoke_gen` classes are abstractions

**Interface Segregation:** Focused interfaces
- No bloated base class
- Each worker has minimal dependencies
- Clear, focused API

**Dependency Inversion:** Depends on abstractions
- Workers use `karaoke_gen` classes (abstractions)
- Not coupled to specific implementations
- Easy to test and mock

### Common Pattern

All workers follow this structure:

```python
async def process_stage(job_id: str) -> bool:
    """Main entry point for worker."""
    job_manager = JobManager()
    storage = StorageService()
    settings = get_settings()
    
    # Get job
    job = job_manager.get_job(job_id)
    
    # Create temp directory
    temp_dir = tempfile.mkdtemp(prefix=f"karaoke_{stage}_{job_id}_")
    
    try:
        # Transition to processing state
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.PROCESSING_STATE,
            progress=X,
            message="Processing..."
        )
        
        # Download inputs from GCS
        inputs = await download_inputs(...)
        
        # Process with karaoke_gen
        outputs = await process_with_karaoke_gen(inputs)
        
        # Upload outputs to GCS
        await upload_outputs(outputs)
        
        # Transition to complete state
        job_manager.transition_to_state(
            job_id=job_id,
            new_status=JobStatus.COMPLETE_STATE,
            progress=Y,
            message="Complete"
        )
        
        # Trigger next stage if applicable
        job_manager.mark_stage_complete(job_id)
        
        return True
        
    except Exception as e:
        # Structured error handling
        job_manager.mark_job_failed(
            job_id=job_id,
            error_message=str(e),
            error_details={"stage": "stage_name", "error": str(e)}
        )
        return False
        
    finally:
        # Always cleanup
        shutil.rmtree(temp_dir)
```

### Error Handling

All workers implement comprehensive error handling:

1. **Try/Except/Finally:** Standard pattern
2. **Structured Errors:** `error_details` dict with context
3. **State Updates:** Job marked as `FAILED` with error message
4. **Cleanup:** Temp directories always removed
5. **Logging:** All errors logged with stack traces
6. **Retry:** Failed jobs can be retried from last checkpoint

### File Management

Workers use temporary directories for isolation:

```python
temp_dir = tempfile.mkdtemp(prefix=f"karaoke_{worker}_{job_id}_")
try:
    # Processing...
finally:
    shutil.rmtree(temp_dir)  # Always cleanup
```

Benefits:
- **Isolation:** Each job has separate workspace
- **No Leaks:** Automatic cleanup
- **Concurrency:** Multiple jobs don't interfere
- **Security:** Temp files are private

### GCS Integration

Workers upload/download files from Google Cloud Storage:

```python
# Download
local_path = os.path.join(temp_dir, "input.flac")
storage.download_file(gcs_url, local_path)

# Upload
gcs_path = f"jobs/{job_id}/category/file.ext"
url = storage.upload_file(local_path, gcs_path)
job_manager.update_file_url(job_id, 'category', 'file_type', url)
```

File organization:
```
gs://bucket/jobs/{job_id}/
├── input.flac                  # Original audio
├── stems/
│   ├── instrumental_clean.flac
│   ├── instrumental_with_backing.flac
│   ├── vocals.flac
│   ├── backing_vocals.flac
│   └── lead_vocals.flac
├── lyrics/
│   ├── corrections.json
│   ├── audio.flac
│   └── reference.txt
├── screens/
│   ├── title.mov
│   └── end.mov
├── videos/
│   └── with_vocals.mkv
└── finals/
    ├── lossless_4k_mp4.mp4
    ├── lossless_4k_mkv.mkv
    ├── lossy_4k_mp4.mp4
    └── lossy_720p_mp4.mp4
```

---

## Worker Coordination

### Parallel Processing

Audio and lyrics workers run in parallel:

```python
# Job submission triggers both
background_tasks.add_task(worker_service.trigger_audio_worker, job_id)
background_tasks.add_task(worker_service.trigger_lyrics_worker, job_id)
```

Coordination via job state:

```python
# Screen generation is gated on LYRICS ONLY — audio separation is decoupled so
# reviewers can start while it finishes. mark_*_complete only persist a flag; the
# screens dispatch is an AWAITED, idempotent call (advance_to_screens_if_ready).
# A previous fire-and-forget asyncio trigger was cancelled on Cloud Run Job loop
# teardown, orphaning jobs at `downloading` (E2E job-orchestration stall, 2026-09).

# Lyrics worker completes (PRIMARY trigger)
job_manager.mark_lyrics_complete(job_id)          # sets lyrics_complete flag
await job_manager.advance_to_screens_if_ready(job_id)
→ if lyrics_complete and status == DOWNLOADING: await trigger_screens_worker()

# Audio worker completes (FALLBACK trigger — rescues a lost lyrics dispatch)
job_manager.mark_audio_complete(job_id)           # sets audio_complete flag
await job_manager.advance_to_screens_if_ready(job_id)
→ no-op unless lyrics already done AND still at DOWNLOADING (status guard)

# Defense-in-depth: recover-stuck-jobs (every 5 min) re-triggers any job left at
# DOWNLOADING with lyrics_complete but no screens (a lost/failed dispatch).
```

Benefits:
- **No race conditions:** Firestore handles atomicity
- **Independent processing:** Workers don't block each other
- **Automatic progression:** No manual coordination needed
- **Clear state tracking:** Easy to debug

### Sequential Processing

Some stages must be sequential:

```
Screens Worker
    ↓ (auto-triggered when audio + lyrics complete)
AWAITING_INSTRUMENTAL_SELECTION
    ↓ (user selects)
Video Worker
    ↓ (generates all formats)
COMPLETE
```

Coordination via state transitions:

```python
# Screens worker transitions to AWAITING_INSTRUMENTAL_SELECTION
# User submits selection
# API endpoint triggers video worker
background_tasks.add_task(worker_service.trigger_video_worker, job_id)
```

---

## Triggering Workers

Workers are triggered via `WorkerService` (see `backend/services/worker_service.py`):

```python
from backend.services.worker_service import get_worker_service

worker_service = get_worker_service()

# Trigger specific worker
await worker_service.trigger_audio_worker(job_id)
await worker_service.trigger_lyrics_worker(job_id)
await worker_service.trigger_screens_worker(job_id)
await worker_service.trigger_video_worker(job_id)
```

Internal API endpoints (see `backend/api/routes/internal.py`):

```
POST /api/internal/workers/audio
POST /api/internal/workers/lyrics
POST /api/internal/workers/screens
POST /api/internal/workers/video
```

Request format:
```json
{
  "job_id": "abc123"
}
```

Response format:
```json
{
  "status": "started",
  "job_id": "abc123",
  "message": "Worker started"
}
```

---

## Testing Workers

### Manual Testing

1. **Submit a job:**
   ```bash
   curl -X POST http://localhost:8080/api/jobs \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://youtube.com/watch?v=...",
       "artist": "ABBA",
       "title": "Waterloo"
     }'
   ```

2. **Check status:**
   ```bash
   curl http://localhost:8080/api/jobs/{job_id}
   ```

3. **Monitor logs:**
   ```bash
   # Watch worker progress
   tail -f logs/backend.log | grep "Job {job_id}"
   ```

### Automated Testing

TODO: Create unit tests for each worker

- Test audio separation with mock Modal API
- Test lyrics transcription with mock AudioShake API
- Test screen generation with mock VideoGenerator
- Test error handling
- Test state transitions

---

## Environment Variables

Workers require these environment variables:

### Required
- `AUDIO_SEPARATOR_API_URL` - Modal audio separation API
- `AUDIOSHAKE_API_TOKEN` - AudioShake transcription API

### Optional
- `GENIUS_API_TOKEN` - Genius lyrics API
- `SPOTIFY_COOKIE_SP_DC` - Spotify lyrics
- `RAPIDAPI_KEY` - Musixmatch via RapidAPI

### GCP
- `GOOGLE_CLOUD_PROJECT` - GCP project ID
- `GCS_UPLOAD_BUCKET` - Upload bucket
- `GCS_TEMP_BUCKET` - Temp files bucket
- `GCS_OUTPUT_BUCKET` - Final outputs bucket

All credentials can be stored in Google Secret Manager (production) or environment variables (development).

---

## Performance

### Processing Times

| Worker | Stage | Time | Can Parallelize |
|--------|-------|------|----------------|
| Audio | Stage 1 | 3-5 min | Yes (with lyrics) |
| Audio | Stage 2 | 2-3 min | Yes (with lyrics) |
| Lyrics | Fetch | <1 min | Yes (with audio) |
| Lyrics | Transcribe | 1-2 min | Yes (with audio) |
| Lyrics | Correct | 30 sec | Yes (with audio) |
| Screens | Generate | 30 sec | No |
| Video | Encode | 15-20 min | No |

**Total Time (ideal):** ~25-30 minutes
- Parallel phase: 5-10 minutes (audio + lyrics)
- Human review: 5-15 minutes (variable)
- Screens: 30 seconds
- Instrumental selection: 30 seconds (user)
- Video encoding: 15-20 minutes

### Resource Usage

Per job:
- **CPU:** Low (workers are I/O bound, encoding is Cloud Build)
- **Memory:** ~500MB (temporary files)
- **Disk:** ~2GB temporary (cleaned up)
- **Network:** ~500MB download, ~1GB upload

### Scaling

Workers scale horizontally:
- Each Cloud Run instance can handle multiple concurrent workers
- Workers are stateless (state in Firestore)
- Files in GCS (not local disk)
- Can run 10+ jobs concurrently per instance

---

## Future Improvements

1. **Cloud Tasks:** Replace HTTP triggers with Cloud Tasks for better reliability
2. **Retries:** Automatic retry with exponential backoff
3. **Checkpoints:** Resume from last successful stage
4. **Progress:** Real-time progress updates (percentage)
5. **Notifications:** Email/SMS when review needed
6. **Caching:** Cache model files, reference lyrics
7. **Metrics:** Worker duration, success rate, error rate
8. **Monitoring:** Dashboards for worker health

---

## Troubleshooting

### Worker Not Starting

**Symptom:** Job stuck in `PENDING`  
**Cause:** Worker trigger failed  
**Solution:** Check logs for HTTP errors, verify internal API is accessible

### Worker Failing

**Symptom:** Job transitions to `FAILED`  
**Cause:** Exception in worker  
**Solution:** Check `error_details` in job, review logs

### Slow Processing

**Symptom:** Worker takes longer than expected  
**Cause:** API slowness, large file  
**Solution:** Check external API status, optimize file sizes

### Files Not Uploading

**Symptom:** Job completes but no files in GCS  
**Cause:** GCS permissions, network issue  
**Solution:** Verify service account has Storage Admin role

### Coordination Issues

**Symptom:** Screens worker not triggered  
**Cause:** One parallel worker failed  
**Solution:** Check both audio and lyrics worker completed successfully

---

## Related Documentation

- [API Manual Testing](../docs/01-reference/API-MANUAL-TESTING.md)
- [CLI Workflow](../docs/01-reference/KARAOKE-GEN-CLI-WORKFLOW.md)
- [Testing Backend](../docs/03-deployment/TESTING-BACKEND.md)
- [Infrastructure as Code](../docs/03-deployment/INFRASTRUCTURE-AS-CODE.md)

