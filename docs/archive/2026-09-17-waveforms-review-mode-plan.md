# Lyrics Review "Waveforms" mode — implementation plan

**Date:** 2026-09-17
**Repo:** karaoke-gen (frontend)
**Branch:** feat/sess-20260917-0028-waveforms-review-mode

## Problem

In the lyrics review UI, the `Synced Lyrics` panel has a `Simple` / `Advanced` toggle.
Advanced draws each line's words as flex-weighted pills (widths ∝ duration) but shows **no
vocal waveform**. To actually tell whether a word's timing is well-aligned, the reviewer has
to open the **Edit Segment** modal for nearly every line — the only place the vocal waveform is
visible. That's the friction.

**Motivating case** (from the modal): a segment `No looking back` where the `looking` bar runs
to ~142.4s but the vocal energy clearly dies at ~142.3s with a visible gap after — an over-long
trailing word only catchable with the waveform. The goal: spot that at a glance for **every**
segment, without opening a modal per line.

## Solution

Add a third view mode, **Waveforms**, to the `Synced Lyrics` toggle. It renders a compact copy
of the Edit Segment timeline **inline for every segment** — resizable/draggable word bars over
the segment's vocal waveform, with the 1s buffer padding + start/end boundary lines + greyed
neighbouring-segment words, exactly as the modal does — minus the per-row time ruler.

### Decisions (confirmed with Andrew)

1. **Edit scope:** drag/resize word bars inline → persists **timing** directly. Text edits and
   add/split/merge/delete-word still open the existing Edit modal (via the segment number or an
   edit affordance on the row). Reuses `TimelineEditor` + the segment-commit path as-is.
2. **Time scale:** fit-to-width per segment — each row maps `[start-1s .. end+1s]` to full
   container width, identical to the modal. No horizontal scroll.
3. **Row detail:** reproduce the modal timeline exactly — buffer padding + dashed boundary
   lines + greyed read-only neighbour words. **Omit** the per-row ruler (too tall for a dense
   list; the whole point is compact scannability).

## Why this is low-risk

- The waveform component already renders from a **compact peak envelope loaded once for the
  whole track** (`AudioData`, ~hundreds of KB) and shared via `VocalsAudioDataLoaderContext`.
  Each segment waveform is just a time-slice into that shared array — cheap to render N of them.
- `TimelineEditor.tsx` already implements everything visual: word bars, resize handles, drag,
  collision checks, `TIMELINE_PAD_SECONDS` padding, boundary lines, greyed `contextWords`,
  playback cursor, click-to-play scrub. We reuse it, not rebuild it.
- `TranscriptionView` already lives **inside** `<VocalsAudioDataLoader>` in `LyricsAnalyzer`,
  so the audio context is in scope for inline waveforms with no plumbing changes.

## Key components (current)

- `components/lyrics-review/TranscriptionView.tsx` — renders the segment list; owns the
  `Simple`/`Advanced` `ToggleGroup`. **← add Waveforms branch + 3rd toggle item.**
- `components/lyrics-review/TimelineEditor.tsx` — the reusable timeline (bars + waveform +
  padding + context). **← add `showRuler?` + `onCommit?` props.**
- `components/lyrics-review/WaveformVisualizer.tsx` — canvas renderer, unchanged.
- `components/lyrics-review/VocalsAudioDataLoader.tsx` — shared audio context, unchanged.
- `components/lyrics-review/LyricsAnalyzer.tsx` — container. Holds `advancedMode` (localStorage),
  `handleUpdateSegment`, `handlePlaySegment`, `editContextWords`, opens `EditModal`.
  **← migrate view-mode state; add inline-commit + per-segment context; wire new props.**
- `components/lyrics-review/modals/EditModal.tsx` — reference for the local-copy → commit
  pattern (its `updateSegment` recomputes seg `start_time`/`end_time` from words).

## Implementation

### 1. New component: `WaveformSegmentRow.tsx`

Per-segment inline editor. Wraps `TimelineEditor`, owns the drag-in-progress local state, and
commits to history **only on drag end** (never per-mousemove — that would flood undo history and
re-render the whole list).

Props:
```
segment: LyricsSegment
segmentIndex: number
contextWords: Word[]          // neighbours within the padded window (see §4)
currentTime?: number
onCommit: (index, updatedSegment) => void   // fires on drag end
onPlaySegment?: (time) => void
onOpenEditModal: (index) => void            // segment #/✎ → existing modal
audioReady: boolean
```

Behaviour:
- Local `words` state, seeded from `segment.words`; re-synced via `useEffect` when the segment's
  words/timings change externally (undo/redo, modal save, auto-correct).
- `handleWordUpdate(i, updates)` → update local `words` only (this row re-renders, not the list).
- `handleCommit()` (drag end) → recompute segment `start_time`/`end_time` from `words`
  (mirror `EditModal.updateSegment`), call `onCommit(segmentIndex, updatedSegment)`.
- Row chrome: segment index (click → `onOpenEditModal`) + play button + edit ✎ icon, matching
  the density of the current Advanced row's left controls.
- Renders `<TimelineEditor words={words} contextWords={contextWords} showRuler={false}
  onWordUpdate={handleWordUpdate} onCommit={handleCommit} onPlaySegment={...}
  currentTime={currentTime} />`.

### 2. `TimelineEditor.tsx` changes (additive, backward-compatible)

- Add `showRuler?: boolean` (default `true`). When `false`: skip the 40px ruler band; keep a
  thin click-to-play strip (or move `handleTimelineClick` onto the waveform wrapper) so
  click-to-scrub still works. Total row height shrinks to ~bars(30) + waveform(35).
- Add `onCommit?: () => void`. Call it in `handleMouseUp` **iff a drag was active**
  (`dragState != null`) so the row can persist on release. Modal usage omits it → no change.
- Everything else (padding overlays, boundary lines, contextWords, collision, cursor) unchanged
  → "exactly as the modal works."

### 3. View-mode state migration (`LyricsAnalyzer.tsx`)

- Replace the `advancedMode: boolean` (localStorage `lyricsReviewAdvancedMode`) with
  `transcriptionViewMode: 'simple' | 'advanced' | 'waveforms'` (localStorage
  `lyricsReviewViewMode`).
- Migration: if new key absent, read old boolean (`'true'` → `'advanced'`, else `'simple'`).
- Pass `viewMode` + `onViewModeChange` down to `TranscriptionView` (keep deriving
  `advancedMode = viewMode === 'advanced'` internally where the flex-pill layout is used, to
  minimise churn in `HighlightedText` wiring).

### 4. Per-segment context words

The modal computes `editContextWords` for the single open segment (±2s neighbours). Waveforms
mode needs this for **every** row. Precompute once per data change to stay O(n):
- Segments are time-ordered; for row `i`, gather timed words from the neighbouring segments
  whose `[start,end]` intersect `[seg.start-1s, seg.end+1s]` (just prev/next segment in
  practice). Return a `Map<segmentIndex, Word[]>` or a small helper called per row.
- Apply `timingOffsetMs` the same way `editContextWords` does.

### 5. Inline commit handler (`LyricsAnalyzer.tsx`)

Add `handleCommitSegmentTiming(index, updatedSegment)`:
- Replace `corrected_segments[index]` and `updateDataWithHistory(newData, 'adjust word timing')`.
- Does **not** depend on `editModalSegment` (unlike `handleUpdateSegment`).
- No edit-log entry needed for pure timing (the modal path only logs text changes today — keep
  parity).

### 6. `TranscriptionView.tsx` changes

- Add third `ToggleGroupItem value="waveforms"` (icon: `AudioWaveform`/`Activity` from lucide)
  with label `t('waveforms')`.
- When `viewMode === 'waveforms'`: map segments → `<WaveformSegmentRow>` instead of
  `HighlightedText` rows. Pass `onCommit`, `onOpenEditModal` (→ parent opens `EditModal`),
  `onPlaySegment`, `currentTime`, and the per-segment `contextWords`.
- Simple/Advanced branches unchanged.

### 7. i18n

- Add `lyricsReview.transcription.waveforms` (+ any row tooltips, e.g. edit ✎) to
  `frontend/messages/en.json`, then:
  `python frontend/scripts/translate.py --messages-dir frontend/messages --target all`
- CI fails if any of the 33 locales are missing keys.

## Performance notes

- N canvases (one per segment; typically 40–90). Each `WaveformVisualizer` uses a
  `ResizeObserver` and slices only its own time window — bounded work. Expected fine.
- Only the actively-dragged row re-renders during a drag (local state), so dragging is smooth
  regardless of list length.
- **If** long lists lag on lower-end devices, add windowing (render waveforms only for rows near
  the viewport) as a follow-up — not in scope for v1.
- Mobile: touch drag on small bars is fiddly; mode remains functional (grid collapses to 1 col).
  Primary use is desktop review. No blocker.

## Testing strategy (per docs/TESTING.md)

- **Unit (Jest):**
  - `WaveformSegmentRow`: local-state seeding + re-sync on external segment change; commit on
    drag end recomputes segment `start_time`/`end_time` from words; does not commit mid-drag.
  - Per-segment context-words helper: returns neighbours intersecting the padded window; honours
    `timingOffsetMs`.
  - View-mode localStorage migration (old boolean → new enum).
- **Component:** `TranscriptionView` renders N waveform rows in `waveforms` mode; clicking the
  segment number invokes `onOpenEditModal`; Simple/Advanced unaffected.
- **`TimelineEditor`:** existing tests still pass with default `showRuler`; new test for
  `showRuler={false}` (no ruler, click-to-play still fires) and `onCommit` firing once on
  mouseup after a drag.
- **E2E (`frontend/e2e/regression/lyrics-review.spec.ts` or a new spec):** switch to Waveforms
  mode → assert ≥1 `<canvas>` waveform present per segment; drag a word bar → assert timing
  persisted (data change / undo enabled); open Edit modal from a row.
- **Production E2E:** extend an existing review prod spec to toggle Waveforms and assert render.

## Rollout

- Version bump `pyproject.toml` (frontend-affecting change ships via the wheel-packaged frontend).
- No backend / infra changes. No new API surface.

## Out of scope (v1)

- Tap-To-Sync inline (stays in modal).
- Inline add/split/merge/delete-word and text editing (stays in modal).
- Global fixed px/second scale + row virtualization (possible follow-ups).
