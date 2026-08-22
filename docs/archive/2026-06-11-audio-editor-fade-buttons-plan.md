# Audio Editor: Fade In / Fade Out Buttons

**Date:** 2026-06-11
**Branch:** `feat/sess-20260611-1250-audio-fade`

## Goal

Add **Fade In** and **Fade Out** action buttons to the audio editor toolbar so users
can ramp the volume of a selected region gracefully instead of a hard cut.

**Primary use case (user quote):** "when cutting off a long intro from a song, we'll
likely want it to fade in gracefully over a few seconds rather than come in really loud
at the point it was trimmed to."

## Design Decisions

- **Edge-gated display**, mirroring the existing `Trim Start` / `Trim End` buttons:
  - **Fade In** appears when the selection starts near the beginning (`startSeconds < 1`).
  - **Fade Out** appears when the selection ends near the end (`endSeconds > duration - 1`).
  - This matches the real use case (fade the new start after trimming an intro; fade the
    new end after trimming an outro) and avoids `afade`'s surprising behaviour of
    silencing audio outside the fade window for mid-track selections.
- **Duration-preserving** (like `mute`) — fades only adjust volume, they don't change length.
- Implemented with FFmpeg's `afade` filter: `afade=t=in|out:st={start}:d={end-start}`.
- No new keyboard shortcut (a single key can't disambiguate in vs out cleanly).

## Changes

### Backend
1. `backend/services/audio_edit_service.py`
   - Add `fade_region(input_path, start, end, direction, output_path)` — validates
     `direction in {"in","out"}`, runs `afade`, returns metadata (duration preserved).
   - Dispatch `fade_in` / `fade_out` in `apply_edit`.
2. `backend/api/routes/review.py`
   - Add `"fade_in"`, `"fade_out"` to `valid_operations`.

### Frontend
3. `frontend/components/audio-editor/AudioEditor.tsx`
   - `handleFadeIn()` / `handleFadeOut()` → `handleApply("fade_in"|"fade_out", {start_seconds, end_seconds})`.
   - Edge-gated toolbar buttons with `TrendingUp` / `TrendingDown` icons.
4. i18n: add `fadeIn`, `fadeOut`, `fadeInSelection`, `fadeOutSelection` to
   `frontend/messages/en.json`, then `translate.py --target all` (33 locales).

## Tests
- **Backend unit** (`test_audio_edit_service.py`): `fade_region` in/out ffmpeg args,
  duration preserved, invalid direction rejected, `apply_edit` dispatch for fade_in/out.
- **Backend route** (`test_audio_edit_routes.py`): fade_in/fade_out accepted (not "Invalid operation").
- **Frontend** (`AudioEditor.test.tsx`): Fade In button renders for a start-anchored
  selection and calls `applyAudioEdit` with `fade_in`.

## Out of Scope (possible future work)
- Mid-track fades (would need a `volume` envelope filter, not `afade`).
- Configurable fade curve / duration input box.
