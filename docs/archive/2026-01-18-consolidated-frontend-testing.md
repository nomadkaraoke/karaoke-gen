# Consolidated Frontend Testing Guide

This document explains how to test the consolidated Next.js frontend against the old standalone React/Vite frontends to ensure feature parity.

## Background

The lyrics review and instrumental review UIs are being migrated from standalone React/Vite/MUI frontends to a consolidated Next.js/Tailwind/shadcn frontend. This guide helps agents verify all functionality works correctly by comparing the old and new code paths.

## Directory Structure

- **Old code path**: `/Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen`
- **New code path**: `/Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen-consolidate-frontends`

## Setting Up Test Jobs

### Option 1: Run Old and New Side-by-Side (Recommended)

This allows you to compare behavior between old and new UIs simultaneously.

#### Terminal 1: Old Code Path (Port 8000)

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen
source venv/bin/activate
karaoke-gen \
  --style_params_json="/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/NomadBranding/karaoke-prep-styles-nomad.json" \
  --enable_cdg \
  --enable_txt \
  /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen/input/waterloo30sec.flac \
  ABBA Waterloo
```

This will start the lyrics review server on port 8000.

**Old Lyrics Review UI URL:**
```
http://localhost:8000/?baseApiUrl=http%3A%2F%2Flocalhost%3A8000%2Fapi&audioHash=3d965459b4d5ee0c2b4478ba1fd2a000
```

#### Terminal 2: New Code Path (Port 8001)

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen-consolidate-frontends
source venv/bin/activate
LYRICS_REVIEW_PORT=8001 karaoke-gen \
  --style_params_json="/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/NomadBranding/karaoke-prep-styles-nomad.json" \
  --enable_cdg \
  --enable_txt \
  /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen/input/waterloo30sec.flac \
  ABBA Waterloo
```

**New Lyrics Review UI URL:**
```
http://localhost:8001/app/jobs/local/review
```

### Option 2: Single Test (New Code Path Only)

If you only need to test the new UI:

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen-consolidate-frontends
source venv/bin/activate
karaoke-gen \
  --style_params_json="/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/NomadBranding/karaoke-prep-styles-nomad.json" \
  --enable_cdg \
  --enable_txt \
  /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen/input/waterloo30sec.flac \
  ABBA Waterloo
```

**New Lyrics Review UI URL:**
```
http://localhost:8000/app/jobs/local/review
```

## Rebuilding Frontend After Code Changes

When you make changes to the frontend code in `frontend/`, you need to rebuild the static export for the local Python server to serve.

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen-consolidate-frontends/frontend
npm run build
```

This builds the Next.js static export to `karaoke_gen/nextjs_frontend/out/`.

After rebuilding, simply **reload the browser** at the review UI URL to see your changes.

## Key Files

### Frontend Components (New Code Path)

- `frontend/app/app/jobs/[[...slug]]/page.tsx` - Route handler for lyrics/instrumental review
- `frontend/app/app/jobs/[[...slug]]/client.tsx` - Main client component
- `frontend/components/lyrics-review/LyricsAnalyzer.tsx` - Main lyrics review component
- `frontend/components/lyrics-review/Header.tsx` - Header with metrics and controls
- `frontend/components/lyrics-review/modals/ReviewChangesModal.tsx` - Preview video modal
- `frontend/components/lyrics-review/PreviewVideoSection.tsx` - Video preview component
- `frontend/lib/local-mode.ts` - Local mode detection logic

### Old Frontend (for reference)

- `lyrics_transcriber_temp/lyrics_transcriber/frontend/` - Old React/Vite/MUI frontend
- `lyrics_transcriber_temp/lyrics_transcriber/review/server.py` - Python review server

### E2E Tests

- `frontend/e2e/regression/lyrics-review.spec.ts` - Comprehensive E2E tests (20 tests)

## Running E2E Tests

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-multiagent/karaoke-gen-consolidate-frontends/frontend

# Run all lyrics-review tests
E2E_PORT=3001 npm run test:e2e -- --grep "lyrics-review"

# Run with UI for debugging
E2E_PORT=3001 npm run test:e2e:ui -- --grep "lyrics-review"
```

Note: Use port 3001 if another Next.js app is running on port 3000.

## Functionality Checklist

### Lyrics Review UI

- [ ] Page loads with transcription data
- [ ] Metrics display (Total Words, Corrections Made, etc.)
- [ ] Transcription view shows word-level segments
- [ ] Reference lyrics panel works
- [ ] Handlers panel shows correction handlers with toggles
- [ ] Audio player loads and plays audio
- [ ] Audio player controls (play/pause, seek, skip)
- [ ] Time display shows current/total time
- [ ] Edit mode selector (highlight/split/replace)
- [ ] Undo/redo buttons work
- [ ] Find/replace modal opens
- [ ] Timing offset adjustment works
- [ ] Preview Video button opens modal
- [ ] Preview video generates and plays in modal
- [ ] "Complete Review" submits corrections
- [ ] Mobile responsive layout

### Instrumental Review UI

- [ ] Page loads with instrumental options
- [ ] Waveform visualization displays
- [ ] Audio playback for each option
- [ ] Selection between clean/backing vocals versions
- [ ] Submit selection works

## Known Issues / Fixed Issues

### Fixed in This Session

1. **Preview Video Modal** - The modal was showing stats only without video preview. Fixed by integrating `PreviewVideoSection` component.

2. **Local Mode Detection** - Dev/testing ports (3000-3002) weren't recognized as local mode. Fixed by adding `DEV_TESTING_PORTS` to `local-mode.ts`.

3. **Audio Hash Sourcing** - Audio player wasn't loading because `audioHash` was undefined. Fixed to use `correctionData.metadata?.audio_hash`.

## Commits in This Branch

Recent commits on `feat/sess-20260109-1931-consolidate-frontends`:

- `ab6b11fa` - fix(lyrics-review): Add video preview to ReviewChangesModal
- `a729e027` - feat(e2e): Make test port configurable and fix E2E tests
- `a4877861` - feat(lyrics-review): Fix audioHash sourcing and add E2E tests
- `4feb687a` - fix(lyrics-review): Match styling with old MUI-based UI
- `f94318b8` - feat: Unify local CLI frontend with Next.js app

## Next Steps

1. Test all functionality in the checklist above
2. Compare behavior with old UI for any discrepancies
3. Fix any issues found
4. Run full E2E test suite
5. Create PR when ready
