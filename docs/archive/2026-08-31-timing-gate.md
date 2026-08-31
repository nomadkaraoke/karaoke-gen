# Timing-plausibility gate (scorer v0.4.0, app v0.216.0)

## Why

Reconstruction-based measurement of real reviews (2026-08-30/31, private
corpus at the workspace root) proved TIMING is the dominant residual
human-edit class in the full-auto initiative: present in 14/25 measurable
corpus jobs and ~21% of a recent-58 prod sample, median human retime 0.70s
(72% > 0.3s — clearly audible), and 100% invisible to every text-based signal
the scorer has. As the auto tiers widen toward the >50% goal, jobs with
machine-mistimed words would silently auto-ship. This gate is the seatbelt.

## What

`backend/services/auto_approval/timing_check.py` — numpy+pydub analysis of the
POST-AI word stream against the job's separated `lead_vocals` stem:

- **G1 start-silence** (gates): ≥8% of words start in vocal silence — the
  hand-retimed *section shift* signature.
- **G2 suspect-mistimed** (gates): ≥15 structurally suspect words (equal-
  duration runs = machine-distributed timing; repeated-phrase runs)
  contradicted by the audio (silent start, dead span, or no spectral-flux
  onset near the claimed start) — the *repetitive-phrase mistiming* signature
  ("come on ×4" all 0.39s each).
- **G3 unclaimed-vocal** (shadow-only): ≥4s of continuous vocal energy no word
  claims — held-note under-extension / missing words. Not gating because
  out-of-sample it also fires on vocal content deliberately absent from
  lyrics (ad-libs / DnB vocal samples; a zero-touch auto job had a 17.6s run).
  Recorded + logged on every checked job for future refinement.

Wiring: the executor computes signals only when the text signals would
otherwise let the lyrics auto-ship (audio IO costs a few seconds; any other
verdict can't change). Fired ⇒ `score_lyrics` returns REVIEW tier
`timing-gate` (a never-auto gate). The payload records
`auto_approval.timing.{status, signals, fired, shadow_fired}`:
`checked | pending_audio | no_lead_stem | error | disabled | not_needed`.
Fail-open on analysis errors (an environmental break must not demote the whole
auto class — the error is recorded and logged); fail-closed on `pending_audio`
(the audio_worker second-chance re-score runs the check once stems exist, and
the C1 summary keeps the lyrics screen until then). Kill switch:
`AUTO_APPROVAL_TIMING_GATE_ENABLED` (code default true, same both-envs pattern
as the backing-keep flag).

## Validation

- **Calibration corpus (25 jobs, reconstruction ground truth):** G1+G2 catch
  6/6 jobs with ≥8 human timing edits; zero fires on clean jobs (the only
  false fires are vocalization-gated jobs that never auto-ship anyway);
  f986dfe5 (the +4s held-note AUTO leak) appears in G3 shadow.
- **Out-of-sample (65 recent prod jobs, not used for tuning):** G1+G2 change
  the outcome of ZERO of the 14 currently-auto (ai-resolved) jobs — no
  auto-rate cost — and catch 6/11 jobs with ≥8 human timing edits
  (41/41/30/19/15/11-edit jobs; the misses have no structural/silence
  signature and are all review-bound today anyway). Private harness:
  `docs/automation-corpus/validate_timing_gate.py` (workspace root) imports
  this exact module — keep it green after any change here.

## Known limitations

- **Partial recall by design:** subtle per-word boundary tweaks inside
  continuous singing (no structural signature, no silence violation) are not
  detectable by these signals — out-of-sample, ai-resolved jobs with 7 and 9
  net timing edits pass the gate, as does a 79-edit needs-review job. Catching
  those needs localized forced alignment (the planned FIXER direction), not
  looser thresholds.
- Thresholds are calibrated on the corpus; re-run the private validator after
  ANY change to `timing_check.py`.
- The vocal-stem quality bounds everything: separation bleed inflates the
  activity floor; ad-libs/samples inflate unclaimed energy (why G3 is shadow).
