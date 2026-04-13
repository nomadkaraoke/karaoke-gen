# Architecture

## System Overview

```text
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (Cloudflare Pages)                                    │
│  Next.js + TypeScript                                           │
│  https://gen.nomadkaraoke.com                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTPS
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Backend API (Cloud Run)                                        │
│  FastAPI + Python 3.12                                          │
│  https://api.nomadkaraoke.com                                   │
│                                                                 │
│  Workers:                                                       │
│  • Audio Worker    - Stem separation via Cloud Run GPU (L4)              │
│  • Lyrics Worker   - Transcription via AudioShake               │
│  • Screens Worker  - Title/end screen generation                │
│  • Render Worker   - Karaoke video with lyrics                  │
│  • Video Worker    - Final assembly and encoding                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┬───────────────┐
              ▼             ▼             ▼               ▼
        ┌──────────┐  ┌──────────┐  ┌──────────────┐ ┌──────────────┐
        │ Firestore│  │   GCS    │  │Secret Manager│ │GCE Encoding  │
        │ (jobs)   │  │ (files)  │  │  (API keys)  │ │  Worker (*)  │
        └──────────┘  └──────────┘  └──────────────┘ └──────────────┘

(*) GCE Encoding Worker: c4d-highcpu-32 VM with AMD EPYC 9B45 (Turin) for
    high-performance FFmpeg encoding (4.92x faster than previous c4-standard-8).
    Used for both final video encoding and preview video generation.
    Uses immutable deployment pattern - see infrastructure/encoding-worker/README.md.
```

### Error Monitor

```text
Cloud Logging (all services) ──→ Error Monitor (Cloud Run Job, */15 min)
                                    │
                                    ├─ Normalize & deduplicate
                                    ├─ LLM incident grouping (Gemini Flash)
                                    ├─ Discord alerts (new patterns, spikes, daily digest)
                                    ├─ Firestore: error_patterns, error_incidents
                                    └─ Auto-resolve silent patterns

Entry point: python -m backend.services.error_monitor.monitor
Design spec: docs/archive/2026-04-12-error-monitor-design.md
```

## Processing Pipeline

```text
1. UPLOAD
   POST /api/jobs/upload
   • Save audio to GCS
   • Create job in Firestore
   • Trigger parallel workers

2. PARALLEL PROCESSING (with Worker Registry coordination)
   ┌─────────────────┐    ┌─────────────────┐
   │  Audio Worker   │    │  Lyrics Worker  │
   │  Cloud Run GPU      │    │  AudioShake     │
   │  2-stage sep    │    │  Auto-correct   │
   │  ↓ register()   │    │  ↓ register()   │
   │  ↓ unregister() │    │  ↓ unregister() │
   └────────┬────────┘    └────────┬────────┘
            └──────────┬───────────┘
                       ▼
3. SCREENS GENERATION + BACKING VOCALS ANALYSIS + AUDIO TRANSCODING
   • Title screen (5s video)
   • End screen (5s video)
   • Analyze backing vocals track (for instrumental selection)
   • Transcode audio to OGG Opus for review UI (~3 MB vs 35 MB FLAC)
                       ▼
4. COMBINED HUMAN REVIEW (AWAITING_REVIEW)
   • React UI for lyrics correction
   • Preview video generation (optionally via GCE for speed)
   • Instrumental track selection (clean vs with_backing)
   • Single submit saves both lyrics + instrumental choice
                       ▼
5. VIDEO RENDERING (optionally via GCE for speed + memory)
   • Merge corrections with original data
   • Generate karaoke video with lyrics (OutputGenerator + ffmpeg/libass)
   • Style assets cached on GCE worker disk between jobs
                       ▼
6. FINAL VIDEO (INSTRUMENTAL_SELECTED, via GCE)
   • Remux with selected instrumental
   • Concatenate: title + karaoke + end
   • Encode to 4 formats
                       ▼
7. COMPLETE
   • User downloads via signed URLs
```

**Note**: As of 2026-01, instrumental selection is combined with lyrics review. Users complete both in a single session rather than in separate steps.

## Job States

| State | Description | Human Action |
|-------|-------------|--------------|
| `pending` | Job created | - |
| `downloading` | Processing audio | - |
| `separating_stage1` | Audio separation stage 1 | - |
| `separating_stage2` | Audio separation stage 2 | - |
| `transcribing` | Lyrics transcription | - |
| `generating_screens` | Creating title/end screens + backing vocals analysis | - |
| `awaiting_review` | **Waiting for human** | Review lyrics + select instrumental |
| `in_review` | Human actively reviewing | Edit lyrics + select instrumental |
| `review_complete` | Review submitted with instrumental selection | - |
| `rendering_video` | Generating karaoke video | - |
| `instrumental_selected` | Instrumental confirmed, ready for final video | - |
| `generating_video` | Final encoding | - |
| `complete` | All done | Download files |
| `failed` | Error occurred | - |

**Note**: `awaiting_instrumental_selection` exists for backwards compatibility with historical jobs but is no longer used. Instrumental selection is now part of the combined review (`awaiting_review` → `in_review` → `review_complete`).

**State Machine Robustness (Feb 2026)**: State transitions are enforced by `STATE_TRANSITIONS` in `models/job.py`. Invalid transitions raise `InvalidStateTransitionError` by default. Use `scripts/generate_state_diagram.py` to generate a Mermaid diagram of valid transitions. The `/health/job-consistency` endpoint detects jobs stuck in invalid states. See `docs/archive/2026-02-02-state-machine-robustness-plan.md` for implementation details.

## GCS File Structure

```text
jobs/{job_id}/
├── input/
│   └── original.flac              # Uploaded audio
├── stems/
│   ├── vocals.flac
│   ├── instrumental_clean.flac
│   ├── instrumental_with_backing.flac
│   └── ...
├── lyrics/
│   ├── corrections.json           # Auto-corrected
│   ├── corrections_updated.json   # After human review
│   ├── edit_log_{session}.json    # User edit log with feedback
│   ├── annotations.json           # Correction annotations
│   └── karaoke.ass
├── screens/
│   ├── title.mov
│   └── end.mov
├── videos/
│   └── with_vocals.mkv            # Raw karaoke video (FLAC audio, large)
└── finals/
    ├── with_vocals_mp4.mp4        # Encoded with-vocals (H264/AAC, ~3x smaller)
    ├── lossless_4k.mp4
    ├── lossless_4k.mkv
    ├── lossy_4k.mp4
    └── lossy_720p.mp4
```

## LyricsTranscriber Integration

**Key Design Decision**: We use LyricsTranscriber as a **library**, not a server.

```text
CLI Mode (Original):              Cloud Mode (Our Approach):
────────────────────              ──────────────────────────
LyricsTranscriber                 LyricsTranscriber
├── transcribe()                  ├── transcribe()    ✅
├── correct()                     ├── correct()       ✅
├── ReviewServer.start()          │
│   └── BLOCKS (waits)            │  (Skip server, save JSON)
│                                 │  ... ASYNC GAP ...
│                                 │  (Human reviews via React)
└── OutputGenerator               │
                                  └── OutputGenerator  ✅
                                      (called after review)
```

**Use**: `LyricsTranscriber`, `CorrectionResult`, `OutputGenerator`, `OutputConfig`
**Don't use**: `ReviewServer`, blocking waits

## External Services

| Service | Purpose | Required |
|---------|---------|----------|
| Cloud Run GPU Job (L4, us-east4) | Audio stem separation — `Separator` runs directly in the audio worker Job (no HTTP hop) | Yes |
| AudioShake | Lyrics transcription | Yes |
| Vertex AI | Agentic AI correction (Gemini 3 Flash) | Default off (SKIP_CORRECTION=false to enable) |
| Genius | Reference lyrics | Yes |
| Flacfetch | Audio downloads (YouTube, torrents) | Recommended* |
| YouTube API | Video upload | Optional |
| SendGrid | Email notifications | Optional |
| Cloud Tasks | Delayed task scheduling (idle reminders, YouTube queue) | Optional |
| Cloud Scheduler | Hourly YouTube upload queue processing | Optional |
| Cloud Monitoring | Cross-reference YouTube API quota consumption from GCP metrics | Optional |
| karaoke-decide | Song catalog (MusicBrainz + Spotify) for autocomplete | Optional |
| KaraokeNerds | Community karaoke version detection | Optional |

*Flacfetch runs on a dedicated GCE VM with YouTube cookies and tracker access. Without it, YouTube downloads will fail due to bot detection on Cloud Run IPs.

### Data Pipeline Cloud Functions

Three Cloud Functions manage the community karaoke data pipeline, running daily on automated schedules:

| Function | Schedule | Purpose |
|----------|----------|---------|
| `divebar-mirror` | Daily 2:00 AM ET | Indexes 48K+ files from [diveBar Karaoke Google Drive](https://drive.google.com/drive/folders/1zxnSZcE03gzy0YVGOdnTrEIi8It_3Wu8) into BigQuery `divebar_catalog` |
| `kn-data-sync` | Daily 3:05 AM + 4:30 AM ET | Fetches KaraokeNerds catalog (281K songs) and community tracks (58K with YouTube URLs) to BigQuery + GCS |
| `divebar-lookup` | Daily 6:00 AM ET + on-demand API | Public search/lookup API for KJ Controller; rebuilds KN↔Divebar cross-reference index |

**Infrastructure** (Pulumi-managed in `infrastructure/`):
- Service accounts: `divebar-mirror@`, `kn-data-sync@`, `divebar-lookup@` (least-privilege IAM)
- GCS: `nomadkaraoke-kn-data` (raw exports), 3 source buckets for function code
- BigQuery tables: `divebar_catalog`, `karaokenerds_community`, `kn_divebar_xref` (in `karaoke_decide` dataset)
- Secret: `karaokenerds-api-key` (KN API access)

**Source code:** `infrastructure/functions/{divebar_mirror,kn_data_sync,divebar_lookup}/`

## Firestore Collections

karaoke-gen shares a GCP project (`nomadkaraoke`) with karaoke-decide, but uses separate Firestore collections:

| Collection | Purpose | Key Fields |
|------------|---------|------------|
| `gen_users` | karaoke-gen user accounts | email, credits, role, is_active |
| `jobs` | Karaoke generation jobs | job_id, user_email, status, state_data, processing_metadata |
| `jobs/{job_id}/logs` | Worker log entries (subcollection) | timestamp, level, worker, message, ttl_expiry |
| `sessions` | Magic link auth sessions | user_email, token, expires_at |
| `magic_links` | Passwordless auth tokens | email, token, expires_at, used |
| `user_feedback` | User feedback for credits (all users) | user_email, ratings, comments, created_at |
| `beta_feedback` | Beta program feedback (deprecated) | user_email, ratings, comments |
| `youtube_quota_pending` | Pending YouTube API quota buffer (~7min GCP delay) | pending_units, pending_uploads[], updated_at |
| `youtube_upload_queue` | Deferred YouTube uploads | job_id, status, user_email, queued_at, youtube_url |

**Note**: The `users` collection in the same Firestore instance belongs to karaoke-decide (different schema: user_id, is_guest, quiz_* fields). Don't use it for karaoke-gen.

**Worker Logs**: Stored in subcollection (`jobs/{job_id}/logs`) instead of embedded array to avoid Firestore's 1MB document size limit. TTL policy auto-deletes logs after 30 days. Feature flag: `USE_LOG_SUBCOLLECTION` (default: true). See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#firestore-document-1mb-limit-with-embedded-arrays).

## Encoding Worker Blue-Green Deployment

The GCE encoding worker uses a blue-green deployment pattern with two named VMs (`encoding-worker-a` and `encoding-worker-b`).

### Architecture

```text
Frontend → Backend (Cloud Run) → Firestore config → Primary VM (encoding-worker-a or b)
                                                   ↑
Cloud Scheduler (5 min) → Cloud Function → checks idle + stops VMs
                                                   ↑
CI (GitHub Actions) → deploys to secondary → health check → swap Firestore → stop old
```

### Key Components

**Two VMs (a/b)**: Both VMs are TERMINATED by default. One is designated "primary" and serves all encoding traffic. The secondary VM exists solely to receive new deployments before they go live.

**Firestore config doc** (`config/encoding-worker`): Single source of truth for routing. Fields: `primary` (a or b), `ip_a`, `ip_b`, `deploy_in_progress` flag, `activity_a`/`activity_b` timestamps.

**Backend routing** (`encoding_interface.py`): Reads the primary IP from the Firestore config doc with a 30-second TTL cache. All encoding requests route to the current primary.

**JIT startup**: When a user enters the lyrics review page, the backend starts the primary VM on demand. This gives ~60 seconds of natural warmup time before encoding is needed, avoiding both 24/7 costs and noticeable cold-start latency for the user.

**Idle auto-shutdown**: A Cloud Function (triggered every 5 minutes by Cloud Scheduler) checks the `activity_*` timestamps in Firestore and stops any VM that has been idle for more than 15 minutes. This is external to the VM — if the encoding worker code itself has a bug, the Cloud Function still shuts it down.

**Blue-green deploys**: CI builds a new wheel, deploys it to the secondary VM (not the live primary), runs a real encode test against the secondary, then atomically swaps the `primary` field in Firestore to point to the secondary. The old primary is stopped after the swap. If the health check fails, the primary is never updated.

### Seeding the Config

Initial setup or reset:
```bash
python scripts/seed-encoding-worker-config.py <ip-a> <ip-b>
```

### Deploy Flow

```text
1. CI builds new karaoke-gen wheel + pushes to GCS
2. CI starts secondary VM, waits for /health to respond
3. CI runs real encode test against secondary VM
4. If test passes: CI writes secondary name to Firestore `primary` field
5. CI stops the old primary VM
6. If test fails: CI stops secondary VM, primary unchanged
```

## Video Worker Orchestrator

The Video Worker uses an orchestrator pattern to ensure all features work regardless of encoding backend (local Cloud Run or GCE VM).

```text
┌─────────────────────────────────────────────────────────────┐
│                 VideoWorkerOrchestrator                      │
│                                                             │
│  Stage 1: PACKAGING        Stage 2: ENCODING                │
│  ├─ CDG file generation    ├─ Local (FFmpeg)               │
│  └─ TXT lyrics export      └─ GCE (remote VM)              │
│                                                             │
│  Stage 3: ORGANIZATION     Stage 4: DISTRIBUTION           │
│  ├─ Final path setup       ├─ Dropbox upload               │
│  └─ File organization      ├─ Google Drive upload          │
│                            └─ YouTube upload               │
│                                                             │
│  Stage 5: NOTIFICATIONS                                     │
│  ├─ Discord webhooks                                        │
│  └─ Email notifications (completion, reminders)             │
└─────────────────────────────────────────────────────────────┘
```

**Key Design Decision**: The orchestrator coordinates stages rather than embedding logic in monolithic functions. This:
- Ensures all features work with both local and GCE encoding
- Makes each stage independently testable (139 new tests)
- Allows feature flag rollback via `USE_NEW_ORCHESTRATOR` env var

**Service Modules**:
| Module | Purpose |
|--------|---------|
| `youtube_download_service.py` | YouTube audio downloads (via remote flacfetch) |
| `flacfetch_client.py` | Client for remote flacfetch HTTP API |
| `audio_search_service.py` | Audio source search (YouTube, RED, OPS, Spotify) |
| `youtube_upload_service.py` | YouTube OAuth flow and upload |
| `discord_service.py` | Discord webhook notifications |
| `packaging_service.py` | CDG/TXT package generation |
| `local_encoding_service.py` | FFmpeg encoding (Cloud Run) |
| `encoding_interface.py` | Abstract interface + GCE backend |
| `video_worker_orchestrator.py` | Stage coordination |
| `email_service.py` | SendGrid email delivery with CC support |
| `template_service.py` | GCS-backed email templates |
| `job_notification_service.py` | Email orchestration (completion, reminders) |
| `audio_transcoding_service.py` | Transcode FLAC → OGG Opus for review UI playback |
| `youtube_quota_service.py` | Track YouTube API quota (GCP Cloud Monitoring + Firestore pending buffer) |
| `email_validation_service.py` | Email validation, tiered disposable domain detection (static list + DeBounce + verifymail.io APIs), blocklist management |
| `youtube_upload_queue_service.py` | Deferred YouTube upload queue management |
| `catalog_proxy_service.py` | Proxy to karaoke-decide catalog API (artist/track search) |
| `karaokenerds_service.py` | Scrape karaokenerds.com for community karaoke versions |

## Audio Source Download Flow

Audio can come from file upload or remote search (YouTube, torrents). All YouTube downloads route through `YouTubeDownloadService` to ensure consistent handling:

```text
┌──────────────────────────────────────────────────────────────────────┐
│                     Audio Source Entry Points                         │
│                                                                      │
│  1. File Upload (/api/jobs/upload)                                   │
│     └─ Direct to GCS → triggers workers                              │
│                                                                      │
│  2. YouTube URL (/api/jobs/create-from-url)                          │
│     └─ YouTubeDownloadService → GCS → triggers workers               │
│                                                                      │
│  3. Audio Search + Select (/api/audio-search/...)                    │
│     ├─ YouTube  → YouTubeDownloadService → GCS → triggers workers    │
│     ├─ RED/OPS  → FlacfetchClient (torrent) → GCS → triggers workers │
│     └─ Spotify  → FlacfetchClient (spotify) → GCS → triggers workers │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    YouTubeDownloadService                             │
│                                                                      │
│  • Single entry point for ALL YouTube downloads in cloud             │
│  • Uses remote flacfetch when FLACFETCH_API_URL is set (recommended) │
│  • Falls back to local yt_dlp with warning (usually fails on CR)     │
│  • Handles all URL formats (watch, youtu.be, shorts, embed)          │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Flacfetch VM (GCE)                               │
│                                                                      │
│  • Has YouTube cookies (avoids bot detection)                        │
│  • Has tracker credentials (RED, OPS private trackers)               │
│  • Runs BitTorrent client for seeding                                │
│  • Uploads directly to GCS (no data transfer through Cloud Run)      │
└──────────────────────────────────────────────────────────────────────┘
```

**Key environment variables**:
- `FLACFETCH_API_URL` - URL of flacfetch VM (e.g., `http://10.x.x.x:8080`)
- `FLACFETCH_API_KEY` - API key for authentication

## Worker Execution

Workers run via two mechanisms depending on their processing duration:

### Cloud Run Jobs (Long-Running Workers)

Audio and lyrics workers run as **Cloud Run Jobs** - standalone batch containers that run to completion without HTTP request lifecycle concerns. This eliminates the instance termination issue where Cloud Run would shut down instances mid-processing.

```text
┌─────────────────────────────────────────────────────────────────┐
│  Cloud Run Jobs (via WorkerService)                             │
│                                                                 │
│  audio-download-job          - 30s-5 min (flacfetch/YouTube)     │
│  lyrics-transcription-job    - 5-15 min (AudioShake + correction)│
│  audio-separation-job        - 10-20 min (L4 GPU, us-east4, direct) │
│  video-encoding-job          - up to 60 min (optional)          │
│                                                                 │
│  Triggered via: google.cloud.run_v2.JobsClient.run_job()        │
│  Job ID passed as: --job-id argument                            │
└─────────────────────────────────────────────────────────────────┘
```

**Why Cloud Run Jobs**: When using FastAPI BackgroundTasks, Cloud Run would terminate instances when HTTP requests completed, even if background work was still running. Cloud Run Jobs solve this by running workers as standalone processes that complete naturally. This applies to audio downloads (jobs 51b8231d, 89e497b1 were killed mid-download), lyrics transcription (job c94cc9d6 killed mid-processing), and audio separation.

**CLI Entry Points**: Each worker has a `main()` function for Cloud Run Job execution:
- `python -m backend.workers.audio_download_worker --job-id abc123`
- `python -m backend.workers.lyrics_worker --job-id abc123`
- `python -m backend.workers.audio_worker --job-id abc123`
- `python -m backend.workers.video_worker --job-id abc123`

### HTTP Workers (Fast Workers)

Screens and render workers run via internal HTTP endpoints with BackgroundTasks, as they complete quickly (~30 seconds). They still use the WorkerRegistry for coordination:

```text
┌─────────────────────────────────────────────────────────────────┐
│                       WorkerRegistry                             │
│                                                                 │
│  register(job_id, "screens")   unregister(job_id, "screens")   │
│  register(job_id, "render")    unregister(job_id, "render")    │
│                                                                 │
│  has_active_workers() → bool                                    │
│  wait_for_completion(timeout) → bool                            │
└─────────────────────────────────────────────────────────────────┘
```

**Shutdown Handler** (in `main.py` lifespan):
- On shutdown signal, checks `worker_registry.has_active_workers()`
- If workers active, calls `wait_for_completion(timeout=600)` (10 min max)
- Logs which workers are still running for debugging

## Audio Separation Architecture

Audio stem separation runs **directly inside the audio worker Cloud Run Job** — the `Separator` class from `audio-separator` is imported and called in-process. There is no separate GPU microservice.

```text
┌──────────────────────────────────────────────────────────────────┐
│  audio-separation-job  (Cloud Run Job, L4 GPU, us-east4)         │
│                                                                  │
│  AudioWorker                                                     │
│  ├── download audio from GCS                                     │
│  ├── create_audio_processor()  ← Separator(model_file_dir=...)   │
│  ├── Stage 1: instrumental_clean preset                          │
│  │   └── produces: vocals.flac, instrumental_clean.flac          │
│  ├── Stage 2: karaoke preset                                     │
│  │   └── produces: instrumental_with_backing.flac                │
│  └── upload stems to GCS                                         │
└──────────────────────────────────────────────────────────────────┘
```

**Key decisions:**
- **Direct GPU** — No HTTP hop to a separate service. Jobs can run up to 24h; no timeout concerns.
- **Two-stage ensemble preset** — `instrumental_clean` (stage 1) + `karaoke` (stage 2).
- **Model cache** — Models are downloaded to `model_file_dir` (GCS-backed or local) and reused across executions.

**Rollback**: The old audio-separator Cloud Run Service (HTTP endpoint) is kept for rollback but no longer receives traffic. It will be removed in a future phase.

## Multitenancy

The platform supports white-label B2B portals where business customers get their own branded karaoke generation experience.

### Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│  Tenant Detection Flow                                          │
│                                                                 │
│  Request arrives at vocalstar.nomadkaraoke.com                  │
│         │                                                       │
│         ▼                                                       │
│  Frontend: detectTenantFromUrl()                                │
│  - Extracts subdomain from hostname                             │
│  - Calls GET /api/tenant/config?tenant=vocalstar                │
│         │                                                       │
│         ▼                                                       │
│  Backend: TenantMiddleware                                      │
│  1. X-Tenant-ID header (from frontend)                          │
│  2. Query param (dev only, disabled in production)              │
│  3. Host header subdomain detection                             │
│         │                                                       │
│         ▼                                                       │
│  TenantService.get_tenant_config()                              │
│  - Loads from GCS: tenants/{tenant_id}/config.json              │
│  - Caches in memory (5 min TTL)                                 │
│         │                                                       │
│         ▼                                                       │
│  Request proceeds with tenant context                           │
│  - request.state.tenant_id                                      │
│  - request.state.tenant_config                                  │
└─────────────────────────────────────────────────────────────────┘
```

### GCS Storage Layout

```text
tenants/{tenant_id}/
├── config.json          # TenantConfig with branding, features, defaults
└── logo.jpg             # Tenant logo (optional)

themes/{theme_id}/
├── style_params.json    # Full theme configuration
└── assets/
    ├── intro_background.png
    ├── karaoke_background.jpg
    ├── end_background.png
    ├── font.ttf
    └── cdg_*.gif
```

### Tenant Config Schema

```python
TenantConfig:
  id: str                    # e.g., "vocalstar"
  name: str                  # Display name: "Vocal Star"
  subdomain: str             # e.g., "vocalstar.nomadkaraoke.com"
  is_active: bool            # Enable/disable tenant
  branding:
    logo_url: str | None     # GCS path to logo
    logo_height: int         # Pixels
    primary_color: str       # Hex color
    secondary_color: str
    accent_color: str | None
    background_color: str | None
    favicon_url: str | None
    site_title: str
    tagline: str | None
  features:
    audio_search: bool       # Enable audio search (vs file upload only)
    file_upload: bool
    youtube_url: bool
    youtube_upload: bool     # YouTube distribution
    dropbox_upload: bool
    gdrive_upload: bool
    theme_selection: bool    # Allow user to pick theme
    color_overrides: bool
    enable_cdg: bool
    enable_4k: bool
    admin_access: bool
  defaults:
    theme_id: str | None     # Default theme if not locked
    locked_theme: str | None # Force this theme (user cannot change)
    distribution_mode: str   # "all", "download_only", etc.
  auth:
    allowed_email_domains: list[str]  # e.g., ["vocal-star.com"]
    require_email_domain: bool
    sender_email: str | None  # Override email sender
```

### Feature Enforcement

**Frontend**: The `useTenant()` hook provides `features` object. UI components conditionally render based on enabled features:

```typescript
const { features } = useTenant()
if (!features.audio_search) {
  // Hide audio search tab
}
```

**Backend**: Routes check tenant config from request state:

```python
tenant_config = get_tenant_config_from_request(request)
if tenant_config and not tenant_config.features.audio_search:
    raise HTTPException(403, "Audio search not enabled for this tenant")
```

### Active Tenants

**Vocal Star** (`vocalstar.nomadkaraoke.com`)
- Features: File upload only (no audio search, no YouTube URL), streamlined 4-field form (Artist, Title, Mixed Audio, Instrumental Audio)
- Distribution: Cloud only (Dropbox + Google Drive to tenant-specific folders, no YouTube)
- Theme: Locked to "vocalstar" theme (yellow/blue)
- Auth: Restricted to `@vocal-star.com` and `@vocalstarmusic.com` emails
- All jobs automatically private with VSTAR brand prefix
- Setup: `python scripts/setup-vocalstar-tenant.py`

**Singa** (`singa.nomadkaraoke.com`)
- Features: File upload only (no audio search, no YouTube URL)
- Distribution: Download only
- Theme: Locked to "singa" theme (green/black)
- Auth: Restricted to `@singa.com` emails
- Setup: `python scripts/setup-singa-tenant.py`

### Tenant Portal Frontend (B2B Deployment)

B2B tenants are served from a **separate Cloudflare Pages project** (`karaoke-gen-tenant`) built with `NEXT_PUBLIC_TENANT_PORTAL=true`. This build flag enables tree-shaking of consumer-only content (landing page, pricing) at build time, keeping the bundle lean for white-label deployments.

**Runtime tenant injection**: A Cloudflare Pages edge function intercepts HTML responses and injects tenant-specific configuration before the page reaches the browser:

```javascript
// Injected by edge function into <head>
window.__TENANT_CONFIG__ = { id: "vocalstar", branding: { ... }, features: { ... } }
// CSS variables for dynamic theming
--tenant-primary-color: #ffd700;
--tenant-background-color: #1a1a2e;
```

The frontend's `TenantProvider` reads `window.__TENANT_CONFIG__` on startup (no API round-trip needed for initial render). This approach works for both custom domains (`vocalstar.nomadkaraoke.com`) and the shared tenant portal domain.

**Magic link emails**: When a tenant user requests a magic link, the email uses the tenant-specific URL (e.g., `https://vocalstar.nomadkaraoke.com/auth/verify?token=...`) rather than the main app URL. The `job_notification_service.py` resolves the correct base URL from the tenant config's `subdomain` field.

**Admin preview**: Admins can preview any tenant's branding in the main app by appending `?preview_tenant=<tenant_id>` to any URL. This bypasses subdomain detection and loads the specified tenant config, useful for verifying branding before DNS cutover.

## Tech Stack

- **Backend**: FastAPI, Python 3.12, Cloud Run
- **Frontend**: Next.js, TypeScript, Cloudflare Pages
- **Database**: Firestore
- **Storage**: Google Cloud Storage
- **Secrets**: Google Secret Manager
- **Task Queues**: Cloud Tasks (idle reminders, YouTube upload queue)
- **Scheduling**: Cloud Scheduler (hourly YouTube queue processing)
- **Email**: SendGrid
- **IaC**: Pulumi
- **CI/CD**: GitHub Actions
