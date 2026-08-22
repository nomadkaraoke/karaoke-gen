# Lyrics Review: Auto-Applied Corrections UX

**Date:** 2026-06-18
**Status:** Shipped (iterated live against prod backend)
**Worktree:** `karaoke-gen-lyrics-autocorrect-ux` · branch `feat/sess-20260618-0022-lyrics-autocorrect-ux`

## Final shipped scope (beyond the original design below)

The design below captured the initial auto-correct-display work. The shipped PR
also includes, from live iteration:

- **Toolbar consolidation:** the applied-corrections row was removed; the toolbar
  button reads **"Auto-corrected · N"** and opens the details panel on demand
  (per-item undo, **Re-run**, and **Revert all** auto-corrections).
- **Long-word duration badge** moved *inside* the word pill, right-aligned.
- **Timeline view replaced:** the old Text/Timeline + Simple/Advanced toggles are
  now a single **Simple / Advanced** toggle (`DurationTimelineView` deleted).
  Inline **>2s word / >2s gap** warnings show in both modes. **Advanced** mode
  stretches each word pill by its duration (flex-grow) so a segment fills the
  full width as a readable timeline, with gap **spacers** you can click to play
  from that point (hover shows a play icon).
- **Auto-corrections are the review baseline:** the on-load apply rebases history
  (not an undoable edit), so no session/restore prompt appears unless there are
  genuine manual edits.
- **Unified session restore:** removed the ad-hoc `window.confirm` localStorage
  path; the same `SessionRestoreDialog` is used everywhere — server-backed in
  cloud mode, localStorage-backed (`localSessionStore`) when running locally.
- **Crash-reporter localhost guard:** local dev errors no longer POST to the prod
  error monitor.

## Problem

Since the AI auto-correct suggestions feature shipped (PR #809+, v0.177.0), the
suggestions are good enough that the operator always clicks **Accept All**. The
manual review of a separate suggestions panel (Image #1) is pure friction. We
want the corrections applied automatically, and the *review* effort redirected
to where it's actually needed: **re-checking word timing on the segments the AI
touched** — especially where timing was estimated.

## Goals (operator's 5 points)

1. Remove the always-visible suggestions panel; collapse it behind a
   **"Show auto-corrections (N)"** toggle (details on demand).
2. **Auto-apply** all suggestions on load — exactly today's *Accept All*
   semantics (apply all pending, conflict-group winners by consensus then
   confidence, default run settings).
3. **Distinct colour** for AI-corrected words (purple) vs human-edited (green).
4. **Compact, always-visible annotation** of what changed: a small bubble above
   each corrected span showing the *original transcription text* (Image #2),
   with extra vertical line spacing to fit.
5. **Guidance + scrutiny**: tell operators to play and re-check sync on every
   edited/auto-corrected segment, and **visually flag words whose timing was
   estimated** (one word split into N evenly-spaced words) — those almost always
   need a timing nudge. A 1:1 word replacement keeps the original word's real
   timing and needs less scrutiny.

## Current architecture (what we build on)

- `useAutoCorrect` already **auto-runs one pass on load** (`autoRunOnLoad`);
  backend pre-generates + caches, so it's normally an instant cache hit.
  Suggestions land as a **pending layer** in `AutoCorrectPanel` (Image #1).
- `acceptAll()` applies pending suggestions via `applySuggestion`, choosing
  conflict-group winners. Applied words are **new `Word`s** with
  `created_during_correction: true`.
- `editedWordIds` (in `LyricsAnalyzer`) is a **diff of current data vs
  `history[0]`** (initial state). New AI word IDs fall into this set → they
  render with the same green/lime "edited" highlight as human edits. *This is
  why there's no visual distinction today.*
- `autoCorrectApply.ts:distributeTimings` is the **estimated-timing signal**:
  for a replace, it spreads the removed span's `[start,end]` evenly across the
  new words. `newTexts.length > 1` ⇒ estimated. A 1:1 replace (or N→1 merge)
  inherits a real range ⇒ trustworthy. `insert_after` words are always synthetic
  ⇒ estimated.
- Re-applying a suggestion is **naturally idempotent**: `isSuggestionStale`
  checks the original word IDs still exist; once replaced they don't, so a
  re-run on reload applies nothing. This de-risks auto-apply-on-load against
  reloads and restored sessions.

## Data model

Stamp new fields on AI-applied `Word`s at apply time (`autoCorrectApply.ts`):

| Field | Meaning | Where set |
|-------|---------|-----------|
| `ai_corrected?: boolean` | This word came from an AI correction → purple + bubble | every new word in a replace/insert |
| `original_text?: string` | The original transcription text for the whole span → bubble content | the **first** new word of the span only |
| `timing_estimated?: boolean` | Timing was guessed by even-split / insert → scrutiny marker | per new word (`true` when `newTexts.length > 1` or `insert_after`) |
| `correction_span_id?: string` | Groups the new words of one suggestion (= suggestion id) → one bubble per span, inline-undo lookup | every new word in the span |

`validation.ts` (zod) gains the same optional fields so saved/restored data
round-trips.

**Purple vs green rule:** purple = `ai_corrected === true`. `editedWordIds`
(green) excludes `ai_corrected` words. If an operator later hand-edits an AI
word, it stays purple — the bubble (original transcription) and estimated marker
remain accurate; this is an acceptable v1 simplification (rare, not harmful).

## Auto-apply flow

- Add an `autoApplyOnLoad` behaviour to `useAutoCorrect`: after the initial
  auto-run reaches `reviewing`, immediately run `acceptAll()` once (guarded by a
  ref), then leave the panel collapsed.
- On reload of an already-corrected job: the stamped words are present (purple
  renders from the stamps, independent of the history diff); the re-run is stale
  ⇒ `acceptAll` applies 0 ⇒ no spurious history entry. Idempotent.
- **EditLog** still records `ai_suggestion_accept` per applied suggestion (now
  automatic). The meaningful negative signal becomes `ai_suggestion_undo`.

## Details panel + undo

- `AutoCorrectPanel` starts **collapsed**; header becomes
  *"Show auto-corrections (N)"*. Expanded: the existing list, each row with
  **Undo** (the accept/reject buttons are gone from the main flow).
- **Inline undo:** clicking a purple word looks up its `correction_span_id` →
  `undoAccept(suggestionId)`. Works within the active review session (where the
  hook holds `undoInfos`). On a fresh reload the corrections are "baked"; the
  panel still lists them, and undo falls back to normal manual editing if no
  in-memory undo info exists. (v1 limitation — operators review in one sitting.)

## Rendering

**Text view only for v1** (`TranscriptionView` → `HighlightedText` →
`WordComponent`). Thread the AI word data down:

- **Purple highlight** for `ai_corrected` words (new constant, e.g.
  `bg-purple-500/30`), visually distinct from green (`bg-green-500/25`) and
  user-edited lime (`bg-lime-400/40`).
- **Bubble** above the first word of each span: tiny muted text = `original_text`
  with a small connector. `TranscriptionView` row gets extra top padding when a
  segment contains AI corrections (point 4: "more space between lines").
- **Estimated-timing marker** on `timing_estimated` words: a distinct treatment
  (e.g. dashed amber underline/ring) vs the solid purple of trustworthy 1:1
  replacements. *Exact styling tuned live in the browser.*

The Duration/Timeline view keeps its current rendering for v1 (timing is already
visible there); colour-only follow-up if wanted.

## Guidance (`GuidancePanel`)

- Add **purple → "AI-corrected"** to the colour legend.
- Rewrite the workflow tips: instruct operators to **play each edited/AI-corrected
  segment and confirm the words still line up**, calling out that
  **[estimated]-marked words had their timing guessed and usually need a nudge**,
  while 1:1 replacements keep the original timing.
- Optional summary line: *"N words auto-corrected · X with estimated timing —
  check these."*

## i18n

All new user-facing strings go in `frontend/messages/en.json` and are translated
to all 33 locales via `python scripts/translate.py --messages-dir ./messages
--target all` (CI enforces completeness).

## Testing

- **Unit (`autoCorrectApply`)**: stamping correctness for replace 1:1 (not
  estimated, original_text on first), replace 1→N (estimated), `insert_after`
  (estimated), `delete` (no new words), N→1 merge (not estimated).
- **Unit (`editedWordIds`)**: excludes `ai_corrected` words; includes a
  hand-edited AI word if/where we decide it flips (v1: stays purple).
- **Unit (auto-apply)**: applies all winners on load; idempotent when stale.
- **Component**: `WordComponent` renders purple + bubble + estimated marker;
  `AutoCorrectPanel` collapsed by default, toggle reveals list with undo.
- **Prod E2E** (`frontend/e2e/production/`): load a job with references, assert
  purple words + bubbles render and the panel is collapsed.

## Files

types.ts · validation.ts · utils/autoCorrectApply.ts · hooks/useAutoCorrect.ts ·
LyricsAnalyzer.tsx · AutoCorrectPanel.tsx · TranscriptionView.tsx ·
shared/HighlightedText.tsx · shared/Word.tsx · constants.ts · GuidancePanel.tsx ·
messages/en.json (+ locales) · tests.

## Out of scope (v1)

Backend changes (auto-apply is frontend-only); purple/bubble in the Timeline
view; durable cross-reload inline-undo of baked corrections.
