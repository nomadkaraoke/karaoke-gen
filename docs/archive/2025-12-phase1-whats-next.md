# What's Next: Review Architecture Implementation

**Last Updated:** 2025-12-08  
**Status:** Ready to implement review API and render video worker

---

## Problem Summary

The LyricsTranscriber library has a blocking `ReviewServer` that's designed for CLI use:
- Starts a local server on port 8000
- Opens browser for human review
- **BLOCKS** until `/api/complete` is called
- Only then generates video with corrected lyrics

This doesn't work for our async cloud backend. We need to:
1. Save correction data for async human review
2. Generate video AFTER review completes (not during lyrics worker)

---

## Target Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              Cloud Backend Flow                               │
└──────────────────────────────────────────────────────────────────────────────┘

                     ┌─────────────────────────────────┐
                     │         File Upload             │
                     │  POST /api/jobs/upload          │
                     └───────────────┬─────────────────┘
                                     │
                                     ▼
                     ┌─────────────────────────────────┐
                     │         DOWNLOADING             │
                     └───────────────┬─────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
        ┌───────────────────┐           ┌───────────────────┐
        │   AUDIO WORKER    │           │   LYRICS WORKER   │
        │                   │           │                   │
        │ • Call Modal API  │           │ • AudioShake API  │
        │ • Separate stems  │           │ • Genius lyrics   │
        │ • Upload to GCS   │           │ • Auto-correct    │
        │                   │           │ • Save JSON ✓     │
        │ tracks:           │           │ • NO VIDEO ✓      │
        │ state_data.audio  │           │ tracks:           │
        └─────────┬─────────┘           │ state_data.lyrics │
                  │                     └─────────┬─────────┘
                  │                               │
                  └───────────┬───────────────────┘
                              │
                              ▼
                  ┌───────────────────────────────┐
                  │      SCREENS WORKER           │
                  │  (waits for audio+lyrics)     │
                  │                               │
                  │ • Generate title screen       │
                  │ • Generate end screen         │
                  │ • Upload to GCS               │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │       AWAITING_REVIEW         │
                  │   ⚠️ HUMAN INTERACTION        │
                  └───────────────┬───────────────┘
                                  │
                  ┌───────────────▼───────────────┐
                  │     React Review Frontend     │
                  │                               │
                  │ GET /api/jobs/{id}/review     │
                  │  → corrections.json + audio   │
                  │                               │
                  │ • User corrects lyrics        │
                  │ • Adjusts timing              │
                  │ • Previews changes            │
                  │                               │
                  │ POST /api/jobs/{id}/review    │
                  │  → saves updated corrections  │
                  │                               │
                  │ POST /api/jobs/{id}/complete  │
                  │  → triggers video render      │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │     RENDER VIDEO WORKER       │
                  │         (NEW)                 │
                  │                               │
                  │ • Download corrected JSON     │
                  │ • Use OutputGenerator         │
                  │ • Render with_vocals.mkv      │
                  │ • Upload to GCS               │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │  AWAITING_INSTRUMENTAL_SELECT │
                  │   ⚠️ HUMAN INTERACTION        │
                  │                               │
                  │ POST /api/jobs/{id}/select    │
                  │  { "selection": "clean" }     │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │       VIDEO WORKER            │
                  │                               │
                  │ • Download all components     │
                  │ • Remux with instrumental     │
                  │ • Concatenate screens         │
                  │ • Encode final formats        │
                  │ • Upload to GCS               │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │          COMPLETE             │
                  │                               │
                  │  User downloads final videos  │
                  └───────────────────────────────┘
```

---

## Implementation Plan

### Step 1: Add Review API Endpoints

**File:** `backend/api/routes/review.py` (new)

```python
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

router = APIRouter(tags=["review"])

@router.get("/{job_id}/review")
async def get_review_data(job_id: str) -> Dict[str, Any]:
    """
    Get data needed for lyrics review interface.
    
    Returns:
    - corrections: The CorrectionResult data
    - audio_url: Signed URL for audio playback
    - metadata: Artist, title, etc.
    """
    job = job_manager.get_job(job_id)
    
    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(400, f"Job not ready for review: {job.status}")
    
    # Get signed URLs for frontend
    corrections_url = storage.get_signed_url(f"jobs/{job_id}/lyrics/corrections.json")
    audio_url = storage.get_signed_url(job.input_media_gcs_path)
    
    return {
        "job_id": job_id,
        "status": job.status,
        "artist": job.artist,
        "title": job.title,
        "corrections_url": corrections_url,
        "audio_url": audio_url
    }

@router.post("/{job_id}/review")
async def save_review_corrections(
    job_id: str, 
    corrections: Dict[str, Any]
) -> Dict[str, str]:
    """
    Save updated corrections during review.
    
    Can be called multiple times - saves progress.
    Frontend should call this as user makes changes.
    """
    job = job_manager.get_job(job_id)
    
    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(400, f"Job not in review state: {job.status}")
    
    # Transition to IN_REVIEW if first save
    if job.status == JobStatus.AWAITING_REVIEW:
        job_manager.transition_to_state(job_id, JobStatus.IN_REVIEW)
    
    # Save updated corrections to GCS
    storage.upload_json(
        f"jobs/{job_id}/lyrics/corrections_updated.json",
        corrections
    )
    
    return {"status": "saved"}

@router.post("/{job_id}/complete-review")
async def complete_review(job_id: str) -> Dict[str, str]:
    """
    Mark review as complete and trigger video rendering.
    
    Frontend calls this when user clicks "Finish Review".
    """
    job = job_manager.get_job(job_id)
    
    if job.status not in [JobStatus.AWAITING_REVIEW, JobStatus.IN_REVIEW]:
        raise HTTPException(400, f"Job not in review state: {job.status}")
    
    # Transition to REVIEW_COMPLETE
    job_manager.transition_to_state(
        job_id, 
        JobStatus.REVIEW_COMPLETE,
        message="Review complete, rendering video"
    )
    
    # Trigger render video worker
    await trigger_render_video_worker(job_id)
    
    return {"status": "complete", "next_status": "rendering_video"}
```

### Step 2: Create Render Video Worker

**File:** `backend/workers/render_video_worker.py` (new)

```python
"""
Render Video Worker

Generates the karaoke video with synchronized lyrics AFTER human review.

This worker:
1. Downloads the corrected lyrics data from GCS
2. Downloads the audio file
3. Uses LyricsTranscriber's OutputGenerator to render video
4. Uploads the with_vocals.mkv to GCS
5. Transitions to AWAITING_INSTRUMENTAL_SELECTION

Key insight: We use OutputGenerator from lyrics_transcriber library
WITHOUT using its blocking ReviewServer.
"""

import os
import logging
import tempfile
from typing import Optional

from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.correction.corrector import CorrectionResult
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig

from backend.services.job_manager import job_manager
from backend.services.storage_service import storage
from backend.models.job import JobStatus

logger = logging.getLogger(__name__)


async def process_render_video(job_id: str) -> bool:
    """
    Render karaoke video with corrected lyrics.
    
    Called after human review is complete.
    """
    logger.info(f"Job {job_id}: Starting video render")
    
    try:
        job = job_manager.get_job(job_id)
        
        # Transition to RENDERING_VIDEO
        job_manager.transition_to_state(
            job_id,
            JobStatus.RENDERING_VIDEO,
            progress=75,
            message="Rendering karaoke video with lyrics"
        )
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. Download corrected corrections data
            corrections_path = os.path.join(temp_dir, "corrections.json")
            corrections_gcs = f"jobs/{job_id}/lyrics/corrections_updated.json"
            
            # Fall back to original if no updates
            if not storage.file_exists(corrections_gcs):
                corrections_gcs = f"jobs/{job_id}/lyrics/corrections.json"
            
            storage.download_file(corrections_gcs, corrections_path)
            
            # 2. Load as CorrectionResult
            import json
            with open(corrections_path, 'r') as f:
                corrections_data = json.load(f)
            
            correction_result = CorrectionResult.from_dict(corrections_data)
            
            # 3. Download audio file
            audio_path = os.path.join(temp_dir, "audio.flac")
            storage.download_file(job.input_media_gcs_path, audio_path)
            
            # 4. Get styles (from job or default)
            styles_path = _get_styles_path(job, temp_dir)
            
            # 5. Configure OutputGenerator
            output_dir = os.path.join(temp_dir, "output")
            os.makedirs(output_dir, exist_ok=True)
            
            config = OutputConfig(
                output_dir=output_dir,
                cache_dir=os.path.join(temp_dir, "cache"),
                output_styles_json=styles_path,
                render_video=True,
                generate_cdg=False,  # CDG optional
                video_resolution="4k",
                subtitle_offset_ms=0
            )
            
            output_generator = OutputGenerator(config, logger)
            
            # 6. Generate video
            logger.info(f"Job {job_id}: Generating karaoke video")
            outputs = output_generator.generate_outputs(
                transcription_corrected=correction_result,
                lyrics_results={},  # Already in correction_result
                output_prefix=f"{job.artist} - {job.title}",
                audio_filepath=audio_path,
                artist=job.artist,
                title=job.title
            )
            
            # 7. Upload video to GCS
            if outputs.video and os.path.exists(outputs.video):
                video_gcs_path = f"jobs/{job_id}/videos/with_vocals.mkv"
                video_url = storage.upload_file(outputs.video, video_gcs_path)
                job_manager.update_file_url(job_id, 'videos', 'with_vocals', video_url)
                logger.info(f"Job {job_id}: Uploaded with_vocals.mkv")
            else:
                raise Exception("Video generation failed - no output file")
            
            # 8. Upload other outputs (LRC, ASS, etc.)
            if outputs.lrc and os.path.exists(outputs.lrc):
                lrc_url = storage.upload_file(
                    outputs.lrc, 
                    f"jobs/{job_id}/lyrics/karaoke.lrc"
                )
                job_manager.update_file_url(job_id, 'lyrics', 'lrc', lrc_url)
            
            if outputs.ass and os.path.exists(outputs.ass):
                ass_url = storage.upload_file(
                    outputs.ass,
                    f"jobs/{job_id}/lyrics/karaoke.ass"
                )
                job_manager.update_file_url(job_id, 'lyrics', 'ass', ass_url)
            
            # 9. Transition to awaiting instrumental selection
            job_manager.transition_to_state(
                job_id,
                JobStatus.AWAITING_INSTRUMENTAL_SELECTION,
                progress=80,
                message="Video rendered - select your instrumental"
            )
            
            logger.info(f"Job {job_id}: Video render complete")
            return True
            
    except Exception as e:
        logger.error(f"Job {job_id}: Video render failed: {e}", exc_info=True)
        job_manager.fail_job(job_id, f"Video render failed: {str(e)}")
        return False


def _get_styles_path(job, temp_dir: str) -> str:
    """Get or create styles JSON for video generation."""
    # Check if job has custom styles
    if job.state_data and job.state_data.get('styles_gcs_path'):
        styles_path = os.path.join(temp_dir, "styles.json")
        storage.download_file(job.state_data['styles_gcs_path'], styles_path)
        return styles_path
    
    # Use default styles
    default_styles = {
        "karaoke": {
            "font_size": 100,
            "max_line_length": 40,
            "top_padding": 200,
            "highlight_color": "#FFFF00",
            "text_color": "#FFFFFF"
        }
    }
    
    styles_path = os.path.join(temp_dir, "styles.json")
    import json
    with open(styles_path, 'w') as f:
        json.dump(default_styles, f)
    
    return styles_path
```

### Step 3: Update State Machine

**File:** `backend/models/job.py`

Add new states and transitions:

```python
class JobStatus(str, Enum):
    # ... existing states ...
    
    # Add after REVIEW_COMPLETE
    RENDERING_VIDEO = "rendering_video"  # OutputGenerator creating with_vocals.mkv


STATE_TRANSITIONS = {
    # ... existing transitions ...
    
    # Update review flow
    JobStatus.REVIEW_COMPLETE: [JobStatus.RENDERING_VIDEO, JobStatus.FAILED],
    JobStatus.RENDERING_VIDEO: [JobStatus.AWAITING_INSTRUMENTAL_SELECTION, JobStatus.FAILED],
    
    # ... rest of transitions ...
}
```

### Step 4: Update Lyrics Worker

**File:** `backend/workers/lyrics_worker.py`

Ensure it does NOT generate video:

```python
config = LyricsTranscriberConfig(
    # ... other config ...
    render_video=False,  # IMPORTANT: Video generated after review
    skip_transcription_review=True,  # No interactive review
)

# Transition to AWAITING_REVIEW (not generating video)
# Video will be generated by render_video_worker after human review
```

### Step 5: Wire Up Routes

**File:** `backend/main.py`

```python
from backend.api.routes import review

app.include_router(review.router, prefix="/api/jobs")
```

---

## Testing Plan

### Local Testing with Emulators

```bash
# 1. Start backend with emulators
./scripts/run-backend-local.sh --with-emulators

# 2. Upload test file
curl -X POST http://localhost:8000/api/jobs/upload \
  -F "file=@tests/data/waterloo10sec.flac" \
  -F "artist=ABBA" \
  -F "title=Waterloo"

# Note the job_id

# 3. Wait for AWAITING_REVIEW status
curl http://localhost:8000/api/jobs/{job_id}

# 4. Get review data
curl http://localhost:8000/api/jobs/{job_id}/review

# 5. Complete review (skip corrections for testing)
curl -X POST http://localhost:8000/api/jobs/{job_id}/complete-review

# 6. Watch for AWAITING_INSTRUMENTAL_SELECTION
curl http://localhost:8000/api/jobs/{job_id}

# 7. Select instrumental
curl -X POST http://localhost:8000/api/jobs/{job_id}/select-instrumental \
  -H "Content-Type: application/json" \
  -d '{"selection": "clean"}'

# 8. Wait for COMPLETE
curl http://localhost:8000/api/jobs/{job_id}
```

---

## Frontend Integration (Future)

The React review UI will:

1. **Load review data:**
   ```typescript
   const reviewData = await fetch(`/api/jobs/${jobId}/review`);
   const { corrections_url, audio_url } = await reviewData.json();
   ```

2. **Display LyricsTranscriber components:**
   - Can reuse components from `lyrics_transcriber_local/lyrics_transcriber/frontend/`
   - Or build new components that consume the same data format

3. **Save progress:**
   ```typescript
   // Auto-save as user edits
   await fetch(`/api/jobs/${jobId}/review`, {
     method: 'POST',
     body: JSON.stringify(updatedCorrections)
   });
   ```

4. **Complete review:**
   ```typescript
   await fetch(`/api/jobs/${jobId}/complete-review`, {
     method: 'POST'
   });
   // Redirect to status page to watch video render
   ```

---

## Summary

**Key insight:** Use LyricsTranscriber as a library, not as a server.

- ✅ Use `CorrectionResult` for data structures
- ✅ Use `OutputGenerator` for video rendering
- ✅ Use `CorrectionOperations` for updates
- ❌ Don't use `ReviewServer` (it blocks)

**New components needed:**
1. Review API endpoints (`/api/jobs/{id}/review`)
2. Render Video Worker (uses `OutputGenerator`)
3. New `RENDERING_VIDEO` state

**Result:** Clean async workflow with human-in-the-loop review that works in cloud architecture.
