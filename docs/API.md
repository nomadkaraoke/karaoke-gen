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

### Jobs

#### Create Job with Upload

```http
POST /api/jobs/upload
Content-Type: multipart/form-data

file: <audio file>
artist: "Artist Name"
title: "Song Title"
```

Returns job ID. Triggers async processing.

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
```

Query parameters:
- `exclude_test` (bool, default: true) - Admin only: filter out jobs from test users
- `status` - Filter by job status
- `limit` / `offset` - Pagination

#### Delete Job

```http
DELETE /api/jobs/{job_id}
```

### Review

#### Get Correction Data

```http
GET /api/review/{job_id}/correction-data
```

Returns lyrics correction data for the review UI.

#### Complete Review

```http
POST /api/review/{job_id}/complete
Content-Type: application/json

{
  "corrections": [...],
  "corrected_segments": [...]
}
```

Saves corrections and triggers video rendering.

#### Generate Preview

```http
POST /api/review/{job_id}/preview-video
```

Generates preview video during review.

#### Stream Audio

```http
GET /api/review/{job_id}/audio/{stem_type}
```

Streams audio for review playback.

### Instrumental Selection

```http
POST /api/jobs/{job_id}/select-instrumental
Content-Type: application/json

{
  "selection": "clean"  // or "with_backing"
}
```

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
| `generating_screens` | Title/end screens |
| `awaiting_review` | Waiting for human review |
| `in_review` | Human reviewing |
| `review_complete` | Review submitted |
| `rendering_video` | Generating karaoke video |
| `awaiting_instrumental_selection` | Waiting for selection |
| `instrumental_selected` | Selection made |
| `generating_video` | Final encoding |
| `complete` | Done |
| `failed` | Error |

## Error Responses

```json
{
  "detail": "Error message"
}
```

Common status codes:
- `400` - Bad request
- `401` - Unauthorized
- `404` - Job not found
- `500` - Server error

## User Authentication

### Send Magic Link

```http
POST /api/users/auth/magic-link
Content-Type: application/json

{"email": "user@example.com"}
```

### Verify Magic Link

```http
GET /api/users/auth/verify?token=TOKEN
```

Returns session token and user info.

**Admin Login Token**: The same endpoint supports admin login tokens embedded in notification emails. When a made-for-you order is received, the admin notification email includes a link with `?admin_token=TOKEN` that auto-logs the admin into the app. The frontend detects this parameter and calls the verify endpoint to authenticate. Admin tokens expire after 24 hours.

### Get Current User

```http
GET /api/users/me
Authorization: Bearer SESSION_TOKEN
```

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

### Stripe Webhook

```http
POST /api/users/webhooks/stripe
```

Handles `checkout.session.completed` events.

## Beta Tester Program (PR #90)

### Enroll as Beta Tester

```http
POST /api/users/beta/enroll
Content-Type: application/json

{
  "email": "user@example.com",
  "promise_text": "I promise to provide feedback...",
  "accept_corrections_work": true
}
```

Returns 1 free credit and session token.

### Submit Feedback

```http
POST /api/users/beta/feedback
Authorization: Bearer SESSION_TOKEN
Content-Type: application/json

{
  "overall_rating": 4,
  "ease_of_use_rating": 5,
  "lyrics_accuracy_rating": 4,
  "correction_experience_rating": 3,
  "what_went_well": "...",
  "what_could_improve": "..."
}
```

Bonus credit for detailed feedback (50+ chars).

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
  "total_credits_issued_30d": 200,
  "total_beta_testers": 30
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

### Beta Stats (Admin)

```http
GET /api/users/admin/beta/stats
GET /api/users/admin/beta/stats?exclude_test=false
Authorization: Bearer ADMIN_TOKEN
```

Query parameters:
- `exclude_test` (bool, default: true) - Filter out test users from beta statistics

### Beta Feedback List (Admin)

```http
GET /api/users/admin/beta/feedback?limit=50
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

### Internal Email Endpoints

These endpoints are used by Cloud Tasks for automated notifications.

#### Check Idle Reminder

```http
POST /api/internal/jobs/{job_id}/check-idle-reminder
Authorization: Bearer ADMIN_TOKEN
```

Called by Cloud Tasks 5 minutes after a job enters a blocking state (awaiting_review or awaiting_instrumental_selection). Sends a reminder email if the user is still idle and no reminder has been sent yet.

## Rate Limits

No rate limits currently implemented.

## Webhooks

Stripe webhooks implemented at `/api/users/webhooks/stripe`.
Job status webhooks not yet implemented.
