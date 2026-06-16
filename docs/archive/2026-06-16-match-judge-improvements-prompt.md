# Match-Judge Improvements — New-Session Prompt Doc

**Created:** 2026-06-16
**For:** a fresh session, started via `/startnomad gen match-judge improvements`
**Builds on:** the artist/title match-judge feature shipped in gen **v0.184.1** (PRs #836 + #838).

## Read first (context)

- Feature overview + gotchas: agent memory `project_match_judge.md` (and `[[project_match_judge]]`).
- Original design + history constraints: `docs/archive/2026-06-15-artist-title-matching-rework-plan.md`.
- **Honor the "Constraints from history" section of that plan** — no Step-1 autocomplete; cosmetic correction must not restart the audio search; deferred job-creation/search-session pattern; public-style brand rule; audio-before-customize ordering. Don't undo these.

### Where the code lives
- Backend: `backend/services/match_judge/` — `classifier.py` (deterministic + catalog), `ai.py` (Vertex `gemini-3.5-flash`), `service.py` (orchestration), `verdict.py` (`MatchVerdict`). Endpoint: `POST /api/catalog/match-judge` in `backend/api/routes/catalog.py`. Config in `backend/config.py` (`MATCH_JUDGE_ENABLED` / `MATCH_JUDGE_MODEL` / `MATCH_JUDGE_TIMEOUT_MS=12000`).
- Frontend: `frontend/components/job/steps/AudioSourceStep.tsx` — the judge effect, `applyCorrection`/`handleSuggestionAccept`/`handleUndoCorrection`, and the `MatchNotice` component. Client: `api.matchJudge` in `frontend/lib/api.ts`. i18n keys live in `frontend/messages/en.json` under `jobFlow` (`matchTidiedTo`, `matchKeepMine`, `matchCorrectedTo`, `matchUndo`, `matchDidYouMean`) — any new/changed strings must be translated to all 33 locales (`python scripts/translate.py --messages-dir ./messages --target all`).

### How it currently works (the starting point)
On Step 2, the judge effect fires **after** the audio search resolves (`isSearching === false`), passing `confidence.tier`. The verdict drives `MatchNotice`: cosmetic → silent apply + quiet "Tidied to X · keep what I typed" (no re-search); confident content → apply + "Corrected to X · Undo", re-search only if tier 3; ambiguous → ask-first "Did you mean?". `applyCorrection(newArtist, newTitle, reSearch)` writes to the parent via `onArtistTitleCorrection` (no-restart path) and, when `reSearch`, resets `searchTriggered`/results so the mount effect re-runs.

---

## Work items (with decisions already made)

### 1. Race: audio results can arrive before the tidy finishes
**Problem:** the judge runs *after* the audio search, so there's a window where results are shown but the tidy hasn't applied. If the user selects audio / advances fast, the un-tidied artist/title can be what's saved (job creation is deferred to Step 4, so it's usually caught, but not guaranteed). Worse: a `content`+reSearch verdict landing while the user is mid-selection can blow away results / a pick.

**Decision (Andrew):** **Tidy earlier (parallel) + a hard gate.**
- Run the **deterministic + catalog** tidy **in parallel with the audio search** (kick it off on mount; it doesn't need the tier). The **AI** layer still needs the tier, so it runs once the audio search resolves — but only when deterministic+catalog aren't confident.
  - This likely means splitting the single `match-judge` call, OR adding a query param so the backend can do a fast catalog-only pass immediately and a full pass (with tier) after. Decide the cleanest shape — could be two calls (`/match-judge` cheap-first without tier, then a second with tier only if needed) or one call kicked off as soon as possible. Keep the backend contract clean.
- **Hard-gate selection:** disable "Use This Audio" (all audio pick buttons) with a subtle indicator (e.g. "Checking song details…") until **both** the audio search **and** the judge verdict have resolved + applied. The judge's 12s timeout must still unlock the gate (never strand the user). Because the tidy runs in parallel, the common cosmetic case adds ~no perceptible wait; only the rarer AI path holds the gate briefly.
- Gate releases when the verdict reaches a terminal state (cosmetic applied / content applied / ambiguous prompt shown / none / timeout). For **ambiguous**, release the gate (don't block indefinitely on an unsure result) but keep the "Did you mean?" prompt visible.

**Acceptance:** it is impossible to reach Step 3 with un-tidied metadata in the normal flow; a slow AI verdict never clobbers a selection; the gate is imperceptible for cosmetic cases and bounded (≤12s) otherwise.

### 2. Make "keep what I typed" reversible (two-way toggle)
**Decision (Andrew):** the tidy notice should let the user **see what it would revert to**, and **switch back** to the tidied version if they change their mind.
- Show both values. e.g. tidied state: *"Tidied to **Hard-Fi — I Shall Overcome** · keep what I typed"*. After clicking, reverted state: *"Using what you typed: **hard-fi - i shall overcome** · use tidied version"*, and "use tidied version" switches back. A clean two-way toggle; current value always clear.
- Same for the **content** correction ("Corrected to X · Undo") — Undo should be reversible back to the correction too.
- This is just frontend `MatchNotice` + state (`appliedFrom` already tracks the pre-correction text; add the inverse). New i18n keys as needed (translate all 33 locales).

### 3. Title-screen preview performance (the original "point D")
`frontend/components/job/TitleCardPreview.tsx` renders a **3840×2160 (4K) canvas** and loads a **4K** background PNG (`/title-card-bg.png`) — for what's shown as a small thumbnail. Slow, especially on mobile.
- Render the canvas at **display resolution** (e.g. ~960×540, or `devicePixelRatio`-aware), not 4K.
- Add a **low-res** background asset (downscale `public/title-card-bg.png`) and use it for the preview; keep the 4K for any real render path.
- **Preload** the background once the artist/title are locked at Step 2 (the preview currently lives on Step 4), so it's warm by the time the user arrives.
- Add a **"Loading title screen preview"** spinner while it renders.
- Note: private mode has a second canvas (`KaraokeBackgroundPreview.tsx`) — apply the same treatment. There's a `TitleCardPreview.test.tsx`; update it.

### 4. Catalog-match trust (the "second point" — yes, it's a real bug)
**Problem:** the classifier's exact catalog match can match a junk / misspelled Spotify entry (e.g. a karaoke/lyric upload titled with the user's exact typo), so it returns `none`/"already canonical" and **the AI judge never runs**. Observed in prod: `Queen / Bohemian Rapsody` → "already canonical" (wrong).
**Decision (Andrew):** **Verify weak matches with AI.** When the audio results were weak (**tier 3**), run the AI judge **even if the catalog "matched"**, and let the AI override the catalog verdict. The extra cost (~$0.0003/call) is only incurred on weak-result cases. Implement in `service.py` (don't let `classify_catalog_match` short-circuit when tier is weak) and/or `classifier.py`. Add unit tests covering: weak tier + junk exact-match → AI consulted → corrected; strong tier + exact-match → still short-circuits (no AI).

---

## Process

1. `/startnomad gen match-judge improvements`, read the context docs above.
2. Brainstorm/confirm the parallel-tidy backend shape (item 1) before coding — it's the trickiest. Consider visual mockups only if helpful; the decisions above are settled, so keep it light.
3. TDD: backend changes get unit/integration tests; frontend changes get Jest tests (mirror the existing `AudioSourceStep.test.tsx` / `TitleCardPreview.test.tsx` patterns). Update i18n for all 33 locales when strings change.
4. Run `make test` (full suite), then `/shipit` (CodeRabbit → version bump → PR with `@coderabbitai ignore` → merge → wait for deploy → verify prod). **Verify item 1 and item 4 live in prod** with real `/api/catalog/match-judge` calls and by clicking through Step 2 on localhost:3000 (dev server proxies to prod backend by default).
5. After shipping, trigger the daily prod E2E workflow: `gh workflow run e2e-daily.yml --ref main`.

## Notes / gotchas
- The shell/rtk wrapper mangles `&&`, `2>&1`, and reformats `gh --json -q` output — use `;`-separated commands and `rtk proxy gh …` for raw JSON.
- `gh pr merge --delete-branch` errors locally because `main` is checked out in the main clone worktree; the remote merge still succeeds — verify with `gh pr view <n> --json state`.
- CI dedupes test jobs on merge and goes straight to "Deploy - Backend (Cloud Run)"; the definitive deploy signal is the prod root version (`https://api.nomadkaraoke.com/`) flipping to the new value.
- Admin token for prod API tests: `gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke | cut -d',' -f1`, send as `Authorization: Bearer <token>`.
