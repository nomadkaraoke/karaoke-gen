# Review Reminder & Auto-Expiry for Stale Jobs

**Date:** 2026-03-19
**Status:** Design approved, ready for implementation
**Branch:** `feat/sess-20260319-1419-review-reminder-autoexpiry`

## Problem

Jobs that enter the review stage (`awaiting_review` / `in_review`) sometimes sit indefinitely because users never complete the lyrics review. Examples: jobs c03686b9, c7eb4e2a, 4dcdb8aa — all stuck for 8+ days. These incomplete jobs linger in the system with no mechanism to resolve them.

## Solution

Hourly Cloud Scheduler cron that queries for stale review jobs and takes two actions:

1. **24h reminder email** — gentle nudge with review link, offer of help, and warning about 48h expiry
2. **48h auto-expiry** — cancel job, refund credit, send expiry notification email

## Design Decisions

- **Approach: Cloud Scheduler cron** (vs Cloud Tasks or hybrid). Simplest, most robust, easy to monitor. Up to 1h variance on timing is acceptable. Follows the existing YouTube queue processor pattern.
- **Clock starts at `blocking_state_entered_at`** — set when job enters `awaiting_review`, regardless of whether user briefly opens the review page (`in_review`).
- **Excluded from auto-expiry:**
  - Made-for-you jobs (`made_for_you == True`) — admin-controlled, customer already paid via Stripe
  - Tenant/white-label jobs (`tenant_id` is non-empty) — B2B partners with different workflows
- **Credits refunded regardless** of whether they were free (welcome) or purchased.
- **No retroactive cleanup** — existing stale jobs will be handled manually. (Note: the processor will naturally clean up old stale jobs on first run if they exist — "no retroactive" means no separate migration script.)
- **`AWAITING_AUDIO_EDIT` excluded** — intentionally out of scope. Audio edit is a rare state (user uploaded audio needing trimming) with a different workflow. Can be added later if needed.
- **Separate flag from idle reminder** — the existing 5-minute idle reminder system already sets `state_data.reminder_sent = True` on every job. The 24h expiry warning uses a distinct flag `state_data.expiry_reminder_sent` to avoid collision.
- **`blocking_state_entered_at` resets on re-visit** — if a user opens and closes the review page, `blocking_state_entered_at` may be reset by `_schedule_idle_reminder()`. This is desirable: the user was recently active, so the clock should restart.
- **Configurable thresholds** — `REVIEW_REMINDER_HOURS = 24` and `REVIEW_EXPIRY_HOURS = 48` as module-level constants for easy tuning.

## Architecture

### New Files

| File | Purpose |
|------|---------|
| `backend/workers/stale_review_processor.py` | Core logic: query stale jobs, send reminders, expire |

### Modified Files

| File | Change |
|------|--------|
| `backend/api/routes/internal.py` | Add `POST /internal/process-stale-reviews` endpoint |
| `backend/services/email_service.py` | Add `send_review_reminder()` and `send_review_expired()` methods |
| `infrastructure/__main__.py` | Add Cloud Scheduler job |

### No Changes Needed

- No new Firestore collections or indexes
- No new Cloud Tasks queues
- No model changes — reuses existing `state_data.blocking_state_entered_at`; adds `expiry_reminder_sent` and `expiry_reminder_sent_at` to `state_data` (no schema change needed, state_data is a flexible dict)
- No new env vars or feature flags

## Detailed Design

### 1. Stale Review Processor (`backend/workers/stale_review_processor.py`)

```python
async def process_stale_reviews() -> dict:
    """
    Query for stale review jobs and take action.

    Returns:
        {reminders_sent: int, jobs_expired: int, errors: list[str]}
    """
    # 1. Query Firestore for jobs with status in [AWAITING_REVIEW, IN_REVIEW]
    # 2. For each job:
    #    a. Skip if made_for_you == True
    #    b. Skip if tenant_id is non-empty
    #    c. Skip if no blocking_state_entered_at in state_data
    #    d. Parse blocking_state_entered_at (naive UTC ISO string) to datetime
    #    e. Calculate hours_since_review = now_utc - blocking_state_entered_at
    #    f. If hours_since_review >= REVIEW_EXPIRY_HOURS (48):
    #         - cancel_job(job_id, reason="Review not completed within 48 hours")
    #         - Send expiry notification email with refund confirmation
    #    g. Elif hours_since_review >= REVIEW_REMINDER_HOURS (24) and not expiry_reminder_sent:
    #         - Send reminder email with review link and help offer
    #         - Update state_data: expiry_reminder_sent=True, expiry_reminder_sent_at=now
    #    Note: blocking_state_entered_at is stored as naive UTC ISO string (datetime.utcnow().isoformat())
    # 3. Return summary
```

**Key details:**
- 48h check comes before 24h check — if a job somehow misses the reminder window (e.g., Cloud Run was down), it still gets expired correctly
- Firestore query is by status only; `made_for_you`, `tenant_id`, and time filtering happen in Python (the number of jobs in review at any time is small)
- Uses existing `JobManager.cancel_job()` which already handles state transition + credit refund
- All operations are wrapped in try/except per job — one failure doesn't block processing other jobs

### 2. Internal Endpoint (`backend/api/routes/internal.py`)

```python
@router.post("/process-stale-reviews")
async def process_stale_reviews_endpoint(
    http_request: Request,
    background_tasks: BackgroundTasks,
    auth_data: Tuple[str, UserType, int] = Depends(require_admin)
):
    """
    Process stale review jobs — send reminders and expire old ones.

    Called by Cloud Scheduler (hourly). Can also be triggered manually.
    """
    # Run in background (same pattern as youtube-queue/process)
    # Return immediately with {status: "started"}
```

### 3. Reminder Email — 24h (`EmailService.send_review_reminder()`)

**Subject:** `Reminder: Your karaoke video is waiting for review — {artist} - {title}`

**Content:**
- Friendly reminder that their video is ready for review
- Direct link to review page: `https://gen.nomadkaraoke.com/app/jobs#{job_id}/review`
- Offer of help: "If you're having trouble with the review process, just reply to this email"
- Expiry warning: "If the review isn't completed within the next 24 hours, the job will automatically expire and your credit will be refunded"

**Styling:** Existing yellow alert box pattern from `send_action_reminder()`.

### 4. Expiry Notification Email — 48h (`EmailService.send_review_expired()`)

**Subject:** `Your karaoke video has expired — {artist} - {title}`

**Content:**
- Job was automatically cancelled because review wasn't completed within 48 hours
- Credit has been refunded — show current balance
- They can create a new job anytime
- Offer of help if they had trouble

**Styling:** Informational tone (not alarming — they got their credit back).

### 5. Cloud Scheduler (`infrastructure/__main__.py`)

```python
stale_review_scheduler = cloudscheduler.Job(
    "stale-review-scheduler",
    name="stale-review-hourly",
    description="Send review reminders and expire stale review jobs",
    region=REGION,
    schedule="0 * * * *",  # Every hour on the hour
    time_zone="America/Los_Angeles",
    http_target=cloudscheduler.JobHttpTargetArgs(
        uri="https://api.nomadkaraoke.com/api/internal/process-stale-reviews",
        http_method="POST",
        oidc_token=cloudscheduler.JobHttpTargetOidcTokenArgs(
            service_account_email=backend_service_account.email,
        ),
    ),
    retry_config=cloudscheduler.JobRetryConfigArgs(
        retry_count=1,
        min_backoff_duration="60s",
        max_backoff_duration="300s",
    ),
)
```

## Testing Strategy

### Unit Tests (`backend/tests/test_stale_review_processor.py`)
- Job >48h with no reminder → expires + refunds + sends expiry email
- Job >48h with reminder already sent → expires (doesn't send duplicate reminder)
- Job 24-48h with no reminder → sends reminder, sets expiry_reminder_sent=True
- Job 24-48h with expiry_reminder already sent → skips (no duplicate)
- Job 24-48h with idle reminder_sent=True but expiry_reminder_sent=False → still sends 24h reminder (flags are independent)
- Job <24h → no action
- Made-for-you job >48h → skipped
- Tenant job >48h → skipped
- Job with no blocking_state_entered_at → skipped
- Job with no user_email → skipped (no email sent, but still expires)
- Error in one job doesn't block others
- Return value includes correct counts

### Unit Tests (`backend/tests/test_email_service.py`)
- `send_review_reminder()` renders correct subject, content, review link
- `send_review_expired()` renders correct subject, content, balance info

### Integration Test (endpoint)
- `POST /internal/process-stale-reviews` requires admin auth
- Returns started status, processes in background

## Sequence Diagram

```
Cloud Scheduler (hourly)
  │
  ▼
POST /api/internal/process-stale-reviews
  │
  ▼
process_stale_reviews()
  │
  ├─ Query: status in [awaiting_review, in_review]
  │
  ├─ For each job:
  │   ├─ Skip if made_for_you or tenant
  │   ├─ Calculate age from blocking_state_entered_at
  │   │
  │   ├─ Age >= 48h:
  │   │   ├─ cancel_job() → sets CANCELLED + refunds credit
  │   │   └─ send_review_expired() email
  │   │
  │   └─ Age >= 24h (reminder not sent):
  │       ├─ send_review_reminder() email
  │       └─ Update state_data.expiry_reminder_sent = True
  │
  └─ Return {reminders_sent, jobs_expired, errors}
```
