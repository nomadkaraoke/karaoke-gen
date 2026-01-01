# Worker Timeout Fixes

**Date**: 2026-01-01
**PRs**: #153, #154, #155
**Status**: Complete

## Problem

Jobs were failing at different stages due to worker timeouts:

1. **Lyrics worker** - Timeout after 600 seconds (10 minutes) during transcription
2. **Audio worker** - Killed after 10 minutes by Cloud Tasks default `dispatch_deadline`
3. **Video worker** - Timeout after 30 minutes (Cloud Run service timeout)

## Root Causes

### Lyrics Transcription Timeout (PR #153)
- `TRANSCRIPTION_TIMEOUT_SECONDS` was 600 (10 minutes)
- AudioShake transcription + agentic correction can take 15+ minutes for complex songs
- **Fix**: Increased to 1200 seconds (20 minutes)

### Cloud Tasks dispatch_deadline (PR #154)
- Cloud Tasks HTTP handlers have a default `dispatch_deadline` of 10 minutes
- Audio separation via Modal API takes 15-20+ minutes
- Worker was killed before Modal could complete, but no error surfaced
- **Symptom**: Job stuck at `downloading` with `audio_complete: false`
- **Fix**: Added explicit `dispatch_deadline` per worker type:
  ```python
  WORKER_DISPATCH_DEADLINES = {
      "audio": 1800,       # 30 min
      "lyrics": 1500,      # 25 min
      "screens": 600,      # 10 min
      "render-video": 1800,  # 30 min
      "video": 1800,       # 30 min
  }
  ```

### Video Encoding Timeout (PR #155)
- Cloud Run service timeout is 1800 seconds (30 minutes)
- `KaraokeFinalise.process()` takes 30-40+ minutes for average songs
- Video encoding includes: 4 format encodings, CDG generation, TXT packages
- **Symptom**: Job stuck at `encoding` with `updated_at` frozen
- **Fix**: Enabled `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true` in `cloudbuild.yaml`
- Cloud Run Jobs support up to 1-hour execution (configured in `infrastructure/__main__.py`)

## Key Insight: dispatch_deadline vs Cloud Run Timeout

These are different timeouts:

| Timeout | Default | Max | Controls |
|---------|---------|-----|----------|
| Cloud Tasks `dispatch_deadline` | 10 min | 30 min | How long Cloud Tasks waits for HTTP response |
| Cloud Run service timeout | 5 min | 60 min | How long Cloud Run allows request to run |
| Cloud Run Jobs timeout | 10 min | 24 hr | How long a batch job can run |

For worker tasks that take >30 minutes, Cloud Tasks HTTP targets can't work. Use Cloud Run Jobs instead.

## Verification

Job `eb725b2c` completed successfully through the full pipeline:
- Audio separation: Complete
- Lyrics transcription: Complete
- Video encoding: Complete
- All 4 video formats generated
- All stems, lyrics, and packages available

## Files Changed

- `backend/services/worker_service.py` - Added `dispatch_deadline` to Cloud Tasks
- `cloudbuild.yaml` - Enabled `USE_CLOUD_RUN_JOBS_FOR_VIDEO=true`
- `pyproject.toml` - Version bumps (0.86.4, 0.86.5)

## Lessons Learned

1. **Cloud Tasks dispatch_deadline is separate from Cloud Run timeout** - Both need to be configured for long-running workers
2. **Default dispatch_deadline (10 min) is often insufficient** - Modal API, video encoding, and other heavy processing need more time
3. **Cloud Run Jobs for truly long tasks** - For anything that might take >30 minutes, Cloud Tasks HTTP targets won't work
4. **E2E tests reveal timeout issues** - Production E2E tests caught these issues that unit tests missed
