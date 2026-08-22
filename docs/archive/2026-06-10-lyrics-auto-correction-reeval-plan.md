# Lyrics Auto-Correction Re-Evaluation & Design Plan

**Date:** 2026-06-10
**Branch:** `feat/sess-20260610-2332-lyrics-auto-correction`
**Status:** Research complete, design proposed, awaiting Andrew's decisions on open questions

## 1. Why we're revisiting this

Auto-correction has been attempted and abandoned multiple times over 2 years. As of
PR #321 (Jan 20, 2026), **all auto-correction is disabled** (`SKIP_CORRECTION=true`):
raw AudioShake transcription goes straight to human review, with anchor/gap analysis
still computed for reviewer guidance (anchors preserved by commits 60baca42/414606e5).

New frontier models (e.g. Claude Fable 5, released 2026-06-09) are dramatically more
capable at exactly this task shape: long-context, multilingual, audio-capable,
structured-output reasoning over messy text. The proposal: reintroduce correction as a
**fully optional, user-triggered, transparent suggestion layer in the review UI** —
never silently applied, every change individually accept/undo-able.

## 2. Why past attempts failed (evidence from git/docs history)

Three compounding killers, all documented in `docs/archive/` and LESSONS-LEARNED.md:

1. **Pipeline blocking + latency.** Agentic correction ran *inside* the job pipeline,
   one LLM call per gap (10–30s × 20–74 gaps = 5–37 min). Even after optimization
   (~55s/20 gaps, PR around 2026-01-08) it added minutes and required timeout
   band-aids (PR #149) that silently skipped correction anyway.
2. **Net-negative accuracy.** From PR #321: *"auto-correction was creating more work
   for reviewers by introducing errors that needed manual correction."* Both rule
   handlers and Gemini-Flash-per-gap corrections applied changes silently, so every
   wrong correction was invisible work added.
3. **Operational fragility.** Gemini 2→3 breaking changes (location=global, multimodal
   response format, 2025-12-30 doc), silent hangs, Langfuse/OTEL leakage.

Key insight: **the failures were architectural, not fundamental.** Per-gap calls with
tiny context windows, applied silently, in the critical path. None of those three
properties is necessary in 2026.

## 3. Evidence from 30 real production jobs

Dataset: `/Users/andrew/Projects/nomadkaraoke/lyrics-alignment-eval-dataset/` (built
2026-05-18 from GCS for the forced-alignment eval; 30 jobs, each with
`corrections.json` = state served to review UI, `corrections_updated.json` = human
ground truth, plus `vocals.flac` stem and reference-source metadata).
Analysis script: `analyze_human_edits.py` (added this session, same dir).

### Aggregate (word-level diff, pre- vs post-human-review)

- **9,877 words total; 10.0% text-edited** — 825 replaced, 153 deleted, 13 inserted
- Median job ~7% edits; range 0.4% (Juanes) to 45% (Saetia screamo)
- **Timing-only edits are rare**: ~1% of unchanged words, excluding one full-resync
  job (568d4317, 100% = global re-sync) and one partial (9229d13c, 5%)
- Reference quality predicts edit volume: jobs with best-source Jaccard ≥0.9 cluster
  at low edit rates; the heavy-rewrite jobs (Saetia 0.894-but-screamo, Bathory 0.693,
  Playa Fly 0.448) are where transcription hallucinated against noisy/extreme vocals

### Edit taxonomy (from sampled diffs)

| Category | Examples | Auto-correctable? |
|---|---|---|
| Sound-alike mishearings (dominant) | `glory`→`chlorine`, `ladder`→`matter`, `sell`→`sail`, `reincarnation`→`red carnation`, `Recht`→`Reich` | ✅ trivial with reference in context |
| Repeated chorus errors (same fix ×12) | `Hold it up`→`Holding out` ×12 (Parcels) | ✅ one decision, propagate |
| Contractions / inflections | `Won`→`Won't`, `burn,`→`burned,`, `I'll`→`I'd` | ✅ |
| Adlib/backing-vocal deletions | `(oh- oh- oh- oh)` deleted, `(Welcome to the camp)` deleted | ✅ with policy guidance |
| Censoring style | `Nigga,`→`N****,` | ✅ but needs explicit user-preference setting |
| Hallucinated lines (extreme vocals) | whole-line rewrites in Saetia/Bathory | ⚠️ possible with reference + audio, lower confidence |
| Non-English | German (Söhne Mannheims), French (Naika), Spanish (Juanes) | ✅ modern models are strongly multilingual |
| Full re-sync | Playa Fly timing offset | ❌ out of scope — keep tap-to-sync |

**Context size check:** a 78-segment song + 3 reference sources ≈ 10KB compact text
≈ 3K tokens. Whole-song single-call correction is trivially cheap (cents) and fast
(one ~10–30s call) vs the old 74-sequential-calls design.

## 4. Proposed approach: opt-in "AI Suggest" in the review UI

### Product shape

- A button in the lyrics review screen (e.g. in Header or above TranscriptionView):
  **"Suggest corrections (AI)"**. Nothing runs unless clicked. Zero change to the
  default journey.
- On click: backend endpoint makes **one whole-song LLM call** with: compact
  transcription (segments + words, ids preserved), all reference lyrics, anchor/gap
  annotations, artist/title, and explicit conservative instructions (only suggest
  when audio-plausible AND reference-supported; never invent).
- Response: list of proposed changes in `WordCorrection` shape (word_id-keyed,
  with `reason`, `confidence`, `source`).
- **Suggestions are a pending layer** — `corrected_segments` is NOT mutated until
  the user accepts. UI shows each suggestion inline (reuse the existing
  `CorrectedWordWithActions` accept/revert/edit/detail affordance + correction
  tooltips + `CorrectionDetailCard`), plus a summary bar: "23 suggestions —
  Accept all / Reject all / step through".
- Every accept/reject lands in the existing **history stack** (undo works) and
  **EditLog** (`operation: 'ai_suggestion_accept' | 'ai_suggestion_reject'`),
  which doubles as labeled training/eval data accruing from day one.

### Why this fixes each historical killer

| Past killer | This design |
|---|---|
| Pipeline blocking/latency | On-demand in review UI; user watches a progress state for one call; job pipeline untouched |
| Silently introduced errors | Nothing applied without explicit accept; wrong suggestions cost one click, not invisible rework |
| Per-gap myopia | Whole-song context: chorus repetition, reference alignment, song-level consistency in one shot |
| Provider fragility | Single thin endpoint, structured output, easy to swap models; failure = "no suggestions", never a stuck job |
| No trust measurement | Accept-rate per suggestion category measured from EditLog; promotion to default someday is a data decision |

### Architecture sketch

1. **Backend** — `POST /api/review/{job_id}/auto-correct` (follows the
   custom-lyrics endpoint pattern, `backend/api/routes/review.py`). Loads current
   correction data (or accepts the client's current working state in the request
   body so it reflects in-session edits), builds compact prompt, calls model,
   validates word_ids, returns `{suggestions: WordCorrection[], model, warnings}`.
2. **Frontend** — new state in `LyricsAnalyzer`: `pendingSuggestions`. Word
   rendering already supports correction highlighting; generalize the
   `handler === 'AgenticCorrector'` gate in `HighlightedText.tsx` (lines ~200, ~301)
   to a pending-suggestion check. Add summary/navigation bar (pattern:
   `GapNavigator`).
3. **Model layer** — reuse `correction/agentic/providers/` LangChain bridge
   (already supports Vertex Gemini, Anthropic, OpenAI, Ollama) or a simpler direct
   client; structured output schema = list of word-id-keyed operations
   (replace/delete/insert-after, new text, reason, confidence).
4. **Eval harness (build FIRST)** — script over the 30-job dataset: feed
   `corrections.json`, generate suggestions, score against the human diff from
   `corrections_updated.json`. Metrics: **precision** (would the human have made
   this change?) and **recall** (% of human edits reproduced). Precision is the
   north star — a low-recall/high-precision assistant is still a big win; the
   reverse destroys trust (that's what happened before).

### Phasing

- **Phase 0:** Eval harness + prompt iteration offline against the 30-job dataset.
  Compare models (Fable 5 / Opus 4.8 / Gemini) and prompt variants. Gate: precision
  target agreed with Andrew (suggest ≥90% before shipping even as opt-in).
- **Phase 1:** Backend endpoint + minimal UI (button → suggestions → accept/reject
  individually + accept-all). Ship to prod behind the button (it IS the flag).
- **Phase 2:** UX polish: category grouping (mishearings / deletions / formatting),
  propagate-identical-fix grouping, per-suggestion audio play button (segment
  playback already exists).
- **Phase 3 (experiment):** attach vocals stem audio for low-confidence gaps —
  models now accept audio input; dataset already has `vocals.flac` per job to eval
  this offline.
- **Someday:** auto-apply-by-default, only when prod accept-rate data earns it.

## 4b. Phase 0 results (2026-06-11): 5-model eval over all 30 jobs

Harness: `lyrics-alignment-eval-dataset/auto-correct-eval/harness.py`; results in
`auto-correct-eval/results/*.json`. One whole-song call per job; suggestions
scored against the human ground-truth diff. *Strict* = same span + same
normalized text as the human edit; *region* = touches a span the human also
edited; *gap_closed* = how much of the pre→truth distance accept-all closes.

| model | prec strict | prec region | recall strict | recall region | mean gap closed | median gap | harmful jobs | median latency |
|---|---|---|---|---|---|---|---|---|
| claude-fable-5 | **0.615** | 0.862 | **0.511** | 0.696 | **+0.505** | +0.77 | 2 | 27s |
| claude-opus-4-8 | **0.627** | **0.897** | 0.336 | 0.466 | +0.475 | +0.58 | 2 | 29s |
| gemini-3.1-pro-preview | 0.556 | 0.860 | 0.464 | 0.717 | +0.499 | +0.67 | 3 | 35s |
| gpt-5.5 | 0.442 | 0.832 | 0.357 | **0.726** | +0.502 | **+0.80** | 2 | 63s |
| claude-sonnet-4-6 | 0.209 | 0.710 | 0.174 | 0.632 | +0.091 | +0.34 | 4 | 14s |
| gpt-5.2 | 0.153 | 0.603 | 0.138 | 0.587 | **−0.796** | +0.24 | 11 | 11s |

Notes:
- GPT-5.5 (added after the first pass — it wasn't visible to the original
  API key's project) lands in the top tier on outcome quality (best median
  gap-closed, best region recall) but with the lowest strict precision of
  the tier and ~2× the latency (median 63s). GPT-5.2 actively damages
  lyrics in accept-all simulation — it would have repeated the 2025 failure.
  Sonnet 4.6 is also below the bar. **Fable 5, Opus 4.8, Gemini 3.1 Pro and
  GPT-5.5 are the viable tier**; the default-model decision below is
  unchanged by the GPT-5.5 result (slower, less precise, new billing
  surface). gpt-5.5-pro requires the OpenAI Responses API and wasn't
  evaluated.
- "Harmful jobs" are almost all near-perfect transcriptions (e.g. Townes Van
  Zandt: 1 human edit in 198 words) where the model proposes
  reference-supported style tweaks (`all right`→`alright`, adding a leading
  "And") that the human didn't bother making — mostly acceptable-or-one-click
  suggestions, not hallucinations. Strict precision therefore *underestimates*
  real-world accept rate; prod accept-rate from the EditLog is the metric that
  matters.
- Confidence is well calibrated (style tweaks 0.55–0.6, real fixes 0.8+), so
  the min-confidence knob meaningfully trades recall for precision.
- Cost ≈ $0.05–0.18/job depending on model. Latency 27–35s for the top tier.

**Decision: v1 default `AUTO_CORRECT_MODEL=gemini-3.1-pro-preview`** — within
noise of Fable 5 on gap-closed (0.499 vs 0.505), best region recall, already
served via Vertex AI in this GCP project (same path as custom-lyrics), no new
secret/billing surface. Fable 5 is the quality ceiling (best strict
recall/balance); switching is a one-env-var change and worth revisiting once
prod accept-rate data exists or if an Anthropic key is added to Secret Manager.

## 4c. Decisions captured from Andrew (2026-06-11)

1. **Models**: test Fable 5 + a cheaper Claude + latest GPT + latest Gemini Pro
   — done (table above).
2. **Scope**: text-only v1 agreed. Later: simple *heuristics* (not LLM) for
   timing pathologies — e.g. AudioShake leaving an unrealistically long last
   word on a segment, or transcribing a 10+ second instrumental as one long
   "And"/"But". Future work.
3. **Knobs**: user-facing with sensible defaults → implemented
   (adlib-removal toggle, insertions toggle, confidence filter).
4. **No-reference songs**: button disabled with explanation (backend also 422s).
   Future experiment (separate plan doc someday): enrich context via audio
   analysis (genre/theme/key/energy), music-matching APIs and web research to
   review transcriptions without references.
5. **Precision gate**: "depends how you measure" — strict precision is the
   conservative floor (0.56–0.63 for the top tier), region precision 0.86–0.90.
   Shipped as fully opt-in suggestions; prod accept-rate from EditLog
   (`ai_suggestion_accept`/`reject` entries) becomes the real gate for any
   future default-on behaviour.

## 4d. Follow-up shipped (2026-06-11, v0.178.0): Fable default + multi-model compare

Per Andrew's decisions after v0.177.0 shipped:

- **Default model switched to Claude Fable 5** (`AUTO_CORRECT_MODEL=claude-fable-5`
  in the Cloud Run deploy). Anthropic provider path added to the service
  (Anthropic API, structured outputs, adaptive thinking — same configuration
  the eval used). Key lives in the `anthropic-api-key` secret (container
  Pulumi-managed in `infrastructure/modules/secrets.py`, value added manually).
- **Multi-model compare mode** (Andrew's idea): `compare_models` setting
  queries all of `AUTO_CORRECT_COMPARE_MODELS` (prod: Fable 5 + Gemini 3.1
  Pro) in parallel, dedupes identical suggestions with a consensus count
  (e.g. "2/2 models"), and groups conflicting overlapping suggestions —
  accepting one variant rejects its siblings; accept-all picks the
  highest-consensus variant per group. Partial model failures degrade to
  warnings; all-fail → 502.
- **Gotcha for posterity**: the google-genai SDK mutates `response_schema`
  dicts in place (injects `property_ordering`), which the Anthropic API
  rejects — schemas must be deep-copied per provider call. Caught by the
  real-call smoke test, now covered by a regression test.

## 5. Open questions for Andrew

1. **Model:** start with Claude Fable 5 (best reasoning, audio-capable) via the
   existing Anthropic provider, or stay all-Google (Vertex Gemini 3) for billing
   consolidation? Eval harness can score both — recommend deciding on data.
2. **Suggestion scope v1:** text-only (replace/delete/insert)? Timing edits are
   ~1% of human work and risky — recommend excluding from v1.
3. **Policy knobs:** adlib deletion and censoring are taste decisions — bake into
   prompt defaults, or expose as settings like `CustomLyricsSettings` does
   (allow_reword/strictness pattern)?
4. **No-reference behavior:** when no internet source matched (custom/niche songs),
   suggestions are audio-only guesses — disable the button, or allow with a
   warning?
5. **Precision gate:** what offline precision threshold before Phase 1 ships?

## 6. Key file references

- Correction types & pipeline: `karaoke_gen/lyrics_transcriber/types.py`
  (`Word`, `LyricsSegment`, `WordCorrection`, `AnchorSequence`, `GapSequence`,
  `CorrectionResult`), `correction/corrector.py`, `correction/anchor_sequence.py`
- Old agentic system (reusable provider layer): `correction/agentic/`
- Review API: `backend/api/routes/review.py` (`correction-data`, `complete`,
  `custom-lyrics/generate` — the pattern to follow)
- Review UI: `frontend/components/lyrics-review/LyricsAnalyzer.tsx` (history/undo,
  EditLog), `shared/HighlightedText.tsx`, `CorrectedWordWithActions.tsx`,
  `CorrectionDetailCard.tsx`, `GapNavigator.tsx`,
  `modals/CustomLyricsMode.tsx` (closest end-to-end LLM feature pattern)
- Frontend types: `frontend/lib/lyrics-review/types.ts`
- Eval dataset: `/Users/andrew/Projects/nomadkaraoke/lyrics-alignment-eval-dataset/`
  (30 jobs; `analyze_human_edits.py` for the human-edit ground-truth diff)
- History: `docs/archive/2025-12-30-gemini3-agentic-correction-fix.md`,
  `docs/archive/2025-12-31-agentic-timeout-implementation.md`,
  `docs/archive/2026-01-08-performance-investigation.md`, PR #321 (disable), PR #149
  (timeouts), PR #738 (custom lyrics LLM mode)
