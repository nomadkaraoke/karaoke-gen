# Syllable-Aware Custom Lyrics — Design

**Date:** 2026-05-02
**Status:** Approved (brainstorm), ready for implementation plan
**Author:** Andrew (with Claude Code, brainstormed in worktree `karaoke-gen-custom-lyrics-syllables`)
**Supersedes:** Augments `docs/archive/2026-04-28-llm-custom-lyrics-design.md` (the v1 LLM custom-lyrics design)

## Problem

The Custom Lyrics LLM mode (shipped 2026-04-28, PR #738) outputs the right *number* of lines but routinely ignores syllable / rhythm budgets. On the canonical Year-5-Stars / Shake-It-Off case:

| # | Original (Shake It Off) | syl | Current AI output | syl | Δ |
|---|---|---|---|---|---|
| 1 | I stay out too late | 5 | We walk in the room, yeah we own this place | 10 | +5 |
| 2 | Got nothin' in my brain | 6 | Anna hits the floor, watch her lead the way | 10 | +4 |
| 3 | That's what people say, mm- mm | 7 | Grace brings the rhythm, dancing every day, mm- mm | 13 | +6 |
| 11 | Sayin', "It's gonna be alright" | 7 | Sayin', "We're gonna win this time" | 7 | ✓ |
| 41 | I- I- I shake it off, I shake it off | 8 | We- we- we're Year 5 stars, we're Year 5 stars | 8 | ✓ |

The LLM nails it when the original line is short and structurally salient (lines 11, 41). It blows the budget by 30–100% when the client gives long substantive material and the LLM doesn't trim.

The existing system prompt already says *"Try to preserve the syllable count of the original line where possible"* — but there's no measurement, no enforcement, and no per-line target communicated to the model.

## Goal

Make the Custom Lyrics generator **syllable-aware** with deterministic measurement and an LLM repair loop, and expose configurable controls (3 toggles + 5-position strictness slider) so operators can pick their own tradeoff between client-text fidelity and singability.

## Non-goals

- Streaming / progressive UI — single synchronous spinner with iteration count badge is enough
- Stress-pattern / rhyme alignment in v1 — syllable count is the primary target
- Auto-running re-sync after save (operator does this manually as today)
- Sectional time re-allocation when line count varies — flat proportional split is the v1 save path
- Per-line LLM generation in v1 — whole-song call with repair loop; per-line is a fallback
- Pre-flight feasibility check warning operator before generation — follow-up
- Cost cap per generation — credits cover it; not needed
- A web UI for human review of eval outputs — markdown is enough for v1
- Wiring the eval harness into `make test` or PR CI — manual run during development

## High-level shape

Three pieces:

1. **A shared `SyllableCounter` utility** extracted from the existing `karaoke_gen/lyrics_transcriber/correction/handlers/syllables_match.py` `_count_syllables` method. Reused by the existing handler and the new custom-lyrics service. Returns 4 counts per word/line (spacy-syllables, pyphen, NLTK cmudict, syllables library) — no single canonical number.

2. **A validate-and-repair loop in `CustomLyricsService`**: build prompt with per-line syllable + time-budget metadata → call Gemini → score with the validator → if violations remain, build a targeted repair prompt for those specific lines and re-call → loop with plateau detection up to `max_iterations` (driven by strictness).

3. **A first-class eval harness at `backend/eval/custom_lyrics/`** with a 5-fixture corpus (Year 5 + 4 handcrafted), a record-and-replay LLM cache, baseline diffing, markdown reports designed for human review, and concrete v1-ships criteria (≥75% lines passing ±2 on Year 5, ≥70% macro on the corpus).

Plus 4 new operator controls in the modal (3 toggles + 5-position slider) that map deterministically to prompt fragments, validator tolerance, and retry budget.

## Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │ frontend/components/.../CustomLyricsMode.tsx │
                    │ paste/upload + 3 toggles + strictness slider │
                    │ preview phase: per-line syllable annotations │
                    └────────────────────┬────────────────────────┘
                                         │  POST /custom-lyrics/generate
                                         │  payload: {existing_segments, custom_text/file,
                                         │            notes, settings: {allow_reword,
                                         │            allow_omit, fixed_line_count,
                                         │            strictness}}
                                         ▼
       ┌──────────────────────────────────────────────────────────────────┐
       │ backend/api/routes/review.py — generate_custom_lyrics endpoint   │
       │ (thin: parse multipart, validate settings, call service)         │
       └──────────────────────────┬───────────────────────────────────────┘
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │ backend/services/custom_lyrics_service.py — orchestrator                 │
   │  1. compute per-line metadata (target_syllables[4], time_budget,         │
   │     observed_rate)                                                       │
   │  2. build settings-conditioned prompt                                    │
   │  3. call Gemini once with whole song                                     │
   │  4. score(candidate, target, tolerance) → list[LineValidation]           │
   │  5. if violations remain and not plateaued: build repair prompt, recall  │
   │  6. cap at max_iterations (driven by strictness); track best iteration   │
   │  7. return CustomLyricsResult with rich metadata                         │
   └──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │ Shared utility (NEW):                                                    │
   │ karaoke_gen/lyrics_transcriber/utils/syllable_counter.py                 │
   │ Class SyllableCounter:                                                   │
   │   count_per_word(words: list[str]) -> list[int]   # 4-method counts      │
   │   count_per_line(line: str)        -> list[int]                          │
   │   any_method_within(candidate, target, tolerance) -> bool                │
   │ Initialised once per process; reuses backend.services.{spacy,nltk}_      │
   │ preloader so cold-start cost ≈ 0.                                        │
   └──────────────────────────────────────────────────────────────────────────┘
```

## SyllableCounter (shared utility)

Extracted from `karaoke_gen/lyrics_transcriber/correction/handlers/syllables_match.py` `_count_syllables` and friends. Located at `karaoke_gen/lyrics_transcriber/utils/syllable_counter.py` so both the existing `SyllablesMatchHandler` and the new `CustomLyricsService` import from one place.

**Public surface:**

```python
class SyllableCounter:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None: ...
    def count_per_word(self, words: list[str]) -> list[int]:
        """Returns [spacy_count, pyphen_count, nltk_count, syllables_count]."""
    def count_per_line(self, line: str) -> list[int]:
        """Tokenise + count_per_word; returns [s, p, n, syl]."""
    def any_method_within(
        self,
        candidate_counts: list[int],
        target_counts: list[int],
        tolerance: int,
    ) -> bool:
        """True iff any pair across the 4×4 method combinations agrees within tolerance."""
```

`SyllablesMatchHandler` is refactored to delegate to this utility — its `_count_syllables*` methods become thin wrappers, no behavior change. Existing tests for the handler must continue to pass.

The "any method agrees" check (`min(|c - t| for c in candidate_counts for t in target_counts) <= tolerance`) mirrors the existing handler's logic at `syllables_match.py:187`.

## Settings model

Four operator-facing controls, plumbed through frontend → API → service → prompt + validator.

```python
class StrictnessLevel(str, Enum):
    VERBATIM = "verbatim"
    LOOSE = "loose"
    BALANCED = "balanced"
    TIGHT = "tight"
    STRICT = "strict"


@dataclass
class GenerationSettings:
    allow_reword: bool = True
    allow_omit: bool = True
    fixed_line_count: bool = True
    strictness: StrictnessLevel = StrictnessLevel.BALANCED
```

### Strictness → backend params

| Strictness | Tolerance (per line) | Max iterations | Prompt phrase |
|---|---|---|---|
| Verbatim | ∞ (validator runs once for metadata only; no repair loop) | 0 | "Use the client's text as-is. Rhythm matching is not a goal." |
| Loose | ±4 | 1 | "Aim to roughly match the original syllable count where convenient." |
| Balanced *(default)* | ±2 | 2 | "Match each line's syllable count within 2 where possible." |
| Tight | ±1 | 3 | "Closely match each line's syllable count. Aim for ±1 syllable." |
| Strict | ±0 (any-method-agrees) | 4 | "Match each line's syllable count exactly. Rhythm precision is the priority." |

**Verbatim semantics:** still makes one LLM call (the LLM is needed to align client-text into the original line structure), but the validator runs once purely to populate per-line metadata for the preview UI; nothing is flagged as a violation; no repair iteration. `iterations_used=0` and `stop_reason="verbatim_skip"`.

### Toggle → prompt rule

| Toggle | Default | If OFF, system prompt gains rule |
|---|---|---|
| `allow_reword` | ON | "RULE: Do NOT paraphrase or reword the client's text. Use it verbatim where it appears in the input." |
| `allow_omit` | ON | "RULE: All client-provided content MUST appear in the output. Do not skip any of the client's words." |
| `fixed_line_count` | ON | (no rule change; output schema and save path differ — see below) |

If `fixed_line_count=true` (default), output schema enforces exactly N lines. If `false`, the schema accepts any non-zero number of lines.

### Edge case combinations

- `allow_reword=OFF` + `strictness=Strict` is contradictory. Frontend shows a warning under the slider; backend still honors what's requested (LLM gets both rules; validator runs at the strict tolerance and will likely produce many remaining violations). The operator sees the violations in the preview and decides.
- `fixed_line_count=OFF` + `allow_reword=OFF` + `allow_omit=OFF` = "use my exact text, however long it is, with no LLM intervention". v1 still calls the LLM (with maximally restrictive instructions); a deterministic fast-path skipping the LLM entirely is a follow-up.

## Validate-and-repair loop

Pure validator function, then orchestration loop.

```python
@dataclass
class LineValidation:
    line_index: int
    target_text: str
    candidate_text: str
    target_syllables: list[int]                       # 4-method counts
    candidate_syllables: list[int]                    # 4-method counts
    min_delta: int                                    # min |c - t| across 4×4
    passes: bool                                      # min_delta <= tolerance
    severity: Literal["ok", "minor", "major"]         # minor = within tolerance+2
    time_budget_seconds: float                        # end_time - start_time
```

```python
def validate(
    candidate_lines: list[str],
    target_lines: list[str],
    target_segments: list[LyricsSegment],
    counter: SyllableCounter,
    tolerance: int,
) -> list[LineValidation]: ...
```

### Loop pseudocode

```python
def generate(self, *, settings, ...):
    metadata = compute_per_line_metadata(target_segments, counter)   # syllables, time_budget, rate
    prompt = build_initial_prompt(metadata, settings, custom_text, notes, ...)
    candidate = call_gemini(prompt)
    validations = validate(candidate, target_lines, target_segments, counter, settings.tolerance)

    best = (candidate, validations)
    iteration = 0
    prev_violation_count = sum(1 for v in validations if not v.passes)

    while iteration < settings.max_iterations:
        violations = [v for v in validations if not v.passes]
        if not violations:
            break

        repair_prompt = build_repair_prompt(
            previous_output=candidate,
            violations=violations,
            metadata=metadata,
            settings=settings,
            target_segments=target_segments,
        )
        candidate = call_gemini(repair_prompt)
        validations = validate(candidate, target_lines, target_segments, counter, settings.tolerance)

        # Track best across iterations
        if score(validations) < score(best[1]):
            best = (candidate, validations)

        # Plateau detection
        new_violation_count = sum(1 for v in validations if not v.passes)
        if new_violation_count >= prev_violation_count:
            break
        prev_violation_count = new_violation_count
        iteration += 1

    return CustomLyricsResult(
        lines=best[0],
        line_metadata=[metadata_for(v) for v in best[1]],
        violations_remaining=[v for v in best[1] if not v.passes],
        iterations_used=iteration,
        stop_reason=...,
        ...
    )
```

`score(validations)` ranks lower violation count first; ties broken by sum of `min_delta`. Used to track best-so-far so a regressing late iteration doesn't blow away a better earlier one.

### Repair prompt format

Targets only violating lines. Includes the **original transcribed line** as grounding (to give the LLM a concrete example of how that line's syllable budget *can* be filled). Keeps unchanged lines explicit.

```text
Your previous output had {V} lines that exceeded the syllable budget too far.
Please fix ONLY those lines. Keep every other line exactly the same as before.

Lines to fix:

- Line 1: target ≈5 syllables, time budget 2.4s, sung at ~2.1 syl/s.
  Original Taylor Swift line (for inspiration on how the budget can be filled): "I stay out too late"
  You wrote: "We walk in the room, yeah we own this place" (10 syllables, +5 over).
  Trim or paraphrase to fit the budget while preserving the client's intent.

- Line 2: target ≈6 syllables, time budget 2.0s, sung at ~3.0 syl/s.
  Original line: "Got nothin' in my brain"
  You wrote: "Anna hits the floor, watch her lead the way" (10 syllables, +4 over).
  Trim or paraphrase to fit.

...

Lines to keep unchanged: 3, 4, 5, 6, 7, 8, 9, 10, ...

Return JSON with all {N} lines, in original order, with the listed lines fixed
and all other lines unchanged.
```

The validator catches drift: any line that was supposed to be unchanged but differs from the previous candidate is logged as `unexpected_change` and counted against plateau. Severe drift triggers fallback to the previous best candidate.

## Variable line count save path (toggle 3 = OFF)

**Strategy:** flat proportional split (Section 2 Option A in brainstorm).

1. Backend computes total vocal time `T = sum(seg.end_time - seg.start_time for seg in target_segments)` (or alternatively `last.end_time - first.start_time` if we want to span gaps too — pick `last.end - first.start` for v1 since it's simpler and operator re-syncs anyway).
2. LLM returns flat `lines: [...]` with M lines (M may differ from N).
3. Backend computes per-new-line syllable counts (using `SyllableCounter.count_per_line`, take median of the 4 methods for stability).
4. Distributes `[first.start_time, last.end_time]` across M slices proportional to per-line syllable count.
5. Returns `new_segment_timing: list[tuple[float, float]]` of length M alongside `lines`.

Frontend's `segmentsFromLines` helper is extended to accept an optional `redistributedTiming?: Array<[start, end]>` parameter; when present, new segments use this timing instead of inheriting from existing segments.

A toast post-save reminds the operator: *"Output line count differs from original ({M} vs {N}); segments are roughly timed — manually re-sync each segment for accurate word timings."*

This is acknowledged-degraded UX. Operator who toggles off `fixed_line_count` is opting in to manual re-sync.

## API surface

`POST /api/review/{job_id}/custom-lyrics/generate` — extends existing endpoint.

**Request additions** (multipart form fields):
- `settings_json` — JSON-encoded `GenerationSettings`. Optional; missing = defaults.

**Response additions** (Pydantic):

```python
class LineMetadataResponse(BaseModel):
    line_index: int
    target_text: str
    candidate_text: str
    target_syllables: list[int]
    candidate_syllables: list[int]
    min_delta: int
    passes: bool
    severity: Literal["ok", "minor", "major"]

class CustomLyricsResponse(BaseModel):
    lines: list[str]
    line_metadata: list[LineMetadataResponse]
    line_count_match: bool
    new_segment_timing: list[tuple[float, float]] | None  # only when M != N
    iterations_used: int
    stop_reason: Literal["success", "plateau", "max_iters_reached", "line_count_mismatch", "verbatim_skip"]
    settings_applied: GenerationSettings
    warnings: list[str]
    model: str
    line_count_mismatch: bool                                 # preserved for backward compatibility with v1 callers
    new_segment_timing: list[tuple[float, float]] | None      # only when M ≠ N (toggle 3 = OFF)
```

Field naming: existing `line_count_mismatch` is preserved (boolean, true when M ≠ N) for backward compatibility with the v1 frontend if any older clients are still in flight. No new `line_count_match` field is added — the negative polarity is fine; renaming would force a coordinated frontend deploy.

## Frontend changes

### `CustomLyricsMode.tsx`

Adds a **collapsible "Generation settings"** section under the existing inputs:

```
[ Tabs: Paste text | Upload file ]
[ Notes textarea ]

[ ▶ Generation settings ]                  ← collapsed by default
   ┌──────────────────────────────────────────────────┐
   │ ☑ Allow rewording client lyrics                  │
   │ ☑ Allow omitting client lyrics                   │
   │ ☑ Maintain original segment count                │
   │                                                  │
   │ Singability strictness:                          │
   │ ●─────●─────◐─────●─────●                        │
   │ Verbatim Loose Balanced Tight Strict            │
   │ "Match each line's syllable count within 2..."  │
   │                                                  │
   │ ⚠ (only when allow_reword=OFF + strict)          │
   │ Rewording is disabled; AI may not match counts.  │
   └──────────────────────────────────────────────────┘
```

`Switch` from `@/components/ui/switch` for toggles. Custom slider component (5 snap positions) — built on Radix Slider primitives if present, else a simple radio-button-styled-as-slider.

### Preview phase decorations

The plain `<Textarea>` is replaced with a **per-line list view** showing each line's syllable status:

```
Line 1  [ We own this place              ] target 5, actual 5 — ✓
Line 2  [ Anna leads the way             ] target 6, actual 6 — ✓
Line 3  [ Grace brings rhythm, mm- mm    ] target 7, actual 8 — minor +1
...
Line 14 [ We're Year 5 strong, nobody... ] target 10, actual 10 — ✓
```

Each line is editable (operator can hand-fix). Severity badges: green ✓ for `ok`, yellow ◐ for `minor`, red ⚠ for `major`. Inline edit recomputes the validator on the frontend (using the same 4-method check via a tiny WASM/JS fallback or via an on-blur backend call — pick on-blur backend call for v1, simpler).

A header summary: "Iterations: 3. Lines passing: 67/70 (96%)."

### Variable line count UI

When `fixed_line_count=false` and the response has `new_segment_timing`, show:

> ℹ Output has 65 lines vs 70 original. Segments will be re-timed proportionally — sync each segment manually after save.

Save remains gated on the M output lines being > 0 and ≤ some upper bound (e.g., 2× N to prevent runaway).

### i18n

All new strings → `frontend/messages/en.json` under `lyricsReview.modals.customLyricsMode.settings.*` and `lyricsReview.modals.customLyricsMode.preview.*`. Pre-commit hook auto-translates to 33 locales via `python frontend/scripts/translate.py --target all`. CI fails if locales are out of sync.

## Eval harness

First-class deliverable. Lives at `backend/eval/custom_lyrics/`.

### Layout

```
backend/eval/custom_lyrics/
├── __init__.py
├── run.py                       # CLI entrypoint
├── runner.py                    # orchestration: load fixture → call service → score → write report
├── scorer.py                    # all metrics; pure functions over (output, fixture)
├── report.py                    # markdown report generation
├── fixtures/
│   ├── year-5-stars-shake-it-off/
│   │   ├── metadata.json
│   │   ├── original_lyrics.txt
│   │   ├── original_segments.json
│   │   ├── client_input.txt
│   │   └── notes.txt
│   ├── twinkle-twinkle-name-swap/         # easy
│   ├── happy-birthday-explicit-replace/   # easy
│   ├── bohemian-rhapsody-name-heavy/      # hard
│   └── single-ladies-line-surplus/        # hard, line-count surplus case
├── baseline.json                 # last committed scores per (fixture, settings)
├── cache/                        # LLM-call cache for record-and-replay
└── results/
    └── YYYY-MM-DD-HHMM/
```

### Metrics

**Per-line:** `min_syllable_delta`, `pass_at_tolerance_{0,1,2,4}`, `severity`.
**Per-fixture × settings:** `pct_pass_at_2`, `pct_pass_at_0`, `mean_delta`, `median_delta`, `max_delta`, `iterations_used`, `final_stop_reason`, `duration_ms_total`, `gemini_calls_count`, `line_count_match`.
**Corpus aggregate:** macro-averaged pass rates, per-difficulty bucket pass rates, total cost.

### Record-and-replay

LLM responses are cached on disk by `hash(prompt + settings + model)`. Re-running eval with unchanged prompts is free (cache hit). Tweaking the validator/scorer is free. Tweaking the prompt forces fresh LLM calls.

```bash
make eval-custom-lyrics                                         # default: full corpus, all settings
poetry run python -m backend.eval.custom_lyrics.run --fixtures year-5-stars
poetry run python -m backend.eval.custom_lyrics.run --no-cache  # force fresh
poetry run python -m backend.eval.custom_lyrics.run --replay-only  # error if cache miss; for scorer-only changes
poetry run python -m backend.eval.custom_lyrics.run --save-as-baseline
```

### Baseline diffing

`baseline.json` committed to repo. Eval prints a diff vs. baseline. Promoting a new run is a manual `--save-as-baseline` flag — deliberate gate.

### Human review workflow

`results/{ts}/per_fixture/{fixture}/output.md` is human-readable; operator opens it, sings through, adds notes inline. Notes saved to `fixtures/{fixture}/human_review/{timestamp}.md`. Future eval runs aggregate any human ratings present.

For v1, markdown only. A web UI for review is a follow-up.

### v1-ships criteria

Two baselines committed alongside the PR:

- **`baseline-pre.json`** — eval run against unmodified current `custom_lyrics_service.py` (the v1 prompt, no validator, no repair loop). Establishes the floor we're improving from.
- **`baseline-post.json`** — eval run against the new code with default Balanced settings.

| Fixture (default Balanced settings) | Target (new code) |
|---|---|
| year-5-stars (hard) | `pct_pass_at_2` ≥ 75% (vs. observed ~30% in `baseline-pre.json`) |
| Across corpus | macro `pct_pass_at_2` ≥ 70% |

Both numbers are *measurements*, not aspirations — the eval harness runs them and either passes or fails the bar. If `baseline-post.json` doesn't hit these targets, we don't ship; we go to fallback paths.

## Falsifiability + fallback paths

If post-ship eval shows persistent shortfall:

| Observation | Trigger | Fallback |
|---|---|---|
| Whole-song call fails to converge after 4 iterations on hard fixtures | macro `pct_pass_at_2 < 70%` after eval baseline | **Chunked generation** — split into ~8-line chunks with 2-line overlap, same loop per chunk, stitch |
| Chunking still fails on individual problem lines | `pct_pass_at_0 < 50%` even on Strict | **Per-line repair** — after chunked pass, single-line generate calls for any remaining major-violation lines |
| Operator reports creative quality regression | Subjective | Lower default strictness from Balanced → Loose; gate Tight/Strict behind "advanced" UI |

Each fallback is a follow-up PR, not part of v1. Documented here so future agents know the off-ramp.

## Testing strategy

**Backend unit:**
- `test_syllable_counter.py` — extracted SyllableCounter; 4 methods × known-syllable words; edge cases ("I- I- I", "Sayin'", numbers, hyphenated, contractions); `any_method_within` truth table
- `test_custom_lyrics_validator.py` — pure function tests
- `test_custom_lyrics_repair_prompt.py` — snapshot tests for repair prompt construction across settings combinations
- `test_custom_lyrics_service.py` — extended: plateau detection, best-iteration tracking, drift handling, settings → prompt mapping, variable line count flow, all 5 strictness levels

**Backend integration:**
- `test_review_custom_lyrics.py` — extended: new request shape with `settings_json`, response shape with `line_metadata`, variable line count case, error cases (invalid settings JSON, contradictory combos)

**Frontend unit:**
- `CustomLyricsMode.test.tsx` — extended: settings panel renders + collapsed by default, slider snaps to 5 positions, contradictory-combination warning shows, preview decorations render with violation data, severity badges color correctly, edit-line on-blur recomputes via mocked API

**Frontend E2E (production):**
- `custom-lyrics-mode.spec.ts` — extended: exercise three settings profiles (default / verbatim / strict), assert per-line decorations visible in preview, save path works for both fixed and variable line count

**Eval harness:** as described in § Eval harness; not part of `make test`. Manual run during development.

`SyllablesMatchHandler`'s existing tests must continue to pass after the SyllableCounter extraction.

## Refactor plan

The shared utility extraction touches existing code that has tests. Order:

1. Create `karaoke_gen/lyrics_transcriber/utils/syllable_counter.py` with the new class
2. Refactor `SyllablesMatchHandler` to delegate to it (no behavior change)
3. Run existing tests to confirm no regression
4. Build new validator using the utility
5. Build new service loop using the validator
6. Build new endpoint shape
7. Build new frontend UI
8. Wire eval harness

Each step is independently testable. The refactor in step 1+2 is a no-op behavior-wise, validated by existing tests.

## Configuration additions

`backend/config.py`:

```python
custom_lyrics_max_iterations: int = 4              # cap; per-strictness override drives actual limit
custom_lyrics_default_strictness: str = "balanced" # default if settings missing
custom_lyrics_max_output_lines_multiplier: float = 2.0  # output lines must be ≤ this × input lines
```

No new external dependencies — `SyllableCounter` reuses libraries already imported by `SyllablesMatchHandler`.

## Rollout

1. Bump `tool.poetry.version` in `pyproject.toml` (patch)
2. Backend deploys via existing Cloud Run CI/CD on merge
3. Frontend deploys via existing Cloudflare Pages CI/CD on merge
4. No feature flag — defaults reproduce ~current behavior + new validator
5. Eval baseline captured *before* shipping (current code) AND *after* (new code), both committed in the PR for proof
6. Manual smoke test: re-run Year 5 in production, sing through the result

## Logging / observability

Per generation:

```python
{
    "job_id": ...,
    "settings": {...},
    "iterations_used": N,
    "stop_reason": "success|plateau|max_iters_reached|line_count_mismatch|verbatim_skip",
    "initial_violation_count": ...,
    "final_violation_count": ...,
    "final_violation_severity_breakdown": {"minor": X, "major": Y},
    "drift_count_per_iteration": [...],
    "model": ...,
    "duration_ms_per_iteration": [...],
    "total_duration_ms": ...,
    "input_lines": N,
    "output_lines": M,
    "line_count_match": ...,
}
```

Lets us monitor in production: are operators hitting plateau? At what strictness setting? Which fixtures regressed after a prompt change?

## Out of scope (potential follow-ups)

- Pre-flight feasibility check ("your input has ~380 syllables; song fits ~220")
- Stress-pattern / rhyme-aware validation
- Section-aware time redistribution when M ≠ N (instead of flat proportional)
- LLM-decided structural grouping for variable-line-count case
- Web UI for human review of eval outputs
- Wiring eval harness into `make test` or PR CI
- Deterministic skip-LLM mode when all toggles are disabled
- Per-job dollar guard
- Streaming Gemini tokens for live progress
- Saving common custom-lyrics templates per client account

## Open questions

None remaining as of approval. Subsequent sections (Frontend, Testing, Falsifiability, Rollout) were decided autonomously per operator's instruction to proceed without further questions.
