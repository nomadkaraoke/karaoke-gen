# Review fast-full-load + E2E canary regression fix — 2026-09-17

**Project:** karaoke-gen   **Branch/commit:** main @ `303ff7e7`   **Status:** done (shipped + deployed + canary-verified)

## Summary

Two pieces of work, back to back:

1. **Review page fast-full-load re-architecture (PR #1018, v0.229.0).** Picked up from a prior-session handoff. Root-cause fix for the 2026-09-17 signing-hang incident: removed the IAM `signBlob` network round-trip from the `/api/review/{id}/correction-data` hot path entirely, so the review page always loads fast with audio present on first paint (not the degraded fail-soft path v0.228.1 fell back to).

2. **Post-deploy canary regression diagnosis + verification.** v0.229.0's post-deploy canary went red. Diagnosed it as a regression from **#1019** (not #1018): the happy-path canary's admin `e2e-test-runner` account got added to the test-email allowlist, and `exclude_test` then hid its own just-created job from its own list → STEP 5 "job card visible" timed out. Andrew had already shipped the identical fix (#1020) in parallel; my duplicate (#1021) was closed. Watched the deploy through and confirmed **both** the deploy canary and the daily E2E happy-path are green again.

## What changed

### PR #1018 — review fast-full-load (Option B: same-origin byte proxy), v0.229.0
- **`backend/api/routes/review.py`:**
  - New endpoint `GET /{job_id}/instrumental-audio/{option_id}` — streams the transcoded OGG bytes with HTTP Range (206) support, `require_review_auth` (token via query for raw `<audio>` src, like `/audio/vocals`), stem path resolved server-side from the option id. Bounded LRU byte cache (`_review_audio_cache`, cap `REVIEW_AUDIO_CACHE_MAX`=16) — prod-safe, unlike the unbounded dev cache. Catches `NotFound` → 404 (CodeRabbit follow-up).
  - `_build_instrumental_options` now returns **relative same-origin proxy paths** as `audio_url` (no signing). Dropped the now-unused `storage` param.
  - `get_correction_data` — removed the `backing_vocals_waveform_url` signing block (dead field, zero consumers; waveform comes from the JSON `/waveform-data` endpoint). Net: `correction-data` performs **zero** `signBlob` calls.
- **`frontend/lib/api.ts`:** `resolveInstrumentalAudioUrls()` rewrites the relative proxy path → absolute `?token=` URL in the API layer (`getCorrectionData` + `refreshInstrumentalUrls`) — zero component churn, mirrors `getVocalsAudioUrl`. Also fixes the CORS `Origin: null` issue and kills the signed-URL expiry/refresh dance.
- Tests: backend endpoint (bytes/Range/auth/404), no-signing assertions on the hot path; frontend URL-resolution units; prod E2E `frontend/e2e/production/review-fast-load.spec.ts`. Docs: `TROUBLESHOOTING.md`, `API.md`, plan in `docs/archive/2026-09-17-review-fast-full-load-plan.md`.
- The v0.228.1 signing timeout + bounded admission stays as defense-in-depth for non-hot-path signing (e.g. preview-video).

### PR #1020 (Andrew's, identical to my closed #1021) — canary regression fix
- **`backend/api/routes/jobs.py`:** the admin-only `exclude_test` filter (both summary + full list modes) now **exempts the caller's own jobs** (`user_email == auth_result.user_email`). #1019's intent preserved (Andrew's admin dashboard still hides e2e-test-runner jobs), but a test account always sees its own.

### Ops / verification actions (external state)
- Merged #1018 (admin auto-merge after CI); deployed to prod. Verified live: `correction-data` 200 in 0.81s with proxy paths; proxy endpoint serves `audio/ogg` + Range 206; 401 without token; 404 bad option.
- Diagnosed the canary failure empirically: minted an `e2e-test-runner` impersonation token → `GET /api/jobs` returned 7 different users' jobs, proving the token is **admin**.
- Closed PR #1021 (redundant with #1020). Cleaned up both worktrees.
- Watched the `303ff7e7` deploy to completion: prod moved to revision **`karaoke-backend-01015-dav`**.
- Confirmed fix live: `e2e-test-runner` (admin) again sees its own jobs with default `exclude_test`.
- **Deploy canary run `35263676868` = ✅ success** (full happy-path incl. STEP 5 + generation + distribution + cleanup).
- **Daily E2E run `35266877382` = ✅ success** — dispatched via `workflow_dispatch` with `use_test_token=true` (impersonates `e2e-test-runner` = the exact broken admin path; skips Stage 1 real-Stripe purchase). Job drove the full pipeline to `complete`.

## Decisions & rationale
- **Option B (same-origin byte proxy) over Option A (local SA-key V4 signing).** Andrew chose B. Org policy `iam.disableServiceAccountKeyCreation` is NOT enforced (A was viable), but B is fully shippable in code (no infra/key provisioning, no long-lived-key liability), reuses the proven dev proxy, and also fixes CORS.
- **Frontend builds the absolute proxy URL, not the backend.** `request.base_url` is unreliable behind Cloudflare (wrong scheme/host); the frontend already knows `API_BASE_URL`. Backend returns a relative path; frontend resolves + appends token — one place, matches `getVocalsAudioUrl`.
- **exclude_test fix exempts the caller's own jobs** (rather than reverting #1019 or making e2e-test-runner non-admin). Surgical, preserves #1019's admin-dashboard hiding, protects any future test account from being invisible to itself.
- **Verified the daily via `use_test_token=true`** — skips the real Stripe charge (unrelated to this fix) while still exercising the broken admin path. Full payment run happens on the next 6am cron.

## Learnings / gotchas
- **`e2e-test-runner@nomadkaraoke.com` impersonation tokens are ADMIN** (internal domain). So the happy-path canary runs the consumer `/app` as an admin, and `list_jobs` returns all-jobs-minus-`exclude_test`. That interaction is what made #1019 break the canary.
- **A red deploy run with backend+frontend deploy = success but Post-Deploy Canary = failure** may be a canary/test-visibility issue, not the shipped change. Check *which STEP* failed: STEP 5 (job card) = list/visibility; STEP 6/8 = the orchestration stalls documented in the e2e-stall memory.
- **Before building a fix for a failing canary, `git fetch origin main`** — Andrew (or a parallel session) may already have an in-flight fix. I independently diagnosed + wrote #1021 only to find #1020 already merged. (Good cross-check, wasted a PR.)
- **GHA deploy concurrency cancels older in-progress deploys.** Three PRs (#1018, #1020, #1017) merged in quick succession; intermediate deploys show "cancelled" — the final main-HEAD deploy carries everything. Don't mistake a cancelled deploy for a failure.
- **`gh run list --branch main` returned stale results** this session; `gh api .../actions/runs` + filtering by `head_sha` / `gcloud run revisions list` were more reliable for tracking the live deploy.
- Frontend `tsc --noEmit` surfaces many pre-existing errors in unrelated files (admin pages, e2e specs, functions) — filter to your touched file to check your own work.

## Open threads & next steps
- **Nothing blocking.** All three PRs (#1018, #1019, #1020 — plus #1017 lyrics-cache) are live on `karaoke-backend-01015-dav` and canary-verified.
- The daily E2E's **Stage 1 (real Stripe credit purchase)** was skipped in my dispatched verification; it runs on the next 6am cron. If it ever fails, that's a payment-flow issue unrelated to today's work.
- The older, separate **job-orchestration stalls** (prep→advance, in_review→finalize) were fixed 2026-09-15 (#1002/#1003) and are documented in agent memory `project_gen_e2e_job_orchestration_stalls` — not seen this session.

## Related docs
- `docs/archive/2026-09-17-review-fast-full-load-plan.md` — Option A/B/C design + rationale
- `docs/archive/2026-09-17-lyrics-review-fast-full-load-rearchitecture-handoff.md` — the prior-session handoff this work started from
- `docs/TROUBLESHOOTING.md` — updated review-signing runbook (root-cause fix section)
- `docs/sessions/2026-Q3/2026-09-17-gen-review-page-signing-hang-incident.md` — the incident that motivated #1018
- Agent memory: `project_gen_signed_url_signing_hang`, `project_gen_e2e_job_orchestration_stalls`
