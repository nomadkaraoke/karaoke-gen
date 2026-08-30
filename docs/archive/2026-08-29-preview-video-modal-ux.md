# Preview Video Modal UX overhaul (lyrics review "Review Changes" modal)

Date: 2026-08-29 · Version: 0.210.0

Five UX improvements to the preview-video modal shown when the reviewer clicks
"Preview Video" at the end of lyrics review (`ReviewChangesModal`).

## What changed

1. **Unified loading experience** (`PreviewVideoSection.tsx`)
   - The two loading states (`generating`, `encoding`) now share one centred
     spinner layout; only the stage message changes ("Preparing your preview…"
     → "Encoding preview video…" → slow-encoder hint). The old small top-left
     "Generating preview video…" row was removed.

2. **Audio toggle: Original ↔ Auto-selected instrumental** (`PreviewVideoSection.tsx`)
   - The preview mp4 always carries the original (with-vocals) audio. A hidden
     `<audio>` element loads the auto-selected instrumental stem
     (`data.instrumental_options[…].audio_url`, already provided by the combined
     review correction-data endpoint) and is kept in lock-step with the video
     (play/pause/seek/ratechange/drift within 0.25s). Switching mutes the video
     and unmutes the stem in place, so playback position is preserved mid-play.
   - Modal title changed from "Preview Video (With Vocals)" → "Preview Video".
   - Toggle only renders when an instrumental `audio_url` is available.

3. **Removed low-value text** (`ReviewChangesModal.tsx`)
   - Deleted the "No manual corrections detected…" line and the "Total segments: X"
     line (and their i18n keys). The "Manual corrections detected…" note is kept
     but now only shows when the reviewer actually made manual edits.

4. **Backing-vocals waveform** (`BackingVocalsWaveform.tsx`, new)
   - When the instrumental auto-selector kept the backing vocals
     (`auto_approval.backing.resolved_selection === 'with_backing'` and the
     decision was confident), a thin 35px canvas waveform of the backing-vocals
     stem renders inside the "We'll keep the backing vocals…" card.
   - Amplitudes come from the existing `GET /api/review/{jobId}/waveform-data`
     endpoint, which returns the **backing_vocals** stem envelope. Colour matches
     the pink backing-vocals highlight on the full instrumental screen.
   - Clicking seeks the preview video to that point and switches audio to the
     instrumental — wired via an imperative handle
     (`PreviewVideoHandle.switchToInstrumentalAndSeek`) on `PreviewVideoSection`.

5. **"Saving" state no longer flashes back to the review screen** (`LyricsAnalyzer.tsx`)
   - `handleSubmitToServer` previously closed the modal + cleared `isSubmitting`
     right after `submitCorrections`, then ran the slow `getCorrectionData` +
     `completeReview` with the review screen visible. Now the modal stays in its
     "Saving…" state through `completeReview`; the full-screen "Review Submitted"
     view (which returns early) replaces it. Branches that navigate to the
     instrumental screen still close the modal first (they're instant).

## Notable implementation details / gotchas

- `getWaveformData` was **not** on the `createLyricsReviewApiClient` factory
  (only on the standalone `lyricsReviewApi` object); it was added to the factory
  + `LyricsReviewApiClient` interface.
- `PreviewVideoSection` is now a `forwardRef` component. Tests that mock it must
  forward the ref (see `ReviewChangesModal.test.tsx`).
- `video.play()` / `audio.play()` are called with `?.catch()` because jsdom's
  `play()` returns `undefined`, not a Promise.
- i18n: en.json keys added (`previewVideo.audioLabel/audioOriginal/audioInstrumental/
  backingVocalsWaveformHint`, `modals.reviewChanges.previewTitle`), removed
  (`previewWithVocals`, `noManualCorrections`, `totalSegments`); all 32 non-en
  locales synced via `scripts/translate.py --target all`.

## Needs live verification (Andrew)

- Audio toggle sync tightness on a real preview (stems align sample-for-sample
  with the original, so drift should be minimal).
- Backing-vocals waveform only appears for `with_backing` auto-selections; click
  seeks + switches audio correctly.
