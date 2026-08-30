# Inline instrumental choice for all jobs + backing-scorer race fix (2026-08-30)

## Problem (job be8231fd "The Qemists - Take It Back")
A clearly-backing-vocals song showed NO inline chooser in the finish modal — just
"Instrumental (clean)" preview + "Proceed to Instrumental Review", forcing the heavy
/instrumental screen.

### Root cause of the missing confident verdict (race)
- `mark_audio_complete` is decoupled from status; screens are gated on lyrics only.
- Lyrics finished first → `screens_worker` scored auto-approval at 17:31 while audio was
  still separating → backing analysis absent → verdict `review` (`analysis_present:false`,
  `audio_incomplete`). Job parked AWAITING_REVIEW.
- User opened the review (→ IN_REVIEW) before audio finished. When audio finished, the
  `audio_worker` re-score (`maybe_auto_complete_review`) bailed at its status gate
  (`executor.py` only allows AWAITING_REVIEW for the audio_worker trigger) → the stale,
  analysis-less verdict was never refreshed.
- The analysis DID land (state_data.backing_vocals_analysis, recommends with_backing,
  corr 0.75, 64% audible) and would score a confident WITH_BACKING keep.

## Fix 1 — read-time self-correction (race-proof)
`auto_approval_summary` (backend/services/auto_approval/instrumental.py): when the stored
backing verdict was scored WITHOUT the analysis (`backing_analysis_available` falsy) but
`job.state_data.backing_vocals_analysis` now exists, re-run `score_backing` on the live
analysis and use that verdict for the summary. Enforcement/auto-complete untouched.

## Fix 2 — inline 3-way chooser for ALL jobs with both stems
Backend `complete` already accepts explicit `clean`/`with_backing` (review.py:958) — so this
is frontend-only. Show the "Your karaoke video will use:" radio group whenever both stems
exist (not only when the scorer was confident). Confident → ✓ recommended badge; not
confident → preselect the analysis's recommended_selection as a neutral "suggested" default.
Picking clean/with_backing completes inline (CTA "Complete Track"); Advanced mode routes to
/instrumental. Submit sends the concrete selection.
