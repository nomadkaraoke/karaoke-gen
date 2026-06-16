# Match-Judge Improvements — Implementation Plan

**Date:** 2026-06-16
**Worktree:** `karaoke-gen-match-judge-improvements`
**Branch:** `feat/sess-20260616-0053-match-judge-improvements`
**Builds on:** match-judge v0.184.1 (PRs #836 + #838)
**Spec:** `docs/archive/2026-06-16-match-judge-improvements-prompt.md` (decisions by Andrew — settled)

Four items. Decisions are already made in the prompt; this doc records the concrete
shapes I'm committing to and the test plan. TDD throughout.

---

## Item 1 — Race fix: parallel tidy + hard gate

**Backend shape (decided: two-call `stage` contract).** Add `stage: "fast" | "full"`
to the endpoint + `judge_match`. Default `"full"` (backwards-compatible).

- `stage="fast"` (no tier needed): run catalog search + `classify_catalog_match` ONLY.
  Never calls AI.
  - confident catalog verdict (cosmetic/none) → return it (`needs_ai=False`).
  - catalog undecided → return `MatchVerdict(none, confident=False, engine="catalog",
    reason="needs ai", needs_ai=True)`.
- `stage="full"` (tier known): current pipeline + Item 4 (see below).

New field `needs_ai: bool = False` on `MatchVerdict` (+ `to_dict`, response model, TS type).

**Frontend two-phase coordination (AudioSourceStep):**
1. On mount (parallel with audio search): `matchJudge(artist, title, { stage: 'fast' })`.
   Store the promise in a ref. If it resolves to a confident cosmetic verdict, apply the
   silent tidy immediately (no re-search).
2. When the audio search resolves (tier known), one coordinating effect:
   - `await` the fast promise.
   - `needFull = fast == null || fast.needs_ai || (fastIsCatalogConfident && tier >= 3)`.
   - if `needFull` → `matchJudge(artist, title, { stage: 'full', audioConfidenceTier: tier })`,
     apply its verdict.
   - settle the gate in a `finally` (always).

**Hard gate.** `gateReleased` state (starts false) → true when the judge settles.
Pick/select buttons (PickCard, ResultRow) and the fallback submit buttons are disabled
while `!gateReleased`. A subtle "Checking song details…" line shows when
`!gateReleased && !isSearching`. Safety timeout `JUDGE_GATE_TIMEOUT_MS = 12000` from when
the full call fires guarantees release even on a network hang (backend AI already caps at
12s). Gate never deadlocks: every path (disabled flag, fast/full failure, timeout) settles.

On a content re-search, keep the gate open (don't re-judge) — mirrors the existing
`judgeTriggered` semantics.

**Acceptance:** impossible to reach Step 3 with un-tidied metadata in normal flow; a slow
AI verdict never clobbers the selection; gate is imperceptible in cosmetic cases and bounded
(≤12s) otherwise.

## Item 2 — Two-way toggle for tidy/correction (frontend only)

`MatchNotice` becomes a clean two-way toggle. Add `correctionActive` state (default true).
- cosmetic applied: "Tidied to **X** · keep what I typed" → revert.
- cosmetic reverted: "Using what you typed: **Y** · use tidied version" → reapply.
- content applied: "Corrected to **X** — you typed “Y” · Undo" → revert.
- content reverted: "Using what you typed: **Y** · use the correction" → reapply.

Toggle is metadata-only (no re-search on toggle — matches old Undo + the "no restart on
cosmetic" rule). `appliedFrom` already holds the typed text; `verdict` holds canonical.

New i18n keys (translate all locales): `matchUsingTyped`, `matchUseTidied`,
`matchUseCorrection`.

## Item 3 — Title-screen preview perf (frontend only)

`TitleCardPreview.tsx` + `KaraokeBackgroundPreview.tsx`:
- Render canvas at **preview resolution** (960×540, dpr-aware, clamp dpr≤2) instead of
  3840×2160. Keep coordinate constants in 4K space; apply `ctx.setTransform(scale,…)` so
  drawing code is unchanged.
- Add **low-res** bg assets: `public/title-card-bg-preview.png`,
  `public/karaoke-bg-preview.png` (downscaled from the 2.7 MB 4K originals). Previews use
  these; the 4K originals stay for any real render path.
- **Preload** the preview bg(s) at Step 2 (export `preloadTitleCardBg()` /
  `preloadKaraokeBg()`, call from AudioSourceStep mount) so they're warm by Step 4.
- **Spinner** "Loading title screen preview" overlay until the first draw completes
  (`ready` state).
- Update `TitleCardPreview.test.tsx` mock (add `setTransform`/`scale`), add coverage for
  the spinner + low-res bg src.

## Item 4 — Catalog-match trust: verify weak matches with AI (backend)

In `judge_match` (stage=full): when `classify_catalog_match` returns a confident verdict
BUT the audio tier is weak (`>= 3`), still call AI and let it override — catches a junk/
misspelled Spotify entry matching the user's exact typo (prod: `Queen / Bohemian Rapsody`
→ wrongly "already canonical"). AI overrides only when confident and `kind != none`;
otherwise keep the catalog verdict. Extra ~$0.0003/call cost only on weak-result cases.

## Testing

- **Backend unit** (`test_service.py`): fast-stage returns catalog verdict / needs_ai;
  fast never calls AI; full-stage weak-tier catalog match → AI consulted → override;
  full-stage strong-tier catalog match → no AI; AI-unconfident on weak tier → keep catalog.
- **Backend unit** (`test_classifier.py`): unchanged (still escalates on no match).
- **Backend route** (`test_match_judge_routes.py`): `stage` forwarded; `needs_ai` in response.
- **Frontend** (`AudioSourceStep.test.tsx`): fast call on mount; cosmetic applies from fast;
  gate disables pick button until settled then enables; full call fired only when needed;
  two-way toggle revert↔reapply.
- **Frontend** (`TitleCardPreview.test.tsx`): preview-res canvas, low-res bg, spinner.
- `make test` full suite green before `/shipit`.

## Out of scope / preserved
No Step-1 autocomplete; cosmetic must not restart search; deferred job creation; public
brand rule; audio-before-customize ordering. (Constraints from the 2026-06-15 doc.)
