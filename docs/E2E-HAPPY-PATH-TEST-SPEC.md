# E2E Happy Path Test Specification

> **STATUS**: ✅ **PASSING** - Test completes all 12 steps in ~38 minutes. Last verified 2026-01-02.
>
> **IMPORTANT**: This document defines the requirements for a comprehensive end-to-end test that validates the entire karaoke generation flow with REAL user interactions only. NO API shortcuts allowed.

## Overview

**Goal**: A single Playwright test that validates the complete karaoke generation journey from first-time user signup through to distribution, using only browser interactions like a real user would.

**Environment**: Production (`gen.nomadkaraoke.com` / `api.nomadkaraoke.com`)

**Automation**: Runs daily via GitHub Actions and can be triggered on-demand.

## Test Requirements

### 1. New User Registration (testmail.app)

- [ ] Navigate to landing page (`gen.nomadkaraoke.com`)
- [ ] Click "Sign In" or "Get Started" button
- [ ] Create a NEW testmail.app inbox (fresh email address for each test run)
- [ ] Enter the test email in the beta enrollment form
- [ ] Submit the form
- [ ] Wait for welcome/magic-link email in testmail.app inbox
- [ ] Extract the magic link from the email
- [ ] Click/navigate to the magic link to authenticate
- [ ] Verify user is logged in and has credits

### 2. Create Karaoke Job (UI Only)

- [ ] Navigate to the karaoke creation page
- [ ] Enter artist name: `piri` (test song with cached results)
- [ ] Enter song title: `dog`
- [ ] Click "Search" or equivalent button
- [ ] Wait for search results to appear in the UI

### 3. Audio Selection (UI Only)

- [ ] Audio selection dialog/panel should appear
- [ ] View available audio options in the UI
- [ ] Click to select the first/best audio option
- [ ] Confirm the selection via UI button
- [ ] Wait for job to transition to processing state

### 4. Wait for Lyrics Transcription

- [ ] Monitor job status in the UI (not via API polling)
- [ ] Wait for job to reach "awaiting lyrics review" state
- [ ] This may take 10-15+ minutes for audio download + transcription

### 5. Lyrics Review (FULL UI Interaction)

> **CRITICAL**: This MUST be done through the UI, not via API calls

- [ ] Click to open the lyrics review interface
- [ ] Verify lyrics are displayed in the editor
- [ ] Interact with the lyrics editor:
  - [ ] View synced lyrics with timestamps
  - [ ] Optionally make a small edit to prove UI interaction works
  - [ ] Preview the video if preview functionality exists
- [ ] Click "Approve" / "Submit" / "Complete Review" button in UI
- [ ] Wait for job to transition to next state

### 6. Instrumental Selection (FULL UI Interaction)

> **CRITICAL**: This MUST be done through the UI, not via API calls

- [ ] Wait for instrumental options to become available
- [ ] Instrumental selection dialog/panel should appear in UI
- [ ] View available instrumental options (e.g., "clean", "with backing vocals")
- [ ] Click to select the preferred instrumental option
- [ ] Confirm selection via UI button
- [ ] Wait for job to transition to video rendering state

### 7. Wait for Video Rendering & Encoding

- [ ] Monitor job progress in the UI
- [ ] Wait for video rendering to complete
- [ ] Wait for final encoding to complete
- [ ] Job should reach "complete" status

### 8. Verify Completion & Downloads

- [ ] Verify job shows as "complete" in the UI
- [ ] Verify download links are visible and clickable
- [ ] Click at least one download link to verify it works
- [ ] Verify the downloaded file is valid (non-zero size, correct format)

### 9. Distribution Verification & Cleanup

> **CRITICAL**: Test MUST clean up after itself to avoid polluting production distribution channels

- [ ] Verify job.state_data contains distribution results:
  - [ ] `brand_code` - The generated brand code (e.g., "NOMAD-1234")
  - [ ] `youtube_url` - YouTube video URL (if YouTube enabled)
  - [ ] `dropbox_link` - Dropbox shared link (if Dropbox enabled)
  - [ ] `gdrive_files` - Google Drive file IDs (if GDrive enabled)
- [ ] **CLEANUP** - After verification, delete all distributed content:
  - [ ] Delete YouTube video via API (using stored youtube_url)
  - [ ] Delete Dropbox folder via API (using brand_code + dropbox_path)
  - [ ] Delete Google Drive files via API (using gdrive_files IDs)
- [ ] Delete the test job from the system (GCS + Firestore)

## Technical Requirements

### Timeouts

| Stage | Timeout |
|-------|---------|
| Page navigation | 30 seconds |
| Email arrival | 120 seconds |
| Audio search | 60 seconds |
| Lyrics transcription | 20 minutes |
| Video rendering | 15 minutes |
| Final encoding | 10 minutes |
| **Total test** | **60 minutes** |

### Environment Variables

```bash
TESTMAIL_API_KEY=xxx           # Required - for email testing
TESTMAIL_NAMESPACE=xxx         # Required - testmail.app namespace
# No pre-auth tokens - test handles full signup
```

### Test Song

Use `piri - dog` because:
- Has cached flacfetch results (faster audio search)
- Known to work reliably
- Short song = faster processing

### Screenshots & Artifacts

Capture screenshots at each major milestone:
1. Landing page loaded
2. Beta form submitted
3. Email received
4. Logged in successfully
5. Job created
6. Audio selected
7. Lyrics review opened
8. Lyrics approved
9. Instrumental selected
10. Job completed
11. Download verified

### Logging

Log detailed progress at each step with timestamps for debugging.

## GitHub Actions Workflow

**File**: `.github/workflows/e2e-happy-path.yml`

### Triggers

1. **Daily schedule**: Run at 6 AM UTC every day
2. **Manual dispatch**: Can be triggered on-demand with optional parameters
3. **Label trigger**: When `e2e-full` label added to PR

### Secrets Required

- `TESTMAIL_API_KEY` - For email testing
- `TESTMAIL_NAMESPACE` - testmail.app namespace

### Workflow Steps

1. Checkout code
2. Setup Node.js
3. Install dependencies
4. Install Playwright browsers
5. Run the e2e happy path test
6. Upload artifacts (screenshots, videos, logs)
7. Report results

### Success Criteria

- All UI interactions complete successfully
- Job reaches "complete" status
- Download link works
- No JavaScript errors in console
- All screenshots captured

## What This Test Proves

When this test passes, it proves:

1. **User signup works** - New users can register via magic link
2. **Authentication works** - Email verification and login flow functional
3. **Job creation works** - Can search and create karaoke jobs
4. **Audio pipeline works** - Audio search, selection, and download functional
5. **Transcription works** - Lyrics transcription completes successfully
6. **Lyrics review UI works** - Users can review and approve lyrics
7. **Instrumental selection UI works** - Users can select instrumental versions
8. **Video rendering works** - Full video generation pipeline functional
9. **Downloads work** - Users can download their completed karaoke videos
10. **End-to-end integration** - All systems work together correctly

## Anti-Patterns to Avoid

1. **NO API calls to complete review** - Must click buttons in UI
2. **NO API calls to select instrumental** - Must click buttons in UI
3. **NO pre-authenticated tokens** - Must go through full signup
4. **NO skipping steps** - Every user-facing interaction must happen
5. **NO hardcoded waits** - Use proper Playwright waiting strategies

## File Location

**Test file**: `frontend/e2e/production/happy-path-real-user.spec.ts`

This is separate from the existing `full-user-journey.spec.ts` which uses API shortcuts.

## Iteration Plan

1. ~~Create initial test with all steps stubbed~~
2. ~~Implement each section one at a time~~
3. ~~Run via GHA workflow~~
4. ~~Debug failures using artifacts~~
5. ~~Iterate until fully passing~~ ✅ Completed 2026-01-02
6. Add to required CI checks once stable

## Key Fixes Applied

During iteration, three issues were fixed:

1. **STEP 8 status detection**: "rendering" status comes BEFORE instrumental selection, not after. Changed to only skip on "encoding".
2. **Instrumental page navigation**: Changed `waitUntil: 'networkidle'` to `'load'` because audio players prevent networkidle.
3. **Download verification**: Changed HEAD to GET request with `maxRedirects: 0` since download endpoint doesn't support HEAD.

See [LESSONS-LEARNED.md](LESSONS-LEARNED.md) for details.

---

*Last updated: 2026-01-02*
*Author: Claude (via Andrew's request)*
