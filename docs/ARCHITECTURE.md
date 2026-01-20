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
│  • Audio Worker    - Stem separation via Modal API              │
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
    Uses LocalEncodingService via GCS wheel deployment.
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
   │  Modal API      │    │  AudioShake     │
   │  2-stage sep    │    │  Auto-correct   │
   │  ↓ register()   │    │  ↓ register()   │
   │  ↓ unregister() │    │  ↓ unregister() │
   └────────┬────────┘    └────────┬────────┘
            └──────────┬───────────┘
                       ▼
3. SCREENS GENERATION
   • Title screen (5s video)
   • End screen (5s video)
                       ▼
4. HUMAN REVIEW (AWAITING_REVIEW)
   • React UI for lyrics correction
   • Preview video generation (optionally via GCE for speed)
   • Submit corrections
                       ▼
5. VIDEO RENDERING
   • Merge corrections with original data
   • Generate karaoke video with lyrics
                       ▼
6. INSTRUMENTAL SELECTION
   • User chooses: clean or with_backing
                       ▼
7. FINAL VIDEO
   • Remux with selected instrumental
   • Concatenate: title + karaoke + end
   • Encode to 4 formats
                       ▼
8. COMPLETE
   • User downloads via signed URLs
```

## Job States

| State | Description | Human Action |
|-------|-------------|--------------|
| `pending` | Job created | - |
| `downloading` | Processing audio | - |
| `separating_stage1` | Audio separation stage 1 | - |
| `separating_stage2` | Audio separation stage 2 | - |
| `transcribing` | Lyrics transcription | - |
| `generating_screens` | Creating title/end screens | - |
| `awaiting_review` | **Waiting for human** | Review lyrics |
| `in_review` | Human actively reviewing | Edit lyrics |
| `review_complete` | Review submitted | - |
| `rendering_video` | Generating karaoke video | - |
| `awaiting_instrumental_selection` | **Waiting for human** | Choose audio |
| `instrumental_selected` | Selection made | - |
| `generating_video` | Final encoding | - |
| `complete` | All done | Download files |
| `failed` | Error occurred | - |

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
│   └── karaoke.ass
├── screens/
│   ├── title.mov
│   └── end.mov
├── videos/
│   └── with_vocals.mkv            # Karaoke video
└── finals/
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
| Modal | GPU audio separation | Yes |
| AudioShake | Lyrics transcription | Yes |
| Vertex AI | Agentic AI correction (Gemini 3 Flash) | Default on |
| Genius | Reference lyrics | Yes |
| YouTube API | Video upload | Optional |
| SendGrid | Email notifications | Optional |
| Cloud Tasks | Delayed task scheduling (idle reminders) | Optional |

## Firestore Collections

karaoke-gen shares a GCP project (`nomadkaraoke`) with karaoke-decide, but uses separate Firestore collections:

| Collection | Purpose | Key Fields |
|------------|---------|------------|
| `gen_users` | karaoke-gen user accounts | email, credits, role, is_active |
| `jobs` | Karaoke generation jobs | job_id, user_email, status, state_data |
| `jobs/{job_id}/logs` | Worker log entries (subcollection) | timestamp, level, worker, message, ttl_expiry |
| `sessions` | Magic link auth sessions | user_email, token, expires_at |
| `magic_links` | Passwordless auth tokens | email, token, expires_at, used |
| `beta_feedback` | Beta program feedback | user_email, ratings, comments |

**Note**: The `users` collection in the same Firestore instance belongs to karaoke-decide (different schema: user_id, is_guest, quiz_* fields). Don't use it for karaoke-gen.

**Worker Logs**: Stored in subcollection (`jobs/{job_id}/logs`) instead of embedded array to avoid Firestore's 1MB document size limit. TTL policy auto-deletes logs after 30 days. Feature flag: `USE_LOG_SUBCOLLECTION` (default: true). See [LESSONS-LEARNED.md](LESSONS-LEARNED.md#firestore-document-1mb-limit-with-embedded-arrays).

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
| `youtube_upload_service.py` | YouTube OAuth flow and upload |
| `discord_service.py` | Discord webhook notifications |
| `packaging_service.py` | CDG/TXT package generation |
| `local_encoding_service.py` | FFmpeg encoding (Cloud Run) |
| `encoding_interface.py` | Abstract interface + GCE backend |
| `video_worker_orchestrator.py` | Stage coordination |
| `email_service.py` | SendGrid email delivery with CC support |
| `template_service.py` | GCS-backed email templates |
| `job_notification_service.py` | Email orchestration (completion, reminders) |

## Worker Coordination

Workers run in parallel via FastAPI BackgroundTasks. To prevent Cloud Run from terminating the container when one worker completes while another is still running, workers register with a global `WorkerRegistry`.

```text
┌─────────────────────────────────────────────────────────────────┐
│                       WorkerRegistry                             │
│                                                                 │
│  register(job_id, "audio")     unregister(job_id, "audio")     │
│  register(job_id, "lyrics")    unregister(job_id, "lyrics")    │
│                                                                 │
│  has_active_workers() → bool                                    │
│  wait_for_completion(timeout) → bool                            │
└─────────────────────────────────────────────────────────────────┘
```

**Flow**:
1. Worker starts → `await worker_registry.register(job_id, "audio")`
2. Worker does work in try block
3. Worker completes → `await worker_registry.unregister(job_id, "audio")` in finally block

**Shutdown Handler** (in `main.py` lifespan):
- On shutdown signal, checks `worker_registry.has_active_workers()`
- If workers active, calls `wait_for_completion(timeout=600)` (10 min max)
- Logs which workers are still running for debugging

**Why needed**: Without this, when the audio worker's BackgroundTask completes, Cloud Run sees the container as idle (no active requests) and may terminate it, killing the still-running lyrics worker.

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

### First Tenant: Vocal Star

- Subdomain: `vocalstar.nomadkaraoke.com`
- Features: File upload only (no audio search, no YouTube URL)
- Distribution: Download only (no YouTube/Dropbox/GDrive)
- Theme: Locked to "vocalstar" theme (yellow/blue)
- Auth: Restricted to `@vocal-star.com` and `@vocalstarmusic.com` emails
- Setup: `python scripts/setup-vocalstar-tenant.py`

## Tech Stack

- **Backend**: FastAPI, Python 3.12, Cloud Run
- **Frontend**: Next.js, TypeScript, Cloudflare Pages
- **Database**: Firestore
- **Storage**: Google Cloud Storage
- **Secrets**: Google Secret Manager
- **Task Queues**: Cloud Tasks (idle reminders)
- **Email**: SendGrid
- **IaC**: Pulumi
- **CI/CD**: GitHub Actions
