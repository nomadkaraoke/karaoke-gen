# Worker Implementation Plan

**Last Updated:** 2025-12-08  
**Status:** Implementing review architecture fix

---

## Overview

This document describes the worker architecture for the karaoke-gen cloud backend. The key insight is that we use the `LyricsTranscriber` library for its processing capabilities but **NOT** its blocking `ReviewServer`.

---

## Worker Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              WORKER PIPELINE                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐    ┌─────────────────┐                               │
│   │  AUDIO WORKER   │    │  LYRICS WORKER  │   (Run in PARALLEL)           │
│   │                 │    │                 │                               │
│   │ • Modal API     │    │ • AudioShake    │                               │
│   │ • Stem separate │    │ • Auto-correct  │                               │
│   │ • 2 stages      │    │ • Save JSON     │                               │
│   │                 │    │ • NO VIDEO      │                               │
│   └────────┬────────┘    └────────┬────────┘                               │
│            │                      │                                         │
│            └──────────┬───────────┘                                         │
│                       │                                                     │
│                       ▼                                                     │
│            ┌─────────────────────┐                                         │
│            │   SCREENS WORKER    │                                         │
│            │                     │                                         │
│            │ • Title screen      │                                         │
│            │ • End screen        │                                         │
│            │ • Waits for both    │                                         │
│            │   audio + lyrics    │                                         │
│            └──────────┬──────────┘                                         │
│                       │                                                     │
│                       ▼                                                     │
│            ┌─────────────────────┐                                         │
│            │   AWAITING_REVIEW   │  ⚠️ HUMAN INTERACTION                   │
│            │                     │                                         │
│            │ • React UI loads    │                                         │
│            │ • User corrects     │                                         │
│            │ • Submits changes   │                                         │
│            └──────────┬──────────┘                                         │
│                       │                                                     │
│                       ▼                                                     │
│            ┌─────────────────────┐                                         │
│            │ RENDER VIDEO WORKER │  ← NEW (post-review)                    │
│            │                     │                                         │
│            │ • OutputGenerator   │                                         │
│            │ • with_vocals.mkv   │                                         │
│            │ • LRC/ASS files     │                                         │
│            └──────────┬──────────┘                                         │
│                       │                                                     │
│                       ▼                                                     │
│            ┌─────────────────────┐                                         │
│            │  AWAITING_SELECT    │  ⚠️ HUMAN INTERACTION                   │
│            │                     │                                         │
│            │ • User picks        │                                         │
│            │   instrumental      │                                         │
│            └──────────┬──────────┘                                         │
│                       │                                                     │
│                       ▼                                                     │
│            ┌─────────────────────┐                                         │
│            │   VIDEO WORKER      │                                         │
│            │                     │                                         │
│            │ • Remux audio       │                                         │
│            │ • Concat screens    │                                         │
│            │ • Encode formats    │                                         │
│            └──────────┬──────────┘                                         │
│                       │                                                     │
│                       ▼                                                     │
│            ┌─────────────────────┐                                         │
│            │      COMPLETE       │                                         │
│            └─────────────────────┘                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Worker Details

### 1. Audio Worker (`backend/workers/audio_worker.py`)

**Purpose:** Separate audio into stems using Modal API

**Status:** ✅ Implemented

**Process:**
1. Download audio from GCS (`input_media_gcs_path`)
2. Stage 1: Separate clean instrumental + 6-stem
3. Stage 2: Extract backing vocals from vocals stem
4. Combine instrumental + backing vocals
5. Upload all stems to GCS
6. Update `state_data.audio_progress`

**Output Files:**
- `jobs/{job_id}/stems/instrumental_clean.flac`
- `jobs/{job_id}/stems/instrumental_with_backing.flac`
- `jobs/{job_id}/stems/vocals.flac`
- `jobs/{job_id}/stems/backing_vocals.flac`
- `jobs/{job_id}/stems/bass.flac`
- `jobs/{job_id}/stems/drums.flac`

**Does NOT transition state** - uses `state_data` for progress tracking

---

### 2. Lyrics Worker (`backend/workers/lyrics_worker.py`)

**Purpose:** Transcribe and auto-correct lyrics

**Status:** ✅ Implemented (with correct settings)

**Process:**
1. Download audio from GCS
2. Fetch reference lyrics from Genius/Spotify
3. Transcribe via AudioShake API
4. Run automatic correction via LyricsTranscriber
5. Save `corrections.json` to GCS
6. Update `state_data.lyrics_progress`

**IMPORTANT Settings:**
```python
config = LyricsTranscriberConfig(
    render_video=False,           # ← VIDEO GENERATED LATER (after review)
    skip_transcription_review=True,  # ← No interactive review server
)
```

**Output Files:**
- `jobs/{job_id}/lyrics/corrections.json` - CorrectionResult data
- `jobs/{job_id}/lyrics/original.txt` - Original transcription
- `jobs/{job_id}/lyrics/corrected.txt` - Auto-corrected text

**Does NOT transition state** - uses `state_data` for progress tracking

---

### 3. Screens Worker (`backend/workers/screens_worker.py`)

**Purpose:** Generate title and end screen videos

**Status:** ✅ Implemented

**Trigger:** Runs after BOTH audio and lyrics workers complete

**Process:**
1. Check `state_data` for audio + lyrics completion
2. Generate title screen video (5 seconds)
3. Generate end screen video (5 seconds)
4. Upload to GCS
5. Transition to `AWAITING_REVIEW`

**Output Files:**
- `jobs/{job_id}/screens/title.mov`
- `jobs/{job_id}/screens/end.mov`

---

### 4. Render Video Worker (`backend/workers/render_video_worker.py`)

**Purpose:** Generate karaoke video with corrected lyrics

**Status:** 🔄 NEW - To be implemented

**Trigger:** Called after human completes review (`POST /api/jobs/{id}/complete-review`)

**Key Insight:** Uses `OutputGenerator` from LyricsTranscriber library:

```python
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.correction.corrector import CorrectionResult

# Load corrected data (from human review)
correction_result = CorrectionResult.from_dict(corrections_data)

# Use OutputGenerator (NOT ReviewServer)
output_generator = OutputGenerator(config, logger)
outputs = output_generator.generate_outputs(
    transcription_corrected=correction_result,
    lyrics_results={},
    output_prefix=f"{artist} - {title}",
    audio_filepath=audio_path
)

# outputs.video = path to with_vocals.mkv
```

**Process:**
1. Download corrected `corrections.json` (or `corrections_updated.json` if edited)
2. Download audio file
3. Load as `CorrectionResult`
4. Configure `OutputGenerator` with video rendering enabled
5. Generate karaoke video with synchronized lyrics
6. Upload to GCS
7. Transition to `AWAITING_INSTRUMENTAL_SELECTION`

**Output Files:**
- `jobs/{job_id}/videos/with_vocals.mkv` - Karaoke video with lyrics
- `jobs/{job_id}/lyrics/karaoke.lrc` - LRC file
- `jobs/{job_id}/lyrics/karaoke.ass` - ASS subtitle file

---

### 5. Video Worker (`backend/workers/video_worker.py`)

**Purpose:** Final video assembly and encoding

**Status:** ✅ Implemented (needs render_video_worker first)

**Trigger:** Called after instrumental selection (`POST /api/jobs/{id}/select-instrumental`)

**Process:**
1. Download with_vocals.mkv
2. Download selected instrumental (clean or with_backing)
3. Remux: Replace audio track with instrumental
4. Download title and end screens
5. Concatenate: title + karaoke + end
6. Encode to multiple formats
7. Upload final videos to GCS
8. Transition to `COMPLETE`

**Output Files:**
- `jobs/{job_id}/finals/lossless_4k.mp4`
- `jobs/{job_id}/finals/lossless_4k.mkv`
- `jobs/{job_id}/finals/lossy_4k.mp4`
- `jobs/{job_id}/finals/lossy_720p.mp4`

---

## State Machine

### Job States

```python
class JobStatus(str, Enum):
    # Initial
    PENDING = "pending"
    DOWNLOADING = "downloading"
    
    # Parallel processing (tracked via state_data)
    SEPARATING_STAGE1 = "separating_stage1"
    SEPARATING_STAGE2 = "separating_stage2"
    TRANSCRIBING = "transcribing"
    
    # Post-parallel
    GENERATING_SCREENS = "generating_screens"
    
    # Human review
    AWAITING_REVIEW = "awaiting_review"       # ⚠️ HUMAN
    IN_REVIEW = "in_review"
    REVIEW_COMPLETE = "review_complete"
    
    # Video rendering (NEW)
    RENDERING_VIDEO = "rendering_video"        # ← NEW STATE
    
    # Instrumental selection
    AWAITING_INSTRUMENTAL_SELECTION = "awaiting_instrumental_selection"  # ⚠️ HUMAN
    INSTRUMENTAL_SELECTED = "instrumental_selected"
    
    # Final video
    GENERATING_VIDEO = "generating_video"
    
    # Terminal
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### State Transitions

```python
STATE_TRANSITIONS = {
    JobStatus.PENDING: [JobStatus.DOWNLOADING, JobStatus.FAILED, JobStatus.CANCELLED],
    JobStatus.DOWNLOADING: [
        JobStatus.SEPARATING_STAGE1, 
        JobStatus.TRANSCRIBING, 
        JobStatus.GENERATING_SCREENS,  # When audio/lyrics use state_data
        JobStatus.FAILED
    ],
    
    # Audio separation (can skip if using state_data)
    JobStatus.SEPARATING_STAGE1: [JobStatus.SEPARATING_STAGE2, JobStatus.FAILED],
    JobStatus.SEPARATING_STAGE2: [JobStatus.GENERATING_SCREENS, JobStatus.FAILED],
    
    # Transcription (can skip if using state_data)
    JobStatus.TRANSCRIBING: [JobStatus.GENERATING_SCREENS, JobStatus.FAILED],
    
    # Screens triggers review
    JobStatus.GENERATING_SCREENS: [
        JobStatus.AWAITING_REVIEW,
        JobStatus.AWAITING_INSTRUMENTAL_SELECTION,  # Skip review if disabled
        JobStatus.FAILED
    ],
    
    # Human review flow
    JobStatus.AWAITING_REVIEW: [JobStatus.IN_REVIEW, JobStatus.FAILED, JobStatus.CANCELLED],
    JobStatus.IN_REVIEW: [JobStatus.REVIEW_COMPLETE, JobStatus.AWAITING_REVIEW, JobStatus.FAILED],
    JobStatus.REVIEW_COMPLETE: [JobStatus.RENDERING_VIDEO, JobStatus.FAILED],
    
    # Video rendering (NEW)
    JobStatus.RENDERING_VIDEO: [JobStatus.AWAITING_INSTRUMENTAL_SELECTION, JobStatus.FAILED],
    
    # Instrumental selection
    JobStatus.AWAITING_INSTRUMENTAL_SELECTION: [JobStatus.INSTRUMENTAL_SELECTED, JobStatus.FAILED, JobStatus.CANCELLED],
    JobStatus.INSTRUMENTAL_SELECTED: [JobStatus.GENERATING_VIDEO, JobStatus.FAILED],
    
    # Final video
    JobStatus.GENERATING_VIDEO: [JobStatus.COMPLETE, JobStatus.FAILED],
    
    # Terminal states
    JobStatus.COMPLETE: [],
    JobStatus.FAILED: [],
    JobStatus.CANCELLED: [],
}
```

---

## API Endpoints for Human Interaction

### Review Endpoints

```
GET  /api/jobs/{job_id}/review
     Returns: corrections JSON URL, audio URL, metadata
     Used by: React review UI to load data

POST /api/jobs/{job_id}/review
     Body: { corrections: {...} }
     Saves updated corrections to GCS
     Used by: React UI to save progress

POST /api/jobs/{job_id}/complete-review
     Transitions to REVIEW_COMPLETE
     Triggers render_video_worker
     Used by: React UI "Finish" button
```

### Instrumental Selection Endpoints

```
GET  /api/jobs/{job_id}/instrumental-options
     Returns: URLs for clean and with_backing instrumentals
     Used by: Frontend to show audio players

POST /api/jobs/{job_id}/select-instrumental
     Body: { "selection": "clean" | "with_backing" }
     Transitions to INSTRUMENTAL_SELECTED
     Triggers video_worker
```

---

## Using LyricsTranscriber as Library

### What We Use

| Component | Usage |
|-----------|-------|
| `LyricsTranscriber` | Transcription + auto-correction |
| `CorrectionResult` | Data structure for corrections |
| `OutputGenerator` | Video rendering with lyrics |
| `OutputConfig` | Configuration for rendering |
| `CorrectionOperations` | Update corrections from review |

### What We Don't Use

| Component | Reason |
|-----------|--------|
| `ReviewServer` | Blocks waiting for human input |
| `server.start()` | Opens browser, blocks thread |
| Interactive prompts | CLI-only features |

### Example: Using OutputGenerator

```python
from karaoke_gen.lyrics_transcriber.output.generator import OutputGenerator
from karaoke_gen.lyrics_transcriber.correction.corrector import CorrectionResult
from karaoke_gen.lyrics_transcriber.core.config import OutputConfig

# 1. Load corrected data from GCS (after human review)
corrections_data = download_json_from_gcs(f"jobs/{job_id}/corrections_updated.json")
correction_result = CorrectionResult.from_dict(corrections_data)

# 2. Configure output generation
config = OutputConfig(
    output_dir="/tmp/output",
    cache_dir="/tmp/cache",
    output_styles_json="/path/to/styles.json",
    render_video=True,      # Enable video generation
    generate_cdg=False,     # CDG optional
    video_resolution="4k",
    subtitle_offset_ms=0
)

# 3. Create generator and generate outputs
output_generator = OutputGenerator(config, logger)
outputs = output_generator.generate_outputs(
    transcription_corrected=correction_result,
    lyrics_results={},
    output_prefix=f"{artist} - {title}",
    audio_filepath=audio_path,
    artist=artist,
    title=title
)

# 4. Upload results
upload_to_gcs(outputs.video, f"jobs/{job_id}/videos/with_vocals.mkv")
upload_to_gcs(outputs.lrc, f"jobs/{job_id}/lyrics/karaoke.lrc")
upload_to_gcs(outputs.ass, f"jobs/{job_id}/lyrics/karaoke.ass")
```

---

## Environment Variables

```bash
# Audio separation (Modal API)
AUDIO_SEPARATOR_API_URL=https://nomadkaraoke--audio-separator-api.modal.run

# Lyrics APIs
AUDIOSHAKE_API_TOKEN=<secret>
GENIUS_API_TOKEN=<secret>

# GCS configuration  
GOOGLE_CLOUD_PROJECT=nomadkaraoke
GCS_BUCKET_NAME=karaoke-gen-storage

# Optional
RAPIDAPI_KEY=<secret>  # For additional lyrics sources
```

---

## Implementation Checklist

### Audio Worker ✅
- [x] Modal API integration
- [x] 2-stage separation
- [x] Stem upload to GCS
- [x] state_data progress tracking

### Lyrics Worker ✅
- [x] AudioShake API integration
- [x] Reference lyrics fetching
- [x] Auto-correction
- [x] corrections.json generation
- [x] `render_video=False` setting
- [x] state_data progress tracking

### Screens Worker ✅
- [x] Title screen generation
- [x] End screen generation
- [x] Waits for audio+lyrics completion
- [x] Transition to AWAITING_REVIEW

### Render Video Worker 🔄
- [ ] Download corrected data
- [ ] Load CorrectionResult
- [ ] Configure OutputGenerator
- [ ] Generate with_vocals.mkv
- [ ] Upload to GCS
- [ ] Transition to AWAITING_INSTRUMENTAL

### Video Worker ✅
- [x] Download components
- [x] Remux with instrumental
- [x] Concatenate screens
- [x] Encode multiple formats
- [x] Upload finals
- [x] Transition to COMPLETE

### Review API 🔄
- [ ] GET /review endpoint
- [ ] POST /review endpoint
- [ ] POST /complete-review endpoint
- [ ] Wire up to main router

---

## Success Criteria

1. ✅ Audio separation produces all stems
2. ✅ Lyrics transcription produces corrections.json
3. ✅ Screens generate title/end videos
4. ⏳ Human can review and correct lyrics via API
5. ⏳ After review, video renders with corrected lyrics
6. ⏳ Human can select instrumental
7. ⏳ Final videos encode in all formats
8. ⏳ Complete job has all downloadable files
