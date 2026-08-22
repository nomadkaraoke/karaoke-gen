# Artist/Title Matching, Suggestions & Auto-Correction Rework (Phase 1)

**Date:** 2026-06-15
**Worktree:** `karaoke-gen-submission-form-simplify`
**Branch:** `feat/sess-20260615-0101-submission-form-simplify`
**Status:** Design + plan — pending implementation

## Problem

On Step 2 ("Choose Audio") the wizard stacks two green "success" cards: the **"Song found in our database"** catalog panel and the **"Perfect match found"** audio card. User feedback: the first reads like "we already have this karaoke video," when its only real job is to nudge the user toward **official artist/title formatting** (a quality-bar concern for *published* tracks). It's confusing, and the underlying case-insensitive match is imperfect.

This phase reworks **only** the artist/title matching → suggestion → auto-correction behavior. The broader flow structure is deliberately left intact (see "Constraints from history"). The title-screen preview perf fix is tracked separately.

## Decisions (from 2026-06-15 brainstorm with Andrew)

1. **Invisible cosmetic tidy.** When formatting differs only cosmetically (casing, spacing, punctuation, diacritics, `&`/`feat.` conventions) and we're confident it's the same song, **silently apply** the official formatting. No green "database" card. Show only a faint, dismissible line: *"Tidied to **Paramore — Big Man, Little Dignity** · keep what I typed"*.
2. **Confident content correction → auto-apply + surface + smart re-search.** When the correction changes actual characters (e.g. `Yesteray → Yesterday`) and we're confident, apply it **and** surface it with one-tap Undo: *"Corrected to **Yesterday** — you typed 'Yesteray' · Undo"*. Because characters changed, the original audio search may be poor, so **re-run the audio search** — automatically **only when the original results were weak** (tier 3 / empty); otherwise offer a quiet "Search again" link.
3. **Ambiguous → ask first.** When the judge isn't confident which song is meant (multiple plausible candidates), show an ask-first prompt: *"Did you mean **X**? [Use this] [Keep mine]"* before changing anything.
4. **Three-layer engine, all in v1.** Deterministic rules → catalog canonical → light AI judge. **The AI judge fires only when the deterministic + catalog layers are not confident** (cheap; happy path never invokes it).

## Constraints from history (do NOT regress)

From `docs/archive/2026-02-28-ux-*`, `2026-03-02-decouple-search-from-job-creation.md`, `2026-03-08-step2-song-suggestions.plan.md`, `2026-03-08-fuzzy-did-you-mean.plan.md`, `docs/LESSONS-LEARNED.md`, PRs #444/#451/#480/#495/#512:

- **No Step-1 autocomplete.** It was added (PR #451) then removed (PR #512) because users didn't understand it. Suggestions belong on Step 2, in parallel with the audio search.
- **Cosmetic correction must NOT restart the audio search.** Already a documented decision — the existing catalog `onArtistTitleCorrection` updates artist/title without re-searching. Preserve this. (Content corrections that change characters MAY re-search — see decision 2.)
- **Deferred job creation (search-session pattern).** No job is created until the final confirm step. Don't move creation earlier. The tidied artist/title is passed at `create-from-search` time; the audio `search_session_id` is unaffected by a metadata tidy.
- **Preserve the fuzzy artist-tiebreaker lesson:** when title-matching returns multiple artists with the same song name, use the user's (possibly garbled) artist input as a tiebreaker. And the gating: fuzzy/"did you mean" only triggers when audio results are poor.
- **`display_artist`/`display_title`** stay distinct from matching `artist`/`title`; they're set at creation time. The tidy applies to the **matching** artist/title (what drives search, lyrics, and published metadata).
- **Public vs private brand rule, audio-before-customize ordering, combined review flow** — untouched by this phase.

## Architecture

Consolidate today's scattered client-side matching (case-insensitive exact match in `SongSuggestionPanel` + LCS fuzzy in `audio-search-utils.ts`) into **one backend "match judge" service**, called from Step 2 in parallel with the audio search (replacing the current `searchCatalogTracks` + client fuzzy effect). Rationale: testable, consistent canonical formatting, and the AI layer needs the backend's LLM clients/keys.

### Backend: `POST /api/catalog/match-judge`

Request: `{ artist, title, audio_confidence_tier?: 1|2|3 }`
(The frontend passes the audio search's computed confidence tier so the judge knows whether weak results suggest a typo.)

Pipeline (server-side):
1. **Deterministic normalize** (reuse/extend `karaoke_gen.utils.normalize_text`): trim, collapse whitespace, fix obvious casing (title-case with small-word handling), straighten quotes, standardize `feat.`/`ft.` → `(feat. …)` and `and`↔`&` per house style.
2. **Catalog lookup** (existing `search_tracks` via `catalog_proxy_service`). If a candidate matches the normalized input under a strict normalized-equality test → **confident cosmetic**: return the catalog's canonical `artist_name`/`track_name`.
3. **AI judge — only if (1)+(2) not confident.** Invoke a light model (see "Model") with the typed input, the top catalog candidates, and the audio tier. It returns a structured verdict.

Response:
```json
{
  "kind": "cosmetic | content | ambiguous | none",
  "confident": true,
  "canonical_artist": "Paramore",
  "canonical_title": "Big Man, Little Dignity",
  "alternatives": [{"artist": "...", "title": "..."}],
  "engine": "deterministic | catalog | ai",
  "reason": "casing only"
}
```
- `kind=cosmetic` → frontend applies invisibly + faint line (no re-search).
- `kind=content` + `confident` → frontend auto-applies + Undo line; re-search if audio tier was weak.
- `kind=ambiguous` (or `content` not confident) → frontend shows ask-first "Did you mean?" with `alternatives`.
- `kind=none` → no suggestion (leave user input as typed).

**Resilience:** AI judge runs under a short timeout (~2–3s) and is fully optional — on timeout/error/unparseable output, fall back to deterministic+catalog result (or `kind=none`). Never block the audio search; never error the request. Mirror `auto_correct`'s greppable usage/cost logging (`match-judge usage/cost job/session=…`).

### Model (decided)

Reuse `auto_correct` dispatch (`claude*` → Anthropic, else Gemini via `genai.Client(vertexai=True, project=…, location="global").models.generate_content(model=…)`).
**Decision (Andrew, 2026-06-15):** default `MATCH_JUDGE_MODEL=gemini-3.5-flash` — the latest Vertex Gemini Flash (https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash). Passed bare to the genai Vertex client exactly like `auto_correct` passes `gemini-3.1-pro-preview`. No `ANTHROPIC_API_KEY` needed. Env-configurable, so if Vertex requires a version suffix it's a one-line env change (an invalid id surfaces as a loud error on first call). Add `gemini-3.5-flash` pricing to `pricing.py` for cost logging.

### Frontend (`AudioSourceStep.tsx`)

- Replace the parallel `searchCatalogTracks` + client fuzzy `useEffect` with a single `api.matchJudge(artist, title, tier)` call fired in parallel on mount (after the audio tier is known, or fired and reconciled when the tier resolves).
- **Remove** the green `SongSuggestionPanel`. Add a quiet `TidyNotice` (cosmetic) and reuse/restyle `DidYouMeanBanner` for the surfaced-content (Undo) and ambiguous (ask) cases.
- Wire cosmetic/confident-content application through the existing `onArtistTitleCorrection` (no restart). Add a `reSearch()` path (reuse `handleFuzzyAccept`'s restart logic) gated on weak prior results for content corrections.
- Keep the `confidence.tier` gating and artist-tiebreaker semantics (now enforced server-side, but the frontend still passes the tier).

## Out of scope (this phase)

- Title-screen preview perf (separate task: smaller canvas + low-res bg + preload at step 2 + spinner).
- Any change to step count / visibility / customize / job-creation timing.
- Auto-selecting audio on a Tier-1 match (possible later, separately).

## Testing strategy

Per `docs/TESTING.md`:
- **Backend unit:** deterministic normalizer rule table (casing, spacing, punctuation, feat./&, quotes); judge response parsing; confidence/kind classification; AI-failure fallback; "only call AI when not confident" gating.
- **Backend integration:** `/api/catalog/match-judge` happy/edge paths with catalog mocked and AI mocked.
- **Frontend unit (Jest):** TidyNotice rendering + Undo; content-correction auto-apply + smart re-search gating (weak vs ok results); ambiguous ask-first; preserve "no restart on cosmetic".
- **E2E (Playwright) + prod E2E:** end-to-end on a real song (cosmetic case), a typo case (content + re-search), and an ambiguous case.

## Telemetry

- Log judge decisions + engine used + latency + token cost (greppable, like `auto-correct usage/cost`).
- Count cosmetic / content / ambiguous / none and Undo clicks to measure real-world correction quality (informs whether thresholds need tuning).

## Open decisions

1. ~~Judge model default~~ — DECIDED: `gemini-3.5-flash` (Vertex), configurable via `MATCH_JUDGE_MODEL`.
2. Confidence thresholds for cosmetic vs content vs ambiguous (start conservative; tune from telemetry).
3. Whether to keep a tiny instant frontend normalizer for immediate display, or rely solely on the judge response (~sub-second). Lean: rely on judge for v1; add instant normalize later if the delay is noticeable.

## Rollout

- `MATCH_JUDGE_ENABLED` env flag (default on) so the AI layer can be disabled without a deploy; deterministic+catalog still function if AI is off.
- Bump `pyproject.toml` version. Ship behind normal CI; Andrew reviews locally (localhost:3000 → prod backend) before merge/deploy.
