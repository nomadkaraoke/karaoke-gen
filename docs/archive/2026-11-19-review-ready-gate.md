# Review button only when the job is fully prepared (2026-11-19)

## Ask

Andrew, after job ae995697 showed "Review Lyrics" while audio separation was still running:

> i want the review button to only appear when a job is "fully prepared", including with the ai
> corrections and the lead vocal waveform images ready to load - and i don't like the different
> code paths implied by this statement; i want the ai corrections to always be processed before i
> open the review page: "The AI corrections are already ready when the button appears: either
> applied on the server, or applied by the page when it loads"

## What was happening (job ae995697, a Made For You order)

| UTC | Event |
|---|---|
| 18:28:44 | Screens worker scored the job: lyrics `review/phantom-gate`, blockers `made_for_you`, `audio_incomplete` |
| 18:29:01 | Waited for AI suggestions, re-scored: still review, `hold_for_audio=False` → AWAITING_REVIEW (button shown) |
| 18:29:01 | Server pre-apply aborted (`duplicate_words:ne,ne,ne,ne,ne`), so the browser would apply on load |
| 18:32:41 | Stems landed; audio-worker re-score: backing `with_backing` |

Two gaps:

1. The review hold (gen #1232, 2026-10-29) only held jobs whose lyrics would **auto-ship**
   (`executor.py` `hold_for_audio`). Any job a human had to review anyway (every Made For You
   order, every lyrics-gated job) opened review before the stems, the backing verdict, the vocal
   waveform (`/vocals-peaks`) and the missing-lyrics markers existed.
2. The server pre-apply used the auto-ship sanity gates (stale / duplicate-word aborts). On abort
   the browser ran its own auto-run + auto-apply on load: a second code path.

## Change

- **Hold every job** whose stems aren't ready when the screens worker finishes
  (`review_gate.route_after_scoring`), not just lyrics-auto ones. Kill switch unchanged
  (`AUTO_APPROVAL_AUDIO_HOLD_ENABLED`); 20-minute backstop unchanged.
- **AI suggestions are generated on the API service before parking**: the hold is normally
  released inside the audio-separation job, which has no LLM credentials and
  `AUTO_CORRECT_PROACTIVE_ENABLED` unset, so it can only apply an existing cache.
- **`enter_human_review` prepares everything before AWAITING_REVIEW**: pre-apply (generate on
  miss), then `review_assets.prepare_review_assets` (OGG transcodes, backing waveform from the
  backing-vocals stem, vocal peak envelope), then the transition.
- **Pre-apply for human review is non-strict** (`build_applied_segments(strict=False)`): it
  produces what "Accept All" would, records stale/duplicate signals as `warnings` in
  `metadata.auto_approval`. The executor's auto-ship path stays strict.
- **The browser never auto-runs or auto-applies suggestions** (`useAutoCorrect`
  `autoRunOnLoad`/`autoApplyOnLoad` and the on-load rebase removed). Without a server pre-apply
  (only if the LLM was down for both attempts) the review opens uncorrected and the AI panel can be
  run by hand.
- **Job card shows "[5/10] Preparing review"** while held (`auto_review_waiting_for_stems` added to
  the job-list summary fields).

The late-stems polling in `LyricsAnalyzer` stays as a backstop for timeout releases.

## Cost

Made For You and lyrics-gated jobs reach review ~3 minutes later (the separation tail), and their
review-ready notification fires then too.
