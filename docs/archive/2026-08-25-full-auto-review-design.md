# Toward Fully-Automated Review (skip lyrics review + instrumental selection when confident)

**Status:** Design / research program. Started 2026-08-25.
**Author:** Andrew (spec) + agent (design).
**Worktree:** `karaoke-gen-full-auto-review`

---

## The goal (Andrew's words, verbatim spec)

> Ideally I'd like to make the karaoke-gen system even more automated, potentially _fully_
> automated (skipping the lyrics review and instrumental selection processes entirely) for songs
> where for both decisions, we have enough heuristics in place to be pretty confident there are no
> potential issues warranting human input.
>
> However, to actually reach that confidence bar is non-trivial without risking sloppy releases
> (which was originally the whole point of the lyrics review UI still being a manual human step).
>
> Please help me reach that level of automation, starting out with the easy cases where both synced
> lyrics are perfect and there's no subjectivity in the backing vocals decision. Then once we have a
> couple of working fully auto examples, we can start reviewing every job I complete the
> review+select process for, and for each record:
> - exactly what did I correct manually in the lyrics review process, and why
> - what did I hear when I previewed the backing vocals, how did i make the decision about backing
>   vocals vs. clean instrumental.
>
> once we've recorded the output of me making at least 10 tracks, recording my answers for both of
> those diligently, in detail, via interactive claude session where I can both describe and share
> screenshots... then we can start brainstorming what types of automated checks and (post-AI
> correction) heuristic fixes we might be able to apply to fully "solve" (fully automate with
> confidence) more whole classes of tracks in the lyrics review. and brainstorm how we could fully
> automate the backing vocals decision, turn it into a simple "retain backing vocals where possible?"
> (yes/no/review) toggle option made up-front during job submission (defaulting to on).

Context: Andrew is currently submitting Vocal Star **tenant** test jobs. This applies to **both**
public and tenant sides of the product.

---

## Guiding principle: shadow-first, never sloppy

The review UI exists precisely to prevent sloppy releases. So we do **not** flip a switch and hope.
The rollout is:

1. **Shadow** — compute an "auto-approvability" verdict on *every* job at the review gate, record it,
   but keep sending the job to human review as normal. No behaviour change, zero release risk.
2. **Record** — for every job Andrew reviews, capture (a) exactly what he changed in the lyrics and
   why, (b) what he heard in the backing-vocals preview and how he decided. Store alongside the shadow
   verdict + all signals that existed at gate time.
3. **Validate** — over ≥10 tracks, check the core hypothesis: **does a "would auto-approve" verdict
   reliably coincide with "Andrew changed nothing / made a non-subjective clean call"?** Tune
   thresholds against this real ground truth.
4. **Enforce (narrow)** — only once validated, let the gate actually skip review for the provably-easy
   intersection, starting maximally conservative and widening class-by-class as the corpus grows.

This gives Andrew the "couple of working fully-auto examples" he asked to start with (the narrow safe
intersection can be enforced early), while the recording program de-risks widening it.

---

## What the system already gives us (investigation results)

### Lyrics confidence signals (all already in `jobs/{id}/lyrics/corrections.json`)

`CorrectionResult` (`karaoke_gen/lyrics_transcriber/types.py:609`) serialized per job. Auto-correction
is **disabled** in prod, so `corrected_segments` ≈ raw AudioShake transcription and `corrections_made`
≈ 0. Useful fields:

- `original_segments` — raw transcription (words + timings + ids).
- `anchor_sequences[]` — runs of transcribed words that matched reference lyrics, each with its own
  `confidence` and `reference_positions` (which sources agreed).
- `gap_sequences[]` — the **uncertain regions** between anchors (transcribed words with no confident
  reference match). Few/short gaps ⇒ high confidence.
- `metadata.rejected_sources` — per reference source: `relevance`, `matched_words`, `total_words`.
  Strong multi-source agreement is the best proxy for "these lyrics are truly right."
- `metadata.anchor_sequences_count`, `gap_sequences_count`, `total_words`.
- Per-word `confidence` — **only present for Whisper**, absent for AudioShake (prod). Treat as optional.

**Derived gate signals** (mirroring `frontend/components/lyrics-review/CorrectionMetrics.tsx:92-99`):
- `anchor_word_fraction` = anchored words / total words → want ≈ 1.0
- `uncorrected_gap_fraction` = gap words not corrected / total → want ≈ 0.0
- `reference_agreement` = ≥N sources with high `matched_words/total_words`

⚠️ **Trap:** the top-level `CorrectionResult.confidence` is `1.0` whenever correction is *disabled*
(`controller.py:593-594`), not because lyrics are perfect. Never gate on it.

**Strongest "easy case" of all:** a user/tenant-supplied **synced** reference (LRC) that the
transcription matches at ~100% anchor coverage with zero gaps. Vocal Star jobs frequently ship their
own instrumental + known-good lyrics — a prime full-auto candidate class.

### Backing-vocals signals (already in `state_data.backing_vocals_analysis`)

`AudioAnalyzer` (`karaoke_gen/instrumental_review/analyzer.py`) analyses the isolated
`backing_vocals.flac` stem: `has_audible_content`, `audible_percentage`, per-segment
`avg/peak_amplitude_db`, and a `recommended_selection` (CLEAN / WITH_BACKING; **never** REVIEW_NEEDED).

- **Non-subjective case:** `has_audible_content == False` ⇒ there are no backing vocals to retain ⇒
  clean is unambiguously correct. Safe to auto-decide today.
- **Subjective case:** audible backing content exists → clean-vs-with is a taste/quality call
  ("does it sound good / help singers / is it just vocal bleed?"). This is what the recording program
  must characterise before we can automate it. The current energy heuristic cannot: it has no measure
  of backing-vocal *quality* or of the delta between the two mixes.

### Where review happens / the insertion point

- `screens_worker.py:214` unconditionally transitions every job → `AWAITING_REVIEW`. **This is where an
  auto-skip gate goes.** The `AWAITING_REVIEW → REVIEW_COMPLETE` transition is already legal
  (`job.py:152`).
- `POST /api/review/{id}/complete` (`review.py:472`) is what a human submit does: writes
  `corrections_updated.json` (only if there are edits), sets `state_data.instrumental_selection`,
  transitions to `REVIEW_COMPLETE`, triggers render. An auto-path must replicate this — including
  supplying a default `instrumental_selection` (the selection requirement is enforced only in the HTTP
  endpoint, not the state machine).
- Render consumes `state_data.instrumental_selection` (`clean`|`with_backing`|`custom`); default when
  absent is already `clean` (`video_worker.py:108`).

### Existing "what changed" records (insufficient for our purpose)

- `review_sessions` `changed_words[]` (`review_session.py:14`) — per-word diff, **no why**.
- `annotations.json` `CorrectionAnnotation` (`review.py:1345`) — has `agentic_agreed`, **no free-text
  why, nothing about backing vocals.**

Neither captures *reasoning*, and nothing captures the backing-vocals listening decision. **That gap is
the core of the recording program.**

---

## Plan

### Phase 0 — Recording + shadow-scoring harness (foundation, zero release risk)

A **plain-files + script** tool (honouring "meta-tooling = plain files + slash commands, never a new
UI"). No frontend, no production code-path change.

**0a. `AutoApprovabilityScorer` (pure function, shared module).** Input: `corrections.json` +
`backing_vocals_analysis`. Output: structured verdict per axis —
`lyrics: {verdict: auto|review, signals:{anchor_word_fraction, uncorrected_gap_fraction,
reference_agreement, total_words, has_synced_reference, ...}, reasons:[...]}` and
`backing: {verdict: clean|with|review, signals:{has_audible_content, audible_percentage, loud_segments,
...}, reasons:[...]}`. Starts with deliberately conservative thresholds. Fully unit-tested against
fixtures. This same module later powers the real gate — build it once, use it in shadow first.

**0b. `scripts/review_capture.py` (the recording tool).** Given a completed/ reviewed job id:
- Pulls `corrections.json` + `corrections_updated.json` + `backing_vocals_analysis` (+ chosen
  `instrumental_selection`) from GCS/Firestore (read-only; works under `claude-readonly` ADC).
- Computes the **structured lyrics diff** (word-text edits, timing nudges, splits/merges, insertions,
  deletions) = exactly what Andrew changed.
- Runs the shadow scorer and prints the verdict + signals next to what Andrew *actually* did.
- Emits a per-job record and appends to a corpus.

**0c. The corpus** (`docs/automation-corpus/` or similar, in-repo, plain files):
- `jobs/{job_id}.md` — human-readable: song, signals, shadow verdict, the diff, and Andrew's dictated
  **why** (lyrics) + **backing-vocals reasoning** (what he heard, how he decided). Screenshots welcome.
- `corpus.jsonl` — one machine-readable row per job for later threshold calibration.
- `README.md` — running tally toward the ≥10-track target + emerging patterns.

**0d. Interactive flow** (this is the "via interactive claude session" Andrew described): after Andrew
finishes a review, we run the tool, look at the diff + signals together, and he dictates the reasoning;
the agent writes it into the corpus. Screenshots pasted into the session get referenced.

**0e. (optional, tiny) shadow logging in prod** — write the scorer's verdict into
`processing_metadata.auto_approvability` at the `screens_worker` gate, without changing behaviour, so
we accumulate verdicts on jobs even when we don't run the capture tool. Deferred until 0a-0d prove out.

### Phase 1 — Narrow, conservative enforcement (the "couple of fully-auto examples")

Once a handful of jobs in the corpus show the safe intersection firing correctly, enable **actual**
auto-skip in `screens_worker` for that intersection **only**:
- Lyrics: synced reference present AND anchor_word_fraction ≥ (high) AND uncorrected_gap_fraction ≈ 0.
- Backing: `has_audible_content == False` → auto-select `clean`.
- Everything else → human review, unchanged.
- Guardrails: feature-flagged (env), tenant-scoped opt-in first (Vocal Star), full audit trail in
  `processing_metadata`, and a kill switch. Auto-approved jobs still get every downstream QC that exists.

### Phase 2 — Widen by class (after ≥10 recorded tracks)

Brainstorm + build **post-AI-correction heuristic fixes** and **checks** that let whole classes pass
without human input, driven by patterns in the corpus (e.g. common mechanical corrections Andrew makes
that we can auto-apply and then re-verify; classes of gap that are reliably safe to accept).

### Phase 3 — Backing-vocals up-front toggle

Add `retain_backing_vocals: yes|no|review` (default `yes`) to submission (`URLSubmissionRequest` /
`UploadSubmissionRequest` → `JobCreate` → `state_data`). Semantics:
- `no` → force `clean`, skip instrumental step.
- `yes` → retain backing **where the (improved) analysis says it's safe**, else fall back per policy.
- `review` → today's human behaviour.
Requires a better backing-vocal quality signal than the current energy threshold — the corpus reasoning
is what tells us what "sounds good enough to retain" actually means.

---

## Day-1 validation (scorer + capture harness, 2026-08-25)

Built the shared scorer (`backend/services/auto_approval/`), the review-diff, and
`scripts/review_capture.py`, then shadow-scanned 30 recent completed jobs (read-only):

- **1/30 would fully auto-approve today** — `56411c70` Nat King Cole "Never Let Me Go":
  100% anchor, 0 gaps, **0 lyric edits**, no audible backing → clean. Independently the
  scorer said AUTO. That's the first genuine fully-auto example, with no false positives in
  the batch.
- **The predictive hypothesis broadly holds:** high anchor / low gap ⇒ few edits; low anchor
  ⇒ many edits (e.g. 61.6% anchor → heavy correction). But it is *not* perfectly monotonic —
  some ~95-98% jobs needed only a handful of edits, one 94% job needed 269. This is exactly
  why the reasoning corpus is needed: to learn *which* edits the high-coverage jobs still need.
- **Key threshold finding → applied:** job `79c4f60c` "Clarity" scored 99.6% anchor with a
  **single gap word**, which turned out to be a real mis-transcription the reviewer fixed
  (`I` → `High`). A gap word is by definition a word with no confident reference match — where
  errors hide. So the AUTO tier was tightened from "≤1% gap" to **zero unresolved gaps**
  (`AUTO_MAX_GAP_FRACTION = 0.0`). This demotes Clarity to `near-miss`/review while keeping
  Nat King Cole as AUTO. Regression test: `test_single_gap_word_blocks_auto`.
- **Diff quality:** the review UI re-keys a word's id when its text is replaced but preserves
  its timing/line, so text corrections first appeared as delete+insert. The diff now pairs
  them into clean **replacements** (`Carl → Karl`, `they're → their`, `you've → you`).

Deliverables this session (all tested, 21 unit tests):
`backend/services/auto_approval/{models,scorer,lyrics_diff}.py`,
`scripts/review_capture.py`, corpus scaffold at workspace-root `docs/automation-corpus/`.

## Session 2 update (2026-08-25) — replay tool + corrected baseline

Andrew refined the plan: instead of capturing via offline markdown diffs, **replay the
real review UIs** for past completed jobs (full audio/backing previews from GCS) and
narrate the decisions, then walk the **20 most-recent `admin@nomadkaraoke.com` jobs**.

**Critical correction to the lyrics baseline.** AI auto-correct is doing the heavy
lifting (proactive suggestions, accepted in the review UI). The **authoritative record
of what happened is the `edit_log_*.json`** (ordered, typed ops: `ai_suggestion_accept`
/ `ai_suggestion_reject` / manual `word_*` / `timing_change`), plus `ai_corrected` /
`original_text` / `timing_estimated` provenance baked into saved words. My earlier raw
`corrections.json → corrections_updated.json` id-diff was misleading (word-id
regeneration inflated del/ins). **Right baseline = post-AI; the interesting signal =
residual manual edits + AI rejections (⚠️ superseded: session-3 established these are the auto-apply's own conflict resolution, NOT human decisions — see session-4 update) + timing.**

Findings from the 20 admin jobs (edit_log counts): AI solves lyrics outright on several
(0 manual, 0 reject: d508adb6, 69ca7c1e, 507513ba, b5a7b8aa, 35ed9697, f986dfe5); AI
rejections cluster the failure modes (f247364e=15, 44622ffa=5, 8e465dc7=4); residual
manual concentrates in a few (a4dcaa21=17, 95d8e844=12). **⚠️ Zero `timing_change` ops
logged across all 20** despite timing being a stated axis — open question for the sessions.

**Built + verified end-to-end against real prod data (read-only):**
- Backend replay: `GET /correction-data?replay=true` (full-auth only) skips the status
  gate + transition and attaches the `edit_log`. `review.py`.
- Dev audio proxy: `REVIEW_AUDIO_PROXY=1` streams review audio **bytes** (local user ADC
  can't sign GCS URLs; impersonating the backend SA is denied and granting it is a GCP
  write we won't make). New `GET /{job}/dev-audio` (prod-inert). Wired into
  correction-data, instrumental-analysis, `_stream_audio`, waveform.
- Frontend replay: `?replay=1` bypasses the state gate (admin), passes `isReadOnly` to
  the already-wired `LyricsAnalyzer` + new `isReadOnly` on `InstrumentalSelector`, and
  renders a **ReplayActionLog** panel (AI✓/AI✗/manual/timing). `client.tsx`.
- Run locally: `./scripts/run-replay-local.sh` + `cd frontend && npm run dev`. See
  `docs/REPLAY.md`. Job queue: private corpus `docs/automation-corpus/REVIEW-QUEUE.md`.
- Tests: 5 new backend (replay gate/auth + dev-audio inert), 61 review-suite pass; 21
  scorer/diff tests pass; frontend changed files typecheck clean.

**Remaining (post-sessions):** refine AI-vs-manual attribution into the corpus
(edit_log-driven), design timing-adjustment detection (incl. vocal-audio signal for
cut-off sustained words), and the backing-vocals decider + up-front toggle
(retain-where-possible / clean / review).

## Open questions / decisions

- **Corpus location & format** — `docs/automation-corpus/` in this repo vs workspace-level. (Leaning
  in-repo so it travels with the code that consumes it.)
- **How much to enforce early** — stay in pure shadow until ≥N corpus jobs, or enable the ultra-narrow
  intersection as soon as it's proven on 2-3 jobs (Andrew's "couple of working examples").
- **Public vs tenant first** — tenant (Vocal Star, own instrumental + known lyrics) is the safest place
  to enable narrow enforcement first.

---

## Key file/line index (for implementers)

| Concern | Location |
|---|---|
| Correction data model | `karaoke_gen/lyrics_transcriber/types.py:609` (`CorrectionResult`) |
| Confidence/metadata computation | `karaoke_gen/lyrics_transcriber/correction/corrector.py:203-256` |
| `confidence=1.0`-when-disabled trap | `karaoke_gen/lyrics_transcriber/core/controller.py:593-669` |
| Review gate (insertion point) | `backend/workers/screens_worker.py:214` |
| Human review-complete behaviour to mirror | `backend/api/routes/review.py:472-604` |
| Legal auto transition | `backend/models/job.py:152` (`AWAITING_REVIEW→REVIEW_COMPLETE`) |
| BV energy analysis + recommendation | `karaoke_gen/instrumental_review/analyzer.py:80-165, 380-409` |
| BV analysis invocation + storage | `backend/workers/screens_worker.py:533-637`, `state_data.backing_vocals_analysis` |
| Instrumental selection → stem mapping | `backend/workers/video_worker.py:1341-1380,1438-1464` |
| Submission-time options | `backend/models/requests.py:8-40`, `backend/models/job.py:540-609` (`JobCreate`) |
| Existing "what changed" records | `backend/models/review_session.py:14`, `backend/api/routes/review.py:1345` |

---

## Session 4 update (2026-08-27) — shadow gate WIRED + scorer v0.2.0

The shadow scorer is now wired into the pipeline (`screens_worker._record_auto_approval_shadow`,
called just before the AWAITING_REVIEW transition; non-fatal; writes
`processing_metadata.auto_approval_shadow`). Scorer v0.2.0 adds, from the 20-job corpus findings:

- **P3 vocalization gate** (runs of ≥5 vocalization tokens / multi-second "Ooh"s) and
  **P8 phantom gate** (words >5s; stretched short parenthetical lines) — never-auto classes that
  override everything.
- **AI-suggestion awareness**: the proactive auto-correct cache
  (`jobs/{id}/lyrics/auto_correct_cache/`) is loaded at scoring time; gap words *covered* by a
  suggestion are auto-fixed on review load, so confidence keys off the **uncovered** remainder.
  New AUTO tier `ai-resolved`: synced ref + anchor ≥90% + uncovered ≤2% and ≤6 words + no gates.

Calibration vs the 20-job corpus (full table in the private corpus,
`docs/automation-corpus/scorer-calibration-2026-08-27.md`): **safety 100%** (all truly-human jobs
gated), lyrics auto 3/20, overall auto 1/20. Raw gap counts demonstrably do NOT separate AI-solved
from human-needed jobs; suggestion coverage does.

Next per SYNTHESIS build order: collect shadow verdicts on live traffic; build the Pattern 4
leading-connective auto-fixer (+P1 dedupe, P5 reference-majority); then revisit thresholds and
begin enforcement (tenant jobs first).

---

## Session 4b update (2026-08-27, same session) — ENFORCEMENT SHIPPED

Andrew's directive: *"keep driving towards eventually shipping fully auto processing, on by
default, for all gen users, only showing the lyrics and/or instrumental review screens when one
or the other is unsolved / definitely needs human review"* → shipped the framework + the
narrow fully-confident class:

- **`Job.review_mode`** (`"auto"` default | `"always_review"`, admin-PATCH-editable) — the
  autonomy/review-level setting. Legacy `non_interactive` is untouched (CLI packaging semantics;
  never gated the cloud review).
- **`backend/services/auto_approval/apply.py`** — faithful server-side port of the review UI's
  on-load auto-apply (`autoCorrectApply.ts` + `autoCorrectConflicts.ts` + `acceptAll`):
  replace/insert_after/delete ops, timing distribution, word provenance flags, conflict-group
  winners by consensus→confidence. Plus `find_suspicious_duplicates` (the P1 self-conflict
  duplicate-word signature, reference-aware).
- **`backend/services/auto_approval/executor.py`** — `maybe_auto_complete_review(job_id, trigger)`
  called from `screens_worker` (pre-AWAITING_REVIEW) and `audio_worker` (post-analysis, for
  audio-lags-lyrics ordering). Scores + records `processing_metadata.auto_approval` on EVERY job;
  enforces only when: flag on + review_mode=auto + not made-for-you + no existing/custom
  instrumental + `overall_auto` + audio complete + clean stem present. Enforce = apply cached AI
  suggestions server-side → sanity checks (stale/duplicates/empty ⇒ abort to review) →
  `corrections_updated.json` → instrumental=clean → clear progress keys →
  GENERATING_SCREENS→REVIEW_COMPLETE (new legal edge; skips the review notification entirely) →
  trigger render worker. FAIL-SAFE: every anomaly/exception falls back to normal human review.
- **Backing-analysis-error trap fixed**: a failed analysis stores `has_audible_content=None`,
  which previously read as `False` ("no audible content") in the scorer — would have wrongly
  auto-picked clean. Now treated as analysis-absent → review.
- Kill switch: `AUTO_APPROVAL_ENFORCE_ENABLED=false` (scoring/recording continues).

Expected initial auto rate ~5% of jobs (1/20 in the calibration corpus): lyrics must be
synced-perfect or ai-resolved AND backing must be non-subjectively clean. Widening comes from
build-order #2-#4 (P4 fixer, vocalization detector already gating, backing decider).

## Session 5 update (2026-08-28) — Phase 2A: deterministic P4/P1/P5 fixers (v0.203.0)

Build-order item #2 shipped: the three mechanical residual-edit classes from the corpus are now
**deterministic suggestion generators inside the auto-correct pipeline** (not executor hacks), so
one mechanism feeds the review UI's on-load auto-apply, the executor's server-side apply, the
scorer's gap-coverage signal, and the proactive cache.

- **`backend/services/auto_correct/deterministic.py`** (new) — pure generators keyed off
  `corrections.json` gap/anchor alignment:
  - **P4 leading-connective delete**: segment starts with And/But/So/Oh/A, the word is a gap
    word, and no reference source's reading of that gap contains it → emit `delete` (+ a
    `replace` re-capitalizing the next word when lowercase). Guard: skipped when the next word is
    a vocalization token (an "Oh- whoa," run is musical judgement, corpus 5c80991d).
  - **P5 reference-majority replace**: a gap whose transcription contains an implausible proper
    noun (capitalized mid-line token absent from every reference) and where ≥2/3 of reference
    sources agree on the same reading → replace with the majority reading. The red flag is
    REQUIRED — plain 2/3 majority over-fires on gaps the human deliberately left (6d0640fa
    "though"→"dog", explicit-lyrics rewrite). Spelling-variant guard: a red-flag token
    string-similar (≥0.75) to a reference token is a transliteration/truncation ("Crick"~
    "Cricket", "Projectorinsky"~"Projektorinski") whose gap alignment is often junk → skip
    (that's the LLM's fix).
  - Per-source gap readings come from the gap's own `reference_word_ids`, else are derived by
    walking the reference stream past the surrounding anchors (the 6d0640fa "Come here," case —
    the aligner had empty alignments for 2 of 3 sources).
- **P1 self-conflict grouping at source** — `service._assign_conflict_groups` (extracted, reused
  post-integration) now also unions an `insert_after` with any suggestion targeting/anchored on
  the same word whose new_text shares a token: the f6439692 "insert you're + replace fire→fire,
  you're → 'you're you're'" signature becomes a pick-one conflict group (winner by consensus→
  confidence picks the 0.95 replace — correct). Executor's `find_suspicious_duplicates` abort
  stays as belt-and-braces.
- Pipeline: `suggest(..., correction_data=None)` — proactive worker passes corrections.json in;
  the review route lets the service fetch it (best-effort). Deterministic suggestions identical
  to an LLM one just tag it (`models += ["deterministic"]`); new ones append; conflict groups are
  recomputed over the combined set. Suggestion-cache key bumped to v2 (old cached results predate
  the fixers).
- **Corpus validation** (private validate_scorer.py, extended with FIXER assertions): safety
  still 100% (all MUST_NOT_AUTO stay review; gates unchanged). P4 fires on all six corpus
  examples; P5 reproduces Andrew's one ref-majority edit ("yo Mick,"→"Come here,") and fires
  nowhere it must not. **6d0640fa (Miguel) now scores lyrics=auto + clean backing → would fully
  auto-ship WITH the human's exact edit applied**; ae0cd7e8 (Samson corpus job) now has its P4
  edits actually applied when auto-shipping. `ai-resolved` caps deliberately NOT loosened
  (69ca7c1e still 14 uncovered words — needs more coverage, not looser caps).
