# Edit Input Audio — Master Plan

**Date:** 2026-03-08
**Branch:** `feat/sess-20260308-1232-edit-input-audio`
**Status:** Complete

## Problem

A common request from clients is to use a live performance from YouTube but skip the first MM:SS of talking/intro before the band starts playing. Currently there's no way to trim, cut, mute, or otherwise edit the input audio before it enters the processing pipeline (separation, transcription, etc.). Users must provide a pre-edited file themselves or accept the full audio as-is.

## Solution Overview

Add an optional **"Edit Input Audio"** phase after audio download completes but before separation/transcription begins. Users opt in during the guided job creation flow. When enabled, the job pauses at a new blocking state (`awaiting_audio_edit`) where users can trim, cut, mute, or join audio using a waveform-based editor UI similar to the existing instrumental review UI. The edited audio replaces the original as input for all subsequent processing stages.

The editor includes full undo/redo support and session persistence (auto-save + restore) following the same pattern built for lyrics review sessions.

## Phases

| Phase | Title | Scope | Dependencies |
|-------|-------|-------|-------------|
| 1 | [State Machine & Backend API](2026-03-08-edit-input-audio-phase1-backend.md) | New job state, audio processing endpoints (trim/cut/mute/join), waveform data generation for input audio | None |
| 2 | [Session Persistence](2026-03-08-edit-input-audio-phase2-sessions.md) | Audio edit session save/restore, Firestore subcollection, GCS storage, deduplication | Phase 1 |
| 3 | [Job Creation Flow Integration](2026-03-08-edit-input-audio-phase3-creation-flow.md) | Opt-in toggle during "Choose Audio" step, job creation with `requires_audio_edit` flag | Phase 1 |
| 4 | [Audio Editor UI](2026-03-08-edit-input-audio-phase4-editor-ui.md) | Waveform viewer, trim/cut/mute/join controls, undo/redo, playback, original vs edited tabs | Phase 1 |
| 5 | [Session Restore & Admin Dashboard](2026-03-08-edit-input-audio-phase5-restore-admin.md) | Auto-save hook, restore dialog on page load, admin dashboard for viewing/replaying audio edit sessions | Phases 2, 4 |

## Key Design Decisions

1. **Opt-in, not default** — Most users don't need audio editing. The option appears as a secondary question after audio selection (for all sources including file uploads), keeping the default flow unchanged.

2. **Server-side audio processing** — Trim, cut, mute, and join operations are performed server-side via FFmpeg. The frontend sends edit descriptors (timestamps, regions) and the backend produces the edited audio file. This avoids loading large audio files into the browser and ensures consistent processing.

3. **Waveform rendered client-side** — Like the instrumental review UI, the frontend renders waveforms on canvas using amplitude data from the backend API. After each edit, the backend returns updated waveform data for the edited audio. All playback uses OGG Opus (not FLAC) to keep streaming fast.

4. **Edit operations are non-destructive** — Each edit creates a new version. The original audio is always preserved. Users can undo/redo freely and always compare original vs edited.

5. **Session persistence follows lyrics review pattern** — Firestore subcollection + GCS for full edit history, deduplication via hash, auto-save every N edits + on page hide.

6. **Blocking state with notifications** — The `awaiting_audio_edit` state follows the same pattern as `awaiting_review`: generates a review token, sends an email notification, and waits for user action.

## Architecture Overview

```
Job Creation (with requires_audio_edit=true)
    ↓
DOWNLOADING / DOWNLOADING_AUDIO (existing)
    ↓
Audio download complete
    ↓
AWAITING_AUDIO_EDIT  ← NEW blocking state
    ↓
[User opens audio editor UI]
    ↓
IN_AUDIO_EDIT  ← NEW (user actively editing)
    ↓
[User submits edited audio]
    ↓
AUDIO_EDIT_COMPLETE  ← NEW
    ↓
[Edited audio saved to GCS, replaces input_media_gcs_path]
    ↓
Trigger workers (separation + transcription) — existing flow continues
```

## Additional Decisions

- **Available for file uploads too** — Users may not be comfortable with external audio editors; our in-app editing makes basic operations accessible.
- **Server-side only processing** — All edit operations use FFmpeg server-side. No client-side Web Audio API. This ensures the preview is perfectly representative of the final result.
- **Admin can reset to audio edit** — Admins can reset a job to `awaiting_audio_edit` via AdminJobActions, useful when a client requests a trim after job creation.
- **Join limited to start/end** — No arbitrary insert. Users can work around by cutting and re-joining.
- **Playback uses OGG Opus** — All preview audio is transcoded to OGG Opus before streaming to the browser. No FLAC streaming.

## Implementation Approach

Implement each phase sequentially on this branch. After each phase:
1. Run `/test-review` and `/docs-review`
2. Commit and push

After all phases complete, test locally with a real audio file (frontend + backend) before `/shipit`.

## GCS File Structure (additions)

```
jobs/{job_id}/
├── input/
│   ├── original.flac              # Original downloaded audio (never modified)
│   └── edited.flac                # Edited audio (after user submits)
├── audio_edit/
│   ├── waveform_original.json     # Cached waveform data for original
│   ├── waveform_edited.json       # Cached waveform data for current edit
│   └── preview_{operation_id}.flac # Temporary previews during editing
├── audio_edit_sessions/
│   └── {session_id}.json          # Full edit history for session restore
└── ... (existing structure)
```
