# Full-Auto Review — Workstream C implementation plan (2026-08-29)

Single PR covering C1 (per-screen skip), C2 (server-side pre-apply), C3 (submission UI).
Ships via full /shipit to prod (Andrew's call, 2026-08-29). Cold-start context:
`docs/archive/2026-08-29-full-auto-review-workstream-c-handoff.md` (workspace root).

## The verdict data (already stored on every job)

`processing_metadata.auto_approval` (written by `executor.maybe_auto_complete_review`) =
`verdict.to_dict()` + extras. Relevant fields:
- `backing.verdict` ∈ {clean, with_backing, review}; `backing.non_subjective` (bool)
- `lyrics.verdict` ∈ {auto, review}; `lyrics.tier`
- `custom_instrumental` (bool), `enforcement_blockers`, `mode`, `overall_auto`

`GET /api/jobs/{id}` returns the full `Job` (response_model=Job) incl. `processing_metadata`
— already in the HTTP response, just not declared on the frontend TS `Job` type.

## Shared helper (both C1 and executor use it)

`backend/services/auto_approval/instrumental.py` (new):
- `resolve_auto_instrumental(job, settings) -> Optional[str]` — the ONE place that maps a
  stored verdict → "clean" | "with_backing" | "custom" | None:
  - custom instrumental present → "custom"
  - backing.verdict == clean AND non_subjective → "clean"
  - backing.verdict == with_backing AND non_subjective AND backing_keep_enabled → "with_backing"
  - else None (not confidently resolvable → human must pick)
  Plus a `backing_preference` override (C3): "clean" forces clean, "review" suppresses.
- `auto_approval_summary(job, settings) -> dict` — compact block for the correction-data
  response: `{backing: {verdict, confident, resolved_selection}, lyrics: {verdict, confident}}`.

Executor's `keep_backing`/selection logic (executor.py ~142-238) refactored to call this so
there is a single source of truth.

## C2 — Server-side pre-apply (kills the on-load race)

Refactor `executor._build_auto_corrections` → extract `apply.py::build_applied_segments(
corrections, ai_suggestions)` returning `{aborted}` | `{segments, applied_ids, rejected_ids}`
(the stale/empty/duplicate sanity checks). Both executor (enforce) and the new pre-apply call it.

New `backend/services/auto_approval/pre_apply.py::ensure_and_pre_apply(job_id)`:
1. ensure the proactive suggestion cache exists — if missing and proactive enabled, call
   `process_proactive_auto_correct(job_id)` (already bounded 180s); fail-open.
2. load suggestions + corrections.json, `build_applied_segments`.
3. write `corrections_updated.json` (new baseline) with
   `metadata.auto_approval = {pre_applied: true, applied_suggestion_ids, rejected_suggestion_ids,
   applied_at, suggestions: [...]}` (suggestions carried for the UI panel display).
4. `update_file_url(job_id, 'lyrics', 'corrections_updated', ...)`.
Best-effort: any failure leaves corrections_updated absent → frontend falls back to today's
client-side auto-apply. Never blocks the review-ready transition beyond the bounded timeout.

`screens_worker` (after the executor call ~220, when outcome != auto_completed, BEFORE the
AWAITING_REVIEW transition/notification): `await ensure_and_pre_apply(job_id)`. Lyrics-only, so
independent of audio state (audio-lags case is fine). Executor always applies from RAW
corrections.json, so no double-apply — it's idempotent w.r.t. corrections_updated.

Frontend: correction-data already prefers corrections_updated.json → the review UI loads the
final state in ONE event. `useAutoCorrect` gains a `preApplied` arg; when set, autoRun/autoApply
are OFF and the panel shows applied/rejected suggestions read-only + a "Re-run AI" escape.
Normal manual word editing is unaffected (that's the per-suggestion escape). Backward compatible:
no marker → today's behavior.

## C1 — Per-screen skip

Backend:
- `complete_review` accepts `instrumental_selection == "auto"` → `resolve_auto_instrumental`;
  None → 400 (client must send an explicit pick). CLI/tenant clients get skip for free.
- correction-data response gains `auto_approval` summary block.
Frontend:
- LyricsAnalyzer `handleSubmitToServer` (1044-1071): new branch — backing confidently
  resolvable (`data.auto_approval.backing.confident`) and not read-only → `completeReview(...,
  'auto', ...)` and DO NOT navigate to the instrumental screen. Escape hatch: a "review
  instrumental anyway" link in ReviewChangesModal that forces the old hash-nav path.
- Mirror case (lyrics auto + backing needs review — Cher class): `JobRouterClient` redirects
  `#/{id}/review` → `#/{id}/instrumental` when the verdict says lyrics confident + backing not
  (needs `auto_approval` on the frontend Job type / getJob). Instrumental-only complete uses the
  pre-applied corrections (already the default via corrections_updated.json).

## C3 — Submission UI (review-mode + retain-backing)

Backend:
- Add `review_mode: str = "auto"` to request models that lack it (URLSubmissionRequest,
  CreateJobFromUrlRequest, CreateJobWithUploadUrlsRequest, AudioSearchRequest,
  CreateFromSearchRequest, BulkSettings) + pass `review_mode=body.review_mode` into each
  `JobCreate(...)`. `JobManager.create_job` already forwards it to `Job`.
- New `backing_preference: str = "auto"` (auto=retain-where-possible | clean | review) on
  Job/JobCreate; copy in job_manager; consume in `resolve_auto_instrumental` / scorer gate.
  Validate values in the admin PATCH allow-list too.
Frontend:
- GuidedJobFlow: review-mode radio (VisibilityStep) + retain-backing select (CustomizeStep);
  plumb into createJobFromUrl / createJobWithUploadUrls / createJobFromSearch bodies; reset in
  resetFlow. i18n keys under `jobFlow` → `translate.py --target all` (33 locales).

## Tests
- Backend unit: `resolve_auto_instrumental` matrix; complete-endpoint "auto" resolution + 400;
  pre_apply build/persist + fail-open; screens_worker pre-apply call; request-model review_mode
  plumbing.
- Frontend Jest: useAutoCorrect preApplied (no auto-run), LyricsAnalyzer skip branch, router
  mirror redirect.
- i18n completeness (CI enforces).
- `make test` + the two private corpus validators (validate_scorer / validate_backing_decider)
  stay green.

## Definition of done
Auto by default; when a human is needed they see exactly ONE prepared screen with everything
applied; submission exposes autonomy + backing prefs in 33 locales. Then update memory +
CHANGELOG, retire the handoff docs, /backlog-finish the INBOX items.
