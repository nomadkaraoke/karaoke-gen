# API Reference

Base URL: `https://api.nomadkaraoke.com` (production) or `http://localhost:8000` (local)

## Authentication

All endpoints except `/health` and `/api/themes` require authentication.

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" ...
```

## Endpoints

### Health

```http
GET /health
```

No auth required. Returns service status.

#### Encoding Worker Health

```http
GET /health/encoding-worker
```

No auth required. Returns GCE encoding worker status for frontend display.

Response:
```json
{
  "available": true,
  "status": "ok",
  "version": "0.95.1",
  "active_jobs": 0,
  "queue_length": 0
}
```

Status values:
- `ok` - Worker is healthy and available
- `offline` - Worker is unavailable (includes `error` field)
- `not_configured` - Encoding worker not configured on backend

#### Flacfetch Service Health

```http
GET /health/flacfetch
```

No auth required. Returns flacfetch service status for frontend display.

Response:
```json
{
  "available": true,
  "status": "ok",
  "version": "1.2.3"
}
```

Status values:
- `ok` - Flacfetch service is healthy and available
- `offline` - Service is unavailable (includes `error` field)
- `not_configured` - Flacfetch service not configured on backend

### Jobs

#### Create Job with Upload

```http
POST /api/jobs/upload
Content-Type: multipart/form-data

file: <audio file>
artist: "Artist Name"
title: "Song Title"
is_private: false  # Optional. If true, Dropbox uses Tracks-NonPublished/NOMADNP, no YouTube/GDrive
```

Returns job ID. Triggers async processing. Max request body ~32MB (Cloud Run limit). For larger files, use the signed URL upload flow below.

#### Create Job with Signed URL Upload (large files)

Three-step flow that bypasses Cloud Run's 32MB body limit. Used automatically by the frontend for files ≥25MB.

**Step 1: Create job and get signed URLs**
```http
POST /api/jobs/create-with-upload-urls
Content-Type: application/json

{
  "artist": "Artist Name",
  "title": "Song Title",
  "files": [{"filename": "song.wav", "content_type": "audio/wav", "file_type": "audio"}],
  "is_private": false
}
```

Returns `job_id` and `upload_urls` list. Each entry has `file_type`, `upload_url` (signed GCS URL), and `content_type`.

**Step 2: Upload file directly to GCS**
```http
PUT <upload_url>
Content-Type: audio/wav

<file bytes>
```

Direct PUT to GCS signed URL — does not go through the backend.

**Step 3: Notify backend**
```http
POST /api/jobs/{job_id}/uploads-complete
Content-Type: application/json

{"uploaded_files": ["audio"]}
```

Triggers async processing.

#### Get Job Status

```http
GET /api/jobs/{job_id}
```

Response includes:
- `status` - Current job state
- `file_urls` - Signed URLs for outputs (when ready)
- `state_data` - Worker progress details
- `timeline` - Processing history

#### List Jobs

```http
GET /api/jobs
GET /api/jobs?status=complete
GET /api/jobs?limit=10&offset=0
GET /api/jobs?exclude_test=false
GET /api/jobs?fields=summary&hide_completed=true
GET /api/jobs?fields=summary&search=bohemian
```

Query parameters:
- `exclude_test` (bool, default: true) - Admin only: filter out jobs from test users
- `status` - Filter by job status
- `limit` / `offset` - Pagination
- `fields` (string, optional) - Set to `summary` to return only dashboard-required fields using Firestore field projection. Reduces payload from ~16MB to <500KB for large job lists.
- `hide_completed` (bool, default: false) - Exclude successful completions (complete, prep_complete) server-side. Failed jobs remain visible.
- `search` (string, optional) - Text search filter (summary mode only). Case-insensitive substring match against artist, title, audio_search_artist, audio_search_title, and job_id. When active, fetches up to 1000 results internally and filters in Python.

#### Delete Job

```http
DELETE /api/jobs/{job_id}
```

#### Change Visibility

Toggle a completed job between public and private. Only the job owner or admin can use this.

```http
POST /api/jobs/{job_id}/change-visibility
Content-Type: application/json

{
  "target_visibility": "private"  // or "public"
}
```

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Job changed to private. Outputs redistributed to private destination.",
  "previous_visibility": "public",
  "new_visibility": "private",
  "reprocessing_required": false
}
```

**Two flows depending on direction:**
- **Public → Private**: Fast (~1-2 min). Deletes distributed outputs (YouTube/Dropbox/GDrive), keeps GCS finals, redistributes to private destination.
- **Private → Public**: Slow (~15-30 min). Clears custom styles, resets to Nomad theme, regenerates screens, re-renders and re-encodes video.

**Validation:** Job must be `complete`, not a tenant job, not already at target visibility, and no concurrent visibility change in progress.

### Review

The combined review flow allows users to review lyrics AND select instrumental track in a single session.

#### Get Correction Data

```http
GET /api/review/{job_id}/correction-data
```

Returns lyrics correction data plus instrumental options for the review UI.

Response includes:
- `correction_data` - Lyrics and segments for editing
- `instrumental_options` - Available instrumental tracks (`clean`, `with_backing`)
- `backing_vocals_analysis` - Analysis data to help with selection

#### Complete Review

```http
POST /api/review/{job_id}/complete
Content-Type: application/json

{
  "corrections": [...],
  "corrected_segments": [...],
  "instrumental_selection": "clean"  // Required: "clean" or "with_backing"
}
```

Saves corrections, stores instrumental selection, and triggers video rendering. The instrumental selection is now required as part of the combined review flow.

#### Generate Preview

```http
POST /api/review/{job_id}/preview-video
Content-Type: application/json

{
  "corrections": [...],
  "corrected_segments": [...],
  "use_background_image": false  // optional, default: false
}
```

Generates preview video during review. The `use_background_image` option controls whether the preview renders with the theme's background image (slower, ~30-60s) or a solid black background (faster, ~10s). Defaults to black background for speed.

#### Stream Audio

```http
GET /api/review/{job_id}/audio/{stem_type}
```

Redirects to a signed GCS URL for review playback. Audio is served as OGG Opus (~3 MB) transcoded from the original FLAC (~35 MB). Transcoding happens eagerly during screen generation; if the transcoded file is missing, falls back to the original FLAC.

#### Create Custom Instrumental

```http
POST /api/jobs/{job_id}/create-custom-instrumental
Content-Type: application/json

{
  "mute_regions": [
    {"start_seconds": 17.0, "end_seconds": 42.0},
    {"start_seconds": 53.8, "end_seconds": 71.4}
  ]
}
```

Creates a custom instrumental by muting selected regions of backing vocals, combining with the clean instrumental, and uploading to GCS. Returns a signed audio URL for immediate playback.

Response:
```json
{
  "status": "success",
  "message": "Custom instrumental created with 2 mute regions",
  "audio_url": "https://storage.googleapis.com/...",
  "muted_duration_seconds": 42.6
}
```

#### Upload Custom Instrumental

```http
POST /api/jobs/{job_id}/upload-instrumental
Content-Type: multipart/form-data

file: <audio file (mp3, wav, flac, ogg, aac, m4a)>
```

Uploads an external instrumental audio file for use during review. The uploaded file's duration is validated against the original audio — must match within ±0.5 seconds. Requires job to be in `awaiting_review` or `in_review` state.

Response:
```json
{
  "status": "success",
  "duration_seconds": 240.0,
  "message": "Custom instrumental uploaded (240.0s)"
}
```

Error (duration mismatch):
```json
{
  "detail": "Duration mismatch: uploaded file is 235.0s but original audio is 240.0s. The instrumental must be exactly 240.0s (±0.5s)."
}
```

#### Edit Completed Track

```http
POST /api/jobs/{job_id}/edit
Content-Type: application/json

{
  "artist": "Updated Artist",  // optional, only if changing
  "title": "Updated Title"     // optional, only if changing
}
```

Reopens a completed track for editing. Cleans up all distributed outputs (YouTube, Dropbox, GDrive, GCS finals), recycles the brand code, and resets the job to `awaiting_review`. A new review token is issued automatically. No additional credits consumed.

If `artist` or `title` are provided and differ from current values, title/end screens are deleted and the screens worker is triggered to regenerate them in the background.

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Track reopened for editing. Previous outputs have been removed.",
  "review_url": "/app/jobs#/abc123/review",
  "review_token": "...",
  "metadata_updated": true,
  "cleanup_results": {
    "youtube": {"status": "success", "video_id": "xyz"},
    "dropbox": {"status": "success", "path": "/Karaoke/..."},
    "gdrive": {"status": "success"},
    "gcs_finals": {"status": "success", "deleted_count": 4},
    "brand_code": {"status": "recycled", "code": "NOMAD-1234"}
  }
}
```

**Requirements:** Job must be in `complete` state with `outputs_deleted_at` not set. User must own the job or be admin.

#### Get Style Upload URLs

```http
POST /api/jobs/{job_id}/style-upload-urls
Content-Type: application/json
Authorization: Bearer <token>

{
  "files": [
    {
      "filename": "bg.png",
      "content_type": "image/png",
      "file_type": "style_karaoke_background"
    }
  ]
}
```

Returns signed GCS upload URLs for custom style assets (backgrounds). Only available for private jobs before the `GENERATING_SCREENS` pipeline stage. Valid `file_type` values: `style_intro_background`, `style_karaoke_background`, `style_end_background`. Only PNG and JPG images are accepted.

Response:
```json
{
  "status": "success",
  "job_id": "job-abc",
  "upload_urls": [
    {
      "file_type": "style_karaoke_background",
      "gcs_path": "uploads/job-abc/style/karaoke_background.png",
      "upload_url": "https://storage.googleapis.com/...",
      "content_type": "image/png"
    }
  ]
}
```

#### Complete Style Uploads

```http
POST /api/jobs/{job_id}/style-uploads-complete
Content-Type: application/json
Authorization: Bearer <token>

{
  "uploaded_files": ["style_karaoke_background"],
  "color_overrides": {
    "artist_color": "#ff0000",
    "title_color": "#00ff00"
  }
}
```

Finalizes style asset uploads. Verifies files exist in GCS, merges into `job.style_assets`, reloads the theme's `style_params.json` with color overrides and custom background paths. The `color_overrides` field is optional — omit it to keep default theme colors.

Response:
```json
{
  "status": "success",
  "job_id": "job-abc",
  "message": "Style assets applied successfully.",
  "assets_updated": ["karaoke_background"]
}
```

#### Submit Edit Log

```http
POST /api/jobs/{job_id}/edit-log
Content-Type: application/json

{
  "session_id": "abc123",
  "job_id": "test-job",
  "audio_hash": "hash",
  "started_at": "2026-03-01T00:00:00.000Z",
  "entries": [
    {
      "id": "entry-1",
      "timestamp": "2026-03-01T00:01:00.000Z",
      "operation": "word_change",
      "segment_id": "seg-0",
      "segment_index": 0,
      "word_ids_before": ["w1"],
      "word_ids_after": ["w1"],
      "text_before": "helo",
      "text_after": "hello",
      "feedback": { "reason": "misheard_word", "timestamp": "..." }
    }
  ]
}
```

Stores a session edit log to GCS at `jobs/{job_id}/lyrics/edit_log_{session_id}.json`. Tracks all user edits and optional feedback reasons for transcription improvement. Called automatically on review submission.

#### Submit Annotations

```http
POST /api/review/{job_id}/v1/annotations
Content-Type: application/json

{
  "annotations": [
    { "annotation_type": "SOUND_ALIKE", "original_text": "helo" }
  ]
}
```

Stores annotations to `jobs/{job_id}/lyrics/annotations.json`. Merges with existing annotations if present.

#### Add Reference Lyrics (Paste)

```http
POST /api/review/{job_id}/add-lyrics
Content-Type: application/json

{
  "source": "manual",
  "lyrics": "Full lyrics text...",
  "force": false
}
```

Adds user-pasted lyrics as a new reference source, re-runs the correction pipeline with all sources, and uploads updated corrections to GCS. The `force` flag (default `false`) bypasses the relevance threshold — use when the transcription is poor and a low match % doesn't mean wrong song.

Returns `{ "status": "success", "data": <CorrectionData> }`.

#### Search Reference Lyrics

```http
POST /api/review/{job_id}/search-lyrics
Content-Type: application/json

{
  "artist": "Billie Eilish",
  "title": "What Was I Made For",
  "force_sources": []
}
```

Searches all configured lyrics providers (Genius, Spotify, Musixmatch, LRCLIB) with the given artist/title. Results are filtered through the relevance threshold (30% word match minimum) — sources below are rejected as likely wrong-song matches. Sources in `force_sources` bypass the threshold.

**Success response** (at least one source passed):
```json
{
  "status": "success",
  "data": { "...CorrectionData..." },
  "sources_added": ["genius", "lrclib"],
  "sources_rejected": {
    "spotify": { "relevance": 0.12, "matched_words": 18, "total_words": 150, "track_name": "...", "artist_names": "..." }
  },
  "sources_not_found": ["musixmatch"]
}
```

**No results response** (all filtered or nothing found):
```json
{
  "status": "no_results",
  "message": "No matching lyrics found from any provider",
  "sources_rejected": { "genius": { "relevance": 0.05, "..." } },
  "sources_not_found": ["lrclib", "musixmatch"]
}
```

#### Review Sessions

Server-side backup/restore of lyrics review editing sessions. Correction data is stored in GCS; metadata in Firestore subcollection `jobs/{job_id}/review_sessions/{session_id}`.

##### Save Review Session

```http
POST /api/review/{job_id}/sessions
Content-Type: application/json

{
  "correction_data": { "corrected_segments": [...], "original_segments": [...] },
  "edit_count": 5,
  "trigger": "auto",  // "auto" | "preview" | "manual"
  "summary": {
    "total_segments": 10,
    "total_words": 100,
    "corrections_made": 5,
    "changed_words": [{"original": "hello", "corrected": "hallo", "segment_index": 0}]
  }
}
```

Deduplicates via SHA-256 hash of correction_data. Returns `{"status": "skipped", "reason": "identical_data"}` if data hasn't changed since last save.

Auth: Review token (same as correction data access).

##### List Review Sessions

```http
GET /api/review/{job_id}/sessions
```

Returns session metadata (no correction_data). Ordered by most recent first.

##### Get Review Session

```http
GET /api/review/{job_id}/sessions/{session_id}
```

Returns full session including correction_data downloaded from GCS.

##### Delete Review Session

```http
DELETE /api/review/{job_id}/sessions/{session_id}
```

Deletes both GCS data and Firestore metadata.

##### Search Review Sessions (Cross-Job)

```http
GET /api/review/sessions/search?q=artist+name&limit=20
```

Searches across all jobs. Requires admin auth. Matches against artist, title, and job_id fields.

### Instrumental Selection (Finalise-Only Jobs)

For finalise-only jobs (where audio prep was done externally), instrumental selection is handled separately:

```http
POST /api/jobs/{job_id}/select-instrumental
Content-Type: application/json

{
  "selection": "clean"  // or "with_backing"
}
```

**Note**: For normal jobs, instrumental selection is now part of the combined review flow (see `/api/review/{job_id}/complete` above).

### Catalog (Song Lookup)

Proxies to karaoke-decide's MusicBrainz + Spotify catalog for autocomplete, and scrapes karaokenerds.com for community karaoke version detection. All endpoints require authentication and are rate-limited (20 requests/minute per user). Results are cached in-memory (5 min for catalog, 1 hour for community checks).

#### Search Artists

```http
GET /api/catalog/artists?q=queen&limit=10
```

Response:
```json
[
  {
    "name": "Queen",
    "mbid": "0383dadf-...",
    "disambiguation": null,
    "artist_type": "Group",
    "spotify_id": "1dfeR4HaWDbWqFHLkxsg1d",
    "popularity": 85,
    "genres": ["rock"],
    "tags": ["classic rock"]
  }
]
```

#### Search Tracks

```http
GET /api/catalog/tracks?q=bohemian&artist=queen&limit=10
```

Response:
```json
[
  {
    "track_name": "Bohemian Rhapsody - Remastered 2011",
    "artist_name": "Queen",
    "track_id": "4u7EnebtmKWzUH433cf5Qv",
    "artist_id": "1dfeR4HaWDbWqFHLkxsg1d",
    "popularity": 81,
    "duration_ms": 354320,
    "explicit": false
  }
]
```

The `artist` query parameter is optional but recommended — filters results by artist for more relevant suggestions.

#### Check Community Versions

```http
POST /api/catalog/community-check
Content-Type: application/json

{
  "artist": "Queen",
  "title": "Bohemian Rhapsody"
}
```

Response:
```json
{
  "has_community": true,
  "songs": [
    {
      "title": "Bohemian Rhapsody",
      "artist": "Queen",
      "community_tracks": [
        {
          "brand_name": "ObsKure Karaoke",
          "brand_code": "OBSK",
          "youtube_url": "https://www.youtube.com/watch?v=...",
          "is_community": true
        }
      ]
    }
  ],
  "best_youtube_url": "https://www.youtube.com/watch?v=..."
}
```

When `has_community` is true, the frontend shows a dismissible green banner suggesting the user check existing versions before creating a new one.

### Audio Search

#### Search for Audio

```http
POST /api/audio-search/search
Content-Type: application/json

{
  "artist": "Artist Name",
  "title": "Song Title",
  "auto_download": false,
  "theme_id": "nomad",
  "display_artist": "Display Artist (optional)",
  "display_title": "Display Title (optional)"
}
```

Searches for audio sources (flacfetch integration). Creates a job and returns search results.

**Key fields:**
- `artist`, `title` - Used to search for audio on trackers
- `display_artist`, `display_title` - Optional overrides for how artist/title appear on title screens and filenames. If omitted, search values are used.
- `auto_download` - If true, automatically selects best result and starts processing
- `theme_id` - Video theme to use

**Use case:** Search for "Jeremy Kushnier" but display "Footloose (Broadway Cast)" on the karaoke video.

#### Get Search Results

```http
GET /api/audio-search/{job_id}/results
```

Returns cached search results for a job awaiting audio selection.

#### Select Audio Source

```http
POST /api/audio-search/{job_id}/select
Content-Type: application/json

{
  "selection_index": 0
}
```

Selects an audio source from the search results and starts processing.

#### Standalone Search (Guided Flow — Step 2)

```http
POST /api/audio-search/search-standalone
Content-Type: application/json

{
  "artist": "Artist Name",
  "title": "Song Title"
}
```

Searches for audio **without creating a job**. Returns a search session (7-day TTL).
Credits are checked here but **not deducted** — deduction happens at job creation.

**Response:**
```json
{
  "search_session_id": "uuid",
  "results": [...],
  "results_count": 3
}
```

No results returns `results: []` (not a 404). Use `search_session_id` with the create-from-search endpoint below.

#### Create Job from Search Session (Guided Flow — Step 3)

```http
POST /api/jobs/create-from-search
Content-Type: application/json

{
  "search_session_id": "uuid",
  "selection_index": 0,
  "artist": "ABBA",
  "title": "Waterloo",
  "display_artist": "ABBA (Display)",
  "display_title": "Waterloo (Karaoke)",
  "is_private": false
}
```

Creates the job from a previously completed standalone search session. All final field values
(is_private, display overrides) are set at creation time — no patching needed afterward.
The job goes directly to `DOWNLOADING_AUDIO` (skips `AWAITING_AUDIO_SELECTION`).
The session is consumed (deleted) on success.

Returns 404 with "Search expired — please search again" if the session has expired.

### Themes

```http
GET /api/themes
```

No auth required. Returns available video themes.

### Tenant Config

```http
GET /api/tenant/config
GET /api/tenant/config?tenant=vocalstar
```

No auth required. Returns tenant configuration for white-label portals.

**Detection priority**:
1. `X-Tenant-ID` header (explicitly set by frontend)
2. `tenant` query parameter (development/testing only, disabled in production)
3. Host header subdomain detection (e.g., `vocalstar.nomadkaraoke.com`)

Response:
```json
{
  "tenant": {
    "id": "vocalstar",
    "name": "Vocal Star",
    "subdomain": "vocalstar.nomadkaraoke.com",
    "is_active": true,
    "branding": {
      "logo_url": "https://storage.googleapis.com/...",
      "logo_height": 60,
      "primary_color": "#ffff00",
      "secondary_color": "#006CF9",
      "accent_color": "#ffffff",
      "background_color": "#000000",
      "favicon_url": null,
      "site_title": "Vocal Star Karaoke Generator",
      "tagline": "Whoever You Are, Be a Vocal Star!"
    },
    "features": {
      "audio_search": false,
      "file_upload": true,
      "youtube_url": false,
      "youtube_upload": false,
      "dropbox_upload": false,
      "gdrive_upload": false,
      "theme_selection": false,
      "color_overrides": false,
      "enable_cdg": true,
      "enable_4k": true,
      "admin_access": false
    },
    "defaults": {
      "theme_id": "vocalstar",
      "locked_theme": "vocalstar",
      "distribution_mode": "download_only"
    }
  },
  "is_default": false
}
```

When no tenant is detected, returns `tenant: null` with `is_default: true` and the default Nomad Karaoke configuration is used.

```http
GET /api/tenant/config/{tenant_id}
```

Get specific tenant config by ID. Returns 404 if tenant not found or inactive.

#### Tenant Asset Proxy

```http
GET /api/tenant/asset/{tenant_id}/{filename}
```

Serves tenant assets (logos, etc.) from GCS. No authentication required. Returns the file with appropriate content-type headers.

- `tenant_id`: Tenant identifier (e.g., `singa`, `vocalstar`)
- `filename`: Asset filename (e.g., `logo.png`)
- Returns 404 if asset not found

Used by the frontend `TenantLogo` component to load tenant logos. Tenant configs store logo paths as `https://api.nomadkaraoke.com/api/tenant/asset/{tenant_id}/logo.png` (the frontend also converts legacy `gs://` paths to this format as a fallback).

### Internal (Admin Only)

These endpoints are used by workers and require admin tokens.

```http
POST /api/internal/jobs/{job_id}/trigger-audio
POST /api/internal/jobs/{job_id}/trigger-lyrics
POST /api/internal/jobs/{job_id}/trigger-screens
POST /api/internal/jobs/{job_id}/trigger-render-video
POST /api/internal/jobs/{job_id}/trigger-video
```

## Job States

| State | Description |
|-------|-------------|
| `pending` | Created, not started |
| `downloading` | Processing input |
| `separating_stage1` | Audio separation (1/2) |
| `separating_stage2` | Audio separation (2/2) |
| `transcribing` | Lyrics transcription |
| `generating_screens` | Title/end screens + backing vocals analysis |
| `awaiting_review` | Waiting for combined human review (lyrics + instrumental) |
| `in_review` | Human reviewing |
| `review_complete` | Review submitted with instrumental selection |
| `rendering_video` | Generating karaoke video |
| `instrumental_selected` | Instrumental selection confirmed, ready for video |
| `generating_video` | Final encoding |
| `complete` | Done |
| `failed` | Error |

**Note**: `awaiting_instrumental_selection` exists for backwards compatibility with historical jobs but is no longer used for new jobs. Instrumental selection is now part of the combined review flow.

## Error Responses

```json
{
  "detail": "Error message"
}
```

Common status codes:
- `400` - Bad request
- `401` - Unauthorized
- `402` - Insufficient credits (see [Credit Enforcement](#credit-enforcement))
- `404` - Job not found
- `429` - Rate limit exceeded
- `500` - Server error

## User Authentication

### Send Magic Link

```http
POST /api/users/auth/magic-link
Content-Type: application/json

{"email": "user@example.com", "device_fingerprint": "optional-fp-string"}
```

The `device_fingerprint` field is optional. If provided, it's used alongside the client IP for signup rate limiting (max 2 new accounts per IP or fingerprint per 24 hours). Existing users are never rate limited. Rate-limited requests receive a silent 200 response (anti-enumeration).

### Verify Magic Link

```http
GET /api/users/auth/verify?token=TOKEN
```

Returns session token, user info, and `credits_granted` (number of welcome credits granted on this verification, 0 for returning users). Welcome credits are granted on first verification, not at account creation.

**Admin Login Token**: The same endpoint supports admin login tokens embedded in notification emails. When a made-for-you order is received, the admin notification email includes a link with `?admin_token=TOKEN` that auto-logs the admin into the app. The frontend detects this parameter and calls the verify endpoint to authenticate. Admin tokens expire after 24 hours.

### Get Current User

```http
GET /api/users/me
Authorization: Bearer SESSION_TOKEN
```

Response includes `feedback_eligible: bool` indicating whether the user can earn credits by submitting feedback (requires 2+ completed jobs and no prior feedback submission).

### Logout

```http
POST /api/users/auth/logout
Authorization: Bearer SESSION_TOKEN
```

## Credits & Payments (PR #90)

### List Credit Packages

```http
GET /api/users/credits/packages
```

No auth required. Returns available packages and prices.

### Create Checkout Session (Credits)

```http
POST /api/users/credits/checkout
Content-Type: application/json

{"package_id": "5_credits", "email": "user@example.com"}
```

Returns Stripe checkout URL. Supports Apple Pay, Google Pay, Link, and other payment methods based on customer device.

### Create Checkout Session (Made For You)

```http
POST /api/users/made-for-you/checkout
Content-Type: application/json

{
  "email": "customer@example.com",
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "source_type": "search",
  "youtube_url": null,
  "notes": "Optional special requests"
}
```

Creates a $15 checkout for the full-service "Made For You" karaoke video. Supports all enabled payment methods.

**source_type**: `search` (find audio automatically), `youtube` (use provided URL)

#### Made-For-You Order Flow

After payment completes via Stripe webhook:

1. **Job Creation**: Job is created with `made_for_you=true`, owned by admin during processing
2. **Audio Search**: System searches for audio sources automatically
3. **Admin Notification**: Admin receives email with link to select audio source
4. **Customer Confirmation**: Customer receives order confirmation email
5. **Pause at Audio Selection**: Job enters `AWAITING_AUDIO_SELECTION` state
6. **Admin Selects Audio**: Admin reviews and selects audio source in admin UI
7. **Processing**: Job processes through normal pipeline (admin handles any intermediate steps)
8. **Completion**: On completion, ownership transfers to customer (`user_email` = `customer_email`)
9. **Delivery Email**: Customer receives completion email with download links

**Key fields on job document:**
- `made_for_you: bool` - Flag for made-for-you orders (default: false)
- `customer_email: str` - Customer's email for final delivery
- `customer_notes: str` - Optional notes from customer

**Email suppression**: Intermediate reminder emails (lyrics review, instrumental selection) are suppressed for made-for-you jobs since admin handles these directly. Only order confirmation and final delivery emails go to customer

### Credit Enforcement

Credits are checked and deducted at job creation time. The flow:

1. **Check** - `user_service.has_credits()` verifies the user has >= 1 credit
2. **Create job** - Job is persisted to Firestore
3. **Deduct** - `user_service.deduct_credit()` atomically deducts 1 credit (with job_id for audit trail)
4. **Refund on failure** - If the job fails, 1 credit is automatically refunded

Admin users bypass credit checks entirely. New users receive 1 welcome credit. Users can earn 1 additional free credit by submitting product feedback after completing 2+ jobs (see [User Feedback for Credits](#user-feedback-for-credits)).

#### 402 Response

When a user has no credits, job creation returns HTTP 402:

```json
{
  "detail": "You're out of credits. Buy more to continue creating karaoke videos.",
  "credits_available": 0,
  "credits_required": 1,
  "buy_url": "/#pricing"
}
```

### Stripe Webhook

```http
POST /api/users/webhooks/stripe
```

Handles `checkout.session.completed` events.

### User Feedback for Credits

Users who have completed 2+ jobs can submit feedback to earn 1 free credit.

#### Check Eligibility

```http
GET /api/users/feedback/eligibility
Authorization: Bearer SESSION_TOKEN
```

Response:
```json
{
  "eligible": true,
  "has_submitted": false,
  "jobs_completed": 3,
  "credits_reward": 1
}
```

#### Submit Feedback

```http
POST /api/users/feedback
Authorization: Bearer SESSION_TOKEN
Content-Type: application/json

{
  "overall_rating": 4,
  "ease_of_use_rating": 5,
  "lyrics_accuracy_rating": 4,
  "correction_experience_rating": 3,
  "what_went_well": "The lyrics sync was really accurate...",
  "what_could_improve": "Would love more theme options...",
  "additional_comments": "",
  "would_recommend": true,
  "would_use_again": true
}
```

Requirements:
- User must have completed 2+ jobs
- At least one text field must have >50 characters
- User can only submit once (duplicate prevention)

Grants 1 credit on success. Feedback stored in `user_feedback` Firestore collection.

The `/api/users/me` response includes `feedback_eligible: bool` so the frontend can show/hide feedback prompts without an extra API call.

## Admin Endpoints

All admin endpoints require an admin-role session token.

### Test Data Filtering

Most admin list/stats endpoints support an `exclude_test` query parameter (default: `true`) to filter out E2E test data:
- Test users: email addresses ending in `@inbox.testmail.app`
- Test jobs: jobs created by test users

```http
GET /api/admin/stats/overview?exclude_test=true   # Default - hide test data
GET /api/admin/stats/overview?exclude_test=false  # Show all data including tests
```

The frontend admin dashboard includes a "Show test data" toggle that controls this filter globally.

### Admin Dashboard Stats

```http
GET /api/admin/stats/overview
GET /api/admin/stats/overview?exclude_test=false
Authorization: Bearer ADMIN_TOKEN
```

Returns platform statistics:
```json
{
  "total_users": 100,
  "active_users_7d": 25,
  "active_users_30d": 60,
  "total_jobs": 500,
  "jobs_last_7d": 50,
  "jobs_last_30d": 150,
  "jobs_by_status": {
    "pending": 5,
    "processing": 3,
    "awaiting_review": 10,
    "complete": 400,
    "failed": 20
  },
  "total_credits_issued_30d": 200
}
```

### List Users (Admin)

```http
GET /api/users/admin/users
GET /api/users/admin/users?search=user@example.com
GET /api/users/admin/users?limit=20&offset=0&sort_by=created_at&sort_order=desc
GET /api/users/admin/users?exclude_test=false
Authorization: Bearer ADMIN_TOKEN
```

Query parameters:
- `exclude_test` (bool, default: true) - Filter out test users
- `limit` / `offset` - Pagination
- `search` - Email prefix search
- `sort_by` / `sort_order` - Sorting

Returns paginated user list with total count.

### User Detail (Admin)

```http
GET /api/users/admin/users/{email}/detail
Authorization: Bearer ADMIN_TOKEN
```

Returns full user profile including:
- User info and stats
- Recent credit transactions (last 20)
- Recent jobs (last 10)
- Active sessions count

### Add Credits (Admin)

```http
POST /api/users/admin/credits
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "email": "user@example.com",
  "amount": 5,
  "reason": "Beta reward"
}
```

### Impersonate User (Admin)

Allows admins to view the application as any user by creating a session token for that user.

```http
POST /api/admin/users/{email}/impersonate
Authorization: Bearer ADMIN_TOKEN
```

Response:
```json
{
  "session_token": "impersonation-session-token",
  "user_email": "user@example.com",
  "message": "Now impersonating user@example.com"
}
```

Notes:
- Creates a real session for the target user (auditable in Firestore)
- Admin cannot impersonate themselves
- Returns 404 if user doesn't exist
- The frontend stores the original admin token in memory (not localStorage) for security

### Set User Role (Admin)

```http
POST /api/users/admin/users/{email}/role
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{"role": "admin"}
```

Note: This endpoint exists but is not exposed in the admin UI. Use database operations for role changes.

### Enable/Disable User (Admin)

```http
POST /api/users/admin/users/{email}/enable
POST /api/users/admin/users/{email}/disable
Authorization: Bearer ADMIN_TOKEN
```

### Delete Job (Admin)

```http
DELETE /api/jobs/{job_id}?delete_files=true
Authorization: Bearer ADMIN_TOKEN
```

Admins can delete any job. Regular users can only delete their own jobs.

### Audio Search Management (Admin)

#### List Audio Searches

```http
GET /api/admin/audio-searches
GET /api/admin/audio-searches?limit=50&status_filter=awaiting_audio_selection
GET /api/admin/audio-searches?exclude_test=false
Authorization: Bearer ADMIN_TOKEN
```

Query parameters:
- `exclude_test` (bool, default: true) - Filter out searches from test users
- `limit` - Max results to return
- `status_filter` - Filter by job status

Returns jobs with cached audio search results. Useful for:
- Monitoring search activity
- Identifying stale cached results (YouTube-only when lossless should be available)
- Clearing cache for specific jobs

Response:
```json
{
  "jobs": [
    {
      "job_id": "abc123",
      "status": "awaiting_audio_selection",
      "user_email": "user@example.com",
      "audio_search_artist": "Artist Name",
      "audio_search_title": "Song Title",
      "created_at": "2026-01-03T12:00:00Z",
      "results_count": 5,
      "has_lossless": false,
      "providers": ["YouTube"],
      "results_summary": [...]
    }
  ],
  "total": 1
}
```

#### Clear Audio Search Cache

```http
POST /api/admin/audio-searches/{job_id}/clear-cache
Authorization: Bearer ADMIN_TOKEN
```

Clears cached search results and resets job to `pending` status, allowing a new search.

Use when:
- Cached results are stale (e.g., flacfetch was updated with new providers)
- User wants to search again
- Results appear incomplete (YouTube-only when lossless should exist)

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Cleared 5 cached search results. Job reset to pending. Flacfetch cache also cleared.",
  "previous_status": "awaiting_audio_selection",
  "new_status": "pending",
  "results_cleared": 5,
  "flacfetch_cache_cleared": true,
  "flacfetch_error": null
}
```

Note: This endpoint now also clears the flacfetch GCS cache for the artist/title combination, ensuring the next search hits trackers fresh.

#### Clear All Flacfetch Cache

```http
DELETE /api/admin/cache
Authorization: Bearer ADMIN_TOKEN
```

Clears the entire flacfetch search cache (GCS-backed). Use when flacfetch has been updated and you want all subsequent searches to use fresh tracker results.

Response:
```json
{
  "status": "success",
  "message": "Cleared 15 cache entries from flacfetch.",
  "deleted_count": 15
}
```

#### Get Flacfetch Cache Stats

```http
GET /api/admin/cache/stats
Authorization: Bearer ADMIN_TOKEN
```

Returns statistics about the flacfetch search cache.

Response:
```json
{
  "count": 42,
  "total_size_bytes": 128000,
  "oldest_entry": "2025-12-15T10:30:00Z",
  "newest_entry": "2026-01-03T15:45:00Z",
  "configured": true
}
```

### Email Notifications (Admin)

#### Get Completion Message

```http
GET /api/admin/jobs/{job_id}/completion-message
Authorization: Bearer ADMIN_TOKEN
```

Returns the rendered completion message for a job (for copying to clipboard).

Response:
```json
{
  "job_id": "abc123",
  "message": "Hi there! Your karaoke video is ready...",
  "subject": "Your Karaoke Video is Ready! 🎤",
  "youtube_url": "https://youtube.com/watch?v=...",
  "dropbox_url": "https://dropbox.com/..."
}
```

#### Send Completion Email

```http
POST /api/admin/jobs/{job_id}/send-completion-email
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "to_email": "customer@example.com",
  "cc_admin": true
}
```

Sends the job completion email to the specified address. When `cc_admin` is true (default), CCs gen@nomadkaraoke.com.

Response:
```json
{
  "success": true,
  "job_id": "abc123",
  "to_email": "customer@example.com",
  "message": "Completion email sent successfully"
}
```

### Job Files (Admin)

#### Get Job Files

```http
GET /api/admin/jobs/{job_id}/files
Authorization: Bearer ADMIN_TOKEN
```

Returns all files associated with a job with signed download URLs (2-hour expiry).

Response:
```json
{
  "job_id": "abc123",
  "artist": "Artist Name",
  "title": "Song Title",
  "files": [
    {
      "name": "input.flac",
      "path": "jobs/abc123/input.flac",
      "download_url": "https://storage.googleapis.com/signed-url...",
      "category": "input",
      "file_key": "input"
    }
  ],
  "total_files": 1
}
```

Categories: `input`, `stems`, `lyrics`, `screens`, `videos`, `finals`, `packages`

### Job Updates (Admin)

#### Update Job Fields

```http
PATCH /api/admin/jobs/{job_id}
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "artist": "New Artist",
  "title": "New Title"
}
```

Updates editable job fields. Allowed fields: `artist`, `title`, `user_email`, `theme_id`, `brand_prefix`, `discord_webhook_url`, `youtube_description`, `youtube_description_template`, `customer_email`, `customer_notes`, `enable_cdg`, `enable_txt`, `enable_youtube_upload`, `non_interactive`, `prep_only`, `is_private`.

**Note:** Setting `is_private=true` on a completed job with existing outputs triggers automatic output deletion.

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "updated_fields": ["artist", "title"],
  "message": "Successfully updated 2 field(s)"
}
```

Non-editable fields (job_id, status, created_at, file_urls, state_data) return 400.

#### Reset Job State

```http
POST /api/admin/jobs/{job_id}/reset
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "target_state": "awaiting_review"
}
```

Resets a job to a specific workflow checkpoint for re-processing.

Allowed target states:
- `pending` - Restart from beginning (clears all processing data)
- `awaiting_audio_selection` - Re-select audio source
- `awaiting_review` - Re-do combined review (lyrics + instrumental selection)
- `instrumental_selected` - **Reprocess video** (preserves all settings, triggers video worker automatically)

**Note**: With the combined review flow, `awaiting_instrumental_selection` is no longer a valid reset target for new jobs. Use `awaiting_review` instead to re-select the instrumental.

The `instrumental_selected` state is useful for re-encoding and re-distributing a job after using "Delete Outputs":
1. Delete outputs → Removes YouTube/Dropbox/GDrive files, recycles brand code
2. Reset to `instrumental_selected` → Clears video state, keeps settings, auto-triggers video worker
3. Job re-processes with same instrumental, lyrics, and distribution settings

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "previous_status": "complete",
  "new_status": "awaiting_review",
  "message": "Job reset from complete to awaiting_review",
  "cleared_data": ["review_complete", "corrected_lyrics", "instrumental_selection"],
  "worker_triggered": null,
  "worker_trigger_error": null
}
```

When resetting to `instrumental_selected`, the response includes worker trigger status:
```json
{
  "status": "success",
  "job_id": "abc123",
  "previous_status": "complete",
  "new_status": "instrumental_selected",
  "message": "Job reset from complete to instrumental_selected",
  "cleared_data": ["video_progress", "render_progress", "screens_progress", "encoding_progress", "distribution", "brand_code", "youtube_url", "youtube_video_id", "dropbox_link", "gdrive_files"],
  "worker_triggered": true,
  "worker_trigger_error": null
}
```

If the worker trigger fails, `worker_triggered` will be `false` and `worker_trigger_error` will contain an error message. The job reset still succeeds, but you'll need to manually trigger the worker using the endpoint below.

#### Trigger Worker (Manual)

```http
POST /api/admin/jobs/{job_id}/trigger-worker
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "worker_type": "video"
}
```

Manually triggers a worker for a job. Use this when the auto-trigger fails after a reset, or to re-run processing without resetting state.

Supported worker types:
- `video` - Video processing worker (for jobs in `instrumental_selected` state)

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "worker_type": "video",
  "triggered": true,
  "message": "Video worker triggered successfully for job abc123",
  "error": null
}
```

#### Regenerate Screens

```http
POST /api/admin/jobs/{job_id}/regenerate-screens
Authorization: Bearer ADMIN_TOKEN
```

Regenerates title and end screen videos using the **current** artist/title metadata. Use this after editing artist or title fields to update the screens without full reprocessing.

Requirements:
- Job must have audio and lyrics processing complete
- Job must be in an allowed state: `complete`, `failed`, `awaiting_review`, `awaiting_instrumental_selection`, `instrumental_selected`, `prep_complete`

The endpoint:
1. Deletes existing screen files from GCS
2. Triggers the screens worker to regenerate with current metadata
3. Returns immediately (does not wait for completion)

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Screens regeneration started",
  "previous_screens_deleted": true,
  "worker_triggered": true,
  "error": null
}
```

Screen generation takes 30-60 seconds. Monitor progress via job timeline or logs.

#### Prepare Review Audio

```http
POST /api/admin/jobs/{job_id}/prepare-review-audio
Authorization: Bearer ADMIN_TOKEN
```

Transcodes all review audio files (input + stems) to OGG Opus 128kbps for fast browser playback. Idempotent — skips files already transcoded.

Use this to backfill existing jobs created before eager transcoding was deployed, or to re-transcode after stems are regenerated.

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "transcoded_files": [
    "jobs/abc123/review-audio/song.ogg",
    "jobs/abc123/review-audio/instrumental_clean.ogg"
  ],
  "message": "Transcoded 2 files to OGG Opus"
}
```

#### Restart Job

```http
POST /api/admin/jobs/{job_id}/restart
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "preserve_audio_stems": true,
  "delete_outputs": true
}
```

Fully restarts a job from the beginning. Unlike the reset endpoint (which just changes state), restart actually **triggers workers** to begin processing.

Options:
- `preserve_audio_stems` (default: false) - If true, keeps existing audio separation and lyrics. Only regenerates screens and video with current metadata. Good for fixing title/artist typos.
- `delete_outputs` (default: true) - Delete existing output files from GCS.

Behavior by job type:
- **YouTube URL jobs**: Re-downloads audio, then triggers audio/lyrics workers
- **Audio search jobs**: Transitions to `awaiting_audio_selection` for admin to re-select
- **File upload jobs**: Triggers audio/lyrics workers directly

When `preserve_audio_stems=true`:
- Validates audio and lyrics processing completed previously
- Clears screens/video/encoding state only
- Triggers screens worker immediately

Allowed states: `pending`, `complete`, `failed`, `awaiting_review`, `awaiting_audio_selection`, `awaiting_instrumental_selection`, `instrumental_selected`, `prep_complete`

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Job restarted from complete to downloading",
  "previous_status": "complete",
  "new_status": "downloading",
  "cleared_data": ["audio_complete", "lyrics_complete", "screens_progress", "..."],
  "deleted_gcs_paths": ["jobs/abc123/screens/title.mov", "..."],
  "workers_triggered": ["download", "audio", "lyrics"],
  "error": null
}
```

#### Override Audio Source

```http
POST /api/admin/jobs/{job_id}/override-audio-source
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "source_type": "audio_search"
}
```

Switches a job's audio source from YouTube URL to audio search mode. Use this when a Made-For-You order was submitted with a YouTube URL but you want to find higher quality audio.

Currently only supports switching to `audio_search` mode, which:
1. Clears existing audio-related state (URL, downloaded file, stems, transcription)
2. Performs an audio search using the job's artist/title
3. Stores search results in state_data
4. Transitions job to `awaiting_audio_selection` with results ready
5. Admin can then select an audio source from the search results in the UI

Allowed states: `pending`, `complete`, `failed`, `awaiting_review`, `awaiting_audio_selection`, `instrumental_selected`, `prep_complete`

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Found 5 audio sources - select one in the admin panel.",
  "previous_source": "youtube",
  "new_source": "audio_search",
  "cleared_data": ["audio_complete", "lyrics_complete", "url", "input_media_gcs_path", "..."],
  "new_status": "awaiting_audio_selection",
  "search_results_count": 5,
  "error": null
}
```

If no audio sources found:
```json
{
  "status": "error",
  "job_id": "abc123",
  "message": "No audio sources found for: Artist - Title",
  "previous_source": "youtube",
  "new_source": "audio_search",
  "cleared_data": ["..."],
  "new_status": "failed",
  "search_results_count": null,
  "error": "No audio sources found for: Artist - Title"
}
```

#### Delete Job Outputs

```http
POST /api/admin/jobs/{job_id}/delete-outputs
Authorization: Bearer ADMIN_TOKEN
```

Deletes all distributed outputs (YouTube, Dropbox, Google Drive) for a job and recycles the brand code. The job record is preserved. Use this to fix quality issues: delete outputs, reset to `awaiting_review`, correct lyrics, and re-process.

Requirements:
- Job must be in terminal state (`complete`, `prep_complete`, `failed`, or `cancelled`)
- Outputs must not already be deleted (checks `outputs_deleted_at`)

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Outputs deleted successfully",
  "deleted_services": {
    "youtube": {"status": "success", "video_id": "dQw4w9WgXcQ"},
    "dropbox": {"status": "success", "path": "/Karaoke/NOMAD-1234 - Artist - Title"},
    "gdrive": {"status": "success", "files": {"mp4": true, "mp4_720p": true}},
    "brand_code": {"status": "recycled", "code": "NOMAD-1234"}
  },
  "cleared_state_data": ["youtube_url", "brand_code", "dropbox_link", "gdrive_files"],
  "outputs_deleted_at": "2026-01-10T12:00:00Z"
}
```

Service statuses: `success`, `failed`, `skipped` (not configured or no data), `partial` (some files failed), `error` (exception).

The brand code is automatically recycled after both Dropbox and GDrive cleanup succeed. If either cleanup fails, the brand code is preserved to prevent collisions. Brand code recycling status is returned in `deleted_services.brand_code`. The `DELETE /api/jobs/{id}` endpoint also recycles brand codes before deletion. When the job is re-processed and outputs are re-uploaded, the `outputs_deleted_at` flag is automatically cleared.

#### Clear Worker Progress

```http
POST /api/admin/jobs/{job_id}/clear-workers
Authorization: Bearer ADMIN_TOKEN
```

Clears all worker completion markers from `state_data` to allow re-execution. Use when a job has stale progress keys that prevent workers from running (e.g., after a reset that didn't fully clear state).

Worker progress keys cleared:
- `audio_progress`
- `lyrics_progress`
- `render_progress`
- `screens_progress`
- `video_progress`
- `encoding_progress`

Response:
```json
{
  "status": "success",
  "job_id": "abc123",
  "message": "Cleared 3 worker progress keys",
  "cleared_keys": ["render_progress", "video_progress", "encoding_progress"]
}
```

**Why this is needed**: Workers check `state_data.{worker}_progress.stage == 'complete'` for idempotency - if this key exists from a previous run, the worker will skip execution. When re-reviewing or resetting jobs, these keys must be cleared.

### Internal Email Endpoints

These endpoints are used by Cloud Tasks for automated notifications.

#### Check Idle Reminder

```http
POST /api/internal/jobs/{job_id}/check-idle-reminder
Authorization: Bearer ADMIN_TOKEN
```

Called by Cloud Tasks 5 minutes after a job enters a blocking state (primarily `awaiting_review` for combined review). Sends a reminder email if the user is still idle and no reminder has been sent yet.

**Note**: With the combined review flow, users complete both lyrics review and instrumental selection in one session. The `awaiting_instrumental_selection` state is only used for finalise-only jobs.

### Admin Feedback

```http
GET /api/admin/feedback
GET /api/admin/feedback?search=user@example.com&exclude_test=true&limit=50&offset=0
Authorization: Bearer ADMIN_TOKEN
```

Lists all user feedback submissions with pagination, search, and aggregate stats.

Response:
```json
{
  "items": [
    {
      "id": "uuid",
      "user_email": "user@example.com",
      "created_at": "2026-03-01T12:00:00",
      "overall_rating": 4,
      "ease_of_use_rating": 5,
      "lyrics_accuracy_rating": 3,
      "correction_experience_rating": 4,
      "what_went_well": "...",
      "what_could_improve": "...",
      "additional_comments": null,
      "would_recommend": true,
      "would_use_again": true
    }
  ],
  "total": 2,
  "offset": 0,
  "limit": 50,
  "has_more": false,
  "avg_overall_rating": 4.0,
  "avg_ease_of_use_rating": 4.5,
  "avg_lyrics_accuracy_rating": 3.5,
  "avg_correction_experience_rating": 4.0
}
```

An email notification is also sent to `admin@nomadkaraoke.com` (configurable via `ADMIN_NOTIFICATION_EMAIL` env var) whenever a user submits new feedback.

## Rate Limits & Abuse Prevention

### Usage Control

Job creation is controlled by credits (purchased or earned via feedback). There is no per-user daily job limit — users can create as many jobs as their credits allow.

### YouTube Upload Quota

- **YouTube uploads**: Quota-aware, ~33 uploads/day within 10,000 units/day API quota (configurable via `YOUTUBE_QUOTA_DAILY_LIMIT`, `YOUTUBE_QUOTA_UPLOAD_COST`, `YOUTUBE_QUOTA_SAFETY_MARGIN`). Uploads exceeding quota are queued and processed hourly.

### Admin Blocklist & Queue API

```http
GET /api/admin/rate-limits/blocklists
POST /api/admin/rate-limits/blocklists/disposable-domains
DELETE /api/admin/rate-limits/blocklists/disposable-domains/{domain}
POST /api/admin/rate-limits/blocklists/allowlisted-domains
DELETE /api/admin/rate-limits/blocklists/allowlisted-domains/{domain}
POST /api/admin/rate-limits/blocklists/sync
POST /api/admin/rate-limits/blocklists/blocked-emails
DELETE /api/admin/rate-limits/blocklists/blocked-emails/{email}
POST /api/admin/rate-limits/blocklists/blocked-ips
DELETE /api/admin/rate-limits/blocklists/blocked-ips/{ip}
POST /api/internal/sync-disposable-domains
```
Manage blocklists for disposable email domains, blocked emails, and blocked IPs. The disposable domain list auto-syncs daily from the [disposable-email-domains](https://github.com/disposable-email-domains/disposable-email-domains) repo (~4,800 domains). Admins can add manual domains, and allowlist false positives. The `GET` response includes `external_domains`, `manual_domains`, `allowlisted_domains`, `last_sync_at`, and `last_sync_count`.

### YouTube Upload Queue

```http
GET /api/admin/rate-limits/youtube-queue
```
Returns list of queued/processing/failed YouTube uploads with job details, status, attempt count, and timestamps.

```http
POST /api/admin/rate-limits/youtube-queue/{job_id}/retry
```
Reset a failed upload back to queued status for retry.

```http
POST /api/admin/rate-limits/youtube-queue/process
```
Manually trigger queue processing (normally runs hourly via Cloud Scheduler).

### Internal YouTube Queue Processing

```http
POST /api/internal/youtube-queue/process
```
Called by Cloud Scheduler hourly. Processes queued uploads while quota is available. Returns summary of processed/failed/remaining uploads.

### Internal Stale Review Processing

```http
POST /api/internal/process-stale-reviews
```
Called by Cloud Scheduler hourly. Queries for jobs in `awaiting_review` or `in_review` status, sends reminder emails at 24h, and auto-cancels with credit refund at 48h. Excludes made-for-you and tenant jobs. Returns `{status: "started", message: "..."}` immediately; processing runs in background.

## Webhooks

Stripe webhooks implemented at `/api/users/webhooks/stripe`.
Job status webhooks not yet implemented.
