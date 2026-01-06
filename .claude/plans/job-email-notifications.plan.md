# Plan: Job Email Notifications & Admin Message Tools

**Created:** 2026-01-06
**Branch:** feat/sess-20260106-0116-add-email-button
**Status:** Draft

## Overview

Add comprehensive email notification system for karaoke-gen that:
1. **Auto-sends emails** when jobs complete or need user input (after 5-minute idle threshold)
2. **Uses cloud-stored templates** for the job completion message
3. **Provides admin tools** to copy/send completion messages manually (for Fiverr, etc.)

## Requirements

### Functional Requirements
- [ ] Auto-send job completion email to user (CC: gen@nomadkaraoke.com)
- [ ] Auto-send "action needed" reminder emails when user is idle for 5+ minutes at blocking states (lyrics review, instrumental selection)
- [ ] Store email templates in GCS (editable without code deploy)
- [ ] Admin button: "Copy Message" - copies templated completion message to clipboard
- [ ] Admin button: "Send Email" - prompts for email address, then sends completion message

### Non-Functional Requirements
- [ ] Email sending must not block job completion (async/background)
- [ ] Idle detection via Cloud Tasks scheduled task (not polling)
- [ ] Template changes should take effect without restart (no long-term caching)

## Technical Approach

### 1. Template Storage (GCS)
Store templates in `gs://karaoke-gen-storage/templates/`:
- `job-completion.txt` - Plain text template for job completion
- `job-completion.html` - HTML version (optional, for rich emails)
- `action-needed-lyrics.txt` - Reminder for lyrics review
- `action-needed-instrumental.txt` - Reminder for instrumental selection

**Template Variables:**
- `{name}` - User's display name or "there" if unknown
- `{youtube_url}` - YouTube video URL
- `{dropbox_url}` - Dropbox folder URL
- `{artist}` - Artist name
- `{title}` - Song title
- `{job_id}` - Job ID
- `{review_url}` - Lyrics review URL (for action-needed emails)
- `{instrumental_url}` - Instrumental selection URL
- `{feedback_url}` - Feedback form URL (for completion emails)

### 2. Email Service Extensions

Extend `backend/services/email_service.py`:
- Add `send_job_completion()` method with CC support
- Add `send_action_reminder()` method
- Add `render_template()` helper to fetch & render GCS templates

### 3. Auto-Email Triggers

**Job Completion:**
- Trigger point: When job enters `COMPLETE` status in `job_manager.transition_to_state()`
- Implementation: Call email service async in background task

**Action Reminders (5-min idle detection):**
- When job enters `AWAITING_REVIEW` or `AWAITING_INSTRUMENTAL_SELECTION`:
  - Record `blocking_state_entered_at` timestamp in `state_data`
  - Schedule a Cloud Tasks task for 5 minutes later
- The scheduled task checks if job is still in blocking state and user hasn't started review
- If idle, send reminder email and set `reminder_sent` flag to prevent duplicates
- **Only one reminder per job** - if `reminder_sent` is true, skip sending

### 4. Admin UI (Frontend)

Add admin-only buttons to completed jobs in `OutputLinks.tsx`:
- "Copy Message" button - fetches rendered template, copies to clipboard
- "Send Email" button - opens dialog for email input, calls API to send

### 5. Backend API Endpoints

New endpoints in `backend/api/routes/admin.py`:
- `GET /api/admin/jobs/{job_id}/completion-message` - Returns rendered completion message
- `POST /api/admin/jobs/{job_id}/send-completion-email` - Sends email to specified address

New internal endpoint for scheduled reminder:
- `POST /api/internal/jobs/{job_id}/check-idle-reminder` - Called by Cloud Tasks

## Implementation Steps

### Phase 1: Template Infrastructure
1. [ ] Upload initial template to GCS from user's existing template file
2. [ ] Create `template_service.py` to fetch/render templates from GCS
3. [ ] Add unit tests for template rendering

### Phase 2: Email Extensions
4. [ ] Extend `email_service.py` with CC support in `send_email()`
5. [ ] Add `send_job_completion()` method
6. [ ] Add `send_action_reminder()` method
7. [ ] Add unit tests for new email methods

### Phase 3: Auto-Email on Completion
8. [ ] Modify `job_manager.py` to trigger completion email when entering COMPLETE
9. [ ] Add integration test for completion email trigger

### Phase 4: Idle Reminder System
10. [ ] Add `blocking_state_entered_at` field tracking to job state transitions
11. [ ] Create Cloud Tasks queue for idle reminders (`idle-reminder-queue`)
12. [ ] Schedule 5-minute delayed task when entering blocking states
13. [ ] Implement `/api/internal/jobs/{job_id}/check-idle-reminder` endpoint
14. [ ] Add tests for idle reminder flow

### Phase 5: Admin API
15. [ ] Add `GET /api/admin/jobs/{job_id}/completion-message` endpoint
16. [ ] Add `POST /api/admin/jobs/{job_id}/send-completion-email` endpoint
17. [ ] Add tests for admin endpoints

### Phase 6: Admin UI
18. [ ] Add `isAdmin` check to `OutputLinks.tsx` component
19. [ ] Add "Copy Message" button (admin-only) with loading state
20. [ ] Add "Send Email" button (admin-only) with email input dialog
21. [ ] Add E2E test for admin buttons

### Phase 7: Documentation
22. [ ] Document template management in docs/DEVELOPMENT.md
23. [ ] Update docs/API.md with new endpoints

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/services/template_service.py` | Create | GCS template fetching and rendering |
| `backend/services/email_service.py` | Modify | Add CC support, completion/reminder methods |
| `backend/services/job_manager.py` | Modify | Trigger emails on state transitions |
| `backend/services/worker_service.py` | Modify | Add idle reminder task scheduling |
| `backend/api/routes/admin.py` | Modify | Add completion message/email endpoints |
| `backend/api/routes/internal.py` | Modify | Add idle reminder check endpoint |
| `backend/models/job.py` | Modify | Add `blocking_state_entered_at`, `reminder_sent_at` to state_data docs |
| `infrastructure/__main__.py` | Modify | Add idle-reminder-queue Cloud Tasks queue |
| `frontend/components/job/OutputLinks.tsx` | Modify | Add admin-only Copy/Send buttons |
| `frontend/lib/api.ts` | Modify | Add API methods for admin endpoints |
| `tests/unit/test_template_service.py` | Create | Template rendering tests |
| `tests/unit/test_email_service_completion.py` | Create | Completion email tests |

## Testing Strategy

**Unit Tests:**
- Template rendering with all variable combinations
- Email service CC support
- Completion/reminder email generation
- Idle detection logic

**Integration Tests:**
- Full flow: job completion → email sent
- Idle reminder: job enters blocking state → 5 min → reminder sent
- Admin endpoint authentication

**Manual Testing:**
- Verify emails render correctly in Gmail/Outlook
- Test admin buttons on completed job
- Test clipboard copy works across browsers

## Open Questions

- [x] Where to store templates? → **GCS bucket: `gs://karaoke-gen-storage/templates/`**
- [x] Should we rate-limit reminder emails? → **Max 1 reminder per job** (send once after 5 min idle, then stop)
- [x] Should completion emails also include a feedback request link? → **Yes, for all users**

## Rollback Plan

1. **Email sending failures** - All email sends are async/fire-and-forget; job completion unaffected
2. **Template fetch failures** - Fall back to hardcoded template in code
3. **Idle reminder failures** - Jobs continue normally; users can still access review UI manually
4. **Feature flag** - Add `ENABLE_AUTO_EMAILS=true/false` env var for quick disable

## Template Content

Initial template (from user's file, with feedback link added):

```
Hi {name},

Thanks for your order!

Here's the link for the karaoke video published to YouTube:
{youtube_url}

Here's the dropbox folder with all the finished files and source files, including:
- "(Final Karaoke Lossless).mkv": combined karaoke video in 4k H264 with lossless FLAC audio
- "(Final Karaoke).mp4": combined karaoke video with title/end screen in 4k H264/AAC
- "(Final Karaoke 720p).mp4": combined karaoke video in 720p H264/AAC (smaller file for older systems)
- "(With Vocals).mp4": sing along video in 4k H264/AAC with original vocals
- "(Karaoke).mov": karaoke video output from MidiCo (no title/end screen)
- "(Title).mov"/"(End).mov": title card and end screen videos
- "(Final Karaoke CDG).zip": CDG+MP3 format for older/commercial karaoke systems
- "(Final Karaoke TXT).zip": TXT+MP3 format for Power Karaoke
- stems/*.flac: various separated instrumental and vocal audio stems in lossless format
- lyrics/*.txt song lyrics from various sources in plain text format

{dropbox_url}

Let me know if anything isn't perfect and I'll happily tweak / fix, or if you need it in any other format I can probably convert it for you!

If you have a moment, I'd really appreciate your feedback (takes 2 minutes):
{feedback_url}

Thanks again and have a great day!
-Andrew
```

**Note:** The `{feedback_url}` placeholder will be populated with the feedback form URL. If no feedback URL is configured, this section can be omitted from the rendered output.

## Updating Templates

To update email templates after deployment:

```bash
# Upload new template
gsutil cp my-template.txt gs://karaoke-gen-storage/templates/job-completion.txt

# Templates are fetched fresh on each use (no caching in code)
# Changes take effect immediately
```
