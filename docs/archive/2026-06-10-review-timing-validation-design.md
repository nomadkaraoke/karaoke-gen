# Review-Phase Word-Timing Validation & Sanitization — Design

**Date:** 2026-06-10
**Status:** Approved (brainstorming) → ready for implementation plan
**Worktree:** `karaoke-gen-review-timing-validation` (`feat/sess-20260610-2307-review-timing-validation`)

## Background

Job `17f7c313` ("Atmosphere - Scalp") rendered two screens of lyrics overlapping. Root
cause: the segment `"A whiskey and a beer, let's forget where we are"` (segment bounds a
correct `15.18–18.04`) had its 4 leading words ("A whiskey and a") carrying invalid
timestamps `start=0.0, end=-0.005`. When the resizer split that line, the new line
inherited `start_time=0.0`, which mis-sorted the lyric screen ahead of the INTRO section
and stacked two screens.

We shipped a **render-time** safety net (`SegmentResizer._sanitize_segment_timings`,
v0.176.2, PR #804) that clamps invalid word timings into segment bounds while building the
ASS. That stops the visual overlap but does **not** fix the source: the bad data is created
and persisted during the lyrics review phase.

### Verified origin (not AudioShake)

- Base `corrections.json` (lyrics phase output) had **valid** timings for these words; 0
  corrections touched them.
- The `-0.005` value is the unique fingerprint of `frontend/hooks/useManualSync.ts`
  (`previousWord.end_time = currentStartTime - 0.005`, plus `currentWord.start_time =
  currentTimeRef.current`).
- So the **manual-sync ("tap-to-time") tool** assigned `start=0` (words synced while the
  audio playhead sat at 0) and cascaded `end = 0 - 0.005`. Manual-sync timing edits are not
  recorded in the edit log (which only logs text ops), which is why the segment looked
  untouched.
- Symptom the operator hit: opening that segment in the editor modal was buggy with the
  0/`-0.005` values; they had to type timestamps in by hand before they could drag-adjust.

## Goal

Prevent invalid word timings from being **created** or **persisted** during review, while
keeping the editing experience flexible — specifically, the operator must still be able to
make a segment start earlier / end later easily. Render-time sanitization remains as the
last-resort net.

## Core invariant (one definition, four enforcement points)

A segment's word timings are **valid** iff, for every word:

```
segment.start_time ≤ word.start_time ≤ word.end_time ≤ segment.end_time
and words are non-decreasing in start_time across the segment
and all times are finite and ≥ 0
```

This is the same invariant the shipped render-time clamp enforces. We mirror it earlier.

### The key reconciliation: clamping is operation-specific, not global

The invariant holds **at rest** (after every save). The operator is never "trapped" by it
because the way you extend a segment is to edit a word, and **word edits expand the segment
to fit** rather than clamping the word back:

| Operation | Behavior |
|---|---|
| Drag / type a word's start earlier (or end later) in the modal | **Segment bounds auto-expand** to contain the new word time. This *is* the "make the segment start earlier / end later" control. No clamp. |
| Manual-sync tap (`useManualSync`) | Tap time is **clamped** to the segment's current audio window `[start, end]` (a tap outside is a sync glitch, the root cause). Warn if clamped. |
| Modal opens a segment containing pre-existing out-of-bounds / broken values | **Repair on open**: clamp offending words into the loaded segment bounds / fix `end<start`/negative, show a non-blocking banner. Restores a sane editing surface. |
| Backend save (`POST /corrections`) | **Sanitize** the submitted corrections to the invariant before persisting to GCS; `logger.warning` a summary. Belt-and-suspenders for any frontend gap. |

Because user edits expand the segment *before* save, legitimate extensions are within
bounds at submit time and are never clamped — only true glitches (a word at 0 in a 15 s
segment) get repaired.

UX philosophy (operator-confirmed): **clamp/repair + non-blocking warn**, never silently and
never blocking the save.

## Components

### Shared sanitizer (one canonical implementation per language)

- **Frontend:** `frontend/lib/lyrics-review/sanitizeWordTimings.ts`
  - `sanitizeSegmentTimings(segment): { segment, changes: TimingChange[] }`
  - Pure function. Returns the corrected segment plus a list of what changed (word id, field,
    from, to) so callers can render warnings. Valid input returns `changes: []` and an
    unchanged segment (referential no-op where possible).
  - Also exports `expandSegmentToWords(segment)` (grow `start_time`/`end_time` to the
    min/max of word times) used by the modal's edit handlers.
- **Backend:** reuse / co-locate with the existing
  `SegmentResizer._sanitize_segment_timings` logic so render and submit share one definition.
  Expose a thin `sanitize_corrections(corrections: dict) -> (dict, summary)` helper for the
  route to call.

### Layer 1 — Manual-sync guard (`frontend/hooks/useManualSync.ts`)

- Clamp the synced `start_time` (and the derived previous-word `end_time`) into the
  segment's current `[start_time, end_time]` window in both the `handleKeyDown` tap path
  (~L120-155) and the `handleTap` path (~L285-316).
- Keep `end ≥ start` and non-decreasing ordering.
- If a clamp changed a value, surface a non-blocking toast (e.g. *"'A whiskey' timing was
  outside the segment — snapped to 15.18s"*).

### Layer 2 — Edit-segment modal (`frontend/components/lyrics-review/modals/EditModal.tsx` + word list / `TimeInput`)

- **Sanitize-on-open:** when `editedSegment` initializes from the incoming segment, run
  `sanitizeSegmentTimings`; if it changed anything, show a dismissible warning banner listing
  affected words. The segment now opens with sane values so the timeline/drag editor works.
- **Segment-follows-words on edit:** word edit handlers (`handleWordChange`, drag handles,
  `handleAddWord`, `splitWordWithTiming`) call `expandSegmentToWords` so editing a word's
  start earlier / end later grows the segment rather than being clamped. Replace the fragile
  `?? 0` fallbacks.
- **Robust inputs:** `TimeInput` and the timeline drag math tolerate `null`/`0`/inverted
  values without NaN, off-canvas blocks, or unclickable handles.

### Layer 3 — Backend safety net (`backend/api/routes/jobs.py::submit_corrections`, POST `/{job_id}/corrections`)

- Before `storage.upload_json(corrections_updated.json, ...)`, run `sanitize_corrections`
  over `submission.corrections['corrected_segments']`.
- Clamp words outside their segment bounds, fix `end<start`/negative, persist the cleaned
  payload. `logger.warning` with job id + count + a sample. Non-blocking (the save succeeds).
- `complete-review` renders from this saved file, so this guarantees clean stored data.

## Data flow

```
Manual sync tap ─clamp→ word times (within segment) ─┐
Modal word edit ─expand segment→ word times ─────────┤→ EditModal state
Modal open (bad data) ─repair+banner→ ───────────────┘
        │  POST /corrections (submission.corrections)
        ▼
   submit_corrections ─sanitize+log→ corrections_updated.json (GCS)
        │  complete-review → render worker
        ▼
   OutputGenerator → SegmentResizer._sanitize_segment_timings  (last-resort net, already shipped)
```

## Testing

- **Frontend (Jest):**
  - `sanitizeWordTimings`: the exact `17f7c313` fixture ("A whiskey and a" at `0/-0.005`,
    segment `15.18–18.04`) → words clamped into bounds, `end≥start`, non-decreasing; valid
    input untouched (`changes: []`); `expandSegmentToWords` grows bounds for a deliberate
    out-of-bounds edit.
  - `useManualSync`: a tap at playhead 0 clamps to segment start and reports a change.
  - `EditModal`: opening a segment with the bad fixture shows the banner and renders
    sane values; dragging a word's start before `segment.start` expands the segment.
- **Backend (pytest):**
  - `submit_corrections` clamps out-of-bounds words before upload and logs; a valid payload
    passes through byte-for-byte; segment-expanded (legitimately extended) payloads are not
    clamped.
- **Regression anchor:** the `17f7c313` shape is the shared fixture across all layers.

## Scope guardrails (YAGNI)

- No backfill of existing jobs — the render-time net already protects them.
- No change to the `complete-review` flow.
- No new "timing health" UI beyond the inline toast/banner.
- i18n: new warning strings go in `frontend/messages/en.json` and run through
  `translate.py --target all` (CI enforces locale completeness).

## Out of scope / follow-ups

- Investigating *why* the manual-sync playhead can be at 0 when a tap arrives (a deeper
  interaction fix) — the clamp makes it harmless regardless; revisit only if it recurs.
