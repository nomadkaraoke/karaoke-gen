# Lyrics Review "Waveforms" mode + preview backing-vocals visibility — 2026-09-17

**Project:** karaoke-gen (frontend)   **Branch/commit:** merged to `main` via PR #1010 (squash `912ca37a`), v0.227.0   **Status:** done / shipped + prod-verified

## Summary
Added a third Synced Lyrics review view, **Waveforms**, that renders a compact inline copy of the
Edit Segment timeline for every segment — so a reviewer can spot mis-timed words (e.g. an over-long
trailing word whose vocal energy died earlier) at a glance without opening a modal per line. Also
changed the preview modal so the **backing-vocals waveform is always shown** (even when "Clean
instrumental" is selected), so backing vocals are never discarded sight-unseen. Shipped to prod and
verified live on a real in-review job.

## What changed
Frontend only (`karaoke-gen/frontend`). Key files:
- **New `components/lyrics-review/WaveformSegmentRow.tsx`** — per-segment inline editor wrapping the
  existing `TimelineEditor`. Holds a local `words` copy during a drag; commits on drag release.
- **`TimelineEditor.tsx`** — new opt-in props: `showRuler`, `compact`, `onCommit`, `onWordClick`,
  `wordDecorations`. Compact mode: no card chrome, tight heights (20px bars + 14px waveform, no gap),
  waveform doubles as the click-to-play target. Click-vs-drag via a 4px threshold (`DRAG_THRESHOLD_PX`).
- **`TranscriptionView.tsx`** — 3-value view toggle (Simple/Advanced/**Waveforms**); renders
  `WaveformSegmentRow`s; builds per-segment context words + colour decorations (memoised).
- **`LyricsAnalyzer.tsx`** — view-mode state migrated from boolean `advancedMode` →
  `transcriptionViewMode` enum (localStorage `lyricsReviewViewMode`, migrating old
  `lyricsReviewAdvancedMode`); added `handleCommitSegmentTiming` (un-applies timing offset before
  storing) + `handleEditSegmentFromWaveforms`; Advanced/Waveforms now size the Reference column to its
  longest line (`flex-none w-max max-w-[46%]`) so Synced gets the freed width.
- **New utils** (all unit-tested): `lib/lyrics-review/utils/contextWords.ts`, `segmentTiming.ts`
  (`recomputeSegmentFromWords`, `resolveInitialViewMode`), `wordDecorations.ts` (`classifyWord`,
  `barClassForWord` using the **exact `HIGHLIGHT_CLASSES` tints**, `buildSegmentDecorations`).
- **`BackingVocalsWaveform.tsx` + `modals/ReviewChangesModal.tsx`** — waveform always shown when a
  playable backing stem exists; selection-aware `kept` prop drives wording.
- **`Header.tsx`** — fixed pre-existing `FORMATTING_ERROR`: `autoCorrectedDesc` rendered without its
  `count` param (surfaced on jobs with ≥1 auto-correction).
- **i18n** — new keys (`waveforms`, `editSegment`, `untimedSegment`, `backingVocalsWaveformKept/Removed`),
  reworded `autoInstrumentalClean/CleanChosen`, removed `backingVocalsWaveformHint`; translated all 33
  locales; pruned the stale key from every locale; parity validated.
- **New tests** — `WaveformSegmentRow`, `contextWords`, `segmentTiming`, `wordDecorations`; updated
  `ReviewChangesModal`. Full frontend suite: 104 suites / 1283 tests green.
- **`scripts/list-review-jobs.py`** — lists in-review jobs (Firestore) with ready-to-open local review URLs.
- Docs: `docs/archive/2026-09-17-waveforms-review-mode-plan.md`; LESSONS-LEARNED entry.

## Decisions & rationale
- **Reuse `TimelineEditor` verbatim** (not a rebuild): the modal timeline already had bars + waveform +
  padding + boundary lines + greyed context, and the vocal peak envelope is loaded once per track and
  shared via context — so N inline waveforms are cheap time-slices.
- **Inline edits = timing only; structural stays in the modal** (Andrew's call) — lowest-risk surface.
- **Fit-to-width per segment** (matches the modal), ruler omitted for density.
- **Colours = exact `HIGHLIGHT_CLASSES` tints** so Waveforms and Advanced read identically (first
  attempt used saturated solids — Andrew rejected as too bright).
- **Always show backing-vocals waveform** so the "Clean recommended" default can't be accepted without
  seeing/hearing what's discarded.
- Merged with only "CI Gate" required (CodeRabbit is advisory, and was stalled ~10 min at merge; PR left
  without the `@coderabbitai ignore` line so its review still posts).

## Learnings / gotchas
- **Never call a parent's setState from inside a `setState` updater.** Committing dragged timing via
  `setWords(cur => { onCommit(...); return cur })` fired the parent's `setHistory` during render →
  "Cannot update a component while rendering a different component." Fix: keep a `wordsRef` synced with
  the state and read it from the event handler.
- **`overflow-hidden` on a bar clips the ghost text above it.** The AI-correction ghost floats above the
  bar via `absolute bottom-full` (a visual child outside the box); adding `overflow-hidden` to truncate
  long words silently hid it. Truncate the inner text span (`truncate min-w-0`) instead.
- **Structural JSX edits to the big `LyricsAnalyzer.tsx` don't hot-swap via Fast Refresh** — needed a full
  page reload to take effect during dev.
- **`translate.py` adds/updates keys but does NOT prune removed ones**; CI (`validate-translations.py`)
  requires exact parity, so a removed en key must be manually deleted from all 33 locales.
- **Local review against prod:** `cd frontend && npm run dev` proxies `/api/*` → prod backend by default
  (`BACKEND_URL`); set `localStorage.karaoke_access_token` to the admin-tokens secret + open
  `/app/jobs#/<id>/review`. `scripts/list-review-jobs.py` prints the URLs.

## Open threads & next steps
- **CodeRabbit review on PR #1010** was still "in progress" at merge — glance later; follow-up if it
  flags anything real.
- **Short-word truncation:** timing-accurate bars mean short words truncate to an ellipsis; mitigated
  with a hover tooltip. Andrew hasn't asked for more, but a different treatment (visible overflow) is an
  option if it annoys.
- Row **virtualization** is a possible follow-up if very long lyric lists lag (not needed so far).
- Dev server + Playwright browser were left running at session end (to be stopped on cleanup).

## Related docs
- Plan: `docs/archive/2026-09-17-waveforms-review-mode-plan.md`
- `docs/LESSONS-LEARNED.md` → "Lyrics-review 'Waveforms' mode — two React/CSS gotchas (Sep 2026, v0.227.0)"
- PR: https://github.com/nomadkaraoke/karaoke-gen/pull/1010
