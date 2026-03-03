# YouTube Quota-Aware Upload System

## Context

4 jobs on 2026-03-03 had YouTube uploads silently skipped because the hard-coded rate limit of 10 uploads/day was reached. All had `is_private: false` and `enable_youtube_upload: true` — users expected YouTube uploads but got none, with no notification of the issue. The rate limit was a primitive approach to avoid exceeding the YouTube Data API v3's "Queries per day" quota (10,000 units/day). In reality, the quota has never been hit in over a year. This plan replaces the hard-coded limit with quota-unit tracking, a deferred upload queue, and user notifications.

**Affected jobs**: `42d3d841`, `56bf22a7`, `7c496ae1`, `2f08e15b`

## Quota Cost Research

YouTube Data API v3 quota costs per operation (from [quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost)):

| Operation | Units | Used in upload flow |
|-----------|-------|-------------------|
| `videos.insert` | 100 | Always |
| `search.list` | 100 | Duplicate check |
| `thumbnails.set` | 50 | Always |
| `videos.delete` | 50 | If replacing duplicate |

**Per upload: ~250-300 units → ~33 uploads/day within 10,000 quota**

Note: Some older sources cite `videos.insert` at 1600 units, but the current quota calculator shows 100, which is consistent with Andrew's experience of 10/day without quota issues. Implementation makes costs configurable.

**On querying Google for quota usage**: There's no direct YouTube API to check remaining quota. The Cloud Monitoring API has `consumer_quota` metrics but it's unreliable for YouTube and adds significant complexity. Instead, we'll self-track units consumed and catch `quotaExceeded` HTTP 403 errors as a safety net. The quota resets at **midnight Pacific Time**.

---

## Implementation Plan

### Phase 1: Quota Tracking Service

**New file: `backend/services/youtube_quota_service.py`** (~200 lines)

Singleton service (same pattern as `RateLimitService`) that tracks quota units in Firestore.

Firestore collection `youtube_quota`, document ID `{YYYY-MM-DD}` (Pacific Time date):
```
{
  "date_pt": "2026-03-03",
  "units_consumed": 2400,
  "units_limit": 10000,
  "operations": [
    { "job_id": "abc123", "operation": "videos.insert", "units": 100, "timestamp": ..., "user_email": "..." },
    ...
  ]
}
```

Key methods:
- `check_quota_available(estimated_units=300) -> (bool, remaining, message)` — same signature as existing `check_youtube_upload_limit()`
- `record_operation(job_id, user_email, operation, units)` — atomic Firestore transaction
- `get_quota_stats() -> dict` — for admin dashboard
- `_get_today_date_pt()` — uses `zoneinfo.ZoneInfo("America/Los_Angeles")` for PT midnight

**Modify: `backend/config.py`** (line ~129)

Replace `RATE_LIMIT_YOUTUBE_UPLOADS_PER_DAY=10` with:
- `YOUTUBE_QUOTA_DAILY_LIMIT=10000` (units)
- `YOUTUBE_QUOTA_UPLOAD_COST=300` (estimated units per upload for pre-flight check)
- `YOUTUBE_QUOTA_SAFETY_MARGIN=500` (reserve buffer)

---

### Phase 2: Upload Queue + Orchestrator Changes

**New file: `backend/services/youtube_upload_queue_service.py`** (~250 lines)

Firestore collection `youtube_upload_queue`, keyed by job_id:
```
{
  "job_id": "abc123",
  "status": "queued",          // queued | processing | completed | failed
  "reason": "quota_exceeded",
  "user_email": "...",
  "artist": "...",
  "title": "...",
  "brand_code": "NOMAD-1287",
  "queued_at": ...,
  "attempts": 0,
  "max_attempts": 5,
  "last_error": null,
  "youtube_url": null,
  "notification_sent": false
}
```

Key methods:
- `queue_upload(job_id, user_email, artist, title, brand_code, reason)`
- `get_queued_uploads(limit=20)` — ordered by queued_at
- `mark_processing(job_id)` — atomic lock
- `mark_completed(job_id, youtube_url)`
- `mark_failed(job_id, error)`
- `retry_upload(job_id)` — admin manual retry (reset to queued)

**Modify: `backend/workers/video_worker_orchestrator.py`** (lines 542-616)

`_upload_to_youtube()` changes:
1. Replace `rate_limit_service.check_youtube_upload_limit()` → `quota_service.check_quota_available()`
2. When quota insufficient: **queue instead of skip**
3. Wrap upload call to catch `YouTubeQuotaExceededError` → queue for retry
4. After each successful YouTube API call, record units via `quota_service.record_operation()`

**Modify: `backend/services/youtube_upload_service.py`**

- Catch `googleapiclient.errors.HttpError` with status 403 + reason `quotaExceeded`
- Raise new `YouTubeQuotaExceededError` (add to existing exceptions file)

**Modify: `backend/workers/video_worker.py`** (lines 339-351)

Add `youtube_upload_queued` to `state_data` so completion email can inform user.

---

### Phase 3: Scheduled Queue Processor

**New file: `backend/workers/youtube_queue_processor.py`** (~200 lines)

`async def process_youtube_upload_queue() -> dict`:
1. Check quota available → exit early if none
2. Get queued uploads
3. For each: mark_processing → download video from GCS → upload to YouTube → mark_completed + update job state_data → send follow-up email
4. Stop processing if `YouTubeQuotaExceededError` encountered
5. Return summary (processed, failed, remaining)

Credentials loaded fresh from Secret Manager each run (same as `video_worker.py` line 242).

**Modify: `backend/api/routes/internal.py`**

Add `POST /api/internal/youtube-queue/process` — called by Cloud Scheduler, same auth pattern as existing internal endpoints.

**Infrastructure (Pulumi):**
- New Cloud Tasks queue `youtube-queue` (1 concurrent dispatch, 30min deadline)
- New Cloud Scheduler job: hourly (`0 * * * *`), timezone `America/Los_Angeles`, calls the internal endpoint

Files:
- `infrastructure/modules/cloud_tasks.py` — add queue
- `infrastructure/__main__.py` — add scheduler job (follows `gdrive_validator_scheduler` pattern at line ~260)

---

### Phase 4: User Notifications

**Modify: `backend/services/job_notification_service.py`**

New method: `send_youtube_upload_notification(job_id, user_email, artist, title, youtube_url, brand_code)`

**Modify: `backend/services/template_service.py`**

1. New template `render_youtube_upload_complete()` — follow-up email with YouTube URL
2. Modify `render_job_completion()` — when `youtube_queued=True` and `youtube_url=None`, add note: "YouTube upload is queued and will be processed automatically. You'll receive a follow-up email with the link."

**Modify: `backend/services/email_service.py`**

New method: `send_youtube_upload_complete(to_email, message_content, artist, title, brand_code)` — follows same pattern as `send_job_completion()`

---

### Phase 5: Admin Dashboard

**Modify: `backend/api/routes/rate_limits.py`**

Update stats response with quota fields:
- `youtube_quota_units_consumed`, `youtube_quota_units_remaining`, `youtube_quota_daily_limit`
- `youtube_uploads_queued`, `youtube_uploads_failed`

New endpoints:
- `GET /admin/rate-limits/youtube-queue` — list queued uploads
- `POST /admin/rate-limits/youtube-queue/{job_id}/retry` — manual retry
- `POST /admin/rate-limits/youtube-queue/process` — trigger processing

**Modify: `frontend/app/admin/rate-limits/page.tsx`**

Add "YouTube Queue" section:
- Quota usage progress bar (units consumed / limit)
- Queued uploads table with Retry buttons
- "Process Queue Now" button

**Modify: `frontend/lib/api.ts`**

Add types and API methods for queue management.

---

### Phase 6: Migration + Cleanup

**Modify: `backend/services/rate_limit_service.py`**

Deprecate `check_youtube_upload_limit()` and `record_youtube_upload()` — redirect to new quota service internally so old code paths still work during transition.

**Manual retry for 4 affected jobs**: Use new admin retry endpoint to queue them.

---

## File Summary

| File | Action | Phase |
|------|--------|-------|
| `backend/services/youtube_quota_service.py` | NEW | 1 |
| `backend/config.py` | MODIFY | 1 |
| `backend/services/youtube_upload_queue_service.py` | NEW | 2 |
| `backend/workers/video_worker_orchestrator.py` | MODIFY | 2 |
| `backend/services/youtube_upload_service.py` | MODIFY | 2 |
| `backend/workers/video_worker.py` | MODIFY | 2 |
| `backend/workers/youtube_queue_processor.py` | NEW | 3 |
| `backend/api/routes/internal.py` | MODIFY | 3 |
| `infrastructure/modules/cloud_tasks.py` | MODIFY | 3 |
| `infrastructure/__main__.py` | MODIFY | 3 |
| `backend/services/job_notification_service.py` | MODIFY | 4 |
| `backend/services/template_service.py` | MODIFY | 4 |
| `backend/services/email_service.py` | MODIFY | 4 |
| `backend/api/routes/rate_limits.py` | MODIFY | 5 |
| `frontend/app/admin/rate-limits/page.tsx` | MODIFY | 5 |
| `frontend/lib/api.ts` | MODIFY | 5 |
| `backend/services/rate_limit_service.py` | MODIFY | 6 |

## Verification

1. **Unit tests**: New test files for quota service, queue service, and processor
2. **Manual test**: Create a job with `enable_youtube_upload: true`, set `YOUTUBE_QUOTA_DAILY_LIMIT=1` to force queueing, verify:
   - Job completes with `youtube_upload_queued: true`
   - Completion email mentions YouTube is pending
   - Queue entry created in Firestore
   - Trigger processor manually → upload completes → follow-up email sent → job state_data updated
3. **Admin dashboard**: Verify quota stats display and manual retry works
4. **Retry 4 affected jobs**: Use admin endpoint to queue them for upload
