# Next Steps: Building Cloud-Native Karaoke-Gen

> **Context:** The backend API skeleton is deployed, but it's only a basic CRUD interface for jobs. To replicate the full `karaoke-gen` CLI functionality in the cloud, we need to implement the complete 8-stage workflow with human-in-the-loop interaction points.
>
> **Reference:** See [`01-reference/KARAOKE-GEN-CLI-WORKFLOW.md`](01-reference/KARAOKE-GEN-CLI-WORKFLOW.md) for complete CLI workflow details.

---

## ✅ Completed

### Phase 1.1: Backend Foundation ✅ COMPLETE
- ✅ Backend API structure (FastAPI on Cloud Run)
- ✅ Basic job CRUD endpoints
- ✅ Firestore for job state
- ✅ Google Cloud Storage integration
- ✅ Infrastructure as Code (Pulumi)
- ✅ Integration tests (17/17 passing)
- ✅ Deployed to Cloud Run
- ✅ CLI workflow documented comprehensively

### Phase 1.2: Async Processing Infrastructure ✅ COMPLETE (100%)
- ✅ **Step 1.2.1:** Job state machine (21 states, validated transitions)
- ✅ **Step 1.2.2:** Worker infrastructure (background tasks, internal API)
- ✅ **Step 1.2.3:** Audio separation worker (complete with karaoke_gen integration)
- ✅ **Step 1.2.4:** Lyrics transcription worker (complete with LyricsTranscriber integration)
- ✅ **Step 1.2.5:** Human-in-the-loop API endpoints (review + selection)
- ✅ **Step 1.2.6:** Worker coordination (parallel processing sync)
- ✅ **Step 1.2.7:** Secret Manager integration
- ✅ **Step 1.2.8:** Screens generation worker (complete)
- ✅ **Step 1.2.9:** Worker service (centralized triggering)

### Phase 1.3: Video Generation & Finalization 🚧 IN PROGRESS (~30% complete)
- ✅ **Step 1.3.1:** Video worker structure (complete)
- ✅ **Step 1.3.2:** Internal API endpoint (complete)
- ⏭️ **Step 1.3.3:** KaraokeFinalise methods implementation (TODO - next)
- ⏭️ **Step 1.3.4:** Cloud Build integration for encoding (TODO)
- ⏭️ **Step 1.3.5:** CDG/TXT package generation (TODO)
- ⏭️ **Step 1.3.6:** End-to-end testing (TODO)

---

## 🎯 Current Goal: Complete Async Processing (Phase 1.2)

The CLI workflow has **8 distinct stages** with **2 critical human interaction points**:

1. Input & Setup
2. **Parallel Processing** (audio separation + lyrics transcription) - 10-15 min
3. Title/End Screen Generation - 30 sec
4. Countdown Padding Synchronization - 5 sec
5. **Human Review** (BLOCKING) - 5-15 min ⚠️
6. **Instrumental Selection** (BLOCKING) - 30 sec ⚠️
7. Video Finalization (encoding, packaging) - 15-20 min
8. Distribution (YouTube, Dropbox, notifications)

**We need to implement ALL of these stages in an async, cloud-native way.**

---

## Phase 1.2: Async Job Processing Infrastructure ✅ ~90% COMPLETE

**Goal:** Enable background processing for long-running tasks

**Status:** Core infrastructure complete! Audio, lyrics, and screens workers implemented with full karaoke_gen integration. Human-in-the-loop endpoints ready. Need to fix worker trigger mechanism and test end-to-end.

**What's Working:**
- ✅ Complete state machine (21 states)
- ✅ Audio separation worker (Modal GPU API)
- ✅ Lyrics transcription worker (AudioShake + correction)
- ✅ Screens generation worker (title + end)
- ✅ Human review endpoints (corrections submission)
- ✅ Instrumental selection endpoints
- ✅ Worker coordination (parallel processing)
- ✅ Secret Manager integration
- ✅ SOLID principles throughout

**What's Left:**
- ⏭️ Fix worker trigger mechanism (HTTP calls)
- ⏭️ End-to-end testing
- ⏭️ Countdown padding implementation

### Step 1.2.1: Implement Job State Machine

**Current:** Jobs have simple `status` field  
**Needed:** Full state machine with transitions

**States:**
```python
class JobStatus(str, Enum):
    # Initial states
    PENDING = "pending"                    # Job created, not started
    DOWNLOADING = "downloading"            # Downloading from URL
    
    # Audio processing states
    SEPARATING_STAGE1 = "separating_stage1"  # Clean instrumental separation
    SEPARATING_STAGE2 = "separating_stage2"  # Backing vocals separation
    AUDIO_COMPLETE = "audio_complete"        # All stems ready
    
    # Lyrics processing states
    TRANSCRIBING = "transcribing"          # AudioShake API call
    CORRECTING = "correcting"              # Automatic correction
    AWAITING_REVIEW = "awaiting_review"    # ⚠️ HUMAN REVIEW NEEDED
    IN_REVIEW = "in_review"                # User is reviewing
    REVIEW_COMPLETE = "review_complete"    # Review submitted
    
    # Video generation states
    GENERATING_SCREENS = "generating_screens"  # Title/end screens
    AWAITING_INSTRUMENTAL_SELECTION = "awaiting_instrumental_selection"  # ⚠️ USER CHOICE NEEDED
    GENERATING_VIDEO = "generating_video"  # Creating karaoke video
    
    # Finalization states
    ENCODING = "encoding"                  # Multi-format encoding
    PACKAGING = "packaging"                # CDG/TXT generation
    UPLOADING = "uploading"                # YouTube/Dropbox
    
    # Terminal states
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

**Implementation:**
- Update `backend/models/job.py` with new states
- Add state transition validation
- Add `state_data` JSON field for stage-specific metadata

**Files to modify:**
- `backend/models/job.py`
- `backend/services/job_manager.py`

---

### Step 1.2.2: Implement Background Worker System

**Current:** All processing in request handlers  
**Needed:** Background workers for long tasks

**Options:**

**A. Cloud Run Background Jobs** (Recommended for MVP)
```python
# backend/workers/audio_worker.py
import asyncio
from google.cloud import firestore

async def process_audio_separation_task(job_id: str):
    """
    Background task for audio separation
    Runs in separate Cloud Run request
    """
    # Update state to SEPARATING_STAGE1
    # Call Modal audio-separator API
    # Poll for completion
    # Download stems
    # Update state to SEPARATING_STAGE2
    # Repeat for backing vocals
    # Update state to AUDIO_COMPLETE
```

**Trigger mechanism:**
```python
# In job submission endpoint
import httpx

# Submit job
job = await job_manager.create_job(...)

# Trigger background worker (async HTTP call)
await httpx.post(
    f"{CLOUD_RUN_URL}/internal/workers/audio",
    json={"job_id": job.id},
    timeout=None
)
```

**B. Cloud Tasks** (Future optimization)
- Better for production
- Automatic retries
- Rate limiting
- Cost tracking

**Implementation Steps:**
1. Create `backend/workers/` directory
2. Implement `audio_worker.py` (audio separation)
3. Implement `lyrics_worker.py` (transcription + correction)
4. Implement `video_worker.py` (encoding + packaging)
5. Add internal endpoints: `/internal/workers/{audio,lyrics,video}`
6. Add worker authentication (shared secret or IAM)

**Files to create:**
- `backend/workers/__init__.py`
- `backend/workers/audio_worker.py`
- `backend/workers/lyrics_worker.py`
- `backend/workers/video_worker.py`
- `backend/api/routes/internal.py`

---

### Step 1.2.3: Integrate `karaoke_gen` Audio Processing

**Current:** Placeholder code  
**Needed:** Real audio separation via Modal API

**Implementation:**
```python
# backend/workers/audio_worker.py
from karaoke_gen.audio_processor import AudioProcessor
from karaoke_gen.file_handler import FileHandler
import os

async def process_audio_separation_task(job_id: str):
    job = await get_job(job_id)
    
    # Download audio from GCS to temp directory
    temp_dir = f"/tmp/{job_id}"
    audio_path = await download_from_gcs(job.file_url, temp_dir)
    
    # Initialize karaoke_gen components
    file_handler = FileHandler(
        input_media=audio_path,
        song_dir=temp_dir,
        artist_title=f"{job.artist} - {job.title}"
    )
    
    audio_processor = AudioProcessor(
        logger=get_job_logger(job_id),
        model_file_dir=None,  # Not needed for remote
        output_dir=temp_dir
    )
    
    # Set environment for remote processing
    os.environ["AUDIO_SEPARATOR_API_URL"] = await get_secret("audio-separator-api-url")
    
    # Stage 1: Clean instrumental
    await update_job_status(job_id, "separating_stage1")
    await audio_processor.process_separation_stage_1(
        audio_path=file_handler.input_media_path
    )
    
    # Stage 2: Backing vocals
    await update_job_status(job_id, "separating_stage2")
    await audio_processor.process_separation_stage_2(
        vocals_path=audio_processor.vocals_path
    )
    
    # Upload stems to GCS
    stems = {
        "instrumental_clean": audio_processor.instrumental_path,
        "instrumental_with_backing": audio_processor.instrumental_with_backing_path,
        "vocals": audio_processor.vocals_path,
        "backing_vocals": audio_processor.backing_vocals_path,
        "lead_vocals": audio_processor.lead_vocals_path,
    }
    
    for stem_type, path in stems.items():
        gcs_url = await upload_to_gcs(path, f"{job_id}/stems/{stem_type}.flac")
        await update_job_metadata(job_id, f"stems.{stem_type}", gcs_url)
    
    # Cleanup local files
    shutil.rmtree(temp_dir)
    
    # Update state
    await update_job_status(job_id, "audio_complete")
    
    # Trigger lyrics worker (if not already running)
    await trigger_worker("lyrics", job_id)
```

**Environment Variables Needed:**
- `AUDIO_SEPARATOR_API_URL` (from Secret Manager)
- `GCS_TEMP_BUCKET` (for stem storage)

**Files to modify:**
- `backend/workers/audio_worker.py`
- `backend/config.py` (add temp bucket config)
- `infrastructure/__main__.py` (add temp bucket, lifecycle rules)

---

### Step 1.2.4: Integrate `karaoke_gen` Lyrics Processing

**Current:** No lyrics processing  
**Needed:** Transcription + correction + corrections JSON

**Implementation:**
```python
# backend/workers/lyrics_worker.py
from karaoke_gen.lyrics_processor import LyricsProcessor
from karaoke_gen.lyrics_transcriber.transcriber import LyricsTranscriber
import os

async def process_lyrics_transcription_task(job_id: str):
    job = await get_job(job_id)
    
    # Download audio from GCS
    temp_dir = f"/tmp/{job_id}"
    audio_path = await download_from_gcs(job.file_url, temp_dir)
    
    # Initialize lyrics processor
    lyrics_processor = LyricsProcessor(
        logger=get_job_logger(job_id),
        artist=job.artist,
        title=job.title,
        audio_filepath=audio_path,
        output_dir=temp_dir
    )
    
    # Set API keys from Secret Manager
    os.environ["AUDIOSHAKE_API_TOKEN"] = await get_secret("audioshake-api-key")
    os.environ["GENIUS_API_TOKEN"] = await get_secret("genius-api-key")
    os.environ["SPOTIFY_COOKIE_SP_DC"] = await get_secret("spotify-cookie")
    os.environ["RAPIDAPI_KEY"] = await get_secret("rapidapi-key")
    
    # Fetch reference lyrics
    await update_job_status(job_id, "transcribing")
    reference_lyrics = await lyrics_processor.fetch_lyrics()
    
    # Transcribe with AudioShake
    transcription = await lyrics_processor.transcribe_audio()
    
    # Automatic correction
    await update_job_status(job_id, "correcting")
    corrections = await lyrics_processor.generate_corrections(
        transcription=transcription,
        reference_lyrics=reference_lyrics
    )
    
    # Upload corrections JSON to GCS
    corrections_url = await upload_json_to_gcs(
        corrections,
        f"{job_id}/lyrics/corrections.json"
    )
    
    # Upload audio for review interface
    audio_url = await upload_to_gcs(
        audio_path,
        f"{job_id}/lyrics/audio.flac"
    )
    
    # Store URLs in job metadata
    await update_job_metadata(job_id, "lyrics.corrections_url", corrections_url)
    await update_job_metadata(job_id, "lyrics.audio_url", audio_url)
    await update_job_metadata(job_id, "lyrics.original_lyrics", reference_lyrics)
    
    # Cleanup
    shutil.rmtree(temp_dir)
    
    # Update state to await human review
    await update_job_status(job_id, "awaiting_review")
    
    # Send notification (email/webhook)
    await notify_user(job_id, "review_ready")
```

**New API Endpoint Needed:**
```python
# backend/api/routes/jobs.py
@router.post("/{job_id}/corrections")
async def submit_corrections(
    job_id: str,
    corrections: dict,
    job_manager: JobManager = Depends(get_job_manager)
):
    """
    User submits corrected lyrics after review
    """
    # Validate corrections format
    # Store corrected lyrics in GCS
    # Update job state to REVIEW_COMPLETE
    # Trigger video generation worker
    return {"status": "accepted"}
```

**Files to create/modify:**
- `backend/workers/lyrics_worker.py`
- `backend/api/routes/jobs.py` (add corrections endpoint)
- `backend/models/requests.py` (add `CorrectionsSubmission` model)

---

### Step 1.2.5: Add Worker Coordination

**Problem:** Audio and lyrics processing run in parallel, both need to complete before video generation

**Solution:** Coordination logic in job manager

```python
# backend/services/job_manager.py
async def check_ready_for_next_stage(job_id: str):
    """
    Called after each worker completes
    Checks if next stage can begin
    """
    job = await get_job(job_id)
    
    # After audio worker completes
    if job.status == "audio_complete":
        # Check if lyrics also complete
        if job.metadata.get("lyrics_complete"):
            await transition_to_video_prep(job_id)
    
    # After lyrics worker completes
    if job.status == "review_complete":
        # Check if audio also complete
        if job.metadata.get("audio_complete"):
            await transition_to_video_prep(job_id)

async def transition_to_video_prep(job_id: str):
    """
    Both audio and lyrics ready
    Generate title/end screens
    Then wait for instrumental selection
    """
    await update_job_status(job_id, "generating_screens")
    await trigger_worker("screens", job_id)
```

**Files to modify:**
- `backend/services/job_manager.py`

---

## Phase 1.3: Human-in-the-Loop Interfaces

**Goal:** Enable user interaction for review and selection

### Step 1.3.1: Lyrics Review API Endpoints

**Needed Endpoints:**

```python
# Get job details for review
GET /api/jobs/{job_id}/review-data
Response:
{
  "corrections_url": "https://storage.googleapis.com/.../corrections.json",
  "audio_url": "https://storage.googleapis.com/.../audio.flac",
  "status": "awaiting_review"
}

# Submit reviewed corrections
POST /api/jobs/{job_id}/corrections
Request:
{
  "corrections": [...],  # Full corrections JSON from frontend
  "user_notes": "Fixed line breaks in chorus"
}
Response:
{
  "status": "accepted",
  "job_status": "review_complete"
}

# Mark job as in-review (user opened interface)
POST /api/jobs/{job_id}/start-review
Response:
{
  "status": "in_review"
}
```

**Files to modify:**
- `backend/api/routes/jobs.py`

---

### Step 1.3.2: Instrumental Selection API Endpoints

**Needed Endpoints:**

```python
# Get instrumental options for playback
GET /api/jobs/{job_id}/instrumental-options
Response:
{
  "options": [
    {
      "id": "clean",
      "label": "Clean Instrumental (no backing vocals)",
      "audio_url": "https://storage.googleapis.com/.../instrumental_clean.flac",
      "duration_seconds": 245.3
    },
    {
      "id": "with_backing",
      "label": "Instrumental with Backing Vocals",
      "audio_url": "https://storage.googleapis.com/.../instrumental_with_backing.flac",
      "duration_seconds": 245.3
    }
  ],
  "status": "awaiting_instrumental_selection"
}

# Submit selection
POST /api/jobs/{job_id}/select-instrumental
Request:
{
  "selection": "clean"  # or "with_backing"
}
Response:
{
  "status": "accepted",
  "job_status": "generating_video"
}
```

**Files to modify:**
- `backend/api/routes/jobs.py`

---

### Step 1.3.3: Notification System

**Current:** No notifications  
**Needed:** Alert user when review/selection needed

**Options:**

**A. Email Notifications** (MVP)
```python
# backend/services/notification_service.py
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

async def notify_review_ready(job_id: str, user_email: str):
    message = Mail(
        from_email='noreply@nomadkaraoke.com',
        to_emails=user_email,
        subject=f'Karaoke ready for review: {job.title}',
        html_content=f'''
            <p>Your karaoke track is ready for lyrics review!</p>
            <p><a href="https://gen.nomadkaraoke.com/review/{job_id}">
                Click here to review
            </a></p>
        '''
    )
    sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
    sg.send(message)
```

**B. Webhook Notifications** (for frontend polling optimization)
```python
# Optional: Send webhook to user's registered endpoint
async def notify_via_webhook(job_id: str, event: str):
    if user_webhook_url := job.metadata.get("webhook_url"):
        await httpx.post(user_webhook_url, json={
            "job_id": job_id,
            "event": event,
            "timestamp": datetime.utcnow().isoformat()
        })
```

**Files to create:**
- `backend/services/notification_service.py`
- `backend/config.py` (add email config)

---

## Phase 1.4: Video Generation & Finalization

**Goal:** Create final karaoke videos in multiple formats

### Step 1.4.1: Title/End Screen Generation

**Implementation:**
```python
# backend/workers/screens_worker.py
from karaoke_gen.video_generator import VideoGenerator

async def generate_screens_task(job_id: str):
    job = await get_job(job_id)
    
    # Load style parameters
    style_params = job.metadata.get("style_params", DEFAULT_STYLE)
    
    # Initialize video generator
    video_gen = VideoGenerator(
        logger=get_job_logger(job_id),
        output_dir=f"/tmp/{job_id}",
        style_params=style_params
    )
    
    # Generate title screen
    title_video = await video_gen.create_title_video(
        artist=job.artist,
        title=job.title
    )
    
    # Generate end screen
    end_video = await video_gen.create_end_video()
    
    # Upload to GCS
    title_url = await upload_to_gcs(title_video, f"{job_id}/screens/title.mov")
    end_url = await upload_to_gcs(end_video, f"{job_id}/screens/end.mov")
    
    await update_job_metadata(job_id, "screens.title", title_url)
    await update_job_metadata(job_id, "screens.end", end_url)
    
    # Transition to awaiting instrumental selection
    await update_job_status(job_id, "awaiting_instrumental_selection")
    await notify_user(job_id, "selection_ready")
```

**Files to create:**
- `backend/workers/screens_worker.py`

---

### Step 1.4.2: Video Generation Worker

**Problem:** Video encoding is CPU-intensive and takes 15-20 minutes  
**Solution:** Use Cloud Build for long-running encoding jobs

**Why Cloud Build?**
- Can allocate more CPU (up to 32 cores)
- Longer timeout (2 hours vs 60 min Cloud Run)
- Better suited for batch processing
- Separate cost tracking

**Implementation:**

```yaml
# backend/cloudbuild-video.yaml
steps:
  # Pull karaoke-gen image
  - name: 'gcr.io/cloud-builders/docker'
    args: ['pull', '${_IMAGE_URL}']
  
  # Run video generation
  - name: '${_IMAGE_URL}'
    env:
      - 'JOB_ID=${_JOB_ID}'
      - 'GCS_BUCKET=${_GCS_BUCKET}'
    script: |
      #!/bin/bash
      python3 /app/workers/video_encoder.py --job-id $JOB_ID

timeout: 7200s  # 2 hours
options:
  machineType: 'N1_HIGHCPU_32'  # 32 vCPUs for parallel encoding
```

**Trigger from backend:**
```python
# backend/services/video_service.py
from google.cloud import build_v1

async def trigger_video_encoding(job_id: str):
    """
    Triggers Cloud Build job for video encoding
    """
    client = build_v1.CloudBuildClient()
    
    build = build_v1.Build(
        source=build_v1.Source(
            storage_source=build_v1.StorageSource(
                bucket="karaoke-build-configs",
                object="cloudbuild-video.yaml"
            )
        ),
        substitutions={
            "_JOB_ID": job_id,
            "_GCS_BUCKET": "karaoke-gen-outputs",
            "_IMAGE_URL": "us-docker.pkg.dev/.../karaoke-backend:latest"
        }
    )
    
    operation = client.create_build(project_id="nomadkaraoke", build=build)
    
    # Store build ID for status tracking
    await update_job_metadata(job_id, "build.operation_id", operation.name)
```

**Video encoding worker:**
```python
# backend/workers/video_encoder.py
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

async def encode_videos_task(job_id: str):
    job = await get_job(job_id)
    
    # Download all assets from GCS
    temp_dir = f"/tmp/{job_id}"
    await download_job_assets(job_id, temp_dir)
    
    # Initialize finalizer
    finaliser = KaraokeFinalise(
        logger=get_job_logger(job_id),
        output_dir=temp_dir,
        artist=job.artist,
        title=job.title
    )
    
    # Get user's instrumental selection
    selected_instrumental = job.metadata["instrumental_selection"]
    
    # Generate all video formats
    await update_job_status(job_id, "encoding")
    
    videos = await finaliser.generate_all_formats(
        lyrics_video=os.path.join(temp_dir, "with_vocals.mkv"),
        instrumental=os.path.join(temp_dir, f"instrumental_{selected_instrumental}.flac"),
        title_screen=os.path.join(temp_dir, "title.mov"),
        end_screen=os.path.join(temp_dir, "end.mov")
    )
    
    # Upload final videos to GCS
    for format_name, video_path in videos.items():
        url = await upload_to_gcs(video_path, f"{job_id}/finals/{format_name}.mp4")
        await update_job_metadata(job_id, f"outputs.{format_name}", url)
    
    # Generate CDG/TXT if requested
    if job.metadata.get("enable_cdg"):
        await generate_cdg_package(job_id, temp_dir)
    
    if job.metadata.get("enable_txt"):
        await generate_txt_package(job_id, temp_dir)
    
    # Cleanup
    shutil.rmtree(temp_dir)
    
    # Next: packaging/upload
    await update_job_status(job_id, "packaging")
    await trigger_worker("distribution", job_id)
```

**Files to create:**
- `cloudbuild-video.yaml`
- `backend/workers/video_encoder.py`
- `backend/services/video_service.py`
- `infrastructure/__main__.py` (add Cloud Build triggers)

---

### Step 1.4.3: CDG/TXT Generation

**Implementation:**
```python
# backend/workers/packaging_worker.py
from karaoke_gen.lyrics_transcriber.output.cdg import CDGGenerator
from lyrics_converter import convert_lrc_to_txt

async def generate_cdg_package(job_id: str, work_dir: str):
    """
    Generate CD+Graphics karaoke package
    """
    # Load corrected LRC
    lrc_path = os.path.join(work_dir, f"{job.artist} - {job.title} (Karaoke).lrc")
    
    # Convert to TOML for CDG generator
    toml_path = convert_lrc_to_toml(lrc_path)
    
    # Generate CDG file
    cdg_generator = CDGGenerator()
    cdg_path = cdg_generator.generate(toml_path)
    
    # Convert instrumental to MP3
    mp3_path = await convert_to_mp3(
        os.path.join(work_dir, "instrumental.flac"),
        bitrate="320k"
    )
    
    # Create ZIP
    zip_path = os.path.join(work_dir, f"{job.artist} - {job.title} (CDG).zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.write(cdg_path, f"{job.artist} - {job.title} (Karaoke).cdg")
        zf.write(mp3_path, f"{job.artist} - {job.title} (Karaoke).mp3")
    
    # Upload to GCS
    url = await upload_to_gcs(zip_path, f"{job_id}/packages/cdg.zip")
    await update_job_metadata(job_id, "outputs.cdg_package", url)

async def generate_txt_package(job_id: str, work_dir: str):
    """
    Generate plain text karaoke package
    """
    # Similar to CDG but with TXT format
    pass
```

**Files to modify:**
- `backend/workers/packaging_worker.py`

---

## Phase 1.5: Distribution Features (Optional for MVP)

**Goal:** Automate uploads and notifications

### Step 1.5.1: YouTube Upload

**Implementation:**
```python
# backend/workers/distribution_worker.py
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

async def upload_to_youtube(job_id: str):
    """
    Upload final video to YouTube
    """
    job = await get_job(job_id)
    
    # Get user's YouTube OAuth token from job metadata
    youtube_token = job.metadata.get("youtube_credentials")
    
    if not youtube_token:
        # Skip if not configured
        return
    
    # Download lossless MKV
    video_path = await download_from_gcs(
        job.metadata["outputs"]["lossless_4k_mkv"],
        f"/tmp/{job_id}.mkv"
    )
    
    # Authenticate
    credentials = Credentials(**youtube_token)
    youtube = build('youtube', 'v3', credentials=credentials)
    
    # Check for duplicates
    existing = await search_youtube_video(youtube, f"{job.artist} - {job.title}")
    if existing:
        await update_job_metadata(job_id, "youtube.url", existing)
        return
    
    # Upload
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": f"{job.artist} - {job.title} (Karaoke)",
                "description": job.metadata.get("youtube_description", ""),
                "tags": ["karaoke", job.artist, job.title],
            },
            "status": {
                "privacyStatus": "unlisted"
            }
        },
        media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True)
    )
    
    response = request.execute()
    youtube_url = f"https://www.youtube.com/watch?v={response['id']}"
    
    # Set thumbnail
    await upload_youtube_thumbnail(youtube, response['id'], job.metadata["screens"]["title"])
    
    # Store URL
    await update_job_metadata(job_id, "youtube.url", youtube_url)
```

**Files to create:**
- `backend/workers/distribution_worker.py`

---

### Step 1.5.2: Discord/Email Notifications

**Implementation:**
```python
# backend/workers/distribution_worker.py
async def send_completion_notification(job_id: str):
    job = await get_job(job_id)
    
    # Discord webhook
    if discord_url := os.environ.get("DISCORD_WEBHOOK_URL"):
        await httpx.post(discord_url, json={
            "content": f"✅ Karaoke complete: **{job.artist} - {job.title}**",
            "embeds": [{
                "title": f"{job.artist} - {job.title}",
                "url": job.metadata.get("youtube.url"),
                "fields": [
                    {"name": "YouTube", "value": job.metadata.get("youtube.url", "N/A")},
                    {"name": "Downloads", "value": f"[View Job](https://gen.nomadkaraoke.com/jobs/{job_id})"}
                ]
            }]
        })
    
    # Email to user
    if user_email := job.metadata.get("user_email"):
        await send_email(
            to=user_email,
            subject=f"Karaoke Complete: {job.title}",
            body=f"""
                Your karaoke track is ready!
                
                Artist: {job.artist}
                Title: {job.title}
                
                Downloads: https://gen.nomadkaraoke.com/jobs/{job_id}
                YouTube: {job.metadata.get('youtube.url', 'Not uploaded')}
            """
        )
```

---

## Phase 2: React Frontend Implementation

**Goal:** Build user interface for all cloud features

### Step 2.1: Project Setup

```bash
# Create React app with Vite
cd frontend-react
npm create vite@latest . -- --template react-ts
npm install

# Install dependencies
npm install \
  @tanstack/react-query \
  axios \
  zustand \
  tailwindcss \
  postcss \
  autoprefixer \
  react-router-dom \
  @headlessui/react \
  lucide-react

# Setup Tailwind
npx tailwindcss init -p
```

**File structure:**
```
frontend-react/
├── src/
│   ├── components/
│   │   ├── JobSubmission.tsx       # Upload/URL input
│   │   ├── JobProgress.tsx         # Real-time status
│   │   ├── LyricsReview.tsx        # ⚠️ CRITICAL - Review interface
│   │   ├── InstrumentalSelector.tsx # ⚠️ CRITICAL - Audio selection
│   │   ├── VideoDownloads.tsx      # Download final files
│   │   └── ErrorDisplay.tsx
│   ├── pages/
│   │   ├── Home.tsx
│   │   ├── JobDetails.tsx
│   │   └── ReviewPage.tsx
│   ├── hooks/
│   │   ├── useJobSubmit.ts
│   │   ├── useJobStatus.ts
│   │   ├── useFileUpload.ts
│   │   └── useCorrectionsSubmit.ts
│   ├── services/
│   │   └── api.ts
│   ├── types/
│   │   └── job.ts
│   ├── stores/
│   │   └── appStore.ts
│   ├── App.tsx
│   └── main.tsx
```

---

### Step 2.2: Critical Component: Lyrics Review Interface

**This is the most complex component** - must embed the entire lyrics-transcriber review UI

**Options:**

**A. Embed existing lyrics-transcriber React app**
```tsx
// frontend-react/src/components/LyricsReview.tsx
import { useEffect, useState } from 'react';

export function LyricsReview({ jobId }: { jobId: string }) {
  const [reviewData, setReviewData] = useState(null);
  
  useEffect(() => {
    // Fetch corrections JSON and audio URL from backend
    fetch(`/api/jobs/${jobId}/review-data`)
      .then(r => r.json())
      .then(setReviewData);
  }, [jobId]);
  
  if (!reviewData) return <div>Loading review interface...</div>;
  
  // Embed lyrics-transcriber review UI
  // This requires packaging lyrics-transcriber as an npm package
  // Or copying the React components from lyrics_transcriber_local/
  return (
    <div className="lyrics-review-container">
      <LyricsTranscriberReview
        correctionsUrl={reviewData.corrections_url}
        audioUrl={reviewData.audio_url}
        onSubmit={(corrections) => {
          // Submit corrections back to backend
          fetch(`/api/jobs/${jobId}/corrections`, {
            method: 'POST',
            body: JSON.stringify({ corrections })
          });
        }}
      />
    </div>
  );
}
```

**B. Use iframe to embedded review server** (easier MVP)
```tsx
export function LyricsReview({ jobId }: { jobId: string }) {
  // Backend spins up temporary review server with corrections pre-loaded
  // Frontend embeds it in iframe
  return (
    <iframe
      src={`/api/jobs/${jobId}/review-interface`}
      className="w-full h-screen"
    />
  );
}
```

**Recommended:** Option B for MVP, Option A for production

---

### Step 2.3: Instrumental Selector Component

**Simpler component with audio playback:**

```tsx
// frontend-react/src/components/InstrumentalSelector.tsx
import { useState } from 'react';

export function InstrumentalSelector({ jobId }: { jobId: string }) {
  const [options, setOptions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [playing, setPlaying] = useState(null);
  
  useEffect(() => {
    fetch(`/api/jobs/${jobId}/instrumental-options`)
      .then(r => r.json())
      .then(data => setOptions(data.options));
  }, [jobId]);
  
  const handleSubmit = async () => {
    await fetch(`/api/jobs/${jobId}/select-instrumental`, {
      method: 'POST',
      body: JSON.stringify({ selection: selected })
    });
    // Redirect to job status page
  };
  
  return (
    <div className="space-y-6">
      <h2>Choose Instrumental Audio</h2>
      <p>Listen to both options and select your preference:</p>
      
      {options.map(option => (
        <div key={option.id} className="border p-4 rounded">
          <div className="flex items-center justify-between">
            <div>
              <h3>{option.label}</h3>
              <p className="text-sm text-gray-600">Duration: {option.duration_seconds}s</p>
            </div>
            
            <div className="flex gap-2">
              <button onClick={() => playAudio(option.audio_url)}>
                {playing === option.id ? 'Pause' : 'Play'}
              </button>
              <button 
                onClick={() => setSelected(option.id)}
                className={selected === option.id ? 'bg-blue-500 text-white' : ''}
              >
                Select
              </button>
            </div>
          </div>
          
          <audio
            ref={ref => audioRefs.current[option.id] = ref}
            src={option.audio_url}
            onEnded={() => setPlaying(null)}
          />
        </div>
      ))}
      
      <button 
        onClick={handleSubmit}
        disabled={!selected}
        className="w-full bg-green-500 text-white p-4 rounded"
      >
        Continue with {selected === 'clean' ? 'Clean' : 'Backing Vocals'} Instrumental
      </button>
    </div>
  );
}
```

---

### Step 2.4: Job Status Polling

**Real-time progress updates:**

```tsx
// frontend-react/src/hooks/useJobStatus.ts
import { useQuery } from '@tanstack/react-query';

export function useJobStatus(jobId: string) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => fetch(`/api/jobs/${jobId}`).then(r => r.json()),
    refetchInterval: (data) => {
      // Poll every 5 seconds if job is in progress
      const terminalStates = ['complete', 'failed', 'cancelled'];
      return terminalStates.includes(data?.status) ? false : 5000;
    }
  });
}
```

**Progress component:**

```tsx
// frontend-react/src/components/JobProgress.tsx
export function JobProgress({ jobId }: { jobId: string }) {
  const { data: job } = useJobStatus(jobId);
  
  const stages = [
    { status: 'pending', label: 'Queued', icon: Clock },
    { status: 'downloading', label: 'Downloading', icon: Download },
    { status: 'separating_stage1', label: 'Audio Separation (1/2)', icon: Music },
    { status: 'separating_stage2', label: 'Audio Separation (2/2)', icon: Music },
    { status: 'transcribing', label: 'Lyrics Transcription', icon: FileText },
    { status: 'correcting', label: 'Auto-Correction', icon: Wand },
    { status: 'awaiting_review', label: 'Ready for Review', icon: AlertCircle },
    { status: 'generating_video', label: 'Generating Video', icon: Video },
    { status: 'encoding', label: 'Encoding (15-20 min)', icon: Cog },
    { status: 'complete', label: 'Complete!', icon: CheckCircle },
  ];
  
  const currentStageIndex = stages.findIndex(s => s.status === job?.status);
  
  return (
    <div className="space-y-4">
      {/* Progress bar */}
      <div className="h-2 bg-gray-200 rounded">
        <div 
          className="h-full bg-blue-500 transition-all"
          style={{ width: `${(currentStageIndex / stages.length) * 100}%` }}
        />
      </div>
      
      {/* Stage list */}
      <div className="space-y-2">
        {stages.map((stage, i) => (
          <div 
            key={stage.status}
            className={`flex items-center gap-2 ${i <= currentStageIndex ? 'opacity-100' : 'opacity-50'}`}
          >
            <stage.icon className={i === currentStageIndex ? 'animate-pulse' : ''} />
            <span>{stage.label}</span>
            {i < currentStageIndex && <CheckCircle className="text-green-500" />}
          </div>
        ))}
      </div>
      
      {/* Action prompts */}
      {job?.status === 'awaiting_review' && (
        <div className="p-4 bg-yellow-50 border border-yellow-200 rounded">
          <h3 className="font-bold">Action Required</h3>
          <p>Your lyrics are ready for review!</p>
          <button onClick={() => navigate(`/review/${jobId}`)}>
            Review Lyrics →
          </button>
        </div>
      )}
      
      {job?.status === 'awaiting_instrumental_selection' && (
        <div className="p-4 bg-yellow-50 border border-yellow-200 rounded">
          <h3 className="font-bold">Action Required</h3>
          <p>Please select your preferred instrumental audio.</p>
          <button onClick={() => navigate(`/select/${jobId}`)}>
            Choose Instrumental →
          </button>
        </div>
      )}
    </div>
  );
}
```

---

### Step 2.5: Deploy to Cloudflare Pages

```bash
# Build frontend
cd frontend-react
npm run build

# Deploy
npx wrangler pages deploy dist \
  --project-name karaoke-gen-frontend \
  --branch main

# Set environment variables
npx wrangler pages secret put VITE_API_URL
# Enter: https://karaoke-backend-xxx.run.app
```

**Cloudflare Pages configuration:**
```toml
# wrangler.toml
name = "karaoke-gen-frontend"
pages_build_output_dir = "dist"

[env.production]
vars = { VITE_API_URL = "https://karaoke-backend-xxx.run.app" }
```

---

## Phase 3: Integration & Testing

### Step 3.1: End-to-End Testing

**Manual test scenarios:**

1. **Full workflow - URL submission:**
   - Submit YouTube URL
   - Wait for review notification
   - Complete lyrics review
   - Select instrumental
   - Download final videos

2. **Full workflow - file upload:**
   - Upload FLAC file
   - Same as above

3. **Error scenarios:**
   - Invalid URL
   - Upload too large
   - API failures
   - User abandons review

4. **Concurrent jobs:**
   - Submit 3 jobs simultaneously
   - Verify no cross-contamination

**Automated E2E tests:**
```typescript
// frontend-react/tests/e2e/full-workflow.spec.ts
import { test, expect } from '@playwright/test';

test('complete karaoke generation workflow', async ({ page }) => {
  // Submit job
  await page.goto('/');
  await page.fill('[name="url"]', 'https://youtube.com/watch?v=...');
  await page.fill('[name="artist"]', 'ABBA');
  await page.fill('[name="title"]', 'Waterloo');
  await page.click('button:has-text("Create Karaoke")');
  
  // Wait for review ready (poll with timeout)
  await page.waitForSelector('text=Ready for Review', { timeout: 600000 });
  
  // Click review button
  await page.click('button:has-text("Review Lyrics")');
  
  // Make some edits (assuming embedded review interface)
  // ... review interface interactions ...
  
  // Submit corrections
  await page.click('button:has-text("Submit Corrections")');
  
  // Wait for instrumental selection
  await page.waitForSelector('text=Choose Instrumental');
  
  // Select option
  await page.click('text=Clean Instrumental');
  
  // Wait for completion
  await page.waitForSelector('text=Complete', { timeout: 1800000 });
  
  // Verify downloads available
  await expect(page.locator('text=Lossless 4K MP4')).toBeVisible();
  await expect(page.locator('text=CDG+MP3 Package')).toBeVisible();
});
```

---

### Step 3.2: Performance Optimization

**Backend:**
- Use Cloud Run min instances = 1 (eliminate cold starts for paying customers)
- Enable Cloud CDN for GCS downloads
- Compress large files before upload
- Use connection pooling for Firestore

**Frontend:**
- Code splitting (separate chunks for review interface)
- Lazy load audio player
- Optimize bundle size (<200KB initial)
- Use service worker for offline status viewing

---

### Step 3.3: Cost Optimization

**Estimated costs per job:**

| Service | Cost |
|---------|------|
| Cloud Run (processing) | $0.05 |
| Cloud Build (encoding) | $0.20 |
| GCS storage (30 days) | $0.10 |
| GCS bandwidth (download) | $0.05 |
| AudioShake API | $0.04 |
| **Total** | **$0.44** |

**Revenue:** $2.00 per job  
**Margin:** $1.56 (78%)

**Optimization strategies:**
- Lifecycle policies: Delete temp files after 7 days
- Lifecycle policies: Delete final files after 30 days (or move to Nearline)
- Use compression for all uploads
- Batch Cloud Build jobs when possible

---

## 🎯 Success Criteria

Before launch:

- [ ] Complete workflow works end-to-end
- [ ] Lyrics review interface functional
- [ ] Instrumental selection works
- [ ] All video formats generated correctly
- [ ] CDG/TXT packages created
- [ ] YouTube upload works (optional)
- [ ] Notifications sent at correct times
- [ ] Concurrent jobs don't interfere
- [ ] Error handling graceful
- [ ] Costs within budget
- [ ] Frontend responsive on mobile
- [ ] Load time < 3 seconds

---

## 📅 Estimated Timeline

| Phase | Tasks | Duration |
|-------|-------|----------|
| 1.2 | Async processing infrastructure | 3-4 days |
| 1.3 | Human-in-the-loop APIs | 2 days |
| 1.4 | Video generation workers | 3-4 days |
| 1.5 | Distribution features | 1-2 days (optional) |
| 2 | React frontend | 4-5 days |
| 3 | Integration & testing | 2-3 days |
| **Total** | **MVP with full CLI parity** | **15-20 days** |

---

## 🚀 Getting Started

**Next immediate steps:**

1. **Implement job state machine** (Step 1.2.1)
   - Update `backend/models/job.py` with all states
   - Add state transition validation
   - Add `state_data` JSON field

2. **Create worker infrastructure** (Step 1.2.2)
   - Create `backend/workers/` directory
   - Implement basic background task pattern
   - Add internal worker endpoints

3. **Integrate audio processing** (Step 1.2.3)
   - Import `AudioProcessor` from karaoke_gen
   - Test Modal API connection
   - Verify stem upload/download

4. **Test first async workflow**
   - Submit job
   - Trigger audio worker
   - Verify state transitions
   - Check GCS uploads

**Let's start with Step 1.2.1!**
