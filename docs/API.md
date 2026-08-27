# API Reference

Base URL: `https://api.nomadkaraoke.com` (production) or `http://localhost:8000` (local)

> **Edge:** production traffic must go through `https://api.nomadkaraoke.com`
> (proxied by Cloudflare — WAF + rate limiting). The Cloud Run origin is
> **origin-locked**: requests hitting the raw `*.run.app` URL directly are
> rejected with `403` unless they carry the Cloudflare-injected `X-Edge-Auth`
> header (`/` and `/api/health*` are exempt for probes). See
> [ARCHITECTURE.md § Edge Security](ARCHITECTURE.md#edge-security-cloudflare).

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

#### Create Job from URL

```http
POST /api/jobs
Content-Type: application/json

{
  "url": "https://www.youtube.com/watch?v=...",
  "artist": "Artist Name",       // optional, auto-detected if omitted
  "title": "Song Title",         // optional, auto-detected if omitted
  "is_private": false,           // optional
  "duration_seconds": 245.3,     // optional — from /api/jobs/estimate; drives pre-flight credit deduction
  "acknowledged_credits": 1      // optional — if provided, server validates it equals the computed cost; 409 on mismatch
}
```

Creates a job from a YouTube (or other supported) URL. Credit cost is `max(1, ceil(minutes/10))`
(10-minute tiers). If `duration_seconds` is omitted the system deducts 1 credit as a hold and
settles the true cost after download (see [Duration-Based Pricing](#duration-based-pricing)).
Admin jobs bypass all credit deduction.

`acknowledged_credits` is **optional**. If provided, the server validates it equals the
server-computed credit cost and returns 409 on mismatch (race-condition guard for the UI). If
omitted, the server charges the computed cost without client confirmation.

Returns `JobResponse` (job id, status, etc.).

**Errors:**
- `402` — Insufficient credits (see [Credit Enforcement](#credit-enforcement))
- `409` — `acknowledged_credits` was provided and does not match server-computed cost; client should refresh and re-confirm
- `422` — Input exceeds 60-minute hard limit

#### Estimate URL Duration (pre-flight)

```http
POST /api/jobs/estimate
Content-Type: application/json

{"url": "https://www.youtube.com/watch?v=..."}
```

Probes the URL (via flacfetch `/check-youtube`) **without creating a job or downloading audio**.
Use this before `POST /api/jobs` to show the user the credit cost.

Response:
```json
{
  "duration_seconds": 245.3,
  "credits": 1,
  "blocked": false,
  "source": "youtube_metadata"
}
```

- `duration_seconds`: `null` if the probe fails (bot challenge, unsupported URL, etc.)
- `source`: `"youtube_metadata"` | `"unknown"` (probe failed)
- `blocked`: `true` when `duration_seconds > 3600` (60 min) — create will be rejected
- `credits`: computed from `max(1, ceil(duration_seconds / 600))`; `1` when duration unknown

When `duration_seconds` is `null`, warn the user that the final cost will be confirmed after
download and create the job with a 1-credit hold.

#### Confirm Duration (resume paused job)

```http
POST /api/jobs/{job_id}/confirm-duration
Content-Type: application/json

{"acknowledged_credits": 3}
```

Called when a job is paused in `AWAITING_DURATION_CONFIRM` state. Two scenarios:

- **`preflight`** — upload flow: duration measured after file lands in GCS; no charge applied yet.
  `acknowledged_credits` must equal the full cost (`pending_additional_credits` in `state_data`).
- **`reconcile`** — post-download: actual duration costs more than was already charged.
  `acknowledged_credits` must equal the new total (`credits_charged + pending_additional_credits`).

On success, deducts the owed credits and resumes by triggering both audio and lyrics workers.
Returns the updated job.

**Errors:**
- `402` — Insufficient credits; user should buy more and retry
- `409` — `acknowledged_credits` doesn't match server's current figure (race/refresh); client should re-fetch the job and re-show the dialog

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

**Optional fields:**
- `upload_mode`: `"signed_put"` (default) or `"resumable"`. With `"resumable"`, each
  `upload_urls` entry has `resumable: true` and `upload_url` is a **GCS resumable session
  URI** — upload in 256KiB-aligned chunks with `Content-Range` headers, query the persisted
  offset after an interruption (`PUT` with `Content-Range: bytes */<total>` → `308` +
  `Range` header), and resume mid-file. Sessions are created with the caller's `Origin` so
  GCS answers session CORS itself. Used by the tenant bulk-upload flow
  (`frontend/lib/resumable-upload.ts`).
- `batch_id`: groups jobs created together in one bulk batch — stamped into
  `state_data.batch_id` with `created_from: "tenant-bulk"`.

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

**Auto-approval:** jobs default to `review_mode="auto"` — when the auto-approval scorer is fully
confident (lyrics verified against a synced reference with every gap covered by the auto-applied
AI suggestions, AND no audible backing vocals), the review screens are skipped entirely: the AI
suggestions are applied server-side, the clean instrumental is selected, and the job goes
straight to render. All other jobs stop at review as before. The verdict for every job is
recorded in `processing_metadata.auto_approval` (fields: `mode`, `outcome`, per-axis verdicts +
signals). Set `review_mode="always_review"` (admin job PATCH) to force the review screens;
global kill switch: `AUTO_APPROVAL_ENFORCE_ENABLED=false`.

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
  "force": "false"
}
```

Adds user-pasted lyrics as a new reference source, re-runs the correction pipeline with all sources, and uploads updated corrections to GCS. The `force` flag (default `false`, sent as a string because the body is typed `Dict[str, str]`) bypasses the relevance threshold — use when the transcription is poor and a low match % doesn't mean wrong song.

Returns `{ "status": "success", "data": <CorrectionData> }` when the source is kept.

If the relevance filter drops the source (below the 30% anchor-match threshold), the corrections are **not** persisted and the response is instead:

```json
{
  "status": "rejected",
  "rejection": { "relevance": 0.013, "matched_words": 3, "total_words": 230 },
  "data": { "...": "CorrectionData from the discarded rerun" }
}
```

The frontend paste tab shows the match % and offers "Add Anyway", which retries with `force: "true"`.

#### AI Auto-Correct Suggestions

```http
POST /api/review/{job_id}/auto-correct
Content-Type: application/json

{
  "segments": [ "...client's current corrected_segments..." ],
  "reference_lyrics": { "genius": { "segments": [...] } },
  "artist": "Twenty One Pilots",
  "title": "Chlorine",
  "settings": {
    "suggest_adlib_removal": true,
    "allow_insertions": true,
    "min_confidence": 0.0,
    "compare_models": true
  }
}
```

AI correction suggestions for the lyrics review UI. Stateless: one whole-song
LLM call per model (Claude models use the Anthropic API via the
`anthropic-api-key` secret, Gemini models use Vertex AI) compares the
client's current transcription against the reference sources and returns
word-id-keyed suggestions. Nothing is applied server-side — the reviewer
accepts/rejects each suggestion in the UI and persistence flows through the
existing corrections/complete paths.

The review UI always requests the multi-model path (`compare_models: true`)
and auto-runs once on load, so suggestions appear without a click. To make
that load instant, suggestions are **proactively pre-generated and cached**
once transcription + references are ready: the lyrics worker fires an internal
trigger (`POST /api/internal/workers/auto-correct`) that runs the same
multi-model generation on the API service (gated by
`AUTO_CORRECT_PROACTIVE_ENABLED`; best-effort — it never blocks or fails the
karaoke job). Because the proactive run uses the same default settings the UI
sends, the on-load request is normally a cache hit with no extra cost.

With `"compare_models": true`, every model in `AUTO_CORRECT_COMPARE_MODELS`
(semicolon-separated) is queried in parallel; identical suggestions are
deduplicated with a per-suggestion `consensus` count, and suggestions that
target overlapping words with different outcomes share a `conflict_group`
(the UI accepts at most one per group). A model failure in compare mode is
reported in `warnings` while remaining model results are returned; only if
all models fail does the endpoint return 502.

Returns:

```json
{
  "suggestions": [
    {
      "id": "uuid",
      "op": "replace",
      "word_ids": ["w3"],
      "segment_ids": ["seg-1"],
      "original_text": "glory",
      "new_text": "chlorine",
      "reason": "All references read 'chlorine' here",
      "category": "mishearing",
      "confidence": 0.95,
      "models": ["claude-opus-4-8", "gemini-3.1-pro-preview"],
      "consensus": 2,
      "total_models": 2,
      "conflict_group": null
    }
  ],
  "model": "claude-opus-4-8, gemini-3.1-pro-preview",
  "elapsed_seconds": 27.4,
  "settings_applied": { "suggest_adlib_removal": true, "allow_insertions": true, "min_confidence": 0.0 },
  "warnings": [],
  "cached": false
}
```

Results are cached per job in GCS
(`jobs/{job_id}/lyrics/auto_correct_cache/{hash}.json`), keyed on the exact
input: word ids + texts, reference lyrics texts, settings, and the resolved
model list. Re-running on unmodified lyrics returns the previous result with
`"cached": true` and incurs no LLM cost; any edit to the lyrics, references,
or settings produces a new key and a fresh run. Cache reads/writes are
best-effort — storage failures degrade to a normal uncached run.

`op` is `replace` | `delete` | `insert_after` (for `insert_after`, `word_ids`
holds the anchor word). Errors: `422` when no reference lyrics are available,
`400` for invalid settings, `429` when the model provider is temporarily
rate-limited (transient — retryable; the service already retries with backoff
before surfacing this), `502` when the model call otherwise fails or returns
malformed output. Design/eval background:
`docs/archive/2026-06-10-lyrics-auto-correction-reeval-plan.md`.

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

#### Match Judge (artist/title formatting + match acceptance)

```http
POST /api/catalog/match-judge
Content-Type: application/json

{
  "artist": "paramore",
  "title": "big man, little dignity",
  "audio_confidence_tier": 1,
  "stage": "full"
}
```

Decides the official formatting for a typed artist/title and whether it matches a
real song. A deterministic normalizer + the catalog run first; a light Vertex
Gemini model (`MATCH_JUDGE_MODEL`, default `gemini-3.5-flash`) is consulted
**only** when those aren't confident. `audio_confidence_tier` (1=strong..3=weak,
optional) lets the judge tell whether weak audio results hint at a typo. The call
never blocks job creation — on timeout/error it returns a `none` verdict. Disable
the AI layer with `MATCH_JUDGE_ENABLED=false` (deterministic+catalog still run).

`stage` (optional, `"fast"` | `"full"`, default `"full"`) supports a two-call
pattern the frontend uses to keep the tidy off the critical path:

- `"fast"` — catalog-only pass (no AI, `audio_confidence_tier` ignored). The
  client fires this on mount, in parallel with the audio search. Returns a
  confident catalog verdict when it can, otherwise `needs_ai: true` (a marker
  telling the client to follow up with a `"full"` call once the tier is known).
- `"full"` — the complete pipeline. Runs the AI judge when the catalog isn't
  confident, **and** when a confident catalog match coincides with a weak audio
  tier (`>= 3`) — a weak tier means the match may be a junk/typo'd entry, so the
  AI verifies it and can override.

Response:
```json
{
  "kind": "cosmetic",
  "confident": true,
  "canonical_artist": "Paramore",
  "canonical_title": "Big Man, Little Dignity",
  "alternatives": [],
  "engine": "catalog",
  "reason": "formatting differs from catalog",
  "needs_ai": false
}
```

`kind` drives the Step 2 UI: `cosmetic` → silent tidy with a two-way toggle
("keep what I typed" ⇄ "use tidied version"); `content` (confident) → applied
with a "Corrected … · Undo" toggle, re-searching audio only when the original
results were weak; `ambiguous` (or unconfident `content`) → an ask-first "Did you
mean?" prompt; `none` → no change. `needs_ai` is only ever `true` on a `"fast"`
verdict that couldn't decide; it is always `false` on a `"full"` verdict.

Until the judge settles, the Step 2 audio-selection buttons are disabled with a
subtle "Checking song details…" indicator, so a user can never advance with
un-tidied metadata; a 12s timeout guarantees the gate releases.

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
  "selection_index": 0,
  "acknowledged_credits": 2   // optional — if provided, server validates it equals the computed total; 409 on mismatch
}
```

Selects an audio source from the search results and starts processing. Credits are deducted here
based on the selected result's duration (`max(1, ceil(minutes/10))`). Torrent results display an
"estimated" label because metadata may differ from actual audio length; the cost is reconciled
post-download and may trigger an `AWAITING_DURATION_CONFIRM` pause if the actual duration is longer.

`acknowledged_credits` is **optional**. If provided, the server validates it equals the
server-computed credit total and returns 409 on mismatch. If omitted, the server deducts the
computed cost without client confirmation.

#### Recover a Parked Job (re-search / provide own audio)

When a search returns nothing usable (0 results, a transient failure, or the auto-picked
source is unusable) the job stays in `AWAITING_AUDIO_SELECTION` with no selectable sources.
These endpoints let the user out of that dead-end without creating a new job or re-charging
base credits (the job was already charged at creation; final duration billing is reconciled
post-download). All require the job to be in `AWAITING_AUDIO_SELECTION` (else `400`).

```http
POST /api/audio-search/{job_id}/research
Content-Type: application/json

{ "artist": "Arctic Monkeys", "title": "The View From the Afternoon" }  // both optional
```

Re-runs the audio search. Blank/omitted fields re-use the job's existing search terms (a plain
retry); provided values are applied to the job's search terms **and** display artist/title, then
searched. Returns the same shape as `/search` (`results`, `results_count`). An empty result set is
a normal `200` (not an error) so the client can offer the manual fallback below. `502` on a hard
search-service error.

```http
POST /api/audio-search/{job_id}/provide-url
Content-Type: application/json

{ "url": "https://youtube.com/watch?v=..." }
```

Attaches a YouTube/yt-dlp URL to the parked job, downloads it, and starts processing
(`AWAITING_AUDIO_SELECTION → DOWNLOADING_AUDIO → DOWNLOADING`). Rejects DRM/streaming links and
unavailable videos with `400`; `502` if the download fails (the job is returned to the queue).

```http
POST /api/audio-search/{job_id}/attach-upload-url        // → { upload_url, gcs_path }
POST /api/audio-search/{job_id}/attach-upload-complete   // after PUTting the file
```

Two-step manual file upload for a parked job: request a signed GCS upload URL, `PUT` the audio
file to it, then call `attach-upload-complete` to point the job at the file and start processing.

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

#### Tenant Bulk Upload — Filename Analysis

```http
POST /api/tenant/bulk/analyze
Content-Type: application/json
Authorization: Bearer <tenant session token>

{"filenames": ["S1100-1 Eddy Grant - I Don't Wanna Dance Guide.mp3", "S1100-2 Eddy Grant - I Don't Wanna Dance BV.mp3", "cover.png"]}
```

Analyses a flat list of filenames (from a browser folder pick) into proposed karaoke jobs:
pairs each **Mixed** (with-vocals) file with its **Instrumental** counterpart and extracts
Artist/Title. Pure analysis — filenames only, no uploads, no state written.

- **Auth:** tenant session required; gated on the tenant's `features.bulk_upload` flag (403 otherwise).
- **Limits:** max 100 audio files per request (non-audio files don't count toward the cap; 2000-filename payload guard).
- **Two passes:** deterministic regex for the `S<code>-<1|2> Artist - Title <Guide|BV|Instru>`
  convention, then a Vertex-Gemini pass (`backend/services/tenant_bulk/analyze.py`) for
  whatever the regex couldn't confidently pair. Falls back to regex-only if the model errors.

Response:
```json
{
  "rows": [{"artist": "Eddy Grant", "title": "I Don't Wanna Dance",
             "mixed_filename": "…Guide.mp3", "instrumental_filename": "…BV.mp3",
             "confidence": "high", "warning": null}],
  "unpaired": [{"filename": "…", "reason": "no_instrumental", "artist": "…", "title": "…", "role": "mixed"}],
  "ignored": [{"filename": "cover.png", "reason": "non_audio"}]
}
```

The tenant portal's Bulk mode (`TenantBulkFlow.tsx`) renders these rows as an editable review
table, then submits each confirmed row through the standard signed-URL upload flow
(`upload_mode: "resumable"`, `batch_id` grouping).

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
| `awaiting_duration_confirm` | **Human checkpoint** — cost confirmation required before heavy processing begins (see [Duration-Based Pricing](#duration-based-pricing)) |
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

### Resend Magic Link From Token

```http
POST /api/users/auth/resend-from-token
Content-Type: application/json

{"token": "PREVIOUSLY_ISSUED_TOKEN"}
```

Used by the verify failure UI to give users a one-click recovery when their sign-in link has expired or already been used. The endpoint looks up the token in Firestore, mints a fresh magic link for the same email, and sends it. The original token is a 32-byte cryptographic secret only the email recipient could possess, so resending to the recorded address adds no new attack surface.

Responses — `status` is either `"sent"` (a fresh link was emailed) or `"no_token"` (the token was not found in Firestore):

Successful resend:

```json
{
  "status": "sent",
  "masked_email": "ho***@ya***.com",
  "message": "If this email is registered, you will receive a sign-in link shortly."
}
```

Token not recognised (the UI should fall back to the standard sign-in form):

```json
{
  "status": "no_token",
  "masked_email": null,
  "message": "We couldn't recognise that sign-in link. Please go back and request a new one from the sign-in form."
}
```

A short per-token cooldown applies — repeated resend attempts for the same token within 60 seconds return `status: "sent"` without re-sending the email. Tenant context, referral code, and device fingerprint from the original token are preserved on the new link.

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

#### Duration-Based Pricing

Credits are charged proportionally to audio duration: `max(1, ceil(minutes / 10))` per job.

| Duration | Credits |
|----------|---------|
| 0:00 – 10:00 | 1 |
| 10:01 – 20:00 | 2 |
| 20:01 – 30:00 | 3 |
| 30:01 – 40:00 | 4 |
| 40:01 – 50:00 | 5 |
| 50:01 – 60:00 | 6 |
| 60:01+ | **blocked** (hard limit) |

Inputs over 60 minutes are rejected at creation time (HTTP 422). The backend
`duration_to_credits()` util (`backend/services/pricing.py`) is the single source of truth —
the frontend mirrors the constants for display only. See
`docs/archive/2026-06-04-long-duration-input-handling-design.md` for full design details.

#### Credit Deduction Flow

Credits are deducted N-atomically (not always just 1) at the earliest point duration is known:

- **URL jobs**: deducted at `POST /api/jobs` using the `duration_seconds` from `/api/jobs/estimate`.
  If duration is unknown, 1 credit is held and reconciled post-download.
- **Audio search select**: deducted at `/api/audio-search/{job_id}/select` from the result's duration.
- **Upload jobs**: 1 credit is charged at job creation (same as URL jobs without a known duration).
  After the audio lands in GCS, the same reconciliation checkpoint runs: ffprobe measures the actual
  duration; if the cost exceeds the 1-credit hold the job pauses in `AWAITING_DURATION_CONFIRM`
  (reason `reconcile`) for the user to pay the delta; if it costs less an auto-refund is issued.

A reconciliation checkpoint runs immediately before audio separation + transcription is triggered.
If the actual measured duration costs more than was charged, the job pauses in
`AWAITING_DURATION_CONFIRM` for the user to pay the difference. If it costs less, a silent
auto-refund is issued. If the actual duration exceeds 60 minutes, all charged credits are refunded
and the job is cancelled.

Admin users bypass credit checks entirely. New users receive 1 welcome credit. Users can earn 1
additional free credit by submitting product feedback after completing 2+ jobs (see
[User Feedback for Credits](#user-feedback-for-credits)).

#### 402 Response

When a user has insufficient credits, job creation or confirmation returns HTTP 402:

```json
{
  "detail": "You're out of credits. Buy more to continue creating karaoke videos.",
  "credits_available": 0,
  "credits_required": 3,
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
- `locale` (email locale: en/es/de) and `ui_locale` (full UI language, any of 33)
- Recent credit transactions (last 20)
- Recent jobs (last 10, each with its `locale`)
- Active sessions count

### Email History (Admin)

List every email ever sent to a user, then fetch one for full-fidelity preview.
Merges the Postmark Messages API (last ~45 days, with delivery metadata) with a
persisted Firestore `email_log` (permanent), de-duplicated by message id.

```http
GET /api/admin/users/{email}/emails
Authorization: Bearer ADMIN_TOKEN
```

Response: `{ email, count, postmark_available, emails: [{ message_id, source,
subject, to, from_email, sent_at, status, email_type, has_stored_html }] }`.

```http
GET /api/admin/emails/{message_id}?source=postmark
Authorization: Bearer ADMIN_TOKEN
```

`source` is `postmark` (default; falls back to the stored log on 404/expiry) or
`log` (read the persisted copy directly). Returns the full rendered `html_body`,
`text_body`, addressing, `status`, `delivered_at`, `open_count`, `click_count`,
`bounce`, and the raw `events`.

### Create User (Admin)

Create a user account directly — e.g. to grant credits, submit jobs on behalf of,
or impersonate someone who has never logged in. The user can later log in normally
via magic link with the same email.

```http
POST /api/users/admin/users
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{
  "email": "user@example.com",
  "display_name": "Optional Name",
  "initial_credits": 3,
  "credit_reason": "made-for-you order"
}
```

Response (201):
```json
{
  "status": "success",
  "email": "user@example.com",
  "credits": 3,
  "message": "Created user user@example.com with 3 credit(s)"
}
```

Notes:
- `display_name`, `initial_credits` (0-1000), and `credit_reason` are optional
- If `initial_credits` > 0, `welcome_credits_granted` is set so the user does NOT
  also receive the automatic welcome credit on first login
- Returns 409 if the user already exists

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

**Note:** `user_email` is trimmed and lowercased before saving. If no account exists
for that email, one is auto-created (so the recipient can log in and see the job,
and the admin can impersonate them immediately). The response message notes when
an account was created.

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
- Job must be in an allowed state: `complete`, `failed`, `awaiting_review`, `in_review`, `awaiting_instrumental_selection`, `instrumental_selected`, `prep_complete`

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

Called by Cloud Tasks after a job enters a blocking state. Sends a reminder email if the user is still idle and no reminder has been sent yet. Handles two `action_type` cases:
- `review_reminder` — fires 5 minutes after `awaiting_review` (combined lyrics + instrumental review)
- `duration_confirm_reminder` — fires 15 minutes after `awaiting_duration_confirm`

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
Called by Cloud Scheduler hourly. Queries for jobs in `awaiting_review`, `in_review`, or
`awaiting_duration_confirm` status. Sends reminder emails at 24h; auto-cancels with full credit
refund at 48h (all `credits_charged` are returned for duration-confirm expirations). Excludes
made-for-you and tenant jobs. Returns `{status: "started", message: "..."}` immediately; processing
runs in background.

## Referral System

### Get Referral Interstitial (Public)

```http
GET /api/referrals/r/{code}
```

No auth required. Returns referral link data for the interstitial page. Increments click count. Returns `valid: false` if code not found or disabled.

### Get Referral Dashboard

```http
GET /api/referrals/me
Authorization: Bearer TOKEN
```

Returns the current user's referral link, stats (clicks, signups, purchases), pending balance, recent earnings, recent payouts, and Stripe Connect status.

### Update Referral Link

```http
PUT /api/referrals/me
Authorization: Bearer TOKEN
Content-Type: application/json

{"display_name": "DJ Tommy", "custom_message": "Best karaoke tool!"}
```

Update the current user's referral link display name and custom message (shown on interstitial page).

### Start Stripe Connect Onboarding

```http
POST /api/referrals/me/connect
Authorization: Bearer TOKEN
```

Creates a Stripe Connect Express account and returns an onboarding URL. Saves the Connect account ID to the user doc. The dashboard response (`GET /api/referrals/me`) includes `stripe_connect_account` with account status details (payouts_enabled, details_submitted, etc.).

### Get Stripe Connect Dashboard Link

```http
POST /api/referrals/me/connect/dashboard-link
Authorization: Bearer TOKEN
```

Returns a one-time login URL to the Stripe Express dashboard where the user can manage their payout settings, view transfer history, and update bank details.

### Get Stripe Connect Update Link

```http
POST /api/referrals/me/connect/update-link
Authorization: Bearer TOKEN
```

Returns a Stripe AccountLink URL for re-onboarding — allows the user to update their bank account or complete setup if they didn't finish initially.

### Create Vanity Link (Admin)

```http
POST /api/referrals/admin/vanity
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{"code": "djtommy", "owner_email": "tommy@example.com", "discount_percent": 15, "kickback_percent": 25}
```

Admin creates a custom vanity referral link with optional override rates.

### List Referral Links (Admin)

```http
GET /api/referrals/admin/links?limit=50&offset=0
Authorization: Bearer ADMIN_TOKEN
```

### Update Any Link (Admin)

```http
PUT /api/referrals/admin/links/{code}
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{"discount_percent": 20, "enabled": false}
```

### Referral Attribution

Referral codes are passed via the `x-referral-code` HTTP header during magic link verification. Attribution is one-time on first login only.

### Referral Discounts at Checkout

Referred users with an active discount window (default 30 days) automatically receive a Stripe coupon at checkout. The discount replaces `allow_promotion_codes` on the checkout session.

### Referral Earnings

After a credit purchase by a referred user, earnings are recorded automatically in the Stripe webhook handler. When a referrer's pending balance reaches $20 and they have a Stripe Connect account, a payout is triggered automatically.

## Bulk Mode

Submit up to 100 karaoke jobs at once (by text rows or by album lookup). All
routes require auth.

### Album lookup (MusicBrainz)

- `GET /api/bulk/album/artists?q=<name>` → `[{mbid,name,disambiguation,type,country}]`
- `GET /api/bulk/album/albums?artist_mbid=<mbid>` → `[{release_group_mbid,title,primary_type,secondary_types,first_release_date,is_studio}]` (studio albums first)
- `GET /api/bulk/album/tracklist?artist=<name>&release_group_mbid=<mbid>` (or `&release_mbid=<mbid>` for a specific edition; optional `&locale=<code>` biases representative-pressing choice) → `{release_mbid,canonical_release_mbid,selected_variant_mbid,title,date,tracks:[{position,title,recording_mbid,length_ms,is_extra,extra_reason,available,brands,versions:[{brand,url}]}],editions:[{release_mbid,title,status,date,country,track_count}],variants:[{representative_release_mbid,label,track_count,year,pressing_count,delta_vs_original}]}`. `variants` collapses the release-group's country pressings into distinct tracklists grouped by track count (label `Original`/`Reissue`, `Original` = earliest); load one via its `representative_release_mbid` as `release_mbid`. Each track is enriched with KaraokeNerds community-version availability — `available`/`brands` plus `versions` (per-version `{brand,url}` YouTube links for clickable preview); enrichment is best-effort and never fatal.

### Availability (text mode)

- `POST /api/bulk/availability` body `{tracks:[{artist,title}]}` (≤100) → `{results:[{artist,title,available,brands,brand_count,versions:[{brand,url}]}]}`. Backed by the existing KaraokeNerds community-version scraper (1h cache, bounded concurrency). `versions` carries per-version YouTube links for clickable preview.

### Submit

- `POST /api/bulk/submit` body `{songs:[{artist,title,display_artist?,display_title?}],settings:{auto_select_if_lossless,is_private,skip_audio_edit,skip_customization}}` → `{batch_id,job_ids,total}`.
  - Credit gate: requires `credits >= len(songs)` (1/song minimum) before any job is created; admins bypass. Returns `402` with `{credits_available,credits_required}` if short (no jobs created). `422` for 0 or >100 songs.
  - Creates one parked job per song (`auto_download=False`), stamps `state_data.batch_id` + `bulk_settings`, and dispatches the `bulk-search-job` Cloud Run Job which runs each search and either auto-selects a confident lossless match or parks the job in `AWAITING_AUDIO_SELECTION`.

### Progress

- `GET /api/bulk/{batch_id}` → `{batch_id,total,counts:{searching,awaiting_selection,processing,complete,failed},jobs:[{job_id,artist,title,status,auto_selected}]}`. Ownership-scoped (admins see any); `404` for another user's batch. Parked jobs are completed via the existing `/api/audio-search/{job_id}/select` flow, or recovered
via `/research` (re-search / edit terms) and `/provide-url` · `/attach-upload-*` (own audio) when a
search returned nothing usable.

## Webhooks

Stripe webhooks implemented at `/api/users/webhooks/stripe`.
Job status webhooks not yet implemented.
